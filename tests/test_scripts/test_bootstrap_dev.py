"""`scripts/bootstrap_dev.py`: điều phối migration/seed/credential cho dev bootstrap.

Vòng đời credential THẬT đã kiểm ở `tests/test_services/test_sync_credentials.py`
và `tests/test_scripts/test_sync_credentials_cli.py`. Module này chỉ kiểm phần
ĐIỀU PHỐI mà `bootstrap_dev.py` thêm vào: từ chối production, credential active
được giữ nguyên (không xoay ngầm), không có credential thì cấp đúng một cái,
nhiều credential active thì giữ credential mới nhất và thu hồi phần dư, và khoá thô không bao
giờ lọt ra stdout hay bị ghi vào file khác ngoài hai file `.env` được trỏ tới
tường minh.

Chạy: TEST_TARGET=tests/test_scripts/test_bootstrap_dev.py bash scripts/test_db.sh -q
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from scripts import bootstrap_dev
from src.models.tables import sync_credentials
from src.services.sync_credentials import SyncCredentialService

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

SYSTEM = "mini_crm"
INSTANCE = "synthetic-bootstrap-dev-instance"


def _sessions():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    return _DisposingSession(engine)


class _DisposingSession:
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


@pytest_asyncio.fixture(autouse=True)
async def db_env(monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("APP_ENV", "development")
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe():
        async with _sessions() as session:
            async with session.begin():
                await session.execute(
                    sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id == INSTANCE)
                )

    await wipe()
    yield
    await wipe()
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


async def _row_count() -> int:
    async with _sessions() as session:
        return await session.scalar(
            sa.select(sa.func.count())
            .select_from(sync_credentials)
            .where(sync_credentials.c.source_instance_id == INSTANCE)
        )


async def _active_row_count() -> int:
    async with _sessions() as session:
        return await session.scalar(
            sa.select(sa.func.count())
            .select_from(sync_credentials)
            .where(
                sync_credentials.c.source_instance_id == INSTANCE,
                sync_credentials.c.revoked_at.is_(None),
            )
        )


# --- production guard --------------------------------------------------------


async def test_guard_refuses_when_app_env_is_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    from src.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(bootstrap_dev.BootstrapError, match="production"):
        bootstrap_dev._guard_non_production()

    get_settings.cache_clear()


async def test_rotate_guard_refuses_staging_even_though_not_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    from src.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(bootstrap_dev.BootstrapError, match="development"):
        bootstrap_dev._guard_explicit_dev_mode()

    get_settings.cache_clear()


async def test_initial_credential_refuses_outside_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    from src.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(bootstrap_dev.BootstrapError, match="development"):
        await bootstrap_dev._ensure_credential(
            dry_run=False,
            no_credential=False,
            rotate=False,
            yes=False,
            source_system=SYSTEM,
            source_instance_id=INSTANCE,
        )

    get_settings.cache_clear()


# --- credential: cấp mới khi trống -------------------------------------------


async def test_ensure_credential_issues_exactly_one_when_none_exists(capsys, monkeypatch):
    captured: dict[str, str] = {}
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: captured.setdefault("key", plaintext))

    status = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert status == "created"
    assert await _active_row_count() == 1
    out = capsys.readouterr().out
    # key_prefix (vd. "afsk_xyz") là định danh KHÔNG bí mật theo đúng thiết kế của
    # SyncCredentialService (dùng để nhận diện khoá trong log) — được phép xuất
    # hiện. Chỉ khoá THÔ đầy đủ (48 ký tự) mới là thứ tuyệt đối không được lọt ra.
    assert captured["key"] not in out


async def test_ensure_credential_never_prints_the_plaintext_key(capsys, monkeypatch):
    captured: dict[str, str] = {}
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: captured.setdefault("key", plaintext))

    await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert "key" in captured and len(captured["key"]) > 0
    out = capsys.readouterr().out
    assert captured["key"] not in out


# --- credential: giữ nguyên cái đang active -----------------------------------


async def test_ensure_credential_preserves_an_existing_active_credential(monkeypatch):
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: True)
    async with _sessions() as session:
        async with session.begin():
            await SyncCredentialService().issue(session, source_system=SYSTEM, source_instance_id=INSTANCE, label="t")

    status = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert status == "existing"
    assert await _row_count() == 1, "credential đang active bị nhân đôi thay vì được giữ nguyên"


async def test_repeated_bootstrap_is_idempotent_no_duplicate(monkeypatch):
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: True)

    first = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )
    second = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )
    third = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert (first, second, third) == ("created", "existing", "existing")
    assert await _active_row_count() == 1


# --- credential: nhiều active -> giữ một, thu hồi phần dư ---------------------


async def test_multiple_active_credentials_are_reconciled(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: True)
    async with _sessions() as session:
        async with session.begin():
            await SyncCredentialService().issue(session, source_system=SYSTEM, source_instance_id=INSTANCE, label="a")
    async with _sessions() as session:
        async with session.begin():
            await SyncCredentialService().issue(session, source_system=SYSTEM, source_instance_id=INSTANCE, label="b")
    assert await _active_row_count() == 2, "tiền đề test: phải có sẵn hai credential active"

    status = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert status == "reconciled"
    assert await _active_row_count() == 1, "sau reconcile phải còn đúng một credential active"
    async with _sessions() as session:
        rows = (
            (
                await session.execute(
                    sa.select(sync_credentials)
                    .where(sync_credentials.c.source_instance_id == INSTANCE)
                    .order_by(sync_credentials.c.created_at.desc())
                )
            )
            .mappings()
            .all()
        )
    assert rows[0]["revoked_at"] is None, "credential mới nhất phải được giữ lại"
    assert rows[1]["revoked_at"] is not None, "credential dư phải bị thu hồi"
    out = capsys.readouterr().out
    assert "normalized" in out.lower()
    assert "warn" in out.lower()
    assert "revoked 1 extra" in out.lower()


# --- credential: --no-credential bỏ qua hoàn toàn -----------------------------


async def test_no_credential_flag_skips_entirely():
    status = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=True, rotate=True, yes=True, source_system=SYSTEM, source_instance_id=INSTANCE
    )
    assert status == "skipped"
    assert await _row_count() == 0


# --- credential: dry-run không ghi gì -----------------------------------------


async def test_dry_run_issues_no_credential(monkeypatch):
    status = await bootstrap_dev._ensure_credential(
        dry_run=True, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )
    assert status == "blocked"
    assert await _row_count() == 0


# --- credential: rotate đòi --yes và đúng dev mode ----------------------------


async def test_rotate_without_yes_is_blocked_and_preserves_old_key(monkeypatch):
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: True)
    async with _sessions() as session:
        async with session.begin():
            await SyncCredentialService().issue(session, source_system=SYSTEM, source_instance_id=INSTANCE, label="t")

    status = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=True, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert status == "blocked"
    assert await _active_row_count() == 1, "rotate không --yes đã đổi trạng thái DB"


async def test_rotate_with_yes_revokes_old_and_creates_new(monkeypatch):
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: True)
    async with _sessions() as session:
        async with session.begin():
            issued = await SyncCredentialService().issue(
                session, source_system=SYSTEM, source_instance_id=INSTANCE, label="t"
            )

    status = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=True, yes=True, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert status == "created"
    assert await _active_row_count() == 1, "sau rotate phải còn đúng MỘT credential active (cái mới)"
    async with _sessions() as session:
        old_row = (
            (await session.execute(sa.select(sync_credentials).where(sync_credentials.c.id == issued.credential_id)))
            .mappings()
            .one()
        )
    assert old_row["revoked_at"] is not None, "credential cũ phải bị thu hồi sau rotate"


# --- env-file write: không đụng file ngoài danh sách tường minh ---------------


async def test_write_env_key_only_touches_the_named_key_and_files(tmp_path, monkeypatch):
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    env_a.write_text("OTHER_VAR=unchanged\nMINICRM_SYNC_API_KEY=old-placeholder\nTRAILING=kept\n")
    env_b.write_text("MINICRM_SYNC_API_KEY=old-placeholder-2\n")
    monkeypatch.setattr(bootstrap_dev, "ENV_FILES", (env_a, env_b))

    bootstrap_dev._write_env_key("new-plaintext-value")

    content_a = env_a.read_text()
    assert "MINICRM_SYNC_API_KEY=new-plaintext-value" in content_a
    assert "OTHER_VAR=unchanged" in content_a
    assert "TRAILING=kept" in content_a
    assert "old-placeholder" not in content_a
    assert "MINICRM_SYNC_API_KEY=new-plaintext-value" in env_b.read_text()


async def test_write_env_key_refuses_when_line_is_missing(tmp_path, monkeypatch):
    env_a = tmp_path / "a.env"
    env_a.write_text("NOTHING_HERE=1\n")
    monkeypatch.setattr(bootstrap_dev, "ENV_FILES", (env_a,))

    with pytest.raises(bootstrap_dev.BootstrapError, match="MINICRM_SYNC_API_KEY"):
        bootstrap_dev._write_env_key("new-plaintext-value")


async def test_write_env_key_returns_false_without_raising_when_files_do_not_exist(tmp_path, monkeypatch):
    """Đây là trạng thái BÌNH THƯỜNG khi chạy bên trong container `api`: `.env`
    không nằm trong filesystem của container (không được mount) — `.env`/
    `minicrm/.env` chỉ tồn tại trên HOST. `_write_env_key` phải trả `False`
    một cách êm ái để bên gọi chuyển sang in tay, KHÔNG được raise (raise ở
    đây sẽ làm mất khoá vừa cấp vì exception xảy ra SAU khi credential đã
    commit vào DB — đây chính là lỗi thật đã xảy ra khi chạy
    `docker compose run --rm api python -m scripts.bootstrap_dev --yes`)."""
    missing_a = tmp_path / "does-not-exist" / "a.env"
    missing_b = tmp_path / "does-not-exist" / "b.env"
    monkeypatch.setattr(bootstrap_dev, "ENV_FILES", (missing_a, missing_b))

    result = bootstrap_dev._write_env_key("new-plaintext-value")

    assert result is False


async def test_ensure_credential_falls_back_to_manual_handoff_when_env_unreachable(monkeypatch, capsys):
    """Mô phỏng ĐÚNG kịch bản thật: script chạy trong container, `.env` không
    reachable. Credential vẫn phải được cấp thật (không mất), và khoá thô
    phải được in ra ĐÚNG MỘT LẦN qua đường in tay thay vì bị nuốt mất bởi một
    exception."""
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: False)

    status = await bootstrap_dev._ensure_credential(
        dry_run=False, no_credential=False, rotate=False, yes=False, source_system=SYSTEM, source_instance_id=INSTANCE
    )

    assert status == "created"
    assert await _active_row_count() == 1, "credential phải vẫn được cấp thật dù không ghi được vào .env"
    out = capsys.readouterr().out
    assert "MINICRM_SYNC_API_KEY=" in out, "phải in khoá thô ra qua đường in tay khi ghi file thất bại"
    assert "CHỈ HIỆN ĐÚNG MỘT LẦN" in out


# --- credential-output-file: đường handoff chính cho dev-reset.sh ------------


async def test_write_credential_file_creates_with_mode_0600(tmp_path):
    parent = tmp_path / "dev-secrets"
    parent.mkdir()
    target = parent / "minicrm_sync_api_key"

    bootstrap_dev._write_credential_file(target, "afsk_realvalue")

    assert target.read_text() == "afsk_realvalue\n"
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600, f"expected mode 0600, got {oct(mode)}"


async def test_write_credential_file_refuses_when_parent_directory_missing(tmp_path):
    target = tmp_path / "does-not-exist" / "minicrm_sync_api_key"

    with pytest.raises(bootstrap_dev.BootstrapError, match="Thư mục cha"):
        bootstrap_dev._write_credential_file(target, "afsk_realvalue")


async def test_handoff_credential_prefers_the_output_file_over_env_write(tmp_path, monkeypatch):
    parent = tmp_path / "dev-secrets"
    parent.mkdir()
    target = parent / "minicrm_sync_api_key"
    write_env_called = []
    monkeypatch.setattr(bootstrap_dev, "_write_env_key", lambda plaintext: write_env_called.append(plaintext) or True)

    bootstrap_dev._handoff_credential("afsk_realvalue", credential_output_file=target)

    assert target.read_text() == "afsk_realvalue\n"
    assert write_env_called == [], "--credential-output-file phải BỎ QUA đường .env, không thử cả hai"


async def test_ensure_credential_writes_to_output_file_when_issuing_new(tmp_path, monkeypatch, capsys):
    parent = tmp_path / "dev-secrets"
    parent.mkdir()
    target = parent / "minicrm_sync_api_key"

    status = await bootstrap_dev._ensure_credential(
        dry_run=False,
        no_credential=False,
        rotate=False,
        yes=False,
        source_system=SYSTEM,
        source_instance_id=INSTANCE,
        credential_output_file=target,
    )

    assert status == "created"
    assert await _active_row_count() == 1
    written = target.read_text().strip()
    assert written.startswith("afsk_")
    assert len(written) == 48
    out = capsys.readouterr().out
    assert written not in out, "khoá thô không được xuất hiện trong stdout khi dùng --credential-output-file"


async def test_ensure_credential_does_not_touch_output_file_when_credential_already_exists(tmp_path, monkeypatch):
    parent = tmp_path / "dev-secrets"
    parent.mkdir()
    target = parent / "minicrm_sync_api_key"
    target.write_text("afsk_previous_value_untouched\n")
    os_stat_before = target.stat().st_mtime_ns

    async with _sessions() as session:
        async with session.begin():
            await SyncCredentialService().issue(session, source_system=SYSTEM, source_instance_id=INSTANCE, label="t")

    status = await bootstrap_dev._ensure_credential(
        dry_run=False,
        no_credential=False,
        rotate=False,
        yes=False,
        source_system=SYSTEM,
        source_instance_id=INSTANCE,
        credential_output_file=target,
    )

    assert status == "existing"
    assert target.stat().st_mtime_ns == os_stat_before, "credential=existing không được ghi đè file đã có"
    assert target.read_text() == "afsk_previous_value_untouched\n"


# --- migration: nhiều head thì từ chối tự migrate -----------------------------


async def test_run_migration_refuses_when_alembic_reports_multiple_heads(monkeypatch):
    monkeypatch.setattr(bootstrap_dev, "_alembic_heads", lambda: ["revA", "revB"])

    with pytest.raises(bootstrap_dev.BootstrapError, match="head"):
        bootstrap_dev._run_migration(dry_run=True)
