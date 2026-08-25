"""Working out which product at one bank is the same product at another.

`families.py` holds the answer — family -> bank -> that bank's own code. This
module is how the answer is *found and kept honest*: it decomposes a product
name into the dimensions banks actually differ on, so a new product appearing in
a live catalogue lands in a family automatically instead of waiting for someone
to notice it.

Measured over all 88 finance products across the eight banks that publish a
catalogue, banks disagree on **five independent axes** and no two of them use
the same axes:

    purpose     what the money is for          konut, tasit, arsa, ...
    condition   the age of the thing bought    yeni | 2el          (Vakıf, Dünya, Emlak)
    ownership   how many the buyer has         ilk | sonraki       (Albaraka, Türkiye Finans)
    usage       who drives it                  binek | ticari      (Kuveyt Türk only)
    insurance   bundled or not                 var | yok           (Türkiye Finans only)

`condition` and `ownership` are the trap. "İLK EVİM KONUT FİNANSMANI" is a
first-home loan and "Sıfır Konut Finansmanı" is a new-build loan; they are not
the same product, and ranking one against the other produces a confident wrong
answer rather than a visible failure. They get separate families.

An axis a bank does not split on is not missing data — Kuveyt Türk sells one
konut product that serves both a first home and a resale, so it is a `general`
member of every konut family rather than a member of none.

Nothing here builds a request. Codes and name stems live in `families.py`
because that is what the bank endpoints actually take; this module only decides
what belongs with what.
"""

import re
import unicodedata

# Turkish letters that str.lower() does not fold to ASCII. Kept separate from
# parse.fold() because that one drops spaces, and every rule below is about
# words: "2 el" must not become indistinguishable from "2el" inside a longer
# name, and "ilk ev" must not match "ilkevim".
_TURKISH = {
    "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c",
}

# "(1-24 AY)", "(0-10.000.000 TL/1-120 AY))" — Ziraat lists one product per term
# band with a ceiling that falls as the term rises. The band is a limit, not an
# identity, so it is stripped before matching. banks/limits.py collapses the
# bands back into an envelope.
_BAND = re.compile(r"\(?\s*\d[\d.,]*\s*-\s*\d[\d.,]*\s*(?:tl\s*/\s*)?[\d.,\s-]*(?:ay|tl)\s*\)*")

# Words every bank sprinkles through a name that carry no distinction.
_NOISE = (
    "kampanya paketi", "kampanya", "finansmani", "finansman", "tuketici",
    "bireysel", "kredisi", "kredi", "paketi", "digerr",
)


def normalize(text: str) -> str:
    """Fold Turkish casing to ASCII while keeping word boundaries."""
    for turkish, ascii_ in _TURKISH.items():
        text = text.replace(turkish, ascii_)
    stripped = unicodedata.normalize("NFKD", text.lower())
    kept = "".join(c if c.isalnum() else " " for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", kept).strip()


def stem(name: str) -> str:
    """A product name with term bands, parentheses and filler words removed."""
    text = normalize(name)
    text = _BAND.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    for word in _NOISE:
        text = text.replace(word, " ")
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, *words: str) -> bool:
    return any(word in text for word in words)


# Ordered most-specific first, because the words overlap: Kuveyt Türk's
# "Elektrikli Araç Şarj Ünitesi Finansmanı" contains "arac" and would otherwise
# be filed as a car loan, and Ziraat's "İHTIYAÇ FINANSMANI HAC / UMRE" contains
# "ihtiyac" but is a pilgrimage product.
_PURPOSE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cevre", ("cevre", "gri su")),
    ("hac-umre", ("hac", "umre")),
    ("sarj", ("sarj",)),
    ("bisiklet", ("bisiklet",)),
    ("motosiklet", ("motosiklet",)),
    ("tekne", ("tekne",)),
    ("kira", ("kira",)),
    ("yurt", ("yurt hizmeti",)),
    ("egitim", ("egitim",)),
    ("cep-telefonu", ("cep telefonu",)),
    ("teknoloji", ("teknoloji",)),
    ("prefabrik", ("prefabrik",)),
    ("engelsiz", ("engelsiz",)),
    ("alisveris", ("alisveris", "ecommerce")),
    ("seyahat", ("seyahat",)),
    # Türkiye Finans finances the properties it holds on its own books. The
    # name says "konut" and "ticari mülk", but the collateral is the bank's own
    # stock and the rate reflects that, so it is not the general konut or
    # işyeri product and must be caught before either.
    ("banka-gayrimenkulu", ("gayrimenkulleri",)),
    ("isyeri", ("isyeri", "is yeri", "ticari mulk")),
    ("arsa", ("arsa",)),
    ("konut", ("konut", "evim", "gayrimenkulleri konut")),
    ("tasit", ("tasit", "arac", "binek")),
    ("ihtiyac", ("ihtiyac", "kolay fon", "saglik", "pratik", "ofis gerecleri")),
)


