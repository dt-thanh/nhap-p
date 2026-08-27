"""Phase D — E2E THẬT: Mini CRM ghi cục bộ (v2, đã LƯU từ Phase C) → RELAY nguyên
văn phong bì đã bắt được sang `POST /sync/{entity}` (v2, MỚI bật ở Phase D) →
backend chiếu vào bản sao.

**Vì sao có bước "RELAY" và nó có phải "giả" không.** Phase C cố ý CHỈ LƯU phong
bì v2 (`minicrm/app/crud.py::_capture_v2`) — không có đường HTTP nào tự động gửi
nó, vì backend lúc đó CHƯA chấp nhận v2. Nối đường TỰ ĐỘNG (Mini CRM tự gửi ý định
đã lưu) là việc của Mini CRM runtime, và Phase D bị cấm đụng runtime đó ("Do NOT
modify Mini CRM runtime"). Bước RELAY ở đây đứng vào đúng chỗ một hệ nguồn thật sự
sẽ đứng: đọc lại phong bì NGUYÊN VĂN Mini CRM đã ký (`GET /outbox/{batch_id}`,
route CÓ SẴN, không sửa gì) rồi POST y hệt sang backend — HAI ĐẦU đều là container
THẬT, HTTP THẬT, ghi DB THẬT. Không có phản hồi nào bị giả lập; đây là bằng chứng
chiếu THẬT, không phải một `FakeBackend`.

Yêu cầu: `docker compose up -d` (minicrm, minicrm_db, api, db) đã chạy, và có
một sync API key hợp lệ cho `MINICRM_SOURCE_INSTANCE_ID` — ưu tiên đọc từ
`.dev-secrets/minicrm_sync_api_key` (Compose secrets, do `scripts/dev-reset.sh`/
`scripts/bootstrap_dev.py --credential-output-file` cấp; xem
`minicrm/app/config.py::sync_api_key_value` cho cùng thứ tự ưu tiên), rơi về
`.env`'s `MINICRM_SYNC_API_KEY` chỉ khi file đó không có — cùng khoá mà
`minicrm/tests/test_real_backend_sync.py` dùng cho v1.

Chạy: pytest tests/test_services/test_real_hierarchy_e2e.py -q
(KHÔNG qua scripts/test_db.sh — bộ này nói chuyện với container THẬT, không phải
database test cục bộ).
"""

from __future__ import annotations

import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
MINICRM_URL = "http://localhost:8100"
BACKEND_URL = "http://localhost:8000/api/v1"

# Hậu tố Mini CRM gắn cho outbox v2 của Unit/Deal (`sync_client.V2_CAPTURE_ENTITIES`)
# — KHÔNG có trên đường dẫn của backend (`/sync/units`, không phải `/sync/units_v2`).
_ROUTE_ENTITY = {"projects": "projects", "areas": "areas", "units_v2": "units", "deals_v2": "deals"}


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


def _sync_api_key(env: dict[str, str]) -> str:
    """Cùng thứ tự ưu tiên với `minicrm/app/config.py::sync_api_key_value`:
    file bí mật Compose trước (luôn khớp credential ACTIVE hiện tại, kể cả sau
    `dev-reset.sh`/`bootstrap_dev.py --credential-output-file`), `.env` chỉ là
    lối tương thích ngược — và không còn được ghi tự động kể từ khi có luồng
    cấp credential tự động cho dev cục bộ."""
    secret_file = REPO_ROOT / ".dev-secrets" / "minicrm_sync_api_key"
    try:
        from_file = secret_file.read_text().rstrip("\n")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        from_file = ""
    if from_file:
        return from_file
    return env.get("MINICRM_SYNC_API_KEY", "")


ENV = _env_file()
SYNC_API_KEY = _sync_api_key(ENV)
SOURCE_INSTANCE = ENV.get("MINICRM_SOURCE_INSTANCE_ID", "mini-crm-dev")
# D-14: Mini CRM giờ đòi xác thực GHI. `admin` (phạm vi ALL, `.env`) — fixture
# này dựng hệ thống phân cấp qua nhiều dự án khác nhau (kể cả một dự án NGOÀI
# phạm vi có chủ đích, xem `test_area_cannot_be_moved_to_a_different_project`),
# nên cần phạm vi rộng nhất thay vì token operator hẹp.
MINICRM_ADMIN_TOKEN = ENV.get("MINICRM_AUTH_ADMIN_TOKEN", "")
MINICRM_AUTH_HEADER = {"Authorization": f"Bearer {MINICRM_ADMIN_TOKEN}"}


