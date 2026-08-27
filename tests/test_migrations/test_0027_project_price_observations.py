"""Contract checks for the price-observation schema migration.

Ba thứ test này giữ, và mỗi thứ đều là một cách hỏng đã thấy thật:

1. **Bảng mới không được đụng bảng cũ.** `units` là bản sao một chiều của hệ
   nguồn; một `add_column` lọt vào đây sẽ phá mô hình sở hữu mà không lỗi nào
   bật lên tới khi đồng bộ chạy.
2. **Khoá ngoại phải cùng kiểu với `units.id` (UUID).** Đặc tả ban đầu ghi
   Integer; nếu lọt thì `alembic upgrade head` mới là nơi phát hiện.
3. **Hình chiếu Core phải khớp migration.** `src/models/tables.py` là bản chiếu,
   migration là nguồn sự thật — cùng nguyên tắc với
   `test_0015_ranking_results.py::test_core_table_definitions_match_the_migrated_schema`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from src.models.tables import project_price_observations

ROOT = Path(__file__).parents[2]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "0027_project_price_observations.py"

spec = importlib.util.spec_from_file_location("migration_0027_price_observations", MIGRATION_PATH)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)

SOURCE = MIGRATION_PATH.read_text(encoding="utf-8")


def test_revision_follows_the_cloudinary_head():
    assert migration.revision == "0027_project_price_observations"
    assert migration.down_revision == "0026_cloudinary_cover_images"


def test_the_migration_touches_no_existing_table():
    """Thuần cộng thêm: chỉ tạo bảng mới, không sửa/không xoá bảng nào đang có."""
    for forbidden in ("add_column", "drop_column", "alter_column", "op.execute"):
        assert forbidden not in SOURCE, f"0027 không được dùng {forbidden}"
    for owned_by_source_system in ('"units"', '"deals"', '"areas"', '"projects"'):
        assert f"op.create_table({owned_by_source_system}" not in SOURCE
        assert f"op.add_column({owned_by_source_system}" not in SOURCE


def test_downgrade_removes_exactly_what_upgrade_created():
    # Migration tham chiếu bảng/index qua hằng số module, không phải chuỗi lặp
    # lại — nên kiểm theo tên hằng số.
    assert "op.create_table(\n        TABLE," in SOURCE
    assert "op.drop_table(TABLE)" in SOURCE
    for index_const in ("INDEX_UNIT", "INDEX_CURRENT"):
        assert f"op.create_index(\n        {index_const}" in SOURCE or f"op.create_index({index_const}" in SOURCE
        assert f"op.drop_index({index_const}, table_name=TABLE)" in SOURCE
    # Hằng số phải trỏ đúng tên thật đã tạo trong database.
    assert migration.TABLE == "project_price_observations"
    assert migration.INDEX_UNIT == "ix_price_obs_unit_id"
    assert migration.INDEX_CURRENT == "ix_price_obs_unit_current"


def test_the_core_projection_matches_the_migrated_columns():
    """Bản chiếu Core phải khớp từng cột với migration."""
    assert project_price_observations.name == "project_price_observations"
    assert set(project_price_observations.c.keys()) == {
        "id",
        "unit_id",
        "official_price",
        "effective_from",
        "effective_to",
        "source",
        "created_at",
    }


def test_the_foreign_key_uses_the_same_type_as_units_id():
    """`units.id` là UUID. Integer ở đây sẽ hỏng lúc `alembic upgrade head`."""
    from src.models.tables import units

    assert str(project_price_observations.c.unit_id.type) == str(units.c.id.type) == "UUID"
    assert str(project_price_observations.c.id.type) == "UUID"


def test_nullable_semantics_separate_current_price_from_expired_price():
    """`effective_to IS NULL` = giá ĐANG áp dụng, khác một ngày trong quá khứ."""
    assert project_price_observations.c.effective_to.nullable is True
    assert project_price_observations.c.effective_from.nullable is False
    assert project_price_observations.c.official_price.nullable is False


def test_price_is_numeric_with_currency_scale():
    price_type = project_price_observations.c.official_price.type
    assert price_type.precision == 18
    assert price_type.scale == 2
