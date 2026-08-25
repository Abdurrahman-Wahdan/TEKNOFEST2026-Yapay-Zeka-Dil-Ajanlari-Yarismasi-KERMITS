"""The live supervisor exposes web links without exposing specialist internals."""

import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agents.main import agent as main_agent
from agents.output_guard.models import GuardedOutput
from api import agent as agent_module
from api.agent import _agent_answer, _searchable_text

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def pass_public_output_guard(monkeypatch):
    """Citation tests isolate source selection from the separately tested guard."""
    monkeypatch.setattr(
        agent_module,
        "guard_output",
        lambda text, **kwargs: GuardedOutput(
            text=text, changed=False, checks=[], patches=[], passes=1
        ),
    )


def test_citation_audit_folds_turkish_bank_names_to_ascii_aliases():
    assert _searchable_text("Vakıf Katılım ve Dünya Katılım") == (
        "vakif katilim ve dunya katilim"
    )


class _CitationAgent:
    def __init__(self):
        self.final: AIMessage | None = None

    def get_state(self, config):
        return SimpleNamespace(values={"messages": [self.final] if self.final else []})

    def stream(self, payload, config, context, stream_mode):
        ledger = [
            {
                "tool": "read_bank_source",
                "bank": "vakif",
                "status": "ok",
                "used_sources": [
                    {
                        "title": "Müşteri Ol",
                        "url": "https://www.vakifkatilim.com.tr/tr/musteri-ol",
                        "source_type": "live_web_page",
                        "provenance": "live_web",
                    },
                    {
                        "title": "Unused Page",
                        "url": "https://www.vakifkatilim.com.tr/tr/unused",
                        "source_type": "live_web_page",
                        "provenance": "live_web",
                    },
                ],
            },
            {
                "tool": "search_bank",
                "bank": "vakif",
                "status": "invoked",
                "used_sources": [{
                    "title": "Bilgi Bankası",
                    "url": "https://www.vakifkatilim.com.tr/tr/bilgi-bankasi",
                    "source_type": "indexed_document",
                    "provenance": "knowledge_base",
                }],
            },
        ]
        handoff = (
            "Specialist answer\n\n"
            "TF26_TOOL_EVIDENCE (machine-preserved from actual specialist calls):\n"
            + json.dumps(ledger)
        )
        # Duplicate handoffs are possible when nested callbacks and the public
        # delegation message both surface. The API must publish one link.
        yield ToolMessage(
            name="ask_vakif", tool_call_id="call-1", content=handoff
        ), {"langgraph_node": "tools"}
        yield ToolMessage(
            name="ask_vakif", tool_call_id="call-2", content=handoff
        ), {"langgraph_node": "tools"}
        answer = (
            "Answer with [online source]"
            "(https://www.vakifkatilim.com.tr/tr/musteri-ol) and "
            "[knowledge source]"
            "(https://www.vakifkatilim.com.tr/tr/bilgi-bankasi)."
        )
        self.final = AIMessage(content=answer, id="citation-answer")
        yield AIMessageChunk(content=answer), {
            "langgraph_node": "model"
        }


def test_agent_stream_emits_one_clickable_citation_per_web_url(monkeypatch):
    monkeypatch.setattr(main_agent, "build_main_agent", lambda **kwargs: _CitationAgent())

    events = list(_agent_answer(
        "question",
        history=None,
        context=None,
        captures=None,
        tool_results=None,
        session_id=uuid.uuid4(),
        web_search=True,
    ))

    citations = [event.citation for event in events if event.type == "citation"]
    assert len(citations) == 2
    assert {
        (citation.cite_url, citation.source_type, citation.doc_kind)
        for citation in citations
    } == {
        (
            "https://www.vakifkatilim.com.tr/tr/musteri-ol",
            "live_web_page",
            "web",
        ),
        (
            "https://www.vakifkatilim.com.tr/tr/bilgi-bankasi",
            "indexed_document",
            "knowledge_base",
        ),
    }
    assert all("unused" not in citation.cite_url for citation in citations)
    assert [event.text for event in events if event.type == "token"] == [
        (
            "Answer with [online source]"
            "(https://www.vakifkatilim.com.tr/tr/musteri-ol) and "
            "[knowledge source]"
            "(https://www.vakifkatilim.com.tr/tr/bilgi-bankasi)."
        )
    ]


