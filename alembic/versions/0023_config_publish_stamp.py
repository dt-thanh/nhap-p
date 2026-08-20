"""ck_ranking_configs_published_stamp: ĐẲNG THỨC -> KÉO THEO

Tên revision giữ dưới 32 ký tự: `alembic_version.version_num` là varchar(32),
và một id dài hơn sẽ chạy hết migration rồi mới hỏng ở bước ghi phiên bản.

Revision ID: 0023_config_publish_stamp
Revises: 0022_ranking_config_v2
Create Date: 2026-08-16

0014 viết ràng buộc là ĐẲNG THỨC:

    (status = 'published') = (published_at IS NOT NULL)

Vế trái sai + vế phải đúng ⇒ vi phạm. Nghĩa là một config chuyển sang
`archived` BẮT BUỘC phải xoá `published_at` của chính nó — **lưu trữ làm mất mốc
phát hành gốc**.

Tới 0022 đây mới chỉ là phiền phức một lần: nó chép mốc gốc sang `note` trước
khi xoá, và ghi lại trong docstring rằng cách sửa đúng thuộc về một revision
khác. Revision đó là đây. Lý do phải làm bây giờ: đợt này mở màn hình quản trị
`ranking_configs`, nên lưu trữ chuyển từ "một lần trong migration" thành **thao
tác thường ngày của người vận hành**. Một ràng buộc xoá dữ liệu kiểm toán mỗi
lần publish là một ràng buộc hỏng.

Bản mới là KÉO THEO — `published` thì bắt buộc có mốc, `archived` thì được giữ
lại mốc cũ:

    status <> 'published' OR published_at IS NOT NULL

Vẫn chặn đúng cái 0014 muốn chặn: một dòng `published` mà không truy được thời
điểm. `tests/.../test_published_config_without_published_at_is_rejected` giữ
nguyên màu xanh — dạng kéo theo vẫn từ chối đúng trường hợp đó.

`ck_ranking_configs_archived_stamp` KHÔNG đụng tới: nó nói `archived` thì phải
có `archived_at`, và đó là đẳng thức đúng — không có trạng thái nào khác được
phép mang `archived_at`.

Không dòng dữ liệu nào bị sửa. v1 (do 0022 lưu trữ) vẫn có `published_at = NULL`
và mốc gốc nằm trong `note` — revision này KHÔNG cố khôi phục lại, vì giá trị
đó chỉ còn ở dạng văn bản và đoán ngược ra timestamp là bịa dữ liệu kiểm toán.
Từ đây trở đi thì không mất nữa.

`downgrade()` đưa lại dạng đẳng thức, nhưng sẽ THẤT BẠI nếu lúc đó đang tồn tại
một config `archived` còn giữ `published_at` — đúng như vậy: lùi về một ràng
buộc chặt hơn mà im lặng xoá dữ liệu để lọt qua thì tệ hơn là dừng lại.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023_config_publish_stamp"
down_revision: str | None = "0022_ranking_config_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_ranking_configs_published_stamp"
IMPLICATION = "status <> 'published' OR published_at IS NOT NULL"
EQUALITY = "(status = 'published') = (published_at IS NOT NULL)"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "ranking_configs", type_="check")
    op.create_check_constraint(CONSTRAINT, "ranking_configs", IMPLICATION)


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "ranking_configs", type_="check")
    op.create_check_constraint(CONSTRAINT, "ranking_configs", EQUALITY)
