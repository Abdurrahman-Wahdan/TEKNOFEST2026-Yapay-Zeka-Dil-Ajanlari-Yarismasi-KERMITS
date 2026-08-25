"""Claiming a due automation, and turning it into one report.

The two halves are separate on purpose. Claiming touches the database and
nothing else; running touches the model and nothing else. So the schedule
arithmetic and the claim semantics -- the parts that break quietly -- are
testable without a language model, and the run is testable with a scripted one.

`answer` is injected rather than imported at call time so a test can hand this a
generator of its own. The real one is `api.agent.answer`, imported lazily inside
`_default_answer` because importing it pulls in LangGraph and ten bank clients.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Iterator

from sqlalchemy import select

from ..db.base import utcnow
from ..db.models import Automation, AutomationReport
from ..db.session import session_scope
from .notifications import hub, report_event
from .schedule import describe, next_run

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class Due:
    """One claimed automation, as plain values.

    Not the ORM row. The claim commits and closes its session before the run
    starts -- a run holds the model for minutes, and holding a database
    transaction open for that long is how a connection pool runs dry -- so
    everything the run needs has to have been copied out by then.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    prompt: str
    web_search: bool
    #: Where `next_run_at` was moved to. Logged, so a report and the next run it
    #: implies can be read off one line.
    next_run_at: datetime


@dataclass
class RunResult:
    """What one run produced. Written whether it succeeded or not."""

    body: str = ""
    citations: list[dict] = field(default_factory=list)
    status: str = "ok"
    error: str = ""


