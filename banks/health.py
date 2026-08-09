"""Does every bank capability still work?

One runner, three callers: `run()` from Python, `python -m banks.health` from a
shell or a scheduler, and the `check_bank_health` tool so the agent can verify a
bank on demand when a user says something looks wrong.

    from banks.health import run

    run()                                  # everything
    run(banks=["vakif"])                   # one bank
    run(capabilities=["finance"])          # one capability, every bank

It exercises the shipped provider path rather than the raw endpoints, so a
provider bug is caught as well as an endpoint change. Checks are contract
checks — a field is present, a number is in a sane range — never exact values,
because rates change daily and that change is not a failure.

Three outcomes, not two. A capability that answers and satisfies its contract is
`ok`. One that errors, or answers something the contract rejects, is `down`. One
where the *bank itself* says it does not offer the thing is `known`: visible in
the report, but nobody is paged, because there is nothing to fix.
"""

import argparse
import json
import logging
import time
from dataclasses import dataclass, field

from config.settings import settings

from . import probes, status
from .providers import BANKS, get_provider
from .providers.base import (
    CAPABILITY_METHODS,
    TemporarilyUnavailable,
    UnsupportedProduct,
)

logger = logging.getLogger(__name__)

OK = "ok"
DOWN = "down"
KNOWN = "known"


@dataclass(frozen=True)
class CheckResult:
    bank: str
    capability: str
    state: str
    detail: str
    seconds: float
    # A plain sentence for the status file, which the tools quote back to a
    # user. `detail` keeps the exception text for the log; an errno and a class
    # name belong there, not in an answer somebody reads.
    reason: str = ""

    @property
    def failed(self) -> bool:
        return self.state == DOWN


@dataclass
class HealthReport:
    results: list[CheckResult] = field(default_factory=list)
    checked_banks: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.state == DOWN]

    @property
    def known_gaps(self) -> list[CheckResult]:
        return [r for r in self.results if r.state == KNOWN]

    @property
    def healthy(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "checked": len(self.results),
            "failed": len(self.failures),
            "known_gaps": len(self.known_gaps),
            "skipped": self.skipped,
            "results": [
                {
                    "bank": r.bank,
                    "capability": r.capability,
                    "state": r.state,
                    "detail": r.detail,
                    "seconds": round(r.seconds, 2),
                }
                for r in self.results
            ],
        }

    def text(self) -> str:
        lines = []
        for result in self.results:
            mark = {OK: "ok   ", DOWN: "DOWN ", KNOWN: "known"}[result.state]
            lines.append(
                f"  {mark} {result.bank:14s} {result.capability:13s} "
                f"{result.seconds:5.2f}s  {result.detail}"
            )
        lines.append(
            f"\n{len(self.results) - len(self.failures)}/{len(self.results)} ok, "
            f"{len(self.failures)} down, {len(self.known_gaps)} known bank-side gap(s)"
        )
        if self.skipped:
            lines.append(f"skipped (nothing to call): {', '.join(self.skipped)}")
        return "\n".join(lines)


# ----- the probes -----


def _check_products(bank) -> str:
    category = probes.PRODUCT_CATEGORY[bank.name]
    found = bank.products(category)
    if not found:
        raise AssertionError(f"{category} catalogue came back empty")
    first = found[0]
    if not first.code or not first.name:
        raise AssertionError("a product arrived with no code or no name")
    return f"{len(found)} {category} product(s)"


def _check_finance(bank) -> str:
    product, amount, months = probes.FINANCE[bank.name]
    quote = bank.finance_quote(product, amount, months)
    if quote.installment <= 0 or quote.total <= quote.amount:
        raise AssertionError(
            f"instalment {quote.installment} / total {quote.total} on {quote.amount}"
        )
    return f"{product[:22]} {amount:,.0f}/{months}ay -> {quote.installment:,.2f}"


