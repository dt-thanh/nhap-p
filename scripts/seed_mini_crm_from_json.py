"""SIMULATOR CRUD đầu-cuối cho Mini CRM — thay thế seed tĩnh cũ.

    python -m scripts.seed_mini_crm_from_json

Ghi DUY NHẤT qua HTTP API thật của Mini CRM (`httpx`) — KHÔNG import
`minicrm/app/**`, KHÔNG chạm `crm_outbox`, KHÔNG ghi thẳng bảng Backend. Đường
đi bắt buộc, và script này không có đường tắt nào khác:

    HTTP → route Mini CRM (auth D-14) → crud.* → transaction
      → _capture_v2() → crm_outbox (cùng transaction)
    COMMIT
      → RelayLoop tự động (Phase C.5) → POST /api/v1/sync/{entity} (X-API-Key)
      → crm_source_records → DomainProjector → projects/areas/units/deals (Backend)

Đọc `docs/mini_crm_seed.json`:

    {
      "projects": [{"external_key": "prj-ocean", "operation": "upsert",
                     "name": "Ocean Park", "launch_date": "2026-01-15"}, ...],
      "areas":    [{"external_key": "ar-2br", "operation": "upsert",
                     "project_external_key": "prj-ocean", "area_name": "Tower A 2BR",
                     "unit_type": "2PN", "bedrooms": 2, "area_sqm": 75.5,
                     "total_units": 100}, ...],
      "units":    [{"external_key": "u-a0101", "operation": "upsert",
                     "area_external_key": "ar-2br", "unit_code": "A-0101",
                     "unit_status": "available"}, ...],           # tuỳ chọn
      "deals":    [{"external_key": "d-a0101", "operation": "upsert",
                     "unit_external_key": "u-a0101", "deal_status": "reserved",
                     "reserved_at": "2026-08-14T09:00:00+07:00"}, ...]  # tuỳ chọn
    }

`operation` ∈ {"upsert", "archive"}, mặc định "upsert" nếu vắng mặt. `external_key`
là khoá CỤC BỘ của file fixture — KHÔNG BAO GIỜ là `external_id` thật của Mini
CRM (`ProjectCreate`/`AreaCreate`/`UnitCreate`/`DealCreate`, `minicrm/app/
schemas.py`, không khai trường đó — Mini CRM luôn tự cấp qua sequence: `P-0001`,
`A-0001`, `U-0001`, `D-0001`).

**Danh tính là DUY NHẤT nguồn idempotency, KHÔNG BAO GIỜ khớp theo `name`**
(`crm_projects.name` không có ràng buộc duy nhất — hai dự án trùng tên là hợp lệ
ở tầng DB, `POST /projects` sẽ tạo bản THỨ HAI, không 409). `external_key ->
external_id` được lưu bền trong `docs/.mini_crm_seed_state.json` (gitignored) —
xem `IdentityMap`. Area/Unit CÓ khoá tự nhiên thật ở DB
(`uq_crm_areas_project_name_unit_type`, `uq_crm_units_live_code`) nên script vẫn
tận dụng chúng làm lưới an toàn phụ (dò lại khi 409/trước khi POST), nhưng
identity map luôn là nguồn khớp CHÍNH.

Units phải ĐƯỢC MIRROR (`mirrored_revision IS NOT NULL`) trước khi bất kỳ Deal
nào tham chiếu chúng được gửi — `crud.create_deal` (`_require_mirrored_unit`) từ
chối bằng 409 `UNIT_NOT_MIRRORED` nếu không. Script CHỦ ĐỘNG chờ (poll có giới
hạn thời gian), không dựa vào may rủi thời điểm.

**KHÔNG đụng `MINICRM_PROJECT_ID`** (`.env`) — biến đó chỉ phong bì v1 (unit/deal
kế thừa) dùng, và Phase D đã xác nhận v1 CHỦ ĐÍCH chết (`422 UNKNOWN_PROJECT`,
`sync=sync_failed` là kết quả ĐÚNG, không phải lỗi cần "sửa"). Repoint biến đó
sang một UUID Backend thật sẽ khiến v1 "sống lại" và va với v2 ở CÙNG revision
nhưng KHÁC dấu vân payload (`MAPPED_FIELDS["units"]` gồm cả `area_name/unit_type`
lẫn `external_area_id`) → `conflict` giả. Xem `pipeline_status.md` dòng ~3034.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
MINICRM_ENV_FILE = REPO_ROOT / "minicrm" / ".env"
ROOT_ENV_FILE = REPO_ROOT / ".env"
SEED_FILE = REPO_ROOT / "docs" / "mini_crm_seed.json"
# Trạng thái CỦA MÁY ĐANG CHẠY, không phải dữ liệu seed — gitignored (rule ".env"
# trong .gitignore KHÔNG khớp file này; xem mục riêng được thêm ở đó).
STATE_FILE = REPO_ROOT / "docs" / ".mini_crm_seed_state.json"

DEFAULT_MINICRM_URL = "http://localhost:8100"
DEFAULT_BACKEND_URL = "http://localhost:8000"
ADMIN_TOKEN_ENV = "MINICRM_AUTH_ADMIN_TOKEN"
# CHỈ dùng để ĐỌC (xác minh chiếu Backend, Phase C) — KHÔNG BAO GIỜ dùng để ghi.
# Backend là read-only ingest+projection; script không có, và không cần, đường
# ghi nào vào Backend.
BACKEND_TOKEN_ENV = "DASHBOARD_ADMIN_TOKEN"

FALLBACK_LAUNCH_DATE = "2026-01-01"

DEFAULT_MIRROR_TIMEOUT = 30.0
DEFAULT_MIRROR_INTERVAL = 1.0
DEFAULT_PROJECTION_TIMEOUT = 30.0
DEFAULT_PROJECTION_INTERVAL = 1.0
DEFAULT_DELIVERY_TIMEOUT = 30.0
DEFAULT_DELIVERY_INTERVAL = 1.0

Operation = Literal["upsert", "archive"]
AUTH_CLASS_STATUSES = frozenset({401, 403, 503})

# `sync_client.V2_CAPTURE_ENTITIES` — bốn nhãn outbox v2. `POST /outbox/{id}/
# resend` TỪ CHỐI thẳng các nhãn này (`V2_DELIVERY_NOT_ENABLED`, xác minh THẬT
# bằng request) — "đường phục hồi" chỉ tồn tại cho v1 ("units"/"deals"). Một dòng
# v2 tử thư KHÔNG có lệnh sửa nào qua API hiện có; chỉ một mutation MỚI trên
# chính thực thể đó mới tạo dòng outbox mới để relay thử lại.
V2_ENTITIES = frozenset({"projects", "areas", "units_v2", "deals_v2"})
# Nhãn outbox v1. Mỗi mutation unit/deal ghi MỘT dòng v1 (đẩy đồng bộ ngay trong
# request) SONG SONG với dòng v2 của nó.
V1_ENTITIES = frozenset({"units", "deals"})

# Thực thể → các nhãn outbox mà MỘT mutation của nó sinh ra.
OUTBOX_ENTITIES_FOR_KIND: dict[str, tuple[str, ...]] = {
    "project": ("projects",),
    "area": ("areas",),
    "unit": ("units", "units_v2"),
    "deal": ("deals", "deals_v2"),
}

# Lane v1 CHẾT CÓ CHỦ ĐÍCH: `MINICRM_PROJECT_ID` trỏ vào một UUID không tồn tại ở
# Backend, nên MỌI phong bì v1 trả về đúng cặp (422, UNKNOWN_PROJECT). Đây là kết
# quả ĐÚNG theo cấu hình, không phải một hồi quy — repoint biến đó sẽ làm v1 sống
# lại và va với v2 ở cùng revision nhưng khác dấu vân payload (`conflict` giả).
# Vì vậy ĐÚNG cặp này, trên ĐÚNG nhãn v1, là dòng 4xx duy nhất KHÔNG chặn lần
# chạy. Mọi 4xx khác — kể cả một 4xx khác trên nhãn v1 — vẫn chặn.
EXPECTED_DEAD_V1_STATUS = 422
EXPECTED_DEAD_V1_ERROR_CODE = "UNKNOWN_PROJECT"

# Trạng thái từng bản ghi, theo đúng vòng đời đã kiểm chứng của đường đồng bộ.
RecordState = Literal[
    "CRUD_ACCEPTED",
    "OUTBOX_PENDING",
    "DELIVERY_ACCEPTED",
    "DELIVERY_FAILED_RETRYABLE",
    "DEAD_LETTER",
    "PROJECTION_CONFIRMED",
    "TOMBSTONE_CONFIRMED",
    "PROJECTION_UNCONFIRMED",
    "TOMBSTONE_VERIFICATION_UNAVAILABLE",
    "NO_MUTATION",
]
# Trạng thái giao hàng CHẶN lần chạy (khác với "chưa mutate gì" và với
# `DELIVERY_ACCEPTED`).
BLOCKING_DELIVERY_STATES = frozenset(
    {"OUTBOX_PENDING", "DELIVERY_FAILED_RETRYABLE", "DEAD_LETTER"}
)


class SeedError(RuntimeError):
    pass


class DependencyError(SeedError):
    """Một mutation phụ thuộc vào một fixture cha KHÔNG có danh tính đã biết —
    KHÔNG được đoán, KHÔNG được gửi một request mơ hồ."""

    def __init__(
        self,
        *,
        fixture_key: str,
        parent_fixture_key: str,
        expected_identity: str,
        http_status: int | None = None,
        body: Any = None,
        recovery_hint: str,
    ) -> None:
        self.fixture_key = fixture_key
        self.parent_fixture_key = parent_fixture_key
        self.expected_identity = expected_identity
        self.http_status = http_status
        self.body = body
        self.recovery_hint = recovery_hint
        super().__init__(
            f"Phụ thuộc hỏng: '{fixture_key}' cần '{parent_fixture_key}' "
            f"(mong đợi danh tính: {expected_identity}) — HTTP {http_status}: {body}. "
            f"Gợi ý khắc phục: {recovery_hint}"
        )


def _load_local_env() -> None:
    """Nạp token cục bộ từ `.env`, KHÔNG bao giờ ghi đè biến đã có sẵn trong
    shell (`override=False`) — biến do người dùng `export` luôn thắng.

    Nạp `.env` ở gốc repo TRƯỚC (nguồn credential thật của container Compose,
    quy ước đã có ở `minicrm/tests/real_env.py::_env_file`), `minicrm/.env` SAU
    chỉ để bù giá trị gốc chưa có (Mini CRM chạy bare-metal, không qua Compose).
    """
    load_dotenv(ROOT_ENV_FILE, override=False)
    load_dotenv(MINICRM_ENV_FILE, override=False)


class IdentityMap:
    """Ánh xạ BỀN VỮNG `external_key` (khoá fixture cục bộ) → `external_id` THẬT
    của Mini CRM. Đây là NGUỒN DUY NHẤT của idempotency trong script này.

    Nếu file này bị xoá, mọi fixture bị coi là CHƯA TỪNG tồn tại và sẽ được TẠO
    MỚI — script không tự dò lại bằng tên (`crm_projects.name` không duy nhất).
    Đây là giới hạn đã biết, ghi lại tường minh trong "Residual risks" của báo
    cáo, không phải một khiếm khuyết bị giấu.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict[str, str]] = {}
        if path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, external_key: str) -> str | None:
        entry = self._data.get(external_key)
        return entry["external_id"] if entry else None

    def set(self, external_key: str, kind: str, external_id: str) -> None:
        self._data[external_key] = {"kind": kind, "external_id": external_id}
        self._save()

    def clear(self, external_key: str) -> None:
        """Xoá một ánh xạ ĐÃ HỎNG (GET 404 — bản ghi không còn ở Mini CRM, vd. DB
        được migrate lại từ đầu trong khi file trạng thái cục bộ này thì không).
        Sau lệnh này, `external_key` bị coi như CHƯA TỪNG tồn tại — đúng ngữ nghĩa
        "cả file bị xoá" ở trên, áp dụng cho TỪNG mục thay vì toàn bộ file."""
        if external_key in self._data:
            del self._data[external_key]
            self._save()

    def all_keys(self) -> set[str]:
        return set(self._data)

    def items(self) -> list[tuple[str, dict[str, str]]]:
        return list(self._data.items())

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def _error_code(body: Any) -> str | None:
    """Rút `error_code` từ MỘT trong hai hình dạng phản hồi lỗi của Mini CRM:

        {"error_code": ..., "message": ..., "detail": ..., "sent": ...}   CrudError
        {"detail": {"message": ..., "error_code": ...}}                   HTTPException (auth.py)
    """
    if not isinstance(body, dict):
        return None
    if "error_code" in body:
        return body["error_code"]
    detail = body.get("detail")
    if isinstance(detail, dict):
        return detail.get("error_code")
    return None


