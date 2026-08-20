"""Which product at each bank is the same product.

Comparing banks needs a way to say "the new-car loan" once and have every bank
understand it. Free text cannot do that. Measured with find_product across seven
banks: "taşıt" resolved correctly at **0 of 7**, "konut" at 2 of 7, "ihtiyaç
finansmanı" at 5 of 7. Banks split the same product by new versus second-hand,
insured versus uninsured, or first-home versus subsequent-home, so a generic
word is ambiguous nearly everywhere — and a comparison that quietly ranked a
second-hand car loan against a new-car one would be worse than no comparison.

So the mapping is written down: family -> the banks that sell it, each with
whatever that bank calls it.

`banks/taxonomy.py` is how this table is kept honest. It decomposes a live
catalogue name into the axes banks differ on, and a test fails when a product
two banks sell lands in no family here. The table stays hand-written because the
values are what the bank endpoints actually take, and a code guessed from a name
is a request that 404s.

## The three things this table has to get right

**A bank can appear twice in one family.** Türkiye Finans prices every product
insured and uninsured; both are the same product and belong side by side, so
`variant` carries the difference to the answer instead of one of them being
dropped.

**Two banks can split the same word on different axes.** "İLK EVİM KONUT
FİNANSMANI" is a first-home loan; "Sıfır Konut Finansmanı" is a new-build loan.
Ranking them together produces a confident wrong answer, so `konut-ilk`/
`konut-sonraki` (ownership) and `konut-yeni`/`konut-2el` (property condition)
are four separate families.

**A bank that does not split an axis is not absent from it.** Kuveyt Türk and
Ziraat each sell one konut product that answers for a first home and a resale
alike, so they join every konut family as `general=True` and the answer says so.

A family may have **one bank**. It still belongs in the comparator: a customer
can run that bank's live endpoint and see plainly that no other integrated bank
currently offers an equivalent. A one-bank result is never ranked as if it had
competition, but it must not disappear into a separate catalogue.

A bank absent from a family does not sell it, and that is reported as an answer
rather than hidden.
"""

from dataclasses import dataclass

from .parse import fold


@dataclass(frozen=True)
class Member:
    """One bank's entry in a family."""

    bank: str

    # The code or name stem this bank's own catalogue uses. Kuveyt Türk, Ziraat
    # and Hayat resolve by name; the rest take a code. Ziraat entries are name
    # STEMS, not codes: it lists one product per term band ("İHTIYAÇ FINANSMANI
    # (1-24 AY)") with a ceiling that falls as the term rises, and its own
    # resolver picks the band that fits. Its numeric eids change when a band is
    # republished; the stem does not. Do not "improve" these into codes.
    query: str

    # Non-empty only where a bank prices one product several ways. Rendered as a
    # column, so two rows from one bank are self-explaining rather than
    # duplicate-looking.
    variant: str = ""

    # True when this bank sells one product covering the whole axis the family
    # splits on. Its number is a real answer for this family; it is simply not a
    # different product from the one it offers the neighbouring family.
    general: bool = False


def _m(bank: str, query: str, **kw) -> Member:
    return Member(bank, query, **kw)


# Kuveyt Türk's single konut product, and Ziraat's, answer for every konut
# family. Named once so the four families cannot drift apart.
_KONUT_GENERAL = (
    _m("kuveytturk", "Konut Finansmanı", general=True),
    _m("ziraat", "KONUT FINANSMANI", general=True),
    # 2,89 against 3,19 on the standard product. The stem "KONUT FINANSMANI"
    # prefix-matches this too, but Ziraat's own resolver picks the standard
    # one, so the cheaper package needs naming in full or it is never quoted.
    _m("ziraat", "KONUT FINANSMANI KAMPANYA PAKETI", variant="kampanya", general=True),
)

# Ziraat sells one taşıt product rather than splitting by the car's age.
_TASIT_GENERAL = (_m("ziraat", "TAŞIT FINANSMANI", general=True),)

