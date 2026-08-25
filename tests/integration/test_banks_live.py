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
from banks.providers import BANKS
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
            # By name, not by code: two entries share ELKTRARACSARJUNITE and
            # the code alone cannot say which, so it is refused as ambiguous.
            quote = kuveytturk.finance_quote(product.name, amount, term)
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


@pytest.mark.parametrize("account", ["Katılma Hesabı", "Ara Dönem Kar Payı Ödemeli Katılma Hesabı"])
@pytest.mark.parametrize("months", [1, 3, 6, 12])
def test_kuveytturk_prices_whole_months(kuveytturk, account, months):
    """A month is 30 days for one account and 31 for another.

    Ara Dönem takes exact 30-day multiples and answers 31, 91 and 181 with
    zeros; plain Katılma answers exactly 30 with zeros and wants 31. Neither is
    derivable from the catalogue, so both are offered and the bank picks.
    """
    quote = kuveytturk.profit_share_quote(account, 100_000, months, "TRY", "month")

    assert quote.net_profit > 0 and quote.ratio and quote.ratio > 0
    assert quote.term in (months * 30, months * 30 + 1)


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
        "KTLMHSP", "KTLARDM", "KURKTLMHSP:bireysel", "KURKTLMHSP:ticari",
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
    "account,currency,unit,amount,expected",
    [
        # Confirmed against Albaraka's own page, not merely from our calls
        # failing: all three answer with zeros. They are told apart by whether
        # the endpoint accepted the request at all -- an accepted call
        # returning 0,00 means the account exists and the bank is currently
        # distributing nothing on it, which is its own answer and not the same
        # as "not offered".
        ("KTLMHSP", "XAU", "month", 200, "0% rate"),
        ("KTLARDM", "TRY", "day", 1000, "no profit-share rate"),
        ("KURKTLMHSP:bireysel", "TRY", "month", 1000, "0% rate"),
    ],
)
def test_albaraka_refuses_what_it_does_not_price(
    albaraka, account, currency, unit, amount, expected
):
    # Above the bank's own per-currency minimum (150 grams, 250 lira), so the
    # request really reaches the endpoint. Below it the honest refusal is the
    # minimum itself, which is a different assertion -- the one below.
    with pytest.raises(UnsupportedProduct, match=expected):
        albaraka.profit_share_quote(account, amount, 6, currency, unit)


def test_albaraka_states_its_minimum_instead_of_blaming_the_rate(albaraka):
    """Below the minimum, the answer is the minimum.

    The bank publishes a different floor per currency -- 150 grams of gold
    against 250 lira -- inside the currency select on its own page. Without
    reading them the endpoint just answers zeros and the refusal blamed the
    rate, which sent someone looking for a missing product that was there.
    """
    with pytest.raises(UnsupportedProduct, match="at least 150"):
        albaraka.profit_share_quote("KTLMHSP", 100, 92, "XAU", "day")
    with pytest.raises(UnsupportedProduct, match="at least 250"):
        albaraka.profit_share_quote("KTLMHSP", 100, 92, "TRY", "day")


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
         "amount": 100000, "term_months": 24}
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


# ----- the other six banks -----


@pytest.fixture
def bank(request, live):
    if not live:
        pytest.skip("the banks are not reachable")
    return get_bank(request.param)


@pytest.mark.parametrize(
    "bank", ["vakif", "emlak", "dunya", "ziraat", "turkiyefinans"], indirect=True
)
def test_finance_catalogues_are_live(bank):
    products = bank.products("finance")
    assert products, bank.name
    assert all(p.code and p.name for p in products)
    # Identity has to be unique, and for several banks the obvious field is not:
    # Albaraka repeats ProductCode, Türkiye Finans repeats Code, and Ziraat
    # repeats the product name across term bands.
    assert len({p.code for p in products}) == len(products), bank.name


@pytest.mark.parametrize("bank", ["vakif", "emlak", "dunya", "ziraat"], indirect=True)
def test_every_bank_quotes_financing_the_same_way(bank):
    """The same call against four more banks, changing only which bank.

    If this needed anything else per bank, the interface would be wrong.
    """
    product = _quotable(bank)
    quote = bank.finance_quote(product.code, 100_000, 24)

    assert quote.bank == bank.name
    assert quote.installment > 0
    assert quote.total > quote.amount
    assert quote.profit_rate > 0


def _quotable(bank):
    """A product that takes 100 000 TL over 24 months."""
    for product in bank.products("finance"):
        if (product.max_term or 0) >= 24 and (product.max_amount or 1e12) >= 100_000:
            return product
    pytest.skip(f"{bank.name} has no product covering 100 000 TL over 24 months")


