"""Quality detectors, and turning a PDF into citable pages."""

from pathlib import Path

import pytest

from config.settings import settings
from corpus import pdf_extract, pdftools, quality
from corpus.pdf_extract import extract

pytestmark = pytest.mark.unit


# ----- detectors -----

def test_a_per_page_stamp_is_found():
    """The 113-page scans carry one of these per page, and it is why the old
    crawler's 40-character floor mistook them for readable text."""
    pages = [f"Doğrulama Kodu: f9702c43\nsayfa {n} içeriği" for n in range(10)]
    assert "Doğrulama Kodu: f9702c43" in quality.stamp_lines(pages)


def test_a_line_on_a_few_pages_is_not_a_stamp():
    pages = ["ortak satır\nbir", "ortak satır\niki", "üç", "dört", "beş"]
    assert "ortak satır" not in quality.stamp_lines(pages, fraction=0.8)


def test_a_scanned_page_has_no_text_once_its_stamp_is_gone():
    """This is the whole classification: 0-1 characters versus 269 and up."""
    stamps = {"Doğrulama Kodu: f9702c43"}
    stripped = quality.strip_stamps("Doğrulama Kodu: f9702c43\n", stamps)
    assert not quality.page_has_text(stripped, minimum=100)


def test_a_real_page_still_has_text_after_stamp_stripping():
    stamps = {"1/23 GENEL KREDİ SÖZLEŞMESİ"}
    page = "1/23 GENEL KREDİ SÖZLEŞMESİ\n" + ("kâr payı oranı ve ücretler. " * 20)
    assert quality.page_has_text(quality.strip_stamps(page, stamps))


def test_the_repetition_pathology_scores_near_zero():
    """pypdf returned the whole body for every page; the ratio is how that is
    detected without knowing which extractor produced the text."""
    doc = "\n".join(["kabul ve beyan eder efendim hazretleri"] * 40)
    assert quality.unique_line_ratio(doc) < 0.1


def test_healthy_text_scores_near_one():
    doc = "\n".join(f"{n}. madde: kâr payı oranı ve ücretler hakkında." for n in range(40))
    assert quality.unique_line_ratio(doc) > 0.9


def test_too_little_text_is_not_judged_repetitive():
    """Two identical short lines is not evidence of a broken extractor."""
    assert quality.unique_line_ratio("aynı\naynı") == 1.0


def test_turkish_prose_scores_high_and_garbage_scores_low():
    turkish = ("Bu kampanya kapsamında müşterilerimize özel kâr payı oranı ile "
               "ödeme kolaylığı sunulmaktadır ve şartlar aşağıda belirtilmiştir.")
    assert quality.turkish_score(turkish) > 0.6
    assert quality.turkish_score("### %%% ??? 123 ///" * 20) < 0.4


def test_english_text_scores_below_turkish():
    """Used to tag the genuinely English documents rather than fail them."""
    english = ("This questionnaire is designed to provide information about the "
               "financial institution and its anti money laundering programme.")
    assert quality.turkish_score(english) < quality.turkish_score(
        "Bu form, banka ile ilgili bilgileri ve kâr payı oranlarını içerir ve "
        "müşteri tarafından imzalanması gerekmektedir.")


def test_a_blind_model_answer_is_recognised():
    """HTTP 200 with a fluent refusal is the only shape this failure has."""
    assert quality.looks_blind("I'm sorry, but I can't see the image.")
    assert quality.looks_blind("Üzgünüm, görseli göremiyorum.")
    assert not quality.looks_blind("Sayfada kâr payı oranı tablosu bulunmaktadır.")


def test_unreadable_marks_are_counted():
    text = f"bir\n{quality.UNREADABLE}\nüç\n{quality.UNREADABLE}"
    assert quality.unreadable_ratio(text) == 0.5


def test_normalisation_composes_turkish_and_expands_ligatures():
    assert quality.normalise("ğ" + "ünlük") == quality.normalise("ğünlük")
    assert "finansman" in quality.normalise("ﬁnansman")


def test_normalisation_is_nfc_not_nfkd():
    """fold() is NFKD and strips punctuation. Running it over stored text would
    turn "İhtiyaç Finansmanı" into "ihtiyacfinansmani"."""
    assert quality.normalise("İhtiyaç Finansmanı") == "İhtiyaç Finansmanı"


# ----- citations -----

def test_a_pdf_page_cites_with_a_page_fragment():
    assert pdf_extract.cite_url("https://x.com.tr/a.pdf", 7) == "https://x.com.tr/a.pdf#page=7"


