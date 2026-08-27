"""Test tầng API upload.

Redis và Postgres được thay bằng stub: ở đây chỉ kiểm tra hợp đồng của router —
validate request, gọi service, map kết quả sang response. Hành vi ghi DB đã có
test riêng chạy trên Postgres thật ở tests/test_services/test_import_records.py.
"""

import csv
import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services import file_upload as file_upload_module
from tests.conftest import DASHBOARD_ADMIN_TOKEN, DASHBOARD_OPERATOR_TOKEN, DASHBOARD_VIEWER_TOKEN

UPLOAD_URL = "/api/v1/files/upload"
LIST_URL = "/api/v1/files"
STATUS_URL = "/api/v1/files/{file_id}/status"
ERRORS_URL = "/api/v1/files/{file_id}/errors"
ERRORS_CSV_URL = "/api/v1/files/{file_id}/errors.csv"

PROJECT_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
FILE_ID = "11111111-2222-3333-4444-555555555555"


class FakeQueue:
    def __init__(self, fail=False):
        self.fail = fail
        self.enqueued = []
        self.connection = object()

    def enqueue(self, func, **kwargs):
        if self.fail:
            raise ConnectionError("redis down")
        self.enqueued.append((func, kwargs))
        return SimpleNamespace(id="job-1")


