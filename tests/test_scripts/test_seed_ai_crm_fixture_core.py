"""Test thuần (không DB) cho `scripts/_seed_ai_crm_fixture_core.py`: xây dựng
statement, tính tất định của id, và các đường lỗi khi tham chiếu hỏng.

Không cần DB: mọi hàm ở đây CHỈ xây `Executable`, không bao giờ `.execute()`.
"""

from __future__ import annotations

import pytest

from scripts._seed_ai_crm_fixture_core import (
    SOURCE_INSTANCE_ID,
    SOURCE_SYSTEM,
    SeedError,
    build_downgrade_statements,
    build_upserts,
    uid,
)

MINIMAL_DATA = {
    "projects": [{"id": "prj_a", "name": "Project A", "launch_date": "2026-01-01"}],
    "dash_areas": [
        {
            "id": "ar_a1",
            "project_id": "prj_a",
            "name": "Area A1",
            "unit_type": "Studio",
            "bedrooms": 0,
            "area_sqm": 30.5,
            "total_units": 10,
            "sold": 6,
            "remaining": 4,
        }
    ],
    "trend_by_area": {"ar_a1": [{"date": "2026-05-01", "units_sold": 2}, {"date": "2026-05-08", "units_sold": 1}]},
    "units": [
        {"id": "ar_a1_u001", "area_id": "ar_a1", "unit_code": "A1-01", "unit_type": "Studio", "status": "available"},
        {"id": "ar_a1_u002", "area_id": "ar_a1", "unit_code": "A1-02", "unit_type": "Studio", "status": "reserved"},
    ],
    "files": [{"id": "f1", "filename": "x.csv", "status": "completed", "rows_ok": 5, "rows_failed": 0, "uploaded_at": "2026-05-01T00:00:00Z"}],
    "sample_errors": [{"id": 1, "file_id": "f1", "row_number": 1, "column_name": "x", "error_code": "E", "message": "m"}],
}


def test_uid_is_deterministic_and_namespaced_per_kind():
    assert uid("project", "prj_a") == uid("project", "prj_a")
    assert uid("project", "prj_a") != uid("area", "prj_a"), "kind khác nhau phải sinh id khác nhau cho cùng khoá"


def test_build_upserts_counts_match_input():
    plan = build_upserts(MINIMAL_DATA)
    assert plan.counts["projects"] == 1
    assert plan.counts["areas"] == 1
    assert plan.counts["units"] == 2
    assert plan.counts["files"] == 1
    assert plan.counts["sample_errors"] == 1
    assert plan.counts["sales_records"] == 1
    assert plan.counts["inventory_snapshots"] == 1
    assert plan.counts["absorption_daily"] == 2


def test_build_upserts_orders_parents_before_children():
    plan = build_upserts(MINIMAL_DATA)
    order = [name for name, _ in plan.statements]
    assert order.index("projects") < order.index("areas") < order.index("units")


def test_build_upserts_stamps_every_row_with_fixture_identity():
    plan = build_upserts(MINIMAL_DATA)
    for table_name, stmt in plan.statements:
        if table_name not in ("projects", "areas", "units", "upload_files"):
            continue
        params = stmt.compile().params
        assert params.get("source_system") == SOURCE_SYSTEM, table_name
        assert params.get("source_instance_id") == SOURCE_INSTANCE_ID, table_name


def test_build_upserts_row_ids_are_deterministic_from_json_ids():
    plan = build_upserts(MINIMAL_DATA)
    project_stmt = next(stmt for name, stmt in plan.statements if name == "projects")
    assert project_stmt.compile().params["id"] == uid("project", "prj_a")


def test_unit_referencing_unknown_area_raises_seed_error():
    bad = dict(MINIMAL_DATA, units=[{"id": "u_x", "area_id": "does-not-exist", "unit_code": "X", "unit_type": "Studio", "status": "available"}])
    with pytest.raises(SeedError, match="does-not-exist"):
        build_upserts(bad)


def test_area_referencing_unknown_project_raises_seed_error():
    bad = dict(MINIMAL_DATA, dash_areas=[{**MINIMAL_DATA["dash_areas"][0], "project_id": "no-such-project"}])
    with pytest.raises(SeedError, match="no-such-project"):
        build_upserts(bad)


def test_sample_error_referencing_unknown_file_raises_seed_error():
    bad = dict(MINIMAL_DATA, sample_errors=[{"id": 99, "file_id": "no-such-file", "error_code": "E", "message": "m"}])
    with pytest.raises(SeedError, match="no-such-file"):
        build_upserts(bad)


def test_unmapped_file_status_raises_loudly_instead_of_guessing():
    """`ck_upload_files_status` không nhận trạng thái tự do của nguồn — một giá
    trị chưa có trong FILE_STATUS_MAP phải làm nổ SeedError, không âm thầm rơi
    mất hay đoán một giá trị gần đúng."""
    bad = dict(MINIMAL_DATA, files=[{**MINIMAL_DATA["files"][0], "status": "not_a_real_status"}])
    with pytest.raises(SeedError, match="ck_upload_files_status"):
        build_upserts(bad)


def test_source_success_and_partial_file_statuses_map_to_supported_enum():
    data = dict(
        MINIMAL_DATA,
        files=[
            {**MINIMAL_DATA["files"][0], "id": "f1", "status": "success"},
            {"id": "f2", "filename": "y.csv", "status": "partial", "rows_ok": 1, "rows_failed": 1, "uploaded_at": "2026-05-01T00:00:00Z"},
        ],
        sample_errors=[],
    )
    plan = build_upserts(data)
    statuses = [stmt.compile().params["status"] for name, stmt in plan.statements if name == "upload_files"]
    assert "success" not in statuses and "partial" not in statuses
    assert "completed" in statuses
    assert "partially_completed" in statuses


def test_absorption_daily_leaves_units_remaining_null_for_every_point():
    """NULL = không có snapshot lịch sử, không phải 0 — kể cả điểm mới nhất
    (điểm hiện tại đi qua `inventory_snapshots`, KHÔNG trộn vào `absorption_daily`)."""
    plan = build_upserts(MINIMAL_DATA)
    absorption_stmts = [stmt for name, stmt in plan.statements if name == "absorption_daily"]
    assert len(absorption_stmts) == 2
    for stmt in absorption_stmts:
        assert stmt.compile().params["units_remaining"] is None


def test_absorption_daily_uses_the_legacy_calculator_label():
    plan = build_upserts(MINIMAL_DATA)
    stmt = next(stmt for name, stmt in plan.statements if name == "absorption_daily")
    assert stmt.compile().params["calculator"] == "legacy_aggregate"


def test_build_downgrade_statements_scopes_every_table_by_fixture_identity_or_fk():
    stmts = build_downgrade_statements()
    assert len(stmts) == 8
    compiled = [str(s.compile(compile_kwargs={"literal_binds": False})) for s in stmts]
    # Mọi câu lệnh phải nhắc tới bảng có cột source_system/instance TRỰC TIẾP,
    # hoặc một subquery lọc theo bảng đó (areas/upload_files) — không câu nào
    # được là một DELETE không điều kiện.
    for c in compiled:
        assert "WHERE" in c, f"DELETE không điều kiện — có nguy cơ xoá dữ liệu ngoài fixture: {c}"
