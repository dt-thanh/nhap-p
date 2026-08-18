"""Tiện ích dùng chung cho các test chạy trên CONTAINER THẬT.

Không phải file test. Nó gom lại đúng những thứ mà một test đầu-cuối cần và không
nên tự bịa: địa chỉ hai service, chuỗi kết nối hai database, và vài phép chờ.

**Vì sao đọc `.env` của repo thay vì nhận tham số.** Compose lấy credential từ
đúng file đó. Test đọc cùng một chỗ thì không có tham số nào phải nhớ khi chạy,
không có mật khẩu nào nằm trong mã nguồn, và không có cửa sổ nào để hai bên lệch
nhau. Đọc bằng cách TÁCH `KEY=VALUE`, không `source`: `source .env` sẽ thực thi
nội dung file — một dòng `FORECAST_CRON=0 2 * * *` biến thành lệnh, và mọi `$(...)`
bị chạy thật. Cùng cách mà `scripts/test_db.sh` làm, vì cùng lý do.

**Module này KHÔNG import gì từ `app/` hay `src/`.** Nó nói chuyện với cả hai hệ
thống qua HTTP và SQL, đúng như một người vận hành sẽ làm. Import model của Mini
CRM vào đây sẽ khiến test khẳng định về mã nguồn đang chạy trong tiến trình pytest
thay vì về mã nguồn đang chạy trong container — hai thứ có thể khác nhau, và phát
hiện đúng lúc chúng khác nhau là một phần việc của bộ test này.
"""

from __future__ import annotations

import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]

MINICRM_URL = "http://localhost:8100"
BACKEND_URL = "http://localhost:8000"

# Phân khu TỔNG HỢP có thật trong dự án mà `.env` trỏ tới. Backend sở hữu dự án và
# phân khu; Mini CRM chỉ tham chiếu, không tạo được (mục 2 của hợp đồng).
AREA_NAME = "DEMO Toà B1"
UNIT_TYPE = "Căn hộ"


def _env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    path = REPO_ROOT / ".env"
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


ENV = _env_file()


def _dsn(user: str, password: str, database: str, port: int) -> str:
    """Mật khẩu phải được URL-encode, nếu không một ký tự `@` sẽ phá vỡ DSN."""
    return (
        f"postgresql+psycopg2://{urllib.parse.quote(user, safe='')}:"
        f"{urllib.parse.quote(password, safe='')}@localhost:{port}/{database}"
    )


BACKEND_DSN = _dsn(
    ENV.get("POSTGRES_USER", "app"),
    ENV.get("POSTGRES_PASSWORD", "app"),
    ENV.get("POSTGRES_DB", "absorption"),
    5432,
)
CRM_DSN = _dsn(
    ENV.get("MINICRM_POSTGRES_USER", "minicrm"),
    ENV.get("MINICRM_POSTGRES_PASSWORD", "minicrm"),
    ENV.get("MINICRM_POSTGRES_DB", "minicrm"),
    5433,
)
SOURCE_INSTANCE = ENV.get("MINICRM_SOURCE_INSTANCE_ID", "mini-crm-dev")
PROJECT_ID = ENV.get("MINICRM_PROJECT_ID", "")

# Xác thực GHI Mini CRM (D-14). `crm()` gắn header admin theo MẶC ĐỊNH — hầu hết
# test THẬT ở đây kiểm hành vi nghiệp vụ/đồng bộ, không kiểm chính cơ chế xác
# thực (đó là việc của `test_real_auth.py`), nên admin (phạm vi ALL) là lựa chọn
# đơn giản nhất để không phải sửa từng lời gọi. Truyền `headers=` riêng vào một
# lời gọi cụ thể sẽ GHI ĐÈ, không cộng dồn.
ADMIN_TOKEN = ENV.get("MINICRM_AUTH_ADMIN_TOKEN", "")
OPERATOR_TOKEN = ENV.get("MINICRM_AUTH_PIPELINE_OPERATOR_TOKEN", "")
VIEWER_TOKEN = ENV.get("MINICRM_AUTH_BUSINESS_VIEWER_TOKEN", "")
ADMIN_AUTH_HEADER = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
OPERATOR_AUTH_HEADER = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
VIEWER_AUTH_HEADER = {"Authorization": f"Bearer {VIEWER_TOKEN}"}

