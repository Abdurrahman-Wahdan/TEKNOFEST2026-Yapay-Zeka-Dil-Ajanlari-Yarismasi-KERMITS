"""The overview of the live comparison page, and the cache that keys it.

The sibling of `test_table_overview.py`, and the same approach: no model and no
database. The agent graph is patched to a fake, so what these pin is the key,
the single-flight, the eviction and the two endpoints — everything except
whether Gemma 4 reads a live board sensibly, which needs the vLLM host.
"""

import threading

import pytest
from fastapi.testclient import TestClient

import agents.table_overview.agent as agent_mod
from agents.table_overview import TableOverview
from api import live_overviews as live
from api.main import app

pytestmark = pytest.mark.unit

OUTLINE = (
    '<page-snapshot path="/compare">\n'
    "## Sonuçlar\n| Banka | Taksit |\n| Kuveyt Türk | 1.000 |\n"
    "</page-snapshot>"
)
RESULT = TableOverview(summary="Bir özet.", recommended=[], not_recommended=[], caveat="")


class _Graph:
    """Stands in for the compiled agent; counts how often it was asked."""

    def __init__(self, result=RESULT, block: threading.Event | None = None):
        self.result = result
        self.block = block
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        if self.block is not None:
            self.block.wait(timeout=5)
        return {"structured_response": self.result}


@pytest.fixture(autouse=True)
def _empty_cache():
    """Each test starts with nothing kept and nothing running."""
    with live._guard:
        live._cache.clear()
        live._running.clear()
    yield
    with live._guard:
        live._cache.clear()
        live._running.clear()


@pytest.fixture
def graph(monkeypatch):
    g = _Graph()
    monkeypatch.setattr(agent_mod, "build_table_overview_agent", lambda: g)
    return g


def _settle(digest: str, locale: str = "tr", tries: int = 200) -> None:
    """Wait for the background thread. Polls the same state the client polls."""
    for _ in range(tries):
        if live.cached(digest, locale) is not None:
            return
        threading.Event().wait(0.01)
    raise AssertionError("the generation never finished")


# ----- the key -----


def test_the_key_is_the_page_not_the_comparison():
    # Two runs with different inputs that produced the same board are the same
    # board to summarise; a board that moved is a new one even though nobody
    # touched the form. That is only true if the digest is over the outline.
    assert live.digest(OUTLINE) == live.digest(OUTLINE)
    assert live.digest(OUTLINE) != live.digest(OUTLINE.replace("1.000", "1.001"))


def test_a_language_is_part_of_the_key(graph):
    d = live.digest(OUTLINE)
    live.start(page_text=OUTLINE, page_digest=d, locale="tr")
    _settle(d, "tr")
    # Written in Turkish, so the English reader is a miss rather than a hit.
    assert live.cached(d, "tr") is not None
    assert live.cached(d, "en") is None


def test_a_reworded_prompt_invalidates_what_the_old_one_wrote(monkeypatch, graph):
    d = live.digest(OUTLINE)
    live.start(page_text=OUTLINE, page_digest=d, locale="tr")
    _settle(d)
    assert live.cached(d, "tr") is not None
    # The digest cannot notice a change that happened in the prompt file, which
    # is why the model name carries the prompt version.
    monkeypatch.setattr(live.pool, "model_name", lambda: "someothermodel@v99")
    assert live.cached(d, "tr") is None


# ----- asking twice -----


def test_a_second_request_for_the_same_page_does_not_start_a_second_call(monkeypatch):
    release = threading.Event()
    g = _Graph(block=release)
    monkeypatch.setattr(agent_mod, "build_table_overview_agent", lambda: g)
    d = live.digest(OUTLINE)

    assert live.start(page_text=OUTLINE, page_digest=d, locale="tr") is True
    # The first is still at the model; the second is told so rather than queued.
    assert live.start(page_text=OUTLINE, page_digest=d, locale="tr") is False
    assert live.running(d, "tr") is True
    release.set()
    _settle(d)
    assert g.calls == 1


