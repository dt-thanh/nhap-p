"""Checkpoint 2 human-auth routes."""

from __future__ import annotations

from app.auth_contract import CrmRole
from app.auth_schemas import (
    InvitationAcceptIn,
    InvitationCreateIn,
    InvitationMessageOut,
    LoginIn,
    LoginOut,
    PasswordResetConfirmIn,
    PasswordResetConfirmOut,
    PasswordResetMessageOut,
    PasswordResetRequestIn,
    RefreshIn,
    UserOut,
)
from app.config import get_settings
from app.human_auth import HumanAuthService, db_session, require_human_principal, require_human_role
from fastapi import APIRouter, Depends, Header, Request, status

router = APIRouter(prefix="/auth", tags=["auth"])
service = HumanAuthService()


@router.post(
    "/invitations",
    response_model=InvitationMessageOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_invitation(
    payload: InvitationCreateIn,
    _principal=Depends(require_human_role(CrmRole.ADMIN)),
) -> InvitationMessageOut:
    session = db_session()
    try:
        invitation = await service.create_invitation(session, login=payload.login, role=payload.role.value)
        del invitation
        return InvitationMessageOut()
    finally:
        await session.close()


@router.post("/invitations/accept", response_model=UserOut)
async def accept_invitation(payload: InvitationAcceptIn) -> UserOut:
    session = db_session()
    try:
        user = await service.accept_invitation(session, token=payload.token, password=payload.password)
        return UserOut.model_validate(user)
    finally:
        await session.close()


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn, request: Request) -> LoginOut:
    session = db_session()
    try:
        client_key = request.client.host if request.client else "unknown"
        token, refresh_token, user = await service.login(
            session, login=payload.login, password=payload.password, client_key=client_key
        )
        return LoginOut(
            access_token=token,
            refresh_token=refresh_token,
            expires_in=get_settings().access_token_ttl_seconds,
            user=UserOut.model_validate(user),
        )
    finally:
        await session.close()


@router.post("/refresh", response_model=LoginOut)
async def refresh(payload: RefreshIn) -> LoginOut:
    session = db_session()
    try:
        token, refresh_token, user = await service.refresh(session, refresh_token=payload.refresh_token)
        return LoginOut(
            access_token=token,
            refresh_token=refresh_token,
            expires_in=get_settings().access_token_ttl_seconds,
            user=UserOut.model_validate(user),
        )
    finally:
        await session.close()


@router.post("/password-reset/request", response_model=PasswordResetMessageOut)
async def request_password_reset(payload: PasswordResetRequestIn, request: Request) -> PasswordResetMessageOut:
    session = db_session()
    try:
        client_key = request.client.host if request.client else "unknown"
        await service.request_password_reset(session, login=payload.login, client_key=client_key)
        return PasswordResetMessageOut()
    finally:
        await session.close()


@router.post("/password-reset/confirm", response_model=PasswordResetConfirmOut)
async def confirm_password_reset(payload: PasswordResetConfirmIn) -> PasswordResetConfirmOut:
    session = db_session()
    try:
        await service.confirm_password_reset(session, token=payload.token, password=payload.password)
        return PasswordResetConfirmOut()
    finally:
        await session.close()


@router.get("/me", response_model=UserOut)
async def me(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> UserOut:
    principal = await require_human_principal(authorization)
    session = db_session()
    try:
        return UserOut.model_validate(await service.current_user(session, principal))
    finally:
        await session.close()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    principal = await require_human_principal(authorization)
    session = db_session()
    try:
        await service.logout_current(session, principal)
    finally:
        await session.close()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    principal = await require_human_principal(authorization)
    session = db_session()
    try:
        await service.logout_all(session, principal)
    finally:
        await session.close()
