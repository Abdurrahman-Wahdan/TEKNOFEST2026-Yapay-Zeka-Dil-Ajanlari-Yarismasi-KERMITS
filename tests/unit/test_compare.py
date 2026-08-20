"""Comparing many banks at once.

No network. Provider methods are replaced so the fan-out, the ranking and the
three ways a bank can be missing from a comparison are all exercised for real.
"""

import json

import pytest

from banks import compare, families, get_bank, status
from banks.models import CardInstallmentQuote, FinanceQuote, Product
from banks.providers import BANKS
from banks.providers.base import TemporarilyUnavailable, UnsupportedProduct
from banks.tools import build_tools

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def status_file(tmp_path, monkeypatch):
    monkeypatch.setattr(status.settings, "HEALTH_STATUS_FILE",
                        str(tmp_path / "bank_status.json"))
    status.clear_cache()
    yield
    status.clear_cache()


def quote(bank: str, installment: float) -> FinanceQuote:
    return FinanceQuote(
        bank=bank,
        product=Product(code="X", name="Test Product", category="finance"),
        amount=100_000, term=24, installment=installment,
        total=installment * 24, profit_rate=3.0, annual_cost_rate=None,
        fees={}, schedule=[], raw={},
    )


def answer(monkeypatch, behaviour: dict, family="ihtiyac", keep=()):
    """Give every bank in the family a canned outcome, then compare.

    Every one, not only those named: a bank left unpatched would make a real
    request, which is both slow and not what any of these tests are about.
    """
    outcomes = {name: 5_000.0 for name in families.banks_in("finance", family)}
    outcomes.update(behaviour)
    unknown = set(behaviour) - set(outcomes)
    assert not unknown, f"{unknown} are not in the {family} family"

    for name, outcome in outcomes.items():
        if name in keep:
            # Left with its real method, so the status gate is what answers.
            continue
        bank = get_bank(name)

        def reply(self, *args, _outcome=outcome, _name=name, **kwargs):
            if isinstance(_outcome, Exception):
                raise _outcome
            return quote(_name, _outcome)

        monkeypatch.setattr(type(bank), "finance_quote", reply)

    # Anything reaching the network here is a bug in the test, not a slow bank.
    from banks.providers import base as provider_base

    def forbidden(*args, **kwargs):
        raise AssertionError("a comparison test must not touch the network")

    monkeypatch.setattr(provider_base, "request_json", forbidden)
    monkeypatch.setattr(provider_base, "request_text", forbidden)
    return compare.finance(family, 100_000, 24)


# ----- the family table -----


def test_every_family_entry_names_a_bank_that_can_serve_it():
    """A typo, a removed bank, or a family pointing at a bank without the
    capability all fail here rather than at a user's question."""
    assert families.unknown_banks() == []


def test_every_family_has_at_least_one_live_bank():
    """A one-bank family is still a valid live-comparison option.

    It returns that bank's live quote and explains that the other banks do not
    offer an equivalent; hiding it in a separate catalogue would make a real
    calculator unreachable from the comparison screen.
    """
    for category, table in families.BY_CATEGORY.items():
        for family, entries in table.items():
            assert entries, f"{category}/{family} has no bank to quote"


def test_a_one_bank_family_returns_its_live_row_and_reports_the_others(monkeypatch):
    result = answer(monkeypatch, {"albaraka": 8_222.41}, family="cevre")

    assert [row.bank for row in result.quotes] == ["albaraka"]
    assert {row.bank for row in result.unavailable} >= {
        "kuveytturk", "vakif", "emlak", "dunya", "ziraat", "turkiyefinans", "tom",
    }


def test_every_family_has_a_label():
    for table in families.BY_CATEGORY.values():
        for family in table:
            assert families.label(family) != family, f"{family} has no label"


def test_a_turkish_word_that_could_mean_two_products_says_which():
    with pytest.raises(ValueError, match="konut-yeni or konut-2el"):
        families.entries("finance", "konut")
    with pytest.raises(ValueError, match="tasit-0km or tasit-2el"):
        families.entries("finance", "taşıt")


def test_an_unambiguous_turkish_word_resolves():
    assert families.entries("finance", "ihtiyaç finansmanı")
    assert families.entries("finance", "arsa")


def test_an_unknown_family_lists_the_real_ones():
    with pytest.raises(ValueError, match="konut-yeni"):
        families.entries("finance", "bilmemne")


# ----- the fan-out -----


def test_the_cheapest_is_ranked_first_and_named(monkeypatch):
    result = answer(monkeypatch, {
        "kuveytturk": 900.0, "vakif": 700.0, "emlak": 800.0,
        "dunya": 1000.0, "ziraat": 1100.0,
    })
    assert [q.bank for q in sorted(result.quotes, key=lambda q: q.installment)][0] == "vakif"
    # Derived, not counted by hand: `answer` gives every bank in the family an
    # outcome, so the ranking is exactly the family. Hardcoding the number means
    # adding a bank to a family breaks a test about sorting.
    #
    # Rows, not banks: Türkiye Finans holds two entries in this family (its
    # products are priced sigortalı and sigortasız) and both are real answers.
    assert len(result.quotes) == len(families.members("finance", "ihtiyac"))
    assert {q.bank for q in result.quotes} == set(
        families.banks_in("finance", "ihtiyac")
    )


