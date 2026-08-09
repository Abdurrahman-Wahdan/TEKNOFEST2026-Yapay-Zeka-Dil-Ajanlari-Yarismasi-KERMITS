"""Which PDFs are worth reading, and why.

The agent answers questions about campaigns, products, fees and rates. Measured
against the 1,088 PDFs the old crawler saved, only ~227 are that: 398 are loan
contracts, 40 are SPK prospectuses, 31 are governance policies, and 367 could not
be judged from their filename at all. Reading everything would spend most of the
extraction budget on `Genel Kredi Sözleşmesi` and bury the fee schedules in it.

Filenames alone are not enough, and the corpus proves it both ways:
`vahesabi_onbilg_formu.pdf` is an *ön bilgilendirme formu* carrying real rates,
while `banking-license.pdf` is a scan of a licence. So the strongest signal is
**where the bank published it** — a PDF linked from a product page is a product
document — with filename rules to override, and a model to judge what is left.

    from corpus.pdf_policy import decide

    decide(url, anchor_text="Ürün ve Hizmet Ücretleri",
           referrer="https://www.kuveytturk.com.tr/kendim-icin/kartlar")
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from banks.parse import fold

# Labels the model may return, and the ones that mean "read it". A closed set:
# an open-ended "what is this?" gets an essay, and an essay cannot be filtered on.
LABELS = (
    "campaign", "product", "fees", "rates", "faq",
    "contract", "corporate", "regulatory", "privacy", "other",
)
ACCEPTED_LABELS = frozenset({"campaign", "product", "fees", "rates", "faq"})

# Path segments that name a language rather than a section. These sit in front
# of the real section at the sites that publish more than one language.
LANGUAGE_SEGMENTS = frozenset({
    "tr", "tr-tr", "en", "en-us", "en-gb", "ar", "ar-sa", "de", "ru", "fr",
})

# Sections where a bank publishes what it sells. A PDF linked from one of these
# is about a product, whatever its filename says.
SECTIONS_IN_SCOPE = frozenset({
    "kampanyalar", "kendim-icin", "isim-icin", "bireysel", "ticari", "kobi",
    "ozel-bankacilik", "kurumsal-bankacilik", "urunler", "hizmetler",
})

# Sections that are about the company rather than its products. A PDF linked
# only from here is out unless a filename rule rescues it.
SECTIONS_OUT_OF_SCOPE = frozenset({
    "yatirimci-iliskileri", "hakkimizda", "kurumsal-yonetim", "surdurulebilirlik",
    "basin-odasi", "kariyer", "kvkk", "bilgi-toplumu-hizmetleri", "yatirimci",
})

# Folded substrings. `fold()` strips punctuation and Turkish diacritics, so
# "Ürün ve Hizmet Ücretleri" and "urun_ve_hizmet_ucretleri.pdf" both contain
# "ucret", and the abbreviated "onbilg" in vahesabi_onbilg_formu.pdf still hits.
#
# Checked BEFORE the deny list, because these name specific document types while
# the deny list names broad classes, and the two legitimately overlap. A
# "sözleşme öncesi bilgi formu" (kk_sozlesmeobf.pdf) is the pre-contractual form
# that states the profit rate and the fees -- exactly what the agent is asked
# about -- and a deny rule on "sozlesme" would otherwise throw it away.
ALLOW_PATTERNS = (
    "ucret", "tarife", "komisyon", "masraf",
    # "karoran", not "karorani": the files say both "kar orani" and
    # "kar_oranlari", and the plural does not contain the singular.
    "karoran", "karpayi", "getirioran", "referansoran", "faizsizoran",
    "onbilg", "oncesibilg", "obf", "urunbilg", "musteribilg",
    "bilgilendirmeformu", "bilgiformu", "tanitimformu",
    "talimat", "sss", "sikcasorulan",
    "kampanya", "katalog", "firsat", "anlasmalimagaza", "anlasmalikurum",
)

# Broad classes that are large, static, and answer nothing the agent is asked.
# Contracts are the biggest single slice of the corpus -- 398 documents, 34.9M
# characters -- and none of them describe a campaign or a fee schedule.
DENY_PATTERNS = (
    "sozlesme", "taahhutname", "protokol", "muvafakatname", "vekaletname",
    "faaliyetraporu", "finansalrapor", "bagimsizdenetim", "guvenceraporu",
    "uyumraporu", "tedarikzinciri",
    "izahname", "ihracbelgesi", "tertip", "kirasertifikasi", "sermayepiyasa",
    "articlesofassociation", "articleofassociation",
    "genelkurul", "bankinglicense", "faaliyetizni", "certificateofactivity",
    "patriot", "wolfsberg", "w8ben", "antimoneylaundering", "aml",
    "kvkk", "kisiselveri", "aydinlatmametni", "acikriza", "cerez",
    "etikilke", "politikasi", "prosedur", "yonetmelik", "uyumbeyani",
)


@dataclass(frozen=True)
class Decision:
    """What to do with one PDF, and the reason, so a wrong call is auditable."""

    accepted: bool
    label: str          # one of LABELS, or "" when only a model can say
    reason: str         # one line, in plain words
    decided_by: str     # "rule:deny" | "rule:allow" | "rule:section" | "model"

    @property
    def needs_model(self) -> bool:
        """Whether the rules abstained and a model has to look at the file."""
        return self.decided_by == "" or self.label == ""


def section_of(url: str) -> str:
    """The first path segment of a URL, which is how these banks file content.

    Language prefixes are skipped: Emlak publishes at /tr/kampanyalar and Türkiye
    Finans at /tr-tr/bireysel, and in both the section is what follows. Listed
    explicitly rather than guessed by shape -- a length-and-hyphen heuristic
    reads "tr-tr" as a section and "kobi" as a language.
    """
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if segments and segments[0].lower() in LANGUAGE_SEGMENTS:
        segments = segments[1:]
    return segments[0].lower() if segments else ""


def _matches(patterns: tuple[str, ...], *texts: str) -> str:
    """The first pattern found in any of `texts`, folded. "" for no match."""
    haystack = fold(" ".join(t for t in texts if t))
    for pattern in patterns:
        if pattern in haystack:
            return pattern
    return ""


def decide(url: str, anchor_text: str = "", referrer: str = "") -> Decision:
    """Whether to read this PDF.

    Args:
        url: The PDF's canonical URL.
        anchor_text: The link text the bank wrote for it. Better evidence than
            the filename — only a third of these files declare a title
            internally, and the link text is always human-written Turkish.
        referrer: The page that linked it. Its section is the strongest signal.

    Returns:
        A Decision. `needs_model` is True when the rules could not judge it, in
        which case the caller sends the first page to a model rather than
        guessing — that is the 367-document "unclassified" pile, and dropping it
        silently would lose real fee and rate documents.
    """
    filename = urlsplit(url).path.rsplit("/", 1)[-1]

    # Allow before deny: these patterns name specific document types, the deny
    # patterns name broad classes, and a pre-contractual information form is
    # both a "sözleşme" and the document that states the rate.
    allowed = _matches(ALLOW_PATTERNS, filename, anchor_text)
    if allowed:
        return Decision(True, _label_for(allowed),
                        f"filename or link text matches {allowed!r}", "rule:allow")

    denied = _matches(DENY_PATTERNS, filename, anchor_text)
    if denied:
        return Decision(False, "corporate",
                        f"filename or link text matches {denied!r}", "rule:deny")

    section = section_of(referrer) if referrer else ""
    if section in SECTIONS_IN_SCOPE:
        return Decision(True, "product",
                        f"linked from /{section}, where this bank publishes "
                        f"what it sells", "rule:section")
    if section in SECTIONS_OUT_OF_SCOPE:
        return Decision(False, "corporate",
                        f"linked only from /{section}, which is about the "
                        f"company rather than its products", "rule:section")

    return Decision(False, "", "no rule matched; needs a model to classify", "")


def _label_for(pattern: str) -> str:
    """Which accepted label a matched allow-pattern implies."""
    if pattern in ("ucret", "tarife", "komisyon", "masraf"):
        return "fees"
    if pattern in ("karoran", "karpayi", "getirioran", "referansoran", "faizsizoran"):
        return "rates"
    if pattern in ("kampanya", "katalog", "firsat"):
        return "campaign"
    if pattern in ("sss", "sikcasorulan"):
        return "faq"
    return "product"


def from_label(label: str, reason: str = "") -> Decision:
    """Turn a model's label into a Decision.

    An unrecognised label is not accepted. A model that answers with something
    outside the closed set has not classified the document, and guessing on its
    behalf is how a loan contract ends up in the campaign collection.
    """
    clean = (label or "").strip().lower()
    if clean not in LABELS:
        return Decision(False, "other",
                        f"model returned {label!r}, which is not one of the "
                        f"allowed labels", "model")
    return Decision(clean in ACCEPTED_LABELS, clean,
                    reason or f"model classified it as {clean}", "model")
