"""One-shot structured agent that prepares a final answer for TTS."""

import json

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from llm import get_llm

from .models import VoiceResponse
from .prompt import NAME


def build_voice_response_agent():
    """Build fresh so a rotated local-model tunnel is never pinned."""
    return create_agent(
        model=get_llm("chat", thinking=False, disable_streaming=True),
        tools=[],
        system_prompt=NAME,
        response_format=ToolStrategy(VoiceResponse),
        name="voice_response_formatter",
    )


def format_voice_response(
    answer: str, *, question: str = "", locale: str = "tr"
) -> VoiceResponse:
    """Adapt one completed answer without sharing the supervisor's state."""
    if not answer.strip():
        raise ValueError("A completed assistant answer is required.")

    language = "Turkish" if locale.lower().startswith("tr") else "English"
    payload = {
        "language": language,
        "user_question": question,
        "assistant_answer": answer,
    }
    result = build_voice_response_agent().invoke(
        {
            "messages": [
                (
                    "user",
                    "Prepare this completed answer for speech synthesis:\n"
                    + json.dumps(payload, ensure_ascii=False),
                )
            ]
        }
    )
    structured = result.get("structured_response")
    if not isinstance(structured, VoiceResponse):
        raise RuntimeError("The voice formatter returned no validated result.")
    return structured