def test_a_page_with_no_url_cites_nothing_rather_than_a_bare_fragment():
    assert pdf_extract.cite_url("", 7) == ""


# ----- extraction -----
#
# The page model is markdown plus items. It used to be a list of typed blocks,
# and tiling used to cut each page into overlapping strips; both were removed
# deliberately -- strips split tables across the seams and left rows without the
# column header that gave them meaning. The tests for those two designs are gone
# with them rather than kept passing against code that no longer exists.


class _Out:
    """What the structured model returns: one page as markdown, plus items."""

    def __init__(self, markdown="", items=()):
        self.markdown = markdown
        self.items = list(items)


def _item(item_id, marker, summary="özet", visible="metin", visual="düzen"):
    return type("I", (), {
        "id": item_id, "marker": marker, "summary": summary,
        "visible_text": visible, "visual_representation": visual,
    })()


class _FakeLLM:
    def __init__(self, per_call, error=None):
        self._per_call, self._error, self.calls = per_call, error, 0

    def with_structured_output(self, _schema, **_kw):
        return self

    def invoke(self, _messages):
        if self._error:
            raise self._error
        result = self._per_call[min(self.calls, len(self._per_call) - 1)]
        self.calls += 1
        return result


@pytest.fixture
def fake_pdf(monkeypatch):
    monkeypatch.setattr(pdftools, "page_size", lambda *a, **k: (1000, 2000))
    monkeypatch.setattr(pdftools, "render", lambda *a, **k: b"\x89PNG")
    monkeypatch.setattr(settings, "CORPUS_PDF_RETRY_BACKOFF", 0.0)


def _pages(monkeypatch, count):
    """How many pages the file has. The text layer is never consulted."""
    monkeypatch.setattr(pdftools, "page_count", lambda _p: count)


def _install(monkeypatch, llm):
    import llm as llm_module
    monkeypatch.setattr(llm_module, "get_llm", lambda *a, **k: llm)


def test_a_readable_pdf_comes_back_whole(monkeypatch, fake_pdf):
    """The one that would have caught the outage.

    `extract` had its body duplicated by a bad merge, and the first copy ended
    in an unconditional `return Extraction(..., error=str(exc))` referencing a
    name bound only inside an earlier `except`. So every call read every page
    -- one model request each, paid for -- and then died with
    UnboundLocalError, while the correct implementation sat below it as dead
    code. Nothing asserted that a good PDF simply works, so nothing failed in a
    way that named the cause.
    """
    _pages(monkeypatch, 2)
    llm = _FakeLLM([_Out("## Ücret Tarifesi"), _Out("ikinci sayfa")])
    _install(monkeypatch, llm)

    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")

    assert result.ok
    assert result.error == ""
    assert [p.number for p in result.pages] == [1, 2]
    assert llm.calls == 2
    assert "Ücret Tarifesi" in result.text and "ikinci sayfa" in result.text


def test_the_text_layer_is_never_read(monkeypatch, fake_pdf):
    """The standard is one thing only: every page is read from its image. If
    anything ever calls pdftotext for content again, this fails."""
    _pages(monkeypatch, 1)

    def forbidden(_p):
        raise AssertionError("the text layer must never be read")

    monkeypatch.setattr(pdftools, "text_pages", forbidden)
    _install(monkeypatch, _FakeLLM([_Out("görüntüden okundu")]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.text == "görüntüden okundu"


def test_every_page_is_marked_from_vision(monkeypatch, fake_pdf):
    """One provenance for every PDF page, so a citation means the same thing
    everywhere."""
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out("bir"), _Out("iki")]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert all(p.from_vision for p in result.pages)
    assert result.engine == "ocr"
    assert result.low_confidence


def test_a_page_carries_its_number_and_citation(monkeypatch, fake_pdf):
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out("içerik bir"), _Out("içerik iki")]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert [p.number for p in result.pages] == [1, 2]
    assert result.pages[1].cite_url == "https://x.com.tr/a.pdf#page=2"


