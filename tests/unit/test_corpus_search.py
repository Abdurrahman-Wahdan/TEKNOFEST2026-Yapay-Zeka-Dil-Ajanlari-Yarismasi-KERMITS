"""The bank-scoped corpus tools: what they filter, what they return whole, and
what they refuse.

The Qdrant client is faked rather than mocked call-by-call, and the fake really
evaluates the filters the tools build. That is the point of the file: every
guarantee here -- a specialist cannot see another bank, an expanded chunk cannot
cross into another document, a `gorsel` point cannot be spliced into a sentence
-- is a filter, so a test that asserted on filter *objects* would pass while the
filter did nothing.
"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from qdrant_client import models

from corpus import search
from corpus.sites import SITES

pytestmark = pytest.mark.unit


# --- the one mapping that fails silently ------------------------------------

def test_corpus_slug_matches_the_crawler_that_wrote_the_documents():
    """`Site.corpus_slug` must be the name the store actually holds.

    Checked against the crawl engines, which are the code that produced the
    directories the embedder took bank names from. A drift here does not raise:
    a Qdrant filter on a name nothing carries matches nothing, and the
    specialist is told its bank has published nothing at all.
    """
    engines = Path("dataprep/crawl/engines")
    declared = {}
    for module in sorted(engines.glob("*.py")):
        if module.stem == "__init__":
            continue
        for line in module.read_text(encoding="utf-8").splitlines():
            if line.startswith("SLUG"):
                declared[module.stem] = line.split("=", 1)[1].strip().strip("'\"")
                break

    assert {site.corpus_slug for site in SITES} == set(declared)
    for stem, slug in declared.items():
        assert stem == slug, f"{stem}.py declares SLUG={slug!r}"


def test_the_corpus_slug_is_not_the_provider_slug_for_most_banks():
    """Named so the difference cannot be 'simplified' away later."""
    differing = {s.slug: s.corpus_slug for s in SITES if s.slug != s.corpus_slug}
    assert differing == {
        "vakif": "vakifkatilim", "emlak": "emlakkatilim", "dunya": "dunyakatilim",
        "ziraat": "ziraatkatilim", "hayat": "hayatfinans", "tom": "tombank",
        "adil": "adilkatilim",
    }


# --- a Qdrant stand-in that honours the filters -----------------------------

def _value_at(payload: dict, key: str):
    node = payload
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _matches(point, condition) -> bool:
    if isinstance(condition, models.Filter):
        if condition.must and not all(_matches(point, c) for c in condition.must):
            return False
        if condition.should and not any(_matches(point, c) for c in condition.should):
            return False
        return True
    if isinstance(condition, models.HasIdCondition):
        return point.id in condition.has_id
    value = _value_at(point.payload, condition.key)
    if condition.match is not None:
        return value == condition.match.value
    if condition.range is not None:
        if value is None:
            return False
        lo, hi = condition.range.gte, condition.range.lte
        return (lo is None or value >= lo) and (hi is None or value <= hi)
    raise AssertionError(f"unsupported condition {condition!r}")


class _Point:
    def __init__(self, pid, payload):
        self.id, self.payload, self.score = pid, payload, 1.0


class FakeQdrant:
    def __init__(self, points):
        self.points = points

    def _hits(self, flt):
        return [p for p in self.points if flt is None or _matches(p, flt)]

    def query_points(self, *, collection_name, query, query_filter=None,
                     limit=10, offset=0, with_payload=True):
        found = self._hits(query_filter)[offset or 0:][:limit]
        return type("R", (), {"points": found})()

    def scroll(self, *, collection_name, scroll_filter=None, limit=10, offset=None,
               with_payload=True):
        return self._hits(scroll_filter)[:limit], None


def _point(pid, bank, url, index, text, kind="metin", **meta):
    return _Point(pid, {"page_content": text,
                        "metadata": {"bank": bank, "url": url, "type": kind,
                                     "chunk_index": index, **meta}})


LONG = "A" * 9000
CORPUS = [
    _point("v0", "vakifkatilim", "https://v.example/konut", 0, LONG),
    _point("v1", "vakifkatilim", "https://v.example/konut", 1, "devamı burada"),
    _point("v2", "vakifkatilim", "https://v.example/konut", 2, "üçüncü parça"),
    _point("vg", "vakifkatilim", "https://v.example/konut", 0, "afiş yazısı",
           kind="gorsel", gorsel_kaynak="https://v.example/afis.png"),
    _point("vsolo", "vakifkatilim", "https://v.example/tek", 0, "tek parçalık sayfa"),
    _point("vold", "vakifkatilim", "https://v.example/biten", 0, "geçmiş kampanya",
           gecerlilik_bitis="2020-01-01"),
    _point("a0", "albaraka", "https://a.example/konut", 0, "başka bankanın metni"),
]


@pytest.fixture
def corpus(monkeypatch):
    client = FakeQdrant(CORPUS)
    monkeypatch.setattr(search, "_shared", lambda: (None, client))
    monkeypatch.setattr(search, "embed_query", lambda q, task=None: [0.0] * 1024)
    return client


def tools(bank="vakifkatilim"):
    marked, discarded = set(), set()
    found = {t.name: t for t in search.build_bank_retrieval_tools(bank, marked, discarded)}
    return found, marked, discarded


# --- search_bank ------------------------------------------------------------

def test_a_long_chunk_comes_back_whole(corpus):
    found, _, _ = tools()
    out = found["search_bank"].invoke({"query": "konut", "intent": "konut finansmanı"})
    assert LONG in out, "the chunk was truncated"


def test_search_never_leaves_its_own_bank(corpus):
    found, _, _ = tools()
    out = found["search_bank"].invoke({"query": "konut", "intent": "konut"})
    assert "başka bankanın metni" not in out
    assert "point_id=a0" not in out


def test_an_expired_campaign_is_not_shown(corpus):
    found, _, _ = tools()
    out = found["search_bank"].invoke({"query": "kampanya", "intent": "kampanya"})
    assert "geçmiş kampanya" not in out


def test_a_result_says_when_its_document_has_more_pieces(corpus):
    found, _, _ = tools()
    out = found["search_bank"].invoke({"query": "konut", "intent": "konut"})
    assert "parça=0/2" in out          # three text chunks, indexes 0..2
    assert "parça=0/0" not in out      # the single-chunk page says nothing


def test_repeating_the_same_query_is_refused_rather_than_answered_again(corpus):
    found, _, _ = tools()
    args = {"query": "konut", "intent": "konut"}
    for _ in range(search.REPEAT_LIMIT):
        assert "point_id=" in found["search_bank"].invoke(dict(args))
    out = found["search_bank"].invoke(dict(args))
    assert "point_id=" not in out
    assert "FARKLI bir sorgu" in out


def test_paging_the_same_query_stops_at_the_depth_limit(corpus):
    found, _, _ = tools()
    args = {"query": "konut", "intent": "konut", "next": True}
    for _ in range(search.NEXT_DEPTH_LIMIT):
        found["search_bank"].invoke(dict(args))
    assert "point_id=" not in found["search_bank"].invoke(dict(args))


def test_marking_inside_the_search_call_records_the_decision(corpus):
    found, marked, discarded = tools()
    found["search_bank"].invoke(
        {"query": "konut", "intent": "konut", "useful": ["v0"], "not_useful": ["v2"]})
    assert marked == {"v0"} and discarded == {"v2"}


# --- expand_chunk -----------------------------------------------------------

def test_expanding_returns_the_neighbours_in_order(corpus):
    found, _, _ = tools()
    out = found["expand_chunk"].invoke({"point_id": "v1", "before": 1, "after": 1})
    assert out.index("point_id=v0") < out.index("point_id=v1") < out.index("point_id=v2")


def test_expanding_at_the_start_does_not_run_off_the_edge(corpus):
    found, _, _ = tools()
    out = found["expand_chunk"].invoke({"point_id": "v0", "before": 3, "after": 1})
    assert "point_id=v0" in out and "point_id=v1" in out
    assert "point_id=v2" not in out


def test_expanding_never_crosses_into_another_document_or_another_type(corpus):
    found, _, _ = tools()
    out = found["expand_chunk"].invoke({"point_id": "v0", "before": 5, "after": 5})
    assert "afiş yazısı" not in out, "an image caption was spliced into the text"
    assert "tek parçalık sayfa" not in out
    assert "başka bankanın metni" not in out


def test_expanding_reports_how_much_of_the_document_is_in_view(corpus):
    found, _, _ = tools()
    out = found["expand_chunk"].invoke({"point_id": "v2", "before": 1, "after": 1})
    assert "Bu belgede 0..2 arası parça var" in out
    assert "1..2 arasını görüyorsun" in out
    assert "before/after" in out

    whole = found["expand_chunk"].invoke({"point_id": "v1", "before": 1, "after": 1})
    assert "Belgenin tamamını görüyorsun" in whole


def test_another_banks_point_id_resolves_to_nothing(corpus):
    found, _, _ = tools()
    out = found["expand_chunk"].invoke({"point_id": "a0"})
    assert "başka bankanın metni" not in out
    assert "bulunamadı" in out


def test_an_oversized_span_returns_no_content_at_all(corpus, monkeypatch):
    monkeypatch.setattr(search, "MAX_TOOL_CHARS", 100)
    found, _, _ = tools()
    out = found["expand_chunk"].invoke({"point_id": "v0", "before": 0, "after": 2})
    assert LONG not in out and "devamı burada" not in out
    assert "kırpılmadı" in out and "0..2" in out


# --- read_full_page ---------------------------------------------------------

def test_the_full_page_tool_finds_pages_by_the_key_the_store_actually_uses(corpus):
    found, _, _ = tools()
    out = found["read_full_page"].invoke({"url": "https://v.example/konut"})
    assert "devamı burada" in out and "üçüncü parça" in out


def test_the_full_page_tool_will_not_read_another_banks_page(corpus):
    found, _, _ = tools()
    out = found["read_full_page"].invoke({"url": "https://a.example/konut"})
    assert "bulunamadı" in out


def test_an_oversized_page_points_at_expand_chunk_instead_of_truncating(corpus, monkeypatch):
    monkeypatch.setattr(search, "MAX_TOOL_CHARS", 100)
    found, _, _ = tools()
    out = found["read_full_page"].invoke({"url": "https://v.example/konut"})
    assert LONG not in out
    assert "expand_chunk" in out


# --- pruning ----------------------------------------------------------------

SEARCH_OUT = ("[1] point_id=v0 url=u\nilk parça"
              "\n\n[2] point_id=v1 url=u\nikinci parça")


def test_pruning_removes_only_the_named_entry():
    assert search.prune_entries(SEARCH_OUT, {"v1"}) == "[1] point_id=v0 url=u\nilk parça"
    assert search.prune_entries(SEARCH_OUT, {"nothing"}) is None


def test_pruning_everything_leaves_a_note_rather_than_an_empty_message():
    assert "sildi" in search.prune_entries(SEARCH_OUT, {"v0", "v1"})


def test_a_horizontal_rule_inside_a_chunk_does_not_split_it():
    """Bank pages are markdown and 2.1% of stored chunks contain a `---` rule.

    Splitting results on a separator string meant those chunks were cut in two:
    only the half carrying `point_id=` was recognised, so discarding a result
    left its tail behind in the thread as an unattributed fragment, and keeping
    one could still drop half of it. Entries are delimited by their own header
    line now, so the body can contain anything.
    """
    body = "üst yarı\n\n---\n\n## Başlık\nalt yarı"
    content = (f"[1] point_id=v0 url=u\n{body}"
               f"\n\n[2] point_id=v1 url=u\nikinci parça")

    assert search.prune_entries(content, {"v1"}) == f"[1] point_id=v0 url=u\n{body}"

    dropped = search.prune_entries(content, {"v0"})
    assert dropped == "[2] point_id=v1 url=u\nikinci parça"
    assert "alt yarı" not in dropped, "the discarded chunk left a fragment behind"


def test_text_before_the_first_entry_is_never_dropped():
    """The marking note and a document summary frame the answer; they are not
    results, so discarding every result must not take them with it."""
    content = ("Bu belgede 0..9 arası parça var.\n\n"
               "[1] point_id=v0 url=u\nilk parça")
    pruned = search.prune_entries(content, {"v0"})
    assert pruned.startswith("Bu belgede 0..9 arası parça var.")
    assert "ilk parça" not in pruned


def test_only_retrieval_output_can_be_pruned():
    """A live-quote envelope that happens to contain `point_id=` is untouchable."""
    messages = [
        AIMessage(content="", tool_calls=[
            {"name": "search_bank", "args": {}, "id": "t1"},
            {"name": "finance_quote", "args": {}, "id": "t2"},
        ]),
        ToolMessage(content=SEARCH_OUT, tool_call_id="t1", id="m1"),
        ToolMessage(content='{"note":"point_id=v1"}', tool_call_id="t2", id="m2"),
        HumanMessage(content="point_id=v1", id="m3"),
    ]
    pruned = search.prune_messages(messages, {"v1"})
    assert [m.id for m in pruned] == ["m1"]
    assert "ikinci parça" not in pruned[0].content


def test_a_tool_message_without_a_name_is_traced_back_to_its_call():
    caller = AIMessage(content="", tool_calls=[{"name": "expand_chunk", "args": {}, "id": "t9"}])
    message = ToolMessage(content=SEARCH_OUT, tool_call_id="t9", id="m9")
    assert search.tool_name_of(message, [caller]) == "expand_chunk"


# --- telling the model a result is cut ---------------------------------------

@pytest.mark.parametrize("tail, cut", [
    ("## 15. Ortak Yatırımlar ve Vadeli İşlemler", True),   # heading, body not started
    ("### 13.1. Genel Uygulama", True),
    ("| ADANA | CEYHAN | ÖZ KUYUMCULUK |", True),           # unterminated table row
    ("Müşteri bu tutarı ödemekle yükümlü olup", True),      # sentence stops
    ("Müşteri bu tutarı ödemekle yükümlüdür.", False),
    ("Şartlar aşağıdaki gibidir:", False),
])
def test_a_chunk_that_stops_before_its_section_does_is_flagged(tail, cut):
    assert search._ends_cut(f"önceki metin\n\n{tail}") is cut


def test_the_cut_flag_rides_on_the_result_itself_not_just_the_prompt():
    """A heading is a complete line, so "this looks cut off" is not something the
    model can read off the text -- measured: shown a chunk ending on
    `## 15. ...` with nothing under it, it reported that the bank publishes no
    such section and answered from other documents instead. The tool knows the
    chunk has a successor and knows it ends on a heading, so it says so next to
    the evidence rather than hoping a system-prompt rule is recalled."""
    meta = {"url": "u", "chunk_index": 6, "type": "metin"}
    entry = search._entry(1, "p1", meta, "gövde\n\n## 15. Bir Bölüm Başlığı", total=19)
    assert "parça=6/18" in entry
    assert "KESİK=devamı_sonraki_parçada" in entry


def test_the_last_chunk_is_never_flagged_as_cut():
    """Nothing follows it, so there is nothing to widen into."""
    meta = {"url": "u", "chunk_index": 18, "type": "metin"}
    entry = search._entry(1, "p1", meta, "gövde\n\n## Son Başlık", total=19)
    assert "KESİK" not in entry


def test_a_single_chunk_document_is_never_flagged():
    meta = {"url": "u", "chunk_index": 0, "type": "metin"}
    entry = search._entry(1, "p1", meta, "gövde\n\n## Başlık", total=1)
    assert "KESİK" not in entry and "parça=" not in entry
