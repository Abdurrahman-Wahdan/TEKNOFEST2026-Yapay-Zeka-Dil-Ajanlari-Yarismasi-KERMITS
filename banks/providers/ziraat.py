"""Ziraat Katılım.

Drupal, and the calculators live on the homepage with no dedicated URL, so
URL-based discovery is structurally blind to this bank.

Leasing is genuinely browser-only: it submits the Drupal form itself, no
`/ajax/` route exists behind it, and that stays a `capabilities` refusal.
Kâr payı is not, despite this file having claimed so until 2026-08-16 --
`/ajax/karpayi-products` answers plain `curl` with no cookie, no token and no
browser fingerprint, exactly like `/ajax/finansmanhesapla` already does for
financing. The claim was never re-checked after it was written; it was wrong.

The answer comes back as HTML inside a Drupal command array. The numbers are the
bank's; we only pull them out of the markup. Contract in
docs/discovery/captured/ziraat.md, exercised by verify_ziraat.py.
"""

import html as htmlmod
import logging
import re

from ..models import FinanceQuote, PaymentRow, ProfitShareQuote, Product
from ..parse import fold, money, rate
from ..parse import term_unit as unit
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
    # Leasing exists on the site but is reachable only by submitting the
    # Drupal form, which refuses every non-browser client; declaring that here
    # means we never spend a request finding out. Kâr payı used to be listed
    # alongside it on the same claim -- disproved 2026-08-16, see the module
    # docstring -- and is a real capability now.
    capabilities = frozenset({"products", "finance", "profit_share"})
    notes = (
        "Its leasing calculator is browser-only: it submits a Drupal form "
        "that answers 493 to any non-browser client, and no JSON route "
        "exists for it. Kâr payı and finansman both have one."
    )

    # The only account kâr payı actually prices right now. "ARA DÖNEM ÖDEMELİ
    # KATILMA HESABI" (code 2) sits in the same dropdown but answered zero for
    # every currency, maturity and amount tried live on 2026-08-16 -- fixed
    # bands and the flexible one alike -- so it is not offered here rather
    # than kept as a choice that can only ever refuse.
    _PROFIT_SHARE_PRODUCT = Product(
        code="5",
        name="Katılma Hesabı",
        category="profit_share",
        # XAU sits in the same currency dropdown and, like account type 2,
        # answered zero at every amount and maturity tried live -- excluded
        # for the same reason.
        currencies=("TRY", "USD", "EUR"),
        raw={},
    )
    # "Esnek Vadeli" (flexible) -- the one maturity type in the dropdown that
    # takes an exact day count instead of snapping to one of the other four
    # fixed bands (1/3/6/12 months). Verified live from 1 to 800+ days: it
    # answers a real, self-consistent rate across that whole range, so every
    # quote goes through it rather than picking the nearest fixed band.
    _FLEXIBLE_MATURITY = "14"

    def _ajax(self, path: str, **fields):
        return self._json("POST", f"{HOST}/ajax/{path}", headers=HEADERS, data=fields)

    # ----- catalogue -----

    def products(self, category: str) -> list[Product]:
        if category == "profit_share":
            # A single, fixed product rather than a live catalogue call: there
            # is no endpoint that lists which account types/currencies work,
            # only one that answers zero for the ones that do not. Discovered
            # live, not parsed off a page -- see the class-level comment.
            return [self._PROFIT_SHARE_PRODUCT]
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

    def resolve(self, category: str, query: str,
                amount: float | None = None, term: int | None = None) -> Product:
        """Ziraat resolves by band, so it needs the amount and the term.

        Overrides BaseBank.resolve so anything checking that a product name
        still works here — the family table, the daily audit — goes through the
        same path a quote takes. find_product answers "matches several" for
        every banded product and would report a healthy catalogue as broken.
        """
        if category != "finance":
            return self.find_product(category, query)
        return self._resolve(query, amount or 100_000, term or 24)

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
        # Same product-level maintenance gate find_product applies, because
        # this path bypasses it.
        return self._not_under_maintenance("finance", min(fits, key=lambda p: (p.max_term or 0)))

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
            # Checked 2026-08-16 against the full text of a live payment-plan
            # response and the bank's homepage: the summary row states
            # "Finansman Tutarı / Taksit Tutarı / Vade / Kâr Oranı / Toplam
            # Geri Ödenen Tutar" -- the monthly rate and two totals, nothing
            # that annualises them, on this response or anywhere on the site.
            annual_cost_rate=None,
            # Every schedule row already carries its own KDV/KKDF/BSMV split
            # (in `schedule[i].taxes`); this product's happens to be all
            # zeros. No separate one-time fee (allocation, appraisal) appears
            # anywhere in the markup for `fees` to hold.
            fees={},
            schedule=schedule,
            raw={"summary": figures, "rows": len(schedule)},
        ))

    # ----- profit share -----

    def profit_share_quote(
        self,
        product: str,
        amount: float,
        term: int,
        currency: str = "TRY",
        term_unit: str | None = None,
    ) -> ProfitShareQuote:
        """A kâr payı quote, computed by the bank's own `/ajax/karpayi-products`.

        Read `/ajax/finansmanhesapla`'s sibling, not something this project
        invented: same Drupal AJAX shape, same "no browser required" answer,
        found by driving the actual calculator widget and watching what it
        called. Every figure below -- net/gross profit, net/gross annual rate
        -- is the bank's own; nothing here does arithmetic on them.
        """
        chosen = self.find_product("profit_share", product)
        currency = currency.upper()
        if currency not in chosen.currencies:
            raise UnsupportedProduct(
                f"{chosen.name} is not offered in {currency} at "
                f"{self.display_name}. Available: {', '.join(chosen.currencies)}."
            )
        self._check_limits(chosen, amount=amount)
        days = term * 30 if unit(term_unit) == "month" else term

        payload = self._json(
            "POST",
            f"{HOST}/ajax/karpayi-products?_wrapper_format=drupal_ajax",
            headers=HEADERS,
            data={
                "karpayi_hesap_type": chosen.code,
                "karpayi_hesap_currency": currency,
                "karpayi_hesap_anapara": str(int(amount)),
                "karpayi_hesap_vade": str(int(days)),
                "karpayi_maturity_type": self._FLEXIBLE_MATURITY,
                "_drupal_ajax": "1",
            },
        )
        fields = _karpayi_fields(payload)
        net = money(fields.get("kar-payi-net-gelir"))
        # Zeros mean "not offered" here exactly as at every other bank that
        # answers this way (Albaraka, Emlak) -- a combination the calculator
        # does not price, not a request that failed.
        if net <= 0:
            raise self._no_rate(chosen, amount, currency, f"{days} days")

        return self._check_profit_share(ProfitShareQuote(
            bank=self.name,
            product=chosen,
            amount=float(amount),
            term=int(days),
            currency=currency,
            term_unit="day",
            # Not published: this endpoint states the same net/gross rates
            # mapped below and nothing that splits them into a ratio, the same
            # gap as every other profit-share bank here.
            ratio=None,
            gross_profit=money(fields.get("kar-payi-brut-gelir")),
            net_profit=net,
            gross_annual_rate=rate(fields.get("kar-payi-brut-oran")),
            net_annual_rate=rate(fields.get("kar-payi-net-oran")),
            raw=payload,
        ))


def _karpayi_fields(payload) -> dict[str, str]:
    """The kâr payı calculator's answer, keyed by the CSS class it fills.

    The same Drupal command-array shape `_plan_markup` reads for financing,
    except this response fills five named slots (`.kar-payi-net-gelir` and
    friends) instead of one document -- each command's own `selector` is the
    field name, so there is nothing to search markup text for here.
    """
    fields: dict[str, str] = {}
    for command in payload or []:
        selector = (command.get("selector") or "").lstrip(".")
        if command.get("command") == "insert" and selector:
            fields[selector] = command.get("data") or ""
    return fields


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
