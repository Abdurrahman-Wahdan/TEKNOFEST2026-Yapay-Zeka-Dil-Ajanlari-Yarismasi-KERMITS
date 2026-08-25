"""Reading a model's context window from the server that serves it.

No network — every probe here is answered by a stub. The live counterpart is
tests/integration/test_compaction_live.py.
"""

import httpx
import pytest

from llm import context as ctx
from llm.context import (
    ContextWindowUnavailable,
    fixed_overhead,
    reported_context_window,
    usable_context_window,
)

pytestmark = pytest.mark.unit


def _card(model_id: str, window: int | None) -> dict:
    card: dict = {"id": model_id, "object": "model"}
    if window is not None:
        card["max_model_len"] = window
    return card


def _serve(monkeypatch, cards: list[dict], calls: list | None = None):
    """Answer any /v1/models probe with these cards."""

    def get(url, **kwargs):
        if calls is not None:
            calls.append(url)
        return httpx.Response(
            200,
            json={"object": "list", "data": cards},
            # raise_for_status() needs the request, and the probe calls it.
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ctx.httpx, "get", get)


def test_window_is_read_from_the_server(monkeypatch):
    """The number comes from the host, not from MODELS."""
    _serve(monkeypatch, [_card("google/gemma-4-31B-it", 131072)])
    assert reported_context_window("gemma") == 131072


def test_the_hardcoded_constant_is_not_used_as_a_fallback(monkeypatch):
    """A silent fallback would reintroduce the stale constant this replaces."""

    def boom(url, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(ctx.httpx, "get", boom)
    monkeypatch.setattr(ctx.tunnel, "refresh_after_failure", lambda *a, **k: False)
    with pytest.raises(ContextWindowUnavailable):
        reported_context_window("gemma")


def test_the_probe_is_cached_per_url(monkeypatch):
    """One HTTP call per model per tunnel, not one per agent build."""
    calls: list[str] = []
    _serve(monkeypatch, [_card("google/gemma-4-31B-it", 131072)], calls)
    reported_context_window("gemma")
    reported_context_window("gemma")
    assert len(calls) == 1


def test_a_rotated_tunnel_is_probed_again(monkeypatch):
    """The cache key includes the base URL, so a new host is asked afresh."""
    calls: list[str] = []
    _serve(monkeypatch, [_card("google/gemma-4-31B-it", 131072)], calls)
    reported_context_window("gemma")
    monkeypatch.setattr(ctx.settings, "VLLM_BASE_URL", "https://moved.example")
    reported_context_window("gemma")
    assert len(calls) == 2


def test_the_card_is_matched_by_id(monkeypatch):
    """A route serving two models must not hand back whichever is listed first."""
    _serve(monkeypatch, [
        _card("some/other-model", 8192),
        _card("google/gemma-4-31B-it", 131072),
    ])
    assert reported_context_window("gemma") == 131072


def test_a_model_that_is_not_served_is_an_error(monkeypatch):
    _serve(monkeypatch, [_card("some/other-model", 8192)])
    with pytest.raises(ContextWindowUnavailable, match="not google/gemma"):
        reported_context_window("gemma")


def test_a_card_without_a_window_is_an_error(monkeypatch):
    """A proxy that drops max_model_len must not read as a zero-token window."""
    _serve(monkeypatch, [_card("google/gemma-4-31B-it", None)])
    with pytest.raises(ContextWindowUnavailable, match="max_model_len"):
        reported_context_window("gemma")


def test_a_failed_probe_refreshes_the_tunnel_and_retries(monkeypatch):
    """The first request after a rotation is the one that discovers it."""
    attempts: list[str] = []

    def get(url, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            raise httpx.ConnectError("stale tunnel")
        return httpx.Response(
            200,
            json={"object": "list", "data": [_card("google/gemma-4-31B-it", 131072)]},
            request=httpx.Request("GET", url),
        )

    def refresh(failed):
        monkeypatch.setattr(ctx.settings, "VLLM_BASE_URL", "https://fresh.example")
        return True

    monkeypatch.setattr(ctx.httpx, "get", get)
    monkeypatch.setattr(ctx.tunnel, "refresh_after_failure", refresh)
    assert reported_context_window("gemma") == 131072
    assert len(attempts) == 2


# ----- the usable window -------------------------------------------------


def test_overhead_grows_with_tools():
    """Tool schemas are re-sent on every call, so they are part of the floor."""
    from agents.shared.agent_tools import build_specialist_tools

    tools = build_specialist_tools()
    assert fixed_overhead("prompt", tools) > fixed_overhead("prompt", [])


def test_usable_window_is_the_reported_window_less_the_floor(monkeypatch):
    _serve(monkeypatch, [_card("google/gemma-4-31B-it", 131072)])
    overhead = fixed_overhead("a system prompt", [])
    assert usable_context_window("gemma", "a system prompt", []) == 131072 - overhead


def test_a_window_with_no_room_left_is_an_error(monkeypatch):
    """Better to say so than to hand back a negative budget."""
    _serve(monkeypatch, [_card("google/gemma-4-31B-it", 8)])
    with pytest.raises(ContextWindowUnavailable, match="leaving nothing"):
        usable_context_window("gemma", "a system prompt long enough to exceed it", [])