class FakeDb:
    """Thay cho các hàm chạm DB trong src.api.files."""

    def __init__(self):
        self.project_exists = True
        self.duplicate_of = None
        self.rows: dict[str, SimpleNamespace] = {}
        self.errors: list[dict] = []
        self.deleted: list[str] = []

    def add_file(
        self,
        file_id=FILE_ID,
        *,
        status="pending",
        rows_ok=0,
        rows_failed=0,
        filename="sales.csv",
        project_id=PROJECT_ID,
    ):
        self.rows[file_id] = SimpleNamespace(
            id=uuid.UUID(file_id),
            project_id=uuid.UUID(project_id) if project_id else None,
            filename=filename,
            status=status,
            rows_ok=rows_ok,
            rows_failed=rows_failed,
            uploaded_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        return file_id


@pytest.fixture
def limits(monkeypatch, tmp_path):
    settings = {"upload_dir": tmp_path, "max_size": 20 * 1024 * 1024}
    original_init = file_upload_module.FileUploadService.__init__

    def init(self, *, upload_dir=None, max_size=None):
        original_init(self, upload_dir=settings["upload_dir"], max_size=settings["max_size"])

    monkeypatch.setattr(file_upload_module.FileUploadService, "__init__", init)
    return settings


@pytest.fixture
def db(monkeypatch, limits):
    """Stub toàn bộ đường chạm DB của router."""
    fake = FakeDb()

    async def project_exists(project_id):
        return fake.project_exists

    async def find_duplicate(project_id, checksum):
        return uuid.UUID(fake.duplicate_of) if fake.duplicate_of else None

    async def create_upload_file(*, project_id, filename, checksum):
        fake.add_file(filename=filename)
        return uuid.UUID(FILE_ID)

    async def delete_upload_file(file_id):
        fake.deleted.append(str(file_id))

    async def fetch_upload_file(file_id):
        from fastapi import HTTPException

        row = fake.rows.get(str(file_id))
        if row is None:
            raise HTTPException(status_code=404, detail={"message": "Không tìm thấy file"})
        return row

    async def load_errors(file_id, limit):
        errors = fake.errors if limit is None else fake.errors[:limit]
        return errors, len(fake.errors)

    monkeypatch.setattr("src.api.files._project_exists", project_exists)
    monkeypatch.setattr("src.api.files._find_duplicate", find_duplicate)
    monkeypatch.setattr("src.api.files._create_upload_file", create_upload_file)
    monkeypatch.setattr("src.api.files._delete_upload_file", delete_upload_file)
    monkeypatch.setattr("src.api.files._fetch_upload_file", fetch_upload_file)
    monkeypatch.setattr("src.api.files._load_errors", load_errors)
    return fake


@pytest.fixture
def queue(monkeypatch, db):
    fake = FakeQueue()
    monkeypatch.setattr("src.api.files.get_queue", lambda name=None: fake)
    return fake


def _csv_bytes(rows):
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


VALID_CSV = _csv_bytes([["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"], ["A1", "2026-01-05", "3", "TX-1"]])


async def _upload(client, **form):
    data = {"template": "sales", "project_id": PROJECT_ID, **form}
    return await client.post(UPLOAD_URL, files={"file": ("sales.csv", VALID_CSV, "text/csv")}, data=data)


# --- POST /files/upload -----------------------------------------------------


@pytest.mark.asyncio
async def test_upload_returns_202_with_file_id_and_job_id(client, queue):
    """file_id có ngay từ 202 vì API tạo `upload_files` trước khi enqueue."""
    response = await _upload(client)

    assert response.status_code == 202
    body = response.json()
    assert body["file_id"] == FILE_ID
    assert body["job_id"] == "job-1"
    assert body["status"] == "pending"
    assert body["filename"] == "sales.csv"
    assert body["template"] == "sales"
    assert body["size_bytes"] == len(VALID_CSV)
    assert len(body["checksum"]) == 64


@pytest.mark.asyncio
async def test_upload_enqueues_job_with_file_id_and_path(client, queue):
    await _upload(client)

    func, kwargs = queue.enqueued[0]
    assert func == "src.jobs.parse_upload.run_parse_upload"
    assert kwargs["file_id"] == FILE_ID
    assert kwargs["project_id"] == PROJECT_ID
    assert kwargs["template"] == "sales"
    assert kwargs["file_path"].endswith(".csv")


@pytest.mark.asyncio
async def test_upload_sanitizes_client_filename_on_disk(client, queue, tmp_path):
    response = await client.post(
        UPLOAD_URL,
        files={"file": ("../../etc/passwd.csv", VALID_CSV, "text/csv")},
        data={"template": "sales", "project_id": PROJECT_ID},
    )

    assert response.status_code == 202
    _, kwargs = queue.enqueued[0]
    stored_name = kwargs["file_path"].rsplit("/", 1)[-1]
    assert "passwd" not in stored_name
    assert (tmp_path / stored_name).exists()


@pytest.mark.asyncio
async def test_duplicate_checksum_returns_409_and_removes_the_file(client, queue, db, tmp_path):
    """Trùng (project_id, checksum) → 409 đồng bộ, không enqueue, không giữ file."""
    db.duplicate_of = "99999999-9999-9999-9999-999999999999"

    response = await _upload(client)

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "DUPLICATE_FILE"
    assert response.json()["detail"]["duplicate_of"] == db.duplicate_of
    assert queue.enqueued == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_upload_rejects_unknown_project(client, queue, db):
    db.project_exists = False

    response = await _upload(client)

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "UNKNOWN_PROJECT"
    assert queue.enqueued == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form", "expected_status", "expected_code"),
    [
        ({"project_id": "khong-phai-uuid"}, 422, "INVALID_PROJECT_ID"),
        ({"template": "khong-ton-tai"}, 422, None),
    ],
    ids=["bad_project_id", "bad_template"],
)
async def test_upload_rejects_bad_form_fields(client, queue, form, expected_status, expected_code):
    response = await _upload(client, **form)

    assert response.status_code == expected_status
    if expected_code:
        assert response.json()["detail"]["error_code"] == expected_code
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_suffix(client, queue):
    response = await client.post(
        UPLOAD_URL,
        files={"file": ("bao-cao.pdf", b"%PDF-1.4", "application/pdf")},
        data={"template": "sales", "project_id": PROJECT_ID},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["error_code"] == "UNSUPPORTED_FORMAT"
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_upload_rejects_oversize_file(client, queue, limits):
    limits["max_size"] = 10
    response = await client.post(
        UPLOAD_URL,
        files={"file": ("sales.csv", b"x" * 5000, "text/csv")},
        data={"template": "sales", "project_id": PROJECT_ID},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["error_code"] == "FILE_TOO_LARGE"
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(client, queue):
    response = await client.post(
        UPLOAD_URL,
        files={"file": ("sales.csv", b"", "text/csv")},
        data={"template": "sales", "project_id": PROJECT_ID},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "EMPTY_FILE"
    assert queue.enqueued == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [{"project_id": PROJECT_ID}, {"template": "sales"}],
    ids=["missing_template", "missing_file_field"],
)
async def test_upload_missing_required_fields_is_422(client, queue, data):
    files = {"file": ("sales.csv", VALID_CSV, "text/csv")} if "template" not in data else None
    response = await client.post(UPLOAD_URL, files=files, data=data)

    assert response.status_code == 422
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_enqueue_failure_removes_file_and_upload_files_row(client, queue, db, tmp_path):
    """Redis chết: gỡ cả file lẫn bản ghi, nếu không sẽ treo pending và khoá checksum."""
    queue.fail = True

    response = await _upload(client)

    assert response.status_code == 503
    assert "error_id" in response.json()["detail"]
    assert list(tmp_path.iterdir()) == []
    assert db.deleted == [FILE_ID]


@pytest.mark.asyncio
async def test_enqueue_failure_does_not_leak_exception_text(client, queue):
    queue.fail = True
    response = await _upload(client)

    assert "redis down" not in response.text


# --- GET /files/{file_id}/status --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("db_status", "expected"),
    [("pending", "pending"), ("processing", "parsing"), ("completed", "done"), ("failed", "failed")],
)
async def test_status_maps_db_vocabulary_to_srs_vocabulary(client, db, db_status, expected):
    """upload_files.status dùng từ vựng của CHECK; API trả từ vựng SRS §5.2."""
    db.add_file(status=db_status)

    response = await client.get(STATUS_URL.format(file_id=FILE_ID))

    assert response.status_code == 200
    assert response.json()["status"] == expected


@pytest.mark.asyncio
async def test_status_reports_counts_from_the_database(client, db):
    db.add_file(status="completed", rows_ok=8, rows_failed=2)

    body = (await client.get(STATUS_URL.format(file_id=FILE_ID))).json()

    assert (body["rows_ok"], body["rows_failed"], body["rows_total"]) == (8, 2, 10)
    assert body["file_id"] == FILE_ID


@pytest.mark.asyncio
async def test_status_surfaces_file_level_error_code(client, db):
    """Lỗi mức file lưu ở upload_errors với column_name NULL; /status kéo lên."""
    db.add_file(status="failed", rows_ok=0, rows_failed=1)
    db.errors = [
        {
            "row_number": 1,
            "column_name": None,
            "error_code": "UNSUPPORTED_ENCODING",
            "message": "File CSV không phải UTF-8",
        }
    ]

    body = (await client.get(STATUS_URL.format(file_id=FILE_ID))).json()

    assert body["status"] == "failed"
    assert body["error_code"] == "UNSUPPORTED_ENCODING"
    assert body["message"] == "File CSV không phải UTF-8"


@pytest.mark.asyncio
async def test_status_of_unknown_file_is_404(client, db):
    response = await client.get(STATUS_URL.format(file_id=FILE_ID))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_status_with_malformed_file_id_is_422(client, db):
    response = await client.get(STATUS_URL.format(file_id="khong-phai-uuid"))
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "INVALID_FILE_ID"


# --- GET /files/{file_id}/errors --------------------------------------------


@pytest.mark.asyncio
async def test_errors_returns_rows_and_total(client, db):
    db.add_file(status="completed", rows_ok=1, rows_failed=1)
    db.errors = [{"row_number": 3, "column_name": "sold_date", "error_code": "INVALID_DATE", "message": "sai ngày"}]

    body = (await client.get(ERRORS_URL.format(file_id=FILE_ID))).json()

    assert body["file_id"] == FILE_ID
    assert body["total"] == 1
    assert body["errors"][0]["row_number"] == 3
    assert body["errors"][0]["column_name"] == "sold_date"


@pytest.mark.asyncio
async def test_errors_of_unknown_file_is_404(client, db):
    assert (await client.get(ERRORS_URL.format(file_id=FILE_ID))).status_code == 404


# --- GET /files/{file_id}/errors.csv ----------------------------------------


@pytest.mark.asyncio
async def test_errors_csv_downloads_all_rows_with_bom(client, db):
    """CSV có BOM: thiếu nó thì Excel trên Windows đọc tiếng Việt ra ký tự lạ."""
    db.add_file(status="completed", rows_ok=0, rows_failed=2, filename="ban-hang.csv")
    db.errors = [
        {"row_number": 3, "column_name": "sold_date", "error_code": "INVALID_DATE", "message": "sai ngày"},
        {"row_number": 4, "column_name": None, "error_code": "DUPLICATE_ROW", "message": "trùng"},
    ]

    response = await client.get(ERRORS_CSV_URL.format(file_id=FILE_ID))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "ban-hang" in response.headers["content-disposition"]

    text = response.text
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert rows[0] == ["row_number", "column_name", "error_code", "message"]
    assert rows[1] == ["3", "sold_date", "INVALID_DATE", "sai ngày"]
    assert rows[2] == ["4", "", "DUPLICATE_ROW", "trùng"]


# --- GET /files -------------------------------------------------------------


def _file_row(**overrides):
    base = dict(
        id=uuid.UUID(FILE_ID),
        filename="sales.csv",
        transport_mode="file_upload",
        status="completed",
        rows_ok=5,
        rows_failed=1,
        uploaded_at=datetime(2026, 1, 5, tzinfo=UTC),
        external_batch_id=None,
        source_system=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _session_returning(rows, monkeypatch):
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_args, **_kwargs):
            return SimpleNamespace(all=lambda: rows)

    monkeypatch.setattr("src.api.files.get_session_factory", lambda: _Session)


@pytest.mark.asyncio
async def test_list_files_returns_history(client, db, monkeypatch):
    """SRS §5.2: lịch sử upload, mới nhất trước."""
    _session_returning([_file_row()], monkeypatch)

    body = (await client.get(LIST_URL)).json()

    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["items"][0]["file_id"] == FILE_ID
    assert body["items"][0]["status"] == "done"
    assert body["items"][0]["rows_ok"] == 5


# --- Phase 5.5 P0: transport_mode (F-1) --------------------------------------


@pytest.mark.asyncio
async def test_list_files_exposes_transport_mode_for_a_real_upload(client, db, monkeypatch):
    _session_returning([_file_row(transport_mode="file_upload", filename="sales.csv")], monkeypatch)

    body = (await client.get(LIST_URL)).json()

    assert body["items"][0]["transport_mode"] == "file_upload"
    assert body["items"][0]["filename"] == "sales.csv"


@pytest.mark.asyncio
async def test_list_files_exposes_transport_mode_for_a_crm_sync_batch_with_null_filename(client, db, monkeypatch):
    """F-1: lô CRM có filename=NULL — client KHÔNG được suy loại lô từ đó nữa."""
    _session_returning(
        [
            _file_row(
                filename=None,
                transport_mode="api_push",
                external_batch_id="batch-001",
                source_system="mini_crm",
            )
        ],
        monkeypatch,
    )

    body = (await client.get(LIST_URL)).json()

    assert body["items"][0]["filename"] is None
    assert body["items"][0]["transport_mode"] == "api_push"
    assert body["items"][0]["external_batch_id"] == "batch-001"
    assert body["items"][0]["source_system"] == "mini_crm"


@pytest.mark.asyncio
async def test_list_files_maps_an_unexpected_transport_value_to_unknown(client, db, monkeypatch):
    _session_returning([_file_row(transport_mode="something-else")], monkeypatch)

    body = (await client.get(LIST_URL)).json()

    assert body["items"][0]["transport_mode"] == "unknown"


@pytest.mark.asyncio
async def test_list_files_rejects_an_invalid_transport_mode_filter(client, db):
    response = await client.get(f"{LIST_URL}?transport_mode=carrier-pigeon")

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "INVALID_TRANSPORT_MODE"


@pytest.mark.asyncio
async def test_list_files_filters_by_transport_mode(client, db, monkeypatch):
    captured = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, query, *_a, **_kw):
            captured["sql"] = str(query)
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr("src.api.files.get_session_factory", lambda: _Session)

    response = await client.get(f"{LIST_URL}?transport_mode=api_push")

    assert response.status_code == 200
    assert "transport_mode" in captured["sql"]


