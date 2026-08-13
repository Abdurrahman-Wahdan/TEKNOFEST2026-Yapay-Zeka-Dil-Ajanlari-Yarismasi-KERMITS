"""Signup, login, refresh, and the current user."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    # A length floor and no composition rules. Measured advice (NIST 800-63B)
    # is that mandatory symbol/digit mixes push users toward predictable
    # substitutions; length is what actually helps.
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(default="", max_length=120)
    locale: str = Field(default="tr", pattern="^(tr|en)$")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    """What a successful signup, login or refresh returns."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime, in seconds.")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    locale: str
    created_at: datetime
    # Whether onboarding has run. The frontend routes a false here to the
    # interview instead of the dashboard.
    has_profile: bool = False
