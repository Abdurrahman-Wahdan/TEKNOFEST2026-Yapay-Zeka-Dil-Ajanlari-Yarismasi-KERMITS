"""Live checks against both banks' calculators.

Needs the internet and nothing else — these endpoints take no cookie, session
or key. Albaraka additionally needs curl_cffi, because its WAF fingerprints the
TLS handshake.

Assertions are contract assertions, lifted from docs/discovery/verify_*.py: a
field is present, a type is right, a number is in a sane range. Never an exact
value — rates change daily and that change is not a failure.
"""

import pytest

from banks import build_tools, get_bank
from banks.providers.base import UnsupportedProduct
from banks.providers.kuveytturk import _params

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@pytest.fixture(scope="module")
def live() -> bool:
    """Whether the banks are reachable at all."""
    import httpx

    try:
        return httpx.get("https://www.kuveytturk.com.tr", timeout=10).status_code < 500
    except Exception:  # noqa: BLE001 - any failure means unavailable
        return False


@pytest.fixture
def kuveytturk(live):
    if not live:
        pytest.skip("kuveytturk.com.tr is not reachable")
    return get_bank("kuveytturk")


@pytest.fixture
def albaraka(live):
    if not live:
        pytest.skip("albaraka.com.tr is not reachable")
    return get_bank("albaraka")


# ----- Kuveyt Türk -----


def test_kuveytturk_catalogue_is_live(kuveytturk):
    """The catalogue is the discovery key: everything else needs a code from it."""
    finance = kuveytturk.products("finance")
    assert len(finance) == 19
    assert all(p.code and p.name for p in finance)
    assert len(kuveytturk.products("profit_share")) >= 7
    assert len(kuveytturk.products("card")) >= 4


def test_kuveytturk_prices_every_finance_product(kuveytturk):
    """All 19, at the term each catalogue entry declares for itself.

    MaturityTerm, not MaturityTermMax: two entries share the code
    ELKTRARACSARJUNITE and the endpoint validates the term against the entry
    named in the request, so the wrong one is a 400.
    """
    failures = []
    for product in kuveytturk.products("finance"):
        terms = _params(product.raw)["MaturityTerm"]
        term = int(terms[0]) if terms else 12
        low = product.min_amount or 1000
        high = product.max_amount or 5_000_000
        amount = min(max(100_000, low), high)
        try:
            quote = kuveytturk.finance_quote(product.code, amount, term)
        except ValueError as exc:
            failures.append(f"{product.name}: {exc}")
            continue
        if not (
            quote.installment > 0
            and quote.total > quote.amount
            and quote.term == term
            and len(quote.schedule) == term
            and quote.profit_rate > 0
        ):
            failures.append(f"{product.name}: implausible quote {quote.installment}")
    assert not failures, failures


def test_kuveytturk_prices_every_participation_account(kuveytturk):
    """Every account in every currency it declares, except the one with no rate."""
    failures = []
    for account in kuveytturk.products("profit_share"):
        for currency in account.currencies:
            amount = 100 if currency == "XAU" else 100_000
            try:
                quote = kuveytturk.profit_share_quote(
                    account.code, amount, 3, currency, "month"
                )
            except UnsupportedProduct as exc:
                # Yuvam publishes no rate at all, on the bank's own page too.
                if "Yuvam" in str(exc):
                    continue
                failures.append(f"{account.name} {currency}: {exc}")
                continue
            # A zero is not a price: an unsupported combination answers 200
            # with every field zero rather than an error.
            if not (quote.net_profit > 0 and quote.ratio and quote.ratio > 0):
                failures.append(f"{account.name} {currency}: zero quote")
    assert not failures, failures


def test_kuveytturk_refuses_yuvam(kuveytturk):
    """A gap on the bank's side stays a refusal, not a retry and not a zero."""
    with pytest.raises(UnsupportedProduct, match="Yuvam"):
        kuveytturk.profit_share_quote("YUVAMKATILMA", 100_000, 3, "USD", "month")


def test_kuveytturk_months_are_not_read_as_days(kuveytturk):
    """The endpoint counts days whatever the day/month flag says.

    Twelve months must be worth far more than twelve days. Asserting the
    relationship rather than either number keeps this true as rates move.
    """
    year = kuveytturk.profit_share_quote("Katılma Hesabı", 100_000, 12, "TRY", "month")
    days = kuveytturk.profit_share_quote("Katılma Hesabı", 100_000, 12, "TRY", "day")

    assert year.term == 360 and days.term == 12
    assert year.net_profit > days.net_profit * 20


def test_kuveytturk_surfaces_the_banks_own_refusal(kuveytturk):
    """An out-of-range term is a 400 carrying a usable Turkish sentence."""
    with pytest.raises(ValueError) as exc:
        kuveytturk.profit_share_quote("KTDIJITALHESAP", 100_000, 3, "TRY", "day")
    assert "değer" in str(exc.value)


def test_kuveytturk_prices_every_card(kuveytturk):
    failures = []
    for card in kuveytturk.products("card"):
        # The catalogue over-promises: Sağlam Kart Troy declares 12 and the
        # endpoint refuses anything above 9, so ask for a count all of them take.
        try:
            quote = kuveytturk.card_installment_quote(card.name, 10_000, 6)
        except ValueError as exc:
            failures.append(f"{card.name}: {exc}")
            continue
        if not (quote.installment > 0 and quote.total > quote.amount):
            failures.append(f"{card.name}: implausible quote")
    assert not failures, failures