def test_a_failed_generation_stops_running_and_leaves_nothing(monkeypatch):
    class Broken:
        def invoke(self, payload):
            raise RuntimeError("the host is down")

    monkeypatch.setattr(agent_mod, "build_table_overview_agent", lambda: Broken())
    d = live.digest(OUTLINE)
    live.start(page_text=OUTLINE, page_digest=d, locale="tr")
    for _ in range(200):
        if not live.running(d, "tr"):
            break
        threading.Event().wait(0.01)
    # Neither cached nor running is what the GET reports as `missing`, and that
    # is the only failure signal the card has.
    assert live.running(d, "tr") is False
    assert live.cached(d, "tr") is None


# ----- what is kept -----


def test_the_cache_is_bounded_and_drops_the_oldest(monkeypatch, graph):
    monkeypatch.setattr(live, "_MAX_ENTRIES", 3)
    digests = []
    for n in range(4):
        text = f"{OUTLINE}<!-- {n} -->"
        d = live.digest(text)
        digests.append(d)
        live.start(page_text=text, page_digest=d, locale="tr")
        _settle(d)
    # A tab watching one board must not have it evicted by a burst of one-off
    # comparison runs, so the oldest goes and the rest stay.
    assert live.cached(digests[0], "tr") is None
    assert all(live.cached(d, "tr") is not None for d in digests[1:])


def test_reading_an_entry_keeps_it(monkeypatch, graph):
    monkeypatch.setattr(live, "_MAX_ENTRIES", 2)
    first, second = f"{OUTLINE}<!-- a -->", f"{OUTLINE}<!-- b -->"
    da, db = live.digest(first), live.digest(second)
    live.start(page_text=first, page_digest=da, locale="tr")
    _settle(da)
    live.start(page_text=second, page_digest=db, locale="tr")
    _settle(db)
    # `da` is the oldest by insertion, so reading it has to be what saves it.
    assert live.cached(da, "tr") is not None

    third = f"{OUTLINE}<!-- c -->"
    dc = live.digest(third)
    live.start(page_text=third, page_digest=dc, locale="tr")
    _settle(dc)
    assert live.cached(da, "tr") is not None
    assert live.cached(db, "tr") is None


# ----- over HTTP -----


@pytest.fixture
def client():
    return TestClient(app)


def test_the_post_answers_with_the_digest_to_poll_with(client, graph):
    body = {"locale": "tr", "page": {"text": OUTLINE}}
    started = client.post("/api/compare/overview", json=body).json()
    assert started["status"] == "generating"
    # The browser never hashes anything: the server hands back the key.
    assert started["digest"] == live.digest(OUTLINE)

    _settle(started["digest"])
    state = client.get(
        "/api/compare/overview", params={"digest": started["digest"], "locale": "tr"}
    ).json()
    assert state["status"] == "ready"
    assert state["overview"]["summary"] == "Bir özet."


def test_posting_the_same_page_again_serves_the_cache(client, graph):
    body = {"locale": "tr", "page": {"text": OUTLINE}}
    first = client.post("/api/compare/overview", json=body).json()
    _settle(first["digest"])
    # This is what makes a five-minute refresh over an unmoved board free.
    again = client.post("/api/compare/overview", json=body).json()
    assert again["status"] == "ready"
    assert again["overview"]["summary"] == "Bir özet."
    assert graph.calls == 1


def test_an_overview_without_the_outline_is_refused(client, graph):
    for empty in ("", "   ", None):
        r = client.post("/api/compare/overview", json={"locale": "tr", "page": {"text": empty}})
        assert r.status_code == 422
    assert graph.calls == 0


def test_a_digest_nobody_asked_for_is_missing_not_a_generation(client, graph):
    # The GET never generates: it is called on a timer, and a GET that costs a
    # model call is not safe to retry.
    state = client.get(
        "/api/compare/overview", params={"digest": "0" * 64, "locale": "tr"}
    ).json()
    assert state["status"] == "missing"
    assert graph.calls == 0


def test_an_unknown_locale_is_turkish_on_both_routes(client, graph):
    started = client.post(
        "/api/compare/overview", json={"locale": "fr", "page": {"text": OUTLINE}}
    ).json()
    _settle(started["digest"], "tr")
    state = client.get(
        "/api/compare/overview", params={"digest": started["digest"], "locale": "fr"}
    ).json()
    assert state["status"] == "ready"
    assert state["overview"]["locale"] == "tr"
