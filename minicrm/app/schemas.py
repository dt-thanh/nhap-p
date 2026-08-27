"""Model request/response của Mini CRM.

Hai nguyên tắc chạy suốt file này:

**1. PATCH phân biệt "không gửi" với "gửi null".** Mọi trường của model PATCH đều
có mặc định `None`, nên `None` một mình không nói được người gọi muốn gì. Phía
service đọc `model_fields_set` để biết khoá nào THẬT SỰ có trong body. Không phân
biệt được hai thứ đó thì `PATCH {"unit_status": "sold"}` sẽ xoá luôn `unit_code`.

**2. Phản hồi của một thao tác ghi LUÔN mang cả hai nửa.** `record` là trạng thái
cục bộ đã commit; `sync` là kết quả đẩy sang backend. Trả một nửa sẽ khiến "đã lưu
nhưng chưa đồng bộ" trông y hệt "đã lưu và đã đồng bộ" — đúng thứ mà Phase 4 cấm.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Soi gương tập trạng thái của backend. Khai bằng `Literal` để FastAPI từ chối
# ngay ở tầng validate, TRƯỚC khi chạm database — một trạng thái lạ không bao giờ
# đi được xa tới chỗ ràng buộc CHECK phải nổ.
UnitStatus = Literal["available", "reserved", "sold", "blocked"]
DealStatus = Literal["lead", "qualified", "interested", "viewing", "reserved", "sold", "lost"]

def _finite_or_none(value: float | None) -> float | None:
    """Chặn `NaN`/`Infinity` — JSON không có ký hiệu chuẩn cho chúng, nhưng bộ mã
    hoá của Python thì phát ra được, nên tầng validate phải tự canh (0008).
    `Field(gt=0)` đã chặn NaN (mọi so sánh với NaN đều `False`) nhưng KHÔNG chặn
    `Infinity` (`inf > 0` là `True`)."""
    if value is not None and not math.isfinite(value):
        raise ValueError("listing_price phải là một số hữu hạn (không NaN, không Infinity)")
    return value


SYNTHETIC_DISCLAIMER = (
    "Mini CRM TỔNG HỢP do chính dự án này viết. KHÔNG phải CRM của khách hàng, "
    "và không chứng minh khả năng tương thích với bất kỳ hệ nguồn thật nào."
)


class HealthOut(BaseModel):
    status: str = Field(..., description="ok khi ứng dụng chạy được")
    app: str
    source_instance_id: str = Field(..., description="Danh tính mà backend sẽ thấy")
    disclaimer: str = Field(default=SYNTHETIC_DISCLAIMER)


class PushOut(BaseModel):
    batch_id: str
    status_code: int
    accepted: bool
    replayed: bool
    sync_run_id: str | None = None
    body: dict[str, Any] = Field(default_factory=dict)


# --- Kết quả đồng bộ --------------------------------------------------------


class SyncOut(BaseModel):
    """Kết quả đẩy lô đi kèm một thao tác CRUD.

    `status` là thứ người gọi phải đọc:

        synced        backend nhận và xử lý (HTTP 202)
        replayed      backend đã xử lý lô này rồi, trả kết quả cũ (HTTP 200)
        sync_failed   backend ĐÃ trả lời và TỪ CHỐI — sửa dữ liệu rồi gửi lại
        sync_pending  lô đã commit vào outbox nhưng RelayLoop chưa giao xong, hoặc
                      không biết backend đã nhận hay chưa (timeout/không nối được)

    `sync_failed` và `sync_pending` tách nhau vì hai bên cần hai hành động khác
    hẳn. Gộp lại thành một chữ "lỗi" sẽ khiến người vận hành gửi lại một lô có thể
    đã tới nơi, hoặc ngồi chờ một lô chắc chắn sẽ không bao giờ tới.
    """

    status: Literal["synced", "replayed", "sync_failed", "sync_pending"]
    external_batch_id: str
    http_status: int | None = None
    sync_run_id: str | None = None
    decisions: dict[str, int] = Field(default_factory=dict)
    projections: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    detail: Any = None


# --- Dự án -------------------------------------------------------------------


class ProjectOut(BaseModel):
    external_id: str
    name: str
    location: str | None = None
    launch_date: date
    status: Literal["active", "archived"]
    source_revision: int
    created_at: datetime
    updated_at: datetime
    mirrored_at: datetime | None = None
    mirrored_revision: int | None = None
    last_sync_batch_id: str | None = None


class ProjectCreate(BaseModel):
    # `extra="forbid"`: Phase B (B1) đòi "unknown-field rejection" tường minh —
    # gửi một cột BACKEND-LOCAL như `absorption_calculator` phải bị TỪ CHỐI, không
    # bị âm thầm bỏ qua (mặc định của Pydantic là "ignore"). Không áp dụng ngược
    # cho `UnitCreate`/`DealCreate` (Phase 3/4, quy ước cũ) — đây là điểm khác
    # biệt CÓ CHỦ ĐÍCH của Phase B, không phải một chỉnh sửa lan tràn.
    model_config = ConfigDict(extra="forbid")

    # `external_id` KHÔNG có ở đây — cùng lý do với Unit/Deal: do dãy của
    # database cấp, không bao giờ do người gọi tự đặt.
    name: str = Field(..., min_length=1, max_length=255)
    location: str | None = Field(default=None, min_length=1, max_length=512)
    launch_date: date

    @field_validator("location")
    @classmethod
    def _location_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("location không được để trống")
        return value


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    location: str | None = Field(default=None, min_length=1, max_length=512)
    launch_date: date | None = None

    @field_validator("location")
    @classmethod
    def _location_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("location không được để trống")
        return value


class ProjectWriteOut(BaseModel):
    """CHỈ `record` — KHÔNG có `sync`.

    Khác `UnitWriteOut`/`DealWriteOut` ở đúng điểm này: Phase B KHÔNG tạo dòng
    outbox cho Project/Area (đó là việc của Phase C), nên không có kết quả đẩy
    nào để báo cáo. Bịa một `SyncOut` rỗng sẽ nói dối rằng có một lần đẩy đã xảy
    ra. `record` nằm trong một khoá thay vì trả trần để Phase C thêm `sync` sau
    này mà không phải đổi hình dạng bao ngoài.
    """

    record: ProjectOut


# --- Phân khu ------------------------------------------------------------


class AreaOut(BaseModel):
    external_id: str
    external_project_id: str
    area_name: str
    unit_type: str
    bedrooms: int
    area_sqm: float
    total_units: int
    status: Literal["active", "archived"]
    source_revision: int
    created_at: datetime
    updated_at: datetime
    mirrored_at: datetime | None = None
    mirrored_revision: int | None = None
    last_sync_batch_id: str | None = None


class AreaCreate(BaseModel):
    # `extra="forbid"` — cùng lý do với `ProjectCreate`. Gửi `external_project_id`
    # kèm một trường LẠ (vd. đánh máy nhầm) phải lộ ra ngay, không âm thầm rơi mất.
    model_config = ConfigDict(extra="forbid")

    # Tham chiếu ỔN ĐỊNH tới dự án cha — external_id, KHÔNG phải tên dự án
    # (phase_a_domain_freeze.md §A3.7: `project_name` không bao giờ là một tham
    # chiếu — `projects.name` không có ràng buộc duy nhất).
    external_project_id: str = Field(..., min_length=1, max_length=128)
    area_name: str = Field(..., min_length=1, max_length=128)
    unit_type: str = Field(..., min_length=1, max_length=64)
    bedrooms: int = Field(..., ge=0)
    area_sqm: float = Field(..., gt=0)
    total_units: int = Field(..., ge=0)


class AreaPatch(BaseModel):
    # `extra="forbid"`: đây là cơ chế THỰC SỰ khiến `external_project_id` bị từ
    # chối khi ai đó cố PATCH nó — KHÔNG khai trường này ở đây là chưa đủ, vì mặc
    # định Pydantic chỉ ÂM THẦM BỎ QUA trường lạ (không báo lỗi). `forbid` biến sự
    # vắng mặt thành một ràng buộc THI HÀNH ĐƯỢC, không chỉ là ý định.
    model_config = ConfigDict(extra="forbid")

    # KHÔNG có `external_project_id`: bất biến bằng cách VẮNG MẶT, cùng khuôn với
    # `DealPatch` không cho sửa `external_unit_id`. Area không được chuyển dự án
    # (phase_a_domain_freeze.md §A1.6) — cách hợp lệ là archive + tạo mới.
    area_name: str | None = Field(default=None, min_length=1, max_length=128)
    unit_type: str | None = Field(default=None, min_length=1, max_length=64)
    bedrooms: int | None = Field(default=None, ge=0)
    area_sqm: float | None = Field(default=None, gt=0)
    total_units: int | None = Field(default=None, ge=0)


class AreaWriteOut(BaseModel):
    """CHỈ `record` — cùng lý do với `ProjectWriteOut`."""

    record: AreaOut


# --- Căn hộ -----------------------------------------------------------------


class UnitOut(BaseModel):
    external_id: str
    area_name: str
    unit_type: str
    unit_code: str
    unit_status: str
    # Giá niêm yết/chính thức (0008). `None` = chưa biết giá — KHÔNG phải giá
    # giao dịch thực (chưa có trường đó, xem docstring migration 0008).
    listing_price: float | None = None
    source_revision: int
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    mirrored_at: datetime | None = None
    mirrored_revision: int | None = None
    last_sync_batch_id: str | None = None

    @property
    def in_sync(self) -> bool:
        return self.mirrored_revision == self.source_revision


class UnitCreate(BaseModel):
    # `external_id` KHÔNG có ở đây, và đó là chủ đích: nó do dãy của database cấp.
    # Cho người gọi tự đặt id nghĩa là cho họ dùng lại một id đã tombstone — phá
    # vỡ giả định A1 của hợp đồng bằng đúng một request.
    #
    # HAI CÁCH tham chiếu phân khu, dùng ĐÚNG MỘT trong hai — Phase B (hardening):
    #   (a) `external_area_id`  — tham chiếu ỔN ĐỊNH tới một `crm_areas` cục bộ.
    #       KHUYẾN NGHỊ, và là đường DUY NHẤT cưỡng chế được "cùng dự án".
    #   (b) `area_name` + `unit_type` — khoá tự nhiên KẾ THỪA từ trước Phase B.
    #       Mini CRM tự tra một phân khu CÒN SỐNG khớp đúng cặp này; khớp đúng
    #       MỘT thì dùng, khớp KHÔNG hoặc khớp NHIỀU thì bị từ chối (không đoán).
    # Giữ cả hai đường để KHÔNG phá vỡ mọi lời gọi hiện có (test, script,
    # container thật) đang dùng hình dạng (a); nhưng cả hai đường đều đi qua
    # CÙNG một phép tra Area — không có đường nào bỏ qua việc kiểm phân khu tồn
    # tại. Xem `app.crud._resolve_area_reference`.
    external_area_id: str | None = Field(default=None, min_length=1, max_length=128)
    area_name: str | None = Field(default=None, min_length=1, max_length=128)
    unit_type: str | None = Field(default=None, min_length=1, max_length=64)
    unit_code: str = Field(..., min_length=1, max_length=64)
    unit_status: UnitStatus = "available"
    # Tuỳ chọn — Mini CRM không bắt buộc biết giá của mọi căn ngay lúc tạo.
    # `> 0`: giá bằng 0 không phải một giá thật (khớp CHECK ở DB, 0008).
    listing_price: float | None = Field(default=None, gt=0)

    @field_validator("listing_price")
    @classmethod
    def _listing_price_is_finite(cls, value: float | None) -> float | None:
        return _finite_or_none(value)

    @model_validator(mode="after")
    def _exactly_one_area_reference(self) -> UnitCreate:
        by_id = self.external_area_id is not None
        by_name = self.area_name is not None or self.unit_type is not None
        if by_id and by_name:
            raise ValueError(
                "Chỉ dùng MỘT cách tham chiếu phân khu: `external_area_id` HOẶC "
                "(`area_name` + `unit_type`), không cả hai."
            )
        if not by_id and not (self.area_name and self.unit_type):
            raise ValueError(
                "Thiếu tham chiếu phân khu: cung cấp `external_area_id`, hoặc CẢ HAI "
                "`area_name` và `unit_type`."
            )
        return self


class UnitPatch(BaseModel):
    # Cùng quy tắc "một trong hai" của `UnitCreate`, nhưng LỎNG hơn vì đây là
    # PATCH: không cung cấp gì cả là hợp lệ (không đổi phân khu). Cung cấp MỘT
    # PHẦN của cặp (area_name) hoặc (unit_type) mà không có phần còn lại thì bị
    # từ chối — cặp đó định danh MỘT phân khu, không tách rời được.
    external_area_id: str | None = Field(default=None, min_length=1, max_length=128)
    area_name: str | None = Field(default=None, min_length=1, max_length=128)
    unit_type: str | None = Field(default=None, min_length=1, max_length=64)
    unit_code: str | None = Field(default=None, min_length=1, max_length=64)
    unit_status: UnitStatus | None = None
    # Vắng mặt = GIỮ NGUYÊN giá đang có (loại khỏi patch bởi `exclude_unset` ở
    # router). `null` TƯỜNG MINH = XOÁ giá đang có (patch mang khoá này với giá
    # trị `None`, `.values(listing_price=None, ...)` ghi NULL). Một số > 0 = đặt
    # giá mới. Xem `routers/units.py::update_unit` và migration 0008.
    listing_price: float | None = Field(default=None, gt=0)

    @field_validator("listing_price")
    @classmethod
    def _listing_price_is_finite(cls, value: float | None) -> float | None:
        return _finite_or_none(value)

    @model_validator(mode="after")
    def _at_most_one_area_reference(self) -> UnitPatch:
        by_id = self.external_area_id is not None
        by_name_partial = self.area_name is not None or self.unit_type is not None
        if by_id and by_name_partial:
            raise ValueError(
                "Chỉ dùng MỘT cách tham chiếu phân khu trong một PATCH: `external_area_id` "
                "HOẶC (`area_name` + `unit_type`), không cả hai."
            )
        if by_name_partial and not (self.area_name and self.unit_type):
            raise ValueError("Đổi phân khu qua tên đòi CẢ HAI `area_name` và `unit_type` trong cùng một PATCH.")
        return self


class UnitWriteOut(BaseModel):
    record: UnitOut
    sync: SyncOut


# --- Giao dịch --------------------------------------------------------------


class DealOut(BaseModel):
    external_id: str
    external_unit_id: str
    deal_status: str
    reserved_at: datetime | None = None
    sold_at: datetime | None = None
    lost_at: datetime | None = None
    source_revision: int
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    mirrored_at: datetime | None = None
    mirrored_revision: int | None = None
    last_sync_batch_id: str | None = None


class DealCreate(BaseModel):
    external_unit_id: str = Field(..., min_length=1, max_length=128)
    deal_status: DealStatus
    reserved_at: datetime | None = None
    sold_at: datetime | None = None
    lost_at: datetime | None = None


class DealPatch(BaseModel):
    external_unit_id: str | None = Field(default=None, min_length=1, max_length=128)
    deal_status: DealStatus | None = None
    reserved_at: datetime | None = None
    sold_at: datetime | None = None
    lost_at: datetime | None = None


class DealWriteOut(BaseModel):
    record: DealOut
    sync: SyncOut


# --- Sổ gửi đi --------------------------------------------------------------


class OutboxOut(BaseModel):
    external_batch_id: str
    entity: str
    http_status: int | None = None
    sent_at: datetime | None = None
    created_at: datetime
    attempts: int
    last_error: str | None = None
    replay_of: str | None = None
    record_count: int = 0
    # `payload`/`response` chỉ có ở đường tra CHI TIẾT. Kèm chúng vào danh sách sẽ
    # biến `GET /outbox` thành một lần tải vài megabyte JSON.
    payload: dict[str, Any] | None = None
    response: Any = None


class OutboxListOut(BaseModel):
    items: list[OutboxOut]
    total: int
    limit: int
    offset: int


class ReplayStaleIn(BaseModel):
    """Chọn lô CŨ để phát lại.

    Bỏ trống thì Mini CRM tự chọn lô outbox mới nhất mà mọi bản ghi trong đó đều
    đã bị chính Mini CRM ghi đè bằng một phiên bản cao hơn — tức là lô chắc chắn
    còn CŨ so với trạng thái hiện tại.
    """

    external_batch_id: str | None = None


class ResendOut(BaseModel):
    """Kết quả một lần gửi lại hoặc phát lại bản cũ."""

    source_batch_id: str
    sent_batch_id: str
    mode: Literal["resend", "replay_stale"]
    sync: SyncOut
