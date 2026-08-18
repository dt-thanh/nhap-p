"""Vòng đời khoá API: cấp, xác minh, buộc instance, hết hạn, thu hồi, xoay.

Đây là tầng canh cửa của toàn bộ Phase 3, nên nó được kiểm cả ở mức "hoạt động
đúng" lẫn ở mức "không rò rỉ thứ không được rò rỉ".
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import sync_credentials
from src.services.sync_credentials import (
    KEY_NAMESPACE,
    KEY_PREFIX_LENGTH,
    CredentialError,
    SyncCredentialService,
    generate_key,
    hash_key,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

# `asyncio` KHÔNG đặt ở pytestmark của module: bốn test sinh khoá dưới đây là hàm
# đồng bộ (không cần DB), và đánh dấu asyncio lên chúng sẽ sinh cảnh báo mỗi lần
# chạy. Các test async được đánh dấu riêng qua fixture async của pytest-asyncio
# ở chế độ STRICT — xem `asyncio_mode` trong cấu hình pytest.
pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

INSTANCE = "synthetic-mini-crm"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean(session_factory):
    """Chỉ xoá khoá của test này — module khác có thể đang giữ khoá riêng."""

    async def wipe():
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id.like("synthetic-%"))
                )

    await wipe()
    yield
    await wipe()


@pytest_asyncio.fixture
async def service():
    return SyncCredentialService()


async def _issue(session_factory, service, **kwargs):
    async with session_factory() as session:
        async with session.begin():
            return await service.issue(
                session,
                source_system=kwargs.pop("source_system", "mini_crm"),
                source_instance_id=kwargs.pop("source_instance_id", INSTANCE),
                **kwargs,
            )


# --- Sinh khoá --------------------------------------------------------------


def test_generated_keys_are_unique_and_namespaced():
    keys = {generate_key()[0] for _ in range(200)}
    assert len(keys) == 200, "khoá sinh ra bị trùng — nguồn ngẫu nhiên có vấn đề"
    assert all(key.startswith(KEY_NAMESPACE) for key in keys)


def test_prefix_is_the_leading_characters_of_the_key():
    raw, prefix, _ = generate_key()
    assert prefix == raw[:KEY_PREFIX_LENGTH]


def test_hash_is_stable_and_differs_per_key():
    raw_a, _, hash_a = generate_key()
    raw_b, _, hash_b = generate_key()
    assert hash_key(raw_a) == hash_a
    assert hash_a != hash_b
    assert len(hash_a) == 64


def test_prefix_alone_cannot_reconstruct_the_key():
    """Prefix chỉ để TRA CỨU. Nó không được đủ để dựng lại khoá."""
    raw, prefix, _ = generate_key()
    assert len(raw) > len(prefix) + 20, "phần bí mật còn lại quá ngắn"


# --- Cấp khoá ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_stores_only_the_hash(session_factory, service):
    """Khoá thô KHÔNG được nằm ở bất kỳ cột nào của bảng.

    Đây là bất biến quan trọng nhất của module: một bản dump bị lộ không được
    đồng nghĩa với toàn bộ khoá bị lộ.
    """
    issued = await _issue(session_factory, service)

    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(sync_credentials).where(sync_credentials.c.id == issued.credential_id)))
            .mappings()
            .one()
        )

    assert row["key_hash"] == hash_key(issued.api_key)
    stored_values = " ".join(str(value) for value in row.values())
    assert issued.api_key not in stored_values, "khoá thô bị lưu xuống DB"


@pytest.mark.asyncio
async def test_issue_rejects_blank_scope(session_factory, service):
    with pytest.raises(CredentialError) as exc:
        await _issue(session_factory, service, source_instance_id="")
    assert exc.value.error_code == "INVALID_CREDENTIAL_SCOPE"


@pytest.mark.asyncio
async def test_issue_rejects_expiry_in_the_past(session_factory, service):
    with pytest.raises(CredentialError) as exc:
        await _issue(session_factory, service, expires_at=datetime.now(UTC) - timedelta(hours=1))
    assert exc.value.error_code == "INVALID_EXPIRY"


# --- Xác thực ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_key_authenticates(session_factory, service):
    issued = await _issue(session_factory, service)

    async with session_factory() as session:
        async with session.begin():
            caller = await service.authenticate(session, api_key=issued.api_key)

    assert caller.credential_id == issued.credential_id
    assert caller.source_instance_id == INSTANCE


@pytest.mark.asyncio
async def test_authenticate_updates_last_used_at(session_factory, service):
    issued = await _issue(session_factory, service)

    async with session_factory() as session:
        async with session.begin():
            await service.authenticate(session, api_key=issued.api_key)

    async with session_factory() as session:
        last_used = await session.scalar(
            sa.select(sync_credentials.c.last_used_at).where(sync_credentials.c.id == issued.credential_id)
        )
    assert last_used is not None


@pytest.mark.parametrize(
    "key, expected",
    [
        ("", "MISSING_API_KEY"),
        ("afsk_khong-phai-khoa-that-dau", "INVALID_API_KEY"),
    ],
)
@pytest.mark.asyncio
async def test_bad_keys_are_rejected(session_factory, service, key, expected):
    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(CredentialError) as exc:
                await service.authenticate(session, api_key=key)
    assert exc.value.error_code == expected


@pytest.mark.asyncio
async def test_key_with_right_prefix_but_wrong_secret_is_rejected(session_factory, service):
    """Đoán đúng 8 ký tự đầu không đủ — phần còn lại mới là bí mật."""
    issued = await _issue(session_factory, service)
    forged = issued.api_key[:KEY_PREFIX_LENGTH] + "x" * 30

    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(CredentialError) as exc:
                await service.authenticate(session, api_key=forged)
    assert exc.value.error_code == "INVALID_API_KEY"


@pytest.mark.asyncio
async def test_expired_key_is_rejected(session_factory, service):
    issued = await _issue(session_factory, service, expires_at=datetime.now(UTC) + timedelta(days=30))

    # Đẩy CẢ `created_at` lẫn `expires_at` về quá khứ, thay vì chờ đồng hồ trôi.
    # Phải dời cả hai: `ck_sync_credentials_expires_after_created` cấm hạn dùng
    # nằm trước ngày cấp, nên hạ mỗi `expires_at` sẽ vi phạm ràng buộc — đúng như
    # nó được thiết kế để làm.
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(sync_credentials)
                .where(sync_credentials.c.id == issued.credential_id)
                .values(created_at=now - timedelta(days=2), expires_at=now - timedelta(days=1))
            )

    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(CredentialError) as exc:
                await service.authenticate(session, api_key=issued.api_key)
    assert exc.value.error_code == "EXPIRED_API_KEY"


@pytest.mark.asyncio
async def test_revoked_key_is_rejected(session_factory, service):
    issued = await _issue(session_factory, service)

    async with session_factory() as session:
        async with session.begin():
            assert await service.revoke(session, issued.credential_id) is True

    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(CredentialError) as exc:
                await service.authenticate(session, api_key=issued.api_key)
    assert exc.value.error_code == "REVOKED_API_KEY"


@pytest.mark.asyncio
async def test_revoking_twice_reports_no_change(session_factory, service):
    issued = await _issue(session_factory, service)
    async with session_factory() as session:
        async with session.begin():
            assert await service.revoke(session, issued.credential_id) is True
            assert await service.revoke(session, issued.credential_id) is False


# --- Ranh giới cô lập giữa các instance -------------------------------------


@pytest.mark.asyncio
async def test_key_cannot_write_to_another_instance(session_factory, service):
    """Bất biến cô lập: khoá của instance A không ghi được vào instance B."""
    issued = await _issue(session_factory, service, source_instance_id="synthetic-instance-a")

    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(CredentialError) as exc:
                await service.authenticate(session, api_key=issued.api_key, claimed_instance_id="synthetic-instance-b")
    assert exc.value.error_code == "INSTANCE_MISMATCH"


@pytest.mark.asyncio
async def test_matching_instance_is_allowed(session_factory, service):
    issued = await _issue(session_factory, service, source_instance_id="synthetic-instance-a")

    async with session_factory() as session:
        async with session.begin():
            caller = await service.authenticate(
                session, api_key=issued.api_key, claimed_instance_id="synthetic-instance-a"
            )
    assert caller.source_instance_id == "synthetic-instance-a"


@pytest.mark.asyncio
async def test_mismatch_message_does_not_disclose_the_real_instance(session_factory, service):
    """Thông báo lỗi không được nói khoá này thuộc về đâu — nói ra là giúp dò."""
    issued = await _issue(session_factory, service, source_instance_id="synthetic-secret-instance")

    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(CredentialError) as exc:
                await service.authenticate(session, api_key=issued.api_key, claimed_instance_id="synthetic-other")

    assert "synthetic-secret-instance" not in exc.value.message


# --- Xoay khoá --------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_issues_a_new_key_then_revokes_the_old(session_factory, service):
    """Cấp mới TRƯỚC, thu hồi cũ SAU — không tạo khoảng thời gian không có khoá nào."""
    old = await _issue(session_factory, service)

    async with session_factory() as session:
        async with session.begin():
            new = await service.rotate(session, old.credential_id)

    assert new.api_key != old.api_key
    assert new.source_instance_id == old.source_instance_id

    async with session_factory() as session:
        async with session.begin():
            caller = await service.authenticate(session, api_key=new.api_key)
            assert caller.credential_id == new.credential_id

            with pytest.raises(CredentialError) as exc:
                await service.authenticate(session, api_key=old.api_key)
            assert exc.value.error_code == "REVOKED_API_KEY"


@pytest.mark.asyncio
async def test_rotate_unknown_credential_is_an_error(session_factory, service):
    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(CredentialError) as exc:
                await service.rotate(session, uuid.uuid4())
    assert exc.value.error_code == "UNKNOWN_CREDENTIAL"