@pytest.mark.parametrize("bank", ["vakif", "emlak", "dunya", "hayat"], indirect=True)
def test_participation_accounts_are_priced(bank):
    """Each bank's own shortest published term, not a term we picked.

    Hayat prices 32 days and nothing shorter; Vakıf and Emlak start at 31.
    Hardcoding one number would test our guess rather than their contract.
    """
    account = bank.products("profit_share")[0]
    term = account.min_term or 31
    quote = bank.profit_share_quote(account.code, 100_000, term, "TRY", "day")
    assert quote.term > 0

    assert quote.net_profit > 0
    assert quote.gross_profit >= quote.net_profit
    assert quote.term_unit in ("day", "month")


@pytest.mark.parametrize("bank", ["vakif"], indirect=True)
def test_vakif_prices_its_card(bank):
    quote = bank.card_installment_quote("Ferah Kart", 10_000, 6)
    assert quote.installment > 0 and quote.total > quote.amount


@pytest.mark.parametrize("bank", ["ziraat"], indirect=True)
def test_ziraat_ceiling_falls_as_the_term_rises(bank):
    short = bank.finance_quote("ihtiyaç finansmanı", 100_000, 12)
    long = bank.finance_quote("ihtiyaç finansmanı", 100_000, 36)

    assert short.product.code != long.product.code
    assert (short.product.max_amount or 0) >= (long.product.max_amount or 0)
    assert long.installment < short.installment


@pytest.mark.parametrize("bank", ["ziraat"], indirect=True)
def test_ziraat_prices_its_public_profit_share_calculator(bank):
    quote = bank.profit_share_quote("Katılma Hesabı", 100_000, 92, "TRY", "day")
    assert quote.net_profit > 0
    assert quote.gross_profit >= quote.net_profit


@pytest.mark.parametrize("bank", ["turkiyefinans"], indirect=True)
def test_turkiyefinans_gives_rates_but_not_payments(bank):
    """It publishes a rate for every product and an instalment for none.

    The quote used to be refused outright, which dropped eighteen products'
    published pricing off the page. It now returns the bank's own rate with
    `installment` left None -- computing the payment here is the one thing the
    rules forbid, and None keeps a rate-only row from ever being sorted as the
    cheapest.
    """
    rows = bank.products("profit_share")
    assert rows and all(r.rate and r.rate > 0 for r in rows)

    quote = bank.finance_quote(bank.products("finance")[0].code, 100_000, 24)
    assert quote.installment is None
    assert quote.total is None
    assert quote.priced is False
    assert quote.profit_rate > 0


@pytest.mark.parametrize("bank", ["hayat"], indirect=True)
def test_hayat_states_its_minimum_rather_than_quoting_zero(bank):
    with pytest.raises(UnsupportedProduct, match="50,000"):
        bank.profit_share_quote("Katılma Hesabı", 49_999, 32)

    priced = bank.profit_share_quote("Katılma Hesabı", 50_000, 32)
    assert priced.net_profit > 0


@pytest.mark.parametrize("bank", ["vakif", "dunya"], indirect=True)
def test_server_side_conversion_is_not_derived(bank):
    result = bank.convert("USD", "TRY", 1000)
    assert result.derived is False
    assert result.result > 1000


@pytest.mark.parametrize("bank", ["hayat"], indirect=True)
def test_rate_only_banks_derive_their_conversion(bank):
    result = bank.convert("USD", "TRY", 1000)
    assert result.derived is True
    assert result.result > 1000


# ----- every declared capability actually answers -----


def test_declared_capabilities_hold_against_the_live_banks(live):
    """capabilities is what the agent is told; this is the check that it is true.

    Every bank that says it publishes rates must return some, every bank that
    says it converts must convert, and the two that say nothing must answer
    without touching the network.
    """
    if not live:
        pytest.skip("the banks are not reachable")

    failures = []
    for bank in BANKS:
        if "rates" in bank.capabilities:
            try:
                assert bank.rates(), "no rates"
            except Exception as exc:  # noqa: BLE001 - collected, reported below
                failures.append(f"{bank.name} rates: {exc}")
        if "convert" in bank.capabilities:
            try:
                assert bank.convert("USD", "TRY", 1000).result > 0
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{bank.name} convert: {exc}")
        if not bank.capabilities:
            with pytest.raises(UnsupportedProduct):
                bank.finance_quote("anything", 100_000, 24)
    assert not failures, failures


# ----- term bands, live -----


@pytest.mark.parametrize("bank", ["kuveytturk", "vakif", "emlak", "dunya"], indirect=True)
def test_a_year_is_priced_as_a_year(bank):
    """The defect this guards: a year answered with the six-month band.

    Three of these banks price a fixed list of month-labelled terms, and a year
    is 360 days — short of their 364/365 band. Snapping down returned about 44%
    of the right figure as a confident quote. Asserting against the one-month
    quote rather than an absolute number keeps this true as rates move.
    """
    account = bank.products("profit_share")[0]
    year = bank.profit_share_quote(account.code, 100_000, 12, "TRY", "month")
    month = bank.profit_share_quote(account.code, 100_000, 1, "TRY", "month")

    assert year.term >= 360, f"{bank.name} priced {year.term} days for a year"
    # A year must be worth several months, not a fraction more than one.
    assert year.net_profit > month.net_profit * 8