def test_no_bank_is_silently_dropped(monkeypatch):
    """Ranked plus not-compared always equals the banks in scope."""
    result = answer(monkeypatch, {
        "kuveytturk": 900.0,
        "vakif": UnsupportedProduct("over its ceiling"),
        "emlak": TemporarilyUnavailable("under maintenance"),
        "dunya": KeyError("shape changed"),
        "ziraat": 1100.0,
    })
    # Albaraka has no ihtiyaç product, so it is in scope and not offered; T.O.M.
    # now publishes a financing calculator, so it is in scope too.
    #
    # The invariant is over BANKS, not rows. A bank can produce more than one
    # row -- Türkiye Finans prices sigortalı and sigortasız -- so counting rows
    # would make this pass or fail for reasons that have nothing to do with a
    # bank going missing, which is the only thing it is here to catch.
    expected = {b.name for b in compare._scope("finance", None)}
    assert result.banks_covered == expected


def test_maintenance_is_never_reported_as_not_offered(monkeypatch):
    """TemporarilyUnavailable subclasses UnsupportedProduct, so the order the
    fan-out catches them in decides whether a user is told the wrong thing."""
    result = answer(monkeypatch, {
        "kuveytturk": TemporarilyUnavailable("the bank could not be reached"),
        "vakif": UnsupportedProduct("that amount is over its ceiling"),
    }, )
    reasons = {u.bank: u.why for u in result.unavailable}
    assert reasons["kuveytturk"] == compare.MAINTENANCE
    assert reasons["vakif"] == compare.DECLINED


def test_a_bank_that_does_not_sell_it_is_reported_not_hidden(monkeypatch):
    result = answer(monkeypatch, {"kuveytturk": 900.0})
    missing = {u.bank: u for u in result.unavailable}
    assert missing["albaraka"].why == compare.NOT_OFFERED
    assert "does not offer" in missing["albaraka"].detail


def test_one_bank_failing_does_not_fail_the_comparison(monkeypatch):
    result = answer(monkeypatch, {
        "kuveytturk": 900.0, "vakif": RuntimeError("boom"), "emlak": 800.0,
    })
    assert [u.why for u in result.unavailable].count(compare.ERROR) == 1
    # Every other bank in the family still answered.
    assert {q.bank for q in result.quotes} == set(
        families.banks_in("finance", "ihtiyac")
    ) - {"vakif"}


def test_a_recorded_outage_surfaces_as_maintenance(monkeypatch):
    """Comparison runs through the status gate rather than around it."""
    status.write({"vakif": {"finance": status.entry(status.DOWN, "could not be reached")}})
    result = answer(monkeypatch, {"kuveytturk": 900.0, "emlak": 800.0}, keep=("vakif",))
    assert {u.bank: u.why for u in result.unavailable}["vakif"] == compare.MAINTENANCE


# ----- card -----

CARD_BANKS = ("kuveytturk", "vakif", "turkiyefinans")


def card_quote(bank: str, card: str, installment: float | None) -> CardInstallmentQuote:
    return CardInstallmentQuote(
        bank=bank,
        card=Product(code=card, name=card, category="card"),
        amount=10_000, installments=6,
        installment=installment,
        total=installment * 6 if installment is not None else None,
        profit_rate=4.0,
        raw={},
    )


def answer_card(monkeypatch, catalogue: dict, price: dict):
    """Give each bank in CARD_BANKS a card catalogue and a price per card.

    `catalogue` maps bank -> list of card names; `price` maps card name ->
    installment (None for a rate-only row, an Exception to simulate a refusal).
    Any bank in CARD_BANKS left out of `catalogue` gets no cards, exactly as
    a real bank with an empty products("card") answer would.
    """
    for name in CARD_BANKS:
        bank = get_bank(name)
        cards = catalogue.get(name, [])

        def products(self, category, _cards=cards):
            assert category == "card"
            return [Product(code=c, name=c, category="card") for c in _cards]

        def quote(self, card, amount, installments, _name=name):
            outcome = price[card]
            if isinstance(outcome, Exception):
                raise outcome
            return card_quote(_name, card, outcome)

        monkeypatch.setattr(type(bank), "products", products)
        monkeypatch.setattr(type(bank), "card_installment_quote", quote)

    from banks.providers import base as provider_base

    def forbidden(*args, **kwargs):
        raise AssertionError("a comparison test must not touch the network")

    monkeypatch.setattr(provider_base, "request_json", forbidden)
    monkeypatch.setattr(provider_base, "request_text", forbidden)
    return compare.card(10_000, 6)


