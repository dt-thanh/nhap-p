"""Checkpoint 2 human authentication.

This module is deliberately separate from ``app.auth``. The latter is the
existing static role-token boundary used by Mini CRM write routes; this module
owns human credentials only. Refresh, logout, reset, SSO, and application RBAC
remain outside this checkpoint.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol
from uuid import UUID, uuid4

import jwt
import sqlalchemy as sa
from app.auth_contract import (
    AccessTokenClaims,
    AuthenticatedHumanPrincipal,
    AuthErrorCode,
    CrmRole,
    Principal,
    UserStatus,
)
from app.config import get_settings
from app.db import get_session_factory
from app.models import crm_auth_invites, crm_auth_sessions, crm_password_reset_tokens, crm_users
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Cookie, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession


class HumanAuthError(Exception):
    def __init__(self, error_code: AuthErrorCode, message: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


PASSWORD_HASHER = PasswordHasher(type=Type.ID)
_DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
GENERIC_INVALID_CREDENTIALS = "Invalid credentials"
GENERIC_INVITE_ERROR = "Invalid or expired invitation"
GENERIC_SESSION_ERROR = "Invalid session"
GENERIC_PASSWORD_RESET_ERROR = "Invalid password reset token"
GENERIC_AUTHORIZATION_ERROR = "Not authorized"


def normalize_login(login: str) -> str:
    return login.strip().casefold()


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_user(row: sa.RowMapping | dict) -> dict:
    return {
        "id": row["id"],
        "login": row["login"],
        "email": row["email"],
        "status": row["status"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


class LoginRateLimiter:
    """Small process-local boundary until shared rate limiting is approved.

    Defaults to the login rate-limit settings so existing callers (``login``,
    password reset) are unaffected; pass ``max_attempts``/``window_seconds`` to
    key a distinct budget off different settings fields (see
    ``logout_rate_limiter`` below).
    """

    MAX_KEYS = 10_000

    def __init__(
        self,
        *,
        max_attempts: Callable[[], int] | None = None,
        window_seconds: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[tuple[str, str], list[float]] = {}
        self._max_attempts = max_attempts or (lambda: get_settings().login_rate_limit_attempts)
        self._window_seconds = window_seconds or (lambda: get_settings().login_rate_limit_window_seconds)

    def allowed(self, key: tuple[str, str]) -> bool:
        cutoff = time.monotonic() - self._window_seconds()
        with self._lock:
            if key not in self._attempts and len(self._attempts) >= self.MAX_KEYS:
                self._attempts.pop(next(iter(self._attempts)))
            attempts = [value for value in self._attempts.get(key, []) if value >= cutoff]
            self._attempts[key] = attempts
            return len(attempts) < self._max_attempts()

    def record_failure(self, key: tuple[str, str]) -> None:
        with self._lock:
            if key not in self._attempts and len(self._attempts) >= self.MAX_KEYS:
                self._attempts.pop(next(iter(self._attempts)))
            self._attempts.setdefault(key, []).append(time.monotonic())

    def clear(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


login_rate_limiter = LoginRateLimiter()
password_reset_rate_limiter = LoginRateLimiter()
logout_rate_limiter = LoginRateLimiter(
    max_attempts=lambda: get_settings().logout_rate_limit_attempts,
    window_seconds=lambda: get_settings().logout_rate_limit_window_seconds,
)


class PasswordResetDelivery(Protocol):
    async def send(self, *, login: str, token: str, expires_at: datetime) -> None:
        """Deliver a reset message without exposing credentials through HTTP."""


class NullPasswordResetDelivery:
    """Production-safe placeholder until an approved provider is integrated."""

    async def send(self, *, login: str, token: str, expires_at: datetime) -> None:
        del login, token, expires_at


def _require_signing_secret() -> str:
    secret = get_settings().auth_signing_secret.get_secret_value()
    if not secret:
        raise HumanAuthError(
            AuthErrorCode.AUTH_NOT_CONFIGURED,
            "Human authentication is not configured",
            503,
        )
    return secret


class JwtAccessTokenIssuer:
    def issue(self, *, subject: UUID, session_id: UUID, now: datetime | None = None) -> str:
        issued_at = now or _now()
        issued_seconds = int(issued_at.timestamp())
        expires_seconds = issued_seconds + get_settings().access_token_ttl_seconds
        claims = {
            "iss": get_settings().auth_issuer,
            "aud": get_settings().auth_audience,
            "sub": str(subject),
            "sid": str(session_id),
            "jti": str(uuid4()),
            "typ": "access",
            "iat": issued_seconds,
            "nbf": issued_seconds,
            "exp": expires_seconds,
            "ver": 1,
        }
        return jwt.encode(claims, _require_signing_secret(), algorithm=get_settings().auth_algorithm)


class JwtAccessTokenVerifier:
    def verify(self, token: str) -> Principal:
        try:
            decoded = jwt.decode(
                token,
                _require_signing_secret(),
                algorithms=[get_settings().auth_algorithm],
                audience=get_settings().auth_audience,
                issuer=get_settings().auth_issuer,
                options={"require": ["iss", "aud", "sub", "sid", "jti", "typ", "iat", "nbf", "exp", "ver"]},
            )
            claims = AccessTokenClaims.model_validate(decoded)
        except HumanAuthError:
            raise
        except (jwt.InvalidTokenError, TypeError, ValueError):
            raise HumanAuthError(AuthErrorCode.INVALID_CREDENTIALS, GENERIC_INVALID_CREDENTIALS, 401) from None
        return Principal(
            issuer=claims.iss,
            subject=claims.sub,
            session_id=claims.sid,
            token_id=claims.jti,
        )


def extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value.strip() or " " in value.strip():
        return None
    return value.strip()


async def require_human_principal(authorization: str | None) -> Principal:
    token = extract_bearer(authorization)
    if not token:
        raise HumanAuthError(AuthErrorCode.INVALID_CREDENTIALS, GENERIC_INVALID_CREDENTIALS, 401)
    return JwtAccessTokenVerifier().verify(token)


def authorize_human_role(principal: AuthenticatedHumanPrincipal, minimum: CrmRole) -> None:
    """Apply the deny-by-default role policy to a server-resolved principal."""
    levels = {
        CrmRole.BUSINESS_VIEWER: 0,
        CrmRole.PIPELINE_OPERATOR: 1,
        CrmRole.ADMIN: 2,
    }
    try:
        actual = CrmRole(principal.role)
        required = CrmRole(minimum)
    except (TypeError, ValueError):
        raise HumanAuthError(AuthErrorCode.AUTHORIZATION_DENIED, GENERIC_AUTHORIZATION_ERROR, 403) from None
    if principal.status != UserStatus.ACTIVE or levels.get(actual, -1) < levels[required]:
        raise HumanAuthError(AuthErrorCode.AUTHORIZATION_DENIED, GENERIC_AUTHORIZATION_ERROR, 403)


async def resolve_human_principal(session: AsyncSession, principal: Principal) -> AuthenticatedHumanPrincipal:
    """Resolve JWT subject/session claims against current database state."""
    row = (
        await session.execute(
            sa.select(crm_users.c.id, crm_users.c.status, crm_users.c.role)
            .join(crm_auth_sessions, crm_auth_sessions.c.user_id == crm_users.c.id)
            .where(
                crm_users.c.id == principal.subject,
                crm_users.c.status == UserStatus.ACTIVE,
                crm_users.c.auth_version == 1,
                crm_auth_sessions.c.id == principal.session_id,
                crm_auth_sessions.c.revoked_at.is_(None),
                crm_auth_sessions.c.expires_at > _now(),
            )
        )
    ).mappings().first()
    if row is None:
        raise HumanAuthError(AuthErrorCode.INVALID_CREDENTIALS, GENERIC_INVALID_CREDENTIALS, 401)
    return AuthenticatedHumanPrincipal(
        user_id=row["id"],
        session_id=principal.session_id,
        role=row["role"],
        status=row["status"],
    )


def require_human_role(minimum: CrmRole):
    """FastAPI dependency for role checks backed by the active human session."""

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> AuthenticatedHumanPrincipal:
        token_principal = await require_human_principal(authorization)
        session = db_session()
        try:
            resolved = await resolve_human_principal(session, token_principal)
        finally:
            await session.close()
        authorize_human_role(resolved, minimum)
        return resolved

    return dependency


async def require_resource_visibility(
    authorization: str | None = Header(default=None, alias="Authorization"),
    minicrm_session: str | None = Cookie(default=None, alias="minicrm_session"),
) -> AuthenticatedHumanPrincipal | object:
    """Require explicit development global visibility for resource reads.

    Legacy static bearer authorization remains valid for existing CRUD reads;
    X-API-Key remains a separate machine boundary and is never accepted here.
    """
    if get_settings().authorization_mode != "global_visibility":
        raise HumanAuthError(AuthErrorCode.AUTHORIZATION_DENIED, GENERIC_AUTHORIZATION_ERROR, 403)

    from app.auth import authenticate

    try:
        return await authenticate(authorization, minicrm_session)
    except HTTPException:
        principal = await require_human_principal(authorization)
        session = db_session()
        try:
            resolved = await resolve_human_principal(session, principal)
        finally:
            await session.close()
        authorize_human_role(resolved, CrmRole.BUSINESS_VIEWER)
        return resolved


@dataclass(frozen=True, slots=True)
class InvitationCreated:
    invitation_id: UUID
    raw_token: str


class HumanAuthService:
    def __init__(self, reset_delivery: PasswordResetDelivery | None = None) -> None:
        self.reset_delivery = reset_delivery or NullPasswordResetDelivery()

    @staticmethod
    def _new_password_reset_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _new_refresh_token() -> str:
        return secrets.token_urlsafe(48)

    @staticmethod
    async def _revoke_family(session: AsyncSession, *, family_id: UUID, now: datetime) -> None:
        await session.execute(
            sa.update(crm_auth_sessions)
            .where(
                crm_auth_sessions.c.family_id == family_id,
                crm_auth_sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=sa.func.greatest(crm_auth_sessions.c.created_at, now))
        )

    @staticmethod
    async def _revoke_user_sessions(session: AsyncSession, *, user_id: UUID, now: datetime) -> None:
        await session.execute(
            sa.update(crm_auth_sessions)
            .where(
                crm_auth_sessions.c.user_id == user_id,
                crm_auth_sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=sa.func.greatest(crm_auth_sessions.c.created_at, now))
        )

    async def request_password_reset(self, session: AsyncSession, *, login: str, client_key: str) -> None:
        normalized_login = normalize_login(login)
        rate_keys = (
            ("password-reset-ip", client_key),
            ("password-reset-login", normalized_login),
        )
        allowed = all(password_reset_rate_limiter.allowed(key) for key in rate_keys)
        for key in rate_keys:
            password_reset_rate_limiter.record_failure(key)
        if not allowed:
            return

        now = _now()
        raw_token = self._new_password_reset_token()
        expires_at = now + timedelta(seconds=get_settings().password_reset_token_ttl_seconds)
        async with session.begin():
            user = (
                await session.execute(
                    sa.select(crm_users).where(
                        crm_users.c.login == normalized_login,
                        crm_users.c.status == "active",
                    )
                )
            ).mappings().first()
            if user is None:
                return

            await session.execute(
                sa.update(crm_password_reset_tokens)
                .where(
                    crm_password_reset_tokens.c.user_id == user["id"],
                    crm_password_reset_tokens.c.used_at.is_(None),
                )
                .values(used_at=sa.func.greatest(crm_password_reset_tokens.c.created_at, now))
            )
            await session.execute(
                sa.insert(crm_password_reset_tokens).values(
                    id=uuid4(),
                    user_id=user["id"],
                    token_hash=hash_opaque_token(raw_token),
                    expires_at=expires_at,
                    created_at=now,
                )
            )

        await self.reset_delivery.send(login=normalized_login, token=raw_token, expires_at=expires_at)

    async def confirm_password_reset(self, session: AsyncSession, *, token: str, password: str) -> None:
        now = _now()
        async with session.begin():
            reset = (
                await session.execute(
                    sa.select(crm_password_reset_tokens)
                    .where(crm_password_reset_tokens.c.token_hash == hash_opaque_token(token))
                    .with_for_update()
                )
            ).mappings().first()
            if reset is None or reset["used_at"] is not None or reset["expires_at"] <= now:
                raise HumanAuthError(AuthErrorCode.PASSWORD_RESET_INVALID, GENERIC_PASSWORD_RESET_ERROR, 400)

            user = (
                await session.execute(
                    sa.select(crm_users)
                    .where(
                        crm_users.c.id == reset["user_id"],
                        crm_users.c.status == "active",
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if user is None:
                raise HumanAuthError(AuthErrorCode.PASSWORD_RESET_INVALID, GENERIC_PASSWORD_RESET_ERROR, 400)

            password_hash = PASSWORD_HASHER.hash(password)
            await session.execute(
                sa.update(crm_users)
                .where(crm_users.c.id == user["id"])
                .values(
                    password_hash=password_hash,
                    updated_at=sa.func.greatest(crm_users.c.updated_at, now),
                    disabled_at=None,
                )
            )
            await session.execute(
                sa.update(crm_password_reset_tokens)
                .where(crm_password_reset_tokens.c.id == reset["id"])
                .values(used_at=sa.func.greatest(crm_password_reset_tokens.c.created_at, now))
            )
            await self._revoke_user_sessions(session, user_id=user["id"], now=now)


    async def create_invitation(self, session: AsyncSession, *, login: str, role: str) -> InvitationCreated:
        normalized_login = normalize_login(login)
        raw_token = secrets.token_urlsafe(32)
        now = _now()
        invitation_id = uuid4()
        await session.execute(
            sa.insert(crm_auth_invites).values(
                id=invitation_id,
                login=normalized_login,
                role=role,
                invite_token_hash=hash_opaque_token(raw_token),
                expires_at=now + timedelta(seconds=get_settings().invite_token_ttl_seconds),
                created_at=now,
            )
        )
        await session.commit()
        # The raw token is returned only to the in-process delivery boundary.
        # The HTTP route intentionally discards it because no delivery provider
        # has been approved in Checkpoint 2.
        return InvitationCreated(invitation_id=invitation_id, raw_token=raw_token)

    async def accept_invitation(self, session: AsyncSession, *, token: str, password: str) -> dict:
        now = _now()
        async with session.begin():
            invite = (
                await session.execute(
                    sa.select(crm_auth_invites)
                    .where(crm_auth_invites.c.invite_token_hash == hash_opaque_token(token))
                    .with_for_update()
                )
            ).mappings().first()
            if invite is None or invite["accepted_at"] is not None or invite["expires_at"] <= now:
                raise HumanAuthError(AuthErrorCode.INVITE_INVALID, GENERIC_INVITE_ERROR, 400)

            existing = (
                await session.execute(
                    sa.select(crm_users).where(crm_users.c.login == invite["login"]).with_for_update()
                )
            ).mappings().first()
            password_hash = PASSWORD_HASHER.hash(password)
            if existing is None:
                user_id = uuid4()
                await session.execute(
                    sa.insert(crm_users).values(
                        id=user_id,
                        login=invite["login"],
                        email=None,
                        password_hash=password_hash,
                        status="active",
                        role=invite["role"],
                        auth_version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                user = {
                    "id": user_id,
                    "login": invite["login"],
                    "email": None,
                    "status": "active",
                    "role": invite["role"],
                    "created_at": now,
                }
            elif existing["status"] == "invited" and existing["password_hash"] is None:
                await session.execute(
                    sa.update(crm_users)
                    .where(crm_users.c.id == existing["id"])
                    .values(
                        password_hash=password_hash,
                        status="active",
                        role=invite["role"],
                        updated_at=now,
                        disabled_at=None,
                    )
                )
                user = {
                    **_safe_user(existing),
                    "status": "active",
                    "role": invite["role"],
                }
            else:
                raise HumanAuthError(AuthErrorCode.INVITE_INVALID, GENERIC_INVITE_ERROR, 400)

            await session.execute(
                sa.update(crm_auth_invites)
                .where(crm_auth_invites.c.id == invite["id"])
                .values(accepted_at=now)
            )
        return user

    async def login(
        self, session: AsyncSession, *, login: str, password: str, client_key: str
    ) -> tuple[str, str, dict]:
        normalized_login = normalize_login(login)
        rate_key = (client_key, normalized_login)
        if not login_rate_limiter.allowed(rate_key):
            raise HumanAuthError(AuthErrorCode.LOGIN_RATE_LIMITED, "Too many login attempts", 429)

        row = (
            await session.execute(sa.select(crm_users).where(crm_users.c.login == normalized_login))
        ).mappings().first()
        password_hash = row["password_hash"] if row is not None and row["password_hash"] else _DUMMY_PASSWORD_HASH
        try:
            valid_password = PASSWORD_HASHER.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            valid_password = False

        if not valid_password or row is None or row["status"] != "active":
            login_rate_limiter.record_failure(rate_key)
            raise HumanAuthError(AuthErrorCode.INVALID_CREDENTIALS, GENERIC_INVALID_CREDENTIALS, 401)

        login_rate_limiter.clear(rate_key)
        _require_signing_secret()
        now = _now()
        await session.rollback()
        session_id = uuid4()
        family_id = uuid4()
        refresh_token = self._new_refresh_token()
        async with session.begin():
            await session.execute(
                sa.insert(crm_auth_sessions).values(
                    id=session_id,
                    user_id=row["id"],
                    family_id=family_id,
                    refresh_token_hash=hash_opaque_token(refresh_token),
                    expires_at=now + timedelta(seconds=get_settings().refresh_token_ttl_seconds),
                    last_seen_at=now,
                    created_at=now,
                )
            )
        return (
            JwtAccessTokenIssuer().issue(subject=row["id"], session_id=session_id, now=now),
            refresh_token,
            _safe_user(row),
        )

    async def refresh(self, session: AsyncSession, *, refresh_token: str) -> tuple[str, str, dict]:
        _require_signing_secret()
        now = _now()
        token_hash = hash_opaque_token(refresh_token)
        reuse_detected = False
        child_id: UUID | None = None
        user: sa.RowMapping | None = None
        async with session.begin():
            row = (
                await session.execute(
                    sa.select(crm_auth_sessions)
                    .where(crm_auth_sessions.c.refresh_token_hash == token_hash)
                    .with_for_update()
                )
            ).mappings().first()
            if row is None:
                raise HumanAuthError(AuthErrorCode.SESSION_INVALID, GENERIC_SESSION_ERROR, 401)

            if row["replaced_by"] is not None:
                await self._revoke_family(session, family_id=row["family_id"], now=now)
                reuse_detected = True
            elif row["revoked_at"] is not None or row["expires_at"] <= now:
                raise HumanAuthError(AuthErrorCode.SESSION_INVALID, GENERIC_SESSION_ERROR, 401)
            else:
                user = (
                    await session.execute(
                        sa.select(crm_users)
                        .where(
                            crm_users.c.id == row["user_id"],
                            crm_users.c.status == "active",
                            crm_users.c.auth_version == 1,
                        )
                        .with_for_update()
                    )
                ).mappings().first()
                if user is None:
                    raise HumanAuthError(AuthErrorCode.SESSION_INVALID, GENERIC_SESSION_ERROR, 401)

                child_id = uuid4()
                child_refresh_token = self._new_refresh_token()
                await session.execute(
                    sa.insert(crm_auth_sessions).values(
                        id=child_id,
                        user_id=row["user_id"],
                        family_id=row["family_id"],
                        refresh_token_hash=hash_opaque_token(child_refresh_token),
                        expires_at=row["expires_at"],
                        last_seen_at=now,
                        created_at=now,
                    )
                )
                await session.execute(
                    sa.update(crm_auth_sessions)
                    .where(crm_auth_sessions.c.id == row["id"])
                    .values(revoked_at=now, replaced_by=child_id)
                )

        if reuse_detected:
            raise HumanAuthError(AuthErrorCode.SESSION_INVALID, GENERIC_SESSION_ERROR, 401)

        assert child_id is not None
        assert user is not None
        access_token = JwtAccessTokenIssuer().issue(subject=row["user_id"], session_id=child_id, now=now)
        return access_token, child_refresh_token, _safe_user(user)

    async def logout_current(self, session: AsyncSession, principal: Principal) -> None:
        async with session.begin():
            session_row = (
                await session.execute(
                    sa.select(crm_auth_sessions.c.id)
                    .where(
                        crm_auth_sessions.c.id == principal.session_id,
                        crm_auth_sessions.c.user_id == principal.subject,
                    )
                    .with_for_update()
                )
            ).first()
            if session_row is None:
                raise HumanAuthError(AuthErrorCode.SESSION_INVALID, GENERIC_SESSION_ERROR, 401)
            await session.execute(
                sa.update(crm_auth_sessions)
                .where(
                    crm_auth_sessions.c.id == principal.session_id,
                    crm_auth_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=sa.func.greatest(crm_auth_sessions.c.created_at, _now()))
            )

    async def logout_all(self, session: AsyncSession, principal: Principal) -> None:
        async with session.begin():
            session_row = (
                await session.execute(
                    sa.select(crm_auth_sessions.c.id)
                    .where(
                        crm_auth_sessions.c.id == principal.session_id,
                        crm_auth_sessions.c.user_id == principal.subject,
                    )
                    .with_for_update()
                )
            ).first()
            if session_row is None:
                raise HumanAuthError(AuthErrorCode.SESSION_INVALID, GENERIC_SESSION_ERROR, 401)
            await self._revoke_user_sessions(session, user_id=principal.subject, now=_now())

    async def current_user(self, session: AsyncSession, principal: Principal) -> dict:
        now = _now()
        row = (
            await session.execute(
                sa.select(crm_users)
                .join(crm_auth_sessions, crm_auth_sessions.c.user_id == crm_users.c.id)
                .where(
                    crm_users.c.id == principal.subject,
                    crm_users.c.status == "active",
                    crm_users.c.auth_version == 1,
                    crm_auth_sessions.c.id == principal.session_id,
                    crm_auth_sessions.c.revoked_at.is_(None),
                    crm_auth_sessions.c.expires_at > now,
                )
            )
        ).mappings().first()
        if row is None:
            raise HumanAuthError(AuthErrorCode.INVALID_CREDENTIALS, GENERIC_INVALID_CREDENTIALS, 401)
        return _safe_user(row)


def db_session() -> AsyncSession:
    return get_session_factory()()
