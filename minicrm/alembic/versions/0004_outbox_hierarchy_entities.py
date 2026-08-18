"""Nới `ck_crm_outbox_entity` cho ý định v2 (Phase C): projects/areas/units_v2/deals_v2

Revision ID: 0004_outbox_hierarchy_entities
Revises: 0003_minicrm_hierarchy
Create Date: 2026-08-13

Phase C (`docs/crm/sync_contract_v2_draft.md`) cần LƯU — KHÔNG GỬI — một ý định
đồng bộ v2 cho MỖI lần ghi Project/Area/Unit/Deal, vào ĐÚNG bảng `crm_outbox` đã
có (xem `app/crud.py::_capture_v2`). Bốn entity mới: `"projects"`, `"areas"`,
`"units_v2"`, `"deals_v2"` — hai cái sau mang hậu tố `_v2` để KHÔNG BAO GIỜ lẫn với
dòng v1 `"units"`/`"deals"` (dòng đó vẫn được `deliver()` gửi thật qua HTTP; dòng
v2 thì không — xem `sync_client.V2_CAPTURE_ENTITIES`).

`ck_crm_outbox_entity` (migration 0001) khoá cứng `entity IN ('units', 'deals')`,
nên bốn giá trị trên bị CHẶN ở tầng database nếu không đổi gì — phát hiện được
bằng cách chạy thử, không phải suy luận trước.

**Chọn nới ra `entity <> ''` (danh sách MỞ) thay vì liệt kê thêm bốn giá trị vào
một enum khép kín.** Cùng lý do và cùng tiền lệ đã có trong chính repo này:
`crm_source_records.source_entity` ở BACKEND (`alembic/versions/0006_sync_foundation.py:224`)
dùng đúng khuôn `CHECK <> ''` — không enum — chính XÁC để Phase A "không cần
migration nào cho `crm_source_records`" khi thêm hai entity mới (`project`, `area`).
`phase_a_domain_freeze.md` §A4.3 gọi đây là "phát hiện khiến Phase A không cần
migration". Áp dụng lại nguyên xi ở đây: nếu Phase D hay một phase sau cần thêm
entity thứ năm, nó cũng không cần một migration MỚI chỉ để nới constraint này lần
nữa.

**Phạm vi tối thiểu, có chủ đích.** Đúng MỘT constraint bị đổi. Không bảng mới,
không cột mới, không dữ liệu bị chạm tới — ba dòng test đã có trong
`crm_outbox` (nếu có) không bị ảnh hưởng vì `'units'`/`'deals'` vẫn thoả
`entity <> ''`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_outbox_hierarchy_entities"
down_revision: str | None = "0003_minicrm_hierarchy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_crm_outbox_entity", "crm_outbox", type_="check")
    op.create_check_constraint("ck_crm_outbox_entity", "crm_outbox", "entity <> ''")


def downgrade() -> None:
    """An toàn để chạy NGƯỢC LẠI trên một database mới nâng (không có dữ liệu v2).

    Sẽ NỔ (đúng ý) nếu database đang giữ một dòng `crm_outbox` có `entity` ngoài
    `('units', 'deals')` — downgrade không được phép âm thầm đánh rơi ràng buộc
    mà một dòng dữ liệu hiện có đang vi phạm.
    """
    op.drop_constraint("ck_crm_outbox_entity", "crm_outbox", type_="check")
    op.create_check_constraint("ck_crm_outbox_entity", "crm_outbox", "entity IN ('units', 'deals')")