def _check_profit_share(bank) -> str:
    product, amount, days, currency = probes.PROFIT_SHARE[bank.name]
    quote = bank.profit_share_quote(product, amount, days, currency, "day")
    if quote.net_profit <= 0:
        raise AssertionError(f"net profit came back {quote.net_profit}")
    return f"{product[:22]} {amount:,.0f}/{days}g -> {quote.net_profit:,.2f}"


def _check_card(bank) -> str:
    card, amount, installments = probes.CARD[bank.name]
    quote = bank.card_installment_quote(card, amount, installments)
    if quote.installment <= 0:
        raise AssertionError(f"instalment came back {quote.installment}")
    return f"{card[:22]} {amount:,.0f}/{installments}x -> {quote.installment:,.2f}"


def _check_rates(bank) -> str:
    rows = bank.rates()
    if not rows:
        raise AssertionError("rate feed came back empty")
    priced = [r for r in rows if r.sell > 0]
    if not priced:
        raise AssertionError(f"{len(rows)} rate(s), none with a sell price")
    return f"{len(rows)} rate(s), e.g. {priced[0].code} {priced[0].sell}"


def _check_convert(bank) -> str:
    source, target, amount = probes.CONVERT[bank.name]
    result = bank.convert(source, target, amount)
    if result.result <= 0:
        raise AssertionError(f"conversion returned {result.result}")
    return f"{amount:,.0f} {source}->{target} = {float(result.result):,.2f}"


CHECKS = {
    "products": _check_products,
    "finance": _check_finance,
    "profit_share": _check_profit_share,
    "card": _check_card,
    "rates": _check_rates,
    "convert": _check_convert,
}


def _plain_reason(exc: Exception) -> str:
    """Why it failed, in words a user can be shown.

    The technical text is kept in `detail` and in the log. What reaches an
    answer should say what happened, not name a Python class or an errno.
    """
    if isinstance(exc, AssertionError):
        return "the bank answered, but its figures did not pass their checks"
    text = str(exc).lower()
    if any(w in text for w in ("could not be reached", "timed out", "timeout",
                               "connecterror", "connection")):
        return "the bank could not be reached"
    if any(w in text for w in ("not json", "layout", "does not recognise")):
        return "the bank answered in a shape this reader does not recognise"
    return "the bank's calculator did not answer correctly"


def _run_one(bank, capability: str) -> CheckResult:
    started = time.monotonic()
    try:
        # The bypass is opened here, inside whichever thread is doing the work.
        # It is thread-local, so a caller that opened one around a thread pool
        # would not pass it to the workers -- and the gate would then refuse the
        # very probe meant to clear an outage.
        with status.bypass():
            detail = CHECKS[capability](bank)
        elapsed = time.monotonic() - started
        # A bank that takes minutes is broken for a chatbot even when it
        # eventually answers, so the budget is part of the contract.
        if elapsed > settings.HEALTH_TIMEOUT:
            return CheckResult(
                bank.name, capability, DOWN,
                f"took {elapsed:.1f}s, over the {settings.HEALTH_TIMEOUT:.0f}s budget",
                elapsed,
                reason="the bank answered too slowly to be useful",
            )
        return CheckResult(bank.name, capability, OK, detail, elapsed)
    except TemporarilyUnavailable as exc:
        # Must sit above UnsupportedProduct, which it subclasses. The gate fired
        # inside the checker, so the bypass above did not take effect and this
        # probe never reached the bank. Calling that KNOWN would write it back as
        # ok and clear a real outage without a single network call, so it is a
        # loud failure instead.
        logger.error("Health gate fired inside the checker for %s/%s: %s",
                     bank.name, capability, exc)
        return CheckResult(
            bank.name, capability, DOWN,
            f"the health gate fired inside the checker: {str(exc)[:150]}",
            time.monotonic() - started,
            reason="the last recorded outage was never re-tested",
        )
    except UnsupportedProduct as exc:
        # The bank answered and declined. Not an outage, and not ours to fix.
        return CheckResult(
            bank.name, capability, KNOWN, str(exc)[:200], time.monotonic() - started
        )
    except Exception as exc:  # noqa: BLE001 - every failure is a result, not a crash
        logger.debug("Health check %s/%s failed", bank.name, capability, exc_info=True)
        return CheckResult(
            bank.name, capability, DOWN,
            f"{type(exc).__name__}: {str(exc)[:180]}", time.monotonic() - started,
            reason=_plain_reason(exc),
        )