FINANCE: dict[str, tuple[Member, ...]] = {
    "ihtiyac": (
        _m("kuveytturk", "İhtiyaç Finansmanı"),
        _m("vakif", "IF"),
        _m("emlak", "EVOFISGERECLERI"),
        _m("dunya", "TUKETICIIHTIYAC"),
        _m("ziraat", "İHTIYAÇ FINANSMANI"),
        # Ziraat's cheaper general-purpose product, 4,19–4,39 against 4,99 on
        # İhtiyaç. Listing only İhtiyaç quoted the bank at its dearest rate and
        # ranked it below banks it actually beats.
        _m("ziraat", "KOLAY FON FINANSMANI", variant="kampanya"),
        # T.O.M. sells exactly one financing product, TKTCDGRFNS, and its own
        # catalogue calls it "İhtiyaç Finansmanı" (1-36 months, 3.99). Until it
        # was listed here it belonged to no family, so every comparison reported
        # "T.O.M. does not offer this" -- of the one product it does offer.
        _m("tom", "TKTCDGRFNS"),
        # Türkiye Finans leaves its standard product unmarked and names only the
        # uninsured one, so the variant is recorded as the bank states it rather
        # than inferring "sigortalı" from the pairing.
        _m("turkiyefinans", "1"),
        _m("turkiyefinans", "999", variant="sigortasiz"),
        # Albaraka publishes no general-purpose ihtiyaç product at all.
    ),
    "ihtiyac-kart": (
        _m("albaraka", "PRTKRT"),
        _m("kuveytturk", "IHTIYACKART"),
    ),
    # ----- konut, split on the age of the property -----
    "konut-yeni": (
        _m("dunya", "KONUTTUKETICI"),
        _m("emlak", "GMENKULKONUTYENI"),
        _m("vakif", "K"),
        *_KONUT_GENERAL,
    ),
    "konut-2el": (
        _m("dunya", "2ELKONUTTUKETICI"),
        _m("vakif", "K2"),
        *_KONUT_GENERAL,
    ),
    # ----- konut, split on how many homes the buyer already has -----
    "konut-ilk": (
        _m("albaraka", "YKKNT0B"),
        _m("turkiyefinans", "16", variant="sigortali"),
        _m("turkiyefinans", "115", variant="sigortasiz"),
        *_KONUT_GENERAL,
    ),
    "konut-sonraki": (
        _m("albaraka", "VRKNT0B"),
        _m("turkiyefinans", "116", variant="sigortali"),
        _m("turkiyefinans", "118", variant="sigortasiz"),
        *_KONUT_GENERAL,
    ),
    "tasit-0km": (
        _m("kuveytturk", "Yeni Binek Araç Finansmanı"),
        _m("albaraka", "KMPARAC"),
        _m("vakif", "BO"),
        _m("emlak", "ARACBINEKYENI"),
        _m("dunya", "ARACBINEKYENITUKETICI"),
        _m("turkiyefinans", "14", variant="sigortali"),
        _m("turkiyefinans", "121", variant="sigortasiz"),
        *_TASIT_GENERAL,
    ),
    "tasit-2el": (
        _m("kuveytturk", "2. El Binek Araç Finansmanı"),
        _m("albaraka", "2.ELTŞT"),
        _m("vakif", "BO2"),
        _m("emlak", "ARACBINEK2EL"),
        _m("dunya", "ARACBINEK2ELTUKETICI"),
        _m("turkiyefinans", "120", variant="sigortali"),
        _m("turkiyefinans", "122", variant="sigortasiz"),
        *_TASIT_GENERAL,
    ),
    "tasit-dijital": (
        _m("albaraka", "SBSZARC"),
        _m("kuveytturk", "Binek Dijital Araç Finansmanı"),
    ),
    "motosiklet": (
        _m("albaraka", "MOTOFİN"),
        _m("turkiyefinans", "1000", variant="sigortali"),
        _m("turkiyefinans", "1001", variant="sigortasiz"),
    ),
    "arsa": (
        _m("kuveytturk", "Arsa Finansmanı"),
        _m("albaraka", "ARSABIR"),
        _m("vakif", "A"),
        _m("dunya", "ARSATUKETICI"),
        _m("ziraat", "ARSA FINANSMANI"),
        _m("turkiyefinans", "17", variant="sigortali"),
        _m("turkiyefinans", "540", variant="sigortasiz"),
    ),
    "isyeri": (
        _m("kuveytturk", "İş Yeri Finansmanı"),
        _m("albaraka", "ISYERII"),
        _m("vakif", "I"),
        _m("ziraat", "BIREYSEL İŞYERI FINANSMANI"),
        _m("turkiyefinans", "18", variant="sigortali"),
        _m("turkiyefinans", "550", variant="sigortasiz"),
    ),
    "egitim": (
        _m("kuveytturk", "Eğitim Finansmanı"),
        _m("albaraka", "EĞİTİM"),
    ),
    "hac-umre": (
        _m("kuveytturk", "Hac-Umre Finansmanı"),
        _m("ziraat", "İHTIYAÇ FINANSMANI HAC / UMRE"),
    ),
    "kira": (
        _m("kuveytturk", "Kira Finansmanı"),
        _m("albaraka", "KNTKIRA"),
    ),
}

