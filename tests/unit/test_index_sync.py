"""The incremental sync: embed only what changed, delete what's gone, refuse a
run that would wipe the index.

A fake Qdrant client and a fake embedding stand in for the live services, so
these run offline and assert the diff logic exactly.
"""

import json

import pytest

from config.settings import settings
from index import sync

pytestmark = pytest.mark.unit


class _FakePoint:
    def __init__(self, pid, payload):
        self.id, self.payload = pid, payload


class _FakeQdrant:
    """Just enough Qdrant to test scroll/upsert/delete."""

    def __init__(self):
        self.points: dict[str, dict] = {}      # point_id -> payload
        self.embed_calls = 0
        self.upserts = 0
        self.deletes = 0

    def scroll(self, collection_name, with_payload, with_vectors, limit, offset=None):
        items = list(self.points.items())
        return [_FakePoint(pid, {"text_hash": pl.get("text_hash", "")})
                for pid, pl in items], None

    def upsert(self, collection_name, points):
        self.upserts += 1
        for p in points:
            self.points[p.id] = p.payload

    def delete(self, collection_name, points_selector):
        self.deletes += 1
        for pid in points_selector.points:
            self.points.pop(pid, None)

    def create_payload_index(self, *a, **k):
        pass


class _FakeEmbedding:
    def __init__(self, client):
        self._client = client

    def embed_documents(self, texts):
        self._client.embed_calls += len(texts)
        return [[0.0] * settings.EMBEDDING_DIMENSIONS for _ in texts]


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """A fake Qdrant + embedding, and an artifact on disk."""
    monkeypatch.setattr(settings, "CORPUS_ROOT", str(tmp_path))
    client = _FakeQdrant()
    monkeypatch.setattr(sync, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(sync, "ensure_collection", lambda *a, **k: True)
    monkeypatch.setattr(sync, "get_embedding", lambda: _FakeEmbedding(client))

    def write_artifact(docs):
        path = tmp_path / sync.ARTIFACT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(d) for d in docs) + "\n", "utf-8")

    return client, write_artifact


def _doc(doc_id="a1", sections=None, **over):
    d = {
        "doc_id": doc_id, "url": f"https://x.com.tr/{doc_id}", "site": "kuveytturk",
        "bank": "Kuveyt Türk", "source_type": "page", "doc_kind": "product",
        "title": "Ürün", "audience": "bireysel", "lang": "tr", "campaign_end": "",
        "sections": sections if sections is not None else [
            {"heading_path": "H", "anchor": "", "level": 2, "text": "gövde metni",
             "order": 0, "cite_url": f"https://x.com.tr/{doc_id}", "text_hash": "h1"}],
    }
    d.update(over)
    return d


# ----- first index -----

def test_a_first_run_embeds_every_chunk(wired):
    client, write = wired
    write([_doc()])
    report = sync.run()
    assert report.embedded == 1
    assert report.skipped == 0
    assert client.embed_calls == 1
    assert len(client.points) == 1


# ----- the incremental guarantees -----

def test_an_unchanged_chunk_is_not_re_embedded(wired):
    client, write = wired
    write([_doc()])
    sync.run()
    client.embed_calls = 0                         # reset after first index

    report = sync.run()                            # same artifact again
    assert report.embedded == 0
    assert report.skipped == 1
    assert client.embed_calls == 0                 # the whole point
    assert not report.written


def test_a_changed_chunk_re_embeds_only_itself(wired):
    client, write = wired
    write([_doc(sections=[
        {"heading_path": "H", "anchor": "", "level": 2, "text": "eski", "order": 0,
         "cite_url": "https://x.com.tr/a1", "text_hash": "h1"},
        {"heading_path": "H2", "anchor": "", "level": 2, "text": "sabit", "order": 1,
         "cite_url": "https://x.com.tr/a1", "text_hash": "stable"}])])
    sync.run()
    client.embed_calls = 0

    # first section's text changes -> new text_hash; second is untouched
    write([_doc(sections=[
        {"heading_path": "H", "anchor": "", "level": 2, "text": "yeni", "order": 0,
         "cite_url": "https://x.com.tr/a1", "text_hash": "h2"},
        {"heading_path": "H2", "anchor": "", "level": 2, "text": "sabit", "order": 1,
         "cite_url": "https://x.com.tr/a1", "text_hash": "stable"}])])
    report = sync.run()
    assert report.embedded == 1                    # only the changed section
    assert report.skipped == 1
    assert client.embed_calls == 1