# --- Auth/RBAC (đóng lỗ hổng MVP1: router này từng hoàn toàn không có xác thực) --


class _ScopeControl:
    """Điều khiển kết quả `resolve_scope_project_ids` không cần DB thật —
    file test này chủ trương stub, xem docstring đầu file."""

    def __init__(self):
        self.value: str | list = "ALL"


@pytest.fixture
def scope_control(monkeypatch):
    control = _ScopeControl()

    async def fake_resolve(_session, _principal):
        return control.value

    monkeypatch.setattr("src.api.files.resolve_scope_project_ids", fake_resolve)
    return control


def _client_as(token: str | None = None) -> AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


async def _try_upload(token: str | None, **form):
    data = {"template": "sales", "project_id": PROJECT_ID, **form}
    async with _client_as(token) as anon_or_role:
        return await anon_or_role.post(UPLOAD_URL, files={"file": ("sales.csv", VALID_CSV, "text/csv")}, data=data)


@pytest.mark.asyncio
async def test_anonymous_upload_is_401(queue):
    response = await _try_upload(None)
    assert response.status_code == 401
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_viewer_role_cannot_upload(queue):
    """Viewer không đủ quyền GHI — chỉ operator/admin mới nạp được file."""
    response = await _try_upload(DASHBOARD_VIEWER_TOKEN)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_operator_role_can_upload(queue):
    response = await _try_upload(DASHBOARD_OPERATOR_TOKEN)
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_admin_role_can_upload(queue):
    response = await _try_upload(DASHBOARD_ADMIN_TOKEN)
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_upload_rejects_operator_outside_project_scope(queue, scope_control):
    scope_control.value = [uuid.UUID("99999999-9999-9999-9999-999999999999")]
    response = await _try_upload(DASHBOARD_OPERATOR_TOKEN)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_upload_allows_operator_with_project_in_scope(queue, scope_control):
    scope_control.value = [uuid.UUID(PROJECT_ID)]
    response = await _try_upload(DASHBOARD_OPERATOR_TOKEN)
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_anonymous_status_is_401(db):
    async with _client_as() as anon:
        response = await anon.get(STATUS_URL.format(file_id=FILE_ID))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_rejects_viewer_outside_project_scope(db, scope_control):
    """File thuộc PROJECT_ID; token viewer có phạm vi KHÁC → 403, không lộ trạng thái."""
    db.add_file(status="completed", project_id=PROJECT_ID)
    scope_control.value = [uuid.UUID("99999999-9999-9999-9999-999999999999")]

    async with _client_as(DASHBOARD_VIEWER_TOKEN) as viewer:
        response = await viewer.get(STATUS_URL.format(file_id=FILE_ID))

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_status_allows_viewer_with_project_in_scope(db, scope_control):
    db.add_file(status="completed", project_id=PROJECT_ID)
    scope_control.value = [uuid.UUID(PROJECT_ID)]

    async with _client_as(DASHBOARD_VIEWER_TOKEN) as viewer:
        response = await viewer.get(STATUS_URL.format(file_id=FILE_ID))

    assert response.status_code == 200
    assert response.json()["status"] == "done"


