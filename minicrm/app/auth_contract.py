"""Provider-neutral human-auth contracts.

This module defines the identity boundary only. Checkpoint 1 deliberately does
not issue, verify, persist, or accept tokens in an API route.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class CrmRole(StrEnum):
    BUSINESS_VIEWER = "business_viewer"
    PIPELINE_OPERATOR = "pipeline_operator"
    ADMIN = "admin"


InviteLifecycle = Literal["pending", "accepted", "expired"]
PasswordResetLifecycle = Literal["pending", "used", "expired"]


class AuthErrorCode(StrEnum):
    AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    LOGIN_RATE_LIMITED = "LOGIN_RATE_LIMITED"
    INVITE_INVALID = "INVITE_INVALID"
    INVITE_EXPIRED = "INVITE_EXPIRED"
    INVITE_USED = "INVITE_USED"
    SESSION_INVALID = "SESSION_INVALID"
    RESET_INVALID = "RESET_INVALID"
    RESET_EXPIRED = "RESET_EXPIRED"
    RESET_USED = "RESET_USED"
    PASSWORD_RESET_INVALID = "PASSWORD_RESET_INVALID"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    USER_DISABLED = "USER_DISABLED"


class AccessTokenClaims(BaseModel):
    """The complete, minimal access-token claim contract."""

    model_config = ConfigDict(extra="forbid")

    iss: str
    aud: str
    sub: UUID
    sid: UUID
    jti: UUID
    typ: Literal["access"]
    iat: int
    nbf: int
    exp: int
    ver: Literal[1]

    @model_validator(mode="after")
    def validate_lifetime(self) -> AccessTokenClaims:
        if self.nbf > self.exp or self.exp <= self.iat:
            raise ValueError("invalid access-token lifetime")
        return self


@dataclass(frozen=True, slots=True)
class Principal:
    """Verified identity only; authorization is intentionally absent."""

    issuer: str
    subject: UUID
    session_id: UUID
    token_id: UUID


@dataclass(frozen=True, slots=True)
class AuthenticatedHumanPrincipal:
    """Server-resolved human identity and role for one active session."""

    user_id: UUID
    session_id: UUID
    role: CrmRole | str
    status: UserStatus | str


class TokenIssuer(Protocol):
    def issue(self, *, subject: UUID, session_id: UUID, now: datetime | None = None) -> str:
        """Issue a short-lived access token for an already authenticated user."""


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal:
        """Verify signature, issuer, audience, type, lifetime, and identifiers."""