def test_card_quotes_every_card_a_bank_publishes(monkeypatch):
    """A bank with five cards contributes five rows, not one."""
    result = answer_card(
        monkeypatch,
        {"kuveytturk": ["SK", "BP", "TK"], "vakif": ["FK"]},
        {"SK": 900.0, "BP": 850.0, "TK": 950.0, "FK": 700.0},
    )
    assert len(result.quotes) == 4
    assert {q.bank for q in result.quotes} == {"kuveytturk", "vakif"}
    assert sorted(q.card.name for q in result.quotes if q.bank == "kuveytturk") == [
        "BP", "SK", "TK",
    ]


def test_card_cheapest_across_banks_is_ranked_first(monkeypatch):
    result = answer_card(
        monkeypatch,
        {"kuveytturk": ["SK"], "vakif": ["FK"]},
        {"SK": 900.0, "FK": 700.0},
    )
    cheapest = min(result.quotes, key=lambda q: q.installment)
    assert cheapest.bank == "vakif"


def test_card_rate_only_row_can_never_win(monkeypatch):
    """Türkiye Finans states a rate and no payment -- it must sink, not sort
    as if a missing instalment were free."""
    result = answer_card(
        monkeypatch,
        {"kuveytturk": ["SK"], "turkiyefinans": ["Kredi Kartı Taksitle"]},
        {"SK": 900.0, "Kredi Kartı Taksitle": None},
    )
    priced = [q for q in result.quotes if q.priced]
    unpriced = [q for q in result.quotes if not q.priced]
    assert {q.bank for q in priced} == {"kuveytturk"}
    assert {q.bank for q in unpriced} == {"turkiyefinans"}


def test_card_bank_with_no_catalogue_is_reported_not_hidden(monkeypatch):
    result = answer_card(monkeypatch, {"kuveytturk": ["SK"]}, {"SK": 900.0})
    missing = {u.bank: u for u in result.unavailable}
    assert missing["vakif"].why == compare.NOT_OFFERED
    assert missing["turkiyefinans"].why == compare.NOT_OFFERED


def test_compare_card_names_the_cheapest(monkeypatch):
    answer_card(
        monkeypatch,
        {"kuveytturk": ["SK"], "vakif": ["FK"]},
        {"SK": 900.0, "FK": 700.0},
    )
    tool = next(t for t in build_tools() if t.name == "compare_card")
    payload = json.loads(tool.invoke({"amount": 10000, "installments": 6}))

    assert payload["cheapest"] == "vakif"
    assert payload["ranked"][0]["bank"] == "vakif"


# ----- the tools -----


def test_compare_finance_names_the_cheapest(monkeypatch):
    answer(monkeypatch, {"kuveytturk": 900.0, "vakif": 700.0})
    tool = next(t for t in build_tools() if t.name == "compare_finance")
    payload = json.loads(tool.invoke(
        {"family": "ihtiyac", "amount": 100000, "term_months": 24}))

    assert payload["cheapest"] == "vakif"
    assert payload["ranked"][0]["bank"] == "vakif"
    # Banks, not rows: eight publish a financing calculator, and Türkiye
    # Finans contributes two rows to this family on its own.
    expected = len(compare._scope("finance", None))
    assert payload["compared"] == expected
    assert len({r["bank"] for r in payload["ranked"]}
               | {r["bank"] for r in payload["not_compared"]}) == expected


def test_shared_values_are_hoisted_out_of_every_row(monkeypatch):
    """Eight copies of the same amount is prompt weight that buys nothing."""
    answer(monkeypatch, {"kuveytturk": 900.0, "vakif": 700.0})
    tool = next(t for t in build_tools() if t.name == "compare_finance")
    payload = json.loads(tool.invoke(
        {"family": "ihtiyac", "amount": 100000, "term_months": 24}))

    assert payload["amount"] == 100_000
    assert "amount" not in payload["ranked"][0]


def test_compare_profit_share_insists_on_one_term_unit():
    tool = next(t for t in build_tools() if t.name == "compare_profit_share")
    for arguments in ({}, {"term_months": 12, "term_days": 90}):
        out = tool.invoke({"family": "katilma", "amount": 100000, **arguments})
        assert "exactly one" in out


def test_an_unknown_family_is_a_sentence_not_a_traceback():
    tool = next(t for t in build_tools() if t.name == "compare_finance")
    out = tool.invoke({"family": "nope", "amount": 100000, "term_months": 24})
    assert not out.startswith("{")
    assert "konut-yeni" in out


def test_naming_only_banks_that_cannot_be_compared_says_who_can(monkeypatch):
    """An empty ranking is an answer with no information in it."""
    with pytest.raises(UnsupportedProduct, match="It is sold by"):
        compare.finance("ihtiyac", 100_000, 24, banks=["adil"])
    with pytest.raises(UnsupportedProduct, match="These do"):
        compare.exchange("USD", "TRY", 1000, banks=["ziraat"])


def test_the_tool_turns_that_into_a_sentence():
    tool = next(t for t in build_tools() if t.name == "compare_finance")
    out = tool.invoke({"family": "ihtiyac", "amount": 100000,
                       "term_months": 24, "banks": ["adil"]})
    assert not out.startswith("{")
    assert "kuveytturk" in out