def claim_due(now: datetime, scope=session_scope, limit: int = 20) -> list[Due]:
    """Take every automation that is due, and move it to its next slot.

    `FOR UPDATE SKIP LOCKED` plus the advance-then-commit order is what makes
    this safe to call from more than one place at once. Two callers cannot claim
    the same row: the first locks it, the second skips it, and by the time the
    lock is released `next_run_at` is already in the future so it is no longer
    due.

    **The advance happens before the run, not after.** A crash mid-run therefore
    costs one report. Advancing afterwards would leave a still-due row behind,
    and the next poll would run it again -- and again after the next crash --
    which turns one bad automation into an unbounded number of model calls.

    `limit` bounds one poll. Twenty is far more than a plausible number of
    automations sharing a minute, and it stops a clock jump (a laptop waking from
    sleep with a hundred rows overdue) from queueing hours of model work.
    """
    claimed: list[Due] = []
    with scope() as store:
        rows = store.scalars(
            select(Automation)
            .where(Automation.enabled.is_(True), Automation.next_run_at <= now)
            .order_by(Automation.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.next_run_at = next_run(now, row.hour, row.minute, row.weekdays)
            claimed.append(
                Due(
                    id=row.id,
                    user_id=row.user_id,
                    title=row.title,
                    prompt=row.prompt,
                    web_search=row.web_search,
                    next_run_at=row.next_run_at,
                )
            )
            logger.info(
                "automation claimed id=%s title=%r schedule=%r next=%s",
                row.id,
                row.title,
                describe(row.hour, row.minute, row.weekdays),
                row.next_run_at.isoformat(),
            )
    return claimed


def _default_answer(due: Due) -> Iterator:
    """The real supervisor, asked this automation's question.

    Imported here rather than at module scope: `api.agent` pulls in LangGraph and
    every bank client, and `claim_due` above must stay importable without them.

    `session_id=due.id` is load-bearing. That argument is only ever used to
    derive `main_thread_id()` and the specialists' thread ids, so passing the
    automation's own id gives each automation a private agent thread -- compacted
    like any other, so a daily report accumulates context about what it said
    yesterday -- while creating no `ChatSession` row. Automations therefore never
    appear in the chat history sidebar and never inflate the profile counters.
    """
    from ..agent import answer

    return answer(
        due.prompt,
        session_id=due.id,
        user_id=due.user_id,
        web_search=due.web_search,
    )


def collect(events: Iterable) -> RunResult:
    """Drain a stream of `StreamEvent`s into one report.

    The same assembly `api/routers/chat.py` does for a chat turn, and it has to
    agree with it: `token` frames concatenated in order, `citation` frames kept
    as the dicts the answer was written from.

    **Nothing already written is discarded.** Two ways a run goes wrong, and both
    keep the prose:

    An `error` frame does not stop the drain. The supervisor can emit one after
    writing a usable partial answer, and half a gold-price report is worth more
    to the reader than a row saying "failed".

    An *exception* out of the generator does not either. The supervisor streams
    over a rotating tunnel, so a mid-answer transport failure is the normal
    failure -- and this is exactly where the router's behaviour must not be
    copied. `api/routers/chat.py` deliberately discards a partial answer, because
    a chat turn the user watched fail can be asked again with one click. Nobody
    is watching an automation at 09:00, and a discarded partial is a morning with
    no report at all.
    """
    result = RunResult()
    parts: list[str] = []
    try:
        for event in events:
            kind = getattr(event, "type", None)
            if kind == "token" and getattr(event, "text", None):
                parts.append(event.text)
            elif kind == "citation" and getattr(event, "citation", None) is not None:
                result.citations.append(event.citation.model_dump(mode="json"))
            elif kind == "error":
                result.status = "failed"
                result.error = getattr(event, "detail", "") or "The assistant failed."
    except Exception as exc:  # noqa: BLE001 - the partial answer is worth keeping
        logger.exception("automation stream failed after %d token(s)", len(parts))
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"[:2000]
    result.body = "".join(parts)
    if not result.body and result.status == "ok":
        result.status = "failed"
        result.error = "The assistant produced no answer."
    return result


def run_automation(
    due: Due,
    ask: Callable[[Due], Iterable] = _default_answer,
    scope=session_scope,
) -> AutomationReport:
    """Run one claimed automation and store its report.

    **This never raises.** A report is the only evidence the user has that an
    automation exists at all; an exception here would leave them with silence,
    which is indistinguishable from an automation they forgot they made. So a
    failure is a stored report with `status="failed"` and the reason in it.

    Returns the report row -- detached, since its session has closed -- so the
    caller can log or push it without a second query.
    """
    try:
        result = collect(ask(due))
    except Exception as exc:  # noqa: BLE001 - the user must get a report either way
        # `collect` keeps whatever a failing stream had already produced, so this
        # is the narrower case: building the stream failed before it yielded
        # anything -- a model client that cannot be constructed, a checkpointer
        # that cannot reach Postgres.
        logger.exception("automation run failed id=%s", due.id)
        result = RunResult(
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )

    with scope() as store:
        report = AutomationReport(
            automation_id=due.id,
            user_id=due.user_id,
            title=due.title,
            body=result.body,
            citations=result.citations,
            status=result.status,
            error=result.error,
        )
        store.add(report)
        # `last_error` on the automation as well as on the report, so the list can
        # show a broken automation as broken without a second query per row.
        automation = store.get(Automation, due.id)
        if automation is not None:
            automation.last_run_at = utcnow()
            automation.last_error = result.error
        store.flush()
        # Read before the session closes; `expire_on_commit=False` keeps the
        # loaded attributes readable afterwards but not ones never loaded.
        store.refresh(report)

    logger.info(
        "automation report id=%s automation=%s status=%s chars=%d citations=%d",
        report.id,
        due.id,
        result.status,
        len(result.body),
        len(result.citations),
    )
    # Tell the browser, if one is open. Here rather than in the two callers --
    # the scheduled loop and the router's manual-run thread -- because both of
    # them arrive at exactly this line, and a second call site is a second place
    # to forget. `publish` never raises and returns immediately when nobody is
    # listening, which is the usual case for a run that fires overnight.
    hub.publish(due.user_id, report_event(report))
    return report


def tick(
    now: datetime | None = None,
    ask: Callable[[Due], Iterable] = _default_answer,
    scope=session_scope,
) -> list[AutomationReport]:
    """One poll: claim what is due, then run it.

    Sequentially, one automation at a time. A report is a full supervisor pass
    over ten bank specialists; five at once is fifty concurrent calls into WAFs
    that already rate-limit this application from one address -- which is the
    reason `BANK_COMPARE_WORKERS` exists. Reports arriving a few minutes apart
    costs nothing; being blocked by a bank for an hour costs every one of them.
    """
    now = now or utcnow()
    return [run_automation(due, ask=ask, scope=scope) for due in claim_due(now, scope)]