# Participation accounts, ordered most-specific first for the same reason: every
# one of them contains the word "katılma", so the plain account has to be last.
#
# These are separate *products*, not currency options on one account. Kuveyt
# Türk's dedicated "Altına Altın Katılma Hesabı" pays a 40% ratio where its
# ordinary account pays 95%, so pricing gold through the general account gives
# the wrong number for the product the customer would actually open.
_PROFIT_SHARE_PURPOSE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("katilma-altin", ("altin", "altina altin")),
    ("katilma-aradonem", ("ara donem",)),
    ("katilma-kurkorumali", ("kur korumali",)),
    ("katilma-dijital", ("dijital",)),
    ("katilma-hosgeldin", ("hos geldin",)),
    ("katilma-gunes", ("gunes",)),
    ("katilma-sepet", ("sepet",)),
    ("katilma-yuvam", ("yuvam",)),
    ("katilma-avantajli-gunluk", ("avantajli gunluk",)),
    ("katilma-avantajli", ("avantajli",)),
    ("katilma", ("katilma", "katilim")),
)

BY_CATEGORY_PURPOSE = {
    "finance": _PURPOSE,
    "profit_share": _PROFIT_SHARE_PURPOSE,
}


class Dimensions(dict):
    """What a product name says, on each axis. Missing key = bank does not split."""

    @property
    def purpose(self) -> str | None:
        return self.get("purpose")


def dimensions(name: str, category: str = "finance") -> Dimensions:
    """Decompose one product name into the axes banks differ on."""
    text = stem(name)
    out = Dimensions()
    purposes = BY_CATEGORY_PURPOSE.get(category, _PURPOSE)
    out["purpose"] = next(
        (key for key, words in purposes if _has(text, *words)), None
    )

    # The age of the thing being bought. "0 km" normalizes to "0 km".
    if _has(text, "2 el", "2el", "ikinci el"):
        out["condition"] = "2el"
    elif _has(text, "yeni", "sifir", "0 km"):
        out["condition"] = "yeni"

    # How many the buyer already has. Only meaningful for konut, and worded
    # completely differently at the two banks that use it: Albaraka says
    # "İLK EVİM" / "2. VE SONRAKİ", Türkiye Finans "İlk Konutunu Alan" /
    # "Mevcut Konutu Olan".
    if _has(text, "ilk ev", "ilk konut", "ilkev"):
        out["ownership"] = "ilk"
    elif _has(text, "sonraki", "mevcut konut"):
        out["ownership"] = "sonraki"

    if "sigortasiz" in text:
        out["insurance"] = "yok"
    elif "sigortali" in text:
        out["insurance"] = "var"

    # Only Kuveyt Türk splits a car loan by who drives it, so "binek" carries no
    # information anywhere else and is recorded only when the word is present.
    # `family_key` treats its absence as binek, which is every bank's default.
    if "ticari" in text:
        out["usage"] = "ticari"
    elif "binek" in text:
        out["usage"] = "binek"

    if "dijital" in text:
        out["channel"] = "dijital"
    elif "kart" in text:
        out["channel"] = "kart"

    return out


# Axes that split a purpose into separate families, in the order they appear in
# the key. An axis absent here is descriptive only: `insurance` never splits a
# family, because Türkiye Finans' two rows are the same product priced two ways
# and belong side by side in one comparison.
_SPLITTING = ("condition", "ownership", "usage", "channel")

# Purposes where an axis is a genuine distinction rather than noise. Applying
# `condition` to an ihtiyaç product would invent a family out of the word
# "yeni" appearing in an unrelated name.
_APPLIES: dict[str, tuple[str, ...]] = {
    "konut": ("condition", "ownership"),
    "tasit": ("condition", "usage", "channel"),
    "ihtiyac": ("channel",),
    "banka-gayrimenkulu": ("usage",),
}

# A purpose whose bare key means "this bank does not split the axis at all",
# and the families such a product is a general member of. Read by
# families.uncovered() so Kuveyt Türk's single konut product is not reported as
# an uncovered family of its own.
GENERAL: dict[str, tuple[str, ...]] = {
    "konut": ("konut-yeni", "konut-2el", "konut-ilk", "konut-sonraki"),
    "tasit": ("tasit-yeni", "tasit-2el"),
}


def family_key(name: str, category: str = "finance") -> str | None:
    """The family a product name belongs to, or None if the purpose is unknown.

    A general product — one whose bank does not split the axis at all — returns
    the bare purpose (`"konut"`). `families.py` records those as `general`
    members of each split family, because Ziraat's single konut product is a
    real answer for a first home and for a resale alike.
    """
    dims = dimensions(name, category)
    purpose = dims.purpose
    if purpose is None:
        return None

    # Participation accounts differ by product, not by the axes a financing
    # product splits on: "2. el" and "sigortalı" mean nothing to a savings
    # account, and applying them would invent families out of stray words.
    if category != "finance":
        return purpose

    applies = _APPLIES.get(purpose, ())
    parts = [purpose]
    for axis in _SPLITTING:
        if axis not in applies:
            continue
        value = dims.get(axis)
        # `binek` is every bank's default and only Kuveyt Türk names it, so
        # recording it would put Kuveyt Türk's car loan in a family of its own.
        if value in (None, "binek"):
            continue
        parts.append(value)
    return "-".join(parts)


def classify(
    catalogue: dict[str, list[str]], category: str = "finance"
) -> dict[str, dict[str, list[str]]]:
    """{bank: [product names]} -> {family key: {bank: [names]}}.

    The discovery pass. Used by a test to fail when a live catalogue grows a
    product that two banks sell and no family covers.
    """
    found: dict[str, dict[str, list[str]]] = {}
    for bank, names in catalogue.items():
        for name in names:
            key = family_key(name, category)
            if key is None:
                continue
            found.setdefault(key, {}).setdefault(bank, []).append(name)
    return found
