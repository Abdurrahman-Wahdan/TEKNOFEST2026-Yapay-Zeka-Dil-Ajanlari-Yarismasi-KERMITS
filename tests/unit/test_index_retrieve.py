"""Search: the filters, and the campaign expiry rule that keeps dead offers out."""

import pytest

from index import retrieve
from index.retrieve import _filter, search

pytestmark = pytest.mark.unit


def _keys(filter_obj):
    """The payload keys named by a filter's top-level `must` conditions."""
    keys = []
    for cond in (filter_obj.must or []):
        if hasattr(cond, "key") and cond.key:
            keys.append(cond.key)
    return keys


# ----- the filter -----

def test_no_facets_and_no_expiry_means_no_filter():
    assert _filter(None, None, None, None, active_only=False, today="2026-08-09") is None


def test_a_bank_facet_becomes_a_match_condition():
    f = _filter("Kuveyt Türk", None, None, None, active_only=False, today="2026-08-09")
    assert "bank" in _keys(f)


def test_source_type_is_only_constrained_when_given():
    """Default (None) must search web + PDF together -- no source_type condition."""
    both = _filter(None, None, None, None, active_only=False, today="2026-08-09")
    assert both is None
    narrowed = _filter(None, None, "pdf", None, active_only=False, today="2026-08-09")
    assert "source_type" in _keys(narrowed)


def test_active_only_adds_the_expiry_disjunction():
    """A nested should = (no end date) OR (end >= today)."""
    from qdrant_client.models import Filter
    f = _filter(None, None, None, None, active_only=True, today="2026-08-09")
    nested = [c for c in f.must if isinstance(c, Filter)]
    assert nested, "expected a nested should-filter for expiry"
    should = nested[0].should
    assert len(should) == 2      # IsEmpty(campaign_end)  OR  campaign_end >= today


def test_active_only_can_be_turned_off():
    f = _filter(None, None, None, None, active_only=False, today="2026-08-09")
    assert f is None      # nothing else constrains it, so no filter at all


# ----- search end to end -----

class _Hit:
    def __init__(self, score, payload):
        self.score, self.payload = score, payload


class _FakeClient:
    def __init__(self, hits):
        self._hits = hits
        self.last_filter = "unset"

    def query_points(self, collection_name, query, query_filter, limit, with_payload):
        self.last_filter = query_filter
        return type("R", (), {"points": self._hits[:limit]})()


class _FakeEmbedding:
    def embed_query(self, text):
        self.query = text
        return [0.1] * 8


@pytest.fixture
def wired(monkeypatch):
    emb = _FakeEmbedding()
    client = _FakeClient([
        _Hit(0.9, {"cite_url": "https://x/kampanya", "text": "Kampanya metni",
                   "bank": "Kuveyt Türk", "source_type": "page", "from_vision": False}),
        _Hit(0.8, {"cite_url": "https://x/form.pdf#page=1", "text": "Ücret tablosu",
                   "bank": "Kuveyt Türk", "source_type": "pdf", "from_vision": True}),
    ])
    monkeypatch.setattr(retrieve, "get_embedding", lambda: emb)
    monkeypatch.setattr(retrieve, "get_qdrant_client", lambda: client)
    return emb, client


def test_search_returns_both_a_page_and_a_pdf_ranked_together(wired):
    hits = search("kâr payı oranı")
    assert [h.payload["source_type"] for h in hits] == ["page", "pdf"]
    assert hits[0].cite_url == "https://x/kampanya"


def test_a_result_exposes_the_cite_url_and_from_vision(wired):
    hits = search("ücret")
    assert hits[1].cite_url.endswith("#page=1")
    assert hits[1].from_vision is True        # agent should hedge on this one


def test_the_query_gets_the_qwen_instruction(wired):
    emb, _ = wired
    search("konut kredisi", collection="c")
    assert emb.query.startswith("Instruct:")     # query_text applied


def test_active_only_default_installs_the_expiry_filter(wired):
    _, client = wired
    search("kampanya")
    assert client.last_filter is not None         # a filter was applied


def test_turning_off_active_only_drops_the_filter(wired):
    _, client = wired
    search("kampanya", active_only=False)
    assert client.last_filter is None
