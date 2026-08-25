"""Stable, compact result envelopes for live bank tools."""

import json
from datetime import UTC, datetime
from typing import Any, Callable


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
        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
