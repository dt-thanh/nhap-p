"""Ranh giới Phase 6, viết lại cho hiện thực mới: động cơ xếp hạng ĐÃ có.

File này trước đây khẳng định "chưa có gì ghi vào bốn bảng xếp hạng" — đúng
TRƯỚC Phase 6. Đúng như docstring gốc đã tiên liệu: "Khi Phase 6 bắt đầu, đúng
những test này phải ĐỎ — và đó là tín hiệu chuyển phase, không phải một hồi
quy... ai sửa nó theo hiện thực mới thì đang làm đúng việc." Phase 6 bắt đầu ở
đợt tích hợp AI Agent (`src/ranking/`, `src/api/agent.py`,
`alembic/versions/0018_agent_recommendations.py`) — xem pipeline_status.md đợt
tương ứng.

Ranh giới MỚI, hẹp hơn nhưng vẫn thật:

* Ranh giới ghi tính THEO BẢNG (xem `ALLOWED_WRITERS` bên dưới, kèm lý do).
  `ranking_scores`/`ranking_runs` — kết quả mô hình — vẫn CHỈ có
  `src/ranking/service.py` ghi. `feature_snapshots`/`ranking_configs` có thêm
  một đường nhập cho dữ liệu do NGƯỜI tạo, và mỗi bảng chỉ nhận đúng những
  module đã khai báo.
* `agent_recommendations` KHÔNG có đường nào tạo thẳng ở trạng thái
  `'approved'`/`'rejected'` — `AGENTS.md` coi bước duyệt người là bắt buộc.
* Động cơ xếp hạng (`src/ranking/engine.py`) vẫn là hàm THUẦN — không import
  bất kỳ thứ gì từ `src.db`/SQLAlchemy session (§10.1: "hàm tính điểm là hàm
  thuần, không I/O, không mạng").
* `src/api/ranking.py` ĐỌC bốn bảng này và gọi `run_ranking` — nó KHÔNG tự
  viết câu INSERT/UPDATE/DELETE nào, nên ranh giới "một nơi ghi duy nhất" ở
  trên vẫn còn nguyên. Ranh giới đó nói về NGƯỜI GHI, không cấm người đọc.
* Lát cắt hiện tại CHƯA có: `units.listed_at` (và do đó `days_on_market`).
  `GET /ranking`, worker RQ, cò sau sync, cò sau đổi config, và endpoint khảo
  sát đều TỪNG nằm trong danh sách này và nay đã có — sửa dòng này theo hiện
  thực là làm đúng việc, không phải làm hỏng một bất biến.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

RANKING_TABLES = ("feature_snapshots", "ranking_configs", "ranking_runs", "ranking_scores")

# Nơi được phép NHẮC TÊN bốn bảng xếp hạng: khai báo schema, và động cơ thật.
DECLARATION_ONLY = {"src/models/tables.py"}

# Ranh giới ghi, tính THEO BẢNG chứ không theo file.
#
# Bản trước cho đúng MỘT file (`src/ranking/service.py`) ghi cả bốn bảng. Ranh
# giới đó đúng khi bốn bảng đều chỉ chứa KẾT QUẢ TÍNH TOÁN. Nay hai trong bốn
# bảng có đầu vào do NGƯỜI nhập:
#
#   * `feature_snapshots` nhận đặc trưng khảo sát từ bộ tổng hợp bên ngoài
#     (`source='survey_external'`) — dữ liệu không có mô hình nào sinh ra được.
#   * `ranking_configs` nhận bộ trọng số do người vận hành soạn.
#
# Ép hai đường nhập đó chui qua `src/ranking/service.py` chỉ để giữ nguyên câu
# chữ của một test sẽ biến module tính toán thành cái sọt đựng mọi thứ.
#
# Cái ranh giới này LUÔN bảo vệ vẫn không đổi: **`ranking_scores` và
# `ranking_runs` — hai bảng chứa kết quả mô hình — vẫn chỉ có MỘT nơi ghi.** Đó
# đúng là lo ngại gốc của file này: một dòng ghi từ module không liên quan sẽ
# khiến bảng điều khiển hiện ra số liệu không có mô hình nào đứng sau. Đầu vào
# do người nhập không tạo ra nguy cơ đó.
ALLOWED_WRITERS: dict[str, set[str]] = {
    "ranking_scores": {"src/ranking/service.py"},
    "ranking_runs": {"src/ranking/service.py"},
    "feature_snapshots": {"src/ranking/service.py", "src/services/survey_features.py"},
    "ranking_configs": {"src/services/ranking_config.py"},
}


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


# --- Mỗi bảng xếp hạng có đúng tập nơi ghi đã khai báo -------------------------


def test_model_result_tables_have_exactly_one_writer():
    """`ranking_scores`/`ranking_runs` chứa KẾT QUẢ MÔ HÌNH. Nhiều hơn một nơi
    ghi nghĩa là bảng điều khiển có thể hiện ra số liệu không có lần chạy nào
    đứng sau — đúng lo ngại gốc của file này."""
    for table in ("ranking_scores", "ranking_runs"):
        assert ALLOWED_WRITERS[table] == {"src/ranking/service.py"}, (
            f"{table} phải giữ ĐÚNG một nơi ghi; nới nó ra là bỏ chính bất biến file này canh"
        )


def test_no_module_writes_to_a_ranking_table_it_is_not_declared_for():
    """Không nhánh ghi lén: mọi câu ghi phải đến từ module ĐÃ khai báo cho đúng
    bảng đó. Thêm một writer mới là một quyết định tường minh, sửa ở đây."""
    writes: list[str] = []
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in DECLARATION_ONLY or relative in ALLOWED_WRITERS:
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for table in RANKING_TABLES:
            if relative in ALLOWED_WRITERS[table]:
                continue
            for verb in ("insert", "update", "delete"):
                if f"{verb}({table}" in text or f"{verb} into {table}" in lowered:
                    writes.append(f"{relative}: {verb} -> {table}")
    assert writes == [], f"có module ghi vào bảng xếp hạng mà nó không được khai báo: {writes}"


def test_ranking_engine_is_a_pure_function_no_db_no_network():
    """§10.1: 'hàm tính điểm là hàm thuần, không I/O, không mạng'. `engine.py`
    không được import session/engine DB hay bất kỳ client mạng nào."""
    text = (SRC / "ranking" / "engine.py").read_text(encoding="utf-8")
    forbidden = ("sqlalchemy", "asyncio", "httpx", "src.db", "AsyncSession")
    offenders = [word for word in forbidden if word in text]
    assert offenders == [], f"src/ranking/engine.py không còn THUẦN — thấy: {offenders}"


# --- agent_recommendations luôn khởi tạo chờ duyệt ---------------------------


def test_create_recommendation_always_inserts_pending_approval():
    """Không đường nào trong `src/api/agent.py` chèn thẳng status khác
    'pending_approval' — bước duyệt người của AGENTS.md không có lối tắt."""
    text = (SRC / "api" / "agent.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    insert_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "insert"
    ]
    assert insert_calls, "không tìm thấy câu insert nào vào agent_recommendations — kiểm lại test này"
    # Câu insert() phải nằm trong một .values(status="pending_approval", ...)
    assert 'status="pending_approval"' in text or "status='pending_approval'" in text


def test_no_route_can_set_a_recommendation_to_approved_or_rejected_except_the_decision_endpoints():
    """`app.openapi()` — bảng định tuyến THẬT — chỉ có đúng hai route mang động
    từ quyết định (`approve`/`reject`)."""
    from src.main import app

    paths = list(app.openapi()["paths"])
    decision_routes = [p for p in paths if "approve" in p.lower() or "reject" in p.lower()]
    assert set(decision_routes) == {
        "/api/v1/agent/recommendations/{rec_id}/approve",
        "/api/v1/agent/recommendations/{rec_id}/reject",
    }


def test_approving_requires_a_higher_role_than_read_only_viewing():
    """Duyệt là một QUYẾT ĐỊNH ghi — `require_approver` phải đòi vai trò cao
    hơn `require_viewer`, không được đứng cùng mức."""
    from src.api.agent import require_approver, require_viewer
    from src.services.dashboard_auth import _ROLE_LEVEL

    # Cả hai là factory dependency đóng trên `minimum`; đọc lại qua closure.
    viewer_minimum = require_viewer.__closure__[0].cell_contents
    approver_minimum = require_approver.__closure__[0].cell_contents
    assert _ROLE_LEVEL[approver_minimum] > _ROLE_LEVEL[viewer_minimum]


# --- Bảng của Phase 2 vẫn còn nguyên hình dạng (bất biến, không đổi) ---------


def test_the_four_ranking_tables_are_still_declared():
    from src.models import tables

    for name in RANKING_TABLES:
        assert hasattr(tables, name), f"src/models/tables.py không còn khai báo {name}"


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("feature_snapshots", {"project_id", "feature_key", "scope", "scope_id"}),
        ("ranking_configs", {"version", "status", "weights"}),
        ("ranking_runs", {"project_id", "status", "sync_run_id"}),
        ("ranking_scores", {"ranking_run_id", "unit_id", "score", "rank_in_project"}),
    ],
)
def test_the_ranking_tables_keep_the_columns_the_engine_uses(table, expected):
    from src.models import tables

    columns = {c.name for c in getattr(tables, table).columns}
    missing = expected - columns
    assert missing == set(), f"{table} thiếu cột {missing}"


# --- Quy trình đổi schema — bất biến, không đổi ------------------------------


def test_only_the_migration_script_and_the_dev_entrypoint_run_alembic_upgrade():
    """Đổi schema CHỈ đi qua `scripts/migrate.sh` (có sao lưu đã kiểm chứng)."""
    allowed = {"scripts/migrate.sh", "docker/entrypoint.sh", "scripts/test_db.sh"}
    offenders: list[str] = []
    for path in list(REPO_ROOT.glob("*.sh")) + sorted((REPO_ROOT / "scripts").glob("*.sh")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in allowed:
            continue
        if "alembic upgrade" in path.read_text(encoding="utf-8"):
            offenders.append(relative)

    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    migrate_recipe = [
        line for line in makefile.splitlines() if line.startswith("\t") and "alembic upgrade" in line
    ]
    assert migrate_recipe == [], f"Makefile gọi thẳng `alembic upgrade`: {migrate_recipe}"
    assert "scripts/migrate.sh" in makefile, "Makefile không còn trỏ tới scripts/migrate.sh"
    assert offenders == [], f"script gọi thẳng `alembic upgrade`, bỏ qua bước sao lưu: {offenders}"


def test_the_migration_script_still_requires_a_verified_backup():
    text = (REPO_ROOT / "scripts" / "migrate.sh").read_text(encoding="utf-8")
    assert "pg_dump" in text, "migrate.sh không còn sao lưu trước khi migrate"
    assert "pg_restore" in text or "--list" in text, "migrate.sh không còn KIỂM CHỨNG bản sao lưu"
    assert '[ -s "$BACKUP" ]' in text, "migrate.sh không còn từ chối một bản sao lưu rỗng"


def test_the_backend_alembic_history_is_now_twentythree_linear_revisions():
    """Phase 6 mở đầu thêm `0018_agent_recommendations` (bảng
    `agent_recommendations`). Ba revision sau đó KHÔNG phải schema migration
    theo nghĩa `create_table`/`add_column` cho bốn bảng xếp hạng:

      * `0019_seed_ai_crm_fixture`  — DATA migration, nạp fixture AI/dev từ
        `crm_real_data.json` vào `projects`/`areas`/`units`/`sales_records`/
        `inventory_snapshots`/`absorption_daily`. KHÔNG chạm `deals`.
      * `0020_agent_advisory_execution` — hành động tư vấn có cấu trúc + thi
        hành có kiểm toán.
      * `0021_seed_ai_crm_fixture_deals` — DATA migration, lấp đúng khoảng
        trống `deals` mà 0019 cố ý để lại: chiếu `units` đã seed + hình dạng
        tuần của lineage `legacy_aggregate` thành giao dịch từng căn, và làm
        `units.status` khớp lại với `deals`. Vẫn KHÔNG chạm bốn bảng xếp hạng
        của Phase 2 (0014/0015) — xem
        `test_the_ranking_tables_keep_the_columns_the_engine_uses` và docstring
        của chính hai migration đó cho lý do đầy đủ.
      * `0022_ranking_config_v2` — DATA migration, lưu trữ `ranking_configs` v1
        và phát hành v2. Vẫn KHÔNG phải schema migration: nó đi đúng con đường
        CHỈ-THÊM mà 0014 dựng sẵn cho việc đổi trọng số, không sửa bảng nào.
        Lý do đổi (kèm số đo) nằm ở docstring của chính revision đó.
      * `0023_config_publish_stamp` — đổi
        `ck_ranking_configs_published_stamp` từ ĐẲNG THỨC sang KÉO THEO, để lưu
        trữ một config không còn buộc phải XOÁ `published_at` của nó. Cần thiết
        vì đợt này mở màn hình quản trị: lưu trữ chuyển từ "một lần trong
        migration" thành thao tác thường ngày.

    Test này ĐỎ mỗi khi có revision mới là TÍN HIỆU ĐÚNG, không phải hồi quy:
    ai cập nhật nó theo hiện thực mới thì đang làm đúng việc."""
    revisions = sorted(p.name for p in (REPO_ROOT / "alembic" / "versions").glob("*.py"))
    assert len(revisions) == 23, f"số revision đã đổi: {len(revisions)} — {revisions}"
    assert revisions[-1].startswith("0023_config_publish_stamp")
