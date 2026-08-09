"""The extensive check: every product at every bank.

`banks/health.py` asks one question per capability, which answers "is this bank
answering at all" in a second and is the right thing for an on-demand check. The
audit is the other end: it walks every product in every catalogue, so a single
broken product is found before a user finds it.

    python -m banks.audit                    # everything
    python -m banks.audit --bank vakif       # one bank
    python -m banks.audit --no-write         # look, change nothing

Meant to run on a schedule (see banks/schedule.py). It sends one report each
time, green or not, and disables what it finds broken.

Two rules keep it from causing the outage it is looking for:

- **One thread per bank**, each walking its own products in order, with a pause
  between them. Around 170 requests in a burst from one address is what a WAF
  throttles, and two of these banks already fingerprint TLS.
- **A failure has to happen twice** before anything is disabled. The
  capability-level probe still runs every time and catches a real outage
  quickly, so the product level can afford to be slow and certain.
"""

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from config.settings import settings

from . import families, status
from .health import DOWN, KNOWN, OK, CheckResult, _plain_reason
from .providers import BANKS, get_provider
from .providers.base import TemporarilyUnavailable, UnsupportedProduct

logger = logging.getLogger(__name__)

# What to probe a product with when its own limits do not say. A flat
# 100 000 / 24 is refused outright by every product whose ceiling is lower,
# which would be a `known` result that checked nothing.
DEFAULT_AMOUNT = 100_000.0
DEFAULT_TERM = 24

# The pseudo-capability under which family-table entries are checked. Not
# something a bank declares, so it never reaches the status file.
FAMILIES = "families"


@dataclass(frozen=True)
class Unit:
    """One thing to check: a capability, or one product inside it."""

    bank: str
    capability: str
    product: str = ""

    @property
    def label(self) -> str:
        return f"{self.capability}/{self.product}" if self.product else self.capability


@dataclass
class AuditReport:
    results: list[CheckResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def failures(self):
        return [r for r in self.results if r.state == DOWN]

    @property
    def known_gaps(self):
        return [r for r in self.results if r.state == KNOWN]

    @property
    def healthy(self) -> bool:
        return not self.failures

    def per_bank(self) -> dict:
        counts: dict = {}
        for result in self.results:
            entry = counts.setdefault(result.bank, {"ok": 0, "down": 0, "known": 0})
            entry[result.state] += 1
        return counts

    def as_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "checked": len(self.results),
            "down": len(self.failures),
            "known_gaps": len(self.known_gaps),
            "seconds": round(self.seconds, 1),
            "skipped": self.skipped,
            "banks": self.per_bank(),
            "failures": [
                {"bank": r.bank, "capability": r.capability,
                 "detail": r.detail, "reason": r.reason}
                for r in self.failures
            ],
        }

    def text(self) -> str:
        lines = [
            f"  {'DOWN ' if r.state == DOWN else r.state:5s} {r.bank:14s} "
            f"{r.capability:30s} {r.detail[:70]}"
            for r in self.results
            if r.state != OK
        ]
        lines.append(
            f"\n{len(self.results) - len(self.failures)}/{len(self.results)} ok, "
            f"{len(self.failures)} down, {len(self.known_gaps)} known gap(s), "
            f"{self.seconds:.1f}s"
        )
        if self.skipped:
            lines.append(f"skipped (nothing to call): {', '.join(self.skipped)}")
        return "\n".join(lines)


def _limits(product) -> tuple[float, int]:
    """An amount and term inside what this product says it accepts.

    Choosing an input the bank will entertain is choosing a question, not
    computing a price. Kuveyt Türk's Elektrikli Araç Şarj Ünitesi really does
    stop at one month, and probing it at 24 would test nothing.
    """
    amount = DEFAULT_AMOUNT
    if product.min_amount:
        amount = max(amount, product.min_amount)
    if product.max_amount:
        amount = min(amount, product.max_amount)

    term = DEFAULT_TERM
    if product.min_term:
        term = max(term, product.min_term)
    if product.max_term:
        term = min(term, product.max_term)
    return amount, max(int(term), 1)


