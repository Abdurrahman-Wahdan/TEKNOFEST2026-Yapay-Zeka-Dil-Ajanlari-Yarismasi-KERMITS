"""The content-addressed store and the manifest."""

import json

import pytest

from config.settings import settings
from corpus import store

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def corpus_root(tmp_path, monkeypatch):
    """Point the store at a scratch directory for every test here."""
    monkeypatch.setattr(settings, "CORPUS_ROOT", str(tmp_path))
    store.clear_cache()
    yield tmp_path
    store.clear_cache()


# ----- blobs -----

def test_the_same_bytes_store_once(corpus_root):
    """A PDF linked from forty pages must not be stored forty times."""
    first_hash, first_blob = store.put(b"%PDF-1.4 body", "application/pdf")
    second_hash, second_blob = store.put(b"%PDF-1.4 body", "application/pdf")
    assert first_hash == second_hash
    assert first_blob == second_blob
    assert len(list((corpus_root / "raw").rglob("*.pdf"))) == 1


def test_different_bytes_store_separately():
    a_hash, _ = store.put(b"one", "text/html")
    b_hash, _ = store.put(b"two", "text/html")
    assert a_hash != b_hash


def test_stored_bytes_come_back_unchanged():
    body = "kâr payı oranı %31,80".encode("utf-8")
    _, blob = store.put(body, "text/html")
    assert store.get(blob) == body


def test_a_missing_blob_raises_rather_than_returning_nothing():
    """A manifest pointing at a missing blob is a corrupt store, not an empty page."""
    with pytest.raises(FileNotFoundError):
        store.get("aa/bb/aabbccdd.html")


def test_blobs_are_named_by_content_not_by_url():
    """This is what makes the index.md / INDEX.md collision unrepresentable."""
    _, blob = store.put(b"homepage", "text/html")
    assert "index" not in blob
    assert blob.endswith(".html")


def test_the_blob_path_is_fanned_out_two_levels():
    content_hash, blob = store.put(b"x", "text/html")
    assert blob == f"{content_hash[:2]}/{content_hash[2:4]}/{content_hash}.html"


def test_the_extension_follows_the_content_type():
    assert store.extension_for("application/pdf") == ".pdf"
    assert store.extension_for("text/html; charset=utf-8") == ".html"
    assert store.extension_for("Text/HTML") == ".html"
    assert store.extension_for("application/octet-stream") == ".bin"
    assert store.extension_for("") == ".bin"


def test_has_reports_whether_a_blob_is_present():
    _, blob = store.put(b"present", "text/html")
    assert store.has(blob)
    assert not store.has("00/00/0000000000000000.html")


# ----- manifest -----

def test_an_absent_manifest_reads_as_empty():
    """A first run must not need a file to exist before it can start."""
    assert store.read_manifest() == {}


def test_a_written_manifest_reads_back():
    store.write_manifest({"https://x.com.tr/p": {"status": 200}})
    assert store.read_manifest()["https://x.com.tr/p"]["status"] == 200


def test_a_malformed_manifest_reads_as_empty_rather_than_raising(corpus_root):
    """A half-written manifest costs one full crawl, not a dead pipeline."""
    (corpus_root / "manifest.json").write_text("{not json", encoding="utf-8")
    store.clear_cache()
    assert store.read_manifest() == {}


def test_a_manifest_that_is_not_an_object_reads_as_empty(corpus_root):
    (corpus_root / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")
    store.clear_cache()
    assert store.read_manifest() == {}


def test_writing_the_manifest_invalidates_the_read_cache():
    """Otherwise a run reads its own stale copy for the rest of the process."""
    store.write_manifest({"a": 1})
    assert store.read_manifest() == {"a": 1}
    store.write_manifest({"a": 2})
    assert store.read_manifest() == {"a": 2}


def test_the_manifest_is_written_as_readable_utf8(corpus_root):
    """A human has to be able to open this and see Turkish, not escape codes."""
    store.write_manifest({"https://x.com.tr/kâr-payı": {"title": "Kâr Payı"}})
    text = (corpus_root / "manifest.json").read_text(encoding="utf-8")
    assert "Kâr Payı" in text
    assert json.loads(text)


def test_no_temporary_files_survive_a_write(corpus_root):
    """os.replace is atomic; a reader must never see a half-written manifest."""
    store.write_manifest({"a": 1})
    store.put(b"body", "text/html")
    store.write_text("clean/documents.jsonl", '{"doc_id": "x"}\n')
    assert not list(corpus_root.rglob("*.tmp"))


# ----- text artifacts -----

def test_write_text_creates_parent_directories(corpus_root):
    store.write_text("clean/kuveytturk/abc123.md", "# Başlık\n")
    assert (corpus_root / "clean" / "kuveytturk" / "abc123.md").read_text(
        encoding="utf-8"
    ) == "# Başlık\n"


def test_write_text_replaces_an_existing_file(corpus_root):
    store.write_text("clean/documents.jsonl", "old\n")
    store.write_text("clean/documents.jsonl", "new\n")
    assert (corpus_root / "clean" / "documents.jsonl").read_text(encoding="utf-8") == "new\n"


# ----- root resolution -----

def test_an_empty_setting_puts_the_corpus_beside_the_project(monkeypatch):
    """A blank line in .env must not scatter the corpus into the working directory."""
    from config.settings import PROJECT_ROOT

    monkeypatch.setattr(settings, "CORPUS_ROOT", "")
    assert store.root() == PROJECT_ROOT / "corpus_data"


def test_whitespace_only_setting_is_treated_as_empty(monkeypatch):
    from config.settings import PROJECT_ROOT

    monkeypatch.setattr(settings, "CORPUS_ROOT", "   ")
    assert store.root() == PROJECT_ROOT / "corpus_data"


# ----- garbage collection -----

def test_an_orphaned_page_blob_is_collected(corpus_root):
    """Page bytes churn every run on an FX timestamp and a rotating WAF token,
    so without this the store grows about a gigabyte a day and none of the
    growth is information."""
    _, old = store.put(b"<html>yesterday</html>", "text/html")
    _, new = store.put(b"<html>today</html>", "text/html")
    manifest = {"https://x.com.tr/p": {"blob": new, "content_hash": "n"}}
    deleted, freed = store.collect_garbage(manifest)
    assert deleted == 1
    assert freed > 0
    assert not store.has(old)
    assert store.has(new)


def test_a_referenced_blob_is_never_collected(corpus_root):
    _, blob = store.put(b"<html>live</html>", "text/html")
    store.collect_garbage({"https://x.com.tr/p": {"blob": blob}})
    assert store.has(blob)


def test_an_orphaned_pdf_is_kept(corpus_root):
    """PDFs answered 304 on a second run, so they do not churn -- an orphaned
    one is usually a document the bank withdrew, and the only copy left of what
    a fee schedule said before it changed."""
    _, pdf = store.put(b"%PDF-1.4 withdrawn", "application/pdf")
    deleted, _ = store.collect_garbage({})
    assert deleted == 0
    assert store.has(pdf)


def test_collecting_an_empty_store_is_harmless():
    assert store.collect_garbage({}) == (0, 0)
