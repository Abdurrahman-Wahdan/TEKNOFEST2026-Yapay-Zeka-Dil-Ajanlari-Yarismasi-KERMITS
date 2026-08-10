"""Live index + retrieval against the local Qdrant and the real embedding model.

Needs Qdrant up (docker) and the embedding model available. Uses a throwaway
collection so it never touches production data, and a tiny hand-built artifact
so it does not depend on a corpus build having run.

The two guarantees these prove end to end: a query returns web AND PDF chunks in
one ranked list, and a second sync over the same artifact does zero work.
"""

import json

import pytest

from config.settings import settings
from index import retrieve, sync

pytestmark = [pytest.mark.integration, pytest.mark.slow]

COLLECTION = "test_index_live"


@pytest.fixture(scope="module")
def qdrant_up() -> bool:
    import httpx
    try:
        return httpx.get(f"{settings.QDRANT_URL}/collections", timeout=5).status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def artifact(tmp_path, monkeypatch, qdrant_up):
    if not qdrant_up:
        pytest.skip("Qdrant is not reachable")
    monkeypatch.setattr(settings, "CORPUS_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "QDRANT_COLLECTION_CHUNKS", COLLECTION)

    docs = [
        {"doc_id": "web1", "url": "https://www.kuveytturk.com.tr/kendim-icin/konut",
         "site": "kuveytturk", "bank": "Kuveyt Türk", "source_type": "page",
         "doc_kind": "product", "title": "Konut Finansmanı", "audience": "bireysel",
         "lang": "tr", "campaign_end": "", "text_hash": "d1", "sections": [
             {"heading_path": "Konut Finansmanı", "anchor": "", "level": 1,
              "text": "Konut finansmanı kâr payı oranları ve ödeme planı hakkında "
                      "ayrıntılı bilgi bu sayfada yer almaktadır.", "order": 0,
              "cite_url": "https://www.kuveytturk.com.tr/kendim-icin/konut",
              "text_hash": "web1s0"}]},
        {"doc_id": "pdf1", "url": "https://x.com.tr/documents/ucretler.pdf",
         "site": "kuveytturk", "bank": "Kuveyt Türk", "source_type": "pdf",
         "doc_kind": "fees", "title": "Ücret Tarifesi", "audience": "", "lang": "tr",
         "campaign_end": "", "text_hash": "d2", "pages": [
             {"number": 1, "cite_url": "https://x.com.tr/documents/ucretler.pdf#page=1",
              "text_hash": "pdf1p1", "from_vision": False, "blocks": [
                  {"kind": "paragraph", "text": "Konut finansmanı dosya masrafı ve "
                   "ücret tarifesi tablosu.", "order": 0}]}]},
        {"doc_id": "camp1", "url": "https://www.kuveytturk.com.tr/kampanyalar/gecmis",
         "site": "kuveytturk", "bank": "Kuveyt Türk", "source_type": "page",
         "doc_kind": "campaign", "title": "Geçmiş Kampanya", "audience": "bireysel",
         "lang": "tr", "campaign_end": "2020-01-01", "text_hash": "d3",
         "text": "Konut kampanyası özel kâr payı oranı ile. Kampanya koşulları "
                 "aşağıda belirtilmiştir.", "sections": []},
    ]
    path = tmp_path / sync.ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(d) for d in docs) + "\n", "utf-8")

    yield
    from vector_stores.client import get_qdrant_client
    try:
        get_qdrant_client().delete_collection(COLLECTION)
    except Exception:  # noqa: BLE001
        pass


def test_indexing_then_a_search_returns_web_and_pdf_together(artifact):
    report = sync.run(collection=COLLECTION)
    assert report.embedded == 3
    assert report.healthy

    hits = retrieve.search("konut finansmanı kâr payı oranı ücret",
                           collection=COLLECTION, active_only=False)
    kinds = {h.payload.get("source_type") for h in hits}
    assert "page" in kinds and "pdf" in kinds, "one search must reach both sources"
    assert all(h.cite_url for h in hits)


def test_the_expiry_filter_hides_the_expired_campaign(artifact):
    sync.run(collection=COLLECTION)
    active = retrieve.search("konut kampanyası kâr payı", collection=COLLECTION,
                             active_only=True, today="2026-08-09")
    assert all(h.payload.get("doc_kind") != "campaign" for h in active), \
        "the 2020 campaign must be filtered out when active_only"
    # with the filter off, it is retrievable
    withexpired = retrieve.search("konut kampanyası kâr payı", collection=COLLECTION,
                                  active_only=False)
    assert any(h.payload.get("doc_kind") == "campaign" for h in withexpired)


def test_a_second_sync_over_the_same_artifact_does_nothing(artifact):
    sync.run(collection=COLLECTION)
    again = sync.run(collection=COLLECTION)
    assert again.embedded == 0
    assert again.skipped == 3
    assert not again.written
