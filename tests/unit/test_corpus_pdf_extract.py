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


# ----- tiling -----

def test_tiles_cover_the_page_and_overlap():
    boxes = pdf_extract._tiles(1000, 2000, 2)
    assert len(boxes) == 2
    assert boxes[0][1] == 0
    # the second tile starts above where the first ended
    assert boxes[1][1] < boxes[0][1] + boxes[0][3]
    assert boxes[-1][1] + boxes[-1][3] == 2000


def test_a_single_tile_means_no_cropping():
    assert pdf_extract._tiles(1000, 2000, 1) == []


# ----- extraction -----

class _Out:
    def __init__(self, blocks):
        self.blocks = [type("B", (), {"kind": k, "text": t})() for k, t in blocks]


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
    # One request per page here, so a test counting calls is counting pages.
    # The tiling tests set this themselves.
    monkeypatch.setattr(settings, "CORPUS_PDF_TILES", 1)


def _pages(monkeypatch, count):
    """How many pages the file has. The text layer is never consulted."""
    monkeypatch.setattr(pdftools, "page_count", lambda _p: count)


def _install(monkeypatch, llm):
    import llm as llm_module
    monkeypatch.setattr(llm_module, "get_llm", lambda *a, **k: llm)


def test_the_text_layer_is_never_read(monkeypatch, fake_pdf):
    """The standard is one thing only: every page is read from its image. If
    anything ever calls pdftotext for content again, this fails."""
    _pages(monkeypatch, 1)

    def forbidden(_p):
        raise AssertionError("the text layer must never be read")

    monkeypatch.setattr(pdftools, "text_pages", forbidden)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "görüntüden okundu")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.text == "görüntüden okundu"


