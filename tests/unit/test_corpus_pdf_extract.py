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


def _install(monkeypatch, llm):
    import llm as llm_module
    monkeypatch.setattr(llm_module, "get_llm", lambda *a, **k: llm)


def test_a_block_repeated_across_tiles_is_kept_once(monkeypatch, fake_pdf):
    """Tiles overlap so a seam line survives, which makes the model return the
    straddling block twice. Measured on a real bulletin: every paragraph on
    page 2 doubled, and the document was wrongly flagged suspect."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["kâr payı oranı " * 20])
    both = _Out([("paragraph", "Tapu Güvenilir Hesap uygulaması."),
                 ("paragraph", "Taraflar tapu müdürlüklerine başvuruyor.")])
    _install(monkeypatch, _FakeLLM([both, both]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    texts = [b.text for b in result.pages[0].blocks]
    assert len(texts) == len(set(texts)) == 2


def test_a_page_carries_its_number_and_citation(monkeypatch, fake_pdf):
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["a" * 200, "b" * 200])
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "içerik bir")]),
                                    _Out([("paragraph", "içerik iki")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert [p.number for p in result.pages] == [1, 2]
    assert result.pages[1].cite_url == "https://x.com.tr/a.pdf#page=2"


def test_a_table_is_marked_as_a_table(monkeypatch, fake_pdf):
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["x" * 200])
    _install(monkeypatch, _FakeLLM([_Out([("table", "| Ücret | Tutar |\n|---|---|")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages[0].has_tables


def test_an_unknown_block_kind_becomes_a_paragraph(monkeypatch, fake_pdf):
    """A model inventing a kind must not create an unbounded vocabulary that
    later becomes chunk boundaries."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["x" * 200])
    _install(monkeypatch, _FakeLLM([_Out([("sidebar", "bir şey")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages[0].blocks[0].kind == "paragraph"


def test_a_blind_model_fails_the_document_rather_than_emptying_it(monkeypatch, fake_pdf):
    """Continuing would write pages that look like a document with nothing in it."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["x" * 200])
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "I cannot see the image")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert not result.ok
    assert "could not see" in result.error
    assert result.pages == ()


def test_a_page_the_model_ignores_falls_back_to_its_text_layer(monkeypatch, fake_pdf):
    """Losing a page because the model returned nothing would be silent data loss."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["kâr payı oranı " * 30])
    _install(monkeypatch, _FakeLLM([_Out([])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert len(result.pages) == 1
    assert "kâr payı" in result.text


def test_a_scanned_page_is_marked_as_read_from_vision(monkeypatch, fake_pdf):
    """So the agent can hedge: vision paraphrases plausibly."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: [""])
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "taranmış içerik burada")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages[0].from_vision
    assert result.low_confidence


def test_a_long_pdf_is_truncated_and_says_so(monkeypatch, fake_pdf):
    monkeypatch.setattr(settings, "CORPUS_PDF_MAX_PAGES", 3)
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["x" * 200] * 120)
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "sayfa")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.truncated
    assert result.page_count == 120
    assert len(result.pages) <= 3


def test_an_extractor_outage_on_every_page_is_reported_not_a_success(monkeypatch, fake_pdf):
    """Falling back to the text layer for every page would produce a document
    with no tables and no headings that still reads as a clean extraction."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["x" * 200] * 3)
    _install(monkeypatch, _FakeLLM([], error=RuntimeError("connection refused")))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert not result.ok
    assert "every page" in result.error


def test_one_failed_page_keeps_the_document_but_lowers_confidence(monkeypatch, fake_pdf):
    """One bad page must not lose the file, but must not pass unremarked either."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["kâr payı " * 40] * 2)
    calls = {"n": 0}

    class _Flaky(_FakeLLM):
        def invoke(self, _messages):
            calls["n"] += 1
            # Only the first tile fails; a page aborts on its first bad tile, so
            # this loses page 1 and leaves page 2 intact.
            if calls["n"] == 1:
                raise RuntimeError("timeout")
            return _Out([("paragraph", "ikinci sayfa içeriği")])

    _install(monkeypatch, _Flaky([]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages
    assert result.low_confidence


def test_an_unreadable_file_is_reported(monkeypatch):
    def boom(_p):
        raise pdftools.PdfToolError("not a PDF")

    monkeypatch.setattr(pdftools, "text_pages", boom)
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert not result.ok
    assert "not a PDF" in result.error


def test_each_page_hashes_separately_for_surgical_re_embedding(monkeypatch, fake_pdf):
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["a" * 200, "b" * 200])
    _install(monkeypatch, _FakeLLM([_Out([("paragraph", "sayfa bir")]),
                                    _Out([("paragraph", "sayfa iki")])]))
    result = extract(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert result.pages[0].text_hash != result.pages[1].text_hash
