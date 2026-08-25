"""The comparison-table directory: the collection layer and the supervisor's tool.

The failures guarded here do not raise. They produce an assistant that either
never links to a table the site already publishes, or links to the wrong one, or
quietly loses the address the moment the pool is rebuilt.
"""

import unicodedata

import pytest

from agents.shared import table_tools
from corpus import tables

pytestmark = pytest.mark.unit


class _FakeClient:
    """Records writes and replays a fixed set of hits."""

    def __init__(self, hits=(), existing=None):
        self._hits = list(hits)
        self._existing = existing
        self.upserts = []
        self.payload_sets = []

    def collection_exists(self, name):
        return True

    def retrieve(self, name, ids, with_payload=None, with_vectors=None):
        if self._existing is None:
            return []
        return [type("P", (), {"payload": self._existing})()]

    def upsert(self, collection_name, points):
        self.upserts.append(points[0])

    def set_payload(self, name, payload, points, wait=None):
        self.payload_sets.append(payload)

    def query_points(self, **kwargs):
        return type("R", (), {"points": self._hits})()


def _hit(score, **payload):
    return type("H", (), {"score": score, "payload": payload})()


@pytest.fixture
def stub(monkeypatch):
    """`_ready` is module state, so it is reset: a previous test's real client
    may have already marked the collection as present."""
    def _install(client):
        monkeypatch.setattr(tables, "_ready", True)
        monkeypatch.setattr(tables, "_shared", lambda: (None, client))
        monkeypatch.setattr(tables, "embed_query", lambda q, task=None: [0.0] * 1024)
        return client
    return _install


# --- point ids ---------------------------------------------------------------
def test_the_point_id_is_uuid5_of_the_id_and_matches_the_live_collection():
    """Measured against the live collection on 2026-08-25: this is the point that
    actually holds the `kredi-kartı` table. A random id would write a second copy
    of every table on every re-index."""
    assert tables.table_point_id("kredi-kartı") == "001fd82f-6b98-5453-a4e3-ebd14088f48f"


def test_the_point_id_does_not_depend_on_the_unicode_form_of_the_id():
    """Turkish ids exist in two byte sequences (NFC in the JSON, NFD on a macOS
    filesystem) and uuid5 hashes bytes. Without normalising, the same table
    becomes two points and only one of them carries the address."""
    nfc = unicodedata.normalize("NFC", "kredi-kartı-doğum-günü")
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    assert tables.table_point_id(nfd) == tables.table_point_id(nfc)


# --- writing -----------------------------------------------------------------
def test_reindexing_keeps_the_ui_url_that_is_already_on_the_point(stub):
    """`upsert` replaces the payload rather than merging into it, and the address
    is written by a different pipeline (`dataprep/stamp_table_urls.py`). A version
    that does not carry it across deletes every link on the next pool rebuild."""
    client = stub(_FakeClient(existing={"ui_url": "/tr/urunler?tablo=x"}))
    tables.index_table("x", "X", "ürün", "s", "d")
    assert client.upserts[0].payload["ui_url"] == "/tr/urunler?tablo=x"


def test_an_explicit_ui_url_wins_over_the_stored_one(stub):
    client = stub(_FakeClient(existing={"ui_url": "/tr/urunler?tablo=old"}))
    tables.index_table("x", "X", "ürün", "s", "d", ui_url="/tr/urunler?tablo=new")
    assert client.upserts[0].payload["ui_url"] == "/tr/urunler?tablo=new"


def test_a_brand_new_table_is_written_without_an_address(stub):
    """Nothing stored yet and none supplied: the field is absent rather than
    empty, so the stamping run is what puts it there."""
    client = stub(_FakeClient(existing=None))
    tables.index_table("x", "X", "ürün", "s", "d")
    assert "ui_url" not in client.upserts[0].payload


