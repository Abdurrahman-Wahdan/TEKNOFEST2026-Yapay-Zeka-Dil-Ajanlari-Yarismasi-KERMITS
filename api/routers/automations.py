"""The user's scheduled agent runs, and the reports they produce.

An automation is a question the assistant will be asked again on a schedule the
user set. Two ways to make one:

    POST /me/automations           the schedule given explicitly, from the form
    POST /me/automations/describe  the schedule read out of a sentence

Reports are read-only here -- nothing creates one but `api/automations/runner.py`
-- except for marking one read, which is the whole notification mechanism: a
report with `read_at IS NULL` is what the bell counts.

Its own module rather than more of `profile.py`, even though it shares the `/me`
prefix. This is a resource with a lifecycle, a background writer and ten routes;
`profile.py` is the onboarding result and a list of saved views.
"""

import asyncio
import logging
import threading
import uuid

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select

from ..automations.notifications import hub
from ..automations.runner import Due, run_automation
from ..automations.schedule import describe, next_run, valid_weekdays
from ..db.base import utcnow
from ..db.models import Automation, AutomationReport, User
from ..db.session import session_scope
from ..deps import CurrentUser, DbSession
from ..security import decode_token
from ..schemas.automations import (
    AutomationDescribeIn, AutomationIn, AutomationOut, AutomationPatch,
    ReportOut, ReportSummary, UnreadCount,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me/automations", tags=["automations"])

#: The ceiling the supervisor tool enforces too. Each automation is a full
#: supervisor pass over ten banks on a schedule nobody is watching, so the limit
#: is about unattended load rather than storage.
MAX_PER_USER = 20

#: How many reports the list returns. The notification menu wants five; the
#: Reports tab wants a page. Above this the user is scrolling history, which is
#: worth a `before` cursor when there is enough history to need one.
REPORTS_PAGE = 100


def _own(session, user, automation_id: uuid.UUID) -> Automation:
    """This user's automation, or 404.

    404 rather than 403 for someone else's row, the rule
    `api/routers/chat.py::_own_session` already sets: 403 confirms the id exists.
    """
    row = session.get(Automation, automation_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such automation.")
    return row


def _own_report(session, user, report_id: uuid.UUID) -> AutomationReport:
    row = session.get(AutomationReport, report_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such report.")
    return row


def _check_room(session, user) -> None:
    used = session.scalar(
        select(func.count())
        .select_from(Automation)
        .where(Automation.user_id == user.id)
    )
    if (used or 0) >= MAX_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You already have {used} automations (limit {MAX_PER_USER}). "
            "Delete one before adding another.",
        )


def _create(
    session, user, *, title: str, prompt: str, hour: int, minute: int,
    weekdays: list[int], web_search: bool,
) -> Automation:
    """The one place a row is written, so `next_run_at` is never forgotten.

    It cannot be a database default or a computed column: the schedule lives in
    Europe/Istanbul and the arithmetic is Python (`automations/schedule.py`). A
    row written without it would be a NOT NULL violation, which is the failure we
    want -- but only one caller should ever have to know that.
    """
    days = valid_weekdays(weekdays)
    row = Automation(
        user_id=user.id,
        title=title.strip(),
        prompt=prompt.strip(),
        hour=hour,
        minute=minute,
        weekdays=days,
        web_search=web_search,
        enabled=True,
        next_run_at=next_run(utcnow(), hour, minute, days),
    )
    session.add(row)
    session.commit()
    logger.info(
        "automation created id=%s user=%s schedule=%r next=%s",
        row.id, user.id, describe(hour, minute, days), row.next_run_at.isoformat(),
    )
    return row


@router.get("", response_model=list[AutomationOut])
def list_automations(user: CurrentUser, session: DbSession) -> list[AutomationOut]:
    """Every automation this user has, oldest first.

    Oldest first, not newest: this is a list someone maintains rather than a feed
    they read, and a list whose rows move when you add one is hard to keep track
    of. The newly created row appearing at the bottom is also where the composer
    that made it is.
    """
    rows = session.scalars(
        select(Automation)
        .where(Automation.user_id == user.id)
        .order_by(Automation.created_at)
    ).all()
    return [AutomationOut.model_validate(r) for r in rows]


@router.post("", response_model=AutomationOut, status_code=status.HTTP_201_CREATED)
def create_automation(
    body: AutomationIn, user: CurrentUser, session: DbSession
) -> AutomationOut:
    """Create one from an explicit schedule."""
    _check_room(session, user)
    row = _create(
        session, user,
        title=body.title, prompt=body.prompt, hour=body.hour, minute=body.minute,
        weekdays=body.weekdays, web_search=body.web_search,
    )
    return AutomationOut.model_validate(row)


@router.post(
    "/describe", response_model=AutomationOut, status_code=status.HTTP_201_CREATED
)
def describe_automation(
    body: AutomationDescribeIn, user: CurrentUser, session: DbSession
) -> AutomationOut:
    """Create one from a sentence, with any hand-set field overriding the agent.

    One round trip, not draft-then-confirm. The list is the confirmation: the row
    appears with the schedule the agent read, and every field on it is editable
    from the same page -- so a misread hour costs a click rather than a step
    everybody has to take every time.

    A field the user set by hand **wins**. They moved the picker after reading
    their own sentence; a model's reading of "akşam" does not outrank that.
    """
    _check_room(session, user)
    from agents.automation import draft_automation

    try:
        draft = draft_automation(body.text)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception as exc:
        logger.exception("automation drafting failed user=%s", user.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not read that automation. Try again, or set the time yourself.",
        ) from exc

    row = _create(
        session, user,
        title=draft.title,
        prompt=draft.prompt,
        hour=body.hour if body.hour is not None else draft.hour,
        minute=body.minute if body.minute is not None else draft.minute,
        weekdays=body.weekdays if body.weekdays is not None else draft.weekdays,
        web_search=(
            body.web_search if body.web_search is not None else draft.web_search
        ),
    )
    return AutomationOut.model_validate(row)


@router.patch("/{automation_id}", response_model=AutomationOut)
def update_automation(
    automation_id: uuid.UUID,
    body: AutomationPatch,
    user: CurrentUser,
    session: DbSession,
) -> AutomationOut:
    """Edit an automation. Only the fields present in the body are written.

    Any change to the schedule recomputes `next_run_at` from *now*. Keeping the
    old value would fire the automation once more at the time the user just
    changed away from, which is exactly the correction they were making.
    """
    row = _own(session, user, automation_id)
    fields = body.model_dump(exclude_unset=True)
    for field, value in fields.items():
        if value is not None:
            setattr(row, field, value)

    if {"hour", "minute", "weekdays", "enabled"} & fields.keys():
        row.weekdays = valid_weekdays(row.weekdays)
        row.next_run_at = next_run(utcnow(), row.hour, row.minute, row.weekdays)
    session.commit()
    logger.info(
        "automation updated id=%s enabled=%s schedule=%r next=%s",
        row.id, row.enabled, describe(row.hour, row.minute, row.weekdays),
        row.next_run_at.isoformat(),
    )
    return AutomationOut.model_validate(row)


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_automation(
    automation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> None:
    """Delete an automation. **Its reports are kept.**

    `automation_id` on a report is ON DELETE SET NULL, and the report carries its
    own snapshot of the title -- cancelling tomorrow's report must not delete
    yesterday's.
    """
    row = _own(session, user, automation_id)
    session.delete(row)
    session.commit()


@router.post("/{automation_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_now(
    automation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict:
    """Run one automation immediately, without touching its schedule.

    Not a convenience: without it, checking that an automation does what the user
    meant means waiting for a wall clock.

    In a thread, and 202 rather than 200, because a run is a full supervisor pass
    over ten bank specialists -- minutes. The report appears when it appears; the
    notification bell is how the user learns it did, which is the same way a
    scheduled run tells them.

    `next_run_at` is deliberately untouched. This is an extra run, not a
    rescheduled one, so tomorrow's report still arrives at the usual time.
    """
    row = _own(session, user, automation_id)
    due = Due(
        id=row.id,
        user_id=row.user_id,
        title=row.title,
        prompt=row.prompt,
        web_search=row.web_search,
        next_run_at=row.next_run_at,
    )
    logger.info("automation manual run id=%s user=%s", row.id, user.id)
    threading.Thread(
        target=run_automation,
        args=(due,),
        name=f"tf26-automation-{row.id}",
        daemon=True,
    ).start()
    return {"started": True, "automation_id": str(row.id)}


@router.get("/reports", response_model=list[ReportSummary])
def list_reports(
    user: CurrentUser,
    session: DbSession,
    unread_only: bool = False,
    limit: int = REPORTS_PAGE,
) -> list[ReportSummary]:
    """This user's reports, newest first. Bodies excluded -- see `ReportSummary`."""
    query = (
        select(AutomationReport)
        .where(AutomationReport.user_id == user.id)
        .order_by(AutomationReport.created_at.desc())
        .limit(max(1, min(limit, REPORTS_PAGE)))
    )
    if unread_only:
        query = query.where(AutomationReport.read_at.is_(None))
    rows = session.scalars(query).all()
    return [ReportSummary.model_validate(r) for r in rows]


@router.get("/reports/unread-count", response_model=UnreadCount)
def unread_count(user: CurrentUser, session: DbSession) -> UnreadCount:
    """What the notification badge shows.

    Its own route rather than `len(list_reports(unread_only=True))`: this is
    polled on a timer by every open tab, and it must stay one indexed count
    (`ix_automation_reports_unread`) rather than a row fetch.
    """
    total = session.scalar(
        select(func.count())
        .select_from(AutomationReport)
        .where(
            AutomationReport.user_id == user.id,
            AutomationReport.read_at.is_(None),
        )
    )
    return UnreadCount(unread=total or 0)


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(
    report_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> ReportOut:
    """One report, with its body and citations.

    Reading does not mark it read. Opening it does, through the route below --
    separate because the report page fetches this to render and a refetch
    (a retry, a cache revalidation) must not silently clear a notification the
    user never saw.
    """
    return ReportOut.model_validate(_own_report(session, user, report_id))


@router.post("/reports/{report_id}/read", response_model=ReportOut)
def mark_report_read(
    report_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> ReportOut:
    """Mark one report read, which is what removes it from the bell.

    Idempotent: an already-read report keeps its original `read_at`. Re-stamping
    would make "when did I first see this" unanswerable for no gain.
    """
    row = _own_report(session, user, report_id)
    if row.read_at is None:
        row.read_at = utcnow()
        session.commit()
    return ReportOut.model_validate(row)


# ----- the live stream -----

#: How long the browser has to send its token after the socket opens. Generous:
#: this is one frame on an already-established connection, and a slow phone on a
#: bad network should not be logged out of notifications for it.
AUTH_TIMEOUT_SECONDS = 10.0

#: Idle heartbeat. A report stream is silent for hours at a time, and an idle
#: WebSocket is exactly what a proxy reaps -- the FX stream never discovered this
#: because it pushes every three seconds. The client treats any frame as liveness
#: and reconnects on close either way, so this is belt and braces.
PING_SECONDS = 30.0


@router.websocket("/reports/stream")
async def reports_stream(socket: WebSocket) -> None:
    """New reports for the signed-in user, pushed as they are written.

    The polling routes above remain the source of truth -- the badge's count and
    the menu's list are still fetched, and this stream's job is only to say
    *now* rather than within the minute. A browser whose socket will not upgrade
    keeps working on the poll alone, which is why nothing here is required for
    correctness.

    **The token arrives as the first message, not as a query parameter.** A
    WebSocket cannot carry an `Authorization` header from the browser, and the
    usual workaround -- `?token=` -- writes an access token into the server log,
    every proxy log in front of it, and the user's own history. One frame on an
    already-open socket costs a round trip and leaks nothing.
    """
    await socket.accept()

    try:
        opening = await asyncio.wait_for(
            socket.receive_json(), timeout=AUTH_TIMEOUT_SECONDS
        )
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await socket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    except Exception:  # noqa: BLE001 - a frame we cannot parse is not a server error
        await socket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token = (opening or {}).get("token") if isinstance(opening, dict) else None
    user_id = decode_token(token, expect="access") if token else None
    if user_id is None:
        await socket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # The same active check `get_current_user_optional` makes: a deactivated
    # account keeps working tokens until they expire, and it should not keep
    # receiving its automations' reports.
    with session_scope() as store:
        account = store.get(User, user_id)
        if account is None or not account.is_active:
            await socket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    try:
        async with hub.subscribe(user_id) as queue:
            # Says "authenticated, and listening". The client flips to live on
            # this, so it can keep its poll as the fallback until it arrives.
            await socket.send_json({"type": "ready"})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except asyncio.TimeoutError:
                    message = {"type": "ping"}
                await socket.send_json(message)
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001 - a dropped listener is not a server error
        logger.debug("Reports stream closed", exc_info=True)
        return