def _dsn(user: str, password: str, database: str, port: int) -> str:
    return (
        f"postgresql+psycopg2://{urllib.parse.quote(user, safe='')}:"
        f"{urllib.parse.quote(password, safe='')}@localhost:{port}/{database}"
    )


BACKEND_DSN = _dsn(
    ENV.get("POSTGRES_USER", "app"), ENV.get("POSTGRES_PASSWORD", "app"), ENV.get("POSTGRES_DB", "absorption"), 5432
)


def _reachable(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def _skip_reason() -> str:
    if not _reachable(MINICRM_URL):
        return f"Container Mini CRM không phản hồi ở {MINICRM_URL} — chạy: docker compose up -d minicrm"
    if not _reachable(BACKEND_URL.removesuffix('/api/v1')):
        return "Backend không phản hồi — chạy: docker compose up -d api"
    if not SYNC_API_KEY:
        return "`.env` thiếu MINICRM_SYNC_API_KEY"
    if not MINICRM_ADMIN_TOKEN:
        return "`.env` thiếu MINICRM_AUTH_ADMIN_TOKEN (D-14)"
    return ""


pytestmark = pytest.mark.skipif(bool(_skip_reason()), reason=_skip_reason())


def _minicrm(method: str, path: str, **kwargs) -> httpx.Response:
    headers = {**kwargs.pop("headers", {}), **MINICRM_AUTH_HEADER}
    with httpx.Client(base_url=MINICRM_URL, timeout=30.0) as client:
        return client.request(method, path, headers=headers, **kwargs)


def _backend(method: str, path: str, **kwargs) -> httpx.Response:
    headers = {**kwargs.pop("headers", {}), "X-API-Key": SYNC_API_KEY}
    with httpx.Client(base_url=BACKEND_URL, timeout=30.0) as client:
        return client.request(method, path, headers=headers, **kwargs)


def _backend_rows(sql: str, **params) -> list[dict[str, Any]]:
    engine = sa.create_engine(BACKEND_DSN)
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(sa.text(sql), params).mappings()]
    finally:
        engine.dispose()


def _latest_v2_envelope(outbox_entity: str) -> tuple[str, dict[str, Any]]:
    """Phong bì v2 MỚI NHẤT mà Mini CRM đã LƯU cho entity này. `(batch_id, envelope)`."""
    listing = _minicrm("GET", "/outbox", params={"entity": outbox_entity, "limit": 1}).json()
    assert listing["items"], f"Mini CRM chưa có dòng outbox nào cho entity='{outbox_entity}'"
    batch_id = listing["items"][0]["external_batch_id"]
    detail = _minicrm("GET", f"/outbox/{batch_id}")
    assert detail.status_code == 200
    return batch_id, detail.json()["payload"]


def _direct_relay(outbox_entity: str, envelope: dict[str, Any]) -> httpx.Response:
    """POST một phong bì trực tiếp cho các probe cố ý bypassing RelayLoop."""
    route = _ROUTE_ENTITY[outbox_entity]
    return _backend("POST", f"/sync/{route}", json=envelope)


