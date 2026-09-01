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


@pytest.mark.parametrize("module", ["engine.py", "ahp.py", "hierarchical_ahp.py"])
def test_ranking_math_modules_are_pure_no_db_no_network(module):
    """§10.1: 'hàm tính điểm là hàm thuần, không I/O, không mạng'. Không module
    toán nào được import session/engine DB hay client mạng.

    `ahp.py` (công thức V2 — suy trọng số bằng AHP) chịu CÙNG ràng buộc với
    `engine.py`: nó rất muốn đọc thẳng config đang phát hành từ DB, và đúng cái
    tiện đó sẽ biến một hàm kiểm được bằng số học thuần thành thứ phải dựng cả
    database mới chạy được. Tầng API mới là nơi được chạm DB."""
    text = (SRC / "ranking" / module).read_text(encoding="utf-8")
    forbidden = ("sqlalchemy", "asyncio", "httpx", "src.db", "AsyncSession")
    offenders = [word for word in forbidden if word in text]
    assert offenders == [], f"src/ranking/{module} không còn THUẦN — thấy: {offenders}"


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
    allowed = {
        "scripts/migrate.sh",
        "docker/entrypoint.sh",
        "scripts/test_db.sh",
        # Chỉ đưa schema đang có VỀ head trước khi xoá/nạp lại DỮ LIỆU nghiệp
        # vụ (allowlist AbsorpIQ) qua API thật của Mini CRM — không đổi/thêm
        # revision nào, cùng tinh thần với `docker/entrypoint.sh`.
            "scripts/dev-reseed-from-minicrm.sh",
            # Hard reset is an explicit --yes-gated development operation: it
            # upgrades both existing schemas, verifies DB identity, then runs
            # strict table allowlists before any truncation.
            "scripts/dev-reset.sh",
        # Tạo + migrate `minicrm_checkpoint1_test` — database TEST riêng của
        # Mini CRM (hậu tố `_test`, tách biệt khỏi schema PRODUCTION mà luật
        # này canh), gọi từ `make testdb`.
        "scripts/migrate_minicrm_testdb.sh",
    }
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


