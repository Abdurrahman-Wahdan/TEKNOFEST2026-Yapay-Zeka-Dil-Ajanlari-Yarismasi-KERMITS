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
from sqlalchemy import select

from banks import families as families_mod
from banks import list_banks

from ..db.base import utcnow
from ..db.models import Profile, SavedView
from ..deps import CurrentUser, DbSession
from ..schemas.profile import ProfileIn, ProfileOut, SavedViewIn, SavedViewOut

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
