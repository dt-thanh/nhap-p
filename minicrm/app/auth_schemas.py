"""HTTP schemas for the Checkpoint 2 human-auth surface."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.auth_contract import CrmRole, UserStatus
from pydantic import BaseModel, ConfigDict, Field


class InvitationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=1, max_length=320)
    role: CrmRole = CrmRole.BUSINESS_VIEWER


class InvitationAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=8, max_length=256)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(max_length=1024)


class PasswordResetRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(max_length=320)


class PasswordResetConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(max_length=1024)
    password: str = Field(min_length=8, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    login: str
    email: str | None
    status: UserStatus
    role: CrmRole
    created_at: datetime


class InvitationMessageOut(BaseModel):
    message: Literal["Invitation created"] = "Invitation created"


class PasswordResetMessageOut(BaseModel):
    message: Literal["If the account is eligible, reset instructions will be sent"] = (
        "If the account is eligible, reset instructions will be sent"
    )


class PasswordResetConfirmOut(BaseModel):
    message: Literal["Password reset completed"] = "Password reset completed"


class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    user: UserOut