@pytest.mark.asyncio
async def test_anonymous_errors_is_401(db):
    async with _client_as() as anon:
        response = await anon.get(ERRORS_URL.format(file_id=FILE_ID))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_anonymous_errors_csv_is_401(db):
    async with _client_as() as anon:
        response = await anon.get(ERRORS_CSV_URL.format(file_id=FILE_ID))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_viewer_cannot_download_errors_csv(db):
    """CSV export cần pipeline_operator trở lên — cao hơn `/errors` (JSON)."""
    db.add_file(status="completed", project_id=PROJECT_ID)
    async with _client_as(DASHBOARD_VIEWER_TOKEN) as viewer:
        response = await viewer.get(ERRORS_CSV_URL.format(file_id=FILE_ID))
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"


@pytest.mark.asyncio
async def test_operator_can_download_errors_csv(db):
    db.add_file(status="completed", project_id=PROJECT_ID, filename="ban-hang.csv")
    async with _client_as(DASHBOARD_OPERATOR_TOKEN) as operator:
        response = await operator.get(ERRORS_CSV_URL.format(file_id=FILE_ID))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_errors_csv_rejects_operator_outside_project_scope(db, scope_control):
    db.add_file(status="completed", project_id=PROJECT_ID, filename="ban-hang.csv")
    scope_control.value = [uuid.UUID("99999999-9999-9999-9999-999999999999")]

    async with _client_as(DASHBOARD_OPERATOR_TOKEN) as operator:
        response = await operator.get(ERRORS_CSV_URL.format(file_id=FILE_ID))

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_anonymous_list_files_is_401():
    async with _client_as() as anon:
        response = await anon.get(LIST_URL)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_viewer_with_no_scope_narrows_the_list_query(monkeypatch):
    """Viewer chưa được cấp phạm vi nào (rỗng, không phải ALL) → câu truy vấn
    phải tự thu hẹp về "không dự án nào" (`sa.false()`), không trả về mọi dự án
    rồi trông cậy client tự lọc — cùng nguyên tắc `list_sync_runs`. Stub session
    ở đây không diễn giải `WHERE` thật, nên kiểm tra qua hình dạng câu SQL, giống
    `test_list_files_filters_by_transport_mode`."""
    captured = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, query, *_a, **_kw):
            captured["sql"] = str(query)
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr("src.api.files.get_session_factory", lambda: _Session)

    async with _client_as(DASHBOARD_VIEWER_TOKEN) as viewer:
        response = await viewer.get(LIST_URL)

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert "false" in captured["sql"].lower()


@pytest.mark.asyncio
async def test_viewer_with_scope_narrows_the_list_query_to_its_projects(monkeypatch, scope_control):
    scope_control.value = [uuid.UUID(PROJECT_ID)]
    captured = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, query, *_a, **_kw):
            captured["sql"] = str(query)
            return SimpleNamespace(all=lambda: [_file_row()])

    monkeypatch.setattr("src.api.files.get_session_factory", lambda: _Session)

    async with _client_as(DASHBOARD_VIEWER_TOKEN) as viewer:
        response = await viewer.get(LIST_URL)

    assert response.status_code == 200
    assert response.json()["items"][0]["file_id"] == FILE_ID
    assert "project_id in" in captured["sql"].lower()


@pytest.mark.asyncio
async def test_list_files_project_filter_rejects_out_of_scope_project(scope_control):
    scope_control.value = [uuid.UUID("99999999-9999-9999-9999-999999999999")]

    async with _client_as(DASHBOARD_VIEWER_TOKEN) as viewer:
        response = await viewer.get(f"{LIST_URL}?project_id={PROJECT_ID}")

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"