def test_topic_and_text_are_written_because_the_search_reads_them(stub):
    """Every one of the 403 live points carries both, and `topic` is what the
    directory prints as the table's name. An earlier version wrote neither, so
    any table it touched lost its name with nothing raising."""
    client = stub(_FakeClient())
    tables.index_table("x", "Konut Finansmanı", "ürün", "murabaha", "Açıklama.")
    payload = client.upserts[0].payload
    assert payload["topic"] == "Konut Finansmanı"
    assert payload["text"].startswith("Konut Finansmanı. Konut Finansmanı. ")


# --- reading -----------------------------------------------------------------
def test_search_returns_the_address_with_the_hit(stub):
    stub(_FakeClient([_hit(0.81, id="konut-finansmanı", topic="Konut Finansmanı",
                           category="ürün", subcategory="murabaha", docstring="D",
                           ui_url="/tr/urunler?tablo=konut-finansman%C4%B1")]))
    (hit,) = tables.search_tables("konut")
    assert hit["ui_url"] == "/tr/urunler?tablo=konut-finansman%C4%B1"
    assert hit["score"] == 0.81


def test_a_point_written_before_topic_existed_falls_back_to_its_slug(stub):
    """A slug is a poor name; a blank one is a hit the model cannot refer to."""
    stub(_FakeClient([_hit(0.7, id="konut-finansmanı", category="ürün",
                           subcategory="s", docstring="D")]))
    (hit,) = tables.search_tables("konut")
    assert hit["topic"] == "konut-finansmanı"
    assert hit["ui_url"] == ""


# --- the supervisor's tool ---------------------------------------------------
def _tool(monkeypatch, hits):
    monkeypatch.setattr(table_tools, "search_tables", lambda *a, **k: hits)
    return table_tools.build_table_directory_tool()


def test_an_off_topic_query_returns_no_table_rather_than_the_top_five(monkeypatch):
    """A vector search always has a top five. Measured on the live collection,
    off-topic queries peak at 0.466 while the intended table never falls below
    0.538, so everything under `MIN_SCORE` is filler the model must not offer."""
    tool = _tool(monkeypatch, [
        {"id": "a", "topic": "Kredi Kartı", "category": "ürün", "subcategory": "s",
         "docstring": "D", "ui_url": "/tr/urunler?tablo=a", "score": 0.38},
    ])
    out = tool.invoke({"query": "kedi maması", "intent": "alakasız"})
    assert "/tr/urunler?tablo=a" not in out
    assert "tablosu yok" in out


def test_a_matching_table_comes_back_with_its_address_verbatim(monkeypatch):
    tool = _tool(monkeypatch, [
        {"id": "a", "topic": "Araç Bakım Kampanyası", "category": "kampanya",
         "subcategory": "alışveriş indirimleri", "docstring": "D",
         "ui_url": "/tr/kampanyalar?tablo=ara%C3%A7", "score": 0.80},
    ])
    out = tool.invoke({"query": "araç bakım", "intent": "tablo var mı"})
    assert "/tr/kampanyalar?tablo=ara%C3%A7" in out
    assert "Araç Bakım Kampanyası" in out


def test_a_table_with_no_address_is_listed_but_offers_no_link(monkeypatch):
    """It is still a real answer to "does one exist". Printing an empty address
    would leave the model nothing to do but construct one."""
    tool = _tool(monkeypatch, [
        {"id": "a", "topic": "T", "category": "ürün", "subcategory": "s",
         "docstring": "D", "ui_url": "", "score": 0.9},
    ])
    out = tool.invoke({"query": "t", "intent": "i"})
    assert "Sitedeki adresi" not in out
    assert "link VERME" in out


def test_the_tool_says_it_carries_no_bank_facts(monkeypatch):
    """The output ships this reminder with every hit, because a docstring
    describing what a table compares reads like something to answer from."""
    tool = _tool(monkeypatch, [
        {"id": "a", "topic": "T", "category": "ürün", "subcategory": "s",
         "docstring": "D", "ui_url": "/tr/urunler?tablo=a", "score": 0.9},
    ])
    out = tool.invoke({"query": "t", "intent": "i"})
    assert "banka bilgisi değil" in out
