import json
import os
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# Phase E: token dashboard MẶC ĐỊNH cho toàn bộ test suite — trước Phase E, các
# route ĐỌC dự án/phân khu/tồn kho/giao dịch không có tầng xác thực nào, nên
# hàng trăm test hiện có gọi chúng KHÔNG kèm header. Đặt env TRƯỚC khi import
# `src.main` (bên dưới) để `get_settings()` không cache một giá trị rỗng trước
# khi bất kỳ test nào kịp chạy. `ALL` cho cả operator lẫn admin: test hiện có
# kiểm HÀNH VI ĐỌC/NGHIỆP VỤ, không kiểm giới hạn phạm vi — đó là việc riêng của
# `test_project_scope.py` (phạm vi hẹp, tự monkeypatch cấu hình của chính nó).
#
# GHI ĐÈ TRỰC TIẾP, không `setdefault`: `scripts/test_db.sh` nạp TOÀN BỘ `.env`
# thật vào môi trường trước khi gọi pytest, và `.env` dev giờ có sẵn
# DASHBOARD_ADMIN_TOKEN thật (để kiểm bằng tay qua trình duyệt/curl). Nếu dùng
# `setdefault`, biến đó coi như đã có giá trị và giữ nguyên token thật — còn
# `DASHBOARD_AUTH_HEADER` bên dưới vẫn mang token giả cố định của conftest, nên
# mọi request trong test suite sẽ nhận 401 dù backend hoạt động đúng. Test suite
# phải độc lập với nội dung tình cờ của `.env` thật, nên ép giá trị test cố định
# ở đây bất kể môi trường đã có gì.
DASHBOARD_VIEWER_TOKEN = "conftest-dashboard-viewer-token"
DASHBOARD_OPERATOR_TOKEN = "conftest-dashboard-operator-token"
DASHBOARD_ADMIN_TOKEN = "conftest-dashboard-admin-token"
DASHBOARD_AUTH_HEADER = {"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}
os.environ["DASHBOARD_BUSINESS_VIEWER_TOKEN"] = DASHBOARD_VIEWER_TOKEN
os.environ["DASHBOARD_PIPELINE_OPERATOR_TOKEN"] = DASHBOARD_OPERATOR_TOKEN
os.environ["DASHBOARD_ADMIN_TOKEN"] = DASHBOARD_ADMIN_TOKEN
os.environ["DASHBOARD_PROJECT_SCOPE"] = json.dumps(
    {DASHBOARD_ADMIN_TOKEN: "ALL", DASHBOARD_OPERATOR_TOKEN: "ALL"}
)

from src.main import app  # noqa: E402 - phải sau khi đặt env DASHBOARD_* ở trên

# --- Hạ tầng test dùng chung cho DB thật -------------------------------------
#
# Trước Phase 1, mười module test tự khai lại CÙNG bốn thứ: cách đọc URL, hàm
# `_skip_reason`, một engine riêng, và một fixture dọn dẹp. Bản sao thì lệch, và
# nó ĐÃ lệch: xem mục "thêm test mới làm 175 test đang xanh chuyển thành lỗi"
# trong pipeline_status.md.
#
# Phần dưới đây là bản dùng chung. Nó KHÔNG thay thế fixture riêng của tám module
# dùng DELETE theo phạm vi — những fixture đó hẹp hơn và nhanh hơn, đổi chúng là
# một lần viết lại rộng mà Phase 1 không cần. Chỉ hai module vốn đã TRUNCATE toàn
# bộ mới chuyển sang đây.


def db_url() -> str | None:
    """URL database test. `TEST_DATABASE_URL` là chính, `DATABASE_URL` là dự phòng."""
    return os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def db_skip_reason() -> str:
    """Lý do bỏ qua test cần DB thật, chuỗi rỗng nghĩa là chạy được.

    Chốt `_test` ở đây là chốt THỨ HAI, không phải chốt duy nhất: `pytest_sessionstart`
    bên dưới đã chặn cả lượt chạy. Giữ cả hai vì chúng trả lời hai câu khác nhau —
    "có được phép chạy không" và "module này có gì để chạy không".
    """
    url = db_url()
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' — chạy `bash scripts/test_db.sh`"
    return ""


# ĐIỂM MỞ RỘNG DUY NHẤT cho bảng mà bộ seed KHÔNG sinh ra.
#
# Bốn bảng xếp hạng (0014, 0015) được nối vào đây ở Phase 2. Bỏ sót thì dòng của
# chúng sống sót giữa các test, và lỗi sẽ hiện ra ở một module không liên quan.
#
# Thứ tự là thứ tự PHỤ THUỘC, con trước cha: `ranking_scores` trỏ vào
# `ranking_runs` và `ranking_configs`; `ranking_runs` trỏ vào `ranking_configs`.
# `TRUNCATE ... CASCADE` không đòi thứ tự này, nhưng giữ đúng nó khiến danh sách
# tự nói ra quan hệ giữa các bảng, và nó là thứ duy nhất còn đúng nếu sau này
# `CASCADE` bị bỏ.
#
# `ranking_configs` bị TRUNCATE cùng: mỗi test dựng lấy config nó cần. Config v1
# do migration 0014 seed KHÔNG sống sót qua một lượt test — đó là chủ đích, vì một
# test dựa vào dữ liệu seed của migration là một test phụ thuộc thứ tự chạy.
EXTRA_TRUNCATE_TABLES: tuple[str, ...] = (
    "ranking_scores",
    "ranking_runs",
    "ranking_configs",
    "feature_snapshots",
    # Phase 6: đề xuất tư vấn — có FK CASCADE tới `projects`/`areas` nên TRUNCATE
    # CASCADE của các bảng đó đã quét nó, liệt kê tường minh chỉ để tài liệu hoá
    # quan hệ, giống `ranking_scores` ở trên.
    "agent_recommendations",
    # P5 (audit 2026-08-25): governance 0033/0034 — `src/services/governance.py`
    # + `tests/test_services/test_governance.py`. Con trước cha, cùng quy ước:
    # `ranking_evidence_document_features` trỏ `ranking_evidence_documents` +
    # `ranking_feature_justifications`; `ranking_config_audit_events`/
    # `ranking_proposal_reviews`/`ranking_evidence_documents` trỏ
    # `ranking_weight_proposals`/`expert_profiles`; `ranking_feature_justifications`
    # trỏ `ranking_weight_proposals` + `ranking_feature_definitions`.
    "ranking_evidence_document_features",
    "ranking_config_audit_events",
    "ranking_proposal_reviews",
    # 0035 (§21.4): chunks + log trạng thái trích xuất đều trỏ
    # `ranking_evidence_documents`, phải dọn trước nó.
    "ranking_evidence_document_chunks",
    "ranking_evidence_extraction_attempts",
    "ranking_evidence_documents",
    "ranking_feature_justifications",
    "ranking_weight_proposals",
    "expert_profiles",
    # PR-3 (0039): materialized Project-grain feature values — con trước cha,
    # cùng quy ước: `ranking_feature_lineage` trỏ `ranking_feature_values`
    # trỏ `ranking_feature_snapshots` (+ `ranking_feature_definitions` qua
    # `feature_definition_id`, cả hai đã có từ 0033, và `ranking_feature_justifications`
    # qua `source_justification_id` mới, 0039).
    "ranking_feature_lineage",
    "ranking_feature_values",
    "ranking_feature_snapshots",
    # 0033 — không bảng nào ở trên FK tới nó cần dọn trước, nhưng test P5 tự
    # tạo hàng ở đây làm fixture nên nó phải được dọn cùng, không cách nào
    # CASCADE tới được nó từ các bảng con phía trên.
    "ranking_feature_definitions",
)


def truncate_tables() -> tuple[str, ...]:
    """MỘT nguồn duy nhất cho danh sách bảng cần dọn sạch.

    Phần chính lấy từ `scripts.seed_dev.build_dataset()` vì bộ seed vốn đã phải
    biết mọi bảng nghiệp vụ theo đúng thứ tự phụ thuộc; cộng thêm
    `EXTRA_TRUNCATE_TABLES` cho những bảng nằm ngoài phạm vi seed.
    """
    from scripts.seed_dev import build_dataset

    return tuple(name for name, _ in build_dataset()) + EXTRA_TRUNCATE_TABLES


@pytest_asyncio.fixture
async def db_engine():
    """Engine async trỏ vào database test. NullPool — xem `src/db.py`."""
    engine = create_async_engine(db_url(), poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def truncate_all(db_engine):
    """Bàn giao một database RỖNG (đã migrate), và trả lại nó rỗng.

    Dọn CẢ HAI đầu, không chỉ đầu vào: các module khác dùng chung database này và
    fixture dọn dẹp của chúng chỉ biết vài bảng của luồng nạp file. Để sót một
    hàng `forecasts` là `DELETE FROM areas` của chúng nổ khoá ngoại, và lỗi hiện
    ra ở một file test hoàn toàn không liên quan.

    Tên là `truncate_all`, KHÔNG phải `clean_db`: tám module đang có một fixture
    `clean_db` riêng với ngữ nghĩa hẹp hơn hẳn. Trùng tên thì fixture của module
    che fixture ở đây — không vỡ, nhưng người đọc sẽ không biết cái nào đang chạy.
    """
    statement = sa.text(
        "TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in truncate_tables()) + " RESTART IDENTITY CASCADE"
    )
    async with db_engine.begin() as conn:
        await conn.execute(statement)
    yield db_engine
    async with db_engine.begin() as conn:
        await conn.execute(statement)


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing API endpoints.

    Gắn sẵn header admin (Phase E, phạm vi ALL) — hầu hết test ở đây kiểm hành vi
    nghiệp vụ, không kiểm chính cơ chế phạm vi; truyền `headers=` riêng vào một
    lời gọi cụ thể sẽ GHI ĐÈ, không cộng dồn (httpx: header cùng tên ở request
    thắng header mặc định của client).
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=DASHBOARD_AUTH_HEADER) as ac:
        yield ac


@pytest.fixture
def mock_llm():
    """Mock LLM to avoid calling OpenAI during tests.

    Usage in test:
        def test_something(mock_llm):
            # LLM calls will return mock response instead of hitting OpenAI
            ...
    """
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock


def pytest_sessionstart(session):
    """CHẶN CẢ LƯỢT CHẠY nếu database đích không phải là database test.

    Từng module test tự khai một hàm `_refuses_to_wipe`/`_skip_reason` kiểm tên
    database kết thúc bằng `_test`. Quy ước đó đã KHÔNG giữ được: hơn mười module
    chỉ kiểm "có URL hay không", và trong số đó có những module xoá dữ liệu.

    Một quy ước phải nhớ ở từng file là một quy ước sẽ bị quên. Chốt này nằm ở
    conftest — một chỗ duy nhất, áp cho mọi module, kể cả module viết sau này.

    Không có cờ bỏ qua. Chạy đúng cách:  bash scripts/test_db.sh
    """
    import os
    from urllib.parse import urlsplit

    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        # Không có URL thì các module tự bỏ qua phần cần DB. Không phải lỗi.
        return

    name = urlsplit(url).path.lstrip("/")
    if name and not name.endswith("_test"):
        raise pytest.UsageError(
            f"TỪ CHỐI CHẠY: database đích là '{name}', không kết thúc bằng '_test'.\n"
            f"Bộ test có những module TRUNCATE/DELETE toàn bảng; trỏ nhầm vào database dev "
            f"là mất dữ liệu thật.\n"
            f"Chạy: bash scripts/test_db.sh"
        )
