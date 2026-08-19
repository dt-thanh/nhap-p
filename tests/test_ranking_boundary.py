"""Ranh giới Phase 6, viết lại cho hiện thực mới: động cơ xếp hạng ĐÃ có.

File này trước đây khẳng định "chưa có gì ghi vào bốn bảng xếp hạng" — đúng
TRƯỚC Phase 6. Đúng như docstring gốc đã tiên liệu: "Khi Phase 6 bắt đầu, đúng
những test này phải ĐỎ — và đó là tín hiệu chuyển phase, không phải một hồi
quy... ai sửa nó theo hiện thực mới thì đang làm đúng việc." Phase 6 bắt đầu ở
đợt tích hợp AI Agent (`src/ranking/`, `src/api/agent.py`,
`alembic/versions/0018_agent_recommendations.py`) — xem pipeline_status.md đợt
tương ứng.

Ranh giới MỚI, hẹp hơn nhưng vẫn thật:

* CHỈ `src/ranking/service.py` được ghi vào bốn bảng xếp hạng — không module
  nghiệp vụ nào khác được tự ý ghi (tránh một nhánh ghi lén thứ hai xuất hiện
  ở đâu đó, đúng lo ngại gốc của file này).
* `agent_recommendations` KHÔNG có đường nào tạo thẳng ở trạng thái
  `'approved'`/`'rejected'` — `AGENTS.md` coi bước duyệt người là bắt buộc.
* Động cơ xếp hạng (`src/ranking/engine.py`) vẫn là hàm THUẦN — không import
  bất kỳ thứ gì từ `src.db`/SQLAlchemy session (§10.1: "hàm tính điểm là hàm
  thuần, không I/O, không mạng").
* Lát cắt hiện tại CHƯA có: worker RQ, cò kích hoạt sau sync, endpoint khảo
  sát, endpoint đọc xếp hạng độc lập (`GET /ranking/...`). Đây vẫn là phạm vi
  CHƯA làm, ghi rõ để không ai đọc lướt rồi tưởng đã đủ so với
  `docs/ranking/implementation_plan.md`.
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
ALLOWED_WRITERS = {"src/ranking/service.py"}


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


# --- Chỉ MỘT nơi ghi vào bốn bảng xếp hạng ------------------------------------


def test_only_ranking_service_writes_to_a_ranking_table():
    """Không nhánh ghi lén thứ hai — đúng lo ngại gốc mà file này được viết ra
    để canh: một dòng ghi từ một module không liên quan sẽ khiến bảng điều
    khiển hiện ra số liệu không có mô hình nào đứng sau."""
    writes: list[str] = []
    for path in _python_files():
        relative = str(path.relative_to(REPO_ROOT))
        if relative in DECLARATION_ONLY or relative in ALLOWED_WRITERS:
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for table in RANKING_TABLES:
            for verb in ("insert", "update", "delete"):
                if f"{verb}({table}" in text or f"{verb} into {table}" in lowered:
                    writes.append(f"{relative}: {verb} -> {table}")
    assert writes == [], f"có mã NGOÀI src/ranking/service.py đang ghi vào bảng xếp hạng: {writes}"


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
        relative = str(path.relative_to(REPO_ROOT))
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


def test_the_backend_alembic_history_is_now_nineteen_linear_revisions():
    """Phase 6 mở đầu thêm `0018_agent_recommendations` (bảng
    `agent_recommendations`). `0019_seed_ai_crm_fixture` thêm SAU đó là một DATA
    migration thuần (không `create_table`/`add_column` nào) — nạp fixture AI/dev
    từ `crm_real_data.json` vào `projects`/`areas`/`units`/`sales_records`/
    `inventory_snapshots`/`absorption_daily`, KHÔNG chạm bốn bảng xếp hạng của
    Phase 2 (0014/0015) hay `deals` — xem
    `test_the_ranking_tables_keep_the_columns_the_engine_uses` và docstring của
    chính migration 0019 cho lý do đầy đủ."""
    revisions = sorted(p.name for p in (REPO_ROOT / "alembic" / "versions").glob("*.py"))
    assert len(revisions) == 19, f"số revision đã đổi: {len(revisions)} — {revisions}"
    assert revisions[-1].startswith("0019_seed_ai_crm_fixture")
