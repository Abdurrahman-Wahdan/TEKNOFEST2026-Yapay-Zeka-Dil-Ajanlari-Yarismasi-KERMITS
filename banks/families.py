"""Which product at each bank is the same product.

Comparing banks needs a way to say "the new-car loan" once and have every bank
understand it. Free text cannot do that. Measured with find_product across seven
banks: "taşıt" resolved correctly at **0 of 7**, "konut" at 2 of 7, "ihtiyaç
finansmanı" at 5 of 7. Banks split the same product by new versus second-hand,
insured versus uninsured, or first-home versus subsequent-home, so a generic
word is ambiguous nearly everywhere — and a comparison that quietly ranked a
second-hand car loan against a new-car one would be worse than no comparison.

So the mapping is written down: family -> bank -> whatever that bank calls it.

A family needs **two banks to be worth having**. One bank alone is not a
comparison, it is finance_quote. Left out on that rule, deliberately: alışveriş,
seyahat and tekne (Kuveyt Türk only), motosiklet, teknoloji, cep telefonu and
prefabrik (Albaraka only). shared_families_missing() fails a test if a product
family reaches a second bank and nobody added it here.

A bank absent from a family does not sell it, and that is reported as an answer
rather than hidden.
"""

from .parse import fold

# family -> bank -> the code or name that bank's own catalogue uses.
#
# Ziraat entries are name STEMS, not codes. It lists one product per term band
# ("İHTIYAÇ FINANSMANI (1-24 AY)") with a ceiling that falls as the term rises,
# and its own resolver picks the band that fits the request. Its numeric eids
# change when a band is republished; the stem does not. Do not "improve" these
# into codes.
FINANCE: dict[str, dict[str, str]] = {
    "ihtiyac": {
        "kuveytturk": "İhtiyaç Finansmanı",
        "vakif": "IF",
        "emlak": "EVOFISGERECLERI",
        "dunya": "TUKETICIIHTIYAC",
        "ziraat": "İHTIYAÇ FINANSMANI",
        # Albaraka publishes no general-purpose ihtiyaç product at all.
    },
    "konut-yeni": {
        "kuveytturk": "Konut Finansmanı",
        "albaraka": "YKKNT0B",
        "vakif": "K",
        "emlak": "GMENKULKONUTYENI",
        "dunya": "KONUTTUKETICI",
        "ziraat": "KONUT FINANSMANI",
    },
    "konut-2el": {
        "albaraka": "VRKNT0B",
        "vakif": "K2",
        "dunya": "2ELKONUTTUKETICI",
    },
    "tasit-0km": {
        "kuveytturk": "Yeni Binek Araç Finansmanı",
        "albaraka": "KMPARAC",
        "vakif": "BO",
        "emlak": "ARACBINEKYENI",
        "dunya": "ARACBINEKYENITUKETICI",
        # Ziraat sells one taşıt product rather than splitting by age, so it is
        # listed under 0 km only. Comparing its general product against the
        # others' second-hand rates would be comparing two different things.
        "ziraat": "TAŞIT FINANSMANI",
    },
    "tasit-2el": {
        "kuveytturk": "2. El Binek Araç Finansmanı",
        "albaraka": "2.ELTŞT",
        "vakif": "BO2",
        "emlak": "ARACBINEK2EL",
        "dunya": "ARACBINEK2ELTUKETICI",
    },
    "arsa": {
        "kuveytturk": "Arsa Finansmanı",
        "albaraka": "ARSABIR",
        "vakif": "A",
        "dunya": "ARSATUKETICI",
        "ziraat": "ARSA FINANSMANI",
    },
    "isyeri": {
        "kuveytturk": "İş Yeri Finansmanı",
        "albaraka": "ISYERII",
        "vakif": "I",
        "ziraat": "BIREYSEL İŞYERI FINANSMANI",
    },
    "egitim": {
        "kuveytturk": "Eğitim Finansmanı",
        "albaraka": "EĞİTİM",
    },
    "hac-umre": {
        "kuveytturk": "Hac-Umre Finansmanı",
        "ziraat": "İHTIYAÇ FINANSMANI HAC / UMRE",
    },
    "kira": {
        "kuveytturk": "Kira Finansmanı",
        "albaraka": "KNTKIRA",
    },
}

PROFIT_SHARE: dict[str, dict[str, str]] = {
    "katilma": {
        "kuveytturk": "Katılma Hesabı",
        "albaraka": "KTLMHSP",
        "vakif": "KAH",
        "emlak": "KATILMA",
        "dunya": "KTLMHSP",
        "hayat": "Katılma Hesabı",
    },
}

BY_CATEGORY = {"finance": FINANCE, "profit_share": PROFIT_SHARE}

