"""Passwords and tokens. The only module that hashes or signs anything.

Two decisions worth knowing:

**Argon2id, not bcrypt.** bcrypt silently truncates a password at 72 bytes, and
a Turkish passphrase reaches 72 bytes well before it reaches 72 characters, so
two different long passwords can authenticate each other. Argon2id has no such
limit and is the current password-hashing recommendation.

**Email is normalised through casefold(), not lower().** In Turkish, 'I'.lower()
is 'ı' -- so a user who signs up as IREM@x.com and logs in as irem@x.com is two
accounts on a Turkish locale and one on an English locale. `casefold()` is
locale-independent, and only the normalised form is ever looked up.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from config.settings import settings

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]

# A development-only fallback so `uvicorn api.main:app` runs on a fresh clone
# with no .env. Settings refuses an empty secret in any other environment, so
# this constant can never reach a deployment.
_DEV_SECRET = "tf26-development-secret-not-for-deployment"


def normalise_email(email: str) -> str:
    """The canonical form an account is stored and looked up under."""
    return email.strip().casefold()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Whether the password matches. False rather than raising, for any failure.

    A malformed hash in the database and a wrong password are the same answer to
    the caller -- distinguishing them in the response would tell an attacker
    which accounts exist.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether this hash predates the current Argon2 parameters.

    Called on successful login: the plaintext is in hand exactly then, and never
    again, so it is the one moment an old hash can be upgraded.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _secret() -> str:
    return settings.API_JWT_SECRET or _DEV_SECRET


def create_token(user_id: uuid.UUID, token_type: TokenType = "access") -> str:
    """Sign a token for this user.

    `jti` is a unique id per token and `typ` names the kind. Without `typ` a
    refresh token would be accepted anywhere an access token is -- a 30-day
    credential silently standing in for a 30-minute one.
    """
    now = datetime.now(timezone.utc)
    lifetime = (
        timedelta(minutes=settings.API_ACCESS_TOKEN_MINUTES)
        if token_type == "access"
        else timedelta(days=settings.API_REFRESH_TOKEN_DAYS)
    )
    payload = {
        "sub": str(user_id),
        "typ": token_type,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, _secret(), algorithm=settings.API_JWT_ALGORITHM)


def decode_token(token: str, expect: TokenType = "access") -> uuid.UUID | None:
    """The user id this token is for, or None if it is not usable.

    None covers every failure -- expired, wrong signature, wrong type, malformed
    subject -- because the caller's response is the same 401 in all of them.
    The reason is logged, not returned.
    """
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[settings.API_JWT_ALGORITHM],
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.PyJWTError as exc:
        logger.debug("Rejected token: %s", exc)
        return None

    if payload.get("typ") != expect:
        logger.debug("Rejected token: expected %s, got %r", expect, payload.get("typ"))
        return None

    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        logger.debug("Rejected token: subject is not a UUID")
        return None