class _UncitedEvidenceAgent:
    def __init__(self):
        self.final: AIMessage | None = None

    def get_state(self, config):
        return SimpleNamespace(values={"messages": [self.final] if self.final else []})

    def stream(self, payload, config, context, stream_mode):
        ledger = [
            {
                "tool": "search_bank_web",
                "bank": "vakif",
                "used_sources": [{
                    "title": "Canlı ürün sayfası",
                    "url": "https://www.vakifkatilim.com.tr/tr/canli-urun",
                    "source_type": "web_search",
                    "provenance": "live_web",
                }],
            },
            {
                "tool": "search_bank",
                "bank": "vakif",
                "used_sources": [{
                    "title": "İndeksli ürün belgesi",
                    "url": "https://www.vakifkatilim.com.tr/tr/urun-belgesi.pdf",
                    "source_type": "indexed_document",
                    "provenance": "knowledge_base",
                }],
            },
        ]
        yield ToolMessage(
            name="ask_vakif",
            tool_call_id="call-1",
            content=(
                "Specialist answer\n\n"
                "TF26_TOOL_EVIDENCE (machine-preserved from actual specialist calls):\n"
                + json.dumps(ledger)
            ),
        ), {"langgraph_node": "tools"}
        answer = "Vakıf Katılım ürünlerini karşılaştırdım."
        self.final = AIMessage(content=answer, id="uncited-answer")
        yield AIMessageChunk(content=answer), {
            "langgraph_node": "model"
        }


def test_evidence_bearing_answer_gets_audited_sources_when_model_drops_links(
    monkeypatch,
):
    monkeypatch.setattr(
        main_agent, "build_main_agent", lambda **kwargs: _UncitedEvidenceAgent()
    )

    events = list(_agent_answer(
        "question",
        history=None,
        context=None,
        captures=None,
        tool_results=None,
        session_id=uuid.uuid4(),
        web_search=True,
    ))

    citations = [event.citation for event in events if event.type == "citation"]
    assert {(citation.cite_url, citation.doc_kind) for citation in citations} == {
        ("https://www.vakifkatilim.com.tr/tr/canli-urun", "web"),
        ("https://www.vakifkatilim.com.tr/tr/urun-belgesi.pdf", "knowledge_base"),
    }


class _HistoricalCitationAgent:
    url = "https://www.kuveytturk.com.tr/tr/gecmis-kaynak"

    def __init__(self):
        self.final: AIMessage | None = None

    def get_state(self, config):
        ledger = [{
            "tool": "search_bank",
            "bank": "kuveytturk",
            "used_sources": [{
                "title": "Geçmiş kaynak",
                "url": self.url,
                "source_type": "indexed_document",
                "provenance": "knowledge_base",
            }],
        }]
        messages = [ToolMessage(
            name="ask_kuveytturk",
            tool_call_id="old-call",
            content=(
                "Old specialist answer\n\n"
                "TF26_TOOL_EVIDENCE (machine-preserved from actual specialist calls):\n"
                + json.dumps(ledger)
            ),
        )]
        if self.final:
            messages.append(self.final)
        return SimpleNamespace(values={"messages": messages})

    def stream(self, payload, config, context, stream_mode):
        answer = f"Önceki [kaynak]({self.url})."
        self.final = AIMessage(content=answer, id="historical-answer")
        yield AIMessageChunk(content=answer), {"langgraph_node": "model"}


def test_followup_can_render_a_citation_from_checkpointed_evidence(monkeypatch):
    monkeypatch.setattr(
        main_agent, "build_main_agent", lambda **kwargs: _HistoricalCitationAgent()
    )

    events = list(_agent_answer(
        "kaynağı tekrar göster",
        history=None,
        context=None,
        captures=None,
        tool_results=None,
        session_id=uuid.uuid4(),
    ))

    citations = [event.citation for event in events if event.type == "citation"]
    assert [citation.cite_url for citation in citations] == [
        _HistoricalCitationAgent.url
    ]