# What to call a family in an answer.
LABELS = {
    "ihtiyac": "İhtiyaç finansmanı",
    "konut-yeni": "Yeni konut finansmanı",
    "konut-2el": "2. el konut finansmanı",
    "tasit-0km": "0 km taşıt finansmanı",
    "tasit-2el": "2. el taşıt finansmanı",
    "arsa": "Arsa finansmanı",
    "isyeri": "İşyeri finansmanı",
    "egitim": "Eğitim finansmanı",
    "hac-umre": "Hac ve umre finansmanı",
    "kira": "Kira finansmanı",
    "katilma": "Katılma hesabı",
}

# Turkish words a model may send instead of a family key. A word that cannot
# pick one family on its own maps to the families it could mean, so the refusal
# teaches the right key rather than listing every slug.
ALIASES: dict[str, tuple[str, ...]] = {
    "ihtiyac": ("ihtiyac",),
    "ihtiyacfinansmani": ("ihtiyac",),
    "konut": ("konut-yeni", "konut-2el"),
    "konutfinansmani": ("konut-yeni", "konut-2el"),
    "ev": ("konut-yeni", "konut-2el"),
    "tasit": ("tasit-0km", "tasit-2el"),
    "tasitfinansmani": ("tasit-0km", "tasit-2el"),
    "arac": ("tasit-0km", "tasit-2el"),
    "araba": ("tasit-0km", "tasit-2el"),
    "sifirkm": ("tasit-0km",),
    "ikinciel": ("tasit-2el", "konut-2el"),
    "arsa": ("arsa",),
    "isyeri": ("isyeri",),
    "egitim": ("egitim",),
    "hac": ("hac-umre",),
    "umre": ("hac-umre",),
    "kira": ("kira",),
    "katilma": ("katilma",),
    "katilmahesabi": ("katilma",),
    "karpayi": ("katilma",),
}


def families(category: str) -> list[str]:
    """Every family key in a category."""
    return sorted(BY_CATEGORY.get(category, {}))


def entries(category: str, family: str) -> dict[str, str]:
    """Bank name -> that bank's own code for this family.

    Raises:
        ValueError: on an unknown family, naming the valid keys — and, when the
            query is a Turkish word that could mean more than one, naming those
            instead so the caller can pick.
    """
    table = BY_CATEGORY.get(category)
    if table is None:
        raise ValueError(
            f"No families for {category!r}. Try: {', '.join(sorted(BY_CATEGORY))}."
        )
    if family in table:
        return dict(table[family])

    suggested = ALIASES.get(fold(family), ())
    known = [f for f in suggested if f in table]
    if len(known) == 1:
        return dict(table[known[0]])
    if known:
        options = " or ".join(known)
        raise ValueError(
            f"{family!r} could mean more than one product here. Say {options}."
        )
    raise ValueError(
        f"{family!r} is not a product family. Valid families: "
        f"{', '.join(families(category))}."
    )


def label(family: str) -> str:
    return LABELS.get(family, family)


def unknown_banks() -> list[tuple[str, str, str]]:
    """(category, family, bank) naming a bank that cannot serve that family.

    Catches a typo, a bank that was removed, and a family pointing at a bank
    that does not declare the capability. Used by a unit test, offline.
    """
    from .providers import BANKS

    known = {bank.name: bank for bank in BANKS}
    needed = {"finance": "finance", "profit_share": "profit_share"}
    wrong = []
    for category, table in BY_CATEGORY.items():
        for family, banks in table.items():
            for name in banks:
                bank = known.get(name)
                if bank is None or needed[category] not in bank.capabilities:
                    wrong.append((category, family, name))
    return wrong


def shared_families_missing(catalogues: dict[str, dict[str, list[str]]]) -> list[str]:
    """Product names that two or more banks sell and no family covers.

    `catalogues` is {category: {bank: [product names]}}. Compares on a folded
    keyword rather than the whole name, because no two banks spell a product
    the same way. Used by a test so coverage cannot quietly fall behind as
    banks add products.
    """
    keywords = {
        "ihtiyac": "ihtiyac", "konut": "konut", "tasit": "tasit", "arac": "tasit",
        "arsa": "arsa", "isyeri": "isyeri", "egitim": "egitim", "hac": "hac-umre",
        "kira": "kira", "katilma": "katilma",
    }
    missing = []
    for category, per_bank in catalogues.items():
        covered = set()
        for banks in BY_CATEGORY.get(category, {}).values():
            covered.update(banks)
        for keyword, family in keywords.items():
            if family not in BY_CATEGORY.get(category, {}):
                continue
            sellers = {
                bank for bank, names in per_bank.items()
                if any(keyword in fold(name) for name in names)
            }
            uncovered = sellers - set(BY_CATEGORY[category][family])
            if len(sellers) >= 2 and len(uncovered) >= 2:
                missing.append(f"{category}/{family}: {', '.join(sorted(uncovered))}")
    return missing
