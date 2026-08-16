"""Signup, login, refresh, and who am I.

One rule runs through all of it: **the API never reveals whether an email has an
account**. A wrong password and an unknown address return the same 401 with the
same text, and signup's duplicate case is the only place that necessarily
differs -- there is no way to create an account without saying it exists.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from config.settings import settings

from ..db.models import Profile, User
from ..deps import CurrentUser, DbSession
from ..schemas.auth import (
    LoginRequest, RefreshRequest, ResetPasswordRequest, ResetPasswordResponse,
    SignupRequest, TokenPair, UserOut,
)
from ..security import (
    create_token, decode_token, hash_password, needs_rehash, normalise_email,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(user.id, "access"),
        refresh_token=create_token(user.id, "refresh"),
        expires_in=settings.API_ACCESS_TOKEN_MINUTES * 60,
    )


def _user_out(session, user: User) -> UserOut:
    has_profile = session.scalar(
        select(Profile.id).where(Profile.user_id == user.id)
    ) is not None
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        locale=user.locale,
        created_at=user.created_at,
        has_profile=has_profile,
    )


@router.post("/signup", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, session: DbSession) -> TokenPair:
    """Create an account and sign in immediately.

    No email verification step: nothing here is sent by email, and a
    verification flow on a local, on-premise system would add a mail server to
    the deployment for no security the deployment actually gains.
    """
    normalised = normalise_email(body.email)
    existing = session.scalar(select(User).where(User.email_normalised == normalised))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=body.email.strip(),
        email_normalised=normalised,
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip(),
        locale=body.locale,
    )
    session.add(user)
    session.commit()
    logger.info("Created account %s", user.id)
    return _tokens(user)


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, session: DbSession) -> TokenPair:
    """Exchange an email and password for a token pair."""
    user = session.scalar(
        select(User).where(User.email_normalised == normalise_email(body.email))
    )
    if user is None or not user.is_active:
        # Hash anyway on a missing account. Returning early makes an unknown
        # email measurably faster than a wrong password, which is enough to
        # enumerate accounts with a stopwatch.
        hash_password(body.password)
        raise BAD_CREDENTIALS

    if not verify_password(body.password, user.password_hash):
        raise BAD_CREDENTIALS

    # The one moment the plaintext exists and an outdated hash can be upgraded.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        session.commit()

    return _tokens(user)


@router.post("/reset-password", response_model=ResetPasswordResponse)
def reset_password(body: ResetPasswordRequest, session: DbSession) -> ResetPasswordResponse:
    """Set a new password directly from an email address — demo shortcut.

    No emailed token, no proof the requester owns the inbox: whoever submits an
    email gets to set that account's password. Fine for a local demo where
    nobody else can reach this API; the moment this is reachable by anyone but
    the account holder, this needs a time-limited emailed token in front of it.

    The response is identical whether or not the email has an account, for the
    same reason the module docstring gives for login: a reset endpoint that
    answers differently for known/unknown emails is an account-enumeration
    oracle.
    """
    user = session.scalar(
        select(User).where(User.email_normalised == normalise_email(body.email))
    )
    if user is not None:
        user.password_hash = hash_password(body.new_password)
        session.commit()
    else:
        # Hash anyway, so a missing account doesn't respond measurably faster
        # than one that exists — same reasoning as login's early-return case.
        hash_password(body.new_password)

    return ResetPasswordResponse(
        detail="If that email has an account, its password has been reset."
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, session: DbSession) -> TokenPair:
    """Trade a refresh token for a new pair.

    Both tokens are reissued, so a session that stays active never has to log in
    again, while an abandoned one expires on the refresh token's clock.
    """
    user_id = decode_token(body.refresh_token, expect="refresh")
    if user_id is None:
        raise BAD_CREDENTIALS
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise BAD_CREDENTIALS
    return _tokens(user)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser, session: DbSession) -> UserOut:
    """The signed-in user. The frontend's route guard calls this on load."""
    return _user_out(session, user)