def test_a_removed_chunk_is_deleted(wired):
    client, write = wired
    write([_doc(f"a{n}") for n in range(10)])      # 10 docs so one delete is 10%
    sync.run()
    assert len(client.points) == 10

    write([_doc(f"a{n}") for n in range(9)])       # a9 gone, under the 20% gate
    report = sync.run()
    assert report.deleted == 1
    assert len(client.points) == 9


def test_a_second_run_over_the_same_artifact_writes_nothing(wired):
    """The idempotence proof: identical input -> zero embeds, zero writes."""
    client, write = wired
    write([_doc("a1"), _doc("a2")])
    sync.run()
    client.embed_calls = client.upserts = client.deletes = 0

    report = sync.run()
    assert (client.embed_calls, client.upserts, client.deletes) == (0, 0, 0)
    assert not report.written


# ----- the delete gate -----

def test_a_run_that_would_delete_most_of_the_index_refuses(wired, monkeypatch):
    """A truncated artifact must not wipe the index."""
    monkeypatch.setattr(settings, "INDEX_MAX_DELETE_PCT", 20)
    client, write = wired
    write([_doc(f"a{n}") for n in range(10)])
    sync.run()
    assert len(client.points) == 10

    write([_doc("a0")])                            # artifact collapsed to 1 doc
    report = sync.run()
    assert report.gate
    assert not report.healthy
    assert len(client.points) == 10                # nothing deleted


def test_a_small_delete_is_allowed(wired, monkeypatch):
    monkeypatch.setattr(settings, "INDEX_MAX_DELETE_PCT", 50)
    client, write = wired
    write([_doc(f"a{n}") for n in range(10)])
    sync.run()
    write([_doc(f"a{n}") for n in range(9)])       # drop one (10%)
    report = sync.run()
    assert report.deleted == 1
    assert not report.gate


# ----- campaign counting -----

def test_expired_and_active_campaigns_are_counted(wired):
    client, write = wired
    write([
        _doc("c1", doc_kind="campaign", campaign_end="2020-01-01",
             text="eski kampanya", sections=[]),
        _doc("c2", doc_kind="campaign", campaign_end="2099-12-31",
             text="gelecek kampanya", sections=[]),
    ])
    report = sync.run()
    assert report.campaigns_expired == 1
    assert report.campaigns_active == 1


# ----- CLI -----

def test_each_batch_is_upserted_before_the_next_is_embedded(wired, monkeypatch):
    """Live progress: points must appear as it goes, not all at the end. An
    all-at-once upsert meant an hour of work with nothing written and no way to
    see how far along it was."""
    monkeypatch.setattr(settings, "INDEX_EMBED_BATCH", 2)
    client, write = wired
    write([_doc(f"a{n}") for n in range(6)])          # 6 chunks, batch of 2

    seen = []
    original = client.upsert

    def watched(collection_name, points):
        original(collection_name, points)
        seen.append(len(client.points))               # how many are live after each

    client.upsert = watched
    sync.run()
    assert seen == [2, 4, 6], "points must land batch by batch, not once at the end"


def test_a_killed_run_keeps_its_work_and_a_restart_resumes(wired, monkeypatch):
    """The resume property: interrupt mid-way, and the chunks already written are
    skipped on the next run instead of being re-embedded."""
    monkeypatch.setattr(settings, "INDEX_EMBED_BATCH", 2)
    client, write = wired
    write([_doc(f"a{n}") for n in range(6)])

    # simulate a kill after the second batch
    calls = {"n": 0}
    original = client.upsert

    def die_after_two(collection_name, points):
        original(collection_name, points)
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("killed mid-run")

    client.upsert = die_after_two
    with pytest.raises(KeyboardInterrupt):
        sync.run()
    assert len(client.points) == 4                    # first two batches survived

    # restart: the 4 already-written chunks are unchanged, so they are skipped
    client.upsert = original
    client.embed_calls = 0
    report = sync.run()
    assert report.skipped == 4                        # resumed, not redone
    assert report.embedded == 2                       # only the remainder
    assert client.embed_calls == 2


def test_a_missing_artifact_is_a_usage_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CORPUS_ROOT", str(tmp_path))
    assert sync.main(["--quiet"]) == 2


def test_no_write_reports_without_touching_the_store(wired):
    client, write = wired
    write([_doc()])
    report = sync.run(write=False)
    assert report.embedded == 0
    assert client.embed_calls == 0
    assert len(client.points) == 0