def test_a_table_item_marks_the_page_as_having_tables(monkeypatch, fake_pdf):
    """`has_tables` now comes from the items, not from a block kind."""
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([
        _Out("Ücretler <table_1>", [_item("table_1", "<table_1>")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    page = result.pages[0]
    assert page.has_tables
    assert not page.has_images
    assert page.items[0].marker == "<table_1>"
    # The marker stays in the markdown, which is what pins the item in place.
    assert "<table_1>" in page.markdown


def test_a_figure_item_marks_images_not_tables(monkeypatch, fake_pdf):
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([
        _Out("Şema <figure_1>", [_item("figure_1", "<figure_1>")])]))
    page = extract(Path("x.pdf"), "https://x.com.tr/a.pdf").pages[0]
    assert page.has_images and not page.has_tables


def test_a_blind_model_fails_the_document_rather_than_emptying_it(monkeypatch, fake_pdf):
    """Continuing would write pages that look like a document with nothing in it."""
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([_Out("I cannot see the image")]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert not result.ok
    assert "could not see" in result.error
    assert result.pages == ()


def test_a_blank_page_is_kept_so_page_numbers_keep_matching(monkeypatch, fake_pdf):
    """Dropping a blank page silently renumbers every citation after it.

    The opposite of the old behaviour, and deliberately so: whether a document
    is wanted is decided upstream by relevance, not by how much text happened
    to land on one of its pages.
    """
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out(""), _Out("ikinci sayfa")]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert [p.number for p in result.pages] == [1, 2]
    assert result.pages[0].markdown == ""
    assert result.pages[1].cite_url.endswith("#page=2")


def test_no_structured_output_is_a_failure_not_a_blank_page(monkeypatch, fake_pdf):
    """The subtlest form of the loss. The call returns None, which looks like a
    page with nothing on it -- and a real bilingual form was emptied that way
    before the image was capped."""
    monkeypatch.setattr(settings, "CORPUS_PDF_PAGE_ATTEMPTS", 2)
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([None]))
    with pytest.raises(pdf_extract.TransientExtractionError):
        extract(Path("x.pdf"), "https://x.com.tr/a.pdf")


def test_a_page_that_fails_is_retried_before_anything_is_concluded(monkeypatch, fake_pdf):
    """A dropped request says nothing about the document, so it must be retried
    rather than persisted as a verdict."""
    monkeypatch.setattr(settings, "CORPUS_PDF_PAGE_ATTEMPTS", 3)
    _pages(monkeypatch, 1)
    llm = _FakeLLM([None, None, _Out("sonunda okundu")])
    _install(monkeypatch, llm)
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert llm.calls == 3
    assert result.pages[0].markdown == "sonunda okundu"


def test_each_page_hashes_separately_for_surgical_re_embedding(monkeypatch, fake_pdf):
    """A one-page change must re-embed one page, not the whole document."""
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out("sayfa bir"), _Out("sayfa iki")]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    hashes = [p.text_hash for p in result.pages]
    assert all(hashes) and len(set(hashes)) == 2


def test_a_long_pdf_is_truncated_and_says_so(monkeypatch, fake_pdf):
    """Truncation is reported, never silent: page_count stays the real total."""
    monkeypatch.setattr(settings, "CORPUS_PDF_MAX_PAGES", 2)
    _pages(monkeypatch, 40)
    _install(monkeypatch, _FakeLLM([_Out("içerik")]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.truncated
    assert result.page_count == 40
    assert len(result.pages) == 2


def test_the_page_image_is_capped_in_size(monkeypatch, fake_pdf):
    """Uncapped, a dense page renders large enough that the model returns
    nothing at all."""
    seen = {}
    monkeypatch.setattr(pdftools, "render",
                        lambda *a, **k: seen.update(k) or b"\x89PNG")
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([_Out("içerik")]))
    extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert seen.get("scale_to") == settings.CORPUS_PDF_SCALE_TO


def test_a_missing_pdf_is_reported_not_raised(monkeypatch, fake_pdf):
    """A file poppler cannot open is an answer about that file."""
    def broken(_p):
        raise pdftools.PdfToolError("cannot open")

    monkeypatch.setattr(pdftools, "page_count", broken)
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert not result.ok
    assert "cannot open" in result.error


def test_a_str_path_is_accepted_like_a_Path(monkeypatch, fake_pdf):
    """Real callers pass both, and one of them used to crash mid-run.

    `extract` is annotated `Path` but the corpus build and the live tests hand
    it a `str`. Nothing failed until the per-page progress line read
    `pdf.name`, by which point the extractor had already been built -- so the
    failure landed deep in the loop as an AttributeError rather than at the
    call.
    """
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([_Out("içerik")]))
    result = extract("x.pdf", "https://x.com.tr/a.pdf")
    assert result.ok
    assert result.pages[0].cite_url.endswith("#page=1")