PROFIT_SHARE: dict[str, tuple[Member, ...]] = {
    "katilma": (
        _m("kuveytturk", "Katılma Hesabı"),
        _m("albaraka", "KTLMHSP"),
        _m("vakif", "KAH"),
        _m("emlak", "KATILMA"),
        _m("dunya", "KTLMHSP"),
        _m("hayat", "Katılma Hesabı"),
        # Added 2026-08-16: kâr payı turned out not to be browser-only after
        # all (see banks/providers/ziraat.py). Its one product takes TRY, USD
        # and EUR but not gold -- unlike Kuveyt Türk and the `general=True`
        # banks below, it never spans the `katilma-altin` axis, so it is a
        # plain member here and nowhere else.
        _m("ziraat", "Katılma Hesabı"),
    ),
    # Gold is its own product, not a currency option on the ordinary account.
    # Kuveyt Türk's dedicated account pays a 40% ratio where its ordinary one
    # pays 95%, so pricing gold through `katilma` answers with a rate nobody
    # would actually get. The banks whose single account takes XAU join as
    # general members, the same way Ziraat's one konut product does.
    "katilma-altin": (
        _m("kuveytturk", "Altına Altın Katılma Hesabı"),
        _m("dunya", "ALTKTLMHSP"),
        _m("emlak", "KATILMA", general=True),
        _m("albaraka", "KTLMHSP", general=True),
        _m("vakif", "KAH", general=True),
    ),
}

# A product does not need a competitor to be selectable.  These entries used
# to live in SINGLE_BANK, which made the product visible only in the catalogue
# despite its bank publishing a live calculator.  Keep each bank's exact
# request key here so the normal comparator sends the live call rather than
# inventing a number or hiding the option.
FINANCE.update({
    "cevre": (_m("albaraka", "CVRE"),),
    "alisveris": (_m("kuveytturk", "ECOMMERCE"),),
    "bisiklet": (_m("kuveytturk", "Bisiklet Finansmanı"),),
    "sarj": (_m("kuveytturk", "Elektrikli Araç Şarj Ünitesi Finansmanı"),),
    "seyahat": (_m("kuveytturk", "Seyahat Finansmanı"),),
    "tekne": (_m("kuveytturk", "Tekne Tüketici Finansmanı"),),
    "tasit-yeni-ticari": (_m("kuveytturk", "Yeni Ticari Araç Finansmanı"),),
    "tasit-2el-ticari": (_m("kuveytturk", "2. El Ticari Araç Finansmanı"),),
    "tasit-ticari-dijital": (_m("kuveytturk", "Ticari Dijital Araç Finansmanı"),),
    "cep-telefonu": (_m("albaraka", "CEPFİN"),),
    "teknoloji": (_m("albaraka", "TEKNO"),),
    "prefabrik": (_m("albaraka", "PRFBFİN"),),
    "engelsiz": (_m("albaraka", "ENGLFİN"),),
    "yurt": (_m("albaraka", "YURTH"),),
    "banka-gayrimenkulu": (_m("turkiyefinans", "102"),),
    "banka-gayrimenkulu-ticari": (_m("turkiyefinans", "105"),),
})