class _PlainConversationAgent:
    def __init__(self):
        self.final: AIMessage | None = None

    def get_state(self, config):
        return SimpleNamespace(values={"messages": [self.final] if self.final else []})

    def stream(self, payload, config, context, stream_mode):
        answer = "Merhaba, nasıl yardımcı olabilirim?"
        self.final = AIMessage(content=answer, id="plain-answer")
        yield AIMessageChunk(content=answer), {
            "langgraph_node": "model"
        }


def test_plain_conversation_does_not_receive_citations(monkeypatch):
    monkeypatch.setattr(
        main_agent, "build_main_agent", lambda **kwargs: _PlainConversationAgent()
    )

    events = list(_agent_answer(
        "merhaba",
        history=None,
        context=None,
        captures=None,
        tool_results=None,
        session_id=uuid.uuid4(),
    ))

    assert not [event for event in events if event.type == "citation"]


# --- our own pages, listed apart from the evidence ----------------------------
def test_a_linked_comparison_table_becomes_its_own_kind_of_source():
    """The assistant offers a table page; the UI lists it under its own heading.

    Read out of the finished prose rather than the tool-evidence ledger, because
    that is what it is: somewhere to go next, not support for a claim.
    """
    from api.agent import site_table_sources

    answer = (
        "Kuveyt Türk %10 indirim sunuyor.\n\n"
        "Daha detaylı karşılaştırma için: "
        "[araç bakım ve onarım indirimi kampanyası]"
        "(/tr/kampanyalar?tablo=ara%C3%A7-bak%C4%B1m-ve-onar%C4%B1m-indirimi-kampanyas%C4%B1)"
    )
    (source,) = site_table_sources(answer)
    assert source["url"] == (
        "/tr/kampanyalar?tablo=ara%C3%A7-bak%C4%B1m-ve-onar%C4%B1m-indirimi-kampanyas%C4%B1")
    # The pool's own name for the table, not the model's link text: the card and
    # the page it opens cannot then disagree.
    assert source["title"] == "araç bakım ve onarım indirimi kampanyası"


def test_an_invented_table_slug_is_dropped_rather_than_shown():
    """A hallucinated slug produces a perfectly well-formed address. Resolving it
    against the pool is the only thing that tells the two apart, and a card
    linking to a table that does not exist is worse than no card."""
    from api.agent import site_table_sources

    assert site_table_sources(
        "Bak: [uydurma tablo](/tr/urunler?tablo=boyle-bir-tablo-asla-yok)") == []


def test_external_links_and_other_app_pages_are_not_table_sources():
    from api.agent import site_table_sources

    assert site_table_sources("[banka](https://www.kuveytturk.com.tr/x)") == []
    assert site_table_sources("[profil](/tr/profile)") == []
    assert site_table_sources("[liste](/tr/kampanyalar)") == []


def test_the_same_table_linked_twice_is_listed_once():
    from api.agent import site_table_sources

    link = "[t](/tr/urunler?tablo=kredi-kart%C4%B1)"
    assert len(site_table_sources(f"{link} ... {link}")) == 1


def test_a_link_written_with_commonmark_whitespace_is_still_found():
    """Observed from the live model on 2026-08-25:
    `[Konut Finansmanı]( /tr/urunler?tablo=konut-finansman%C4%B1)`.

    CommonMark permits the padding and the renderer honours it, so the link works
    in the prose. A parser that requires the path to touch the paren produces a
    working link and no source card -- the feature looking half-built rather than
    the regex being wrong.
    """
    from api.agent import site_table_sources

    for written in (
        "[Konut Finansmanı]( /tr/urunler?tablo=konut-finansman%C4%B1)",
        "[Konut Finansmanı](/tr/urunler?tablo=konut-finansman%C4%B1 )",
        "[Konut Finansmanı](  /tr/urunler?tablo=konut-finansman%C4%B1  )",
        "[Konut Finansmanı](</tr/urunler?tablo=konut-finansman%C4%B1>)",
    ):
        (source,) = site_table_sources(written)
        assert source["url"] == "/tr/urunler?tablo=konut-finansman%C4%B1", written
        assert source["title"] == "Konut Finansmanı"