def test_the_backend_alembic_history_has_one_current_head():
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

      * `0023_seed_domain_demo_2026`, `0024_vinhomes_labels_stats`,
        `0025_synthetic_unit_labels` — DATA migration cho namespace synthetic,
        cộng `7022f5bfa250` gộp hai nhánh `0023_*`. Không bảng nào bị tạo/sửa.
      * `0026_cloudinary_cover_images` — DATA migration, chỉ set
        `cover_image_url` cho dòng đã có.
      * `0027_project_price_observations` — schema migration THUẦN CỘNG THÊM:
        một bảng MỚI cho quan trắc giá niêm yết. Vẫn KHÔNG chạm bốn bảng xếp
        hạng, cũng không chạm `units`/`deals`: hợp đồng đồng bộ cấm trường giá,
        nên giá đi đường vào thứ hai qua bảng riêng. Xem docstring của chính
        revision đó.
      * `0028_unit_status_history`, `0029_deal_status_history` — schema
        migration THUẦN CỘNG THÊM: hai bảng nhật ký append-only dùng cho
        CRM sync, replay/backfill, và audit. Không chạm bốn bảng xếp hạng.
      * `0030_status_history_triggers` — CHỈ thêm trigger DB trên `units`/
        `deals` để phát sinh sự kiện vào hai bảng ở trên. Không cột nào của
        `units`/`deals` bị đổi, không bảng xếp hạng nào bị chạm.
      * `0031_unit_inventory_daily` — schema migration THUẦN CỘNG THÊM cũ;
        bảng vật chất hoá của tính năng đã loại bỏ và được xoá bởi 0036.
      * `0032_replay_identity_index` — CHỈ thêm hai UNIQUE INDEX riêng phần
        trên `unit_status_history`/`deal_status_history` (idempotency cho
        `scripts/backfill_status_history.py`). Không bảng nào khác bị chạm.
      * `0033_ranking_evidence_foundation` — schema migration THUẦN CỘNG THÊM:
        catalog tính năng xếp hạng, snapshot lần chạy, lineage, và giải thích.
        Không đổi `feature_snapshots`/`ranking_configs`/`ranking_scores` hiện có;
        bảng mới rỗng lúc upgrade, chỉ được điền bởi luồng ứng dụng sau này.
      * `0034_expert_ranking_governance` — schema migration THUẦN CỘNG THÊM:
        metadata quản trị chuyên gia + bằng chứng cho `ranking_configs`.
      * `0035_evidence_document_chunks` — schema migration THUẦN CỘNG THÊM:
        kho chunk bằng chứng cho governance.
      * `0036_remove_historical_ranking` — xoá bảng vật chất hoá cũ
        `unit_inventory_daily` và các index/ràng buộc riêng của nó; giữ lại
        hai bảng status-history dùng chung cho sync/backfill/audit.
      * `0037_hierarchical_scoring_pr1` — schema migration THUẦN CỘNG THÊM:
        ba cột nullable — `ranking_scores.hierarchical_score`,
        `ranking_scores.hierarchical_contributions`,
        `ranking_configs.hierarchical_weights` — cho bước tính hạng phân cấp
        song song (D29/D37/D41). Không cột/ràng buộc/writer nào hiện có bị
        đổi; mọi dòng cũ nhận `NULL` ở ba cột mới, một trạng thái hợp lệ
        không phải lỗi.
      * `0038_governance_value_mode` — PR-2: mở rộng CỘNG THÊM ba bảng
        governance đã có (`ranking_feature_definitions.grain` thêm
        `'market'`; `ranking_weight_proposals` nới `scope_type`/`area_id` +
        cột `assertion_kind`, `base_config_id` thành nullable;
        `ranking_feature_justifications` nới `proposed_weight` thành
        nullable + các cột value-mode XOR với nó;
        `ranking_proposal_reviews` thêm hai cột nullable
        `reviewer_subject`/`reviewer_is_ceo`). Mọi dòng cũ nhận
        `assertion_kind='weight'` và các cột mới `NULL` — hành vi weight-mode
        không đổi.
      * `0039_project_value_materialize` — PR-3: một cột nullable duy
        nhất, `ranking_feature_values.source_justification_id` (+ FK tới
        `ranking_feature_justifications`, + index tra cứu) — mối liên kết
        truy vết ngược từ một giá trị Project-grain đã materialize về đúng
        value assertion/CEO-approval đã sinh ra nó. Không bảng/CHECK/trigger
        nào khác bị đổi — `scope_type='project'` đã là giới hạn DUY NHẤT của
        `ranking_feature_values`/`ranking_feature_snapshots` từ `0033`, không
        phải một giới hạn PR-3 mới nới ra.
      * `0040_market_grain_scope` — PR-4: nới CHECK `scope_type` trên
        `ranking_feature_snapshots`/`ranking_feature_values` từ `= 'project'`
        sang `IN ('project', 'market')` (giữ nguyên `area_id`/`unit_id` luôn
        `NULL` cho cả hai — Market cũng denormalized-per-project như Project,
        D39 còn PENDING); cộng thêm hạt giống dữ liệu: bốn
        `ranking_feature_definitions` grain `'market'`
        (`market_interest_rate`/`market_credit_policy`/`market_liquidity`/
        `market_demand`) kèm `definition_metadata.max_shelf_life_days`
        (30/90 ngày, §24.5). Không đổi `ranking_feature_definitions.grain`
        (đã nới ở `0038`) hay scope governance (đã nới ở `0038`).
      * `0041_area_grain_scope` — PR-5: nới CHECK `scope_type` trên
        `ranking_feature_snapshots`/`ranking_feature_values` sang
        `IN ('project', 'market', 'area')`; KHÔNG NHƯ Market, Area cần danh
        tính THẬT theo từng phân khu — `area_id IS NOT NULL` khi
        `scope_type='area'` (CHECK nới thành có điều kiện theo scope), một
        cặp index UNIQUE riêng phần thay cho ràng buộc UNIQUE cũ (một cho
        Project/Market giữ nguyên đúng-một-dòng-mỗi-run, một mới cho
        đúng-một-dòng-mỗi-phân-khu-mỗi-run), và composite FK
        `ranking_feature_values -> ranking_feature_snapshots` nới thêm
        `area_id`. Cộng thêm hạt giống: ba `ranking_feature_definitions`
        grain `'area'` (`area_accessibility`/`area_current_infrastructure`/
        `area_future_infrastructure`) — hai khoá CRM
        (`area_velocity_norm`/`area_conversion_norm`) CỐ Ý không có dòng nào
        ở đây, chúng vẫn là đặc trưng vận hành thuần (`_area_features()`),
        không bao giờ qua value-mode assertion.
      * `0042_legal_assertion_gate` — PR-6: nới CHECK `scope_type` trên
        `ranking_feature_snapshots`/`ranking_feature_values` sang
        `IN ('project', 'market', 'area', 'legal')`, và nới hai CHECK hình
        dạng (`ck_rfs_scope_shape`/`ck_rfv_scope_shape`) để `'legal'` cũng
        buộc `area_id IS NULL` giống `'project'`/`'market'` — không index
        UNIQUE mới nào cần thêm (`uq_rfs_run_project_scope_no_area` đã bao
        MỌI scope_type có `area_id IS NULL`). Cộng thêm hạt giống: đúng một
        `ranking_feature_definitions` grain `'project'`, `value_type`
        `'categorical'` (`project_legal_status`), vocabulary tối giản D40
        (`HIGH_RISK`/`NOT_HIGH_RISK`/`UNKNOWN`) ghi trong
        `definition_metadata.allowed_categorical_values` — không phải một
        CHECK bảng-rộng trên `categorical_value` (xem docstring migration).
      * `0043_unit_enrichment_attributes` — schema migration THUẦN CỘNG THÊM:
        một bảng MỚI, generic, không gắn với một dự án cụ thể nào
        (`unit_enrichment_attributes`, FK tới `units.id`,
        `UNIQUE(unit_id)`), cho dữ liệu bối cảnh/tham chiếu theo từng căn
        (subdivision, tầng, giá niêm yết gốc, provenance/synthetic-origin).
        KHÔNG chạm bốn bảng xếp hạng, KHÔNG chạm `ranking_feature_definitions`
        (không hạt giống nào được thêm) — không có đường đọc nào từ
        `src/ranking/` vào bảng này (xem
        `tests/test_ranking/test_unit_enrichment_not_authoritative.py`).

    Test này ĐỎ mỗi khi có revision mới là TÍN HIỆU ĐÚNG, không phải hồi quy:
    ai cập nhật nó theo hiện thực mới thì đang làm đúng việc."""
    revisions = sorted(p.name for p in (REPO_ROOT / "alembic" / "versions").glob("*.py"))
    assert len(revisions) == 48, f"số revision đã đổi: {len(revisions)} — {revisions}"
    # Sắp theo tên: revision gộp `7022f5bfa250` đứng cuối vì tiền tố chữ, không
    # phải vì nó là head. Head thật là `0036_remove_historical_ranking`.
    assert "0036_remove_historical_ranking.py" in revisions
    assert revisions[-1].startswith("7022f5bfa250_merge_0023_0025")


# --- Bảng governance (0033/0034) cũng chỉ một nơi ghi ------------------------
#
# TÁCH KHỎI `RANKING_TABLES`/`ALLOWED_WRITERS` ở trên có chủ đích: hai hằng đó,
# tên hàm test, và toàn bộ docstring phía trên đều nói "BỐN bảng" — nới chúng
# ra 11 bảng sẽ làm sai tên một bất biến lịch sử cụ thể. Nguyên tắc thì giống
# hệt (một bảng, một nơi ghi); chỉ phạm vi tách riêng.
#
# `src/services/governance.py` — thêm ở P5 (audit 2026-08-25, đóng khoảng
# trống §21.1 của `ranking_consultant.md`: bảng có từ 0033/0034, KHÔNG có
# route/service nào ghi chúng trước module này.
GOVERNANCE_TABLES = (
    "expert_profiles",
    "ranking_weight_proposals",
    "ranking_feature_justifications",
    "ranking_evidence_documents",
    "ranking_evidence_document_features",
    "ranking_proposal_evidence_links",
    "ranking_proposal_reviews",
    "ranking_config_audit_events",
    # 0044 (mandatory-scope item 4): document archive/delete lifecycle log.
    "ranking_evidence_document_lifecycle_events",
    # 0046: versioned evidence-to-value rubrics for qualitative assertions.
    "ranking_feature_rubrics",
    "ranking_feature_rubric_bands",
)
GOVERNANCE_ALLOWED_WRITERS: dict[str, set[str]] = {
    table: {"src/services/governance.py"} for table in GOVERNANCE_TABLES
}
GOVERNANCE_DECLARATION_ONLY = {"src/models/tables.py"}


def test_governance_tables_are_still_declared():
    from src.models import tables

    for name in GOVERNANCE_TABLES:
        assert hasattr(tables, name), f"src/models/tables.py không còn khai báo {name}"


def test_governance_tables_have_exactly_one_writer_module():
    """`ranking_weight_proposals` v.v. chứa đầu vào của chuyên gia và quyết
    định duyệt — cùng lo ngại với `ranking_scores`: nhiều nơi ghi nghĩa là
    trạng thái đề xuất có thể bị đổi từ một nhánh không qua máy trạng thái ở
    `src/services/governance.py`."""
    for table in GOVERNANCE_TABLES:
        assert GOVERNANCE_ALLOWED_WRITERS[table] == {"src/services/governance.py"}, (
            f"{table} phải giữ ĐÚNG một nơi ghi (src/services/governance.py)"
        )


def test_no_module_writes_to_a_governance_table_it_is_not_declared_for():
    writes: list[str] = []
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in GOVERNANCE_DECLARATION_ONLY or relative in GOVERNANCE_ALLOWED_WRITERS.get(
            "expert_profiles", set()
        ):
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for table in GOVERNANCE_TABLES:
            if relative in GOVERNANCE_ALLOWED_WRITERS[table]:
                continue
            for verb in ("insert", "update", "delete"):
                if f"{verb}({table}" in text or f"{verb} into {table}" in lowered:
                    writes.append(f"{relative}: {verb} -> {table}")
    assert writes == [], f"có module ghi vào bảng governance mà nó không được khai báo: {writes}"


# --- Bảng chunk + log trạng thái trích xuất (0035, §21.4) cũng chỉ một nơi ghi
#
# Tách khỏi GOVERNANCE_TABLES có chủ đích, cùng lý do GOVERNANCE_TABLES tách
# khỏi RANKING_TABLES ở trên: nơi ghi khác nhau. Cả hai bảng dưới đây được ghi
# bởi `src/services/evidence_extraction.py` (gọi từ job RQ
# `src/jobs/extract_evidence.py` VÀ từ route enqueue trong `src/api/governance.py`
# — cùng module ghi, hai nơi gọi), KHÔNG phải `src/services/governance.py` —
# dữ liệu là kết quả suy ra (chunk/embedding, log trạng thái trích xuất), không
# phải input trực tiếp của chuyên gia như bảy bảng governance.
#
# `ranking_evidence_extraction_attempts` (mới ở 0035) tồn tại vì
# `ranking_evidence_documents.extraction_status` KHÔNG THỂ là trạng thái thật:
# bảng đó nằm trong bốn bảng bị `ranking_governance_append_only_guard` (0034)
# chặn UPDATE/DELETE — xem docstring migration 0035. Trạng thái thật là dòng
# mới nhất trong log này, cùng khuôn mẫu `unit_status_history`/
# `ranking_config_audit_events` đã dùng thay vì sửa cột trạng thái tại chỗ.
EVIDENCE_CHUNK_TABLES = (
    "ranking_evidence_document_chunks",
    "ranking_evidence_extraction_attempts",
)
EVIDENCE_CHUNK_ALLOWED_WRITERS: dict[str, set[str]] = {
    table: {"src/services/evidence_extraction.py"} for table in EVIDENCE_CHUNK_TABLES
}
EVIDENCE_CHUNK_DECLARATION_ONLY = {"src/models/tables.py"}


def test_evidence_chunk_table_is_still_declared():
    from src.models import tables

    for name in EVIDENCE_CHUNK_TABLES:
        assert hasattr(tables, name), f"src/models/tables.py không còn khai báo {name}"


def test_evidence_chunk_table_has_exactly_one_writer_module():
    for table in EVIDENCE_CHUNK_TABLES:
        assert EVIDENCE_CHUNK_ALLOWED_WRITERS[table] == {"src/services/evidence_extraction.py"}, (
            f"{table} phải giữ ĐÚNG một nơi ghi (src/services/evidence_extraction.py)"
        )


def test_no_module_writes_to_the_evidence_chunk_table_it_is_not_declared_for():
    writes: list[str] = []
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in EVIDENCE_CHUNK_DECLARATION_ONLY:
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for table in EVIDENCE_CHUNK_TABLES:
            if relative in EVIDENCE_CHUNK_ALLOWED_WRITERS[table]:
                continue
            for verb in ("insert", "update", "delete"):
                if f"{verb}({table}" in text or f"{verb} into {table}" in lowered:
                    writes.append(f"{relative}: {verb} -> {table}")
    assert writes == [], f"có module ghi vào bảng evidence-chunk mà nó không được khai báo: {writes}"


# --- PR-3 (0039): Project-grain materialized feature-store tables -----------
#
# `ranking_feature_values`/`ranking_feature_snapshots`/`ranking_feature_lineage`
# (0033) had NO declared writer at all before this PR — `docs/ranking/
# ranking_consultant.md §24.7`'s S6 finding, and `hierarchical_scoring_
# implementation_plan.md §3.3`'s "closing three of the six 0033 tables'
# no-declared-writer-today gap". `src/ranking/service.py`'s
# `materialize_published_feature_value()`/`build_project_feature_snapshot_for_run()`
# are the first and only writers, per the same one-table-one-writer
# discipline this file already enforces for every other governed table.
FEATURE_STORE_TABLES = (
    "ranking_feature_values",
    "ranking_feature_snapshots",
    "ranking_feature_lineage",
)
FEATURE_STORE_ALLOWED_WRITERS: dict[str, set[str]] = {
    table: {"src/ranking/service.py"} for table in FEATURE_STORE_TABLES
}
FEATURE_STORE_DECLARATION_ONLY = {"src/models/tables.py"}


def test_feature_store_tables_are_still_declared():
    from src.models import tables

    for name in FEATURE_STORE_TABLES:
        assert hasattr(tables, name), f"src/models/tables.py không còn khai báo {name}"


def test_feature_store_tables_have_exactly_one_writer_module():
    """Cùng lo ngại với `ranking_scores`/bảng governance: nhiều hơn một nơi
    ghi nghĩa là một giá trị Project-grain có thể xuất hiện mà không truy
    ngược được về đúng CEO approval đã sinh ra nó."""
    for table in FEATURE_STORE_TABLES:
        assert FEATURE_STORE_ALLOWED_WRITERS[table] == {"src/ranking/service.py"}, (
            f"{table} phải giữ ĐÚNG một nơi ghi (src/ranking/service.py)"
        )


def test_no_module_writes_to_a_feature_store_table_it_is_not_declared_for():
    writes: list[str] = []
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in FEATURE_STORE_DECLARATION_ONLY:
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for table in FEATURE_STORE_TABLES:
            if relative in FEATURE_STORE_ALLOWED_WRITERS[table]:
                continue
            for verb in ("insert", "update", "delete"):
                if f"{verb}({table}" in text or f"{verb} into {table}" in lowered:
                    writes.append(f"{relative}: {verb} -> {table}")
    assert writes == [], f"có module ghi vào bảng feature-store mà nó không được khai báo: {writes}"


def test_governance_module_still_writes_no_feature_store_table():
    """The PR-2 boundary suite already asserts `src/services/governance.py`
    writes none of these three tables (`tests/test_services/
    test_governance_pr2_boundaries.py`) — restated here, table-first, so this
    file's own `FEATURE_STORE_ALLOWED_WRITERS` declaration is the single
    source of truth an auditor checks first."""
    text = (SRC / "services" / "governance.py").read_text(encoding="utf-8")
    lowered = text.lower()
    offenders = [
        f"{verb} -> {table}"
        for table in FEATURE_STORE_TABLES
        for verb in ("insert", "update", "delete")
        if f"{verb}({table}" in text or f"{verb} into {table}" in lowered
    ]
    assert offenders == [], f"src/services/governance.py không còn 'không viết feature-store': {offenders}"
