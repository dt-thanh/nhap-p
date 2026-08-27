"""Contract checks for the operator-managed Cloudinary image data migration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "0026_cloudinary_cover_images.py"


spec = importlib.util.spec_from_file_location("migration_0026_cloudinary_images", MIGRATION_PATH)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)


def test_revision_is_after_current_merge_head():
    assert migration.revision == "0026_cloudinary_cover_images"
    assert migration.down_revision == "7022f5bfa250"


def test_operator_mappings_allow_only_public_cloudinary_urls_or_unset_values():
    for mapping in (migration.PROJECT_IMAGES, migration.AREA_IMAGES):
        assert all(
            image_url is None or image_url.startswith("https://res.cloudinary.com/")
            for image_url in mapping.values()
        )


# --- downgrade() policy: intentional no-op, documented and tested ----------
#
# Audit finding (backend/data-systems audit, 2026-08-22): downgrade() is a
# no-op even though upgrade() can set cover_image_url. This migration is
# APPLIED in every environment that has run `alembic upgrade head` past
# 0026 (confirmed live: dev DB is at head 0032) — rewriting its upgrade()/
# downgrade() bodies in place is exactly what the migration rules here forbid
# for an applied/shared revision. The committed PROJECT_IMAGES/AREA_IMAGES
# mappings are BOTH still empty (every value commented out), so upgrade()
# performs zero actual writes in any environment built from this exact
# commit — confirmed live: `SELECT count(*) FROM projects/areas WHERE
# cover_image_url IS NOT NULL` is 0 in both tables on the dev database. The
# risk the audit describes (an operator fills in a URL, applies it, then a
# downgrade silently discards it) is therefore latent, not realized, in any
# shared history so far. See pipeline_status.md ("Migration Status") for the
# documented operational rollback procedure this test protects.


def test_downgrade_is_intentionally_a_no_op():
    """Guards against a well-meaning future edit "fixing" this without
    understanding why: a generic downgrade() that nulls every external_id in
    PROJECT_IMAGES/AREA_IMAGES would be WRONG the moment any of those rows'
    cover_image_url has been legitimately updated by something else since
    upgrade() ran (the migration's own upgrade() docstring says as much)."""
    import inspect

    source = inspect.getsource(migration.downgrade)
    body_lines = [
        line.strip()
        for line in source.splitlines()[1:]
        if line.strip() and not line.strip().startswith("#")
    ]
    assert body_lines == ["pass"], (
        "downgrade() đã đổi từ no-op sang có hành vi thật — nếu đây là một quyết "
        "định có chủ đích (ví dụ một quy trình rollback vận hành mới), cập nhật "
        "lại test này VÀ mục 'Migration Status' trong pipeline_status.md."
    )


def test_committed_mappings_are_still_empty():
    """Nếu ai đó điền URL thật vào đây và commit, `upgrade()` sẽ không còn là
    no-op nữa ở mọi môi trường khác chạy từ commit này — đúng rủi ro mà audit
    nêu. Test này canh đúng tiền đề "hiện tại luôn rỗng", không canh mãi mãi:
    nếu nó đỏ vì ai đó thật sự cần điền ảnh bìa, hãy đọc kèm docstring của
    migration trước khi commit URL thật vào một file được áp dụng lại ở mọi
    lần build."""
    assert migration.PROJECT_IMAGES == {} or all(v is None for v in migration.PROJECT_IMAGES.values())
    assert migration.AREA_IMAGES == {} or all(v is None for v in migration.AREA_IMAGES.values())


def test_apply_cover_urls_skips_missing_external_id_without_raising():
    """`upgrade()` phải chịu được một `external_id` không còn tồn tại (dự án bị
    xoá giữa lúc soạn migration và lúc áp dụng) — im lặng bỏ qua, không 500 khi
    `alembic upgrade head` chạy tự động."""
    import sqlalchemy as sa

    table = sa.table("projects", sa.column("external_id"), sa.column("cover_image_url"))

    class _FakeResult:
        rowcount = 0

    class _FakeBind:
        def execute(self, _statement):
            return _FakeResult()

    # Không raise là đủ để chứng minh hành vi "bỏ qua, không nổ".
    migration._apply_cover_urls(_FakeBind(), table, {"prj_does_not_exist": "https://res.cloudinary.com/x/y.jpg"})


def test_apply_cover_urls_rejects_a_non_cloudinary_url():
    import sqlalchemy as sa

    table = sa.table("projects", sa.column("external_id"), sa.column("cover_image_url"))

    class _FakeBind:
        def execute(self, _statement):  # pragma: no cover - không nên tới được đây
            raise AssertionError("không được thực thi UPDATE với URL không hợp lệ")

    with pytest.raises(ValueError, match="Cloudinary URL required"):
        migration._apply_cover_urls(_FakeBind(), table, {"prj_x": "https://evil.example.com/steal.jpg"})
