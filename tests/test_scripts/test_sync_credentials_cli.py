"""CLI `scripts/sync_credentials.py`: bootstrap khoá API đồng bộ cho dev local.

Vòng đời khoá THẬT đã được kiểm ở `tests/test_services/test_sync_credentials.py`
(qua `SyncCredentialService` trực tiếp). Module này kiểm phần CHỈ CLI thêm vào:
từ chối cấp trùng theo mặc định, chế độ dry-run không đổi gì trong DB, `--yes`
mới thực sự thực hiện, và không có đường nào (kể cả `list`) làm lộ khoá thô.

Chạy: TEST_TARGET=tests/test_scripts/test_sync_credentials_cli.py bash scripts/test_db.sh
"""

from __future__ import annotations

import argparse
import os
import uuid
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from scripts.sync_credentials import cmd_issue, cmd_list, cmd_revoke, cmd_rotate
from src.models.tables import sync_credentials

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _refuses_to_wipe(url: str | None) -> str:
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối chạy trên database '{name}' vì tên không kết thúc bằng '_test'."
    return ""


_SKIP_REASON = _refuses_to_wipe(TEST_DATABASE_URL)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or ""),
]

INSTANCE = "synthetic-cli-instance"


@pytest_asyncio.fixture(autouse=True)
async def db_env(monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe():
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id.like("synthetic-cli%"))
                    )
        finally:
            await engine.dispose()

    await wipe()
    yield
    await wipe()
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "source_system": "mini_crm",
        "source_instance_id": None,
        "label": "",
        "expires_in_days": None,
        "rotate": False,
        "yes": False,
        "credential_id": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


async def _row_count() -> int:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            return await session.scalar(
                sa.select(sa.func.count())
                .select_from(sync_credentials)
                .where(sync_credentials.c.source_instance_id == INSTANCE)
            )
    finally:
        await engine.dispose()


