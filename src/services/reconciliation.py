"""ReconciliationService — bản sao có tự nhất quán không, và có khớp nguồn không.

**Hai loại đối soát, và việc tách chúng ra là điểm quan trọng nhất của module.**

*Đối soát NỘI BỘ* (`scope='internal'`) chỉ dùng database của chính ta. Nó chứng
minh bản sao tự nhất quán: `crm_source_records` và `units`/`deals` khớp nhau,
không có giao dịch mồ côi, không có hai giao dịch cùng giữ một căn, không có căn
trỏ vào phân khu không tồn tại. Chạy được NGAY BÂY GIỜ, không cần CRM.

*Đối soát VỚI NGUỒN* (`scope='source'`) so với số liệu hệ nguồn công bố. **Chưa
chạy được và sẽ không chạy được cho tới khi Mini CRM tồn tại.**

Trộn hai loại này lại là cách nhanh nhất để tuyên bố sai rằng đồng bộ đã đúng:
đối soát nội bộ có thể xanh hoàn toàn trong khi bản sao lệch hẳn khỏi CRM, vì nó
không có gì để so ngoài chính nó. `scope` là cột NOT NULL để mỗi kết quả luôn tự
khai nó chứng minh được điều gì.

**Chỉ ĐỌC.** Không check nào ghi vào bảng nghiệp vụ. Ngoại lệ duy nhất là
tombstone theo ảnh chụp đã qua chốt an toàn — và nó nằm trong cùng transaction
với lần chạy đối soát, có `SnapshotGate` canh, có finding ghi lại.

**Quy tắc mức độ.** `error` chặn "đạt": thiếu/thừa id, bản ghi mồ côi, trùng lặp,
bản ghi bị từ chối, bản sao lệch bản ghi nguồn. `warning` phải kèm chi tiết nhưng
không chặn: bản ghi cũ, chênh lệch giữa hai bộ tính. `info` là số liệu nền.

Một lần chạy ĐẠT khi và chỉ khi không có finding nào mức `error` — quy tắc này
được DB ràng buộc, không chỉ nằm ở đây.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import (
    areas,
    crm_source_records,
    deals,
    reconciliation_findings,
    reconciliation_runs,
    units,
    upload_errors,
    upload_files,
)
from src.services.snapshot_gate import SnapshotGate

log = get_logger("src.services.reconciliation")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# Trạng thái giao dịch ĐANG GIỮ một căn. Trùng với partial unique index của 0007.
HOLDING_STATUSES = ("reserved", "sold")

# Trần số finding cùng loại ghi xuống DB. Một sự cố hệ thống có thể sinh hàng
# nghìn bản ghi lệch; ghi hết chỉ làm bảng phình mà không thêm thông tin, và
# `details` của finding đầu đã mang số đếm đầy đủ.
MAX_FINDINGS_PER_CHECK = 100


@dataclass(slots=True)
class Finding:
    """Một phát hiện, ở dạng máy đọc được."""

    check_code: str
    severity: str
    message: str
    entity: str | None = None
    external_id: str | None = None
    area_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReconciliationResult:
    reconciliation_run_id: str
    scope: str
    status: str
    passed: bool
    error_count: int
    warning_count: int
    info_count: int
    checks_run: int
    findings: list[Finding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    tombstoned: int = 0


class ReconciliationService:
    """Chạy các kiểm tra nhất quán và ghi lại kết quả ở dạng tra cứu được."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def run(
        self,
        *,
        project_id: uuid.UUID,
        source_system: str = "mini_crm",
        source_instance_id: str,
        scope: str = "internal",
        snapshot_id: str | None = None,
        snapshot_entity: str | None = None,
        override_safety_gate: bool = False,
    ) -> ReconciliationResult:
        """Chạy một lượt đối soát và lưu kết quả.

        `scope='snapshot'` thêm phần kiểm ảnh chụp và — chỉ khi chốt an toàn cho
        phép — tombstone tập vắng mặt trong CÙNG transaction.
        """
        run_id = uuid.uuid4()
        started = datetime.now(UTC)
        findings: list[Finding] = []
        checks = 0
        tombstoned = 0

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.insert(reconciliation_runs).values(
                        id=run_id,
                        project_id=project_id,
                        scope=scope,
                        source_system=source_system,
                        source_instance_id=source_instance_id,
                        snapshot_id=snapshot_id if scope == "snapshot" else None,
                        status="running",
                        passed=False,
                        started_at=started,
                    )
                )

                context = _Context(
                    session=session,
                    project_id=project_id,
                    source_system=source_system,
                    source_instance_id=source_instance_id,
                )

                for check in (
                    self._check_entity_counts,
                    self._check_counts_by_scope,
                    self._check_external_id_differences,
                    self._check_mirror_source_consistency,
                    self._check_duplicate_active_deals,
                    self._check_orphan_deals,
                    self._check_units_missing_areas,
                    self._check_rejected_records,
                    self._check_tombstone_consistency,
                ):
                    findings.extend(await check(context))
                    checks += 1

                if scope == "snapshot":
                    snapshot_findings, tombstoned = await self._reconcile_snapshot(
                        context,
                        snapshot_id=snapshot_id,
                        entity=snapshot_entity or "units",
                        override=override_safety_gate,
                    )
                    findings.extend(snapshot_findings)
                    checks += 3  # đủ mảnh, bản ghi cũ sau ảnh chụp, chốt an toàn

                counts = _count_by_severity(findings)
                passed = counts[SEVERITY_ERROR] == 0
                status = "completed" if passed else "failed"

                await self._persist_findings(session, run_id, findings, started)
                summary = {
                    "by_severity": counts,
                    "by_check": _count_by_check(findings),
                    "tombstoned": tombstoned,
                    # Nói thẳng ranh giới của kết luận này, ngay trong dữ liệu.
                    "proves": (
                        "bản sao tự nhất quán (không so với CRM thật — chưa có CRM)"
                        if scope != "source"
                        else "khớp với số liệu hệ nguồn công bố"
                    ),
                }

                await session.execute(
                    sa.update(reconciliation_runs)
                    .where(reconciliation_runs.c.id == run_id)
                    .values(
                        status=status,
                        passed=passed,
                        error_count=counts[SEVERITY_ERROR],
                        warning_count=counts[SEVERITY_WARNING],
                        info_count=counts[SEVERITY_INFO],
                        checks_run=checks,
                        summary=summary,
                        finished_at=datetime.now(UTC),
                    )
                )

        log.info(
            "reconciliation.finished",
            reconciliation_run_id=str(run_id),
            scope=scope,
            passed=passed,
            errors=counts[SEVERITY_ERROR],
            warnings=counts[SEVERITY_WARNING],
            tombstoned=tombstoned,
        )

        return ReconciliationResult(
            reconciliation_run_id=str(run_id),
            scope=scope,
            status=status,
            passed=passed,
            error_count=counts[SEVERITY_ERROR],
            warning_count=counts[SEVERITY_WARNING],
            info_count=counts[SEVERITY_INFO],
            checks_run=checks,
            findings=findings,
            summary=summary,
            tombstoned=tombstoned,
        )

    # --- 1. Số lượng thực thể ----------------------------------------------

    async def _check_entity_counts(self, ctx: _Context) -> list[Finding]:
        """Số bản ghi còn sống / đã tombstone của từng thực thể. Số liệu nền."""
        details: dict[str, Any] = {}
        for entity, table, id_col in (("units", units, "external_unit_id"), ("deals", deals, "external_deal_id")):
            live = await ctx.session.scalar(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.source_instance_id == ctx.source_instance_id, table.c.deleted_at.is_(None))
            )
            tombstoned = await ctx.session.scalar(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.source_instance_id == ctx.source_instance_id, table.c.deleted_at.is_not(None))
            )
            details[entity] = {"live": live or 0, "tombstoned": tombstoned or 0, "id_column": id_col}

        return [
            Finding(
                check_code="ENTITY_COUNTS",
                severity=SEVERITY_INFO,
                message="Số lượng bản ghi theo thực thể",
                details=details,
            )
        ]

    # --- 2. Số lượng theo dự án / phân khu / trạng thái ---------------------

    async def _check_counts_by_scope(self, ctx: _Context) -> list[Finding]:
        area_rows = (
            (
                await ctx.session.execute(
                    sa.select(areas.c.area_name, areas.c.unit_type, units.c.status, sa.func.count())
                    .select_from(units.join(areas, units.c.area_id == areas.c.id))
                    .where(
                        areas.c.project_id == ctx.project_id,
                        units.c.source_instance_id == ctx.source_instance_id,
                        units.c.deleted_at.is_(None),
                    )
                    .group_by(areas.c.area_name, areas.c.unit_type, units.c.status)
                    .order_by(areas.c.area_name, areas.c.unit_type, units.c.status)
                )
            )
            .mappings()
            .all()
        )

        deal_rows = (
            (
                await ctx.session.execute(
                    sa.select(deals.c.status, sa.func.count())
                    .where(deals.c.source_instance_id == ctx.source_instance_id, deals.c.deleted_at.is_(None))
                    .group_by(deals.c.status)
                    .order_by(deals.c.status)
                )
            )
            .mappings()
            .all()
        )

        return [
            Finding(
                check_code="COUNTS_BY_SCOPE",
                severity=SEVERITY_INFO,
                message="Số lượng theo dự án, phân khu và trạng thái",
                details={
                    "project_id": str(ctx.project_id),
                    "units_by_area_status": [
                        {
                            "area_name": row["area_name"],
                            "unit_type": row["unit_type"],
                            "status": row["status"],
                            "count": row["count"],
                        }
                        for row in area_rows
                    ],
                    "deals_by_status": [{"status": row["status"], "count": row["count"]} for row in deal_rows],
                },
            )
        ]

    # --- 3. Chênh lệch external-id, HAI CHIỀU -------------------------------

    async def _check_external_id_differences(self, ctx: _Context) -> list[Finding]:
        """So tập id giữa `crm_source_records` và bản sao, theo cả hai chiều.

        Một chiều là không đủ: "có ở nguồn mà thiếu ở bản sao" và "có ở bản sao mà
        không có ở nguồn" là hai lỗi khác hẳn nhau — một bên là mất dữ liệu, bên
        kia là dữ liệu ma.
        """
        findings: list[Finding] = []

        for entity, table, id_col in (("units", units, "external_unit_id"), ("deals", deals, "external_deal_id")):
            tracked = set(
                (
                    await ctx.session.execute(
                        sa.select(crm_source_records.c.source_record_id).where(
                            crm_source_records.c.source_system == ctx.source_system,
                            crm_source_records.c.source_instance_id == ctx.source_instance_id,
                            crm_source_records.c.source_entity == entity,
                            crm_source_records.c.state == "active",
                        )
                    )
                ).scalars()
            )
            mirrored = set(
                (
                    await ctx.session.execute(
                        sa.select(table.c[id_col]).where(
                            table.c.source_instance_id == ctx.source_instance_id,
                            table.c.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )

            missing = sorted(tracked - mirrored)
            extra = sorted(mirrored - tracked)

            for external_id in missing[:MAX_FINDINGS_PER_CHECK]:
                findings.append(
                    Finding(
                        check_code="MISSING_IN_MIRROR",
                        severity=SEVERITY_ERROR,
                        entity=entity,
                        external_id=external_id,
                        message=f"Bản ghi nguồn '{external_id}' đang active nhưng không có trong bản sao {entity}",
                        details={"total_missing": len(missing), "entity": entity},
                    )
                )
            for external_id in extra[:MAX_FINDINGS_PER_CHECK]:
                findings.append(
                    Finding(
                        check_code="EXTRA_IN_MIRROR",
                        severity=SEVERITY_ERROR,
                        entity=entity,
                        external_id=external_id,
                        message=f"Bản sao {entity} có '{external_id}' nhưng không có bản ghi nguồn active tương ứng",
                        details={"total_extra": len(extra), "entity": entity},
                    )
                )

        return findings

    # --- 4. crm_source_records ↔ units/deals -------------------------------

    async def _check_mirror_source_consistency(self, ctx: _Context) -> list[Finding]:
        """Bản sao và bản ghi nguồn phải khớp về PHIÊN BẢN, không chỉ về sự tồn tại.

        Cùng một id ở hai bên nhưng `source_revision` lệch nghĩa là tầng chiếu đã
        chạy nửa vời: bản ghi nguồn ghi nhận một phiên bản mà bản sao chưa nhận.
        """
        findings: list[Finding] = []

        for entity, table, id_col in (("units", units, "external_unit_id"), ("deals", deals, "external_deal_id")):
            rows = (
                (
                    await ctx.session.execute(
                        sa.select(
                            crm_source_records.c.source_record_id,
                            crm_source_records.c.source_revision.label("source_revision"),
                            crm_source_records.c.source_updated_at.label("source_updated_at"),
                            crm_source_records.c.state,
                            table.c.source_revision.label("mirror_revision"),
                            table.c.source_updated_at.label("mirror_updated_at"),
                            table.c.deleted_at,
                        )
                        .select_from(
                            crm_source_records.join(
                                table,
                                sa.and_(
                                    crm_source_records.c.source_record_id == table.c[id_col],
                                    crm_source_records.c.source_instance_id == table.c.source_instance_id,
                                ),
                            )
                        )
                        .where(
                            crm_source_records.c.source_system == ctx.source_system,
                            crm_source_records.c.source_instance_id == ctx.source_instance_id,
                            crm_source_records.c.source_entity == entity,
                        )
                    )
                )
                .mappings()
                .all()
            )

            for row in rows:
                if (
                    row["source_revision"] != row["mirror_revision"]
                    or row["source_updated_at"] != row["mirror_updated_at"]
                ):
                    findings.append(
                        Finding(
                            check_code="MIRROR_SOURCE_VERSION_MISMATCH",
                            severity=SEVERITY_ERROR,
                            entity=entity,
                            external_id=row["source_record_id"],
                            message=f"Phiên bản ở bản sao lệch bản ghi nguồn cho '{row['source_record_id']}'",
                            details={
                                "source_revision": row["source_revision"],
                                "mirror_revision": row["mirror_revision"],
                                "source_updated_at": _iso(row["source_updated_at"]),
                                "mirror_updated_at": _iso(row["mirror_updated_at"]),
                            },
                        )
                    )

                tombstoned_at_source = row["state"] == "tombstoned"
                tombstoned_in_mirror = row["deleted_at"] is not None
                if tombstoned_at_source != tombstoned_in_mirror:
                    findings.append(
                        Finding(
                            check_code="TOMBSTONE_STATE_MISMATCH",
                            severity=SEVERITY_ERROR,
                            entity=entity,
                            external_id=row["source_record_id"],
                            message=f"Trạng thái tombstone lệch nhau cho '{row['source_record_id']}'",
                            details={
                                "source_state": row["state"],
                                "mirror_deleted_at": _iso(row["deleted_at"]),
                            },
                        )
                    )

        return findings

    # --- 5. Trùng giao dịch đang giữ ---------------------------------------

    async def _check_duplicate_active_deals(self, ctx: _Context) -> list[Finding]:
        """Một căn chỉ được có MỘT giao dịch đang giữ.

        `uq_deals_active_per_unit` (0007) đã chặn ở DB, nên phát hiện được ở đây
        nghĩa là ràng buộc đã bị gỡ hoặc dữ liệu vào bằng đường khác — cả hai đều
        nghiêm trọng, vì tồn kho sẽ bị đếm hụt.
        """
        rows = (
            (
                await ctx.session.execute(
                    sa.select(deals.c.unit_id, sa.func.count().label("holding"))
                    .where(
                        deals.c.source_instance_id == ctx.source_instance_id,
                        deals.c.status.in_(HOLDING_STATUSES),
                        deals.c.deleted_at.is_(None),
                    )
                    .group_by(deals.c.unit_id)
                    .having(sa.func.count() > 1)
                )
            )
            .mappings()
            .all()
        )

        return [
            Finding(
                check_code="DUPLICATE_ACTIVE_DEAL",
                severity=SEVERITY_ERROR,
                entity="deals",
                message=f"Căn {row['unit_id']} có {row['holding']} giao dịch đang giữ",
                details={"unit_id": str(row["unit_id"]), "holding_deals": row["holding"]},
            )
            for row in rows[:MAX_FINDINGS_PER_CHECK]
        ]

    # --- 6. Giao dịch mồ côi ------------------------------------------------

    async def _check_orphan_deals(self, ctx: _Context) -> list[Finding]:
        """Giao dịch trỏ tới căn không tồn tại, hoặc căn đã bị tombstone.

        Vế thứ hai quan trọng không kém: một giao dịch còn sống gắn vào căn đã xoá
        vẫn giữ chỗ trong mọi phép đếm tồn kho, dù căn đó không còn.
        """
        rows = (
            (
                await ctx.session.execute(
                    sa.select(
                        deals.c.external_deal_id, deals.c.unit_id, units.c.id.label("unit_exists"), units.c.deleted_at
                    )
                    .select_from(deals.outerjoin(units, deals.c.unit_id == units.c.id))
                    .where(deals.c.source_instance_id == ctx.source_instance_id, deals.c.deleted_at.is_(None))
                )
            )
            .mappings()
            .all()
        )

        findings: list[Finding] = []
        for row in rows:
            if row["unit_exists"] is None:
                findings.append(
                    Finding(
                        check_code="ORPHAN_DEAL",
                        severity=SEVERITY_ERROR,
                        entity="deals",
                        external_id=row["external_deal_id"],
                        message=f"Giao dịch '{row['external_deal_id']}' trỏ tới căn không tồn tại",
                        details={"unit_id": str(row["unit_id"]), "reason": "unit_missing"},
                    )
                )
            elif row["deleted_at"] is not None:
                findings.append(
                    Finding(
                        check_code="ORPHAN_DEAL",
                        severity=SEVERITY_ERROR,
                        entity="deals",
                        external_id=row["external_deal_id"],
                        message=f"Giao dịch '{row['external_deal_id']}' còn sống nhưng căn đã bị tombstone",
                        details={"unit_id": str(row["unit_id"]), "reason": "unit_tombstoned"},
                    )
                )
        return findings[:MAX_FINDINGS_PER_CHECK]

    # --- 7. Căn thiếu phân khu ----------------------------------------------

    async def _check_units_missing_areas(self, ctx: _Context) -> list[Finding]:
        rows = (
            (
                await ctx.session.execute(
                    sa.select(units.c.external_unit_id, units.c.area_id)
                    .select_from(units.outerjoin(areas, units.c.area_id == areas.c.id))
                    .where(
                        units.c.source_instance_id == ctx.source_instance_id,
                        units.c.deleted_at.is_(None),
                        areas.c.id.is_(None),
                    )
                )
            )
            .mappings()
            .all()
        )

        return [
            Finding(
                check_code="UNIT_MISSING_AREA",
                severity=SEVERITY_ERROR,
                entity="units",
                external_id=row["external_unit_id"],
                area_id=row["area_id"],
                message=f"Căn '{row['external_unit_id']}' trỏ tới phân khu không tồn tại",
                details={"area_id": str(row["area_id"])},
            )
            for row in rows[:MAX_FINDINGS_PER_CHECK]
        ]

    # --- 9. Bản ghi bị từ chối ----------------------------------------------

    async def _check_rejected_records(self, ctx: _Context) -> list[Finding]:
        """Lô có bản ghi bị từ chối = dữ liệu hệ nguồn chưa vào được bản sao."""
        rows = (
            (
                await ctx.session.execute(
                    sa.select(
                        upload_files.c.id,
                        upload_files.c.external_batch_id,
                        upload_files.c.status,
                        upload_files.c.rows_failed,
                    ).where(
                        upload_files.c.project_id == ctx.project_id,
                        upload_files.c.source_instance_id == ctx.source_instance_id,
                        upload_files.c.rows_failed > 0,
                    )
                )
            )
            .mappings()
            .all()
        )

        findings: list[Finding] = []
        for row in rows[:MAX_FINDINGS_PER_CHECK]:
            categories = (
                (
                    await ctx.session.execute(
                        sa.select(upload_errors.c.error_category, sa.func.count())
                        .where(upload_errors.c.file_id == row["id"])
                        .group_by(upload_errors.c.error_category)
                    )
                )
                .mappings()
                .all()
            )
            findings.append(
                Finding(
                    check_code="REJECTED_RECORDS",
                    severity=SEVERITY_ERROR,
                    message=f"Lô '{row['external_batch_id']}' có {row['rows_failed']} bản ghi bị từ chối",
                    details={
                        "sync_run_id": str(row["id"]),
                        "external_batch_id": row["external_batch_id"],
                        "batch_status": row["status"],
                        "rows_failed": row["rows_failed"],
                        "by_category": {c["error_category"]: c["count"] for c in categories},
                    },
                )
            )
        return findings

    # --- 10. Nhất quán tombstone --------------------------------------------

    async def _check_tombstone_consistency(self, ctx: _Context) -> list[Finding]:
        """Bản ghi nguồn đã tombstone thì bản sao cũng phải tombstone, và ngược lại.

        Bổ sung cho check 4: ở đây bắt cả những bản ghi nguồn KHÔNG có hàng tương
        ứng trong bản sao, vốn không xuất hiện trong phép join.
        """
        findings: list[Finding] = []

        for entity, table, id_col in (("units", units, "external_unit_id"), ("deals", deals, "external_deal_id")):
            tombstoned_source = set(
                (
                    await ctx.session.execute(
                        sa.select(crm_source_records.c.source_record_id).where(
                            crm_source_records.c.source_system == ctx.source_system,
                            crm_source_records.c.source_instance_id == ctx.source_instance_id,
                            crm_source_records.c.source_entity == entity,
                            crm_source_records.c.state == "tombstoned",
                        )
                    )
                ).scalars()
            )
            live_mirror = set(
                (
                    await ctx.session.execute(
                        sa.select(table.c[id_col]).where(
                            table.c.source_instance_id == ctx.source_instance_id,
                            table.c.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )

            still_live = sorted(tombstoned_source & live_mirror)
            for external_id in still_live[:MAX_FINDINGS_PER_CHECK]:
                findings.append(
                    Finding(
                        check_code="TOMBSTONE_NOT_APPLIED",
                        severity=SEVERITY_ERROR,
                        entity=entity,
                        external_id=external_id,
                        message=f"'{external_id}' đã tombstone ở bản ghi nguồn nhưng vẫn còn sống trong bản sao",
                        details={"entity": entity, "total": len(still_live)},
                    )
                )

        return findings

    # --- 8 + 11. Ảnh chụp ----------------------------------------------------

    async def _reconcile_snapshot(
        self, ctx: _Context, *, snapshot_id: str | None, entity: str, override: bool
    ) -> tuple[list[Finding], int]:
        """Kiểm đủ mảnh, tính tập vắng mặt, áp chốt an toàn, và tombstone nếu được phép."""
        findings: list[Finding] = []

        if not snapshot_id:
            return [
                Finding(
                    check_code="SNAPSHOT_ID_MISSING",
                    severity=SEVERITY_ERROR,
                    message="Đối soát phạm vi 'snapshot' nhưng không có snapshot_id",
                    details={"scope": "snapshot"},
                )
            ], 0

        gate = SnapshotGate(ctx.session)
        scope_row = (
            (
                await ctx.session.execute(
                    sa.select(upload_files.c.snapshot_scope)
                    .where(
                        upload_files.c.snapshot_id == snapshot_id,
                        upload_files.c.source_instance_id == ctx.source_instance_id,
                        upload_files.c.snapshot_scope.is_not(None),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        scope = (scope_row or {}).get("snapshot_scope") or {}

        decision = await gate.evaluate(
            project_id=ctx.project_id,
            source_system=ctx.source_system,
            source_instance_id=ctx.source_instance_id,
            snapshot_id=snapshot_id,
            entity=entity,
            scope=scope,
            override=override,
        )
        state = decision.completeness

        # 11. Đủ mảnh chưa.
        findings.append(
            Finding(
                check_code="SNAPSHOT_COMPLETENESS",
                severity=SEVERITY_INFO if (state and state.is_complete) else SEVERITY_WARNING,
                message=(
                    f"Ảnh chụp '{snapshot_id}' đã đủ mảnh"
                    if state and state.is_complete
                    else f"Ảnh chụp '{snapshot_id}' CHƯA đủ mảnh — không suy ra xoá"
                ),
                details=decision.as_details(),
            )
        )

        # 8. Bản ghi cũ sau một ảnh chụp đầy đủ: cảnh báo kèm chi tiết bắt buộc.
        if state and state.is_complete and decision.absent_count:
            findings.append(
                Finding(
                    check_code="STALE_AFTER_SNAPSHOT",
                    severity=SEVERITY_WARNING,
                    entity=entity,
                    message=(
                        f"{decision.absent_count} bản ghi {entity} còn sống nhưng vắng mặt "
                        f"trong ảnh chụp đầy đủ '{snapshot_id}'"
                    ),
                    details=decision.as_details(),
                )
            )

        # Chốt an toàn.
        if not decision.allowed:
            severity = SEVERITY_ERROR if (state and state.is_complete) else SEVERITY_WARNING
            findings.append(
                Finding(
                    check_code="SNAPSHOT_SAFETY_GATE_BLOCKED",
                    severity=severity,
                    entity=entity,
                    message=decision.reason,
                    details=decision.as_details(),
                )
            )
            return findings, 0

        tombstoned = await gate.apply(decision, entity=entity, source_instance_id=ctx.source_instance_id)
        findings.append(
            Finding(
                check_code="SNAPSHOT_TOMBSTONED",
                severity=SEVERITY_INFO,
                entity=entity,
                message=f"Đã tombstone {tombstoned} bản ghi {entity} vắng mặt trong ảnh chụp",
                details={**decision.as_details(), "tombstoned": tombstoned},
            )
        )
        return findings, tombstoned

    # --- Lưu findings --------------------------------------------------------

    async def _persist_findings(
        self, session: AsyncSession, run_id: uuid.UUID, findings: list[Finding], now: datetime
    ) -> None:
        if not findings:
            return
        await session.execute(
            sa.insert(reconciliation_findings),
            [
                {
                    "id": uuid.uuid4(),
                    "reconciliation_run_id": run_id,
                    "check_code": finding.check_code,
                    "severity": finding.severity,
                    "entity": finding.entity,
                    "external_id": finding.external_id,
                    "area_id": finding.area_id,
                    "message": finding.message,
                    "details": finding.details,
                    "created_at": now,
                }
                for finding in findings
            ],
        )


@dataclass(slots=True)
class _Context:
    session: AsyncSession
    project_id: uuid.UUID
    source_system: str
    source_instance_id: str


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _count_by_severity(findings: list[Finding]) -> dict[str, int]:
    counts = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 0, SEVERITY_INFO: 0}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def _count_by_check(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.check_code] = counts.get(finding.check_code, 0) + 1
    return counts
