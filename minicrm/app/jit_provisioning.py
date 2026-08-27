"""JIT (Just-In-Time) provisioning cho user Mini CRM.

Khi user login OIDC lần đầu, tạo một dòng trong ``crm_users`` liên kết với
danh tính Keycloak. Lần login sau chỉ update các field mirror (email, role).

Nguyên tắc:
- KHÔNG lưu password (Keycloak giữ). ``password_hash`` = NULL cho user OIDC.
- Khoá liên kết là ``keycloak_sub`` (claim ``sub`` của token), KHÔNG dùng email.
- Idempotent: gọi 100 lần cho cùng 1 user = 1 dòng duy nhất.
- Fail-open: nếu upsert lỗi (DB tạm không ghi được), auth KHÔNG bị chặn — chỉ log.
  Lý do: identity ở Keycloak vẫn đủ để authorize; JIT chỉ để mirror profile.
- Role local mirror role Keycloak để hiển thị/admin xem; source of truth về
  authorization vẫn là Keycloak realm role trong token.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session_factory
from app.oidc import OidcIdentity

logger = logging.getLogger(__name__)


def _derive_login(identity: OidcIdentity) -> str:
    """Chọn giá trị hợp lý cho ``login`` (UNIQUE) từ identity Keycloak.

    Ưu tiên preferred_username/email; fallback về ``sub`` để luôn có giá trị
    khác NULL (cột NOT NULL).
    """
    if identity.email:
        return identity.email.strip().lower()
    return f"kc:{identity.subject}"


async def upsert_from_oidc(
    identity: OidcIdentity,
    role: str,
    session: AsyncSession | None = None,
) -> None:
    """Tạo hoặc update dòng ``crm_users`` cho user OIDC.

    Args:
        identity: đối tượng đã verify từ token OIDC (đã kiểm sig/iss/aud/exp).
        role: role đã resolve (admin / pipeline_operator / business_viewer).
        session: optional; nếu None thì tự mở session mới và commit.
    """
    own_session = session is None
    factory = get_session_factory()
    if own_session:
        session = factory()

    try:
        login = _derive_login(identity)
        now = datetime.now(timezone.utc)

        # 1) Đã có dòng theo keycloak_sub? -> update mirror fields.
        row = (
            await session.execute(
                text(
                    "SELECT id FROM crm_users WHERE keycloak_sub = :sub LIMIT 1"
                ),
                {"sub": identity.subject},
            )
        ).first()

        if row is not None:
            await session.execute(
                text(
                    """
                    UPDATE crm_users
                       SET email      = COALESCE(:email, email),
                           login      = :login,
                           role       = :role,
                           status     = 'active',
                           updated_at = :now
                     WHERE keycloak_sub = :sub
                    """
                ),
                {
                    "email": identity.email,
                    "login": login,
                    "role": role,
                    "now": now,
                    "sub": identity.subject,
                },
            )
        else:
            # 2) Chưa có dòng theo sub. Có thể user legacy đã tồn tại theo login
            #    (email trùng) — nếu vậy, gắn keycloak_sub vào dòng cũ thay vì
            #    tạo trùng UNIQUE(login).
            legacy = (
                await session.execute(
                    text(
                        "SELECT id FROM crm_users "
                        "WHERE login = :login AND keycloak_sub IS NULL LIMIT 1"
                    ),
                    {"login": login},
                )
            ).first()

            if legacy is not None:
                await session.execute(
                    text(
                        """
                        UPDATE crm_users
                           SET keycloak_sub = :sub,
                               email        = COALESCE(:email, email),
                               role         = :role,
                               status       = 'active',
                               updated_at   = :now
                         WHERE id = :id
                        """
                    ),
                    {
                        "sub": identity.subject,
                        "email": identity.email,
                        "role": role,
                        "now": now,
                        "id": legacy.id,
                    },
                )
            else:
                # 3) Insert mới hoàn toàn.
                await session.execute(
                    text(
                        """
                        INSERT INTO crm_users
                            (id, login, email, password_hash, status, role,
                             auth_version, keycloak_sub, created_at, updated_at)
                        VALUES
                            (:id, :login, :email, NULL, 'active', :role,
                             1, :sub, :now, :now)
                        """
                    ),
                    {
                        "id": uuid4(),
                        "login": login,
                        "email": identity.email,
                        "role": role,
                        "sub": identity.subject,
                        "now": now,
                    },
                )

        if own_session:
            await session.commit()

    except Exception as exc:  # pragma: no cover — fail-open, không chặn auth
        logger.warning(
            "JIT provisioning failed for sub=%s login=%s: %s",
            identity.subject,
            _derive_login(identity),
            exc,
        )
        if own_session:
            try:
                await session.rollback()
            except Exception:
                pass
    finally:
        if own_session and session is not None:
            await session.close()
