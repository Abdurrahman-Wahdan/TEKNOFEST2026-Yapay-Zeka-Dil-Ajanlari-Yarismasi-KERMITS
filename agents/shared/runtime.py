"""Typed runtime data injected into main-agent tools."""

from typing import NotRequired, TypedDict


class AgentContext(TypedDict):
    session_id: str
    web_search_enabled: NotRequired[bool]
    #: Who is asking. Present only on the supervisor's live path, where the HTTP
    #: layer knows the signed-in account -- the standalone answer path has no
    #: user, and neither does a bank specialist, which is why this is optional
    #: rather than required.
    #:
    #: Only tools that write something belonging to a person need it
    #: (`create_automation`, `list_automations`). A tool reading it must handle
    #: its absence by refusing in prose, not by raising.
    user_id: NotRequired[str]
    #: Loaded fresh from Postgres for every turn and injected transiently into
    #: model calls, rather than checkpointed as a fake transcript message.
    feedback_notes: NotRequired[str]
