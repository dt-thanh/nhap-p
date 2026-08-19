"""Core (statement-building, DB-I/O-free) logic for the `crm_real_data.json`-
derived AI/dev fixture. Shared by exactly two callers, deliberately:

    scripts/seed_backend_from_json.py           (async CLI, `src/db.py` AsyncEngine)
    alembic/versions/0019_seed_ai_crm_fixture.py (sync migration, `op.get_bind()`)

Every function here returns SQLAlchemy `Executable`s and never calls `.execute()`
itself — a plain `Connection`/`AsyncConnection` can run them identically, so the
mapping logic exists in exactly ONE place regardless of which harness runs it.
No existing migration in this repo imports from `scripts/`; this one does,
because Alembic here runs with `prepend_sys_path = .` (confirmed: `docker compose
exec api python -c "import scripts.seed_mini_crm_from_json"` succeeds), and the
alternative — copy-pasting this mapping into the migration file — would let the
CLI script and the migration drift apart silently.

Fixture identity, used on EVERY row this module writes (projects/areas/units/
upload_files), and the ONLY thing `downgrade()` keys off:

    source_system      = "crm_real_data_fixture"
    source_instance_id  = "ai-dev-fixture"

Both are obviously distinct from any real Mini CRM `source_instance_id`
(`mini-crm-dev`) — the real sync pipeline's `SourceIdentityService`/
`DomainProjector` will never produce or match this instance id, so this
fixture cannot collide with, or be mistaken for, real ingested data.

Row identity is a DETERMINISTIC `uuid5(NS_INGESTION_SEED, "<kind>:<json_id>")`
— not the source-identity unique constraint — so `ON CONFLICT (id) DO UPDATE`
is what makes reruns idempotent (same convention as `scripts/seed_dev.py`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import Executable

from src.models.tables import (
    absorption_daily,
    areas,
    inventory_snapshots,
    projects,
    sales_records,
    units,
    upload_errors,
    upload_files,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# CỐ Ý dưới `scripts/`, KHÔNG `docs/`: `.dockerignore` loại BỎ TOÀN BỘ `docs/`
# khỏi build context (xác nhận sống — `docker compose build api` rồi
# `alembic upgrade head` báo "Không thấy /app/docs/ingestion_seed.json"), và
# `docs/` cũng KHÔNG nằm trong `volumes:` của backend-base (chỉ `src/`/`alembic/`
# được mount sống). `scripts/` thì CÓ được `COPY . .` vào image lúc build — xác
# nhận bằng chính revision 0019 import được `scripts._seed_ai_crm_fixture_core`
# sau rebuild. Đổi lại giá trị này = phải rebuild lại image mỗi khi fixture đổi,
# giống hệt các file .py khác trong `scripts/`.
SEED_FILE = REPO_ROOT / "scripts" / "fixtures" / "ingestion_seed.json"

# Riêng với `scripts/seed_dev.py::NS_SEED` — hai bộ seed không được sinh trùng
# UUID cho hai thực thể khác nhau. Giữ NGUYÊN giá trị này qua mọi lần sửa: đổi
# nó = đổi toàn bộ id = mất khả năng ON CONFLICT khớp lại lần chạy trước.
NS_INGESTION_SEED = uuid.UUID("8f2b0c1d-6a4e-4b3f-9c7a-2e5d1a9b6f30")

SOURCE_SYSTEM = "crm_real_data_fixture"
SOURCE_INSTANCE_ID = "ai-dev-fixture"

CALCULATOR_LEGACY = "legacy_aggregate"
FALLBACK_LAUNCH_DATE = date(2026, 1, 1)

# `projects.status`/`areas.status` CHECK ('pending','active','rejected','archived')
# — crm_real_data.json's free-text status ("selling") không khớp; mọi dự án
# nguồn đều đang mở bán nên chuẩn hoá về 'active', không bịa trạng thái khác.
PROJECT_STATUS = "active"
AREA_STATUS = "active"

# Dữ liệu trend/tồn kho là số THẬT từ nguồn, không phải suy diễn.
DATA_QUALITY_OK = "ok"

# `ck_upload_files_status` (0001_initial_schema.py / nới ở 0016) chỉ nhận
# ('pending','processing','completed','completed_with_conflicts',
# 'partially_completed','failed') — trạng thái tự do của crm_real_data.json
# ('success'/'partial') KHÔNG khớp. Ánh xạ TƯỜNG MINH, không đoán: một giá trị
# lạ phải làm nổ SeedError ngay, không âm thầm rơi mất hay bịa một trạng thái
# gần đúng.
FILE_STATUS_MAP = {
    "success": "completed",
    "partial": "partially_completed",
    "failed": "failed",
    "processing": "processing",
    "pending": "pending",
    "completed": "completed",
    "completed_with_conflicts": "completed_with_conflicts",
    "partially_completed": "partially_completed",
}


def uid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NS_INGESTION_SEED, f"{kind}:{key}")


def _row_hash(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class SeedError(RuntimeError):
    pass


def load_seed(path: Path = SEED_FILE) -> dict[str, Any]:
    if not path.exists():
        raise SeedError(
            f"Không thấy {path}. File này phải được tạo trước bằng "
            "`python -m scripts.derive_ingestion_seed_from_crm_real_data --source <crm_real_data.json>` "
            "— không tự bịa dữ liệu."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"projects", "dash_areas", "trend_by_area", "units"}
    missing = required - data.keys()
    if missing:
        raise SeedError(f"{path.name} thiếu khoá bắt buộc: {sorted(missing)}")
    for i, area in enumerate(data["dash_areas"]):
        need = {"unit_type", "bedrooms", "area_sqm"} - area.keys()
        if need:
            raise SeedError(
                f"dash_areas[{i}] (id={area.get('id')}) thiếu {sorted(need)} — file phải được dựng lại bằng "
                "scripts/derive_ingestion_seed_from_crm_real_data.py, không sửa tay."
            )
    return data


@dataclass
class UpsertPlan:
    """`statements`: (table_name, statement) theo ĐÚNG thứ tự an toàn khoá ngoại."""

    statements: list[tuple[str, Executable]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def build_upserts(data: dict[str, Any]) -> UpsertPlan:
    """Thuần — không I/O. Trả về TOÀN BỘ statement upsert theo thứ tự cha trước con."""
    now = datetime.now(UTC)
    plan = UpsertPlan()

    # --- projects ---------------------------------------------------------
    project_ids: dict[str, uuid.UUID] = {}
    for p in data["projects"]:
        pid = uid("project", p["id"])
        project_ids[p["id"]] = pid
        launch_date = _parse_date(p["launch_date"]) if p.get("launch_date") else FALLBACK_LAUNCH_DATE
        stmt = pg_insert(projects).values(
            id=pid,
            name=p["name"],
            launch_date=launch_date,
            created_at=now,
            updated_at=now,
            status=PROJECT_STATUS,
            absorption_calculator=CALCULATOR_LEGACY,
            external_id=p["id"],
            source_system=SOURCE_SYSTEM,
            source_instance_id=SOURCE_INSTANCE_ID,
            source_revision=1,
            source_updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[projects.c.id],
            set_={
                "name": p["name"],
                "launch_date": launch_date,
                "updated_at": now,
                "external_id": p["id"],
                "source_system": SOURCE_SYSTEM,
                "source_instance_id": SOURCE_INSTANCE_ID,
                "source_revision": 1,
                "source_updated_at": now,
            },
        )
        plan.statements.append(("projects", stmt))
    plan.counts["projects"] = len(project_ids)

    # --- areas --------------------------------------------------------------
    area_ids: dict[str, uuid.UUID] = {}
    for a in data["dash_areas"]:
        project_id = project_ids.get(a["project_id"])
        if project_id is None:
            raise SeedError(f"dash_areas id={a['id']} tham chiếu project_id='{a['project_id']}' không có trong 'projects'")
        aid = uid("area", a["id"])
        area_ids[a["id"]] = aid
        stmt = pg_insert(areas).values(
            id=aid,
            project_id=project_id,
            area_name=a["name"],
            unit_type=a["unit_type"],
            bedrooms=a["bedrooms"],
            area_sqm=Decimal(str(a["area_sqm"])),
            total_units=a["total_units"],
            created_at=now,
            status=AREA_STATUS,
            external_id=a["id"],
            source_system=SOURCE_SYSTEM,
            source_instance_id=SOURCE_INSTANCE_ID,
            source_revision=1,
            source_updated_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[areas.c.id],
            set_={
                "area_name": a["name"],
                "unit_type": a["unit_type"],
                "bedrooms": a["bedrooms"],
                "area_sqm": Decimal(str(a["area_sqm"])),
                "total_units": a["total_units"],
                "updated_at": now,
                "external_id": a["id"],
                "source_system": SOURCE_SYSTEM,
                "source_instance_id": SOURCE_INSTANCE_ID,
                "source_revision": 1,
                "source_updated_at": now,
            },
        )
        plan.statements.append(("areas", stmt))
    plan.counts["areas"] = len(area_ids)

    # --- units ----------------------------------------------------------------
    # Chỉ tạo khi JSON có bản ghi CẤP CĂN thật (`data["units"]`, từ
    # `ranking_by_area` của crm_real_data.json) — KHÔNG suy ra hàng nghìn căn từ
    # `total_units` cấp phân khu (đó là tổng CẢ TOÀ, không phải tồn kho từng căn).
    unit_count = 0
    for u in data.get("units", []):
        area_id = area_ids.get(u["area_id"])
        if area_id is None:
            raise SeedError(f"units id={u['id']} tham chiếu area_id='{u['area_id']}' không có trong 'dash_areas'")
        unit_row_id = uid("unit", u["id"])
        stmt = pg_insert(units).values(
            id=unit_row_id,
            source_system=SOURCE_SYSTEM,
            source_instance_id=SOURCE_INSTANCE_ID,
            external_unit_id=u["id"],
            area_id=area_id,
            unit_code=u["unit_code"],
            unit_type=u["unit_type"],
            status=u["status"],
            source_revision=1,
            source_updated_at=now,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[units.c.id],
            set_={
                "unit_code": u["unit_code"],
                "unit_type": u["unit_type"],
                "status": u["status"],
                "updated_at": now,
                "source_system": SOURCE_SYSTEM,
                "source_instance_id": SOURCE_INSTANCE_ID,
                "source_revision": 1,
                "source_updated_at": now,
            },
        )
        plan.statements.append(("units", stmt))
        unit_count += 1
    plan.counts["units"] = unit_count

    # --- upload_files (QA display only) ---------------------------------------
    file_ids: dict[str, uuid.UUID] = {}
    for f in data.get("files", []):
        mapped_status = FILE_STATUS_MAP.get(f["status"])
        if mapped_status is None:
            raise SeedError(
                f"files id={f['id']}: status '{f['status']}' không có ánh xạ nào tới ck_upload_files_status "
                f"— thêm vào FILE_STATUS_MAP thay vì đoán."
            )
        fid = uid("upload_file_qa", f["id"])
        file_ids[f["id"]] = fid
        stmt = pg_insert(upload_files).values(
            id=fid,
            project_id=None,
            filename=f["filename"],
            status=mapped_status,
            rows_ok=f["rows_ok"],
            rows_failed=f["rows_failed"],
            uploaded_at=_parse_ts(f["uploaded_at"]),
            source_system=SOURCE_SYSTEM,
            source_instance_id=SOURCE_INSTANCE_ID,
            input_format="csv",
            transport_mode="file_upload",
            sync_mode="full_snapshot",
            schema_version=1,
            rows_received=f["rows_ok"] + f["rows_failed"],
            error_summary={},
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[upload_files.c.id],
            set_={"status": mapped_status, "rows_ok": f["rows_ok"], "rows_failed": f["rows_failed"]},
        )
        plan.statements.append(("upload_files", stmt))
    plan.counts["files"] = len(file_ids)

    # --- sample_errors ----------------------------------------------------------
    error_count = 0
    for e in data.get("sample_errors", []):
        file_id = file_ids.get(e["file_id"])
        if file_id is None:
            raise SeedError(f"sample_errors id={e['id']} tham chiếu file_id='{e['file_id']}' không có trong 'files'")
        eid = uid("upload_error", str(e["id"]))
        stmt = pg_insert(upload_errors).values(
            id=eid,
            file_id=file_id,
            row_number=e.get("row_number"),
            column_name=e.get("column_name"),
            error_code=e["error_code"],
            message=e["message"],
            created_at=now,
            error_category="field",
            retry_status="open",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[upload_errors.c.id],
            set_={"message": e["message"], "error_code": e["error_code"]},
        )
        plan.statements.append(("upload_errors", stmt))
        error_count += 1
    plan.counts["sample_errors"] = error_count

    # --- batch anchor upload_files row (sales_records/inventory_snapshots) -----
    total_rows = sum(a.get("total_units", 0) for a in data["dash_areas"])
    batch_file_id = uid("upload_file_batch", "ingestion_seed_batch")
    plan.statements.append(
        (
            "upload_files",
            pg_insert(upload_files)
            .values(
                id=batch_file_id,
                project_id=None,
                filename="ingestion_seed_batch.json",
                status="completed",
                rows_ok=total_rows,
                rows_failed=0,
                uploaded_at=now,
                source_system=SOURCE_SYSTEM,
                source_instance_id=SOURCE_INSTANCE_ID,
                input_format="json",
                transport_mode="api_push",
                sync_mode="full_snapshot",
                schema_version=1,
                rows_received=total_rows,
                error_summary={},
            )
            .on_conflict_do_update(
                index_elements=[upload_files.c.id], set_={"rows_ok": total_rows, "rows_received": total_rows}
            ),
        )
    )

    # --- sales_records / inventory_snapshots -----------------------------------
    sales_count = inv_count = 0
    for a in data["dash_areas"]:
        area_id = area_ids[a["id"]]
        sold = a.get("sold")
        remaining = a.get("remaining")
        if sold is not None:
            sid = uid("sales_record", a["id"])
            stmt = (
                pg_insert(sales_records)
                .values(
                    id=sid,
                    area_id=area_id,
                    file_id=batch_file_id,
                    sold_date=now.date(),
                    units_sold=sold,
                    external_record_id=f"seed:{a['id']}:sales",
                    source_row_hash=_row_hash("sales", a["id"], sold),
                    created_at=now,
                )
                .on_conflict_do_update(index_elements=[sales_records.c.id], set_={"units_sold": sold})
            )
            plan.statements.append(("sales_records", stmt))
            sales_count += 1
        if remaining is not None:
            iid = uid("inventory_snapshot", a["id"])
            stmt = (
                pg_insert(inventory_snapshots)
                .values(
                    id=iid,
                    area_id=area_id,
                    file_id=batch_file_id,
                    snapshot_date=now.date(),
                    units_remaining=remaining,
                    snapshot_type="manual",
                    source_row_hash=_row_hash("inventory", a["id"], remaining),
                    created_at=now,
                )
                .on_conflict_do_update(index_elements=[inventory_snapshots.c.id], set_={"units_remaining": remaining})
            )
            plan.statements.append(("inventory_snapshots", stmt))
            inv_count += 1
    plan.counts["sales_records"] = sales_count
    plan.counts["inventory_snapshots"] = inv_count

    # --- absorption_daily -------------------------------------------------------
    # `units_remaining` chỉ điền cho điểm MỚI NHẤT (giá trị hiện tại thật) — các
    # điểm lịch sử để NULL, đúng ngữ nghĩa "không có snapshot lịch sử", không phải 0.
    absorption_count = 0
    for json_area_id, points in data["trend_by_area"].items():
        area_id = area_ids.get(json_area_id)
        if area_id is None:
            raise SeedError(f"trend_by_area chứa area id='{json_area_id}' không có trong 'dash_areas'")
        ordered = sorted(points, key=lambda p: p["date"])
        for i, point in enumerate(ordered):
            window7 = ordered[max(0, i - 6) : i + 1]
            window30 = ordered[max(0, i - 29) : i + 1]
            velocity_7d = sum(p["units_sold"] for p in window7)
            velocity_30d = sum(p["units_sold"] for p in window30)
            row_id = uid("absorption_daily", f"{json_area_id}:{point['date']}")
            stmt = (
                pg_insert(absorption_daily)
                .values(
                    id=row_id,
                    area_id=area_id,
                    stat_date=_parse_date(point["date"]),
                    units_sold=point["units_sold"],
                    velocity_7d=Decimal(velocity_7d),
                    velocity_30d=Decimal(velocity_30d),
                    data_quality_status=DATA_QUALITY_OK,
                    is_observed=True,
                    computed_at=now,
                    units_remaining=None,
                    calculator=CALCULATOR_LEGACY,
                    units_reserved=None,
                    computation_id=None,
                )
                .on_conflict_do_update(
                    index_elements=[absorption_daily.c.id],
                    set_={
                        "units_sold": point["units_sold"],
                        "velocity_7d": Decimal(velocity_7d),
                        "velocity_30d": Decimal(velocity_30d),
                    },
                )
            )
            plan.statements.append(("absorption_daily", stmt))
            absorption_count += 1
    plan.counts["absorption_daily"] = absorption_count

    return plan


def build_downgrade_statements() -> list[Executable]:
    """Thuần — không I/O. Xoá ĐÚNG những dòng mang danh tính fixture này, theo
    thứ tự con trước cha. KHÔNG phụ thuộc file JSON còn tồn tại hay không — mọi
    điều kiện tra ngược lại từ CHÍNH các cột danh tính đã ghi trong DB."""
    own_area = sa.select(areas.c.id).where(
        areas.c.source_system == SOURCE_SYSTEM, areas.c.source_instance_id == SOURCE_INSTANCE_ID
    )
    own_file = sa.select(upload_files.c.id).where(
        upload_files.c.source_system == SOURCE_SYSTEM, upload_files.c.source_instance_id == SOURCE_INSTANCE_ID
    )
    return [
        sa.delete(upload_errors).where(upload_errors.c.file_id.in_(own_file)),
        sa.delete(sales_records).where(sales_records.c.file_id.in_(own_file)),
        sa.delete(inventory_snapshots).where(inventory_snapshots.c.file_id.in_(own_file)),
        sa.delete(absorption_daily).where(absorption_daily.c.area_id.in_(own_area)),
        sa.delete(units).where(units.c.source_system == SOURCE_SYSTEM, units.c.source_instance_id == SOURCE_INSTANCE_ID),
        sa.delete(upload_files).where(
            upload_files.c.source_system == SOURCE_SYSTEM, upload_files.c.source_instance_id == SOURCE_INSTANCE_ID
        ),
        sa.delete(areas).where(areas.c.source_system == SOURCE_SYSTEM, areas.c.source_instance_id == SOURCE_INSTANCE_ID),
        sa.delete(projects).where(
            projects.c.source_system == SOURCE_SYSTEM, projects.c.source_instance_id == SOURCE_INSTANCE_ID
        ),
    ]
