"""Vòng đời khoá API đồng bộ (Phase 3/4): cấp, xoay, thu hồi, liệt kê — dòng lệnh.

    python -m scripts.sync_credentials issue \\
        --source-system mini_crm --source-instance-id mini-crm-dev
    python -m scripts.sync_credentials issue \\
        --source-system mini_crm --source-instance-id mini-crm-dev --rotate --yes
    python -m scripts.sync_credentials rotate --source-instance-id mini-crm-dev --yes
    python -m scripts.sync_credentials revoke --credential-id <uuid> --yes
    python -m scripts.sync_credentials list --source-instance-id mini-crm-dev

Vì sao cần script này thay vì gọi `SyncCredentialService` bằng tay mỗi lần: cấp
khoá là thao tác THỦ CÔNG, LOCAL/DEV — không có chỗ nào trong ứng dụng tự cấp
khoá lúc khởi động (một hành vi như thế sẽ tạo khoá mới mỗi lần container restart,
và không ai biết khoá nào đang thật sự được dùng). Script này là con đường TƯỜNG
MINH duy nhất để bootstrap một khoá cho môi trường dev mới.

Bốn nguyên tắc, khớp `src/services/sync_credentials.py`:

1. **Khoá thô chỉ in ra ĐÚNG MỘT LẦN**, ra stdout, kèm cảnh báo sẽ không hiện lại.
   Không log, không ghi file, không trả về lần thứ hai bằng bất kỳ lệnh nào khác
   (kể cả `list`) — `list` chỉ hiện metadata không bí mật (fingerprint = prefix).

2. **`issue` là THAO TÁC AN TOÀN theo mặc định.** Instance đã có khoá active mà
   không truyền `--rotate` → từ chối, không tạo thêm khoá, thoát mã khác 0. Đây
   là "idempotent" theo nghĩa: chạy `issue` nhiều lần không âm thầm nhân bản khoá
   active cho cùng một instance.

3. **`revoke`/`rotate` đòi `--yes` tường minh.** Cả hai đều có hiệu lực ngay lập
   tức trên một khoá đang có thể đang được dùng thật (relay MiniCRM) — không có
   cờ, lệnh chỉ IN RA nó SẼ làm gì (chế độ dry-run mặc định) rồi dừng.

4. **Không tự kết nối bằng DSN rời.** Dùng `src.db.get_session_factory()` để
   luôn trỏ đúng database mà ứng dụng đang trỏ (tôn trọng `DATABASE_URL`/
   `TEST_DATABASE_URL` đã export ở môi trường gọi lệnh).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from src.db import get_session_factory
from src.models.tables import sync_credentials
from src.services.sync_credentials import CredentialError, SyncCredentialService


def _status(row: dict) -> str:
    now = datetime.now(UTC)
    if row["revoked_at"] is not None:
        return "revoked"
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return "expired"
    return "active"


async def _find_active(session, *, source_system: str, source_instance_id: str) -> dict | None:
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
    for row in rows:
        if row["expires_at"] is None or row["expires_at"] > now:
            return dict(row)
    return None


async def cmd_issue(args: argparse.Namespace) -> int:
    session_factory = get_session_factory()
    expires_at = datetime.now(UTC) + timedelta(days=args.expires_in_days) if args.expires_in_days else None

    async with session_factory() as session:
        async with session.begin():
            existing = await _find_active(
                session, source_system=args.source_system, source_instance_id=args.source_instance_id
            )

            if existing is not None and not args.rotate:
                print(
                    f"Instance '{args.source_instance_id}' ({args.source_system}) đã có khoá active "
                    f"(credential_id={existing['id']}, prefix={existing['key_prefix']}, "
                    f"created_at={existing['created_at'].isoformat()}). Không cấp thêm — dùng --rotate "
                    "để thay thế nó một cách tường minh, hoặc `rotate --source-instance-id ...`.",
                    file=sys.stderr,
                )
                return 1

            if existing is not None and args.rotate:
                if not args.yes:
                    print(
                        f"[DRY-RUN] SẼ xoay khoá: cấp mới cho '{args.source_instance_id}', "
                        f"rồi thu hồi credential_id={existing['id']} (prefix={existing['key_prefix']}). "
                        "Thêm --yes để thực hiện thật.",
                    )
                    return 0
                issued = await SyncCredentialService().rotate(session, existing["id"], label=args.label or "")
                print(f"Đã xoay khoá. Khoá cũ credential_id={existing['id']} đã bị thu hồi.")
            else:
                issued = await SyncCredentialService().issue(
                    session,
                    source_system=args.source_system,
                    source_instance_id=args.source_instance_id,
                    label=args.label or "",
                    expires_at=expires_at,
                )

    _print_issued(issued)
    return 0


async def cmd_rotate(args: argparse.Namespace) -> int:
    session_factory = get_session_factory()

    async with session_factory() as session:
        async with session.begin():
            if args.credential_id:
                existing_id = uuid.UUID(args.credential_id)
                existing = (
                    (await session.execute(sa.select(sync_credentials).where(sync_credentials.c.id == existing_id)))
                    .mappings()
                    .one_or_none()
                )
                if existing is None:
                    print(f"Không tìm thấy credential_id={existing_id}", file=sys.stderr)
                    return 1
            else:
                if not args.source_instance_id:
                    print("Cần --credential-id HOẶC --source-instance-id (kèm --source-system nếu khác mini_crm)", file=sys.stderr)
                    return 1
                existing = await _find_active(
                    session, source_system=args.source_system, source_instance_id=args.source_instance_id
                )
                if existing is None:
                    print(f"Không có khoá active nào cho instance '{args.source_instance_id}'.", file=sys.stderr)
                    return 1

            if not args.yes:
                print(
                    f"[DRY-RUN] SẼ cấp khoá mới cho instance '{existing['source_instance_id']}', "
                    f"rồi thu hồi credential_id={existing['id']} (prefix={existing['key_prefix']}). "
                    "Thêm --yes để thực hiện thật."
                )
                return 0

            issued = await SyncCredentialService().rotate(session, existing["id"], label=args.label or "")

    print(f"Đã xoay khoá. Khoá cũ credential_id={existing['id']} đã bị thu hồi.")
    _print_issued(issued)
    return 0


async def cmd_revoke(args: argparse.Namespace) -> int:
    session_factory = get_session_factory()
    credential_id = uuid.UUID(args.credential_id)

    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(sync_credentials).where(sync_credentials.c.id == credential_id)))
            .mappings()
            .one_or_none()
        )
    if row is None:
        print(f"Không tìm thấy credential_id={credential_id}", file=sys.stderr)
        return 1

    if not args.yes:
        print(
            f"[DRY-RUN] SẼ thu hồi credential_id={credential_id} "
            f"(instance={row['source_instance_id']}, prefix={row['key_prefix']}, "
            f"trạng thái hiện tại={_status(dict(row))}). Thêm --yes để thực hiện thật."
        )
        return 0

    async with session_factory() as session:
        async with session.begin():
            revoked = await SyncCredentialService().revoke(session, credential_id)

    if revoked:
        print(f"Đã thu hồi credential_id={credential_id}.")
        return 0
    print(f"credential_id={credential_id} đã bị thu hồi từ trước — không có gì để làm.", file=sys.stderr)
    return 1


async def cmd_list(args: argparse.Namespace) -> int:
    session_factory = get_session_factory()
    conditions = []
    if args.source_instance_id:
        conditions.append(sync_credentials.c.source_instance_id == args.source_instance_id)
    if args.source_system:
        conditions.append(sync_credentials.c.source_system == args.source_system)

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    sa.select(sync_credentials).where(*conditions).order_by(sync_credentials.c.created_at.desc())
                )
            )
            .mappings()
            .all()
        )

    if not rows:
        print("Không có khoá nào khớp bộ lọc.")
        return 0

    header = f"{'credential_id':<38} {'instance':<24} {'prefix':<10} {'status':<8} {'created_at':<26} {'last_used_at'}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{str(row['id']):<38} {row['source_instance_id']:<24} {row['key_prefix']:<10} "
            f"{_status(dict(row)):<8} {row['created_at'].isoformat():<26} "
            f"{row['last_used_at'].isoformat() if row['last_used_at'] else '-'}"
        )
    return 0


def _print_issued(issued) -> None:
    print("")
    print("=" * 72)
    print("KHOÁ MỚI — CHỈ HIỆN ĐÚNG MỘT LẦN. Dán ngay vào .env, KHÔNG commit, KHÔNG log lại.")
    print("=" * 72)
    print(f"  credential_id     : {issued.credential_id}")
    print(f"  source_system     : {issued.source_system}")
    print(f"  source_instance_id: {issued.source_instance_id}")
    print(f"  key_prefix        : {issued.key_prefix}  (dùng để nhận diện khoá trong log/list, KHÔNG bí mật)")
    print(f"  expires_at        : {issued.expires_at.isoformat() if issued.expires_at else '(không hết hạn)'}")
    print(f"  api_key           : {issued.api_key}")
    print("=" * 72)
    print("")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_issue = sub.add_parser("issue", help="Cấp khoá mới cho một source_instance_id")
    p_issue.add_argument("--source-system", default="mini_crm")
    p_issue.add_argument("--source-instance-id", required=True)
    p_issue.add_argument("--label", default="")
    p_issue.add_argument("--expires-in-days", type=int, default=None)
    p_issue.add_argument(
        "--rotate", action="store_true", help="Nếu instance đã có khoá active, xoay thay vì từ chối"
    )
    p_issue.add_argument("--yes", action="store_true", help="Cần khi kèm --rotate — xác nhận thu hồi khoá cũ")
    p_issue.set_defaults(func=cmd_issue)

    p_rotate = sub.add_parser("rotate", help="Cấp khoá mới rồi thu hồi khoá cũ")
    p_rotate.add_argument("--credential-id", default=None, help="credential_id cần xoay")
    p_rotate.add_argument("--source-system", default="mini_crm")
    p_rotate.add_argument("--source-instance-id", default=None, help="Thay cho --credential-id: xoay khoá active hiện tại của instance này")
    p_rotate.add_argument("--label", default="")
    p_rotate.add_argument("--yes", action="store_true", help="Xác nhận thực hiện — không có cờ này chỉ in dry-run")
    p_rotate.set_defaults(func=cmd_rotate)

    p_revoke = sub.add_parser("revoke", help="Thu hồi một khoá theo credential_id")
    p_revoke.add_argument("--credential-id", required=True)
    p_revoke.add_argument("--yes", action="store_true", help="Xác nhận thực hiện — không có cờ này chỉ in dry-run")
    p_revoke.set_defaults(func=cmd_revoke)

    p_list = sub.add_parser("list", help="Liệt kê khoá (KHÔNG hiện khoá thô) — lọc theo instance/system")
    p_list.add_argument("--source-instance-id", default=None)
    p_list.add_argument("--source-system", default=None)
    p_list.set_defaults(func=cmd_list)

    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        return asyncio.run(args.func(args))
    except CredentialError as exc:
        print(f"Lỗi: {exc.error_code} — {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
