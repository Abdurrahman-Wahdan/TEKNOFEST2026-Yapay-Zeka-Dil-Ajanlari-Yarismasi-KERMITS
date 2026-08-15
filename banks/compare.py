"""Ask every bank the same question at once.

    from banks.compare import finance
    finance("konut-yeni", 1_000_000, 120)

One call instead of one per bank, and the banks are asked in parallel because
sequentially it would be slower than the calls it replaces: six banks measured
**11.99s one at a time against 0.59s together**, a twentyfold difference.

Returns dataclasses. banks/tools.py renders them, reusing the same formatters
the single-bank tools use, so a comparison row and a quote never drift apart.

Nothing is silently dropped. A bank that does not sell the product, one that is
under maintenance, and one that failed are three different answers, and all
three are reported — "no bank offers this" and "we could not reach anyone" must
never look alike.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait
from functools import partial
from dataclasses import dataclass, field, replace
from typing import Callable

from config.settings import settings

from . import families
from .providers import BANKS, get_provider
from .providers.base import BaseBank, TemporarilyUnavailable, UnsupportedProduct

logger = logging.getLogger(__name__)

NOT_OFFERED = "not_offered"
MAINTENANCE = "maintenance"
DECLINED = "declined"
ERROR = "error"

DETAIL_CHARS = 200


@dataclass(frozen=True)
class Unavailable:
    """A bank that is not in the ranking, and why it is not."""

    bank: str
    why: str
    detail: str


@dataclass
class Comparison:
    category: str
    family: str
    quotes: list = field(default_factory=list)
    unavailable: list[Unavailable] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def in_scope(self) -> int:
        """Rows produced. Not a bank count — see `banks_covered`."""
        return len(self.quotes) + len(self.unavailable)

    @property
    def banks_covered(self) -> set[str]:
        """Every bank this comparison accounted for, ranked or not.

        This is the invariant worth checking, not the row count. A bank can
        produce more than one row: Türkiye Finans prices each product sigortalı
        and sigortasız and both belong in the ranking. What must never happen
        is a bank in scope appearing in neither list -- that is a bank silently
        dropped, which is the failure this whole module exists to prevent.
        """
        return {getattr(q, "bank", "") for q in self.quotes} | {
            u.bank for u in self.unavailable
        }


def _scope(capability: str, banks: list[str] | None) -> list[BaseBank]:
    """The banks a comparison covers.

    Every bank declaring the capability, not only the ones in the family table —
    so a bank that does not sell the product is reported as not selling it
    rather than quietly left out of the count.
    """
    if banks:
        chosen = [get_provider(name) for name in banks]
    else:
        chosen = list(BANKS)
    return [bank for bank in chosen if capability in bank.capabilities]


def _fan_out(work: list[tuple[BaseBank, Callable[[BaseBank], object]]]) -> tuple[list, list[Unavailable]]:
    """Run one call per bank at the same time and collect what came back.

    One worker per bank and no more: providers keep their own catalogue caches
    and the transports share a client each, so one request in flight per bank is
    the shape that was measured safe.
    """
    if not work:
        return [], []

    results: list = []
    problems: list[Unavailable] = []
    workers = min(len(work), settings.BANK_COMPARE_WORKERS)
    # Deliberately not a context manager: __exit__ joins every worker, which
    # would wait for the very bank the timeout is meant to escape.
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="compare")
    try:
        futures = {pool.submit(call, bank): bank for bank, call in work}
        done, pending = wait(futures, timeout=settings.BANK_COMPARE_TIMEOUT)

        for future in futures:
            bank = futures[future]
            if future in pending:
                future.cancel()
                problems.append(Unavailable(
                    bank.name, ERROR,
                    "the bank did not answer inside the comparison's time budget",
                ))
                continue
            try:
                results.append(future.result())
            # Order matters: TemporarilyUnavailable subclasses UnsupportedProduct
            # subclasses ValueError. Catching the general one first would report
            # a bank under maintenance as one that does not sell the product.
            except TemporarilyUnavailable as exc:
                problems.append(Unavailable(bank.name, MAINTENANCE, str(exc)[:DETAIL_CHARS]))
            except UnsupportedProduct as exc:
                problems.append(Unavailable(bank.name, DECLINED, str(exc)[:DETAIL_CHARS]))
            except ValueError as exc:
                problems.append(Unavailable(bank.name, DECLINED, str(exc)[:DETAIL_CHARS]))
            except Exception as exc:  # noqa: BLE001 - one bank must not sink the rest
                logger.exception("Comparison failed at %s", bank.name)
                problems.append(Unavailable(
                    bank.name, ERROR,
                    f"the bank's response was not the shape this tool expects "
                    f"({type(exc).__name__})",
                ))
    finally:
        # Orphaned calls finish on their own; BANK_HTTP_TIMEOUT bounds them.
        pool.shutdown(wait=False)
    return results, problems


def _prepare(category: str, capability: str, family: str, banks: list[str] | None):
    """Split the banks in scope into ones to ask and ones that do not sell it.

    A bank can be asked more than once. Türkiye Finans prices every product
    both sigortalı and sigortasız, and those are two real answers to the same
    question -- collapsing them to one would hide a rate the bank publishes.
    Each entry carries the family membership that produced it, so the row can
    say which variant it is and whether the product is specific to this family.
    """
    members = families.members(category, family)
    scope = _scope(capability, banks)
    if not scope:
        # Nothing to compare and nothing to report against, so an empty ranking
        # would be an answer with no information in it. Say who does sell it.
        raise UnsupportedProduct(
            f"None of the banks named can be compared on "
            f"{families.label(family)}. It is sold by: "
            f"{', '.join(families.banks_in(category, family))}."
        )
    by_bank: dict[str, list] = {}
    for member in members:
        by_bank.setdefault(member.bank, []).append(member)
    asking = [
        (bank, member)
        for bank in scope
        for member in by_bank.get(bank.name, ())
    ]
    # The label is not lowercased: "İhtiyaç".lower() leaves a combining dot and
    # renders as "i̇htiyaç". The sentence is worded so it can stay as written.
    missing = [
        Unavailable(
            bank.name, NOT_OFFERED,
            f"{bank.display_name} does not offer {families.label(family)}.",
        )
        for bank in scope if bank.name not in by_bank
    ]
    return asking, missing


def finance(family: str, amount: float, term_months: int,
            banks: list[str] | None = None) -> Comparison:
    """One financing product at every bank that sells it."""
    started = time.monotonic()
    asking, missing = _prepare("finance", "finance", family, banks)
    work = [
        (bank, partial(_quote_finance, member=member, amount=amount, term=term_months))
        for bank, member in asking
    ]
    quotes, problems = _fan_out(work)
    return Comparison("finance", family, quotes, missing + problems,
                      time.monotonic() - started)


def profit_share(family: str, amount: float, term: int, unit: str,
                 currency: str = "TRY", banks: list[str] | None = None) -> Comparison:
    """One participation account at every bank that sells it."""
    started = time.monotonic()
    asking, missing = _prepare("profit_share", "profit_share", family, banks)
    work = [
        (bank, partial(_quote_profit_share, member=member, amount=amount,
                       term=term, currency=currency, unit=unit))
        for bank, member in asking
    ]
    quotes, problems = _fan_out(work)
    return Comparison("profit_share", family, quotes, missing + problems,
                      time.monotonic() - started)


def exchange(source: str, target: str, amount: float,
             banks: list[str] | None = None) -> Comparison:
    """The same conversion at every bank that converts.

    Conversion rather than the rate feed: five banks convert against three that
    publish a rate table, and "1000 dolar hangi bankada en iyi" is the question
    people actually ask. Each result carries the `derived` flag, so a figure we
    worked out from a quoted rate is never presented as the bank's own.
    """
    started = time.monotonic()
    scope = _scope("convert", banks)
    if not scope:
        converting = sorted(b.name for b in BANKS if "convert" in b.capabilities)
        raise UnsupportedProduct(
            f"None of the banks named convert currency. These do: "
            f"{', '.join(converting)}."
        )
    work = [
        (bank, partial(_convert, source=source, target=target, amount=amount))
        for bank in scope
    ]
    results, problems = _fan_out(work)
    return Comparison("convert", f"{source}->{target}", results, problems,
                      time.monotonic() - started)


# Named rather than lambdas so a traceback says which call failed, and so the
# loop cannot accidentally close over the last product it saw.

def _quote_finance(bank, *, member, amount, term):
    """Quote one family member, stamping the row with how it got here.

    The provider knows the product; only the family table knows that this row
    is Türkiye Finans' uninsured variant, or that Ziraat's single taşıt product
    is answering a question about 0 km cars. Attached after the fact so no
    provider has to carry a concept that belongs to the comparison.
    """
    quote = bank.finance_quote(member.query, amount, term)
    if not member.variant and not member.general:
        return quote
    return replace(quote, variant=member.variant, general=member.general)


def _quote_profit_share(bank, *, member, amount, term, currency, unit):
    """Same stamping as finance: the family knows what the provider cannot."""
    quote = bank.profit_share_quote(member.query, amount, term, currency, unit)
    if not member.variant and not member.general:
        return quote
    return replace(quote, variant=member.variant, general=member.general)


def _convert(bank, *, source, target, amount):
    return bank.convert(source, target, amount)
