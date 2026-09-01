"""Bootstrap môi trường dev cho toàn bộ P-100 (migration -> seed -> sync credential).

    python -m scripts.bootstrap_dev                    # chạy đủ ba bước
    python -m scripts.bootstrap_dev --dry-run           # chỉ in kế hoạch, không ghi gì
    python -m scripts.bootstrap_dev --print-status      # chỉ đọc trạng thái hiện tại, không ghi gì
    python -m scripts.bootstrap_dev --no-seed
    python -m scripts.bootstrap_dev --no-credential
    python -m scripts.bootstrap_dev --force-reseed --yes
    python -m scripts.bootstrap_dev --rotate-credential --yes
    # trong Docker, --no-seed tự dùng /app/.dev-secrets/minicrm_sync_api_key
    python -m scripts.bootstrap_dev --credential-output-file .dev-secrets/minicrm_sync_api_key

Đây KHÔNG phải một cơ chế mới: mỗi bước gọi thẳng cơ chế CHÍNH THỨC đã có
trong repo — `alembic upgrade head` (giống hệt `docker/entrypoint.sh`),
`scripts.seed_dev.seed()` (idempotent, id tất định, `ON CONFLICT DO UPDATE`),
và `src.services.sync_credentials.SyncCredentialService` (cùng service mà
`scripts/sync_credentials.py` dùng). Script này chỉ ĐIỀU PHỐI ba cơ chế đó
theo đúng thứ tự, và không bao giờ tự sinh logic hash/migrate/seed riêng.

**Vì sao KHÔNG dùng `scripts/migrate.sh` ở đây.** `migrate.sh` là đường CHỦ Ý
CHẬM cho một thay đổi schema có TÍNH TOÁN trên môi trường đã có dữ liệu thật:
sao lưu → migrate → xác minh, gõ tay tên database ở production. Bootstrap dev
thì ngược lại — mục tiêu là một database TRỐNG hoặc gần trống sẵn sàng nhanh.
Dùng đúng cơ chế `alembic upgrade head` mà `entrypoint.sh` đã dùng, không phát
minh đường thứ ba.

**Đường handoff credential mặc định:**

1. `--credential-output-file <path>` (đường CHÍNH cho `scripts/dev-reset.sh`):
   ghi khoá thô — và CHỈ khoá thô, không kèm nhãn/hướng dẫn — vào đúng file đó,
   mode `0600`, không in ra stdout/stderr. Dùng khi `<path>` là một file nằm
   trong thư mục ĐÃ được bind-mount từ `.dev-secrets/` trên host (xem
   `docker-compose.yml`, mount `./.dev-secrets:/app/.dev-secrets` cho service
   `api`) — nhờ đó tiến trình TRONG container ghi được ra HOST mà không cần
   biết gì về `.env`. File đó lại được Compose `secrets:` mount READ-ONLY vào
   `minicrm` tại `/run/secrets/minicrm_sync_api_key`
   (`minicrm/app/config.py::sync_api_key_value` đọc đúng đường này trước).
2. Ghi thẳng vào `.env`/`minicrm/.env` chỉ còn là đường tương thích cũ khi
   `_handoff_credential()` được gọi trực tiếp mà không truyền output file.
   Lệnh bootstrap thông thường không dùng đường này và không in raw key ra log.

AbsorpIQ chỉ lưu `key_prefix`/`key_hash` — không có cách nào lấy lại được
chuỗi thô sau khi `issue()` trả về, nên đường (1) LUÔN được ưu tiên khi có
mặt: nó là đường DUY NHẤT không cần thao tác tay và không phụ thuộc việc
script chạy trên host hay trong container.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from src.config import get_settings
from src.db import get_engine, get_session_factory
from src.models.tables import sync_credentials
from src.services.sync_credentials import CredentialError, SyncCredentialService

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT / "minicrm" / ".env")
ENV_KEY_NAME = "MINICRM_SYNC_API_KEY"

DEFAULT_SOURCE_SYSTEM = "mini_crm"
DEFAULT_SOURCE_INSTANCE_ID = "mini-crm-dev"
DEFAULT_CREDENTIAL_OUTPUT_FILE = REPO_ROOT / ".dev-secrets" / "minicrm_sync_api_key"


class BootstrapError(RuntimeError):
    """Lỗi có chủ đích, đã có thông điệp rõ ràng cho người vận hành."""


def _log(msg: str) -> None:
    print(f"[bootstrap_dev] {msg}")


# --------------------------------------------------------------------------
# Bước 0: bảo vệ môi trường
# --------------------------------------------------------------------------


def _guard_non_production() -> None:
    settings = get_settings()
    if settings.app_env == "production":
        raise BootstrapError(
            "TỪ CHỐI: APP_ENV=production. Script này CHỈ dành cho dev/test cục bộ — "
            "không bao giờ cấp credential hay migrate qua đường này ở production. "
            "Dùng scripts/migrate.sh và quy trình cấp credential production riêng."
        )
    _log(f"app_env={settings.app_env} (không phải production — tiếp tục)")


def _guard_explicit_dev_mode() -> None:
    """Chặt hơn `_guard_non_production`: `--rotate-credential` đòi ĐÚNG development,
    không chấp nhận staging/test — xoay một khoá đang sống là thao tác có tác động
    ngay tới relay MiniCRM thật, không phải thứ nên làm tuỳ tiện ở môi trường chia sẻ."""
    settings = get_settings()
    if settings.app_env != "development":
        raise BootstrapError(
            f"TỪ CHỐI: --rotate-credential đòi APP_ENV=development (đang là "
            f"'{settings.app_env}'). Xoay khoá là thao tác có tác động ngay tới "
            "relay MiniCRM thật — không cho phép ở staging/test/production qua đường này."
        )


# --------------------------------------------------------------------------
# Bước 1: kết nối database
# --------------------------------------------------------------------------


async def _check_db_connectivity() -> None:
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — báo lỗi kết nối rõ ràng, không nuốt
        raise BootstrapError(f"Không kết nối được database (DATABASE_URL): {exc}") from exc
    _log("database connectivity: OK")


# --------------------------------------------------------------------------
# Bước 2: migration
# --------------------------------------------------------------------------


def _alembic_current() -> str | None:
    result = subprocess.run(
        ["alembic", "current"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BootstrapError(f"`alembic current` thất bại:\n{result.stderr}")
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("INFO"):
            return line.split(" ")[0]
    return None


def _alembic_heads() -> list[str]:
    result = subprocess.run(
        ["alembic", "heads"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BootstrapError(f"`alembic heads` thất bại:\n{result.stderr}")
    heads = [
        line.strip().split(" ")[0]
        for line in result.stdout.splitlines()
        if line.strip() and not line.strip().startswith("INFO")
    ]
    return heads


def _run_migration(*, dry_run: bool) -> dict[str, Any]:
    heads = _alembic_heads()
    if len(heads) != 1:
        raise BootstrapError(
            f"Nhiều hơn một Alembic head ({heads}) — không tự migrate. "
            "Giải quyết phân nhánh (merge revision) trước khi bootstrap."
        )
    before = _alembic_current()
    if dry_run:
        _log(f"[dry-run] sẽ chạy: alembic upgrade head (hiện tại: {before}, head: {heads[0]})")
        return {"before": before, "after": before, "head": heads[0], "ran": False}

    result = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BootstrapError(f"`alembic upgrade head` thất bại:\n{result.stderr}")
    after = _alembic_current()
    _log(f"migration: {before} -> {after} (head={heads[0]})")
    return {"before": before, "after": after, "head": heads[0], "ran": True}


# --------------------------------------------------------------------------
# Bước 3: seed dev fixture (AbsorpIQ) — tái dùng scripts.seed_dev nguyên vẹn
# --------------------------------------------------------------------------


async def _run_seed(*, dry_run: bool, force_reseed: bool) -> dict[str, int]:
    from scripts import seed_dev

    names = [name for name, _ in seed_dev.build_dataset()]
    if dry_run:
        try:
            existing = await seed_dev.counts(names)
        except Exception:  # noqa: BLE001 — bảng có thể chưa tồn tại trước migrate
            existing = {name: 0 for name in names}
        total = sum(existing.values())
        _log(f"[dry-run] sẽ chạy: scripts.seed_dev.seed(reset={force_reseed}) — hiện có {total} dòng seed-managed")
        return existing

    written = await seed_dev.seed(reset=force_reseed)
    total = sum(written.values())
    _log(f"seed: {'reset rồi nạp lại' if force_reseed else 'nạp/cập nhật'} — {total} dòng trên {len(written)} bảng")
    return written


# --------------------------------------------------------------------------
# Bước 4: sync credential — tái dùng SyncCredentialService, KHÔNG tự hash
# --------------------------------------------------------------------------


async def _active_credentials(
    session, *, source_system: str, source_instance_id: str
) -> list[dict[str, Any]]:
    """Mọi row ĐANG active (chưa thu hồi, chưa hết hạn) cho đúng identity này.

    Trả về LIST đầy đủ (không chỉ row đầu) — để phát hiện được trường hợp có
    NHIỀU HƠN MỘT credential active cùng lúc, thứ mà chỉ lấy row mới nhất sẽ
    che giấu mất.
    """
    rows = (
        (
            await session.execute(
                sa.select(sync_credentials)
                .where(
                    sync_credentials.c.source_system == source_system,
                    sync_credentials.c.source_instance_id == source_instance_id,
                    sync_credentials.c.revoked_at.is_(None),
                )
                .order_by(sync_credentials.c.created_at.desc())
            )
        )
        .mappings()
        .all()
    )
    now = datetime.now(UTC)
    return [dict(r) for r in rows if r["expires_at"] is None or r["expires_at"] > now]


def _display(p: Path) -> str:
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def _write_env_key(plaintext: str) -> bool:
    """Ghi khoá thô vào ĐÚNG dòng `MINICRM_SYNC_API_KEY=` trong hai file .env đã có.

    Trả về `True` nếu ghi được cả hai file, `False` nếu KHÔNG file nào trong
    `ENV_FILES` tồn tại được ở đường dẫn này — đây là trường hợp BÌNH THƯỜNG
    khi script chạy BÊN TRONG container `api`: `.env`/`minicrm/.env` là file
    Compose chỉ đọc để nội suy biến môi trường LÚC khởi động container, chúng
    KHÔNG được mount vào filesystem của container (`x-backend-base.volumes`
    trong `docker-compose.yml` chỉ mount `./src`/`./alembic`/`./data`/
    `uploads`) — nên `REPO_ROOT` bên trong container trỏ tới `/app`, nơi
    không có `.env` nào cả. Gọi ở đây KHÔNG raise cho trường hợp này — bên gọi
    (`_ensure_credential`) sẽ tự chuyển sang đường an toàn thứ hai: in khoá
    thô ra đúng MỘT lần kèm hướng dẫn dán tay, thay vì đánh mất khoá vừa cấp.

    Vẫn raise cho trường hợp KHÁC: file tồn tại (script đang chạy trên host,
    nơi `.env` có thật) nhưng KHÔNG có đúng một dòng `MINICRM_SYNC_API_KEY=`
    — đó là dấu hiệu cấu trúc file bất thường, lỗi rõ ràng ở đây tốt hơn âm
    thầm ghi thêm một dòng vào một file có thể đang ở cấu trúc khác kỳ vọng.
    """
    if not all(path.exists() for path in ENV_FILES):
        return False

    for path in ENV_FILES:
        content = path.read_text()
        new_content, n = re.subn(
            rf"^{ENV_KEY_NAME}=.*$", f"{ENV_KEY_NAME}={plaintext}", content, count=1, flags=re.MULTILINE
        )
        if n != 1:
            raise BootstrapError(
                f"{path} không có đúng một dòng `{ENV_KEY_NAME}=` — không tự ghi đè. "
                "Kiểm tra file thủ công."
            )
        path.write_text(new_content)

    _log(f"đã ghi khoá mới vào {', '.join(_display(p) for p in ENV_FILES)} (giá trị KHÔNG in ra)")
    return True


def _print_manual_handoff(plaintext: str) -> None:
    """Đường an toàn thứ hai (Option A): in khoá thô ra ĐÚNG MỘT LẦN khi không
    ghi trực tiếp được vào `.env`/`minicrm/.env` (chạy trong container `api`,
    nơi hai file đó không nằm trong filesystem — xem docstring `_write_env_key`).

    Đây KHÔNG phải lỗi — là hành vi ĐÚNG kỳ vọng khi chạy qua
    `docker compose run --rm api python -m scripts.bootstrap_dev`. Credential
    ĐÃ được cấp thật trong `sync_credentials`; chỉ có bước ghi vào file trên
    HOST là không tự làm được từ bên trong container.
    """
    names = ", ".join(_display(p) for p in ENV_FILES)
    print("")
    print("=" * 72)
    print("KHOÁ MỚI — CHỈ HIỆN ĐÚNG MỘT LẦN. Không ghi tự động được vào file .env")
    print(f"trên HOST từ bên trong container. Dán ngay vào dòng {ENV_KEY_NAME}=")
    print(f"trong CẢ HAI file sau (trên host, không phải trong container): {names}")
    print("Sau đó chạy: docker compose up -d --force-recreate minicrm")
    print("=" * 72)
    print(f"{ENV_KEY_NAME}={plaintext}")
    print("=" * 72)
    print("")


def _write_credential_file(path: Path, plaintext: str) -> None:
    """Ghi khoá thô — CHỈ khoá thô, không nhãn, không newline thừa ngoài MỘT
    newline cuối — vào `path`, mode `0600` NGAY TỪ LÚC TẠO (không phải
    `write_text()` rồi `chmod` sau, để không có khoảng hở nào mà file tồn tại
    với quyền mặc định trước khi bị siết lại).

    Từ chối nếu thư mục cha CHƯA tồn tại: đây là dấu hiệu `path` không thật sự
    nằm trong một bind mount đã chuẩn bị sẵn (`.dev-secrets/` trên host, mount
    vào `api` tại `/app/.dev-secrets` — xem `docker-compose.yml`) — tự tạo thư
    mục ở đây có thể ghi nhầm vào một đường dẫn không ai ngờ tới.
    """
    if not path.parent.is_dir():
        raise BootstrapError(
            f"Thư mục cha của --credential-output-file không tồn tại: {path.parent}. "
            "Đường dẫn này phải nằm trong một thư mục ĐÃ được mount sẵn "
            "(vd. .dev-secrets/ trên host, mount vào /app/.dev-secrets trong container `api`)."
        )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(plaintext + "\n")
    finally:
        try:
            os.chmod(path, 0o600)
        except PermissionError:
            # Docker Desktop bind-mounts backed by NTFS do not expose POSIX
            # permission bits. The file was opened with 0600 above on
            # filesystems that support it; keep the Windows bind-mount case
            # usable while still failing on other unexpected errors.
            pass


def _handoff_credential(plaintext: str, *, credential_output_file: Path | None) -> str:
    """Thực hiện đúng MỘT trong ba đường handoff (xem docstring module), trả về
    câu mô tả ngắn để ghép vào log — KHÔNG BAO GIỜ chứa khoá thô."""
    if credential_output_file is not None:
        _write_credential_file(credential_output_file, plaintext)
        return f"đã ghi vào {credential_output_file} (mode 0600, giá trị KHÔNG in ra)"
    if _write_env_key(plaintext):
        return f"đã ghi vào {', '.join(_display(p) for p in ENV_FILES)} (giá trị KHÔNG in ra)"
    _print_manual_handoff(plaintext)
    return "KHÔNG ghi tự động được — đã in ra đúng một lần kèm hướng dẫn dán tay (xem trên)"


async def _ensure_credential(
    *,
    dry_run: bool,
    no_credential: bool,
    rotate: bool,
    yes: bool,
    source_system: str = DEFAULT_SOURCE_SYSTEM,
    source_instance_id: str = DEFAULT_SOURCE_INSTANCE_ID,
    credential_output_file: Path | None = None,
) -> str:
    """Đảm bảo đúng một credential active cho một identity trong dev.

    Trả về một trong: existing | created | reconciled | blocked | skipped.
    Khi có nhiều credential active, credential mới nhất được giữ lại và các
    credential dư bị thu hồi bằng `SyncCredentialService.revoke`.
    """
    if no_credential:
        _log("credential: bỏ qua (--no-credential)")
        return "skipped"

    session_factory = get_session_factory()
    async with session_factory() as session:
        active = await _active_credentials(
            session, source_system=source_system, source_instance_id=source_instance_id
        )

    if len(active) > 1:
        _guard_explicit_dev_mode()
        if dry_run:
            _log(
                f"[dry-run] sẽ giữ credential mới nhất và thu hồi "
                f"{len(active) - 1} credential dư cho ({source_system}, {source_instance_id})"
            )
            return "blocked"

        session_factory = get_session_factory()
        async with session_factory() as session:
            async with session.begin():
                # Re-read in the write transaction so the decision and revocations
                # use the same current active set. `_active_credentials` orders
                # newest first; keep that row and revoke only the extras.
                current = await _active_credentials(
                    session, source_system=source_system, source_instance_id=source_instance_id
                )
                kept = current[0] if current else None
                revoked_count = 0
                if kept is not None:
                    service = SyncCredentialService()
                    for row in current[1:]:
                        if await service.revoke(session, row["id"]):
                            revoked_count += 1

        if kept is None:
            _log(
                f"credential=missing: active set changed while reconciling "
                f"({source_system}, {source_instance_id}); bootstrap will issue a new credential"
            )
        else:
            _log(
                f"WARN: credential=normalized: found multiple active credentials for "
                f"({source_system}, {source_instance_id}); revoked {revoked_count} extra "
                f"credential(s), kept credential_id={kept['id']}"
            )

        if kept is not None:
            return "reconciled"

    if len(active) == 1 and not rotate:
        row = active[0]
        _log(
            f"credential=existing: đã có credential active "
            f"(credential_id={row['id']}, prefix={row['key_prefix']}, "
            f"created_at={row['created_at'].isoformat()}) — GIỮ NGUYÊN, không xoay, không in lại khoá."
        )
        return "existing"

    if len(active) == 1 and rotate:
        if not yes:
            _log("[dry-run cần --yes] SẼ xoay: cấp khoá mới rồi thu hồi credential hiện có. Thêm --yes để thực hiện.")
            return "blocked"
        _guard_explicit_dev_mode()
        if dry_run:
            _log(f"[dry-run] sẽ xoay credential_id={active[0]['id']} (prefix={active[0]['key_prefix']})")
            return "blocked"
        session_factory = get_session_factory()
        async with session_factory() as session:
            async with session.begin():
                issued = await SyncCredentialService().rotate(session, active[0]["id"], label="bootstrap_dev-rotate")
        handoff = _handoff_credential(issued.api_key, credential_output_file=credential_output_file)
        _log(
            f"credential=created (rotated): credential_id={issued.credential_id}, "
            f"key_prefix={issued.key_prefix}. Khoá cũ (credential_id={active[0]['id']}) đã bị thu hồi. {handoff}."
        )
        return "created"

    # Không có credential active nào — cấp mới, KHÔNG cần --yes (đây là bootstrap
    # ban đầu, không phải thay thế một khoá đang sống).
    _guard_explicit_dev_mode()
    if dry_run:
        _log(f"[dry-run] sẽ cấp credential mới cho ({source_system}, {source_instance_id})")
        return "blocked"

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            try:
                issued = await SyncCredentialService().issue(
                    session,
                    source_system=source_system,
                    source_instance_id=source_instance_id,
                    label="bootstrap_dev",
                )
            except CredentialError as exc:
                raise BootstrapError(f"Cấp credential thất bại: {exc.error_code} — {exc.message}") from exc
    handoff = _handoff_credential(issued.api_key, credential_output_file=credential_output_file)
    _log(f"credential=created: credential_id={issued.credential_id}, key_prefix={issued.key_prefix}. {handoff}.")
    return "created"


# --------------------------------------------------------------------------
# Điều phối
# --------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    try:
        _guard_non_production()

        if args.print_status:
            await _check_db_connectivity()
            current = _alembic_current()
            heads = _alembic_heads()
            session_factory = get_session_factory()
            async with session_factory() as session:
                active = await _active_credentials(
                    session, source_system=DEFAULT_SOURCE_SYSTEM, source_instance_id=DEFAULT_SOURCE_INSTANCE_ID
                )
            print("")
            print("=== bootstrap_dev status (read-only) ===")
            print(f"  alembic current : {current}")
            print(f"  alembic heads   : {heads}")
            print(f"  credential      : {len(active)} active row(s) for ({DEFAULT_SOURCE_SYSTEM}, {DEFAULT_SOURCE_INSTANCE_ID})")
            for row in active:
                print(f"    - credential_id={row['id']} key_prefix={row['key_prefix']} created_at={row['created_at'].isoformat()}")
            print("")
            return 0

        if args.force_reseed and not args.yes:
            raise BootstrapError("--force-reseed đòi --yes (nó xoá rồi nạp lại toàn bộ dòng seed-managed).")

        credential_output_file: Path | None = None
        if args.credential_output_file:
            # Ghi khoá thô ra một file cụ thể là thao tác nhạy cảm hơn "chỉ đọc" —
            # siết chặt hơn `_guard_non_production` (chỉ chặn production), khớp
            # yêu cầu "chỉ cho phép ở development" cho riêng cờ này.
            _guard_explicit_dev_mode()
            credential_output_file = Path(args.credential_output_file)
        elif not args.no_credential:
            # Docker bind-mounts the host `.dev-secrets` directory at this path.
            # Use it by default so a newly issued key never falls back to logs.
            _guard_explicit_dev_mode()
            credential_output_file = DEFAULT_CREDENTIAL_OUTPUT_FILE

        await _check_db_connectivity()
        migration = _run_migration(dry_run=args.dry_run)

        seed_result: dict[str, int] = {}
        if not args.no_seed:
            seed_result = await _run_seed(dry_run=args.dry_run, force_reseed=args.force_reseed)
        else:
            _log("seed: bỏ qua (--no-seed)")

        credential_status = await _ensure_credential(
            dry_run=args.dry_run,
            no_credential=args.no_credential,
            rotate=args.rotate_credential,
            yes=args.yes,
            credential_output_file=credential_output_file,
        )

        print("")
        print("=== bootstrap_dev summary ===")
        print(f"  mode        : {'dry-run (không ghi gì)' if args.dry_run else 'thực thi'}")
        print(f"  migration   : {migration['before']} -> {migration['after']} (head={migration['head']})")
        print(f"  seed        : {sum(seed_result.values()) if seed_result else 0} dòng trên {len(seed_result)} bảng")
        print(f"  credential  : {credential_status}")
        print("")
        return 0
    except BootstrapError as exc:
        print(f"\n[bootstrap_dev] LỖI: {exc}\n", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in kế hoạch, không ghi database hay file nào.")
    parser.add_argument("--no-seed", action="store_true", help="Bỏ qua bước seed dev fixture.")
    parser.add_argument("--no-credential", action="store_true", help="Bỏ qua bước bảo đảm sync credential.")
    parser.add_argument(
        "--force-reseed", action="store_true", help="Xoá các dòng seed-managed rồi nạp lại. Đòi --yes."
    )
    parser.add_argument(
        "--rotate-credential",
        action="store_true",
        help="Xoay credential đang active thay vì giữ nguyên. Đòi --yes và APP_ENV=development.",
    )
    parser.add_argument(
        "--credential-output-file",
        default=None,
        metavar="PATH",
        help=(
            "Ghi khoá thô (nếu vừa cấp/xoay) ra CHÍNH XÁC file này, mode 0600, "
            "KHÔNG in ra stdout. Thư mục cha PHẢI đã tồn tại (bind mount từ "
            ".dev-secrets/ trên host). Mặc định là "
            "/app/.dev-secrets/minicrm_sync_api_key trong Docker. Chỉ cho phép "
            "khi APP_ENV=development."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Xác nhận cho các thao tác có tác động (reseed/rotate).")
    parser.add_argument(
        "--print-status", action="store_true", help="Chỉ đọc và in trạng thái hiện tại, không ghi gì, bỏ qua các cờ khác."
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
