"""Ziraat Katılım.

Drupal, and the calculators live on the homepage with no dedicated URL, so
URL-based discovery is structurally blind to this bank.

Only finansman is reachable without a browser. Kâr payı and leasing submit the
Drupal form itself, which answers 493 to every non-browser client — curl_cffi
impersonation included — and no /ajax/ route exists for them. That is declared
in `capabilities` rather than worked around, so the refusal costs no request.

The answer comes back as HTML inside a Drupal command array. The numbers are the
bank's; we only pull them out of the markup. Contract in
docs/discovery/captured/ziraat.md, exercised by verify_ziraat.py.
"""

import html as htmlmod
import logging
import re

from ..models import FinanceQuote, PaymentRow, Product
from ..parse import fold, money, rate
from .base import BaseBank, UnsupportedProduct

logger = logging.getLogger(__name__)

HOST = "https://www.ziraatkatilim.com.tr"
PAGE = f"{HOST}/anasayfa"

HEADERS = {
    "x-requested-with": "XMLHttpRequest",
    "accept": "application/json, text/javascript, */*; q=0.01",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "referer": PAGE,
}

_SELECT = re.compile(r'<select[^>]*name="finansman_type"[^>]*>(.*?)</select>', re.S)
_OPTION = re.compile(r'<option[^>]*value="([^"]+)"[^>]*>\s*([^<]*?)\s*(?:</option>|<)', re.S)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
# The summary states the amount, the instalment and the total in that order,
# each suffixed TRY. Column order is asserted below rather than trusted.
_TRY = re.compile(r"([\d.]+,\d{2})\s*(?:&nbsp;)?\s*TRY")

# Schedule columns are conditional — KDV, KKDF and BSMV appear only for the
# products that carry them — so cells are read by header name, not position.
COLUMNS = {
    "taksitsayisi": "order",
    "taksittutari": "amount",
    "anapara": "principal",
    "kartutari": "profit",
    "kdvtutari": "vat",
    "kkdftutari": "kkdf",
    "bsmvtutari": "bsmv",
    "kalananaparatutari": "remaining",
}


def _text_of(markup: str) -> str:
    return htmlmod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))).strip()


