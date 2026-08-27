"""The onboarding result, and the dashboards a user kept.

A profile is entirely optional. A user who skipped the interview gets the
unfiltered dashboard, not a broken one, and nothing downstream may assume a
profile exists -- which is why GET returns a default-shaped profile rather than
404 when there is none.

Bank and family keys are validated against the live registry on write. Storing
an unknown key would not fail here; it would fail much later as a dashboard that
is silently empty, with nothing to point at.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from banks import families as families_mod
from banks import list_banks

from ..db.base import utcnow
from ..db.models import (
    Automation, AutomationReport, ChatMessage, ChatSession, Profile, SavedView,
)
from ..deps import CurrentUser, DbSession
from ..schemas.profile import (
    NotificationSettingsIn, NotificationSettingsOut, ProfileIn, ProfileOut,
    SavedViewIn, SavedViewOut, StatsOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["profile"])


def _validate_banks(names: list[str]) -> None:
    known = set(list_banks())
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown bank(s): {', '.join(unknown)}. "
            f"Valid: {', '.join(sorted(known))}.",
        )


def _validate_families(keys: list[str]) -> None:
    known = {k for table in families_mod.BY_CATEGORY.values() for k in table}
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown product family/families: {', '.join(unknown)}. "
            f"Valid: {', '.join(sorted(known))}.",
        )


def _profile_out(profile: Profile | None) -> ProfileOut:
    """A profile, or the empty default that means "onboarding has not run"."""
    if profile is None:
        return ProfileOut(
            persona="customer", banks=[], families=[], typical_amount=None,
            typical_term_months=None, answers={}, completed_at=None,
        )
    return ProfileOut.model_validate(profile)


@router.get("/profile", response_model=ProfileOut)
def get_profile(user: CurrentUser, session: DbSession) -> ProfileOut:
    """This user's preferences. Empty defaults when they have not answered yet."""
    profile = session.scalar(select(Profile).where(Profile.user_id == user.id))
    return _profile_out(profile)


@router.get("/settings/notifications", response_model=NotificationSettingsOut)
def get_notification_settings(user: CurrentUser) -> NotificationSettingsOut:
    return NotificationSettingsOut(
        account_email=user.email,
        notification_email=user.notification_email,
        effective_email=user.notification_email or user.email,
    )


@router.put("/settings/notifications", response_model=NotificationSettingsOut)
def put_notification_settings(
    body: NotificationSettingsIn, user: CurrentUser, session: DbSession
) -> NotificationSettingsOut:
    user.notification_email = str(body.notification_email) if body.notification_email else None
    session.commit()
    return NotificationSettingsOut(
        account_email=user.email,
        notification_email=user.notification_email,
        effective_email=user.notification_email or user.email,
    )


@router.put("/profile", response_model=ProfileOut)
def put_profile(body: ProfileIn, user: CurrentUser, session: DbSession) -> ProfileOut:
    """Write the profile. A partial body updates only the fields it carries.

    PUT rather than PATCH is a small lie about semantics, and a deliberate one:
    the interview arrives in pieces as the agent asks questions, and requiring
    the client to send the whole profile every time would mean a dropped answer
    silently erases an earlier one.
    """
    if body.banks is not None:
        _validate_banks(body.banks)
    if body.families is not None:
        _validate_families(body.families)

    profile = session.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        profile = Profile(user_id=user.id)
        session.add(profile)

    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(profile, field, value)

    # Stamped on the first write that names a bank or a product -- the point at
    # which the interview produced something usable.
    if profile.completed_at is None and (profile.banks or profile.families):
        profile.completed_at = utcnow()

    session.commit()
    return _profile_out(profile)


def _count(session, model, *where) -> int:
    """One indexed COUNT. Returns 0 rather than None for an empty table."""
    return session.scalar(
        select(func.count()).select_from(model).where(*where)
    ) or 0


@router.get("/stats", response_model=StatsOut)
def get_stats(user: CurrentUser, session: DbSession) -> StatsOut:
    """This user's own usage, for the profile overview.

    Eight counts and two timestamps, all from tables that already exist. Written
    as separate queries rather than one join with conditional aggregates: they hit
    four unrelated tables, each count is an index-only scan, and a single clever
    statement here would be harder to read than the page it feeds.

    Message counts go through the session join rather than a `user_id` on
    `chat_messages`, because there is no such column -- a message belongs to a
    conversation, and the conversation belongs to the user. That is the right
    shape; it just means these two counts are the only ones with a subquery.
    """
    own_sessions = select(ChatSession.id).where(ChatSession.user_id == user.id)
    return StatsOut(
        chat_sessions=_count(session, ChatSession, ChatSession.user_id == user.id),
        messages_sent=_count(
            session, ChatMessage,
            ChatMessage.session_id.in_(own_sessions),
            ChatMessage.role == "user",
        ),
        messages_received=_count(
            session, ChatMessage,
            ChatMessage.session_id.in_(own_sessions),
            ChatMessage.role == "assistant",
        ),
        saved_tables=_count(session, SavedView, SavedView.user_id == user.id),
        automations=_count(session, Automation, Automation.user_id == user.id),
        reports=_count(
            session, AutomationReport, AutomationReport.user_id == user.id
        ),
        unread_reports=_count(
            session, AutomationReport,
            AutomationReport.user_id == user.id,
            AutomationReport.read_at.is_(None),
        ),
        # The first conversation started and the last one touched. `updated_at`
        # for the latter because a reply written into an old thread is activity,
        # and ordering by `created_at` would report the user as idle since the day
        # they opened that thread.
        first_activity=session.scalar(
            select(func.min(ChatSession.created_at)).where(
                ChatSession.user_id == user.id
            )
        ),
        last_activity=session.scalar(
            select(func.max(ChatSession.updated_at)).where(
                ChatSession.user_id == user.id
            )
        ),
    )


@router.get("/views", response_model=list[SavedViewOut])
def list_views(user: CurrentUser, session: DbSession) -> list[SavedViewOut]:
    """Every dashboard this user kept, newest first."""
    views = session.scalars(
        select(SavedView)
        .where(SavedView.user_id == user.id)
        .order_by(SavedView.updated_at.desc())
    ).all()
    return [SavedViewOut.model_validate(v) for v in views]


@router.put("/views/{slug}", response_model=SavedViewOut)
def put_view(
    slug: str, body: SavedViewIn, user: CurrentUser, session: DbSession
) -> SavedViewOut:
    """Create or replace a saved dashboard.

    Component types are not checked against a catalog here. The catalog lives in
    the frontend, and duplicating it in Python would guarantee the two drift;
    an unknown type renders as a visible placeholder tile instead -- which
    matters, because the AI Overview page writes this JSON and a model will
    eventually name a component that does not exist.
    """
    if slug != body.slug:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Path slug {slug!r} does not match body slug {body.slug!r}.",
        )

    view = session.scalar(
        select(SavedView).where(
            SavedView.user_id == user.id, SavedView.slug == slug
        )
    )
    if view is None:
        view = SavedView(user_id=user.id, slug=slug)
        session.add(view)

    view.title = body.title
    view.components = [c.model_dump() for c in body.components]
    view.generated = body.generated
    session.commit()
    return SavedViewOut.model_validate(view)


@router.delete("/views/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(slug: str, user: CurrentUser, session: DbSession) -> None:
    view = session.scalar(
        select(SavedView).where(
            SavedView.user_id == user.id, SavedView.slug == slug
        )
    )
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No saved view {slug!r}.")
    session.delete(view)
    session.commit()