def plan(bank, capabilities: list[str] | None = None) -> list[Unit]:
    """Every unit to check at one bank.

    Products come from the bank's own catalogue, so one it adds tomorrow is
    covered the next morning without editing anything here.
    """
    units: list[Unit] = []
    wanted = set(capabilities or [])
    for capability in sorted(bank.capabilities):
        if wanted and capability not in wanted:
            continue
        if capability in ("rates", "convert", "products"):
            units.append(Unit(bank.name, capability))
            continue
        try:
            catalogue = bank.products(capability)
        except UnsupportedProduct:
            continue
        units.extend(Unit(bank.name, capability, p.code or p.name) for p in catalogue)

    if not wanted or FAMILIES in wanted:
        for category, table in families.BY_CATEGORY.items():
            for family, entries in table.items():
                if bank.name in entries:
                    units.append(Unit(bank.name, FAMILIES, f"{category}/{family}"))
    return units


def _check(bank, unit: Unit) -> str:
    """Run one unit and describe what came back."""
    from .health import CHECKS

    if not unit.product:
        return CHECKS[unit.capability](bank)

    if unit.capability == FAMILIES:
        category, family = unit.product.split("/", 1)
        query = families.entries(category, family)[bank.name]
        product = bank.resolve(category, query, DEFAULT_AMOUNT, DEFAULT_TERM)
        return f"{family} -> {product.name[:40]}"

    product = bank.resolve(unit.capability, unit.product, DEFAULT_AMOUNT, DEFAULT_TERM)
    amount, term = _limits(product)

    if unit.capability == "finance":
        quote = bank.finance_quote(unit.product, amount, term)
        if quote.installment <= 0 or quote.total <= quote.amount:
            raise AssertionError(f"instalment {quote.installment} on {quote.amount}")
        return f"{amount:,.0f}/{term}ay -> {quote.installment:,.2f}"

    if unit.capability == "profit_share":
        days = max(int(product.min_term or 31), 31)
        quote = bank.profit_share_quote(unit.product, amount, days, "TRY", "day")
        if quote.net_profit <= 0:
            raise AssertionError(f"net profit {quote.net_profit}")
        return f"{amount:,.0f}/{days}g -> {quote.net_profit:,.2f}"

    if unit.capability == "card":
        count = max(int(product.min_term or 3), 3)
        quote = bank.card_installment_quote(unit.product, 10_000, count)
        if quote.installment <= 0:
            raise AssertionError(f"instalment {quote.installment}")
        return f"10,000/{count}x -> {quote.installment:,.2f}"

    raise AssertionError(f"no check for {unit.capability}")