def _classify(resp: httpx.Response) -> str:
    """`'<status> OK'` cho 2xx, `'<status> <error_code>'` cho lỗi — KHÔNG BAO
    GIỜ dựa vào status thô một mình.

    409 từ `/deals` đặc biệt: `UNIT_NOT_MIRRORED` (chờ được, KHÔNG phải skip
    lành tính) khác hẳn `UNIT_ALREADY_HELD` (nghiệp vụ thật) — cùng status,
    khác hành động đúng. Luôn đọc field này qua `error_code_of()`, không so
    chuỗi trả về đây.
    """
    if 200 <= resp.status_code < 300:
        return f"{resp.status_code} OK"
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = _error_code(body) or "UNKNOWN"
    return f"{resp.status_code} {code}"


def error_code_of(resp: httpx.Response) -> str | None:
    try:
        return _error_code(resp.json())
    except ValueError:
        return None


def _mask(token: str) -> str:
    if not token:
        return "(trống)"
    return f"{token[:6]}…(len={len(token)})"


@dataclass
class ItemResult:
    fixture_key: str
    kind: str
    status: str  # created|updated|unchanged|archived|failed|dependency_error
    external_id: str | None
    detail: str = ""
    operation: str = "upsert"
    # Các `external_batch_id` mà CHÍNH mutation này sinh ra — rỗng khi không có
    # mutation (`unchanged`) hoặc khi mutation thất bại.
    batches: list[str] = field(default_factory=list)
    # `source_revision` mà Mini CRM trả về NGAY SAU mutation này. Đích để so ở
    # phía Backend — không phải "có dòng nào đó cùng external_id" mà là "dòng đó
    # đã tới ÍT NHẤT phiên bản của lần ghi này".
    expected_revision: int | None = None
    # Trường nghiệp vụ đã đổi, dùng cho tầng KHÔNG phơi `source_revision` ra API
    # đọc (units — xem `InventoryUnitOut`).
    expected_field: tuple[str, Any] | None = None
    delivery: str = "NO_MUTATION"
    state: str = "CRUD_ACCEPTED"

    def mutated(self) -> bool:
        return self.status in ("created", "updated", "archived")


@dataclass
class RunSummary:
    results: list[ItemResult] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""

    def add(self, item: ItemResult) -> ItemResult:
        self.results.append(item)
        return item

    def failures(self) -> list[ItemResult]:
        return [r for r in self.results if r.status in ("failed", "dependency_error")]


@dataclass(frozen=True)
class OutboxRow:
    """Một dòng outbox rút gọn, đủ để phân loại — KHÔNG giữ payload."""

    external_batch_id: str
    entity: str
    http_status: int | None
    error_code: str | None

    @property
    def is_dead_letter(self) -> bool:
        return self.http_status is not None and 400 <= self.http_status < 500

    @property
    def is_expected_dead_v1(self) -> bool:
        """Lane v1 chết có chủ đích — xem `EXPECTED_DEAD_V1_*`.

        Hẹp có chủ ý: ĐÚNG nhãn v1, ĐÚNG status, ĐÚNG error_code. Một 401 trên
        nhãn v1 (khoá đồng bộ hỏng) KHÔNG lọt qua đây và vẫn chặn lần chạy.
        """
        return (
            self.entity in V1_ENTITIES
            and self.http_status == EXPECTED_DEAD_V1_STATUS
            and self.error_code == EXPECTED_DEAD_V1_ERROR_CODE
        )

    def delivery_state(self) -> str:
        if self.http_status is None:
            return "OUTBOX_PENDING"
        if 200 <= self.http_status < 300:
            return "DELIVERY_ACCEPTED"
        if self.http_status >= 500:
            return "DELIVERY_FAILED_RETRYABLE"
        return "DEAD_LETTER"


