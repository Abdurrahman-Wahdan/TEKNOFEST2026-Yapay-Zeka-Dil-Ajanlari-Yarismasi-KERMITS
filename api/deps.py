"""The dependencies every router shares: a database session and a caller.

`CurrentUser` is the one to reach for. `OptionalUser` exists for endpoints that
serve public bank data but personalise it when someone is signed in -- the
dashboard's landing view is the same endpoint whether or not you have an account.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db.models import User
from .db.session import get_session
from .security import decode_token

DbSession = Annotated[Session, Depends(get_session)]

# auto_error=False so a missing header reaches our own handler and can mean
# "anonymous" for OptionalUser, instead of HTTPBearer raising 403 first.
_bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user_optional(
    session: DbSession, credentials: Credentials
) -> User | None:
    """The signed-in user, or None. Never raises."""
    if credentials is None:
        return None
    user_id = decode_token(credentials.credentials, expect="access")
    if user_id is None:
        return None
    user = session.get(User, user_id)
    # A deactivated account keeps working tokens until they expire, so the
    # active check is here rather than only at login.
    return user if user is not None and user.is_active else None


def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    """The signed-in user, or 401."""
    if user is None:
        raise UNAUTHENTICATED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
