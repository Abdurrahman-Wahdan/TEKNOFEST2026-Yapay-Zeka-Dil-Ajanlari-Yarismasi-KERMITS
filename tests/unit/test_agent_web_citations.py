"""The live supervisor exposes web links without exposing specialist internals."""

import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from agents.main import agent as main_agent
from api.agent import _agent_answer, _searchable_text

pytestmark = pytest.mark.unit


def test_citation_audit_folds_turkish_bank_names_to_ascii_aliases():
    assert _searchable_text("Vakıf Katılım ve Dünya Katılım") == (
        "vakif katilim ve dunya katilim"
    )


class _CitationAgent:
    def get_state(self, config):
        return SimpleNamespace(values={})

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
        yield AIMessageChunk(content=(
            "Answer with [online source]"
            "(https://www.vakifkatilim.com.tr/tr/musteri-ol) and "
            "[knowledge source]"
            "(https://www.vakifkatilim.com.tr/tr/bilgi-bankasi)."
        )), {
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
    def get_state(self, config):
        return SimpleNamespace(values={})

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
        yield AIMessageChunk(content="Vakıf Katılım ürünlerini karşılaştırdım."), {
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
        return SimpleNamespace(values={"messages": [ToolMessage(
            name="ask_kuveytturk",
            tool_call_id="old-call",
            content=(
                "Old specialist answer\n\n"
                "TF26_TOOL_EVIDENCE (machine-preserved from actual specialist calls):\n"
                + json.dumps(ledger)
            ),
        )]})

    def stream(self, payload, config, context, stream_mode):
        yield AIMessageChunk(
            content=f"Önceki [kaynak]({self.url})."
        ), {"langgraph_node": "model"}


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
    def get_state(self, config):
        return SimpleNamespace(values={})

    def stream(self, payload, config, context, stream_mode):
        yield AIMessageChunk(content="Merhaba, nasıl yardımcı olabilirim?"), {
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