class OutboxTracker:
    """Ranh giới lần chạy, đo bằng ĐÚNG `external_batch_id`, không bằng thời gian.

    `snapshot()` ghi lại TOÀN BỘ batch id đã tồn tại TRƯỚC khi mutation đầu tiên
    chạy. Từ đó, một batch id không có trong ảnh chụp = do CHÍNH lần chạy này
    sinh ra. Đây là lý do không dùng mốc thời gian: đồng hồ của script và của
    database không cùng nguồn, và một dòng ghi trong cùng giây với ảnh chụp sẽ
    rơi vào vùng mơ hồ mà không có cách nào phân xử.

    CHỈ đi qua HTTP (`GET /outbox`, `GET /outbox/{id}`) — script không chạm
    `crm_outbox` bằng SQL, và không có đường ghi nào vào outbox ở đây.
    """

    def __init__(self, client: httpx.Client, *, page_size: int = 200) -> None:
        self.client = client
        self.page_size = page_size
        self._baseline: dict[str, OutboxRow] = {}
        self._seen: set[str] = set()
        self._current: dict[str, OutboxRow] = {}

    # --- Đọc ---------------------------------------------------------------

    def _page(self, offset: int) -> dict:
        resp = self.client.get("/outbox", params={"limit": self.page_size, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    def _summary_row(self, item: dict) -> OutboxRow:
        """Dòng danh sách KHÔNG kèm `response`, nên `error_code` chỉ lấy được ở
        đường chi tiết — và chỉ cần lấy khi dòng thật sự là 4xx."""
        status = item["http_status"]
        code: str | None = None
        if status is not None and 400 <= status < 500:
            code = self._error_code_of_batch(item["external_batch_id"])
        return OutboxRow(
            external_batch_id=item["external_batch_id"],
            entity=item["entity"],
            http_status=status,
            error_code=code,
        )

    def _error_code_of_batch(self, batch_id: str) -> str | None:
        detail = self.detail(batch_id)
        if detail is None:
            return None
        return _error_code(detail.get("response"))

    def detail(self, batch_id: str) -> dict | None:
        resp = self.client.get(f"/outbox/{batch_id}")
        if resp.status_code != 200:
            return None
        return resp.json()

    # --- Ranh giới lần chạy --------------------------------------------------

    def snapshot(self) -> None:
        """Chụp toàn bộ outbox HIỆN CÓ. Gọi TRƯỚC mutation đầu tiên."""
        offset = 0
        while True:
            page = self._page(offset)
            items = page["items"]
            for item in items:
                row = self._summary_row(item)
                self._baseline[row.external_batch_id] = row
                self._seen.add(row.external_batch_id)
            offset += len(items)
            if not items or offset >= page["total"]:
                break

    def discover(self, *, external_id: str, entities: tuple[str, ...]) -> list[str]:
        """Batch id MỚI mà payload của nó chứa `external_id`, giới hạn ở `entities`.

        Quét từ đầu danh sách (mới nhất trước) và DỪNG ở trang đầu tiên không còn
        dòng lạ nào — mutation vừa xong luôn nằm ở đầu, nên không cần quét hết.
        Mọi dòng lạ gặp trên đường đều được đánh dấu đã thấy, kể cả dòng không
        khớp `external_id`, để lần `discover()` sau không phải tải lại chi tiết
        của chúng.
        """
        matched: list[str] = []
        offset = 0
        while True:
            page = self._page(offset)
            items = page["items"]
            fresh = [it for it in items if it["external_batch_id"] not in self._seen]
            for item in fresh:
                batch_id = item["external_batch_id"]
                self._seen.add(batch_id)
                if item["entity"] not in entities:
                    continue
                if self._payload_mentions(batch_id, external_id):
                    matched.append(batch_id)
            offset += len(items)
            if not fresh or not items or offset >= page["total"]:
                break
        return sorted(matched)

    def _payload_mentions(self, batch_id: str, external_id: str) -> bool:
        detail = self.detail(batch_id)
        if detail is None:
            return False
        records = (detail.get("payload") or {}).get("records") or []
        return any(r.get("external_id") == external_id for r in records)

    # --- Chờ giao hàng -------------------------------------------------------

    def wait_for_delivery(self, batch_id: str, *, timeout: float, interval: float) -> OutboxRow:
        """Thăm dò tới khi lô có kết quả CHUNG CUỘC, hoặc hết giờ.

        2xx và 4xx là chung cuộc (relay không chọn lại chúng —
        `relay.py::_pending_query`). NULL và 5xx thì relay CÒN thử lại, nên tiếp
        tục chờ; hết giờ thì trả về đúng trạng thái quan sát được cuối cùng, không
        nâng cấp nó thành một kết luận đẹp hơn.
        """
        deadline = time.monotonic() + timeout
        last = OutboxRow(batch_id, "?", None, None)
        while True:
            detail = self.detail(batch_id)
            if detail is not None:
                last = OutboxRow(
                    external_batch_id=batch_id,
                    entity=detail["entity"],
                    http_status=detail["http_status"],
                    error_code=_error_code(detail.get("response")),
                )
                if last.delivery_state() in ("DELIVERY_ACCEPTED", "DEAD_LETTER"):
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        self._current[batch_id] = last
        return last

    def record(self, row: OutboxRow) -> None:
        self._current[row.external_batch_id] = row

    # --- Báo cáo -------------------------------------------------------------

    def baseline_size(self) -> int:
        return len(self._baseline)

    def historical_dead_letters(self) -> list[OutboxRow]:
        return [r for r in self._baseline.values() if r.is_dead_letter]

    def current_dead_letters(self) -> list[OutboxRow]:
        return [r for r in self._current.values() if r.is_dead_letter]

    def blocking_dead_letters(self) -> list[OutboxRow]:
        return [r for r in self.current_dead_letters() if not r.is_expected_dead_v1]

    def expected_dead_v1(self) -> list[OutboxRow]:
        return [r for r in self.current_dead_letters() if r.is_expected_dead_v1]


def resend_guidance(row: OutboxRow, base_url: str) -> str:
    """Lệnh gửi lại CHÍNH XÁC cho v1; nói thẳng là KHÔNG có lệnh nào cho v2."""
    if row.entity in V2_ENTITIES:
        return (
            "KHÔNG có lệnh gửi lại (v2 — /outbox/{id}/resend từ chối nhãn này bằng "
            "V2_DELIVERY_NOT_ENABLED). Chỉ một mutation MỚI trên cùng thực thể mới "
            "thử lại được; dòng này tử thư vĩnh viễn."
        )
    return f"POST {base_url}/outbox/{row.external_batch_id}/resend"


def preflight(
    *, minicrm_url: str, backend_url: str, admin_token: str, backend_token: str, require_backend_token: bool = True
) -> None:
    """Kiểm TRƯỚC KHI GHI BẤT CỨ GÌ. Fail closed. KHÔNG BAO GIỜ in giá trị token.

    Mini CRM không có route GET nào đòi xác thực (`GET /outbox`/`GET /projects`
    đều mở — xem docstring `minicrm/app/routers/outbox.py`: "Hai route GET giữ
    NGUYÊN mở"), nên KHÔNG có cách xác minh vai trò của token mà không thử ghi
    thật. Preflight ở đây xác minh những gì XÁC MINH ĐƯỢC mà không mutate: biến
    môi trường có mặt, hai dịch vụ khoẻ. Token SAI (khác token RỖNG) chỉ lộ ra ở
    lần ghi ĐẦU TIÊN — khi đó toàn bộ phần còn lại của lô dừng ngay (xem
    `RunSummary.aborted`), không tiếp tục sang area/unit/deal nào khác.
    """
    print("=== Preflight ===")
    print(f"MINICRM_URL              = {minicrm_url}")
    print(f"BACKEND_URL               = {backend_url}")
    print(f"{ADMIN_TOKEN_ENV:<26}= {_mask(admin_token)}")
    print(f"{BACKEND_TOKEN_ENV:<26}= {_mask(backend_token)}")

    if not admin_token:
        raise SeedError(
            f"Thiếu biến môi trường {ADMIN_TOKEN_ENV} — POST /projects đòi vai trò admin. "
            f"Cấu hình trong {ROOT_ENV_FILE} hoặc {MINICRM_ENV_FILE}, hoặc export trong shell."
        )
    if require_backend_token and not backend_token:
        raise SeedError(
            f"Thiếu biến môi trường {BACKEND_TOKEN_ENV} — cần để XÁC MINH lô đã chiếu sang Backend "
            "(Phase C của simulator này), không dùng để ghi bất cứ gì."
        )

    try:
        r = httpx.get(f"{minicrm_url}/health", timeout=5.0)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SeedError(f"Mini CRM không phản hồi khoẻ mạnh ở {minicrm_url}: {exc}") from exc

    try:
        r = httpx.get(f"{backend_url}/health", timeout=5.0)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SeedError(f"Backend không phản hồi khoẻ mạnh ở {backend_url}: {exc}") from exc

    print("Preflight OK — biến môi trường có mặt, cả hai dịch vụ khoẻ.\n")


def _load_seed(identity: IdentityMap, seed_file: Path = SEED_FILE) -> dict[str, Any]:
    if not seed_file.exists():
        try:
            shown = seed_file.relative_to(REPO_ROOT)
        except ValueError:
            shown = seed_file
        raise SeedError(f"Không thấy {shown}. File này phải được tạo trước — script không tự bịa dữ liệu.")
    data = json.loads(seed_file.read_text(encoding="utf-8"))
    missing = {"projects", "areas"} - data.keys()
    if missing:
        raise SeedError(f"{seed_file.name} thiếu khoá bắt buộc: {sorted(missing)}")

    projects = data.get("projects", [])
    areas = data.get("areas", [])
    units = data.get("units", [])
    deals = data.get("deals", [])

    # --- Kiểm CỤC BỘ, KHÔNG một request nào rời máy trước khi qua hết đây ----

    # 1. external_key trùng lặp trong TOÀN FILE (không phân biệt tầng) — hai
    #    fixture khác tầng dùng chung một khoá là lỗi cấu hình chắc chắn.
    seen: dict[str, str] = {}
    for kind, items in (("project", projects), ("area", areas), ("unit", units), ("deal", deals)):
        for item in items:
            key = item.get("external_key")
            if not key:
                raise SeedError(f"{kind} thiếu 'external_key': {item}")
            if key in seen:
                raise SeedError(f"external_key trùng lặp '{key}' — xuất hiện ở cả '{seen[key]}' và '{kind}'")
            seen[key] = kind

    # 2. operation hợp lệ.
    for kind, items in (("project", projects), ("area", areas), ("unit", units), ("deal", deals)):
        for item in items:
            op = item.get("operation", "upsert")
            if op not in ("upsert", "archive"):
                raise SeedError(f"{kind} '{item['external_key']}': operation '{op}' không hợp lệ (upsert|archive)")

    # 3. Tham chiếu cha PHẢI giải được cục bộ: có trong CHÍNH file này, hoặc đã
    #    có trong identity map từ lần chạy trước. Không đoán, không gửi thử.
    project_keys = {p["external_key"] for p in projects} | identity.all_keys()
    for a in areas:
        ref = a.get("project_external_key")
        if not ref or ref not in project_keys:
            raise DependencyError(
                fixture_key=a["external_key"],
                parent_fixture_key=str(ref),
                expected_identity="một project_external_key có trong 'projects' của file này hoặc đã upsert trước đó",
                recovery_hint=f"thêm project '{ref}' vào 'projects', hoặc sửa lại project_external_key",
            )

    area_keys = {a["external_key"] for a in areas} | identity.all_keys()
    for u in units:
        ref = u.get("area_external_key")
        if not ref or ref not in area_keys:
            raise DependencyError(
                fixture_key=u["external_key"],
                parent_fixture_key=str(ref),
                expected_identity="một area_external_key có trong 'areas' của file này hoặc đã upsert trước đó",
                recovery_hint=f"thêm area '{ref}' vào 'areas', hoặc sửa lại area_external_key",
            )

    unit_keys = {u["external_key"] for u in units} | identity.all_keys()
    for d in deals:
        ref = d.get("unit_external_key")
        if not ref or ref not in unit_keys:
            raise DependencyError(
                fixture_key=d["external_key"],
                parent_fixture_key=str(ref),
                expected_identity="một unit_external_key có trong 'units' của file này hoặc đã upsert trước đó",
                recovery_hint=f"thêm unit '{ref}' vào 'units', hoặc sửa lại unit_external_key",
            )

    return data


class MiniCrmSeeder:
    def __init__(
        self,
        client: httpx.Client,
        identity: IdentityMap,
        tracker: OutboxTracker,
        *,
        mirror_timeout: float,
        mirror_interval: float,
        refresh_existing: bool = False,
    ) -> None:
        self.client = client
        self.identity = identity
        self.tracker = tracker
        self.mirror_timeout = mirror_timeout
        self.mirror_interval = mirror_interval
        self.refresh_existing = refresh_existing
        self.summary = RunSummary()

    def _track(
        self,
        item: ItemResult,
        *,
        kind: str,
        external_id: str,
        record: dict | None = None,
        expected_field: tuple[str, Any] | None = None,
    ) -> ItemResult:
        """Gắn vào `item` các batch id mà CHÍNH mutation này vừa sinh ra.

        Gọi NGAY sau một mutation thành công, trước mutation kế — `discover()`
        quét từ đầu danh sách outbox, nên trì hoãn sẽ trộn batch của nhiều
        mutation vào nhau và mất khả năng quy trách nhiệm từng bản ghi.
        """
        item.batches = self.tracker.discover(
            external_id=external_id, entities=OUTBOX_ENTITIES_FOR_KIND[kind]
        )
        if record is not None:
            item.expected_revision = record.get("source_revision")
        item.expected_field = expected_field
        return item

    def _get(self, path: str, **kwargs) -> httpx.Response:
        r = self.client.get(path, **kwargs)
        r.raise_for_status()
        return r

    def _maybe_abort(self, resp: httpx.Response, context: str) -> None:
        if resp.status_code in AUTH_CLASS_STATUSES:
            self.summary.aborted = True
            self.summary.abort_reason = f"{context}: {_classify(resp)} — dừng TOÀN BỘ lô, không ghi tiếp"

    # --- Projects -------------------------------------------------------

    def seed_projects(self, items: list[dict]) -> dict[str, str]:
        """Trả `{external_key: external_id}`. Khớp DUY NHẤT qua identity map —
        KHÔNG BAO GIỜ dò theo `name` (không có ràng buộc duy nhất ở DB)."""
        out: dict[str, str] = {}
        for p in items:
            if self.summary.aborted:
                break
            key = p["external_key"]
            op = p.get("operation", "upsert")

            if op == "archive":
                existing_id = self.identity.get(key)
                if existing_id is None:
                    self.summary.add(ItemResult(key, "project", "failed", None, "archive: chưa có danh tính đã biết"))
                    continue
                resp = self.client.delete(f"/projects/{existing_id}")
                if resp.status_code == 200:
                    out[key] = existing_id
                    item = ItemResult(
                        key, "project", "archived", existing_id, _classify(resp), operation="archive"
                    )
                    self._track(item, kind="project", external_id=existing_id, record=resp.json()["record"])
                    self.summary.add(item)
                else:
                    self.summary.add(ItemResult(key, "project", "failed", existing_id, _classify(resp)))
                    self._maybe_abort(resp, f"project '{key}' archive")
                continue

            # `.get()`, KHÔNG `[...]`: một trường bắt buộc VẮNG MẶT phải trở
            # thành `None` trong payload rồi bị CHÍNH API từ chối 422 (Pydantic
            # `Field(...)` ở `ProjectCreate`) — KHÔNG được crash Python bằng
            # `KeyError` thô, đúng thứ "no error class swallowed" cấm.
            name = p.get("name")
            launch_date = p.get("launch_date", FALLBACK_LAUNCH_DATE)
            existing_id = self.identity.get(key)

            if existing_id is not None:
                resp, stale = self._get_existing(f"/projects/{existing_id}", key=key, kind="project")
                if resp is not None:
                    current = resp.json()
                    patch = {
                        "name": name,
                        "launch_date": launch_date,
                    } if self.refresh_existing else {
                        field_name: value
                        for field_name, value in (
                            ("name", name),
                            ("launch_date", launch_date),
                        )
                        if str(current[field_name]) != str(value)
                    }
                    if patch:
                        r2 = self.client.patch(f"/projects/{existing_id}", json=patch)
                        if r2.status_code == 200:
                            out[key] = existing_id
                            item = ItemResult(key, "project", "updated", existing_id, _classify(r2))
                            self._track(item, kind="project", external_id=existing_id, record=r2.json()["record"])
                            self.summary.add(item)
                        else:
                            self.summary.add(ItemResult(key, "project", "failed", existing_id, _classify(r2)))
                            self._maybe_abort(r2, f"project '{key}' update")
                    else:
                        out[key] = existing_id
                        # KHÔNG mutate ⇒ KHÔNG có batch nào để chờ. Vẫn giữ
                        # `expected_revision` để Phase D so được chiếu Backend với
                        # phiên bản Mini CRM đang giữ.
                        self.summary.add(
                            ItemResult(
                                key,
                                "project",
                                "unchanged",
                                existing_id,
                                expected_revision=current.get("source_revision"),
                            )
                        )
                    continue
                if not stale:
                    continue
                existing_id = None  # ánh xạ cũ đã hỏng — rơi xuống nhánh tạo mới

            resp = self.client.post("/projects", json={"name": name, "launch_date": launch_date})
            if resp.status_code == 201:
                record = resp.json()["record"]
                self.identity.set(key, "project", record["external_id"])
                out[key] = record["external_id"]
                item = ItemResult(key, "project", "created", record["external_id"], _classify(resp))
                self._track(item, kind="project", external_id=record["external_id"], record=record)
                self.summary.add(item)
            else:
                self.summary.add(ItemResult(key, "project", "failed", None, _classify(resp)))
                self._maybe_abort(resp, f"project '{key}' create")
        return out

    def _get_existing(self, path: str, *, key: str, kind: str) -> tuple[httpx.Response | None, bool]:
        """GET một bản ghi theo `external_id` đã biết trong `IdentityMap`.

        Trả `(response, was_stale)`. 404 nghĩa là ánh xạ CŨ đã hỏng — bản ghi
        không còn tồn tại ở Mini CRM (vd. DB được migrate lại từ đầu trong khi
        `docs/.mini_crm_seed_state.json` cục bộ thì không) — KHÔNG chặn lần chạy:
        xoá khỏi `IdentityMap` (`IdentityMap.clear`) và báo `was_stale=True` để
        nơi gọi coi `key` như CHƯA TỪNG được tạo, tự tạo lại. Mọi status khác
        (401/403/409/422/503/...) vẫn là THẤT BẠI THẬT — ghi `failed`, chặn như
        trước (không được lẫn với staleness).
        """
        resp = self.client.get(path)
        if resp.status_code == 200:
            return resp, False
        if resp.status_code == 404:
            stale_id = path.rsplit("/", 1)[-1]
            print(
                f"  [tự phục hồi] {kind} '{key}': danh tính cũ '{stale_id}' không còn tồn tại "
                "ở Mini CRM (404) — coi như chưa từng tạo, tạo lại."
            )
            self.identity.clear(key)
            return None, True
        self.summary.add(ItemResult(key, kind, "failed", path.rsplit("/", 1)[-1], _classify(resp)))
        self._maybe_abort(resp, f"GET {path}")
        return None, False

    # --- Areas ------------------------------------------------------------

    def seed_areas(self, items: list[dict], project_ids: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for a in items:
            if self.summary.aborted:
                break
            key = a["external_key"]
            op = a.get("operation", "upsert")
            json_project_key = a.get("project_external_key")
            external_project_id = project_ids.get(json_project_key) or self.identity.get(json_project_key)

            if external_project_id is None:
                raise DependencyError(
                    fixture_key=key,
                    parent_fixture_key=str(json_project_key),
                    expected_identity="external_id của project cha",
                    recovery_hint="project cha phải upsert thành công TRƯỚC area này trong cùng lần chạy",
                )

            if op == "archive":
                existing_id = self.identity.get(key)
                if existing_id is None:
                    self.summary.add(ItemResult(key, "area", "failed", None, "archive: chưa có danh tính đã biết"))
                    continue
                resp = self.client.delete(f"/areas/{existing_id}")
                if resp.status_code == 200:
                    out[key] = existing_id
                    item = ItemResult(key, "area", "archived", existing_id, _classify(resp), operation="archive")
                    self._track(item, kind="area", external_id=existing_id, record=resp.json()["record"])
                    self.summary.add(item)
                else:
                    self.summary.add(ItemResult(key, "area", "failed", existing_id, _classify(resp)))
                    self._maybe_abort(resp, f"area '{key}' archive")
                continue

            payload = {
                "external_project_id": external_project_id,
                "area_name": a.get("area_name"),
                "unit_type": a.get("unit_type"),
                "bedrooms": a.get("bedrooms"),
                "area_sqm": a.get("area_sqm"),
                "total_units": a.get("total_units"),
            }
            existing_id = self.identity.get(key)

            if existing_id is not None:
                resp, stale = self._get_existing(f"/areas/{existing_id}", key=key, kind="area")
                if resp is not None:
                    current = resp.json()
                    patch = (
                        {
                            field_name: payload[field_name]
                            for field_name in ("area_name", "unit_type", "bedrooms", "area_sqm", "total_units")
                        }
                        if self.refresh_existing
                        else {
                            field_name: payload[field_name]
                            for field_name in ("area_name", "unit_type", "bedrooms", "area_sqm", "total_units")
                            if current.get(field_name) != payload[field_name]
                        }
                    )
                    if patch:
                        r2 = self.client.patch(f"/areas/{existing_id}", json=patch)
                        if r2.status_code == 200:
                            out[key] = existing_id
                            item = ItemResult(key, "area", "updated", existing_id, _classify(r2))
                            self._track(item, kind="area", external_id=existing_id, record=r2.json()["record"])
                            self.summary.add(item)
                        else:
                            self.summary.add(ItemResult(key, "area", "failed", existing_id, _classify(r2)))
                            self._maybe_abort(r2, f"area '{key}' update")
                    else:
                        out[key] = existing_id
                        self.summary.add(
                            ItemResult(
                                key, "area", "unchanged", existing_id, expected_revision=current.get("source_revision")
                            )
                        )
                    continue
                if not stale:
                    continue
                existing_id = None  # ánh xạ cũ đã hỏng — rơi xuống nhánh tạo mới

            resp = self.client.post("/areas", json=payload)
            if resp.status_code == 201:
                record = resp.json()["record"]
                self.identity.set(key, "area", record["external_id"])
                out[key] = record["external_id"]
                item = ItemResult(key, "area", "created", record["external_id"], _classify(resp))
                self._track(item, kind="area", external_id=record["external_id"], record=record)
                self.summary.add(item)
                continue
            if resp.status_code == 409 and error_code_of(resp) == "AREA_NATURAL_KEY_CONFLICT":
                # Lưới an toàn phụ: khoá tự nhiên (area_name, unit_type) THẬT
                # SỰ duy nhất ở DB — dò lại thay vì coi là lỗi.
                rows = self._get(
                    "/areas", params={"external_project_id": external_project_id, "include_archived": "true"}
                ).json()
                match = next(
                    (r for r in rows if r["area_name"] == a.get("area_name") and r["unit_type"] == a.get("unit_type")),
                    None,
                )
                if match is None:
                    self.summary.add(ItemResult(key, "area", "failed", None, _classify(resp)))
                    continue
                self.identity.set(key, "area", match["external_id"])
                existing_id = match["external_id"]
                self.summary.add(
                    ItemResult(
                        key,
                        "area",
                        "unchanged",
                        existing_id,
                        "409 khớp lại qua khoá tự nhiên",
                        expected_revision=match.get("source_revision"),
                    )
                )
                out[key] = existing_id
                continue
            self.summary.add(ItemResult(key, "area", "failed", None, _classify(resp)))
            self._maybe_abort(resp, f"area '{key}' create")
        return out

    # --- Units --------------------------------------------------------------

    def seed_units(self, items: list[dict], area_info: dict[str, dict[str, str]]) -> dict[str, str]:
        """`area_info`: `{external_key: {"external_id":..., "area_name":...}}`.

        Mini CRM KHÔNG bọc va chạm `uq_crm_units_live_code` bằng `CrudError`
        (không có `except IntegrityError` trong `crud.create_unit`, khác hẳn
        `create_area`) — một `unit_code` trùng trong CÙNG phân khu ném
        `IntegrityError` THÔ, FastAPI trả 500 KHÔNG có `error_code`, không phải
        409 sạch. Vì khoá tự nhiên đó vẫn THẬT SỰ duy nhất ở DB, script DÒ TRƯỚC
        bằng GET (an toàn) thay vì dựa vào một nhánh phục hồi 409 không tồn tại
        — xem "Residual risks" của báo cáo về khoảng trống này trong Mini CRM.
        """
        out: dict[str, str] = {}
        for u in items:
            if self.summary.aborted:
                break
            key = u["external_key"]
            op = u.get("operation", "upsert")
            json_area_key = u.get("area_external_key")
            area = area_info.get(json_area_key)
            if area is None:
                raise DependencyError(
                    fixture_key=key,
                    parent_fixture_key=str(json_area_key),
                    expected_identity="external_id + area_name của area cha",
                    recovery_hint="area cha phải upsert thành công TRƯỚC unit này trong cùng lần chạy",
                )

            if op == "archive":
                existing_id = self.identity.get(key)
                if existing_id is None:
                    self.summary.add(ItemResult(key, "unit", "failed", None, "archive: chưa có danh tính đã biết"))
                    continue
                resp = self.client.delete(f"/units/{existing_id}")
                if resp.status_code == 200:
                    out[key] = existing_id
                    item = ItemResult(key, "unit", "archived", existing_id, _classify(resp), operation="archive")
                    self._track(item, kind="unit", external_id=existing_id, record=resp.json()["record"])
                    self.summary.add(item)
                else:
                    self.summary.add(ItemResult(key, "unit", "failed", existing_id, _classify(resp)))
                    self._maybe_abort(resp, f"unit '{key}' archive")
                continue

            unit_status = u.get("unit_status", "available")
            existing_id = self.identity.get(key)

            if existing_id is not None:
                resp, stale = self._get_existing(f"/units/{existing_id}", key=key, kind="unit")
                if resp is not None:
                    current = resp.json()
                    if self.refresh_existing or current.get("unit_status") != unit_status:
                        r2 = self.client.patch(f"/units/{existing_id}", json={"unit_status": unit_status})
                        if r2.status_code == 200:
                            out[key] = existing_id
                            item = ItemResult(key, "unit", "updated", existing_id, _classify(r2))
                            self._track(
                                item,
                                kind="unit",
                                external_id=existing_id,
                                record=r2.json()["record"],
                                expected_field=("status", unit_status),
                            )
                            self.summary.add(item)
                        else:
                            self.summary.add(ItemResult(key, "unit", "failed", existing_id, _classify(r2)))
                            self._maybe_abort(r2, f"unit '{key}' update")
                    else:
                        out[key] = existing_id
                        self.summary.add(
                            ItemResult(
                                key,
                                "unit",
                                "unchanged",
                                existing_id,
                                expected_revision=current.get("source_revision"),
                                expected_field=("status", unit_status),
                            )
                        )
                    continue
                if not stale:
                    continue
                existing_id = None  # ánh xạ cũ đã hỏng — rơi xuống nhánh tạo mới

            # Dò trước bằng khoá tự nhiên (area_name, unit_code) — DB-unique
            # thật, tránh chạm nhánh IntegrityError-thô ở phía Mini CRM.
            existing_rows = self._get("/units", params={"include_deleted": "false"}).json()
            match = next(
                (r for r in existing_rows if r["area_name"] == area["area_name"] and r["unit_code"] == u.get("unit_code")),
                None,
            )
            if match is not None:
                self.identity.set(key, "unit", match["external_id"])
                existing_id = match["external_id"]
                if self.refresh_existing:
                    r2 = self.client.patch(f"/units/{existing_id}", json={"unit_status": unit_status})
                    if r2.status_code == 200:
                        out[key] = existing_id
                        item = ItemResult(key, "unit", "updated", existing_id, _classify(r2))
                        self._track(
                            item,
                            kind="unit",
                            external_id=existing_id,
                            record=r2.json()["record"],
                            expected_field=("status", unit_status),
                        )
                        self.summary.add(item)
                    else:
                        self.summary.add(ItemResult(key, "unit", "failed", existing_id, _classify(r2)))
                        self._maybe_abort(r2, f"unit '{key}' natural-key refresh")
                    continue
                self.summary.add(
                    ItemResult(
                        key,
                        "unit",
                        "unchanged",
                        existing_id,
                        "khớp lại qua khoá tự nhiên trước khi POST",
                        expected_revision=match.get("source_revision"),
                        expected_field=("status", match.get("unit_status")),
                    )
                )
                out[key] = existing_id
                continue

            resp = self.client.post(
                "/units",
                json={
                    "external_area_id": area["external_id"],
                    "unit_code": u.get("unit_code"),
                    "unit_status": unit_status,
                },
            )
            if resp.status_code == 201:
                record = resp.json()["record"]
                self.identity.set(key, "unit", record["external_id"])
                out[key] = record["external_id"]
                item = ItemResult(key, "unit", "created", record["external_id"], _classify(resp))
                # `InventoryUnitOut` KHÔNG phơi `source_revision`, nên tầng unit
                # được xác minh bằng TRƯỜNG NGHIỆP VỤ đã ghi.
                self._track(
                    item,
                    kind="unit",
                    external_id=record["external_id"],
                    record=record,
                    expected_field=("status", unit_status),
                )
                self.summary.add(item)
            else:
                self.summary.add(ItemResult(key, "unit", "failed", None, _classify(resp)))
                self._maybe_abort(resp, f"unit '{key}' create")
        return out

    # --- Deals --------------------------------------------------------------

    def wait_for_unit_mirror(self, unit_external_id: str, *, fixture_key: str) -> int:
        """Chờ CÓ GIỚI HẠN cho tới khi `mirrored_revision IS NOT NULL`.

        `crud.create_deal._require_mirrored_unit` từ chối bằng 409
        `UNIT_NOT_MIRRORED` nếu không — chờ ở ĐÂY thay vì để lỗi đó xảy ra rồi
        coi là "skip lành tính" (đúng thứ Fix #3 cấm)."""
        deadline = time.monotonic() + self.mirror_timeout
        last_mirrored: int | None = None
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            resp = self.client.get(f"/units/{unit_external_id}")
            if resp.status_code != 200:
                raise SeedError(f"unit '{unit_external_id}' (fixture '{fixture_key}'): GET thất bại — {_classify(resp)}")
            body = resp.json()
            last_mirrored = body.get("mirrored_revision")
            if last_mirrored is not None:
                return last_mirrored
            time.sleep(self.mirror_interval)
        elapsed = self.mirror_timeout
        raise SeedError(
            f"unit '{unit_external_id}' (fixture '{fixture_key}') KHÔNG mirror sau {elapsed:.0f}s "
            f"({attempts} lần thăm dò, mỗi {self.mirror_interval}s) — mirrored_revision cuối cùng quan sát: "
            f"{last_mirrored!r}. Kiểm relay còn sống (`MINICRM_RELAY_ENABLED`), hoặc khoá đồng bộ Backend "
            f"(`MINICRM_SYNC_API_KEY` khớp một dòng CÒN SỐNG trong `sync_credentials`)."
        )

    def seed_deals(self, items: list[dict], unit_ids: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        existing_by_unit = {
            row["external_unit_id"]: row
            for row in self._get("/deals", params={"include_deleted": "false"}).json()
        }
        for d in items:
            if self.summary.aborted:
                break
            key = d["external_key"]
            op = d.get("operation", "upsert")
            json_unit_key = d.get("unit_external_key")
            unit_external_id = unit_ids.get(json_unit_key) or self.identity.get(json_unit_key)
            if unit_external_id is None:
                raise DependencyError(
                    fixture_key=key,
                    parent_fixture_key=str(json_unit_key),
                    expected_identity="external_id của unit cha",
                    recovery_hint="unit cha phải upsert thành công TRƯỚC deal này trong cùng lần chạy",
                )

            if op == "archive":
                existing_id = self.identity.get(key)
                if existing_id is None:
                    self.summary.add(ItemResult(key, "deal", "failed", None, "archive: chưa có danh tính đã biết"))
                    continue
                resp = self.client.delete(f"/deals/{existing_id}")
                if resp.status_code == 200:
                    out[key] = existing_id
                    item = ItemResult(key, "deal", "archived", existing_id, _classify(resp), operation="archive")
                    self._track(item, kind="deal", external_id=existing_id, record=resp.json()["record"])
                    self.summary.add(item)
                else:
                    self.summary.add(ItemResult(key, "deal", "failed", existing_id, _classify(resp)))
                    self._maybe_abort(resp, f"deal '{key}' archive")
                continue

            already = existing_by_unit.get(unit_external_id)
            if already is not None:
                self.identity.set(key, "deal", already["external_id"])
                out[key] = already["external_id"]
                # `DealPatch`/`exclude_unset` giữ lịch sử cũ an toàn (router
                # docstring: "reserved → sold chỉ cần gửi {deal_status, sold_at}",
                # mốc không gửi thì GIỮ NGUYÊN) — khác PATCH của project/area,
                # đây KHÔNG phải upsert-diff toàn trường, chỉ gửi field đổi.
                patch: dict[str, Any] = {}
                if self.refresh_existing:
                    patch["deal_status"] = d.get("deal_status")
                    for ts_field in ("reserved_at", "sold_at", "lost_at"):
                        if d.get(ts_field):
                            patch[ts_field] = d[ts_field]
                else:
                    if already.get("deal_status") != d.get("deal_status"):
                        patch["deal_status"] = d.get("deal_status")
                    for ts_field in ("reserved_at", "sold_at", "lost_at"):
                        if d.get(ts_field) and not already.get(ts_field):
                            patch[ts_field] = d[ts_field]
                if patch:
                    r2 = self.client.patch(f"/deals/{already['external_id']}", json=patch)
                    if r2.status_code == 200:
                        item = ItemResult(key, "deal", "updated", already["external_id"], _classify(r2))
                        self._track(
                            item, kind="deal", external_id=already["external_id"], record=r2.json()["record"]
                        )
                        self.summary.add(item)
                    else:
                        self.summary.add(ItemResult(key, "deal", "failed", already["external_id"], _classify(r2)))
                        self._maybe_abort(r2, f"deal '{key}' update")
                else:
                    self.summary.add(
                        ItemResult(
                            key,
                            "deal",
                            "unchanged",
                            already["external_id"],
                            "đã khớp trạng thái/mốc",
                            expected_revision=already.get("source_revision"),
                        )
                    )
                continue

            try:
                self.wait_for_unit_mirror(unit_external_id, fixture_key=key)
            except SeedError as exc:
                self.summary.add(ItemResult(key, "deal", "failed", None, str(exc)))
                continue

            payload: dict[str, Any] = {"external_unit_id": unit_external_id, "deal_status": d.get("deal_status")}
            for ts_field in ("reserved_at", "sold_at", "lost_at"):
                if d.get(ts_field):
                    payload[ts_field] = d[ts_field]

            resp = self.client.post("/deals", json=payload)
            if resp.status_code == 201:
                record = resp.json()["record"]
                self.identity.set(key, "deal", record["external_id"])
                out[key] = record["external_id"]
                existing_by_unit[unit_external_id] = record
                item = ItemResult(key, "deal", "created", record["external_id"], _classify(resp))
                self._track(item, kind="deal", external_id=record["external_id"], record=record)
                self.summary.add(item)
            else:
                code = error_code_of(resp)
                label = _classify(resp)
                if code == "UNIT_NOT_MIRRORED":
                    # KHÔNG BAO GIỜ log như một skip lành tính (Fix #3) — dù
                    # `wait_for_unit_mirror` vừa xác nhận mirrored, một khoảng
                    # đua cực hiếm (unit bị archive giữa hai bước) vẫn có thể
                    # tạo ra đúng lỗi này; báo cáo như một THẤT BẠI thật.
                    self.summary.add(ItemResult(key, "deal", "failed", None, f"{label} (SAU khi đã chờ mirror)"))
                else:
                    self.summary.add(ItemResult(key, "deal", "failed", None, label))
                self._maybe_abort(resp, f"deal '{key}' create")
        return out


class BackendVerifier:
    """Xác minh chiếu Backend qua ĐÚNG endpoint đọc công khai đã có
    (`src/api/dashboard.py`, `src/api/inventory.py`) — KHÔNG truy vấn database
    trực tiếp để đi tắt (dùng API thật, không phải bằng chứng riêng).

    Khớp theo DANH TÍNH NGUỒN (`external_id`/`external_unit_id`/
    `external_deal_id` dưới `source_instance_id` của Mini CRM), không bao giờ
    theo tên. Và không chỉ khớp SỰ TỒN TẠI: một dòng cùng `external_id` có thể
    đã nằm sẵn từ lần chạy TRƯỚC, nên mỗi phép xác minh còn đòi bằng chứng rằng
    dòng đó đã tới ÍT NHẤT phiên bản (hoặc mang đúng trường nghiệp vụ) của lần
    ghi này.

    Tầng bị giới hạn: `AreaOut` (`src/models/schemas.py`) KHÔNG phơi `status` hay
    `deleted_at`, dù `DomainProjector._archive_area` CÓ đặt `areas.status =
    'archived'`. Không có đường đọc công khai nào cho trạng thái tombstone của
    phân khu ⇒ báo `TOMBSTONE_VERIFICATION_UNAVAILABLE`, không báo PASS giả.
    """

    def __init__(self, client: httpx.Client, *, timeout: float, interval: float) -> None:
        self.client = client
        self.timeout = timeout
        self.interval = interval

    def _poll(self, fetch, timeout: float | None = None) -> Any | None:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            found = fetch()
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.interval)

    @staticmethod
    def _revision_ok(row: dict, expected: int | None) -> bool:
        """`>=` chứ không `==`: một mutation KHÁC trên cùng thực thể (hoặc một
        lần chạy sau) có thể đã đẩy phiên bản lên cao hơn. Điều cần chứng minh là
        Backend đã tới ÍT NHẤT phiên bản của lần ghi này, không phải đứng yên ở
        đúng nó."""
        if expected is None:
            return True
        actual = row.get("source_revision")
        return actual is not None and actual >= expected

    # --- Project -------------------------------------------------------------

    def project(self, item: ItemResult) -> tuple[str, str]:
        """`GET /api/v1/projects/{external_id}` (Phase D — đọc TRỰC TIẾP theo
        danh tính Mini CRM, khác `GET /api/v1/projects` là danh sách rút gọn
        không phơi `status`). `ProjectDetail` CÓ `status`, nên — khác Area —
        tombstone dự án xác minh được trọn vẹn qua đường đọc công khai."""
        eid = item.external_id

        def row() -> dict | None:
            resp = self.client.get(f"/api/v1/projects/{eid}")
            if resp.status_code != 200:
                return None
            return resp.json()

        if item.operation == "archive":
            found = self._poll(lambda: (r if (r := row()) and r.get("status") == "archived" else None))
            if found is not None:
                return "TOMBSTONE_CONFIRMED", "status=archived"
            last = row()
            return "PROJECTION_UNCONFIRMED", f"status={(last or {}).get('status')!r}, cần 'archived'"

        found = self._poll(lambda: (r if (r := row()) and self._revision_ok(r, item.expected_revision) else None))
        if found is not None:
            return "PROJECTION_CONFIRMED", f"source_revision={found.get('source_revision')}"
        last = row()
        if last is None:
            return "PROJECTION_UNCONFIRMED", "không thấy dòng nào mang external_id này"
        return (
            "PROJECTION_UNCONFIRMED",
            f"source_revision={last.get('source_revision')} < cần {item.expected_revision}",
        )

    # --- Area ----------------------------------------------------------------

    def area(self, item: ItemResult, external_project_id: str) -> tuple[str, str]:
        eid = item.external_id

        def row() -> dict | None:
            rows = self.client.get(
                "/api/v1/areas", params={"external_project_id": external_project_id}
            ).json()
            return next((r for r in rows if r.get("external_id") == eid), None)

        if item.operation == "archive":
            present = self._poll(row)
            if present is None:
                return "PROJECTION_UNCONFIRMED", "không thấy dòng nào mang external_id này"
            return (
                "TOMBSTONE_VERIFICATION_UNAVAILABLE",
                "AreaOut không phơi 'status'/'deleted_at' — không có đường đọc công khai nào "
                "cho trạng thái tombstone của phân khu (DomainProjector._archive_area CÓ đặt "
                "areas.status='archived', nhưng src/api/dashboard.py không trả trường đó)",
            )

        found = self._poll(lambda: (r if (r := row()) and self._revision_ok(r, item.expected_revision) else None))
        if found is not None:
            return "PROJECTION_CONFIRMED", f"source_revision={found.get('source_revision')}"
        last = row()
        if last is None:
            return "PROJECTION_UNCONFIRMED", "không thấy dòng nào mang external_id này"
        return (
            "PROJECTION_UNCONFIRMED",
            f"source_revision={last.get('source_revision')} < cần {item.expected_revision}",
        )

    # --- Unit ----------------------------------------------------------------

    def unit(self, item: ItemResult, external_project_id: str, external_area_id: str) -> tuple[str, str]:
        eid = item.external_id

        def row(include_deleted: bool) -> dict | None:
            body = self.client.get(
                "/api/v1/inventory",
                params={
                    "external_project_id": external_project_id,
                    "external_area_id": external_area_id,
                    "include_units": "true",
                    "include_deleted": "true" if include_deleted else "false",
                },
            ).json()
            return next((u for u in body.get("units", []) if u.get("external_unit_id") == eid), None)

        if item.operation == "archive":
            # `include_deleted=true` + `InventoryUnitOut.deleted_at` — tombstone
            # căn xác minh được trọn vẹn.
            found = self._poll(lambda: (r if (r := row(True)) and r.get("deleted_at") else None))
            if found is not None:
                return "TOMBSTONE_CONFIRMED", f"deleted_at={found.get('deleted_at')}"
            last = row(True)
            if last is None:
                return "PROJECTION_UNCONFIRMED", "không thấy dòng nào kể cả khi include_deleted=true"
            return "PROJECTION_UNCONFIRMED", "dòng còn sống (deleted_at=None), chưa tombstone"

        # `InventoryUnitOut` KHÔNG có `source_revision` — xác minh bằng TRƯỜNG
        # NGHIỆP VỤ vừa ghi thay vì bằng phiên bản.
        field_name, expected_value = item.expected_field or ("status", None)

        def matches() -> dict | None:
            r = row(False)
            if r is None:
                return None
            if expected_value is not None and r.get(field_name) != expected_value:
                return None
            return r

        found = self._poll(matches)
        if found is not None:
            return "PROJECTION_CONFIRMED", f"{field_name}={found.get(field_name)!r}"
        last = row(False)
        if last is None:
            return "PROJECTION_UNCONFIRMED", "không thấy dòng nào mang external_unit_id này"
        return (
            "PROJECTION_UNCONFIRMED",
            f"{field_name}={last.get(field_name)!r}, cần {expected_value!r}",
        )

    # --- Deal ----------------------------------------------------------------

    def deal(self, item: ItemResult, external_project_id: str) -> tuple[str, str]:
        eid = item.external_id

        def row(include_deleted: bool) -> dict | None:
            body = self.client.get(
                "/api/v1/deals",
                params={
                    "external_project_id": external_project_id,
                    "include_deleted": "true" if include_deleted else "false",
                },
            ).json()
            return next((it for it in body.get("items", []) if it.get("external_deal_id") == eid), None)

        if item.operation == "archive":
            found = self._poll(lambda: (r if (r := row(True)) and r.get("deleted_at") else None))
            if found is not None:
                return "TOMBSTONE_CONFIRMED", f"deleted_at={found.get('deleted_at')}"
            last = row(True)
            if last is None:
                return "PROJECTION_UNCONFIRMED", "không thấy dòng nào kể cả khi include_deleted=true"
            return "PROJECTION_UNCONFIRMED", "dòng còn sống (deleted_at=None), chưa tombstone"

        found = self._poll(
            lambda: (r if (r := row(False)) and self._revision_ok(r, item.expected_revision) else None)
        )
        if found is not None:
            return "PROJECTION_CONFIRMED", f"source_revision={found.get('source_revision')}"
        last = row(False)
        if last is None:
            return "PROJECTION_UNCONFIRMED", "không thấy dòng nào mang external_deal_id này"
        return (
            "PROJECTION_UNCONFIRMED",
            f"source_revision={last.get('source_revision')} < cần {item.expected_revision}",
        )


def _resolve_parents(data: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """`fixture_key -> {project_key, area_key}` cho MỌI tầng, giải một lần.

    Phase D cần `external_project_id` (và `external_area_id` cho unit) để gọi
    đúng endpoint đọc; suy chúng ngay tại chỗ bốn lần như trước khiến cùng một
    phép tra được viết lại bốn kiểu hơi khác nhau.
    """
    out: dict[str, dict[str, str | None]] = {}
    area_by_key = {a["external_key"]: a for a in data.get("areas", [])}
    unit_by_key = {u["external_key"]: u for u in data.get("units", [])}

    for p in data.get("projects", []):
        out[p["external_key"]] = {"project_key": p["external_key"], "area_key": None}
    for a in data.get("areas", []):
        out[a["external_key"]] = {"project_key": a.get("project_external_key"), "area_key": a["external_key"]}
    for u in data.get("units", []):
        area = area_by_key.get(u.get("area_external_key"))
        out[u["external_key"]] = {
            "project_key": area.get("project_external_key") if area else None,
            "area_key": u.get("area_external_key"),
        }
    for d in data.get("deals", []):
        unit = unit_by_key.get(d.get("unit_external_key"))
        area = area_by_key.get(unit.get("area_external_key")) if unit else None
        out[d["external_key"]] = {
            "project_key": area.get("project_external_key") if area else None,
            "area_key": unit.get("area_external_key") if unit else None,
        }
    return out


def _await_delivery(tracker: OutboxTracker, item: ItemResult, *, timeout: float, interval: float) -> None:
    """Phase C: chốt trạng thái GIAO HÀNG của CHÍNH các lô mà mutation này sinh ra.

    Đây là chốt chặn ngăn "một dòng chiếu từ lần chạy TRƯỚC làm cho một lần ghi
    HỎNG của lần chạy NÀY trông như thành công": nếu lô của lần ghi này chưa được
    Backend nhận, `item.delivery` KHÔNG phải `DELIVERY_ACCEPTED`, và Phase D sẽ
    không thăm dò chiếu nữa.

    Lô v1 chết có chủ đích (`EXPECTED_DEAD_V1_*`) được ghi nhận nhưng KHÔNG được
    phép quyết định kết quả — nếu nó quyết định, mọi mutation unit/deal sẽ vĩnh
    viễn đỏ và cổng CI thành vô dụng, đúng lỗi đang được sửa.
    """
    if not item.mutated():
        item.delivery = "NO_MUTATION"
        return
    if not item.batches:
        # Mutation thành công nhưng KHÔNG tìm thấy lô nào — không được coi là
        # thành công. Hoặc outbox không ghi, hoặc `discover()` bỏ sót.
        item.delivery = "OUTBOX_PENDING"
        item.detail = (item.detail + " | không tìm thấy lô outbox nào cho mutation này").strip(" |")
        return

    states: list[str] = []
    for batch_id in item.batches:
        row = tracker.wait_for_delivery(batch_id, timeout=timeout, interval=interval)
        if row.is_expected_dead_v1:
            continue  # lane v1 chết có chủ đích — ghi nhận, không quyết định
        states.append(row.delivery_state())

    if not states:
        # Chỉ có lô v1 chết có chủ đích và không có lô v2 nào ⇒ không chứng minh
        # được gì về đường v2. Không nâng thành ACCEPTED.
        item.delivery = "OUTBOX_PENDING"
        return
    for blocking in ("DEAD_LETTER", "DELIVERY_FAILED_RETRYABLE", "OUTBOX_PENDING"):
        if blocking in states:
            item.delivery = blocking
            return
    item.delivery = "DELIVERY_ACCEPTED"


def main() -> int:
    _load_local_env()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("MINICRM_URL", DEFAULT_MINICRM_URL))
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL))
    parser.add_argument("--mirror-timeout", type=float, default=DEFAULT_MIRROR_TIMEOUT)
    parser.add_argument("--mirror-interval", type=float, default=DEFAULT_MIRROR_INTERVAL)
    parser.add_argument("--delivery-timeout", type=float, default=DEFAULT_DELIVERY_TIMEOUT)
    parser.add_argument("--delivery-interval", type=float, default=DEFAULT_DELIVERY_INTERVAL)
    parser.add_argument("--projection-timeout", type=float, default=DEFAULT_PROJECTION_TIMEOUT)
    parser.add_argument("--projection-interval", type=float, default=DEFAULT_PROJECTION_INTERVAL)
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="re-emit unchanged fixture records through Mini CRM PATCH so a cleared destination can be rebuilt",
    )
    parser.add_argument("--skip-verify", action="store_true", help="Bỏ qua Phase D (xác minh chiếu Backend)")
    parser.add_argument(
        "--fixture",
        default=None,
        help=f"Đường dẫn fixture thay cho mặc định ({SEED_FILE.relative_to(REPO_ROOT)}) — "
        "vd. một fixture riêng cho một lần seed bổ sung, không đụng fixture legacy.",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help=f"Đường dẫn state file thay cho mặc định ({STATE_FILE.relative_to(REPO_ROOT)}).",
    )
    args = parser.parse_args()

    seed_file = Path(args.fixture) if args.fixture else SEED_FILE
    state_file = Path(args.state_file) if args.state_file else STATE_FILE

    admin_token = os.getenv(ADMIN_TOKEN_ENV, "")
    backend_token = os.getenv(BACKEND_TOKEN_ENV, "")

    try:
        preflight(
            minicrm_url=args.base_url,
            backend_url=args.backend_url,
            admin_token=admin_token,
            backend_token=backend_token,
            require_backend_token=not args.skip_verify,
        )
    except SeedError as exc:
        print(f"LỖI (preflight): {exc}", file=sys.stderr)
        return 1

    identity = IdentityMap(state_file)

    try:
        data = _load_seed(identity, seed_file)
    except SeedError as exc:
        print(f"LỖI (kiểm cục bộ — CHƯA GHI GÌ): {exc}", file=sys.stderr)
        return 1

    with httpx.Client(
        base_url=args.base_url, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30.0
    ) as minicrm_client:
        # RANH GIỚI LẦN CHẠY — chụp TRƯỚC mutation đầu tiên. Mọi batch id không có
        # trong ảnh chụp này là của lần chạy NÀY.
        tracker = OutboxTracker(minicrm_client)
        tracker.snapshot()
        historical = tracker.historical_dead_letters()
        print(f"Ranh giới lần chạy: {tracker.baseline_size()} lô outbox đã tồn tại từ trước.\n")

        seeder = MiniCrmSeeder(
            minicrm_client,
            identity,
            tracker,
            mirror_timeout=args.mirror_timeout,
            mirror_interval=args.mirror_interval,
            refresh_existing=args.refresh_existing,
        )

        project_ids = seeder.seed_projects(data["projects"])
        area_ids: dict[str, str] = {}
        area_info: dict[str, dict[str, str]] = {}
        unit_ids: dict[str, str] = {}
        deal_ids: dict[str, str] = {}

        if not seeder.summary.aborted:
            area_ids = seeder.seed_areas(data["areas"], project_ids)
            for a in data["areas"]:
                aid = area_ids.get(a["external_key"])
                if aid:
                    area_info[a["external_key"]] = {"external_id": aid, "area_name": a["area_name"]}

        if not seeder.summary.aborted and data.get("units"):
            unit_ids = seeder.seed_units(data["units"], area_info)

        if not seeder.summary.aborted and data.get("deals"):
            deal_ids = seeder.seed_deals(data["deals"], unit_ids)

        print("\n=== Phase B — Mini CRM CRUD, kết quả từng mục ===")
        for r in seeder.summary.results:
            print(f"[{r.kind:7s}] {r.fixture_key:24s} {r.status:10s} {r.external_id or '':10s} {r.detail}")

        if seeder.summary.aborted:
            print(f"\nDỪNG TOÀN BỘ LÔ: {seeder.summary.abort_reason}", file=sys.stderr)
            return 1

        # --- Phase C: giao hàng của CHÍNH lô lần chạy này --------------------
        print("\n=== Phase C — giao hàng outbox (chờ ĐÚNG lô của lần chạy này) ===")
        for r in seeder.summary.results:
            if r.status in ("failed", "dependency_error"):
                continue
            _await_delivery(tracker, r, timeout=args.delivery_timeout, interval=args.delivery_interval)
            batches = ", ".join(r.batches) if r.batches else "(không có lô — không mutate)"
            print(f"  {r.kind:7s} {r.fixture_key:24s} {r.delivery:26s} {batches}")

        blocking_dead = tracker.blocking_dead_letters()
        expected_dead = tracker.expected_dead_v1()

    # --- Báo cáo tử thư, TÁCH BA ------------------------------------------
    print(f"\n=== Dead letters — lần chạy này: {len(blocking_dead)} (chặn) ===")
    for row in blocking_dead:
        print(
            f"  entity={row.entity:10s} batch={row.external_batch_id} http={row.http_status} "
            f"code={row.error_code} -> {resend_guidance(row, args.base_url)}"
        )
    if not blocking_dead:
        print("  (không có)")

    print(f"\n=== Dead letters — lane v1 chết có chủ đích, lần chạy này: {len(expected_dead)} (KHÔNG chặn) ===")
    for row in expected_dead:
        print(
            f"  entity={row.entity:10s} batch={row.external_batch_id} http={row.http_status} "
            f"code={row.error_code} -> {resend_guidance(row, args.base_url)}"
        )
    if not expected_dead:
        print("  (không có)")

    print(f"\n=== Historical dead letters: {len(historical)} (thông tin, KHÔNG chặn) ===")
    if historical:
        by_entity: dict[str, int] = {}
        for row in historical:
            by_entity[f"{row.entity}/{row.http_status}"] = by_entity.get(f"{row.entity}/{row.http_status}", 0) + 1
        for label, count in sorted(by_entity.items()):
            print(f"  {label:20s} x{count}")
        print("  (có sẵn TRƯỚC lần chạy này — không xoá, không sửa; xem GET /outbox để tra từng lô)")
    else:
        print("  (không có)")

    failures = seeder.summary.failures()
    if failures:
        print(f"\n{len(failures)} mục THẤT BẠI ở Mini CRM — không tiếp tục xác minh Backend cho các mục đó.")

    if args.skip_verify:
        print("\n--skip-verify: bỏ qua Phase D (xác minh chiếu Backend).")
        undelivered = [r for r in seeder.summary.results if r.delivery in BLOCKING_DELIVERY_STATES]
        return 1 if (failures or blocking_dead or undelivered) else 0

    print("\n=== Phase D — xác minh chiếu Backend (theo source identity, không theo tên) ===")
    parents = _resolve_parents(data)
    unconfirmed: list[str] = []
    undelivered: list[str] = []
    unavailable: list[str] = []

    def _eid_for(fixture_key: str | None) -> str | None:
        if fixture_key is None:
            return None
        return (
            project_ids.get(fixture_key)
            or area_ids.get(fixture_key)
            or unit_ids.get(fixture_key)
            or deal_ids.get(fixture_key)
            or identity.get(fixture_key)
        )

    with httpx.Client(
        base_url=args.backend_url, headers={"Authorization": f"Bearer {backend_token}"}, timeout=30.0
    ) as backend_client:
        verifier = BackendVerifier(backend_client, timeout=args.projection_timeout, interval=args.projection_interval)

        for r in seeder.summary.results:
            if r.status in ("failed", "dependency_error") or r.external_id is None:
                continue

            # Chốt chặn: chưa chứng minh được lô của LẦN CHẠY NÀY đã tới nơi thì
            # KHÔNG thăm dò chiếu. Một dòng chiếu cũ không được phép trở thành
            # bằng chứng cho một lần ghi mới.
            if r.delivery in BLOCKING_DELIVERY_STATES:
                r.state = r.delivery
                undelivered.append(f"{r.kind}:{r.fixture_key}")
                print(f"  {r.kind:7s} {r.fixture_key:22s} {r.external_id:8s} -> {r.state} (BỎ QUA thăm dò chiếu)")
                continue

            parent = parents.get(r.fixture_key, {})
            proj_eid = _eid_for(parent.get("project_key"))
            area_eid = _eid_for(parent.get("area_key"))

            if r.kind == "project":
                state, detail = verifier.project(r)
            elif r.kind == "area":
                if proj_eid is None:
                    continue
                state, detail = verifier.area(r, proj_eid)
            elif r.kind == "unit":
                if proj_eid is None or area_eid is None:
                    continue
                state, detail = verifier.unit(r, proj_eid, area_eid)
            elif r.kind == "deal":
                if proj_eid is None:
                    continue
                state, detail = verifier.deal(r, proj_eid)
            else:
                continue

            r.state = state
            print(f"  {r.kind:7s} {r.fixture_key:22s} {r.external_id:8s} -> {state} ({detail})")
            if state == "PROJECTION_UNCONFIRMED":
                unconfirmed.append(f"{r.kind}:{r.fixture_key}")
            elif state == "TOMBSTONE_VERIFICATION_UNAVAILABLE":
                unavailable.append(f"{r.kind}:{r.fixture_key}")

    print("\n=== Tổng kết ===")
    print(f"Mini CRM thất bại                     : {len(failures)}")
    print(f"Current-run dead letters (chặn)       : {len(blocking_dead)}")
    print(f"Current-run dead letters (v1 dự kiến) : {len(expected_dead)}")
    print(f"Historical dead letters               : {len(historical)}")
    print(f"Chưa giao được lô của lần chạy này    : {len(undelivered)} {undelivered if undelivered else ''}")
    print(f"Chưa xác minh chiếu Backend           : {len(unconfirmed)} {unconfirmed if unconfirmed else ''}")
    print(f"Không có đường đọc để xác minh        : {len(unavailable)} {unavailable if unavailable else ''}")

    if failures or blocking_dead or undelivered or unconfirmed:
        return 1
    print("\nOK — mọi tầng đã ghi qua Mini CRM, lô của LẦN CHẠY NÀY đã được Backend nhận, và chiếu đã xác nhận.")
    if unavailable:
        print(
            f"Lưu ý: {len(unavailable)} mục không có đường đọc công khai để xác minh tombstone "
            "— báo TOMBSTONE_VERIFICATION_UNAVAILABLE, KHÔNG báo PASS giả."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