def run(
    banks: list[str] | None = None,
    capabilities: list[str] | None = None,
    *,
    write_status: bool = True,
    notify: bool = True,
) -> HealthReport:
    """Check banks and record what is down.

    Args:
        banks: Bank names to check. None means every bank.
        capabilities: Capability names to check. None means every capability the
            bank declares.
        write_status: Update the status file the tools read. Off for a dry run.
        notify: Send alerts for capabilities that changed state.

    Returns:
        A HealthReport. `healthy` is False if anything is down.
    """
    wanted = [get_provider(n) for n in banks] if banks else list(BANKS)
    report = HealthReport()
    previous = status.read()
    fresh: dict = {}

    # The bypass that lets the checker ignore its own status file is opened per
    # check, inside _run_one, so it works the same whether this loop runs here or
    # in a worker thread.
    for bank in wanted:
        if bank.transport == "none" or not bank.capabilities:
            # Nothing to call. Adil has no calculator and T.O.M.'s API needs
            # a credential we do not have; polling either is noise.
            report.skipped.append(bank.name)
            continue
        report.checked_banks.append(bank.name)
        for capability in sorted(bank.capabilities):
            if capabilities and capability not in capabilities:
                continue
            if capability not in CAPABILITY_METHODS:
                continue
            result = _run_one(bank, capability)
            report.results.append(result)
            was = (previous.get(bank.name) or {}).get(capability)
            state = status.DOWN if result.state == DOWN else status.OK
            fresh.setdefault(bank.name, {})[capability] = status.entry(
                state, result.reason if result.state == DOWN else "", was
            )

    if write_status:
        status.write(_merge(previous, fresh))
    if notify:
        _notify_changes(previous, fresh)
    return report


def _merge(previous: dict, fresh: dict) -> dict:
    """Keep entries for anything this run did not check.

    A scoped run must not clear an outage it never looked at. Two levels are
    enough here because status.entry() carries a capability's product-level
    records forward from `previous`, so replacing the capability record does not
    drop the products underneath it.
    """
    merged = {bank: dict(caps) for bank, caps in previous.items()}
    for bank, caps in fresh.items():
        merged.setdefault(bank, {}).update(caps)
    return merged


def _notify_changes(previous: dict, fresh: dict) -> None:
    from .notify import send

    changes = []
    for bank, caps in fresh.items():
        for capability, record in caps.items():
            was = ((previous.get(bank) or {}).get(capability) or {}).get("state")
            if was is None and record["state"] == status.OK:
                # First time we have ever checked it, and it is fine. Announcing
                # that a working bank works is the noise that teaches people to
                # ignore the alert that matters.
                continue
            if was != record["state"]:
                changes.append(
                    {
                        "bank": bank,
                        "capability": capability,
                        "from": was or "unknown",
                        "to": record["state"],
                        "reason": record.get("reason", ""),
                    }
                )
    if changes:
        send(changes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m banks.health",
        description="Check that every bank capability still answers.",
    )
    parser.add_argument("--bank", action="append", dest="banks",
                        help="Check only this bank. Repeatable.")
    parser.add_argument("--capability", action="append", dest="capabilities",
                        help="Check only this capability. Repeatable.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument("--no-write", action="store_true",
                        help="Do not update the status file the tools read.")
    parser.add_argument("--no-notify", action="store_true", help="Do not send alerts.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=settings.LOG_LEVEL, format="%(levelname)s %(message)s")
    try:
        report = run(
            banks=args.banks,
            capabilities=args.capabilities,
            write_status=not args.no_write,
            notify=not args.no_notify,
        )
    except ValueError as exc:  # an unknown bank name lists the valid ones
        print(exc)
        return 2

    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
          if args.json else report.text())
    return 0 if report.healthy else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
