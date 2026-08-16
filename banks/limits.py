"""What each bank will accept, before it is asked.

A comparison form that lets someone request 360 months produces the screen where
every bank declines and the user learns nothing. Every finance product already
declares its own bounds in the catalogue, so the bounds can be read up front and
the form kept inside them.

The useful number is rarely one bank's limit, though — it is the **intersection**
across the banks in the run. Dünya's konut product stops at 84 months while the
other five reach 120, so a comparison including Dünya can only ask for 84. That
is not a failure to report afterwards; it is a ceiling to show before, next to
the name of the bank that set it.

Catalogues are cached by the provider layer (15 minutes), so this is cheap to
call on every form change.
"""

from __future__ import annotations

from . import families
from .factory import get_bank
from .providers import UnsupportedProduct
from .providers.base import TemporarilyUnavailable


def _band(products: list) -> dict:
    """Collapse several catalogue rows into the envelope they cover.

    Ziraat lists one product per term band — "İHTIYAÇ FINANSMANI (1-24 AY)" and
    so on, each with its own ceiling — and picks the band that fits at quote
    time. Reporting only the band that happened to match a name lookup would
    understate what the bank will accept, so the widest envelope is reported and
    the bank is left to choose the band.
    """
    def _min(values):
        real = [v for v in values if v is not None]
        return min(real) if real else None

    def _max(values):
        real = [v for v in values if v is not None]
        return max(real) if real else None

    currencies: set[str] = set()
    for p in products:
        currencies.update(p.currencies or ["TRY"])

    return {
        "min_amount": _min(p.min_amount for p in products),
        "max_amount": _max(p.max_amount for p in products),
        "min_term": _min(p.min_term for p in products),
        "max_term": _max(p.max_term for p in products),
        "currencies": sorted(currencies),
    }


def for_family(
    category: str, family: str, banks: list[str] | None = None
) -> dict:
    """Per-bank bounds for one family, plus the intersection across them.

    `unavailable` carries the banks that cannot take part and why, in the same
    vocabulary the comparison layer uses, so the form can grey them out with a
    reason instead of dropping them.
    """
    # A bank can hold several entries in one family — Türkiye Finans prices
    # everything insured and uninsured. The form only needs one set of bounds
    # per bank, so every entry that bank has is resolved and the envelope
    # covers all of them: asking inside it is answerable by at least one, and
    # `_band` already exists to collapse Ziraat's term bands the same way.
    by_bank: dict[str, list[str]] = {}
    for member in families.members(category, family):
        if banks is not None and member.bank not in banks:
            continue
        by_bank.setdefault(member.bank, []).append(member.query)

    per_bank: list[dict] = []
    unavailable: list[dict] = []

    for bank_key, queries in by_bank.items():
        try:
            bank = get_bank(bank_key)
            catalogue = bank.products(category)
            matches = []
            for query in queries:
                # Match the family entry against the catalogue the same way the
                # quote path will: exact code or name first, then the stem
                # prefix that covers a banded product.
                folded = query.casefold()
                found = [
                    p for p in catalogue
                    if p.code.casefold() == folded or p.name.casefold() == folded
                ]
                if not found:
                    found = [p for p in catalogue if p.name.casefold().startswith(folded)]
                matches.extend(found)
            if not matches:
                raise UnsupportedProduct(
                    f"{bank_key} catalogue has no product matching "
                    f"{', '.join(repr(q) for q in queries)}."
                )

            per_bank.append({
                "bank": bank_key,
                # With one match that is the product. With several the bank has
                # banded it or priced it more than one way, and naming the first
                # would claim a specific product the user did not ask for — the
                # stem is the honest label, and the envelope covers every match.
                "product": matches[0].name if len(matches) == 1 else queries[0],
                "products_matched": len(matches),
                **_band(matches),
            })
        except TemporarilyUnavailable as exc:
            unavailable.append({"bank": bank_key, "why": "maintenance", "detail": str(exc)})
        except UnsupportedProduct as exc:
            unavailable.append({"bank": bank_key, "why": "not_offered", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 - a catalogue read failing is not fatal
            unavailable.append({"bank": bank_key, "why": "error", "detail": str(exc)})

    return {
        "category": category,
        "family": family,
        "banks": per_bank,
        "unavailable": unavailable,
        "intersection": intersect(per_bank),
    }


def intersect(per_bank: list[dict]) -> dict:
    """The range every listed bank will accept, and who narrowed it.

    `limited_by` is the point of this function. "Maximum 84 months" on its own
    reads like our limitation; "84 months — Dünya Katılım is the shortest" tells
    the user which bank to drop if they want longer.
    """
    if not per_bank:
        return {
            "min_amount": None, "max_amount": None,
            "min_term": None, "max_term": None,
            "currencies": [], "limited_by": {},
        }

    def tightest(field: str, pick):
        candidates = [(b[field], b["bank"]) for b in per_bank if b.get(field) is not None]
        if not candidates:
            return None, []
        value = pick(v for v, _ in candidates)
        return value, sorted(b for v, b in candidates if v == value)

    min_amount, min_amount_by = tightest("min_amount", max)
    max_amount, max_amount_by = tightest("max_amount", min)
    min_term, min_term_by = tightest("min_term", max)
    max_term, max_term_by = tightest("max_term", min)

    shared: set[str] | None = None
    for b in per_bank:
        current = set(b.get("currencies") or [])
        shared = current if shared is None else (shared & current)

    limited_by = {
        key: names
        for key, names in {
            "min_amount": min_amount_by,
            "max_amount": max_amount_by,
            "min_term": min_term_by,
            "max_term": max_term_by,
        }.items()
        # Only worth naming when one bank is the reason. If every bank agrees on
        # the bound it is the product's shape, not somebody's restriction.
        if names and len(names) < len(per_bank)
    }

    return {
        "min_amount": min_amount,
        "max_amount": max_amount,
        "min_term": min_term,
        "max_term": max_term,
        "currencies": sorted(shared or set()),
        "limited_by": limited_by,
    }