class Ziraat(BaseBank):
    name = "ziraat"
    display_name = "Ziraat Katılım Bankası"
    # Finansman only. Kâr payı and leasing exist on the site but are reachable
    # only by submitting the Drupal form, which refuses every non-browser
    # client; declaring that here means we never spend a request finding out.
    capabilities = frozenset({"products", "finance"})
    notes = (
        "Its kâr payı and leasing calculators are browser-only: they submit a "
        "Drupal form that answers 493 to any non-browser client, and no JSON "
        "route exists for them."
    )

    def _ajax(self, path: str, **fields):
        return self._json("POST", f"{HOST}/ajax/{path}", headers=HEADERS, data=fields)

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category != "finance":
            raise UnsupportedProduct(
                f"{self.display_name} publishes no {category} catalogue. "
                + self._what_it_does()
            )
        cached = self._cached(category)
        if cached is not None:
            return cached

        page = self._text(PAGE)
        block = _SELECT.search(page)
        if not block:
            raise UnsupportedProduct(
                f"{self.display_name} returned no finance products. The catalogue "
                f"is parsed out of the homepage, so its layout may have changed."
            )
        built = []
        for eid, label in _OPTION.findall(block.group(1)):
            # get-vade is the catalogue call: it returns the allowed terms, the
            # profit rate and the ceiling for this one product, and the rate has
            # to be handed back to the calculation.
            data = (self._ajax("get-vade", eid=eid) or {}).get("data") or {}
            terms = data.get("range") or []
            built.append(
                Product(
                    code=eid,
                    name=htmlmod.unescape(re.sub(r"\s+", " ", label)).strip(),
                    category="finance",
                    min_amount=money(data.get("minimum_amount")) or None,
                    max_amount=money(data.get("maximum_amount")) or None,
                    min_term=int(terms[0]) if terms else None,
                    max_term=int(terms[-1]) if terms else None,
                    rate=rate(data.get("ratio")) or None,
                    raw=data,
                )
            )
        self._store(category, built)
        logger.debug("Loaded %d finance product(s) from %s", len(built), self.name)
        return built

    def _resolve(self, query: str, amount: float, term: int) -> Product:
        """Pick the band that actually covers this amount and term.

        The same product is listed once per term band with a different ceiling,
        and the ceiling falls as the term rises: İhtiyaç runs 1–12 at 999 999,
        1–24 at 249 999 and 1–36 at 124 999. A user asking for "ihtiyaç
        finansmanı" names all three, so the request itself decides which band —
        the tightest one that fits.

        Different products are never picked between, though. "İhtiyaç
        Finansmanı" is also a prefix of "İhtiyaç Finansmanı Hac / Umre", and
        quietly quoting the pilgrimage product to someone who asked for the
        ordinary one would be a wrong answer rather than a near miss.
        """
        wanted = fold(query)
        candidates = [
            p
            for p in self.products("finance")
            if fold(p.code) == wanted or wanted in fold(p.name)
        ]
        if not candidates:
            # Not a Ziraat product at all; find_product writes that refusal.
            return self.find_product("finance", query)

        bands = candidates
        if len(candidates) > 1:
            # Group the bands of one product together, then insist on one
            # product: naming a band is a choice, naming a different product is
            # not ours to make.
            by_stem: dict[str, list[Product]] = {}
            for candidate in candidates:
                by_stem.setdefault(fold(_stem(candidate.name)), []).append(candidate)
            bands = by_stem.get(wanted) or (
                list(by_stem.values())[0] if len(by_stem) == 1 else None
            )
            if bands is None:
                names = ", ".join(sorted(_stem(p.name) for p in candidates))
                raise UnsupportedProduct(
                    f"{query!r} matches several {self.display_name} products: "
                    f"{names}. Ask for one of them by name."
                )

        # The fit check runs whether the query named one band or many. Skipping
        # it for an exact name was the whole defect: list_products teaches the
        # model the exact names, so that was the likely path, not the rare one.
        # Asking 200 000 TL of a 124 999 band answers 200 000,16 — a principal
        # -only schedule and 0,16 TL of profit — while reporting a 4,99% rate.
        fits = [
            p
            for p in bands
            if (p.max_term or 0) >= term
            and (p.min_term or 1) <= term
            and (p.max_amount or float("inf")) >= amount
        ]
        if not fits:
            offers = "; ".join(
                f"{p.name} (to {p.max_term} months, max {p.max_amount:,.0f} TL)"
                for p in bands
            )
            raise UnsupportedProduct(
                f"{self.display_name} has no {query!r} band covering "
                f"{amount:,.0f} TL over {term} months. It offers: {offers}."
            )
        return min(fits, key=lambda p: (p.max_term or 0))

    # ----- finance -----

    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        self._check_limits(Product(code="", name="", category="finance"),
                           amount=amount, term=term)
        chosen = self._resolve(product, amount, term)
        payload = self._json(
            "POST",
            f"{HOST}/ajax/finansmanhesapla?_wrapper_format=drupal_ajax",
            headers=HEADERS,
            data={
                "lang": "tr",
                "finansman_is_bank_ratio": "true",
                "finans_type": chosen.code,
                # The bank's own rate, handed straight back from get-vade.
                "finans_kar_orani": chosen.raw.get("ratio"),
                "finans_vade": str(int(term)),
                "finans_tutari": str(int(amount)),
                "_drupal_ajax": "1",
            },
        )
        markup = _plan_markup(payload)
        if not markup:
            raise UnsupportedProduct(
                f"{self.display_name} returned no payment plan for {chosen.name} "
                f"at {amount:,.0f} TL over {term} months."
            )

        figures = [money(f) for f in _TRY.findall(_text_of(markup))]
        # amount, instalment, total — asserted rather than assumed, so a
        # changed layout fails loudly instead of quoting the wrong column.
        if len(figures) < 3 or abs(figures[0] - amount) > 1:
            raise UnsupportedProduct(
                f"{self.display_name} returned a payment plan this reader does "
                f"not recognise for {chosen.name}. Its summary layout has "
                f"probably changed."
            )
        schedule = _schedule(markup)
        return self._check_quote(FinanceQuote(
            bank=self.name,
            product=chosen,
            amount=figures[0],
            term=int(term),
            installment=figures[1],
            total=figures[2],
            profit_rate=chosen.rate or 0.0,
            annual_cost_rate=None,
            fees={},
            schedule=schedule,
            raw={"summary": figures, "rows": len(schedule)},
        ))


def _stem(name: str) -> str:
    """A product name without its term band: "İhtiyaç (1-24 AY)" -> "İhtiyaç"."""
    return re.sub(r"\s*\(.*$", "", name).strip()


def _plan_markup(payload) -> str:
    """The payment plan out of the Drupal command array."""
    for command in payload or []:
        if command.get("command") == "insert" and command.get("data"):
            return command["data"]
    return ""


def _schedule(markup: str) -> list[PaymentRow]:
    rows = _ROW.findall(markup)
    if not rows:
        return []
    headers = [fold(_text_of(cell)) for cell in _CELL.findall(rows[0])]
    index = {COLUMNS[h]: i for i, h in enumerate(headers) if h in COLUMNS}
    if "amount" not in index:
        return []

    def cell(cells, key, default=0.0):
        position = index.get(key)
        return money(_text_of(cells[position])) if position is not None and position < len(cells) else default

    built = []
    for row in rows[1:]:
        cells = _CELL.findall(row)
        if len(cells) < len(index):
            continue
        built.append(
            PaymentRow(
                order=int(cell(cells, "order")),
                amount=cell(cells, "amount"),
                principal=cell(cells, "principal"),
                profit=cell(cells, "profit"),
                taxes=cell(cells, "vat") + cell(cells, "kkdf") + cell(cells, "bsmv"),
                remaining=cell(cells, "remaining"),
            )
        )
    return built