@pytest.mark.parametrize("bank", ["vakif", "emlak", "dunya"], indirect=True)
def test_an_unmatched_term_is_refused_not_swapped(bank):
    """`term_unit` defaults to empty, so a bare 12 must not become 31 days."""
    account = bank.products("profit_share")[0]
    with pytest.raises(UnsupportedProduct, match="fixed terms only"):
        bank.profit_share_quote(account.code, 100_000, 12, "TRY", None)


@pytest.mark.parametrize("bank", ["kuveytturk", "hayat"], indirect=True)
def test_gold_is_findable_by_its_standard_code(bank):
    """These feeds call gold "ALT (gr)"; a caller should not have to know."""
    found = bank.find_rates(["XAU"])

    assert len(found) == 1
    assert found[0].sell > 0
    assert found[0].unit == "gram"


@pytest.mark.parametrize("bank", ["hayat"], indirect=True)
def test_hayat_daily_account_is_not_passed_off_as_a_term_quote(bank):
    """It returns one day's profit whatever term is sent."""
    with pytest.raises(UnsupportedProduct, match="does not follow"):
        bank.profit_share_quote("Avantajlı Günlük Hesap", 100_000, 365, "TRY", "day")


@pytest.mark.parametrize("bank", ["vakif"], indirect=True)
def test_vakif_finance_carries_its_payment_plan(bank):
    quote = bank.finance_quote("IF", 100_000, 24)
    assert len(quote.schedule) == 24
    assert quote.schedule[-1].remaining == 0.0


# ----- the family table and comparison, live -----


@pytest.mark.parametrize("category", ["finance", "profit_share"])
def test_every_family_entry_still_resolves(live, category):
    """A bank renaming a product would otherwise drop out of comparisons quietly.

    Resolution goes through resolve(), the same path a quote takes — Ziraat's
    banded products answer "matches several" through find_product and would
    report a healthy table as broken.
    """
    from banks import families, get_bank

    broken = []
    for family in families.families(category):
        for name, query in families.entries(category, family).items():
            try:
                get_bank(name).resolve(category, query, 100_000, 24)
            except Exception as exc:  # noqa: BLE001 - collect them all
                broken.append(f"{category}/{family}/{name}: {str(exc)[:60]}")
    assert not broken, "family entries no longer resolve: " + "; ".join(broken)


def test_comparing_is_faster_than_asking_each_bank(live):
    """The whole point is saving the agent time, so it is asserted."""
    import time

    from banks import compare, get_bank

    for name in ("kuveytturk", "vakif", "emlak", "dunya", "ziraat"):
        get_bank(name).products("finance")   # warm the catalogues for both paths

    together = compare.finance("ihtiyac", 100_000, 24)
    assert len(together.quotes) >= 4

    started = time.monotonic()
    for quote in together.quotes:
        get_bank(quote.bank).finance_quote(quote.product.code, 100_000, 24)
    one_at_a_time = time.monotonic() - started

    assert together.seconds < one_at_a_time


def test_a_comparison_never_loses_a_bank(live):
    """The invariant is over banks, not rows.

    A bank can produce more than one row -- Türkiye Finans prices every product
    sigortalı and sigortasız, and Ziraat lists a campaign package beside its
    standard konut product -- so counting rows would make this pass or fail for
    reasons that have nothing to do with a bank going missing, which is the one
    thing it exists to catch.
    """
    from banks import compare

    result = compare.finance("konut-yeni", 1_000_000, 120)
    expected = {b.name for b in compare._scope("finance", None)}
    assert result.banks_covered == expected
    assert result.quotes, "no bank quoted a 1m konut over 120 months"


def test_a_bank_that_does_not_sell_it_says_so(live):
    from banks import compare

    result = compare.finance("ihtiyac", 100_000, 24)
    missing = {u.bank: u.why for u in result.unavailable}
    assert missing.get("albaraka") == compare.NOT_OFFERED


def test_no_shared_product_family_is_missing_from_the_table(live):
    """Coverage must not fall behind as banks add products.

    A family only earns its place when two banks sell it; this fails when a
    second bank starts selling something the table does not cover yet.
    """
    from banks import families, get_bank

    catalogues = {
        "finance": {
            name: [p.name for p in get_bank(name).products("finance")]
            for name in ("kuveytturk", "albaraka", "vakif", "emlak", "dunya", "ziraat")
        }
    }
    missing = families.shared_families_missing(catalogues)
    assert not missing, "two or more banks sell these and no family covers them: " + "; ".join(missing)
