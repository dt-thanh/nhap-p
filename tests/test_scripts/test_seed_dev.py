"""Test `scripts/seed_dev.py` trên PostgreSQL THẬT.

Điều đáng kiểm tra ở một script seed không phải là "có chèn được không" mà là:
tính bất biến khi chạy lại, tính toàn vẹn khoá ngoại, và phủ đủ các trạng thái để
tầng trên có gì mà lọc/sắp xếp/tổng hợp. Ba nhóm đó là ba nhóm test dưới đây.

Chạy:

    TEST_TARGET=tests/test_scripts bash scripts/test_db.sh
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa

from scripts.seed_dev import build_dataset, counts, seed
from tests.conftest import db_skip_reason

# Dọn dẹp dùng chung ở `tests/conftest.py` (Phase 1): URL, engine và câu TRUNCATE
# nay chỉ có một bản. Module này vốn đã TRUNCATE toàn bộ nên chuyển sang là
# tương đương về hành vi, không phải một thay đổi ngữ nghĩa.
_SKIP_REASON = db_skip_reason()

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or ""),
]

TABLE_ORDER = [name for name, _ in build_dataset()]


@pytest_asyncio.fixture
async def empty_db(truncate_all):
    """Database rỗng (đã migrate) cho từng test — bí danh của `truncate_all`.

    Giữ tên cũ để không phải sửa mười mấy chữ ký test trong file này; ngữ nghĩa
    và phạm vi dọn dẹp là của fixture dùng chung.
    """
    return truncate_all


# --------------------------------------------------------------------------
# Tính bất biến khi chạy lại
# --------------------------------------------------------------------------


async def test_seed_populates_every_table_on_an_empty_database(empty_db):
    written = await seed(reset=False, engine=empty_db)
    actual = await counts(TABLE_ORDER, empty_db)

    assert set(actual) == set(TABLE_ORDER)
    empty = [name for name, n in actual.items() if n == 0]
    assert empty == [], f"Các bảng vẫn rỗng sau khi seed: {empty}"
    assert actual == written


async def test_running_seed_twice_does_not_duplicate(empty_db):
    await seed(reset=False, engine=empty_db)
    after_first = await counts(TABLE_ORDER, empty_db)

    await seed(reset=False, engine=empty_db)
    after_second = await counts(TABLE_ORDER, empty_db)

    assert after_second == after_first


async def test_running_seed_a_third_time_with_reset_is_also_stable(empty_db):
    await seed(reset=False, engine=empty_db)
    baseline = await counts(TABLE_ORDER, empty_db)

    await seed(reset=True, engine=empty_db)

    assert await counts(TABLE_ORDER, empty_db) == baseline


async def test_ids_are_deterministic_across_builds():
    """Không có id nào sinh ngẫu nhiên — nền tảng của tính idempotent."""
    first = {name: rows for name, rows in build_dataset()}
    second = {name: rows for name, rows in build_dataset()}

    for name in TABLE_ORDER:
        assert first[name] == second[name], f"Bảng {name} sinh dữ liệu khác nhau giữa hai lần dựng"


async def test_reset_only_deletes_rows_the_seed_owns(empty_db):
    """`--reset` không được đụng vào dữ liệu người dùng tự nhập."""
    await seed(reset=False, engine=empty_db)

    meta = sa.MetaData()
    async with empty_db.begin() as conn:
        await conn.run_sync(meta.reflect, only=["projects", "users"])
        projects, users = meta.tables["projects"], meta.tables["users"]
        owner = (await conn.execute(sa.select(users.c.id).limit(1))).scalar_one()
        await conn.execute(
            projects.insert().values(
                # id ngẫu nhiên: dòng này KHÔNG thuộc không gian id của seed, đúng
                # với thứ mà nó đại diện — dữ liệu người dùng tự nhập.
                id=uuid.uuid4(),
                name="Dự án do người dùng nhập tay",
                launch_date=date(2025, 9, 1),
                created_at=datetime(2025, 9, 1, tzinfo=UTC),
                status="active",
                created_by=owner,
            )
        )

    await seed(reset=True, engine=empty_db)

    async with empty_db.connect() as conn:
        surviving = (
            await conn.execute(
                sa.select(sa.func.count()).select_from(projects).where(projects.c.name.like("Dự án do người dùng%"))
            )
        ).scalar_one()
    assert surviving == 1


# --------------------------------------------------------------------------
# Toàn vẹn dữ liệu
# --------------------------------------------------------------------------


async def test_no_row_violates_any_foreign_key(empty_db):
    """Ép PostgreSQL kiểm tra lại TOÀN BỘ khoá ngoại đã tồn tại.

    Ràng buộc được kiểm tra lúc INSERT, nhưng `VALIDATE`/`ALTER CONSTRAINT` lại là
    cách duy nhất chứng minh không dòng nào lọt lưới — rẻ hơn nhiều so với tự
    viết lại phép kiểm cho từng cặp bảng.
    """
    await seed(reset=False, engine=empty_db)

    async with empty_db.connect() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE contype = 'f' ORDER BY 1, 2"
                )
            )
        ).all()
        assert rows, "Không tìm thấy khoá ngoại nào — schema sai, test vô nghĩa"

        for table, _conname, definition in rows:
            # Bóc "FOREIGN KEY (a, b) REFERENCES parent(x, y)" thành câu đối chiếu.
            head, tail = definition.split(" REFERENCES ")
            child_cols = head.split("(", 1)[1].rstrip(")")
            parent_table, parent_cols = tail.split("(", 1)
            parent_cols = parent_cols.split(")")[0]
            join = " AND ".join(
                f"c.{c.strip()} = p.{p.strip()}"
                for c, p in zip(child_cols.split(","), parent_cols.split(","), strict=True)
            )
            not_null = " AND ".join(f"c.{c.strip()} IS NOT NULL" for c in child_cols.split(","))
            orphans = (
                await conn.execute(
                    sa.text(
                        f"SELECT count(*) FROM {table} c "  # noqa: S608 — tên bảng lấy từ catalog, không từ input
                        f"LEFT JOIN {parent_table.strip()} p ON {join} "
                        f"WHERE {not_null} AND p.* IS NULL"
                    )
                )
            ).scalar_one()
            assert orphans == 0, f"{table}: {orphans} dòng mồ côi theo {definition}"


async def test_seeded_rows_satisfy_every_check_constraint(empty_db):
    """Không dòng nào chỉ 'tình cờ' hợp lệ: bắt PostgreSQL soi lại từng CHECK."""
    await seed(reset=False, engine=empty_db)

    async with empty_db.connect() as conn:
        checks = (
            await conn.execute(
                sa.text(
                    "SELECT conrelid::regclass::text, conname, "
                    "regexp_replace(pg_get_constraintdef(oid), '^CHECK \\s*\\((.*)\\)$', '\\1') "
                    "FROM pg_constraint WHERE contype = 'c' AND conrelid <> 0 ORDER BY 1, 2"
                )
            )
        ).all()
        assert len(checks) > 20, "Số CHECK quá ít so với schema — truy vấn sai"

        for table, conname, expr in checks:
            bad = (
                await conn.execute(sa.text(f"SELECT count(*) FROM {table} WHERE NOT ({expr})"))  # noqa: S608
            ).scalar_one()
            assert bad == 0, f"{table}.{conname}: {bad} dòng vi phạm"


async def test_no_duplicate_business_keys(empty_db):
    """Chạy lại seed không được sinh bản ghi trùng theo KHOÁ NGHIỆP VỤ.

    Khoá chính tất định đã chặn trùng theo id; phép kiểm này bắt trường hợp tệ hơn:
    hai id khác nhau nhưng cùng một thực thể nghiệp vụ.
    """
    await seed(reset=False, engine=empty_db)
    await seed(reset=False, engine=empty_db)

    async with empty_db.connect() as conn:
        uniques = (
            await conn.execute(
                sa.text(
                    "SELECT conrelid::regclass::text, conname, "
                    "regexp_replace(pg_get_constraintdef(oid), '^UNIQUE \\s*\\((.*)\\)$', '\\1') "
                    "FROM pg_constraint WHERE contype = 'u' ORDER BY 1, 2"
                )
            )
        ).all()
        for table, conname, cols in uniques:
            # PostgreSQL coi MỘT bộ toàn NULL không vi phạm UNIQUE (NULL luôn KHÁC
            # NULL) — nhưng GROUP BY thì gộp mọi NULL vào MỘT nhóm, nên phép đếm
            # thô sẽ báo "trùng" giả cho những ràng buộc có cột NULLABLE (ví dụ
            # `uq_projects_source_identity`/`uq_areas_source_identity`, 0017 — dự
            # án/phân khu DI SẢN không có danh tính nguồn nên NULL ở cả hai cột).
            # Loại các dòng có bất kỳ cột nào NULL trước khi gộp, đúng ngữ nghĩa
            # UNIQUE thật của Postgres.
            not_null_clause = " AND ".join(f"{col.strip()} IS NOT NULL" for col in cols.split(","))
            dupes = (
                await conn.execute(
                    sa.text(  # noqa: S608 — tên lấy từ catalog hệ thống
                        f"SELECT count(*) FROM (SELECT {cols} FROM {table} WHERE {not_null_clause} "
                        f"GROUP BY {cols} HAVING count(*) > 1) x"
                    )
                )
            ).scalar_one()
            assert dupes == 0, f"{table}.{conname}: {dupes} nhóm trùng"


# --------------------------------------------------------------------------
# Độ phủ: dữ liệu có đủ đa dạng để tầng trên có gì mà lọc/tổng hợp không
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ("users", "role", {"admin", "manager", "analyst"}),
        ("projects", "status", {"pending", "active", "rejected", "archived"}),
        ("areas", "status", {"pending", "active", "rejected", "archived"}),
        ("upload_files", "status", {"pending", "processing", "completed", "failed"}),
        ("inventory_snapshots", "snapshot_type", {"opening", "closing", "manual", "derived"}),
        ("absorption_daily", "data_quality_status", {"ok", "warning", "error"}),
        ("forecast_jobs", "status", {"completed", "failed", "running"}),
        ("forecast_jobs", "trigger_type", {"manual", "scheduled"}),
        ("forecasts", "confidence_label", {"low", "medium", "high"}),
        ("alerts", "status", {"open", "closed", "dismissed"}),
        ("alerts", "severity", {"low", "medium", "high", "critical"}),
        ("suggestions", "risk_level", {"low", "medium", "high"}),
        ("proposals", "status", {"open", "approved", "rejected", "cancelled"}),
        ("approvals", "decision", {"approved", "rejected"}),
        ("llm_calls", "status", {"success", "error", "timeout"}),
    ],
)
async def test_every_status_value_appears_at_least_once(empty_db, table, column, expected):
    await seed(reset=False, engine=empty_db)
    async with empty_db.connect() as conn:
        found = {r[0] for r in (await conn.execute(sa.text(f"SELECT DISTINCT {column} FROM {table}"))).all()}  # noqa: S608
    assert expected <= found, f"{table}.{column} thiếu: {sorted(expected - found)}"


async def test_optional_fields_are_sometimes_empty_and_sometimes_filled(empty_db):
    """Nếu cột nullable nào cũng có giá trị thì không test được nhánh 'thiếu dữ liệu'."""
    await seed(reset=False, engine=empty_db)
    probes = [
        ("projects", "cover_image_url"),
        ("projects", "review_reason"),
        ("areas", "cover_image_public_id"),
        ("upload_files", "uploaded_by"),
        ("upload_errors", "column_name"),
        ("forecasts", "sellout_date"),
        ("forecasts", "mape"),
        ("forecast_jobs", "finished_at"),
        ("alerts", "closed_at"),
        ("llm_calls", "error_code"),
        ("audit_logs", "user_id"),
        ("refresh_tokens", "revoked_at"),
    ]
    async with empty_db.connect() as conn:
        for table, column in probes:
            nulls, filled = (
                await conn.execute(
                    sa.text(  # noqa: S608
                        f"SELECT count(*) FILTER (WHERE {column} IS NULL), "
                        f"count(*) FILTER (WHERE {column} IS NOT NULL) FROM {table}"
                    )
                )
            ).one()
            assert nulls > 0, f"{table}.{column}: không có dòng nào để trống"
            assert filled > 0, f"{table}.{column}: không có dòng nào có giá trị"


async def test_time_series_is_long_enough_for_pagination_and_aggregation(empty_db):
    """Dashboard và biểu đồ cần chuỗi đủ dài, nhiều phân khu, có cả ngày bằng 0."""
    await seed(reset=False, engine=empty_db)
    async with empty_db.connect() as conn:
        areas_with_sales, days, zero_days = (
            await conn.execute(
                sa.text(
                    "SELECT count(DISTINCT area_id), count(DISTINCT sold_date), "
                    "count(*) FILTER (WHERE units_sold = 0) FROM sales_records"
                )
            )
        ).one()
        assert areas_with_sales >= 3
        assert days >= 30
        assert zero_days > 0

        gap_filled = (
            await conn.execute(sa.text("SELECT count(*) FROM absorption_daily WHERE is_observed IS FALSE"))
        ).scalar_one()
        assert gap_filled > 0, "Không có điểm lấp đầy — không test được nhánh gap-fill"


async def test_many_to_many_assignments_are_not_trivial(empty_db):
    """user_areas phải là N-N thật: có người nhiều phân khu, có phân khu nhiều người."""
    await seed(reset=False, engine=empty_db)
    async with empty_db.connect() as conn:
        multi_area_users = (
            await conn.execute(
                sa.text("SELECT count(*) FROM (SELECT user_id FROM user_areas GROUP BY 1 HAVING count(*) > 1) x")
            )
        ).scalar_one()
        multi_user_areas = (
            await conn.execute(
                sa.text("SELECT count(*) FROM (SELECT area_id FROM user_areas GROUP BY 1 HAVING count(*) > 1) x")
            )
        ).scalar_one()
    assert multi_area_users > 0
    assert multi_user_areas > 0


async def test_seed_contains_no_real_looking_contact_details(empty_db):
    """Chốt chặn: dữ liệu mẫu chỉ được dùng email demo và IP dải tài liệu."""
    await seed(reset=False, engine=empty_db)
    async with empty_db.connect() as conn:
        stray = (
            await conn.execute(sa.text("SELECT count(*) FROM users WHERE email NOT LIKE '%@demo.local'"))
        ).scalar_one()
        assert stray == 0

        # 198.51.100.0/24 là TEST-NET-2 (RFC 5737), không định tuyến trên Internet.
        stray_ip = (
            await conn.execute(sa.text("SELECT count(*) FROM audit_logs WHERE ip_address NOT LIKE '198.51.100.%'"))
        ).scalar_one()
        assert stray_ip == 0

        # Không tài khoản nào đăng nhập được: password_hash không phải hash hợp lệ.
        hashes = {r[0] for r in (await conn.execute(sa.text("SELECT DISTINCT password_hash FROM users"))).all()}
        assert all(not h.startswith("$2") for h in hashes)