def _relay(outbox_entity: str, envelope: dict[str, Any]) -> httpx.Response:
    """Chờ RelayLoop thật xử lý batch đã được Mini CRM commit.

    Nếu envelope không còn là một dòng outbox (ví dụ orphan/cross-parent probe),
    gửi trực tiếp để kiểm tra lỗi contract ở Backend. Các batch CRUD thật phải đi
    qua RelayLoop, vì chỉ đường đó đóng dấu `mirrored_revision` ở Mini CRM.
    """
    batch_id = envelope["external_batch_id"]
    detail = _minicrm("GET", f"/outbox/{batch_id}")
    if detail.status_code != 200:
        return _direct_relay(outbox_entity, envelope)

    deadline = time.time() + 30
    while time.time() < deadline:
        detail = _minicrm("GET", f"/outbox/{batch_id}")
        if detail.status_code == 200:
            state = detail.json()
            if state["http_status"] is not None:
                return httpx.Response(
                    status_code=state["http_status"],
                    request=httpx.Request("POST", f"{BACKEND_URL}/sync/{_ROUTE_ENTITY[outbox_entity]}"),
                )
        time.sleep(0.25)
    return httpx.Response(
        status_code=504,
        request=httpx.Request("POST", f"{BACKEND_URL}/sync/{_ROUTE_ENTITY[outbox_entity]}"),
    )


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --- Toàn bộ phân cấp: Project → Area → Unit → Deal, tạo qua Mini CRM, chiếu ---
# --- sang backend qua relay. Một fixture cho cả module để tiết kiệm request. ---


@pytest.fixture(scope="module")
def hierarchy():
    """Tạo MỘT phân cấp đầy đủ qua Mini CRM thật, relay từng tầng sang backend
    thật, đúng thứ tự project → area → unit → deal (§A5.2)."""
    project_name = _unique("E2E Du an")
    project = _minicrm("POST", "/projects", json={"name": project_name, "launch_date": "2026-06-01"}).json()["record"]
    project_batch, project_envelope = _latest_v2_envelope("projects")
    project_relay = _relay("projects", project_envelope)
    assert project_relay.status_code in (200, 202), project_relay.text

    area_name = _unique("E2E Toa")
    area = _minicrm(
        "POST",
        "/areas",
        json={
            "external_project_id": project["external_id"],
            "area_name": area_name,
            "unit_type": "2PN",
            "bedrooms": 2,
            "area_sqm": 68.5,
            "total_units": 120,
        },
    ).json()["record"]
    area_batch, area_envelope = _latest_v2_envelope("areas")
    area_relay = _relay("areas", area_envelope)
    assert area_relay.status_code in (200, 202), area_relay.text

    unit = _minicrm(
        "POST",
        "/units",
        json={"external_area_id": area["external_id"], "unit_code": _unique("U"), "unit_status": "available"},
    ).json()["record"]
    unit_batch, unit_envelope = _latest_v2_envelope("units_v2")
    unit_relay = _relay("units_v2", unit_envelope)
    assert unit_relay.status_code in (200, 202), unit_relay.text

    deal = _minicrm(
        "POST", "/deals", json={"external_unit_id": unit["external_id"], "deal_status": "lead"}
    ).json()["record"]
    deal_batch, deal_envelope = _latest_v2_envelope("deals_v2")
    deal_relay = _relay("deals_v2", deal_envelope)
    assert deal_relay.status_code in (200, 202), deal_relay.text

    return {
        "project": project,
        "project_batch": project_batch,
        "area": area,
        "area_batch": area_batch,
        "unit": unit,
        "unit_batch": unit_batch,
        "deal": deal,
        "deal_batch": deal_batch,
    }


def test_project_create_reaches_the_backend_mirror(hierarchy):
    rows = _backend_rows("SELECT * FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"])
    assert len(rows) == 1
    assert rows[0]["name"] == hierarchy["project"]["name"]
    assert rows[0]["source_instance_id"] == SOURCE_INSTANCE
    assert rows[0]["status"] == "active"


def test_area_create_under_project_reaches_the_backend_mirror(hierarchy):
    rows = _backend_rows("SELECT * FROM areas WHERE external_id = :ext", ext=hierarchy["area"]["external_id"])
    assert len(rows) == 1
    project_rows = _backend_rows(
        "SELECT id FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"]
    )
    assert rows[0]["project_id"] == project_rows[0]["id"]


def test_unit_create_under_area_reaches_the_backend_mirror(hierarchy):
    rows = _backend_rows("SELECT * FROM units WHERE external_unit_id = :ext", ext=hierarchy["unit"]["external_id"])
    assert len(rows) == 1
    area_rows = _backend_rows("SELECT id FROM areas WHERE external_id = :ext", ext=hierarchy["area"]["external_id"])
    assert rows[0]["area_id"] == area_rows[0]["id"]


