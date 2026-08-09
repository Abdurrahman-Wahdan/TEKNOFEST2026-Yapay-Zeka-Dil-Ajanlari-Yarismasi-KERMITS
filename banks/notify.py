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

from . import clock
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


def send_report(report, changes: list[dict] | None = None) -> bool:
    """The scheduled audit's message, sent whether or not anything is wrong.

    This is the one exception to the change-only rule above, and it earns it by
    putting the verdict in the first line — the only part anyone reads. A green
    day is one sentence; a bad day leads with what broke. Any state changes are
    carried inside this message rather than sent again separately, so a
    scheduled run is never two notifications.
    """
    changes = changes or []
    generated_at = clock.now()
    when = clock.display(generated_at)
    checked = len(report.results)
    ok = checked - len(report.failures)

    if report.healthy:
        headline = (f"bank audit {when} — {ok}/{checked} ok, "
                    f"{len(report.known_gaps)} known gaps, "
                    f"{report.seconds:.1f}s. All well.")
        lines = [headline]
    else:
        headline = (f"bank audit {when} — {len(report.failures)} DOWN, "
                    f"{ok}/{checked} ok, {report.seconds:.1f}s")
        lines = [headline, ""]
        lines += [
            f"  {failure.bank} {failure.capability}: {failure.reason or failure.detail}"
            for failure in report.failures
        ]
        lines.append("")
        lines.append("These are disabled and shown to users as under maintenance.")

    if changes:
        lines.append("")
        lines.append(summarise(changes))

    text = "\n".join(lines)
    (logger.info if report.healthy else logger.warning)("%s", headline)

    return _post({
        "source": "tf26-bank-audit",
        "text": text,
        "generated_at": clock.stamp(generated_at),
        "healthy": report.healthy,
        "totals": {"checked": checked, "ok": ok, "down": len(report.failures),
                   "known": len(report.known_gaps)},
        "banks": report.per_bank(),
        "failures": [
            {"bank": f.bank, "capability": f.capability, "reason": f.reason}
            for f in report.failures
        ],
        "changes": changes,
    })


def _post(payload: dict) -> bool:
    """Deliver to the webhook, or say why not. Never raises."""
    url = (settings.HEALTH_WEBHOOK_URL or "").strip()
    if not url:
        logger.debug("No HEALTH_WEBHOOK_URL set; logged only")
        return False
    try:
        request("POST", url, headers={"content-type": "application/json"}, json=payload)
        return True
    except Exception as exc:  # noqa: BLE001 - alerting must never break the run
        logger.error("Could not deliver health notification: %s", exc)
        logger.error("Unsent payload: %s", json.dumps(payload, ensure_ascii=False))
        return False


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

    return _post({"source": "tf26-bank-health", "text": text, "changes": changes})