def test_kuveytturk_refuses_more_instalments_than_the_calculator_takes(kuveytturk):
    with pytest.raises(UnsupportedProduct, match="refused"):
        kuveytturk.card_installment_quote("Sağlam Kart Troy", 10_000, 12)


def test_kuveytturk_publishes_fx_and_metal_rates(kuveytturk):
    rates = {r.code: r for r in kuveytturk.rates()}

    assert len(rates) >= 20
    for code in ("USD", "EUR", "GBP", "ALT (gr)", "GMS (gr)", "ZCeyrek"):
        assert code in rates, code
        assert rates[code].sell > 0 and rates[code].sell >= rates[code].buy
    assert rates["ALT (gr)"].unit == "gram"


def test_kuveytturk_conversion_uses_the_quoted_rate(kuveytturk):
    """There is no converter endpoint, so this is the agreed derived figure."""
    rates = {r.code: r for r in kuveytturk.rates()}
    result = kuveytturk.convert("XAU", "TRY", 10)

    assert result.derived is True
    assert float(result.result) == pytest.approx(rates["ALT (gr)"].buy * 10, rel=1e-6)


# ----- Albaraka -----


def test_albaraka_catalogue_parses_out_of_the_live_page(albaraka):
    """Parsed from the page, so an empty list usually means the WAF rejected us."""
    products = albaraka.products("finance")
    assert len(products) >= 10
    assert all(p.code and p.name for p in products)
    assert len({p.code for p in products}) == len(products)
    assert [a.code for a in albaraka.products("profit_share")] == [
        "KTLMHSP", "KTLARDM", "KURKTLMHSP",
    ]


def test_albaraka_prices_every_finance_product(albaraka):
    failures = []
    for product in albaraka.products("finance"):
        term = min(24, product.max_term or 24)
        term = max(term, product.min_term or 1)
        amount = int(min(100_000.0, product.max_amount or 100_000.0))
        try:
            quote = albaraka.finance_quote(product.code, amount, term)
        except ValueError as exc:
            failures.append(f"{product.name}: {exc}")
            continue
        if not (
            quote.installment > 0
            and quote.total > quote.amount
            and len(quote.schedule) == term
            and quote.profit_rate > 0
        ):
            failures.append(f"{product.name}: implausible quote {quote.installment}")
    assert not failures, failures


def test_albaraka_prices_the_accounts_it_offers(albaraka):
    """Katılma in every currency but gold, Ara Dönem in months only."""
    for currency in ("TRY", "USD", "EUR"):
        quote = albaraka.profit_share_quote("KTLMHSP", 100_000, 6, currency, "month")
        assert quote.net_profit > 0
        assert quote.gross_profit >= quote.net_profit
        assert quote.gross_annual_rate and quote.gross_annual_rate > 0
        assert quote.ratio is None

    day = albaraka.profit_share_quote("KTLMHSP", 100_000, 90, "TRY", "day")
    assert day.net_profit > 0 and day.term_unit == "day"

    interim = albaraka.profit_share_quote("KTLARDM", 100_000, 6, "TRY", "month")
    assert interim.net_profit > 0


@pytest.mark.parametrize(
    "account,currency,unit",
    [
        # Confirmed against Albaraka's own page, not merely from our calls
        # failing: all three answer 200 with zeros.
        ("KTLMHSP", "XAU", "month"),
        ("KTLARDM", "TRY", "day"),
        ("KURKTLMHSP", "TRY", "month"),
    ],
)
def test_albaraka_refuses_what_it_does_not_price(albaraka, account, currency, unit):
    with pytest.raises(UnsupportedProduct, match="no profit-share rate"):
        albaraka.profit_share_quote(account, 100, 6, currency, unit)


def test_albaraka_publishes_fx_and_gold_rates(albaraka):
    rates = {r.code: r for r in albaraka.rates()}

    assert {"USD", "EUR", "XAU", "GBP"} <= set(rates)
    for row in rates.values():
        assert row.sell > 0 and row.sell >= row.buy
    assert rates["XAU"].unit == "gram"


def test_albaraka_converts_server_side(albaraka):
    """Unlike Kuveyt Türk, Albaraka converts for us, gold included."""
    for source, target, amount in (
        ("USD", "TRY", 1000), ("EUR", "TRY", 1000),
        ("TRY", "USD", 100_000), ("XAU", "TRY", 10),
    ):
        result = albaraka.convert(source, target, amount)
        assert result.derived is False
        assert result.result > 0


def test_albaraka_has_no_card_calculator(albaraka):
    with pytest.raises(UnsupportedProduct, match="does not publish"):
        albaraka.card_installment_quote("any", 10_000, 6)


# ----- the tools, end to end -----


def test_the_tools_answer_for_both_banks(live):
    """One tool per category, `bank` as a parameter, against the live endpoints."""
    if not live:
        pytest.skip("the banks are not reachable")

    tools = {t.name: t for t in build_tools()}

    for bank in ("kuveytturk", "albaraka"):
        answer = tools["exchange_rates"].invoke({"bank": bank, "codes": ["USD"]})
        assert '"code":"USD"' in answer

    turkish = tools["finance_quote"].invoke(
        {"bank": "kuveytturk", "product": "ihtiyaç finansmanı",
         "amount": 100000, "term": 24}
    )
    assert '"monthly_installment"' in turkish

    derived = tools["convert_currency"].invoke(
        {"bank": "kuveytturk", "source": "XAU", "target": "TRY", "amount": 10}
    )
    assert '"derived":true' in derived

    quoted = tools["convert_currency"].invoke(
        {"bank": "albaraka", "source": "XAU", "target": "TRY", "amount": 10}
    )
    assert '"derived":false' in quoted
