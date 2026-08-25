"""Stable, compact result envelopes for live bank tools."""

import json
from typing import Any, Callable

from banks import clock


def live_result(
    bank: str,
    tool: str,
    call: Callable[[], Any],
    *,
    source_url: str = "",
    source_title: str = "",
) -> str:
    """Run one provider call and serialize a model-safe live-result envelope."""
    base = {
        "bank": bank,
        "tool": tool,
        # Turkey time, with its offset, because a model repeats a timestamp
        # verbatim. This was UTC ("...Z"), and the visible consequence was a rate
        # fetched at 11:04 presented to a Turkish reader as 08:04 -- the model
        # quoting exactly what it was handed. See `banks/clock.py::stamp_tr`.
        "retrieved_at": clock.stamp_tr(),
        "source_type": "live_endpoint",
    }
    if source_url:
        base["source_url"] = source_url
        base["source_title"] = source_title
    try:
        return json.dumps(
            {**base, "status": "ok", "data": call()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except ValueError as exc:
        return json.dumps(
            {**base, "status": "unavailable", "message": str(exc)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception as exc:  # noqa: BLE001 - endpoint changes must not end an agent turn
        return json.dumps(
            {
                **base,
                "status": "error",
                "message": (
                    f"Live lookup failed unexpectedly ({type(exc).__name__}). "
                    "The bank endpoint may have changed or be unavailable."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