PROFIT_SHARE.update({
    "katilma-aradonem": (
        _m("albaraka", "KTLARDM"),
        _m("kuveytturk", "Ara Dönem Kar Payı Ödemeli Katılma Hesabı"),
    ),
    "katilma-kurkorumali": (
        _m("albaraka", "KURKTLMHSP:bireysel", variant="bireysel"),
        _m("albaraka", "KURKTLMHSP:ticari", variant="ticari"),
    ),
    "katilma-dijital": (_m("kuveytturk", "Dijital Katılma Hesabı"),),
    "katilma-hosgeldin": (_m("kuveytturk", "Hoş Geldin Katılma Hesabı"),),
    "katilma-sepet": (_m("kuveytturk", "Sepet Hesap"),),
    "katilma-yuvam": (_m("kuveytturk", "Yuvam TL Katılma Hesabı"),),
    "katilma-gunes": (_m("dunya", "GNSHSP"),),
    "katilma-avantajli": (_m("hayat", "AVANTAJLIHESAP"),),
    "katilma-avantajli-gunluk": (_m("hayat", "AVANTAJLIGUNLUKHESAP"),),
})

BY_CATEGORY = {"finance": FINANCE, "profit_share": PROFIT_SHARE}

# Products only one bank sells, and the bank that sells it. A family of one is
# not a comparison, but the absence still needs a reason, and the taxonomy test
# reads this so a product reaching a second bank fails the build.
# Sold by two or more banks, and priced by none of them. A family here would be
# a comparison that always answers with two refusals -- measured across every
# amount and term, Albaraka and Kuveyt Türk both publish the interim-profit
# account and neither publishes its rate. Recorded rather than shipped, so the
# absence has a reason and uncovered() does not report it every run.
NOT_PRICED: dict[str, dict[str, str]] = {}

# Keys are taxonomy.family_key() output, so a product reaching a second bank is
# caught by uncovered() rather than by someone re-reading this file.
SINGLE_BANK_FINANCE: dict[str, str] = {}

SINGLE_BANK_PROFIT_SHARE: dict[str, str] = {}

SINGLE_BANK: dict[str, dict[str, str]] = {
    "finance": SINGLE_BANK_FINANCE,
    "profit_share": SINGLE_BANK_PROFIT_SHARE,
}

# What to call a family in an answer.
LABELS = {
    "ihtiyac": "İhtiyaç finansmanı",
    "ihtiyac-kart": "İhtiyaç kartı",
    "konut-yeni": "Yeni konut finansmanı",
    "konut-2el": "2. el konut finansmanı",
    "konut-ilk": "İlk konut finansmanı",
    "konut-sonraki": "2. ve sonraki konut finansmanı",
    "tasit-0km": "0 km taşıt finansmanı",
    "tasit-2el": "2. el taşıt finansmanı",
    "tasit-dijital": "Dijital taşıt finansmanı",
    "motosiklet": "Motosiklet finansmanı",
    "arsa": "Arsa finansmanı",
    "isyeri": "İşyeri finansmanı",
    "egitim": "Eğitim finansmanı",
    "hac-umre": "Hac ve umre finansmanı",
    "kira": "Kira finansmanı",
    "cevre": "Çevre finansmanı",
    "alisveris": "Alışveriş finansmanı",
    "bisiklet": "Bisiklet finansmanı",
    "sarj": "Elektrikli araç şarj ünitesi finansmanı",
    "seyahat": "Seyahat finansmanı",
    "tekne": "Tekne finansmanı",
    "tasit-yeni-ticari": "Yeni ticari taşıt finansmanı",
    "tasit-2el-ticari": "2. el ticari taşıt finansmanı",
    "tasit-ticari-dijital": "Ticari dijital taşıt finansmanı",
    "cep-telefonu": "Cep telefonu finansmanı",
    "teknoloji": "Teknoloji finansmanı",
    "prefabrik": "Prefabrik finansmanı",
    "engelsiz": "Engelsiz hayat finansmanı",
    "yurt": "Yurt hizmeti finansmanı",
    "banka-gayrimenkulu": "Banka gayrimenkulü finansmanı",
    "banka-gayrimenkulu-ticari": "Banka gayrimenkulü ticari finansmanı",
    "katilma": "Katılma hesabı",
    "katilma-altin": "Altın katılma hesabı",
    "katilma-aradonem": "Ara dönem kâr payı ödemeli hesap",
    "katilma-kurkorumali": "Kur korumalı katılma hesabı",
    "katilma-dijital": "Dijital katılma hesabı",
    "katilma-hosgeldin": "Hoş geldin katılma hesabı",
    "katilma-sepet": "Sepet hesap",
    "katilma-yuvam": "Yuvam TL katılma hesabı",
    "katilma-gunes": "Güneş katılma hesabı",
    "katilma-avantajli": "Avantajlı hesap",
    "katilma-avantajli-gunluk": "Avantajlı günlük hesap",
}