def test_deal_create_under_unit_reaches_the_backend_mirror(hierarchy):
    rows = _backend_rows("SELECT * FROM deals WHERE external_deal_id = :ext", ext=hierarchy["deal"]["external_id"])
    assert len(rows) == 1
    unit_rows = _backend_rows(
        "SELECT id FROM units WHERE external_unit_id = :ext", ext=hierarchy["unit"]["external_id"]
    )
    assert rows[0]["unit_id"] == unit_rows[0]["id"]


def test_project_update_reaches_the_backend_mirror(hierarchy):
    new_name = _unique("Ten moi")
    _minicrm("PATCH", f"/projects/{hierarchy['project']['external_id']}", json={"name": new_name})
    _, envelope = _latest_v2_envelope("projects")
    response = _relay("projects", envelope)
    assert response.status_code in (200, 202), response.text

    rows = _backend_rows("SELECT name, source_revision FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"])
    assert rows[0]["name"] == new_name
    assert rows[0]["source_revision"] == 2


def test_area_update_reaches_the_backend_mirror(hierarchy):
    _minicrm("PATCH", f"/areas/{hierarchy['area']['external_id']}", json={"total_units": 130})
    _, envelope = _latest_v2_envelope("areas")
    response = _relay("areas", envelope)
    assert response.status_code in (200, 202), response.text

    rows = _backend_rows("SELECT total_units FROM areas WHERE external_id = :ext", ext=hierarchy["area"]["external_id"])
    assert rows[0]["total_units"] == 130


def test_unit_update_reaches_the_backend_mirror(hierarchy):
    _minicrm("PATCH", f"/units/{hierarchy['unit']['external_id']}", json={"unit_status": "reserved"})
    _, envelope = _latest_v2_envelope("units_v2")
    response = _relay("units_v2", envelope)
    assert response.status_code in (200, 202), response.text

    rows = _backend_rows("SELECT status FROM units WHERE external_unit_id = :ext", ext=hierarchy["unit"]["external_id"])
    assert rows[0]["status"] == "reserved"


def test_deal_update_reaches_the_backend_mirror(hierarchy):
    _minicrm(
        "PATCH",
        f"/deals/{hierarchy['deal']['external_id']}",
        json={"deal_status": "reserved", "reserved_at": "2026-08-10T09:00:00+07:00"},
    )
    _, envelope = _latest_v2_envelope("deals_v2")
    response = _relay("deals_v2", envelope)
    assert response.status_code in (200, 202), response.text

    rows = _backend_rows("SELECT status FROM deals WHERE external_deal_id = :ext", ext=hierarchy["deal"]["external_id"])
    assert rows[0]["status"] == "reserved"


def test_duplicate_replay_of_the_same_batch_id_is_idempotent(hierarchy):
    """Gửi lại ĐÚNG batch id cũ → `replayed=true`, không sinh dòng thứ hai."""
    _, envelope = _latest_v2_envelope("projects")
    first = _direct_relay("projects", envelope)
    second = _direct_relay("projects", envelope)

    assert first.status_code in (200, 202)
    assert second.status_code == 200
    assert second.json()["replayed"] is True

    rows = _backend_rows(
        "SELECT count(*) AS n FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"]
    )
    assert rows[0]["n"] == 1


def test_stale_replay_under_a_new_batch_id_is_skipped(hierarchy):
    """Phát lại envelope CŨ (revision 1 — chính lô TẠO dự án, chắc chắn cũ hơn
    trạng thái hiện tại vì `test_project_update_reaches_the_backend_mirror` đã
    đẩy lên revision 2) dưới batch id MỚI → `skip_stale`, không ghi đè."""
    stale_batch_id = hierarchy["project_batch"]  # lô TẠO — luôn ở revision 1
    stale_envelope = _minicrm("GET", f"/outbox/{stale_batch_id}").json()["payload"]
    assert stale_envelope["records"][0]["source_revision"] == 1

    current = _backend_rows(
        "SELECT source_revision, name FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"]
    )[0]
    assert current["source_revision"] > 1, "test_project_update_reaches_the_backend_mirror phải chạy trước để có revision > 1"

    replayed_envelope = {**stale_envelope, "external_batch_id": f"{stale_batch_id}-replay-{uuid.uuid4().hex[:6]}"}
    response = _relay("projects", replayed_envelope)

    assert response.status_code in (200, 202)
    assert response.json()["decisions"].get("skip_stale", 0) == 1

    after = _backend_rows(
        "SELECT name FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"]
    )[0]
    assert after["name"] == current["name"], "skip_stale không được ghi đè trạng thái mới hơn"


