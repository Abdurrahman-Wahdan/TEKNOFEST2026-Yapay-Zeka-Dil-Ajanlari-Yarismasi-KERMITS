"""Typed runtime data injected into main-agent tools."""

from typing import TypedDict


class AgentContext(TypedDict):
    session_id: str
