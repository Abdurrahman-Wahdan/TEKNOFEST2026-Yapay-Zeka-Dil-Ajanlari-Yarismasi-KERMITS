"""Typed runtime data injected into main-agent tools."""

from typing import NotRequired, TypedDict


class AgentContext(TypedDict):
    session_id: str
    web_search_enabled: NotRequired[bool]
