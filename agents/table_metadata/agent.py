"""One-shot structured agent that names and describes a saved table."""

import json
from collections.abc import Sequence

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from llm import get_llm

from .models import TableMetadata
from .prompt import NAME


def build_table_metadata_agent():
    """Build a fresh agent so a rotated Gemma tunnel is never pinned here."""
    return create_agent(
        model=get_llm("chat", disable_streaming=True),
        tools=[],
        system_prompt=NAME,
        response_format=ToolStrategy(TableMetadata),
        name="table_metadata",
    )


def generate_table_metadata(
    conversation: Sequence[tuple[str, str]], table: dict
) -> TableMetadata:
    """Generate metadata from the visible transcript and the exact chosen table."""
    messages = [
        ("assistant" if role == "assistant" else "user", content)
        for role, content in conversation
        if content.strip()
    ]
    messages.append(
        (
            "user",
            "Create durable metadata for this exact table:\n"
            + json.dumps(table, ensure_ascii=False, indent=2),
        )
    )
    result = build_table_metadata_agent().invoke({"messages": messages})
    structured = result.get("structured_response")
    if not isinstance(structured, TableMetadata):
        raise RuntimeError("The table metadata agent returned no validated result.")
    return structured
