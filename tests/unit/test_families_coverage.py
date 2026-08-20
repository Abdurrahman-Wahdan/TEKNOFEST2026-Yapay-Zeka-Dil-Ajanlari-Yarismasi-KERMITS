"""Every bank that can be compared is in a family to be compared in.

`unknown_banks()` already checks the family -> bank direction: a family may not
name a bank that does not exist. Nothing checked the reverse, and that is the
direction that goes wrong silently: T.O.M. declared `finance` and sold exactly
one product while appearing in no family, so every comparison answered "T.O.M.
does not offer this" -- a false statement about the one product it does offer,
rendered to users for as long as nobody looked.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from banks import families, list_banks
from banks.taxonomy import classify, family_key

# Every product name every bank published, captured live. Checked in so the
# coverage check runs offline: it has to fail in CI when the map falls behind,
# and a test that needs ten bank endpoints to be up would be skipped instead.
# Refresh with scripts/refresh_catalogues.py after a bank changes its range.
_CAPTURED = json.loads(
    (Path(__file__).parents[1] / "fixtures/banks/catalogues.json").read_text()
)

# {category: {bank: [product name]}} — what the family checks read. The fixture
# stores code and name per product, because addressability needs both.
CATALOGUES = {
    category: {bank: [p["name"] for p in products] for bank, products in per_bank.items()}
    for category, per_bank in _CAPTURED.items()
}

# A bank may legitimately publish a capability and still belong to no family:
# its products are all single-bank, so no comparison exists. Naming them here
# makes that a decision rather than an oversight.
NO_SHARED_PRODUCTS: dict[str, set[str]] = {
    "finance": set(),
    "profit_share": set(),
}

CAPABILITY_FOR = {"finance": "finance", "profit_share": "profit_share"}


def test_every_selectable_family_has_exactly_one_semantic_group():
    """A live calculator must be discoverable under a meaningful picker group."""
    selectable = {
        family
        for table in families.BY_CATEGORY.values()
        for family in table
    }
    assert set(families.FAMILY_GROUPS) == selectable
    assert all(families.group(family) for family in selectable)


@pytest.mark.parametrize("category", sorted(CAPABILITY_FOR))
def test_every_capable_bank_belongs_to_a_family(category):
    capability = CAPABILITY_FOR[category]
    capable = {
        name for name, entry in list_banks().items()
        if capability in entry["publishes"]
    }
    placed = {
        member.bank
        for family in families.BY_CATEGORY[category].values()
        for member in family
    }
    missing = capable - placed - NO_SHARED_PRODUCTS[category]
    assert not missing, (
        f"{sorted(missing)} publish {capability!r} but appear in no {category} "
        "family, so every comparison will report that they do not offer the "
        "product. Add them to the families they sell, or to NO_SHARED_PRODUCTS "
        "with a reason."
    )


def test_no_product_two_banks_sell_is_left_out_of_the_families():
    """The direction that goes wrong quietly: a product nobody mapped.

    Read straight off the captured catalogues through the taxonomy, so a bank
    renaming a product or adding one lands in the right family without anyone
    matching keywords by hand. This is the check that found Türkiye Finans'
    eighteen unmapped products and the three families -- motosiklet,
    tasit-dijital, ihtiyac-kart -- that had quietly reached a second bank.
    """
    gaps = families.uncovered(CATALOGUES)
    assert not gaps, (
        "these products are sold by two or more banks and no family covers "
        "them, so they cannot be compared:\n  " + "\n  ".join(gaps)
    )


@pytest.mark.parametrize("category", sorted(CAPABILITY_FOR))
def test_every_captured_product_name_lands_somewhere(category):
    """A name the taxonomy cannot read is a silent hole in the map.

    An unclassified product is not reported by `uncovered` -- it has no family
    key to be missing from -- so it would vanish rather than fail. This ran on
    finance only at first, which is exactly how the gold participation account
    stayed invisible: every profit_share name returned None and was skipped.

    Türkiye Finans is excluded for profit_share: it declares no such capability
    and its 55 "rows" are a rate table (`TL 32-91 gün, bireysel, en az 250 TL`),
    not accounts anyone can be quoted on.
    """
    capable = {
        name for name, entry in list_banks().items()
        if CAPABILITY_FOR[category] in entry["publishes"]
    }
    unreadable = [
        f"{bank}: {name}"
        for bank, names in CATALOGUES[category].items()
        if bank in capable
        for name in names
        if family_key(name, category) is None
    ]
    assert not unreadable, (
        "banks/taxonomy.py cannot work out what these products are, so they "
        "can never be matched against another bank:\n  " + "\n  ".join(unreadable)
    )


def test_the_two_konut_axes_never_share_a_family():
    """Ownership and property condition are different questions.

    "İLK EVİM KONUT FİNANSMANI" is a first-home loan and "Sıfır Konut
    Finansmanı" is a new-build loan. They were mapped to the same family, so
    the app ranked Albaraka's first-home product against Vakıf's new-build one
    and called them the same product -- a confident wrong answer rather than a
    visible failure. Both banks price konut identically today, which is why it
    went unnoticed, so this is pinned rather than left to inspection.
    """
    condition = {"konut-yeni", "konut-2el"}
    ownership = {"konut-ilk", "konut-sonraki"}

    for family in condition | ownership:
        assert family in families.FINANCE, f"{family} is missing from the map"

    def specific(family):
        """Banks whose own product is specific to this family."""
        return {m.bank for m in families.FINANCE[family] if not m.general}

    for left in condition:
        for right in ownership:
            shared = specific(left) & specific(right)
            assert not shared, (
                f"{sorted(shared)} appear as a specific product in both {left} "
                f"and {right}, which splits konut on two different axes"
            )

    # The banks that gave the axes their names, pinned by product code.
    assert ("albaraka", "YKKNT0B") in {
        (m.bank, m.query) for m in families.FINANCE["konut-ilk"]
    }
    assert ("albaraka", "VRKNT0B") in {
        (m.bank, m.query) for m in families.FINANCE["konut-sonraki"]
    }


def test_a_general_product_joins_every_family_under_its_purpose():
    """A bank that does not split an axis answers for all of it.

    Kuveyt Türk and Ziraat each sell one konut product. Listing it under only
    one of the four konut families would report the other three as "does not
    offer" -- the same false sentence the T.O.M. bug produced.
    """
    from banks.taxonomy import GENERAL

    for purpose, splits in GENERAL.items():
        generals = {
            m.bank
            for split in splits
            for m in families.FINANCE[families.TAXONOMY_KEYS.get(split, split)]
            if m.general
        }
        for split in splits:
            family = families.TAXONOMY_KEYS.get(split, split)
            listed = {m.bank for m in families.FINANCE[family] if m.general}
            assert listed == generals, (
                f"{purpose}: {sorted(generals - listed)} sell one product "
                f"covering the whole axis but are not listed in {family}"
            )


def test_a_bank_listed_twice_is_listed_with_different_products():
    """Two rows from one bank must be two products, not one repeated."""
    for category, table in families.BY_CATEGORY.items():
        for family, members in table.items():
            seen = {}
            for member in members:
                key = (member.bank, member.query)
                assert key not in seen, (
                    f"{category}/{family} lists {member.bank} twice with the "
                    f"same product {member.query!r}"
                )
                seen[key] = member
            # Where a bank appears more than once, every entry must say which
            # variant it is, or the rows are indistinguishable in the answer.
            counts = {}
            for member in members:
                counts.setdefault(member.bank, []).append(member)
            for bank, entries in counts.items():
                if len(entries) > 1:
                    variants = [m.variant for m in entries]
                    assert len(set(variants)) == len(variants), (
                        f"{category}/{family}: {bank} has {len(entries)} entries "
                        f"but variants {variants} do not tell them apart"
                    )


@pytest.mark.parametrize("category", sorted(CAPABILITY_FOR))
def test_single_bank_products_are_named_with_the_bank_that_sells_them(category):
    """The excluded list has to stay true, or it is just stale commentary."""
    capable = {
        name for name, entry in list_banks().items()
        if CAPABILITY_FOR[category] in entry["publishes"]
    }
    scoped = {b: n for b, n in CATALOGUES[category].items() if b in capable}
    found = classify(scoped, category)
    for key, bank in families.SINGLE_BANK.get(category, {}).items():
        sellers = found.get(key, {})
        assert sellers, f"{key} is listed as sold by {bank} but nothing matches it"
        assert set(sellers) == {bank}, (
            f"{key} is listed as single-bank ({bank}) but is sold by "
            f"{sorted(sellers)} — it now needs a family"
        )


def test_a_family_nobody_prices_is_recorded_rather_than_shipped():
    """A comparison where every bank always refuses is not a comparison.

    Albaraka and Kuveyt Türk both sell the interim-profit account and neither
    publishes a rate for it -- measured across every amount and term. Shipping
    the family would add a dropdown entry that can only ever answer with two
    refusals, so it is recorded here instead, with the reason.
    """
    for category, entries in families.NOT_PRICED.items():
        table = families.BY_CATEGORY[category]
        for key, reason in entries.items():
            assert key not in table, (
                f"{category}/{key} is recorded as unpriced but also shipped as "
                f"a family; one of the two is wrong"
            )
            assert reason, f"{category}/{key} needs a reason, not an empty string"


def test_gold_is_its_own_participation_family():
    """Gold is a product, not a currency option on the ordinary account.

    Kuveyt Türk's dedicated "Altına Altın Katılma Hesabı" pays a 40% ratio
    where its ordinary account pays 95%. Pricing gold through `katilma`
    answers with a rate nobody opening the gold account would get, so the two
    families must never share a bank's specific product.
    """
    assert "katilma-altin" in families.PROFIT_SHARE

    def specific(family):
        return {m.query for m in families.PROFIT_SHARE[family] if not m.general}

    assert not specific("katilma") & specific("katilma-altin"), (
        "the same product is listed as specific to both the ordinary and the "
        "gold participation family"
    )
    # The two banks that sell a dedicated gold account, pinned.
    dedicated = {m.bank for m in families.PROFIT_SHARE["katilma-altin"] if not m.general}
    assert dedicated == {"kuveytturk", "dunya"}


def test_a_currency_intersection_survives_a_bank_using_its_own_labels():
    """The picker is built from this, so an empty set is a dead control.

    Emlak's catalogue reported "TL" and "ALT (gr)" where every other bank
    reports "TRY" and "XAU", so the intersection across any participation
    family came out empty and the UI fell back to a hardcoded TRY. Gold and
    foreign-currency comparisons were unreachable, and nothing failed.
    """
    from banks.parse import canonical_currency

    assert canonical_currency("TL") == "TRY"
    assert canonical_currency("ALT (gr)") == "XAU"
    assert canonical_currency("GMS (gr)") == "XAG"
    # An unknown code passes through upper-cased rather than being dropped:
    # losing a currency silently is the failure this exists to prevent.
    assert canonical_currency("usd") == "USD"
    assert canonical_currency("ZZZ") == "ZZZ"


def test_the_api_and_the_catalogue_canonicalise_the_same_way():
    """Two tables that must agree eventually do not, so there is only one."""
    from api.converters import canonical_code
    from banks.parse import canonical_currency

    for code in ("TL", "ALT (gr)", "GMS (gr)", "USD", "EUR", "XAU"):
        assert canonical_code(code) == canonical_currency(code)


def test_rate_response_rejects_duplicate_comparable_identities():
    """A frontend map must never silently replace one live quote with another."""
    from api.converters import DuplicateRateIdentity, rate_list_out
    from banks.models import Rate

    rows = [
        Rate(code="USD", name="US Doları", buy=47.0, sell=48.0),
        Rate(code="USD", name="US Doları", buy=47.1, sell=48.1),
    ]
    with pytest.raises(DuplicateRateIdentity, match="duplicate live rate identity"):
        rate_list_out(rows)


def test_rate_response_keeps_different_quote_bases_distinct():
    from api.converters import rate_list_out
    from banks.models import Rate

    rows = [
        Rate(code="ALT (gr)", name="Altın-Gr", buy=6800.0, sell=7000.0, unit="gram"),
        Rate(code="XAU", name="Altın-Ons (USD)", buy=4500.0, sell=4550.0, unit="ounce", quote_currency="USD"),
    ]
    assert len(rate_list_out(rows)) == 2


def test_every_capability_has_somewhere_to_be_compared():
    """A capability with no comparison is data nobody can reach.

    Türkiye Finans was exactly this: it published a rate for eighteen finance
    products and appeared in no comparison, so the bank was simply absent from
    the page with no explanation. This pins the mapping so a capability added
    to a provider cannot sit unreachable.
    """
    from banks.providers import BANKS

    # capability -> the comparison category that surfaces it. `products` is the
    # catalogue every other category reads, not a comparison of its own.
    SURFACED = {
        "finance", "profit_share", "rates", "convert", "card", "mile_rates",
    }
    declared = {c for bank in BANKS for c in bank.capabilities} - {"products"}
    orphans = declared - SURFACED
    assert not orphans, (
        f"{sorted(orphans)} are published by a bank but no comparison category "
        "reads them, so nothing in the app can show them"
    )


@pytest.mark.parametrize("category", sorted(CAPABILITY_FOR))
def test_every_product_is_either_compared_or_explicitly_excluded(category):
    """No product may fall out of the map without a reason on the record.

    Stronger than `uncovered`, which only reports products two or more banks
    sell. This one says every single catalogue entry at every capable bank must
    resolve to a family, to a general membership, or to a named single-bank
    exclusion -- so a product cannot go missing just because only one bank
    happens to sell it today.
    """
    from banks.taxonomy import GENERAL

    table = families.BY_CATEGORY[category]
    excluded = families.SINGLE_BANK.get(category, {})
    unpriced = families.NOT_PRICED.get(category, {})
    capable = {
        name for name, entry in list_banks().items()
        if CAPABILITY_FOR[category] in entry["publishes"]
    }

    def accounted(bank: str, name: str) -> bool:
        key = family_key(name, category)
        if key is None:
            return False
        family = families.TAXONOMY_KEYS.get(key, key)
        if key in GENERAL:
            return all(
                bank in {
                    m.bank
                    for m in table[families.TAXONOMY_KEYS.get(split, split)]
                    if m.general
                }
                for split in GENERAL[key]
            )
        if family in excluded:
            return excluded[family] == bank
        if family in unpriced:
            return True
        return family in table and bank in {m.bank for m in table[family]}

    orphans = [
        f"{bank}: {name} (reads as {family_key(name, category)})"
        for bank, names in CATALOGUES[category].items()
        if bank in capable
        for name in names
        if not accounted(bank, name)
    ]
    assert not orphans, (
        f"{len(orphans)} {category} product(s) belong to no family, no general "
        "membership and no recorded exclusion:\n  " + "\n  ".join(orphans)
    )


def test_every_product_stays_addressable_by_code_or_name():
    """A product nobody can name is a product nobody can quote.

    Kuveyt Türk ships two genuine collisions: `ELKTRARACSARJUNITE` names both
    Bisiklet Finansmanı and Elektrikli Araç Şarj Ünitesi, and the card code
    `BP` names both Sağlam Business Kart and Miles&Smiles Business. In both
    cases the name still resolves, so nothing is lost and `find_product`
    refuses the ambiguous code while naming the alternatives.

    What must never happen is a product whose code *and* name both collide --
    then neither lookup can reach it and it drops out of the system silently.
    This is the check for that, over every bank and every category.
    """
    from banks.parse import fold

    unreachable = []
    for category, per_bank in _CAPTURED.items():
        for bank, products in per_bank.items():
            codes = Counter(fold(p["code"]) for p in products if p["code"])
            names = Counter(fold(p["name"]) for p in products if p["name"])
            for product in products:
                by_code = bool(product["code"]) and codes[fold(product["code"])] == 1
                by_name = bool(product["name"]) and names[fold(product["name"])] == 1
                if not (by_code or by_name):
                    unreachable.append(
                        f"{bank}/{category}: {product['code']!r} / {product['name']!r}"
                    )
    assert not unreachable, (
        "these products cannot be resolved by code or by name, so no caller "
        "can quote them:\n  " + "\n  ".join(unreachable)
    )


def test_the_known_code_collisions_are_still_only_these():
    """The collisions are the bank's own data, so they are pinned, not fixed.

    Pinned so a new one shows up as a failure and gets looked at, rather than
    joining the noise.
    """
    from banks.parse import fold

    collisions = set()
    for category, per_bank in _CAPTURED.items():
        for bank, products in per_bank.items():
            codes = Counter(fold(p["code"]) for p in products if p["code"])
            collisions.update(
                (bank, category, p["code"]) for p in products
                if p["code"] and codes[fold(p["code"])] > 1
            )
    assert collisions == {
        ("kuveytturk", "finance", "ELKTRARACSARJUNITE"),
        ("kuveytturk", "card", "BP"),
    }, f"the set of duplicate product codes changed: {sorted(collisions)}"
