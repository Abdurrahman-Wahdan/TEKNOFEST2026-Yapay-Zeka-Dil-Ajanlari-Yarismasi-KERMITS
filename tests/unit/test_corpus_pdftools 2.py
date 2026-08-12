"""Poppler wrappers, and the classifier gate that sits on top of them."""

from pathlib import Path

import pytest

from corpus import pdf_policy, pdftools

pytestmark = pytest.mark.unit


# ----- tool discovery -----

def test_a_missing_binary_names_the_install_command(monkeypatch):
    """Better than dying inside a subprocess three hours into a crawl."""
    monkeypatch.setattr(pdftools.shutil, "which", lambda _: None)
    with pytest.raises(pdftools.PdfToolError, match="brew install poppler"):
        pdftools.require("pdftotext")


def test_a_present_binary_returns_its_path(monkeypatch):
    monkeypatch.setattr(pdftools.shutil, "which", lambda _: "/opt/homebrew/bin/pdftotext")
    assert pdftools.require("pdftotext") == "/opt/homebrew/bin/pdftotext"


# ----- page splitting -----

def test_pages_split_on_the_form_feed(monkeypatch):
    monkeypatch.setattr(pdftools, "_run",
                        lambda *a, **k: "one\f two\f three\f".encode("utf-8"))
    monkeypatch.setattr(pdftools, "require", lambda b: b)
    assert pdftools.text_pages(Path("x.pdf")) == ["one", " two", " three"]


def test_the_trailing_form_feed_does_not_add_an_empty_page(monkeypatch):
    """pdftotext ends every document with a break; counting it would report one
    page too many, and page numbers are what citations are built on."""
    monkeypatch.setattr(pdftools, "_run", lambda *a, **k: b"only page\f")
    monkeypatch.setattr(pdftools, "require", lambda b: b)
    assert pdftools.text_pages(Path("x.pdf")) == ["only page"]


# ----- pdfinfo -----

def test_info_parses_the_fields_it_reports(monkeypatch):
    monkeypatch.setattr(pdftools, "require", lambda b: b)
    monkeypatch.setattr(pdftools, "_run", lambda *a, **k: (
        b"Title:          Genel Kredi Sozlesmesi\n"
        b"Pages:          36\n"
        b"Page size:      595.276 x 841.89 pts (A4)\n"
        b"ModDate:        Mon Jul 29 08:53:21 2024\n"))
    fields = pdftools.info(Path("x.pdf"))
    assert fields["Pages"] == "36"
    assert fields["Title"] == "Genel Kredi Sozlesmesi"
    assert fields["ModDate"].startswith("Mon Jul 29")


def test_a_failing_pdfinfo_yields_no_fields_rather_than_raising(monkeypatch):
    """One unreadable PDF must not end a run over 8,500 documents."""
    monkeypatch.setattr(pdftools, "require", lambda b: b)

    def boom(*a, **k):
        raise pdftools.PdfToolError("encrypted")

    monkeypatch.setattr(pdftools, "_run", boom)
    assert pdftools.info(Path("x.pdf")) == {}
    assert pdftools.page_count(Path("x.pdf")) == 0


def test_page_size_converts_points_to_pixels(monkeypatch):
    monkeypatch.setattr(pdftools, "require", lambda b: b)
    monkeypatch.setattr(pdftools, "_run",
                        lambda *a, **k: b"Page size: 595.276 x 841.89 pts (A4)\n")
    width, height = pdftools.page_size(Path("x.pdf"), dpi=200)
    assert (width, height) == (1653, 2338)      # A4 at 200 DPI


def test_an_unparseable_page_size_is_zero_not_a_crash(monkeypatch):
    monkeypatch.setattr(pdftools, "require", lambda b: b)
    monkeypatch.setattr(pdftools, "_run", lambda *a, **k: b"Page size: unknown\n")
    assert pdftools.page_size(Path("x.pdf")) == (0, 0)


# ----- the classifier gate -----

class _FakeLLM:
    def __init__(self, verdict=None, error=None):
        self._verdict, self._error = verdict, error

    def with_structured_output(self, _schema, **_kwargs):
        return self

    def invoke(self, _messages):
        if self._error:
            raise self._error
        return self._verdict


class _V:
    def __init__(self, label, reason=""):
        self.label, self.reason = label, reason


@pytest.fixture
def readable_pdf(monkeypatch):
    """A PDF whose first page reads and renders without touching poppler."""
    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["Ürün ve Hizmet Ücretleri"])
    monkeypatch.setattr(pdftools, "render", lambda *a, **k: b"\x89PNG fake")


def _with_llm(monkeypatch, llm):
    import llm as llm_module
    monkeypatch.setattr(llm_module, "get_llm", lambda *a, **k: llm)


def test_a_model_label_becomes_a_decision(monkeypatch, readable_pdf):
    _with_llm(monkeypatch, _FakeLLM(_V("fees", "Ücret tarifesi listesi.")))
    decision = pdf_policy.classify(Path("x.pdf"), "https://x.com.tr/a.pdf")
    assert decision.accepted
    assert decision.label == "fees"
    assert decision.decided_by == "model"


def test_a_model_rejection_is_final_not_ambiguous(monkeypatch, readable_pdf):
    """A classified-and-rejected document must not be re-sent every night."""
    _with_llm(monkeypatch, _FakeLLM(_V("privacy", "KVKK aydınlatma metni.")))
    decision = pdf_policy.classify(Path("x.pdf"))
    assert not decision.accepted
    assert not decision.needs_model


def test_an_invented_label_is_not_accepted(monkeypatch, readable_pdf):
    _with_llm(monkeypatch, _FakeLLM(_V("bir tür ücret belgesi")))
    assert not pdf_policy.classify(Path("x.pdf")).accepted


def test_an_llm_outage_leaves_the_document_for_tomorrow(monkeypatch, readable_pdf):
    """A transient outage must mean "ask again", never "excluded forever"."""
    _with_llm(monkeypatch, _FakeLLM(error=RuntimeError("connection refused")))
    decision = pdf_policy.classify(Path("x.pdf"))
    assert decision.needs_model
    assert "classifier unavailable" in decision.reason


def test_an_empty_model_answer_leaves_the_document_for_tomorrow(monkeypatch, readable_pdf):
    _with_llm(monkeypatch, _FakeLLM(verdict=None))
    assert pdf_policy.classify(Path("x.pdf")).needs_model


def test_an_unreadable_pdf_is_reported_not_swallowed(monkeypatch):
    def boom(*a, **k):
        raise pdftools.PdfToolError("pdftoppm produced no image")

    monkeypatch.setattr(pdftools, "text_pages", lambda _p: [""])
    monkeypatch.setattr(pdftools, "render", boom)
    decision = pdf_policy.classify(Path("x.pdf"))
    assert decision.needs_model
    assert "could not read the first page" in decision.reason


def test_the_page_image_is_capped_before_it_is_sent(monkeypatch):
    """An uncapped render of a large page earned a 413 from the vLLM host."""
    seen = {}

    def spy(_pdf, _page, **kwargs):
        seen.update(kwargs)
        return b"\x89PNG"

    monkeypatch.setattr(pdftools, "text_pages", lambda _p: ["x"])
    monkeypatch.setattr(pdftools, "render", spy)
    _with_llm(monkeypatch, _FakeLLM(_V("other")))
    pdf_policy.classify(Path("x.pdf"))
    assert seen["scale_to"] == pdf_policy.CLASSIFY_MAX_PX
