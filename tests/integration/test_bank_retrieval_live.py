"""The specialists' corpus tools against the real collection.

What these cover that the unit tests cannot: whether the bank name each
specialist is bound to is a name the store actually holds. That mapping is the
one part of this feature with no loud failure mode -- a filter on the wrong
name matches nothing and raises nothing -- so the only way to know it is right
is to ask the collection and count.

No fixture holds a client across tests: `tests/conftest.py` closes every cached
Qdrant client between tests, and a fixture that cached one would hand the next
test a closed socket.
"""

import pytest

from agents.shared.bank_tools import build_bank_tools
from agents.shared.registry import SPECS
from corpus import search
from corpus.sites import get_site
from vector_stores.client import get_qdrant_client

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@pytest.fixture
def client():
    live = get_qdrant_client()
    if not live.collection_exists(search.COLLECTION):
        pytest.skip(f"{search.COLLECTION} is not indexed here")
    return live


def _tools(bank):
    return {tool.name: tool for tool in build_bank_tools(bank)}


def _documents(live) -> dict[tuple[str, str, str], int]:
    """(bank, url, type) -> how many chunks it has, over the whole collection."""
    counts: dict[tuple[str, str, str], int] = {}
    offset = None
    while True:
        points, offset = live.scroll(
            search.COLLECTION, limit=1000, offset=offset,
            with_payload=["metadata.url", "metadata.bank", "metadata.type"])
        for point in points:
            meta = (point.payload or {}).get("metadata", {}) or {}
            key = (meta.get("bank"), meta.get("url"), meta.get("type"))
            counts[key] = counts.get(key, 0) + 1
        if offset is None:
            return counts


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.bank)
def test_every_specialist_finds_its_own_banks_documents(client, spec):
    """The proof of the slug mapping: ten banks, ten non-empty answers."""
    out = _tools(spec.bank)["search_bank"].invoke(
        {"query": "katılım bankacılığı kâr payı hesap",
         "intent": "Bu bankanın yayımladığı içerikten herhangi bir sayfa bul"})
    assert "point_id=" in out, f"{spec.bank} searched its own corpus and found nothing"
    assert "url=http" in out


def test_a_real_multi_chunk_document_can_be_walked(client):
    """Zero-overlap splitting is why `expand_chunk` exists; this reads across a
    real boundary rather than a fixture's idea of one."""
    counts = _documents(client)
    (bank, url, kind), total = max(counts.items(), key=lambda kv: kv[1])
    assert total > 1, "the collection has no multi-chunk document to walk"

    spec = next(s for s in SPECS if get_site(s.bank).corpus_slug == bank)
    tools = _tools(spec.bank)

    middle = total // 2
    points, _ = client.scroll(
        search.COLLECTION, limit=1, with_payload=False,
        scroll_filter=_one_chunk_filter(bank, url, kind, middle))
    assert points, "could not locate the anchor chunk"

    out = tools["expand_chunk"].invoke(
        {"point_id": str(points[0].id), "before": 1, "after": 1})
    assert out.count("point_id=") == 3
    assert f"Bu belgede 0..{total - 1} arası parça var" in out
    assert f"{middle - 1}..{middle + 1} arasını görüyorsun" in out
    assert "before/after" in out


def _one_chunk_filter(bank, url, kind, index):
    from qdrant_client import models

    return search._bank_filter(
        bank,
        models.FieldCondition(key="metadata.url", match=models.MatchValue(value=url)),
        models.FieldCondition(key="metadata.type", match=models.MatchValue(value=kind)),
        models.FieldCondition(key="metadata.chunk_index",
                              match=models.MatchValue(value=index)),
    )


def test_reading_a_whole_page_works_against_the_key_the_store_uses(client):
    """`read_full_page` filtered on metadata names no point carries, so every
    call returned 'not found'. Anything but that message proves the fix."""
    tools = _tools("vakif")
    out = tools["search_bank"].invoke(
        {"query": "katılma hesabı", "intent": "Bir sayfa bul"})
    url = out.split("url=")[1].split()[0]
    page = tools["read_full_page"].invoke({"url": url})
    assert "bulunamadı" not in page
    assert "point_id=" in page


def test_the_largest_document_is_refused_rather_than_truncated(client):
    """One real document runs to hundreds of thousands of characters -- larger
    than the model's whole window. It must come back as a size report, with no
    content in it at all."""
    counts = _documents(client)
    (bank, url, _), total = max(counts.items(), key=lambda kv: kv[1])
    spec = next(s for s in SPECS if get_site(s.bank).corpus_slug == bank)

    out = _tools(spec.bank)["read_full_page"].invoke({"url": url})
    if "kırpılmadı" not in out:
        pytest.skip(f"the largest document ({total} chunks) still fits the budget")
    assert "expand_chunk" in out
    assert len(out) < search.MAX_TOOL_CHARS