def test_area_under_an_unknown_project_is_rejected(hierarchy):
    """Tham chiếu tới một dự án CHƯA từng đồng bộ ở backend → TỪ CHỐI CẢ PHONG
    BÌ (§A5.3), không tạo phân khu mồ côi."""
    _, area_envelope = _latest_v2_envelope("areas")
    orphan = {
        **area_envelope,
        "external_batch_id": _unique("orphan-batch"),
        "project_ref": {"external_project_id": _unique("P-KHONG-TON-TAI")},
        "records": [{**area_envelope["records"][0], "external_id": _unique("A-orphan")}],
    }
    response = _relay("areas", orphan)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"


def test_area_cannot_be_moved_to_a_different_project(hierarchy):
    """Upsert lại CHÍNH phân khu đã có, nhưng dưới `project_ref` của một dự án
    KHÁC — bị từ chối, không được chuyển dự án âm thầm."""
    other_name = _unique("E2E Du an khac")
    other_project = _minicrm(
        "POST", "/projects", json={"name": other_name, "launch_date": "2026-06-01"}
    ).json()["record"]
    _, other_project_envelope = _latest_v2_envelope("projects")
    assert _relay("projects", other_project_envelope).status_code in (200, 202)

    _, area_envelope = _latest_v2_envelope("areas")
    moved = {
        **area_envelope,
        "external_batch_id": _unique("move-batch"),
        "project_ref": {"external_project_id": other_project["external_id"]},
        "records": [
            {
                **area_envelope["records"][0],
                "external_id": hierarchy["area"]["external_id"],
                "source_revision": area_envelope["records"][0]["source_revision"] + 50,
            }
        ],
    }
    response = _relay("areas", moved)

    assert response.status_code in (200, 202)  # lô được NHẬN, bản ghi bị TỪ CHỐI
    assert response.json()["rows_failed"] == 1

    rows = _backend_rows("SELECT project_id FROM areas WHERE external_id = :ext", ext=hierarchy["area"]["external_id"])
    original_project = _backend_rows(
        "SELECT id FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"]
    )
    assert rows[0]["project_id"] == original_project[0]["id"], "phân khu KHÔNG được chuyển dự án"


def test_mirrored_data_survives_a_backend_restart(hierarchy):
    """Khởi động lại container `api` — dữ liệu đã chiếu phải còn nguyên, không
    cần đồng bộ lại. Đây là bằng chứng bản sao BỀN, không phải cache tạm."""
    import subprocess

    before = _backend_rows(
        "SELECT name, source_revision FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"]
    )[0]

    result = subprocess.run(
        ["docker", "compose", "restart", "api"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr

    deadline = time.time() + 90
    while time.time() < deadline and not _reachable(BACKEND_URL.removesuffix("/api/v1")):
        time.sleep(1.5)
    assert _reachable(BACKEND_URL.removesuffix("/api/v1")), "backend không sống lại sau restart"

    after = _backend_rows(
        "SELECT name, source_revision FROM projects WHERE external_id = :ext", ext=hierarchy["project"]["external_id"]
    )[0]
    assert after == before


def test_a_pending_captured_batch_can_still_be_relayed_after_restart(hierarchy):
    """Sau khi backend khởi động lại, một phong bì Mini CRM đã LƯU TỪ TRƯỚC vẫn
    relay được bình thường — không trạng thái nào bị kẹt vì lần khởi động lại."""
    _minicrm("PATCH", f"/areas/{hierarchy['area']['external_id']}", json={"bedrooms": 3})
    _, envelope = _latest_v2_envelope("areas")
    response = _relay("areas", envelope)

    assert response.status_code in (200, 202), response.text
    rows = _backend_rows("SELECT bedrooms FROM areas WHERE external_id = :ext", ext=hierarchy["area"]["external_id"])
    assert rows[0]["bedrooms"] == 3