# Presentation groups are derived from the semantic family, not a bank's
# marketing label. They organise the picker only; the family still owns the
# exact comparability contract and the endpoint product code.
FAMILY_GROUPS: dict[str, str] = {
    "ihtiyac": "personal", "ihtiyac-kart": "personal", "alisveris": "personal",
    "egitim": "personal", "hac-umre": "personal", "kira": "personal", "seyahat": "personal",
    "cep-telefonu": "personal", "teknoloji": "personal", "prefabrik": "personal",
    "engelsiz": "personal", "yurt": "personal", "cevre": "personal",
    "tasit-0km": "vehicle", "tasit-2el": "vehicle", "tasit-dijital": "vehicle",
    "tasit-yeni-ticari": "vehicle", "tasit-2el-ticari": "vehicle",
    "tasit-ticari-dijital": "vehicle", "motosiklet": "vehicle",
    "bisiklet": "vehicle", "sarj": "vehicle", "tekne": "vehicle",
    # Property condition and ownership are intentionally not merged.
    "konut-yeni": "property", "konut-2el": "property", "konut-ilk": "property",
    "konut-sonraki": "property", "arsa": "property", "isyeri": "property",
    "banka-gayrimenkulu": "property", "banka-gayrimenkulu-ticari": "property",
    "katilma": "standard_account", "katilma-altin": "standard_account",
    "katilma-aradonem": "standard_account", "katilma-kurkorumali": "standard_account",
    "katilma-dijital": "special_account", "katilma-hosgeldin": "special_account",
    "katilma-sepet": "special_account", "katilma-yuvam": "special_account",
    "katilma-gunes": "special_account", "katilma-avantajli": "special_account",
    "katilma-avantajli-gunluk": "special_account",
}

# Turkish words a model may send instead of a family key. A word that cannot
# pick one family on its own maps to the families it could mean, so the refusal
# teaches the right key rather than listing every slug.
ALIASES: dict[str, tuple[str, ...]] = {
    "ihtiyac": ("ihtiyac",),
    "ihtiyacfinansmani": ("ihtiyac",),
    "ihtiyackart": ("ihtiyac-kart",),
    "konut": ("konut-yeni", "konut-2el", "konut-ilk", "konut-sonraki"),
    "konutfinansmani": ("konut-yeni", "konut-2el", "konut-ilk", "konut-sonraki"),
    "ev": ("konut-yeni", "konut-2el", "konut-ilk", "konut-sonraki"),
    "ilkev": ("konut-ilk",),
    "ilkkonut": ("konut-ilk",),
    "ilkevim": ("konut-ilk",),
    "tasit": ("tasit-0km", "tasit-2el"),
    "tasitfinansmani": ("tasit-0km", "tasit-2el"),
    "arac": ("tasit-0km", "tasit-2el"),
    "araba": ("tasit-0km", "tasit-2el"),
    "sifirkm": ("tasit-0km",),
    "ikinciel": ("tasit-2el", "konut-2el"),
    "motosiklet": ("motosiklet",),
    "arsa": ("arsa",),
    "isyeri": ("isyeri",),
    "egitim": ("egitim",),
    "hac": ("hac-umre",),
    "umre": ("hac-umre",),
    "kira": ("kira",),
    "katilma": ("katilma",),
    "katilmahesabi": ("katilma",),
    "karpayi": ("katilma",),
    "altin": ("katilma-altin",),
    "altinkatilma": ("katilma-altin",),
    "altinhesabi": ("katilma-altin",),
}

# taxonomy.family_key() spells the car families "tasit-yeni"/"tasit-2el"; the
# public keys predate it and are in the UI, the i18n files and saved views.
# Mapped rather than renamed on either side.
TAXONOMY_KEYS = {"tasit-yeni": "tasit-0km"}


