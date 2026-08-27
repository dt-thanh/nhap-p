"""Fixture dùng chung cho test Mini CRM.

Test cần DB chạy trên một database RIÊNG, tên kết thúc bằng `_test` — cùng quy ước
an toàn với backend, vì cùng một lý do: fixture ở đây TẠO VÀ HUỶ database, và trỏ
nhầm vào database Mini CRM đang dùng là mất dữ liệu thật.

Từ Phase 4, mỗi test CRUD chạy trên một database DÙNG MỘT LẦN của riêng nó
(`mccrud_<hex>_test`). Đắt hơn TRUNCATE, nhưng nó mua về một thứ TRUNCATE không
mua được: **dãy sinh `external_id` cũng khởi động lại**, nên mọi test đều bắt đầu
ở `U-0001`/`D-0001` và khẳng định được về giá trị id THẬT thay vì về một biến.
Một dãy dùng chung sẽ khiến khẳng định đó phụ thuộc vào thứ tự chạy test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

MINICRM_ROOT = Path(__file__).resolve().parents[1]

# Dự án + phân khu TỔNG HỢP mà test dùng. Trùng với dữ liệu seed của backend để
# một payload dựng trong test cũng là payload gửi thật được. `TEST_PROJECT_ID` là
# UUID của phía NHẬN (v1: project_ref.project_id) — KHÔNG liên quan tới
# `TEST_PROJECT_EXTERNAL_ID`/`TEST_AREA_EXTERNAL_ID` dưới đây, vốn là danh tính
# CỤC BỘ của Mini CRM cho phân cấp Project/Area mà chính nó sở hữu (Phase B).
TEST_PROJECT_ID = "40fa9c11-4e0f-589c-80a4-9bcb1f889960"
TEST_AREA_NAME = "DEMO Toà B1"
TEST_UNIT_TYPE = "Căn hộ"

# Danh tính CỤC BỘ của phân khu/dự án BOOTSTRAP mà `crm_app` seed sẵn — xem
# `_seed_bootstrap_hierarchy`. Tiền tố "BOOTSTRAP-" cố ý KHÔNG khớp khuôn
# "P-%04d"/"A-%04d" mà `next_external_id()` cấp, nên seed này không lấn vào dãy
# số của app: dự án/phân khu ĐẦU TIÊN một test tự tạo qua API vẫn là "P-0001"/
# "A-0001".
TEST_PROJECT_EXTERNAL_ID = "BOOTSTRAP-PROJECT"
TEST_AREA_EXTERNAL_ID = "BOOTSTRAP-AREA"
TARGET_TEST_DATABASE = "minicrm_checkpoint1_test"
TARGET_ONLY_ENV = "MINICRM_TARGET_DATABASE_ONLY"

# Token D-14 dùng chung cho test. `crm_app` cấu hình đúng ba token này ở MỌI
# database rỗng dùng một lần — sequence `external_id` khởi động lại mỗi test
# (xem docstring module), nên dự án ĐẦU TIÊN một test tự tạo LUÔN là "P-0001",
# và phạm vi của `OPERATOR_TOKEN` cố định được đúng hai giá trị này một cách an
# toàn: "BOOTSTRAP-PROJECT" (seed sẵn) và "P-0001" (dự án đầu tiên nếu test tự
# tạo). `ADMIN_TOKEN` mang phạm vi "ALL" — hầu hết test CRUD hiện có dùng token
# này làm mặc định để không phải sửa từng lời gọi; `test_auth.py` mới là nơi
# kiểm phạm vi HẸP của operator một cách có chủ đích.
ADMIN_TOKEN = "test-admin-token"  # noqa: S105 - test fixture, không phải bí mật thật
OPERATOR_TOKEN = "test-operator-token"  # noqa: S105
VIEWER_TOKEN = "test-viewer-token"  # noqa: S105
ADMIN_AUTH_HEADER = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
OPERATOR_AUTH_HEADER = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
VIEWER_AUTH_HEADER = {"Authorization": f"Bearer {VIEWER_TOKEN}"}


def db_url() -> str | None:
    return os.getenv("MINICRM_TEST_DATABASE_URL") or os.getenv("MINICRM_DATABASE_URL")


def target_only_mode() -> bool:
    return os.getenv(TARGET_ONLY_ENV) == "1"


def _assert_target_database(url: str) -> None:
    if urlsplit(url).path.lstrip("/") != TARGET_TEST_DATABASE:
        raise pytest.UsageError(f"Target-only tests require database '{TARGET_TEST_DATABASE}'")
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.connect() as connection:
            current = connection.execute(sa.text("SELECT current_database()")).scalar_one()
            if current != TARGET_TEST_DATABASE:
                raise pytest.UsageError(f"Connected database is not '{TARGET_TEST_DATABASE}'")
    finally:
        engine.dispose()


def _reset_target_tables(url: str) -> None:
    """Reset only Mini CRM tables inside the explicitly approved test database."""
    tables = (
        "crm_password_reset_tokens",
        "crm_auth_invites",
        "crm_auth_sessions",
        "crm_deals",
        "crm_units",
        "crm_areas",
        "crm_projects",
        "crm_outbox",
        "crm_users",
    )
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"))
            for sequence in (
                "crm_unit_external_seq",
                "crm_deal_external_seq",
                "crm_project_external_seq",
                "crm_area_external_seq",
            ):
                connection.execute(sa.text(f"ALTER SEQUENCE {sequence} RESTART WITH 1"))
    finally:
        engine.dispose()


def db_skip_reason() -> str:
    url = db_url()
    if not url:
        return "Không có MINICRM_TEST_DATABASE_URL/MINICRM_DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' vì tên không kết thúc bằng '_test'"
    return ""


def pytest_sessionstart(session):
    """Chặn cả lượt chạy nếu database đích không phải database test."""
    url = db_url()
    if target_only_mode() and not url:
        raise pytest.UsageError(f"{TARGET_ONLY_ENV}=1 requires MINICRM_TEST_DATABASE_URL")
    if not url:
        return
    name = urlsplit(url).path.lstrip("/")
    if target_only_mode() and name != TARGET_TEST_DATABASE:
        raise pytest.UsageError(f"Target-only tests require database '{TARGET_TEST_DATABASE}'")
    if name and not name.endswith("_test"):
        raise pytest.UsageError(
            f"TỪ CHỐI CHẠY: database đích là '{name}', không kết thúc bằng '_test'."
        )


def sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def run_alembic(url: str, *args: str) -> None:
    """Chạy alembic của MINI CRM (cwd = minicrm/), với MINICRM_DATABASE_URL riêng."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=MINICRM_ROOT,
        env={**os.environ, "MINICRM_DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"alembic {' '.join(args)} failed for database '{TARGET_TEST_DATABASE}'")


@pytest.fixture
def scratch_db_url():
    """Một database trống, dùng một lần, huỷ ngay sau test."""
    base = db_url()
    if not base:
        pytest.skip(db_skip_reason())

    if target_only_mode():
        _assert_target_database(base)
        yield base
        return

    import sqlalchemy as sa

    name = f"mccrud_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(sync_url(with_database(base, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield with_database(base, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name}
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _seed_bootstrap_hierarchy(url: str) -> None:
    """Seed MỘT dự án + MỘT phân khu, khớp `TEST_AREA_NAME`/`TEST_UNIT_TYPE`.

    Lý do tồn tại: Phase B đổi `POST /units` sang BẮT BUỘC tham chiếu một phân
    khu cục bộ có thật (`external_area_id`, hoặc `area_name`+`unit_type` tự tra
    đúng MỘT phân khu khớp — xem `app.crud._resolve_area_reference`). Không có
    seed này, MỌI test đang dùng hình dạng CŨ (`{"area_name":..., "unit_type":...}`
    không kèm `external_area_id`, có từ trước Phase B) sẽ nhận `AREA_NOT_FOUND`.

    Ghi thẳng bằng SQL đồng bộ, KHÔNG qua `app.crud`: fixture này chạy trước khi
    ứng dụng được trỏ vào database (biến môi trường `MINICRM_DATABASE_URL` chưa
    set), và tạo một app/session chỉ để seed hai dòng là phức tạp hoá không cần
    thiết. Ba số kế hoạch (`bedrooms`/`area_sqm`/`total_units`) là placeholder
    TỔNG HỢP rõ ràng — không giả vờ là số liệu nghiệp vụ thật, đúng tinh thần của
    toàn bộ Mini CRM (`SYNTHETIC_DISCLAIMER`).
    """
    engine = sa.create_engine(sync_url(url))
    now = "now()"
    try:
        with engine.begin() as conn:
            project_id = conn.execute(
                sa.text(
                    f"""
                    INSERT INTO crm_projects (id, external_id, name, launch_date, status, source_revision, created_at, updated_at)
                    VALUES (gen_random_uuid(), :external_id, :name, :launch_date, 'active', 1, {now}, {now})
                    RETURNING id
                    """
                ),
                {
                    "external_id": TEST_PROJECT_EXTERNAL_ID,
                    "name": "Dự án BOOTSTRAP cho test (tổng hợp)",
                    "launch_date": "2026-01-01",
                },
            ).scalar_one()
            conn.execute(
                sa.text(
                    f"""
                    INSERT INTO crm_areas
                        (id, external_id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units,
                         status, source_revision, created_at, updated_at)
                    VALUES
                        (gen_random_uuid(), :external_id, :project_id, :area_name, :unit_type, 2, 68.5, 999,
                         'active', 1, {now}, {now})
                    """
                ),
                {
                    "external_id": TEST_AREA_EXTERNAL_ID,
                    "project_id": project_id,
                    "area_name": TEST_AREA_NAME,
                    "unit_type": TEST_UNIT_TYPE,
                },
            )
    finally:
        engine.dispose()


@pytest.fixture
def crm_app(scratch_db_url, monkeypatch):
    """Ứng dụng Mini CRM trỏ vào một database trống đã migrate tới head.

    Cache của `get_settings`/`get_engine`/`get_session_factory` bị XOÁ ở cả hai
    đầu fixture. Không xoá ở đầu ra thì test tiếp theo sẽ dùng lại engine trỏ vào
    một database vừa bị DROP, và lỗi hiện ra ở một file test hoàn toàn khác.
    """
    run_alembic(scratch_db_url, "upgrade", "head")
    if target_only_mode():
        _assert_target_database(scratch_db_url)
        _reset_target_tables(scratch_db_url)
    _seed_bootstrap_hierarchy(scratch_db_url)

    from app.config import get_settings
    from app.db import get_engine, get_session_factory

    def _clear():
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_session_factory.cache_clear()

    _clear()
    monkeypatch.setenv("MINICRM_DATABASE_URL", scratch_db_url)
    monkeypatch.setenv("MINICRM_SYNC_BASE_URL", "http://api:8000")
    monkeypatch.setenv("MINICRM_SYNC_API_KEY", "afsk_test_key")
    monkeypatch.setenv("MINICRM_SOURCE_SYSTEM", "mini_crm")
    monkeypatch.setenv("MINICRM_SOURCE_INSTANCE_ID", "mini-crm-dev")
    monkeypatch.setenv("MINICRM_PROJECT_ID", TEST_PROJECT_ID)
    # `.env` của máy không được lọt vào: nó có thể mang một project id khác và
    # test sẽ khẳng định về một giá trị không ai kiểm soát.
    monkeypatch.setenv("MINICRM_RUN_MIGRATIONS", "false")
    monkeypatch.setenv("MINICRM_APP_ENV", "development")
    monkeypatch.setenv("MINICRM_AUTHORIZATION_MODE", "global_visibility")
    # D-14: ba token vai trò + phạm vi. Xem hằng số ADMIN_TOKEN/OPERATOR_TOKEN ở
    # trên cho lý do các giá trị phạm vi cụ thể.
    monkeypatch.setenv("MINICRM_AUTH_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("MINICRM_AUTH_PIPELINE_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.setenv("MINICRM_AUTH_BUSINESS_VIEWER_TOKEN", VIEWER_TOKEN)
    monkeypatch.setenv(
        "MINICRM_AUTH_PROJECT_SCOPE",
        json.dumps({ADMIN_TOKEN: "ALL", OPERATOR_TOKEN: [TEST_PROJECT_EXTERNAL_ID, "P-0001"]}),
    )
    # `app/auth.py::authenticate()` giờ trả RỖNG cho cả ba token D-14 ở trên khi
    # cờ này tắt (mặc định — xem app/config.py). Test CRUD hiện có dùng
    # ADMIN_AUTH_HEADER/OPERATOR_AUTH_HEADER/VIEWER_AUTH_HEADER làm đường xác
    # thực DUY NHẤT (chưa có Keycloak trong tiến trình test), nên phải bật ở
    # đây — đúng tinh thần "dev/CI" mà biến này được thiết kế cho.
    monkeypatch.setenv("MINICRM_LEGACY_TOKEN_AUTH_ENABLED", "true")

    try:
        yield scratch_db_url
    finally:
        engine = get_engine()
        engine.sync_engine.dispose()
        _clear()


class FakeBackend:
    """Một backend GIẢ, cố ý NGU.

    Nó biết đúng hai điều mà hợp đồng nói rõ và không suy diễn thêm gì: cùng
    `external_batch_id` ⇒ trả kết quả cũ kèm `replayed=true`; ngoài ra thì đếm
    bản ghi rồi trả 202.

    **Nó KHÔNG mô phỏng tầng so phiên bản của backend**, và đó là chủ đích. Viết
    một bản sao `SourceIdentityService` ở đây sẽ tạo ra một phép thử tự chấm điểm
    mình: mọi kết luận về `skip_stale`, `conflict` hay `duplicate_noop` sẽ là kết
    luận về BẢN SAO trong file test, không phải về backend. Những kết luận đó chỉ
    có ở `test_real_backend_sync.py`, nơi HTTP đi tới container thật.

    Việc của backend giả chỉ là: Mini CRM dựng ra ĐÚNG cái gì, gửi tới ĐÚNG chỗ
    nào, và ghi lại ĐÚNG những gì nhận về.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.batches: dict[str, dict] = {}
        self.mode = "accept"
        self.failure = (422, {"detail": {"error_code": "CONTRACT_VALIDATION_FAILED"}})

    def fail_with(self, status_code: int, body: dict) -> None:
        self.mode = "fail"
        self.failure = (status_code, body)

    def time_out(self) -> None:
        self.mode = "timeout"

    def accept(self) -> None:
        self.mode = "accept"

    @property
    def envelopes(self) -> list[dict]:
        return [r["body"] for r in self.requests]

    def handler(self, request):
        import json

        import httpx

        body = json.loads(request.content)
        self.requests.append(
            {"url": str(request.url), "api_key": request.headers.get("X-API-Key"), "body": body}
        )

        if self.mode == "timeout":
            raise httpx.ReadTimeout("quá hạn", request=request)
        if self.mode == "fail":
            return httpx.Response(self.failure[0], json=self.failure[1])

        batch_id = body["external_batch_id"]
        if batch_id in self.batches:
            return httpx.Response(200, json={**self.batches[batch_id], "replayed": True})

        records = body.get("records", [])
        upserts = sum(1 for r in records if r.get("operation") == "upsert")
        deletes = sum(1 for r in records if r.get("operation") == "delete")
        result = {
            "sync_run_id": str(uuid.uuid4()),
            "status": "completed",
            "replayed": False,
            "rows_received": len(records),
            "rows_ok": len(records),
            "rows_failed": 0,
            "decisions": {k: v for k, v in (("insert", upserts), ("tombstone", deletes)) if v},
            "projections": {k: v for k, v in (("inserted", upserts), ("tombstoned", deletes)) if v},
        }
        self.batches[batch_id] = result
        return httpx.Response(202, json=result)


@pytest.fixture
def backend(monkeypatch):
    """Chặn MỌI đường ra HTTP của Mini CRM và thay bằng `FakeBackend`.

    Vá `app.sync_client.http_client` chứ không truyền `client=` vào từng lời gọi:
    test đi qua đường HTTP của FastAPI, nên nó không có chỗ nào để chuyền một
    client vào. Vá ở đây cũng bảo đảm KHÔNG có request thật nào thoát ra — một
    test vô tình gọi vào backend dev sẽ làm bẩn dữ liệu thật.
    """
    import httpx

    fake = FakeBackend()
    monkeypatch.setattr(
        "app.sync_client.http_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )
    return fake
