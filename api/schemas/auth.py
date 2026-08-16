"""Signup, login, refresh, and the current user."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    # A short floor, by design: this is a local system with no external
    # exposure, and a lower bar makes account creation faster to try out.
    password: str = Field(min_length=5, max_length=200)
    display_name: str = Field(default="", max_length=120)
    locale: str = Field(default="tr", pattern="^(tr|en)$")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    # Same floor as signup, so a password valid at signup is never rejected
    # on reset.
    new_password: str = Field(min_length=5, max_length=200)


class ResetPasswordResponse(BaseModel):
    detail: str


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
