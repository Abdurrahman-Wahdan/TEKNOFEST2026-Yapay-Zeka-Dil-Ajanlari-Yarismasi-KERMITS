"""Persistent, conversation-aware next-message recommendations."""

from __future__ import annotations

from collections.abc import Sequence

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage

from llm import get_llm
from llm.context import usable_context_window
from llm.factory import resolve_model_key

from ..shared.checkpoints import get_checkpointer
from ..shared.compaction import build_compaction
from .models import ConversationRecommendation
from .prompt import NAME

_MARKER = "tf26_recommendation_for"
_LOCALE = "tf26_recommendation_locale"


def recommendation_thread_id(session_id: str) -> str:
    """The recommendation agent is private and separate from every other agent."""
    return f"{session_id}:recommendation"


def recommendation_compaction():
    """Use the same durable compaction policy as the visible conversation."""
    window = usable_context_window(resolve_model_key("chat"), NAME, [])
    return build_compaction(window, specialist=False), window


def build_recommendation_agent():
    """Compile a fresh graph while its memory remains in the shared checkpointer."""
    compaction, _ = recommendation_compaction()
    return create_agent(
        model=get_llm("chat", disable_streaming=True),
        tools=[],
        system_prompt=NAME,
        response_format=ToolStrategy(ConversationRecommendation),
        checkpointer=get_checkpointer(),
        middleware=[compaction],
        name="conversation_recommendation",
    )


def _message(role: str, content: str):
    return AIMessage(content=content) if role == "assistant" else HumanMessage(content=content)


def _last_processed(messages: Sequence[object]) -> tuple[str | None, str | None]:
    for message in reversed(messages):
        kwargs = getattr(message, "additional_kwargs", {}) or {}
        marker = kwargs.get(_MARKER)
        if marker:
            return str(marker), str(kwargs.get(_LOCALE) or "") or None
    return None, None


def generate_recommendation(
    conversation: Sequence[tuple[str, str, str]], *, session_id: str, locale: str
) -> ConversationRecommendation:
    """Advance one private recommendation thread from the authoritative transcript.

    The database transcript seeds a missing checkpoint in full. Afterwards only
    turns newer than the marker on the prior recommendation are appended. This
    keeps the recommendation agent parallel to the supervisor without copying
    or exposing the supervisor's private tool state.
    """
    visible = [
        (str(message_id), role, content)
        for message_id, role, content in conversation
        if content.strip()
    ]
    if not visible or visible[-1][1] != "assistant":
        raise ValueError("A completed assistant turn is required for a recommendation.")

    latest_id = visible[-1][0]
    config = {"configurable": {"thread_id": recommendation_thread_id(session_id)}}
    agent = build_recommendation_agent()
    state = agent.get_state(config)
    stored = list((state.values or {}).get("messages") or [])
    processed, processed_locale = _last_processed(stored)
    requested_locale = "tr" if locale.lower().startswith("tr") else "en"

    if processed == latest_id and processed_locale == requested_locale:
        existing = (state.values or {}).get("structured_response")
        if isinstance(existing, ConversationRecommendation):
            return existing

    if not stored:
        pending = visible
    elif processed:
        processed_at = next(
            (index for index, item in enumerate(visible) if item[0] == processed),
            -1,
        )
        pending = visible[processed_at + 1 :] if processed_at >= 0 else visible
    else:
        # A legacy or manually rewritten checkpoint has no marker. Re-seed from
        # the stored transcript instead of guessing which turns it remembers.
        pending = visible

    language = "Turkish" if requested_locale == "tr" else "English"
    messages = [_message(role, content) for _id, role, content in pending]
    messages.append(
        HumanMessage(
            content=f"Recommend the next user message now. Write it in {language}.",
            additional_kwargs={_MARKER: latest_id, _LOCALE: requested_locale},
        )
    )
    result = agent.invoke({"messages": messages}, config=config)
    structured = result.get("structured_response")
    if not isinstance(structured, ConversationRecommendation):
        raise RuntimeError("The recommendation agent returned no validated result.")
    return structured