# Xác thực mặt đọc Backend (Phase E). Dùng khi test gọi thẳng
# `GET {BACKEND_URL}/api/v1/...` mà không đi qua relay/Mini CRM — kể từ khi
# Phase E bật xác thực thật trong `.env`, các lời gọi trực tiếp này cũng cần
# token, admin (phạm vi ALL) để không phải chọn dự án cụ thể.
BACKEND_ADMIN_TOKEN = ENV.get("DASHBOARD_ADMIN_TOKEN", "")
BACKEND_AUTH_HEADER = {"Authorization": f"Bearer {BACKEND_ADMIN_TOKEN}"}


def reachable(url: str, timeout: float = 5.0) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def skip_reason() -> str:
    """Lý do bỏ qua, hoặc chuỗi rỗng nếu môi trường đã sẵn sàng."""
    if not reachable(MINICRM_URL):
        return f"Container Mini CRM không phản hồi ở {MINICRM_URL} — chạy: docker compose up -d --build minicrm_db minicrm"
    if not reachable(BACKEND_URL):
        return f"Backend không phản hồi ở {BACKEND_URL} — chạy: docker compose up -d api"
    if not PROJECT_ID:
        return "`.env` thiếu MINICRM_PROJECT_ID — Mini CRM không biết đẩy vào dự án nào"
    return ""


def crm(method: str, path: str, **kwargs) -> httpx.Response:
    headers = {**ADMIN_AUTH_HEADER, **kwargs.pop("headers", {})}
    with httpx.Client(base_url=MINICRM_URL, timeout=30.0) as client:
        return client.request(method, path, headers=headers, **kwargs)


def _query(dsn: str, sql: str, **params) -> list[dict[str, Any]]:
    engine = sa.create_engine(dsn)
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(sa.text(sql), params).mappings()]
    finally:
        engine.dispose()


def backend_rows(sql: str, **params) -> list[dict[str, Any]]:
    return _query(BACKEND_DSN, sql, **params)


def crm_rows(sql: str, **params) -> list[dict[str, Any]]:
    return _query(CRM_DSN, sql, **params)


def nonzero(counters: dict | None) -> dict:
    """Bỏ những khoá bằng 0 khỏi `decisions`/`projections`.

    Backend trả về TOÀN BỘ từ vựng quyết định, phần lớn bằng 0 — hình dạng ổn định
    cho người đọc bảng điều khiển. Khẳng định trên dict đầy đủ sẽ khiến test hỏng
    mỗi lần backend thêm một loại quyết định mới, việc chẳng nói gì về tính tương
    thích. Lọc số 0 rồi so trên phần CÓ THẬT.
    """
    return {key: value for key, value in (counters or {}).items() if value}


def compose(*args: str, timeout: float = 240.0) -> None:
    result = subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
    )
    assert result.returncode == 0, f"docker compose {' '.join(args)} thất bại:\n{result.stderr}"


def wait_until_up(url: str, timeout: float = 120.0) -> None:
    """Chờ một service sống lại sau khi tắt/khởi động lại.

    Poll `/health` chứ không `sleep` một khoảng cố định: một khoảng cố định hoặc
    quá ngắn (test chớp tắt) hoặc quá dài (mỗi lần chạy tốn thêm vài chục giây),
    và không có giá trị nào đúng cho cả hai máy.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if reachable(url, timeout=3.0):
            return
        time.sleep(1.5)
    raise AssertionError(f"{url} không sống lại sau {timeout}s")


def wait_for_postgres(dsn: str, timeout: float = 120.0) -> None:
    """Chờ một database nhận kết nối lại. Container khoẻ trước khi Postgres sẵn sàng."""
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            _query(dsn, "SELECT 1 AS ok")
            return
        except Exception as exc:  # noqa: BLE001 - mọi lỗi kết nối đều là "chưa sẵn sàng"
            last = exc
            time.sleep(1.5)
    raise AssertionError(f"database không nhận kết nối sau {timeout}s: {last}")
