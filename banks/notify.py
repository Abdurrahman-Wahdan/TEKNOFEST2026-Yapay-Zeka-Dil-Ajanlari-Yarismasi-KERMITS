"""Tell someone when a bank changes state.

Only on a change — green to red, or red back to green. A message every morning
saying everything is fine is a message people stop reading, and then they miss
the one that matters.

Logging always happens. The webhook is optional: with no URL configured the run
still logs and still writes the status file, so the tools degrade correctly even
when nobody is listening.
"""

import json
import logging

from config.settings import settings

from .http import request

logger = logging.getLogger(__name__)


def summarise(changes: list[dict]) -> str:
    """One line per change, readable in a log or a chat message."""
    lines = []
    for change in changes:
        if change["to"] == "down":
            lines.append(
                f"DOWN  {change['bank']} {change['capability']}: {change['reason']}"
            )
        else:
            lines.append(f"BACK  {change['bank']} {change['capability']} is answering again")
    return "\n".join(lines)


def send(changes: list[dict]) -> bool:
    """Announce state changes. Returns whether a webhook was delivered.

    Never raises: a failed alert must not fail the health run that produced it,
    because the status file is the thing that actually protects users.
    """
    if not changes:
        return False

    text = summarise(changes)
    for line in text.splitlines():
        logger.warning("%s", line)

    url = (settings.HEALTH_WEBHOOK_URL or "").strip()
    if not url:
        logger.debug("No HEALTH_WEBHOOK_URL set; logged %d change(s) only", len(changes))
        return False

    payload = {
        "source": "tf26-bank-health",
        "text": text,
        "changes": changes,
    }
    try:
        request(
            "POST", url,
            headers={"content-type": "application/json"},
            json=payload,
        )
        logger.info("Notified %d bank state change(s)", len(changes))
        return True
    except Exception as exc:  # noqa: BLE001 - alerting must never break the run
        logger.error("Could not deliver health notification: %s", exc)
        logger.error("Unsent payload: %s", json.dumps(payload, ensure_ascii=False))
        return False
