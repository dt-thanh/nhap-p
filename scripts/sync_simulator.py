"""Trình mô phỏng hệ nguồn — CÔNG CỤ PHÁT TRIỂN CỤC BỘ, KHÔNG PHẢI MINI CRM.

    python -m scripts.sync_simulator --list
    python -m scripts.sync_simulator --issue-key --instance synthetic-mini-crm
    python -m scripts.sync_simulator --scenario 01_units_incremental --api-key afsk_...
    python -m scripts.sync_simulator --validate-all

╔══════════════════════════════════════════════════════════════════════════╗
║  ĐÂY LÀ FIXTURE KIỂM THỬ. KHÔNG PHẢI PRODUCTION CRM.                     ║
║  KHÔNG PHẢI NGUỒN SỰ THẬT NGHIỆP VỤ.                                     ║
║                                                                          ║
║  Mini CRM chưa tồn tại. Công cụ này gửi payload TỔNG HỢP do chúng tôi     ║
║  tự bịa, để kiểm rằng PHÍA NHẬN cư xử đúng hợp đồng. Nó không chứng minh ║
║  — và không thể chứng minh — rằng một Mini CRM tương lai sẽ gửi được      ║
║  đúng hình dạng này.                                                     ║
╚══════════════════════════════════════════════════════════════════════════╝

Nó KHÔNG phải và không được biến thành: CRM client, webhook, job polling, hay
connector production. Nó chỉ đọc file JSON trong `docs/crm/fixtures/` rồi POST
lên endpoint cục bộ. Không có logic nghiệp vụ nào ở đây, và không được thêm vào:
mọi quy tắc nghiệp vụ thuộc về phía NHẬN, còn một trình mô phỏng biết quá nhiều
sẽ che mất chính những lỗi nó cần phơi ra.

Vì sao nó tồn tại: kiểm thử tự động chạy trong tiến trình, không đi qua HTTP thật,
nên không bao giờ chạm tới tầng ASGI, header, mã trạng thái hay giới hạn kích
thước. Công cụ này đi qua đúng những thứ đó bằng tay khi cần soi.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "docs" / "crm" / "fixtures"

DEFAULT_BASE_URL = "http://localhost:8000"

BANNER = "*** TRÌNH MÔ PHỎNG — DỮ LIỆU TỔNG HỢP, KHÔNG PHẢI CRM THẬT, KHÔNG PHẢI NGUỒN SỰ THẬT NGHIỆP VỤ ***"


def _scenarios() -> dict[str, Path]:
    return {path.stem: path for path in sorted(FIXTURE_DIR.glob("*.json"))}


def _load(name: str) -> dict:
    scenarios = _scenarios()
    if name not in scenarios:
        raise SystemExit(f"Không có kịch bản '{name}'. Có: {', '.join(sorted(scenarios))}")
    return json.loads(scenarios[name].read_text(encoding="utf-8"))


def cmd_list() -> int:
    print(BANNER)
    print(f"\nKịch bản trong {FIXTURE_DIR.relative_to(REPO_ROOT)}:\n")
    for name, path in _scenarios().items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        comment = payload.get("_comment", "")
        # Cắt ngắn để danh sách còn đọc được.
        summary = comment[:88] + ("…" if len(comment) > 88 else "")
        print(f"  {name:32s} records={len(payload.get('records', [])):>2}  {summary}")
    return 0


def cmd_validate_all() -> int:
    """Kiểm mọi fixture theo JSON Schema của hợp đồng, không cần server."""
    from src.services.contract_validation import ContractValidator

    print(BANNER)
    print("\nKiểm fixture theo src/contracts/crm_sync_v1.schema.json\n")

    validator = ContractValidator()
    failures = 0
    for name, path in _scenarios().items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        violations = validator.validate(payload)
        if violations:
            failures += 1
            print(f"  {name:32s} KHÔNG HỢP LỆ ({len(violations)} vi phạm)")
            for violation in violations[:3]:
                print(f"      {violation.json_path}  {violation.error_code}  {violation.message[:70]}")
        else:
            print(f"  {name:32s} hợp lệ")

    print(f"\n{len(_scenarios()) - failures}/{len(_scenarios())} fixture đúng hợp đồng.")
    print("Lưu ý: một số fixture CỐ Ý sai hợp đồng để kiểm đường lỗi — xem fixtures/README.md.")
    return 0


# Dự án + phân khu mà mọi fixture trỏ tới. UUID cố định để fixture chạy lại được.
SYNTHETIC_PROJECT_ID = "5117d1c0-0000-4000-8000-000000000001"


# Non-CRM lineage — obviously distinct from any real Mini CRM instance id
# (`mini-crm-dev`) and from the other dev-only fixtures' identities, so this
# row can never be mistaken for real synced data or be untraceable
# (`source_system IS NULL`).
SYNTHETIC_PROJECT_SOURCE_SYSTEM = "sync_simulator_fixture"
SYNTHETIC_PROJECT_SOURCE_INSTANCE_ID = "sync-simulator-local"


def cmd_seed_project(*, confirmed: bool) -> int:
    """Dựng dự án + phân khu TỔNG HỢP mà các fixture tham chiếu.

    Cần bước này vì dự án và phân khu do HỆ THỐNG NÀY sở hữu — hợp đồng nói rõ CRM
    tham chiếu được nhưng không tạo được (mục 2). Fixture vì thế không tự dựng được
    phạm vi của chính nó, và đó là hành vi đúng chứ không phải thiếu sót.

    Bắt buộc ``--confirm-seed`` và ``APP_ENV=development`` — đây là một lần GHI,
    và dòng nó tạo ra phải luôn mang danh tính nguồn (`source_system`), không
    bao giờ để trống, khớp bất biến "MiniCRM là chủ sở hữu duy nhất của
    Project/Area/Unit/Deal" của dự án.
    """
    import os

    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env and app_env != "development":
        raise SystemExit(f"Từ chối: sync_simulator --seed-project không chạy ngoài development (APP_ENV={app_env!r}).")
    if not confirmed:
        raise SystemExit("Từ chối: --seed-project cần --confirm-seed đi kèm (đây là một lần GHI).")

    import asyncio
    import uuid
    from datetime import UTC, date, datetime

    import sqlalchemy as sa

    from src.db import get_session_factory
    from src.models.tables import areas

    async def seed():
        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO projects (id, name, launch_date, created_at, external_id, "
                        "source_system, source_instance_id) "
                        "VALUES (:i, 'SYNTHETIC — simulator fixture', :d, :t, :ext, :sys, :inst) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "i": uuid.UUID(SYNTHETIC_PROJECT_ID),
                        "d": date(2026, 1, 1),
                        "t": datetime.now(UTC),
                        "ext": SYNTHETIC_PROJECT_ID,
                        "sys": SYNTHETIC_PROJECT_SOURCE_SYSTEM,
                        "inst": SYNTHETIC_PROJECT_SOURCE_INSTANCE_ID,
                    },
                )
                existing = await session.scalar(
                    sa.select(areas.c.id).where(
                        areas.c.project_id == uuid.UUID(SYNTHETIC_PROJECT_ID), areas.c.area_name == "A1"
                    )
                )
                if existing is None:
                    await session.execute(
                        sa.insert(areas).values(
                            id=uuid.uuid4(),
                            project_id=uuid.UUID(SYNTHETIC_PROJECT_ID),
                            area_name="A1",
                            unit_type="2PN",
                            bedrooms=2,
                            area_sqm=75,
                            total_units=100,
                            created_at=datetime.now(UTC),
                            external_id="sync-simulator-fixture-area-a1",
                            source_system=SYNTHETIC_PROJECT_SOURCE_SYSTEM,
                            source_instance_id=SYNTHETIC_PROJECT_SOURCE_INSTANCE_ID,
                        )
                    )

    asyncio.run(seed())
    print(BANNER)
    print(f"\nĐã dựng dự án tổng hợp {SYNTHETIC_PROJECT_ID} kèm phân khu A1/2PN.")
    return 0


def cmd_issue_key(instance: str, system: str) -> int:
    """Cấp một khoá API cho môi trường dev cục bộ.

    Chạy thẳng vào DB qua service, không qua HTTP: chưa có endpoint quản trị nào
    cấp khoá, và ở Phase 3 thì chưa nên có — cấp khoá là thao tác vận hành, không
    phải thứ để lộ ra trên mạng.
    """
    import asyncio

    from src.db import get_session_factory
    from src.services.sync_credentials import SyncCredentialService

    async def issue():
        async with get_session_factory()() as session:
            async with session.begin():
                return await SyncCredentialService().issue(
                    session,
                    source_system=system,
                    source_instance_id=instance,
                    label="local simulator (synthetic)",
                )

    issued = asyncio.run(issue())
    print(BANNER)
    print("\nĐã cấp khoá. Chuỗi dưới đây CHỈ hiện ra lần này — hệ thống chỉ giữ hash.\n")
    print(f"  source_instance_id : {issued.source_instance_id}")
    print(f"  key_prefix         : {issued.key_prefix}")
    print(f"  api_key            : {issued.api_key}")
    print("\nDùng:  --api-key <chuỗi trên>")
    return 0


def cmd_send(scenario: str, base_url: str, api_key: str, entity: str) -> int:
    payload = _load(scenario)

    # Chốt an toàn: chỉ gửi thứ tự dán nhãn tổng hợp. Nếu ai đó bỏ payload thật
    # vào thư mục fixture, công cụ này từ chối gửi nó.
    instance = payload.get("source_instance_id", "")
    if not instance.startswith("synthetic-"):
        raise SystemExit(
            f"Từ chối gửi: source_instance_id '{instance}' không mang tiền tố 'synthetic-'.\n"
            "Công cụ này chỉ gửi fixture tổng hợp — xem docs/crm/fixtures/README.md."
        )

    url = f"{base_url.rstrip('/')}/api/v1/sync/{entity}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — URL cục bộ do người chạy tự nhập
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )

    print(BANNER)
    print(f"\nPOST {url}")
    print(f"  kịch bản  : {scenario}")
    print(f"  instance  : {instance}")
    print(f"  byte      : {len(body)}\n")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            status = response.status
            content = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        content = exc.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Không kết nối được {base_url}: {exc.reason}") from exc

    print(f"HTTP {status}")
    try:
        print(json.dumps(json.loads(content), indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        print(content)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="liệt kê kịch bản tổng hợp")
    parser.add_argument("--validate-all", action="store_true", help="kiểm mọi fixture theo JSON Schema")
    parser.add_argument("--seed-project", action="store_true", help="dựng dự án + phân khu tổng hợp cho fixture")
    parser.add_argument("--confirm-seed", action="store_true", help="bắt buộc đi kèm --seed-project — đây là một lần GHI")
    parser.add_argument("--issue-key", action="store_true", help="cấp một khoá API cho dev cục bộ")
    parser.add_argument("--scenario", help="tên kịch bản cần gửi")
    parser.add_argument("--api-key", default="", help="khoá API dùng để gửi")
    parser.add_argument("--entity", default="units", help="entity trên đường dẫn (mặc định: units)")
    parser.add_argument("--instance", default="synthetic-mini-crm", help="source_instance_id khi cấp khoá")
    parser.add_argument("--system", default="mini_crm", help="source_system khi cấp khoá")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list()
    if args.validate_all:
        return cmd_validate_all()
    if args.seed_project:
        return cmd_seed_project(confirmed=args.confirm_seed)
    if args.issue_key:
        return cmd_issue_key(args.instance, args.system)
    if args.scenario:
        if not args.api_key:
            raise SystemExit("Thiếu --api-key. Cấp một khoá bằng --issue-key trước.")
        return cmd_send(args.scenario, args.base_url, args.api_key, args.entity)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