def test_every_page_is_marked_from_vision(monkeypatch, fake_pdf):
    """One provenance for every PDF page, so a citation means the same thing
    everywhere."""
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "bir")]),
                                    _Out([("paragraph", "iki")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert all(p.from_vision for p in result.pages)
    assert result.engine == "ocr"
    assert result.low_confidence


def test_a_block_repeated_across_tiles_is_kept_once(monkeypatch, fake_pdf):
    """Tiles overlap so a seam line survives, which makes the model return the
    straddling block twice. Measured on a real bulletin: every paragraph on
    page 2 doubled, and the document was wrongly flagged suspect."""
    monkeypatch.setattr(settings, "CORPUS_PDF_TILES", 2)
    _pages(monkeypatch, 1)
    both = _Out([("paragraph", "Tapu Güvenilir Hesap uygulaması."),
                 ("paragraph", "Taraflar tapu müdürlüklerine başvuruyor.")])
    _install(monkeypatch, _FakeLLM([both, both]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    texts = [b.text for b in result.pages[0].blocks]
    assert len(texts) == len(set(texts)) == 2


def test_a_page_carries_its_number_and_citation(monkeypatch, fake_pdf):
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "içerik bir")]),
                                    _Out([("paragraph", "içerik iki")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert [p.number for p in result.pages] == [1, 2]
    assert result.pages[1].cite_url == "https://x.com.tr/a.pdf#page=2"


def test_a_table_is_marked_as_a_table(monkeypatch, fake_pdf):
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([_Out([("table", "| Ücret | Tutar |\n|---|---|")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages[0].has_tables


def test_an_unknown_block_kind_becomes_a_paragraph(monkeypatch, fake_pdf):
    """A model inventing a kind must not create an unbounded vocabulary that
    later becomes chunk boundaries."""
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([_Out([("sidebar", "bir şey")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages[0].blocks[0].kind == "paragraph"


def test_a_blind_model_fails_the_document_rather_than_emptying_it(monkeypatch, fake_pdf):
    """Continuing would write pages that look like a document with nothing in it."""
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "I cannot see the image")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert not result.ok
    assert "could not see" in result.error
    assert result.pages == ()


# ----- empty versus failed: the distinction the whole design turns on -----

def test_a_page_the_model_read_as_blank_is_dropped(monkeypatch, fake_pdf):
    """The reading succeeded and found nothing, so the page really is blank --
    a separator or a back cover. This is the only honest reason to drop one."""
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out([]), _Out([("paragraph", "ikinci sayfa")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert [p.number for p in result.pages] == [2]
    assert result.empty_pages == 1


def test_a_region_the_model_loops_on_is_split_and_retried(monkeypatch, fake_pdf):
    """The model burns its whole budget on a region with too much text in it and
    returns no tool call. Halving that region is what gets the text out, and it
    is the difference between reading a dense contract page and losing it."""
    monkeypatch.setattr(settings, "CORPUS_PDF_MAX_SPLIT_DEPTH", 2)
    _pages(monkeypatch, 1)
    calls = {"n": 0}

    class _FailsUntilSplit(_FakeLLM):
        def invoke(self, _messages):
            calls["n"] += 1
            # the whole tile fails; each half succeeds
            return None if calls["n"] == 1 else _Out([("paragraph", f"parça {calls['n']}")])

    _install(monkeypatch, _FailsUntilSplit([]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert calls["n"] == 3                       # one failure, then two halves
    assert len(result.pages[0].blocks) == 2      # both halves' text kept


def test_a_region_still_failing_at_max_depth_gives_up(monkeypatch, fake_pdf):
    """Splitting has to stop somewhere: a region that fails when it is tiny is
    not failing because of its length, so more cuts would only burn tokens."""
    monkeypatch.setattr(settings, "CORPUS_PDF_MAX_SPLIT_DEPTH", 1)
    monkeypatch.setattr(settings, "CORPUS_PDF_PAGE_ATTEMPTS", 1)
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([None]))
    with pytest.raises(pdf_extract.TransientExtractionError):
        extract(Path("x.pdf"), "https://x.com.tr/a.pdf")


def test_no_structured_output_is_a_failure_not_a_blank_page(monkeypatch, fake_pdf):
    """The subtlest form of the loss. The call returns None, which looks like a
    page with no blocks on it -- and a real bilingual form was emptied that way
    before the image was capped."""
    monkeypatch.setattr(settings, "CORPUS_PDF_PAGE_ATTEMPTS", 2)
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([None]))
    with pytest.raises(pdf_extract.TransientExtractionError):
        extract(Path("x.pdf"), "https://x.com.tr/a.pdf")


def test_the_page_image_is_capped_in_size(monkeypatch, fake_pdf):
    """Uncapped, a dense page renders large enough that the model returns
    nothing at all."""
    seen = {}
    monkeypatch.setattr(pdftools, "render",
                        lambda *a, **k: seen.update(k) or b"\x89PNG")
    _pages(monkeypatch, 1)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "içerik")])]))
    extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert seen["scale_to"] == settings.CORPUS_PDF_SCALE_TO


def test_a_page_that_fails_is_retried_before_anything_is_concluded(monkeypatch, fake_pdf):
    """A tunnel blip is not a verdict about the page."""
    monkeypatch.setattr(settings, "CORPUS_PDF_PAGE_ATTEMPTS", 3)
    _pages(monkeypatch, 1)
    calls = {"n": 0}

    class _Flaky(_FakeLLM):
        def invoke(self, _messages):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("ngrok gateway error")
            return _Out([("paragraph", "sonunda okundu")])

    _install(monkeypatch, _Flaky([]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert calls["n"] == 3
    assert result.pages[0].blocks[0].text == "sonunda okundu"


def test_a_page_that_never_reads_refuses_the_whole_pdf(monkeypatch, fake_pdf):
    """The bug this replaces: the page was dropped and the document was still
    written, so a 40-page contract silently became 39 pages and the cache made
    that permanent. Refusing the file means the next run redoes it."""
    monkeypatch.setattr(settings, "CORPUS_PDF_PAGE_ATTEMPTS", 2)
    _pages(monkeypatch, 3)
    _install(monkeypatch, _FakeLLM([], error=RuntimeError("ngrok gateway error")))
    with pytest.raises(pdf_extract.TransientExtractionError):
        extract(Path("x.pdf"), "https://x.com.tr/a.pdf")


def test_one_bad_page_among_good_ones_still_refuses_the_pdf(monkeypatch, fake_pdf):
    """A document with a hole must never be written, however small the hole."""
    monkeypatch.setattr(settings, "CORPUS_PDF_PAGE_ATTEMPTS", 2)
    _pages(monkeypatch, 3)
    calls = {"n": 0}

    class _SecondPageDies(_FakeLLM):
        def invoke(self, _messages):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("ngrok gateway error")
            return _Out([("paragraph", "ilk sayfa")])

    _install(monkeypatch, _SecondPageDies([]))
    with pytest.raises(pdf_extract.TransientExtractionError):
        extract(Path("x.pdf"), "https://x.com.tr/a.pdf")


def test_a_long_pdf_is_truncated_and_says_so(monkeypatch, fake_pdf):
    monkeypatch.setattr(settings, "CORPUS_PDF_MAX_PAGES", 3)
    _pages(monkeypatch, 120)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "sayfa")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.truncated
    assert result.page_count == 120
    assert len(result.pages) <= 3


def test_an_unreadable_file_is_reported(monkeypatch):
    def boom(_p):
        raise pdftools.PdfToolError("not a PDF")

    monkeypatch.setattr(pdftools, "page_count", boom)
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert not result.ok
    assert "not a PDF" in result.error


def test_each_page_hashes_separately_for_surgical_re_embedding(monkeypatch, fake_pdf):
    _pages(monkeypatch, 2)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "sayfa bir")]),
                                    _Out([("paragraph", "sayfa iki")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages[0].text_hash != result.pages[1].text_hash