def _run_unit(bank, unit: Unit, retry: bool = True) -> CheckResult:
    started = time.monotonic()
    try:
        with status.bypass():
            detail = _check(bank, unit)
        return CheckResult(bank.name, unit.label, OK, detail, time.monotonic() - started)
    except TemporarilyUnavailable as exc:
        # The bypass above should have prevented this. If it did not, the probe
        # never reached the bank, and calling it anything but down would clear a
        # real outage without checking it.
        return CheckResult(
            bank.name, unit.label, DOWN,
            f"the maintenance gate fired inside the audit: {str(exc)[:120]}",
            time.monotonic() - started,
            reason="the last recorded outage was never re-tested",
        )
    except UnsupportedProduct as exc:
        # The bank answered and declined. Never disables anything.
        return CheckResult(bank.name, unit.label, KNOWN, str(exc)[:160],
                           time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 - a failure is a result, not a crash
        if retry:
            # One transient blip must not take a working product offline for the
            # rest of the day. Pause, then ask once more before believing it.
            time.sleep(settings.HEALTH_AUDIT_PRODUCT_DELAY * 4)
            return _run_unit(bank, unit, retry=False)
        logger.debug("Audit %s/%s failed", bank.name, unit.label, exc_info=True)
        return CheckResult(
            bank.name, unit.label, DOWN,
            f"{type(exc).__name__}: {str(exc)[:150]}", time.monotonic() - started,
            reason=_plain_reason(exc),
        )


def _walk(bank, capabilities) -> list[CheckResult]:
    """Every unit at one bank, in order, with a pause between them."""
    results = []
    for index, unit in enumerate(plan(bank, capabilities)):
        if index:
            time.sleep(settings.HEALTH_AUDIT_PRODUCT_DELAY)
        results.append(_run_unit(bank, unit))
    return results


def run(banks: list[str] | None = None, capabilities: list[str] | None = None,
        *, write_status: bool = True, notify: bool = True) -> AuditReport:
    """Check every product at every bank, and record what is broken."""
    started = time.monotonic()
    wanted = [get_provider(n) for n in banks] if banks else list(BANKS)
    report = AuditReport()

    live = []
    for bank in wanted:
        if bank.transport == "none" or not bank.capabilities:
            report.skipped.append(bank.name)
        else:
            live.append(bank)

    pool = ThreadPoolExecutor(
        max_workers=min(len(live) or 1, settings.HEALTH_AUDIT_WORKERS),
        thread_name_prefix="audit",
    )
    try:
        futures = {pool.submit(_walk, bank, capabilities): bank for bank in live}
        wait(futures)
        for future, bank in futures.items():
            try:
                report.results.extend(future.result())
            except Exception as exc:  # noqa: BLE001 - one bank cannot sink the audit
                logger.exception("Audit failed at %s", bank.name)
                report.results.append(CheckResult(
                    bank.name, "audit", DOWN, f"{type(exc).__name__}: {exc}", 0.0,
                    reason="the audit could not complete for this bank",
                ))
    finally:
        pool.shutdown(wait=False)

    report.results.sort(key=lambda r: (r.bank, r.capability))
    report.seconds = time.monotonic() - started

    changes: list[dict] = []
    if write_status:
        # status.apply() re-reads the file itself, right before merging, so a
        # run that took ~20 seconds merges against what is on disk now, not
        # against a snapshot from when this run started.
        _, changes = status.apply(_record(report))
    if notify:
        from .notify import send_report

        send_report(report, changes)
    return report


def _record(report: AuditReport) -> dict:
    """This run's results, grouped for status.apply(): {bank: {capability:
    (state, reason, products_or_None)}}.

    A capability is only marked down when *every* product under it failed: that
    is an endpoint change rather than a product change, and it keeps the refusal
    cheap. One broken product disables itself and nothing else. Carrying
    forward whatever this run did not check -- unrelated banks, products it
    never looked at -- is status.apply()'s job, not this one's.
    """
    by_capability: dict = {}
    for result in report.results:
        capability, _, product = result.capability.partition("/")
        if capability == FAMILIES:
            # Not a capability a bank declares, and nothing gates on it. It is
            # checked so a stale family entry is found every morning, and it
            # belongs in the report rather than in the file the tools read.
            continue
        by_capability.setdefault((result.bank, capability), []).append((product, result))

    fresh: dict = {}
    for (bank, capability), items in by_capability.items():
        products = {}
        for product, result in items:
            if not product:
                continue
            state = status.DOWN if result.state == DOWN else status.OK
            products[product] = (state, result.reason if result.state == DOWN else "")
        priced = [r for _, r in items if r.state != KNOWN]
        everything_failed = bool(priced) and all(r.state == DOWN for r in priced)
        fresh.setdefault(bank, {})[capability] = (
            status.DOWN if everything_failed else status.OK,
            "every product under it failed, so the endpoint itself looks broken"
            if everything_failed else "",
            products or None,
        )
    return fresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m banks.audit",
        description="Check every product at every bank and report.",
    )
    parser.add_argument("--bank", action="append", dest="banks")
    parser.add_argument("--capability", action="append", dest="capabilities")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Print nothing on a green run.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=settings.LOG_LEVEL, format="%(levelname)s %(message)s")
    try:
        report = run(banks=args.banks, capabilities=args.capabilities,
                     write_status=not args.no_write, notify=not args.no_notify)
    except ValueError as exc:
        print(exc)
        return 2

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    elif not (args.quiet and report.healthy):
        print(report.text())
    return 0 if report.healthy else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