def families(category: str) -> list[str]:
    """Every family key in a category."""
    return sorted(BY_CATEGORY.get(category, {}))


def members(category: str, family: str) -> tuple[Member, ...]:
    """Every bank entry in a family, including a bank listed more than once.

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
        return table[family]

    suggested = ALIASES.get(fold(family), ())
    known = [f for f in suggested if f in table]
    if len(known) == 1:
        return table[known[0]]
    if known:
        options = " or ".join(known)
        raise ValueError(
            f"{family!r} could mean more than one product here. Say {options}."
        )
    raise ValueError(
        f"{family!r} is not a product family. Valid families: "
        f"{', '.join(families(category))}."
    )


def entries(category: str, family: str) -> dict[str, str]:
    """Bank name -> that bank's own code for this family.

    The flat view, for callers that only need one query per bank. Where a bank
    appears twice the first entry wins, so anything ranking variants separately
    must use members() instead.
    """
    out: dict[str, str] = {}
    for member in members(category, family):
        out.setdefault(member.bank, member.query)
    return out


def banks_in(category: str, family: str) -> list[str]:
    """The distinct banks in a family, sorted."""
    return sorted({m.bank for m in members(category, family)})


def label(family: str) -> str:
    return LABELS.get(family, family)


def group(family: str) -> str:
    """The semantic picker group for a family.

    Missing metadata is a programming error, not a harmless fallback: an
    ungrouped product is how a newly discovered live calculator becomes hard to
    find even though its endpoint was wired correctly.
    """
    try:
        return FAMILY_GROUPS[family]
    except KeyError as exc:
        raise ValueError(f"{family!r} has no semantic family group") from exc


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
        for family, entries_ in table.items():
            for member in entries_:
                bank = known.get(member.bank)
                if bank is None or needed[category] not in bank.capabilities:
                    wrong.append((category, family, member.bank))
    return wrong


def uncovered(catalogues: dict[str, dict[str, list[str]]]) -> list[str]:
    """Products two or more banks sell that no family covers.

    `catalogues` is {category: {bank: [product names]}}. Runs the taxonomy over
    the live names rather than matching keywords, so a bank that renames a
    product or a bank that adds one still lands in the right family. Anything
    deliberately left out is in SINGLE_BANK with the bank that sells it, and
    reaching a second bank there is exactly the case this is meant to catch.
    """
    from .providers import BANKS
    from .taxonomy import GENERAL, classify

    capability = {"finance": "finance", "profit_share": "profit_share"}
    missing = []
    for category, per_bank in catalogues.items():
        table = BY_CATEGORY.get(category, {})
        excluded = SINGLE_BANK.get(category, {})
        unpriced = NOT_PRICED.get(category, {})
        # A bank that does not declare the capability can never be asked, so a
        # product only it sells is not a gap in the map.
        askable = {
            b.name for b in BANKS if capability.get(category, category) in b.capabilities
        }
        scoped = {b: n for b, n in per_bank.items() if b in askable}

        for key, sellers in classify(scoped, category).items():
            family = TAXONOMY_KEYS.get(key, key)
            if len(sellers) < 2:
                continue
            # A bare purpose key means the bank does not split the axis. Those
            # products belong to every family under it as general members, so
            # the check is that each seller is in all of them.
            if key in GENERAL:
                for split in GENERAL[key]:
                    target = TAXONOMY_KEYS.get(split, split)
                    covered = {m.bank for m in table.get(target, ())}
                    gap = sorted(set(sellers) - covered)
                    if gap:
                        missing.append(
                            f"{category}/{target}: general product not listed for "
                            f"{', '.join(gap)}"
                        )
                continue
            if family in unpriced:
                continue
            if family in excluded:
                missing.append(
                    f"{category}/{family}: listed as single-bank "
                    f"({excluded[family]}) but sold by {', '.join(sorted(sellers))}"
                )
                continue
            if family not in table:
                missing.append(
                    f"{category}/{family}: no family, sold by {', '.join(sorted(sellers))}"
                )
                continue
            covered = {m.bank for m in table[family]}
            gap = sorted(set(sellers) - covered)
            if gap:
                missing.append(f"{category}/{family}: missing {', '.join(gap)}")
    return missing


# Kept under the old name: tests and tools import it.
shared_families_missing = uncovered