async def _get_row() -> dict:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            row = (
                (
                    await session.execute(
                        sa.select(sync_credentials).where(sync_credentials.c.source_instance_id == INSTANCE)
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)
    finally:
        await engine.dispose()


# --- issue --------------------------------------------------------------


async def test_issue_creates_exactly_one_credential(capsys):
    code = await cmd_issue(_ns(source_instance_id=INSTANCE))

    assert code == 0
    assert await _row_count() == 1
    out = capsys.readouterr().out
    assert "api_key" in out
    assert "CHỈ HIỆN ĐÚNG MỘT LẦN" in out


async def test_issue_twice_without_rotate_refuses_and_creates_no_second_row(capsys):
    await cmd_issue(_ns(source_instance_id=INSTANCE))

    code = await cmd_issue(_ns(source_instance_id=INSTANCE))

    assert code == 1
    assert await _row_count() == 1, "issue lần hai đã tạo thêm một khoá active"


async def test_issue_with_rotate_but_no_yes_is_a_dry_run(capsys):
    first = await _issue_and_capture_key(capsys)

    code = await cmd_issue(_ns(source_instance_id=INSTANCE, rotate=True, yes=False))

    assert code == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert await _row_count() == 1, "dry-run đã đổi trạng thái DB"
    row = await _get_row()
    assert row["revoked_at"] is None, "dry-run đã thu hồi khoá cũ"
    assert first not in out


async def test_issue_with_rotate_and_yes_replaces_the_key(capsys):
    await _issue_and_capture_key(capsys)
    old_row = await _get_row()

    code = await cmd_issue(_ns(source_instance_id=INSTANCE, rotate=True, yes=True))

    assert code == 0
    out = capsys.readouterr().out
    assert "api_key" in out

    async with _sessions() as session:
        rows = (
            (
                await session.execute(
                    sa.select(sync_credentials).where(sync_credentials.c.source_instance_id == INSTANCE)
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 2, "rotate phải để lại đúng hai dòng: khoá cũ (thu hồi) + khoá mới"
    revoked = [r for r in rows if r["id"] == old_row["id"]]
    assert revoked[0]["revoked_at"] is not None


# --- rotate ---------------------------------------------------------------


async def test_rotate_by_source_instance_id_without_yes_is_a_dry_run(capsys):
    await _issue_and_capture_key(capsys)

    code = await cmd_rotate(_ns(source_instance_id=INSTANCE, yes=False))

    assert code == 0
    assert "DRY-RUN" in capsys.readouterr().out
    row = await _get_row()
    assert row["revoked_at"] is None


async def test_rotate_by_source_instance_id_with_yes_executes(capsys):
    await _issue_and_capture_key(capsys)
    old_row = await _get_row()

    code = await cmd_rotate(_ns(source_instance_id=INSTANCE, yes=True))

    assert code == 0
    async with _sessions() as session:
        old_after = (
            await session.execute(sa.select(sync_credentials).where(sync_credentials.c.id == old_row["id"]))
        ).mappings().one()
    assert old_after["revoked_at"] is not None


async def test_rotate_unknown_instance_is_an_error(capsys):
    code = await cmd_rotate(_ns(source_instance_id="synthetic-cli-does-not-exist", yes=True))
    assert code == 1


async def test_rotate_requires_credential_id_or_instance(capsys):
    code = await cmd_rotate(_ns(yes=True))
    assert code == 1


# --- revoke -----------------------------------------------------------------


async def test_revoke_without_yes_is_a_dry_run(capsys):
    await _issue_and_capture_key(capsys)
    row = await _get_row()

    code = await cmd_revoke(_ns(credential_id=str(row["id"]), yes=False))

    assert code == 0
    assert "DRY-RUN" in capsys.readouterr().out
    after = await _get_row()
    assert after["revoked_at"] is None


async def test_revoke_with_yes_executes(capsys):
    await _issue_and_capture_key(capsys)
    row = await _get_row()

    code = await cmd_revoke(_ns(credential_id=str(row["id"]), yes=True))

    assert code == 0
    after = await _get_row()
    assert after["revoked_at"] is not None


async def test_revoke_twice_is_reported_as_a_no_op(capsys):
    await _issue_and_capture_key(capsys)
    row = await _get_row()

    await cmd_revoke(_ns(credential_id=str(row["id"]), yes=True))
    code = await cmd_revoke(_ns(credential_id=str(row["id"]), yes=True))

    assert code == 1


async def test_revoke_unknown_credential_id_is_an_error(capsys):
    code = await cmd_revoke(_ns(credential_id=str(uuid.uuid4()), yes=True))
    assert code == 1


# --- list: không bao giờ lộ khoá thô -----------------------------------------


async def test_list_never_prints_the_raw_key(capsys):
    raw_key = await _issue_and_capture_key(capsys)

    code = await cmd_list(_ns(source_instance_id=INSTANCE))

    assert code == 0
    out = capsys.readouterr().out
    assert raw_key not in out
    assert "afsk_" in out, "phải hiện được key_prefix để nhận diện khoá"


async def test_list_shows_status_transitions(capsys):
    await _issue_and_capture_key(capsys)
    row = await _get_row()
    await cmd_revoke(_ns(credential_id=str(row["id"]), yes=True))
    capsys.readouterr()

    await cmd_list(_ns(source_instance_id=INSTANCE))

    out = capsys.readouterr().out
    assert "revoked" in out


# --- trợ giúp -----------------------------------------------------------------


def _sessions():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    return _DisposingSession(engine)


class _DisposingSession:
    """Session async tự đóng engine riêng của nó — tránh rò kết nối giữa test."""

    def __init__(self, engine):
        self._engine = engine
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        self._session = None

    async def __aenter__(self):
        self._session = self._sessionmaker()
        return await self._session.__aenter__()

    async def __aexit__(self, *exc):
        await self._session.__aexit__(*exc)
        await self._engine.dispose()


async def _issue_and_capture_key(capsys) -> str:
    await cmd_issue(_ns(source_instance_id=INSTANCE))
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.strip().startswith("api_key"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("không tìm thấy dòng api_key trong output của issue")
