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

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from pydantic import Field as PydanticField

from banks.parse import fold

logger = logging.getLogger(__name__)

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
    # No bare "aml": as a folded substring it fires inside ordinary Turkish
    # words -- it rejected "bina-tamamlama-sigortasi" on a live run.
    "patriot", "wolfsberg", "w8ben", "antimoneylaundering", "aklamaylamucadele",
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


# Longest side of the page image sent to the classifier. Deciding what a
# document *is* needs the layout and the title, not legible fine print, and an
# uncapped 200 DPI render of a large-format brochure earned a 413 from the vLLM
# host on a live run. Extraction, which does need the fine print, tiles instead.
CLASSIFY_MAX_PX = 1400

# Turkish, because these documents are Turkish and the model mirrors the prompt
# language. The label set is closed and repeated in the prompt: an open question
# gets an essay back, and an essay cannot be filtered on.
CLASSIFY_PROMPT = """Bu, bir Türk katılım bankasının web sitesinden alınmış bir PDF belgesinin ilk sayfasıdır.

Belgeyi aşağıdaki etiketlerden TAM OLARAK biriyle sınıflandır:

- campaign: kampanya, indirim, taksit fırsatı, promosyon koşulları
- product: ürün veya hizmet tanıtımı, bilgilendirme formu, ön bilgilendirme formu, talep formu
- fees: ücret, tarife, komisyon veya masraf listesi
- rates: kâr payı oranı, getiri oranı, referans oran tablosu
- faq: sıkça sorulan sorular
- contract: sözleşme, taahhütname, akit metni
- corporate: faaliyet raporu, finansal tablo, ana sözleşme, basın bülteni, kurumsal belge
- regulatory: izahname, ihraç belgesi, lisans, yetki belgesi, denetim raporu
- privacy: KVKK, aydınlatma metni, açık rıza, çerez politikası
- other: yukarıdakilerin hiçbiri

Kurallar:
- Sadece listedeki etiketlerden birini kullan. Yeni etiket uydurma.
- Emin değilsen en yakın etiketi seç ve gerekçende belirt.
- Gerekçe tek cümle, Türkçe olsun.
"""


class _Verdict(BaseModel):
    """What the classifier must return."""

    label: str = PydanticField(description="Yalnızca izin verilen etiketlerden biri.")
    reason: str = PydanticField(description="Tek cümlelik Türkçe gerekçe.")


def classify(pdf: Path, url: str = "", anchor_text: str = "",
             model: str | None = None) -> Decision:
    """Ask a model what this PDF is, from its first page.

    Used only where the rules abstained — 161 of 1,088 files measured against the
    crawled corpus, or about one in seven. The verdict is cached by the caller
    against the PDF's content hash, so a file is classified once, ever.

    Both the page image and the page's text layer are sent. The text is what the
    file itself says; the image is what it looks like, which is the only evidence
    available for the scans.

    Returns:
        A Decision. On any failure it returns one that still reports
        `needs_model`, so a transient outage means "ask again tomorrow" rather
        than "this document is excluded forever".
    """
    import base64

    from config.settings import settings
    from llm import get_llm

    from . import pdftools

    try:
        page_text = (pdftools.text_pages(pdf) or [""])[0][:4000]
        image = pdftools.render(pdf, 1, dpi=settings.CORPUS_PDF_DPI,
                                scale_to=CLASSIFY_MAX_PX)
    except Exception as exc:  # noqa: BLE001 - an unreadable PDF is not a crash
        return Decision(False, "", f"could not read the first page: {exc}", "")

    described = f"Dosya adı: {urlsplit(url).path.rsplit('/', 1)[-1]}\n"
    if anchor_text:
        described += f"Bağlantı metni: {anchor_text}\n"
    described += f"\nSayfadaki metin katmanı:\n{page_text or '(metin katmanı yok)'}"

    try:
        llm = get_llm(model or settings.CORPUS_PDF_MODEL)
        # function_calling, never json_schema: the latter invents values for
        # fields it cannot find, which for a classifier means a confident label
        # for a page it could not read. See docs/ARCHITECTURE.md.
        structured = llm.with_structured_output(_Verdict, method="function_calling")
        message = HumanMessage(content=[
            {"type": "text", "text": CLASSIFY_PROMPT + "\n" + described},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(image).decode()}},
        ])
        # Repo-wide rule (dataprep/vlm.py::_post, crawl/policy.py, compare/*):
        # a transient outage must never become a verdict. Returning "classifier
        # unavailable" here quietly dropped PDFs whose only problem was that the
        # tunnel blipped, so this now retries forever with capped backoff and
        # only a permanent 4xx gives up.
        attempt = 0
        delay = 1.0
        started = time.time()
        last_warn = 0.0
        while True:
            attempt += 1
            try:
                verdict = structured.invoke([message])
                break
            except Exception as exc:  # noqa: BLE001 - retried below
                if any(c in str(exc) for c in ("400", "401", "403", "404", "BadRequest")):
                    return Decision(False, "", f"classifier permanent error: {exc}", "")
                elapsed = time.time() - started
                if elapsed - last_warn >= 300:
                    logger.warning("[PDF_POLICY_UZUN_SURELI_HATA] classifier failing for "
                                   "%.0fs (attempt %d): %s -- still retrying",
                                   elapsed, attempt, type(exc).__name__)
                    last_warn = elapsed
                time.sleep(delay)
                delay = min(delay * 2, 60)
    except Exception as exc:  # noqa: BLE001 - model construction itself failed
        return Decision(False, "", f"classifier unavailable: {exc}", "")

    if verdict is None:
        return Decision(False, "", "classifier returned nothing", "")
    return from_label(verdict.label, verdict.reason)


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
