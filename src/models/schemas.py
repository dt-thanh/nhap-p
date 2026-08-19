from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000, description="Tin nhắn từ user")


class ChatResponse(BaseModel):
    response: str = Field(..., description="Phản hồi từ agent")
    analysis: str = Field(default="", description="Phân tích nội bộ")
    status: str = "completed"
    usage: dict = Field(default_factory=dict)
    tool_calls: list[str] = Field(default_factory=list, description="Các tool dữ liệu đã được agent sử dụng")
    sources: list[dict] = Field(default_factory=list, description="Nguồn và thời điểm dữ liệu hỗ trợ câu trả lời")
    suggested_actions: list[str] = Field(default_factory=list)
    resolved_project_id: str | None = Field(
        default=None,
        description="external_id dự án mà agent nhận diện từ câu hỏi hoặc scope hiện tại, nếu có",
    )


class RecommendationRequest(BaseModel):
    """POST /agent/recommendations — dự án là bắt buộc, phân khu là ống kính."""

    project_id: str = Field(..., min_length=1, description="external_id của dự án")
    area_id: str | None = Field(default=None, description="external_id của phân khu, không bắt buộc")


class RecommendedAction(BaseModel):
    unit_id: str
    action: str
    reason: str


class RecommendationResponse(BaseModel):
    recommendation_id: str
    project_id: str
    area_id: str | None = None
    status: Literal["pending_approval", "approved", "rejected"]
    ranking_run_id: str
    summary: str
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    generated_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    action_type: str | None = None
    action_payload: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    confidence: float | None = None
    execution_status: Literal["not_started", "executing", "executed", "failed"] = "not_started"
    executed_by: str | None = None
    executed_at: datetime | None = None
    execution_result: dict = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    actor: str = Field(..., min_length=1, max_length=200)
    reason: str = Field(default="", max_length=2000)


class ExecutionRequest(BaseModel):
    actor: str = Field(..., min_length=1, max_length=200)
    confirmed: bool = Field(default=False, description="Xác nhận riêng cho bước thực thi sau phê duyệt")


class ExecutionResponse(BaseModel):
    recommendation_id: str
    execution_id: str
    action_type: str
    status: Literal["executed", "failed"]
    result: dict = Field(default_factory=dict)
    executed_by: str
    executed_at: datetime


class PhaseChangeRequest(BaseModel):
    direction: str = Field(pattern="^(next|previous)$")
    confirmed: bool
    actor: str = "Admin"


class PhaseAddRequest(BaseModel):
    kind: str = Field(pattern="^(booking|sale)$")
    confirmed: bool
    actor: str = "Admin"


class ScenarioRunRequest(BaseModel):
    scenario_id: str
    confirmed: bool
    actor: str = "Admin"
    intensity: int = Field(default=40, ge=5, le=100)


class ProposalGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=2000)
    actor: str = "Admin"


class ProposalDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = Field(min_length=2, max_length=500)
    confirmed: bool
    actor: str = "Admin"
    unit_ids: list[str] = Field(default_factory=list)


# Trạng thái parse theo đúng từ vựng SRS §5.2 (UploadPage polling 3 giây/lần).
ParseStatus = Literal["pending", "parsing", "done", "failed"]

# upload_files.status dùng từ vựng của ck_upload_files_status, khác SRS ở hai giá
# trị. Map ở một chỗ duy nhất để hai bên không trôi khỏi nhau.
DB_STATUS_TO_API: dict[str, ParseStatus] = {
    "pending": "pending",
    "processing": "parsing",
    "completed": "done",
    "failed": "failed",
}


class UploadAccepted(BaseModel):
    """202 trả về ngay sau khi nhận file — parse chạy nền ở worker.

    `file_id` có ngay từ đây vì API tạo bản ghi `upload_files` trước khi enqueue
    (SRS §5.2: "khởi tạo bản ghi upload_files, chạy parse nền"). Nhờ vậy client
    poll được `/files/{file_id}/status` ngay, không phải chờ worker chạy xong.
    """

    file_id: str = Field(..., description="UUID bản ghi upload_files")
    job_id: str = Field(..., description="Mã job RQ, chỉ để lưu vết/gỡ lỗi")
    filename: str = Field(..., description="Tên file người dùng đã tải lên")
    checksum: str = Field(..., description="SHA-256 của file gốc")
    size_bytes: int = Field(..., description="Dung lượng file đã nhận")
    template: str = Field(..., description="Template áp dụng: sales | inventory | areas")
    status: ParseStatus = Field(default="pending", description="Trạng thái parse")


class ParseRowError(BaseModel):
    """Một lỗi validate theo dòng/cột (khớp cột bảng `upload_errors`)."""

    row_number: int = Field(..., description="Số dòng trên file, header = 1")
    column_name: str | None = Field(default=None, description="Tên cột lỗi")
    error_code: str = Field(..., description="Mã lỗi để frontend i18n")
    message: str = Field(..., description="Mô tả lỗi cho người dùng")


class FileStatus(BaseModel):
    """Trạng thái một lần nạp, đọc từ `upload_files`.

    Đọc DB chứ không đọc job result của RQ: job result hết hạn sau 500 giây,
    còn bản ghi DB thì còn mãi.
    """

    file_id: str = Field(..., description="UUID bản ghi upload_files")
    status: ParseStatus = Field(..., description="pending | parsing | done | failed")
    rows_total: int = Field(default=0, description="rows_ok + rows_failed")
    rows_ok: int = Field(default=0, description="Số dòng đã ghi vào bảng đích")
    rows_failed: int = Field(default=0, description="Số dòng lỗi")
    error_code: str | None = Field(default=None, description="Mã lỗi mức file, nếu có")
    message: str | None = Field(default=None, description="Mô tả lỗi mức file, nếu có")


class FileErrorList(BaseModel):
    """Danh sách lỗi theo dòng — endpoint riêng để status polling không tải theo."""

    file_id: str = Field(..., description="UUID bản ghi upload_files")
    errors: list[ParseRowError] = Field(default_factory=list, description="Lỗi theo dòng")
    total: int = Field(default=0, description="Tổng số lỗi đã lưu cho file này")


TransportMode = Literal["file_upload", "api_push", "unknown"]


class FileSummary(BaseModel):
    """Một dòng trong lịch sử upload (`GET /files`).

    `filename` là NULL cho lô đồng bộ CRM (`transport_mode='api_push'`) — phong
    bì JSON không có tên file. Phase 5.5 P0: `transport_mode` PHẢI có mặt để
    frontend không còn suy "có filename hay không" thành "đây là loại lô gì"
    (F-1 trong `docs/roadmap.md`).
    """

    file_id: str = Field(..., description="UUID bản ghi upload_files")
    filename: str | None = Field(default=None, description="Tên file — NULL với lô đồng bộ CRM (api_push)")
    transport_mode: TransportMode = Field(
        ...,
        description="file_upload (tải file thật) | api_push (lô đồng bộ CRM) | unknown (giá trị lạ, không nên xảy ra)",
    )
    status: ParseStatus = Field(..., description="pending | parsing | done | failed")
    rows_ok: int = Field(..., description="Số dòng đã ghi vào bảng đích")
    rows_failed: int = Field(..., description="Số dòng lỗi")
    uploaded_at: datetime = Field(..., description="Thời điểm nhận file")
    external_batch_id: str | None = Field(
        default=None, description="Mã lô do hệ nguồn cấp — chỉ có với api_push, giúp phân biệt các lô CRM"
    )
    source_system: str | None = Field(default=None, description="Hệ nguồn — chỉ có ý nghĩa với api_push")


class FileList(BaseModel):
    """Lịch sử upload, phân trang theo `uploaded_at` giảm dần (SRS §5.2)."""

    items: list[FileSummary] = Field(default_factory=list)
    limit: int = Field(..., description="Số bản ghi tối đa mỗi trang")
    offset: int = Field(..., description="Vị trí bắt đầu")


class MePermissionsOut(BaseModel):
    """Phase E — CHỈ để FE hiển thị (chọn Project nào cho vào selector), KHÔNG
    BAO GIỜ là nguồn cưỡng chế: mọi route đọc TỰ kiểm phạm vi lại ở tầng truy vấn,
    độc lập với những gì endpoint này trả về."""

    role: str = Field(..., description="business_viewer | pipeline_operator | admin")
    project_scope: str | list[str] = Field(..., description="'ALL' hoặc danh sách external_id các dự án được cấp")


class ProjectSummary(BaseModel):
    """Một dự án — frontend cần để chọn phạm vi upload và dashboard."""

    project_id: str = Field(..., description="UUID dự án")
    name: str = Field(..., description="Tên dự án")
    launch_date: date = Field(..., description="Ngày mở bán")
    status: str = Field(..., description="pending | active | rejected | archived")
    # Trả kèm nội dung hiển thị để form sửa đổ sẵn giá trị cũ, không phải gọi
    # thêm một endpoint chi tiết chỉ để lấy hai chuỗi này.
    headline: str = Field(default="", description="Tiêu đề hiển thị")
    introduce: str = Field(default="", description="Mô tả dự án")
    cover_image_url: str | None = Field(default=None, description="Ảnh bìa, None nếu chưa có")
    # Phase D — danh tính nguồn. `None` = dự án DI SẢN, tạo TRƯỚC Phase D bởi
    # đường ghi cũ (đã bị thu hẹp) — xem `src/services/domain_projection.py`.
    external_id: str | None = Field(default=None, description="external_id ở Mini CRM — None nếu là dự án di sản")
    source_revision: int | None = Field(default=None, description="Phiên bản nguồn gần nhất đã soi gương")


class AreaOut(BaseModel):
    """Phân khu kèm tồn kho mới nhất (SRS §5.2 — GET /api/areas)."""

    area_id: str = Field(..., description="UUID phân khu")
    area_name: str = Field(..., description="Tên phân khu")
    unit_type: str = Field(..., description="Loại căn")
    bedrooms: int = Field(..., description="Số phòng ngủ")
    area_sqm: Decimal = Field(..., description="Diện tích m2")
    total_units: int = Field(..., description="Tổng số căn")
    units_remaining: int | None = Field(default=None, description="Tồn kho mới nhất, None nếu chưa chốt")
    snapshot_date: date | None = Field(default=None, description="Ngày của bản chốt tồn kho")
    headline: str = Field(default="", description="Tiêu đề hiển thị")
    introduce: str = Field(default="", description="Mô tả phân khu")
    cover_image_url: str | None = Field(default=None, description="Ảnh bìa, None nếu chưa có")
    # Phase D — cùng lý do với `ProjectSummary`.
    external_id: str | None = Field(default=None, description="external_id ở Mini CRM — None nếu là phân khu di sản")
    source_revision: int | None = Field(default=None, description="Phiên bản nguồn gần nhất đã soi gương")


class AbsorptionPointOut(BaseModel):
    """Một điểm trên biểu đồ tốc độ hấp thụ."""

    stat_date: date = Field(..., description="Ngày (hoặc ngày cuối tuần khi granularity=week)")
    units_sold: int = Field(..., description="Số căn bán")
    velocity_7d: Decimal = Field(..., description="Trung bình trượt 7 ngày")
    velocity_30d: Decimal = Field(..., description="Trung bình trượt 30 ngày")
    is_observed: bool = Field(..., description="False = ngày điền bù, không có giao dịch")
    data_quality_status: str = Field(..., description="ok | warning")


class AbsorptionSeries(BaseModel):
    area_id: str = Field(..., description="UUID phân khu")
    granularity: Literal["day", "week"] = Field(..., description="Mức gộp")
    points: list[AbsorptionPointOut] = Field(default_factory=list)


class AbsorptionSummaryOut(BaseModel):
    """Thẻ số liệu tổng hợp toàn dự án (SRS §5.2 — GET /api/absorption/summary)."""

    units_remaining: int = Field(..., description="Tồn kho còn lại")
    units_sold: int = Field(..., description="Đã bán")
    avg_velocity_30d: Decimal = Field(..., description="Tốc độ trung bình 30 ngày")
    updated_at: datetime | None = Field(default=None, description="Lần tính gần nhất")
    # 0007 — trường THÊM, không thay trường nào. `units_reserved` là NULL với bộ
    # tính cũ: dữ liệu tổng hợp không dựng lại được số căn đang giữ chỗ, và trả 0
    # sẽ bị đọc nhầm thành "không có căn nào đang giữ".
    units_reserved: int | None = Field(
        default=None, description="Số căn đang giữ chỗ; NULL khi bộ tính không tính được"
    )
    calculator: str = Field(
        default="legacy_aggregate",
        description="Bộ tính đã sinh ra các số trên — NGUỒN của các số phía trên, không được suy đoán ở frontend",
    )

    # Phase 5.5 P0 (F-2): bằng chứng đồng bộ THẬT từ backend — thay cho đồng hồ
    # trình duyệt. Ba trường tách biệt vì chúng trả lời BA câu khác nhau: "lần
    # tính thẻ số liệu gần nhất" (updated_at, đã có từ trước), "lần ĐỒNG BỘ CRM
    # THÀNH CÔNG gần nhất", và "lần đồng bộ CRM ĐƯỢC GHI NHẬN gần nhất — kể cả
    # thất bại". Không gộp thành một verdict fresh/stale ở đây: ngưỡng "bao lâu
    # thì gọi là cũ" là quyết định nghiệp vụ, để frontend áp — xem
    # `frontend/src/utils/freshness.js`.
    last_successful_sync: datetime | None = Field(
        default=None, description="Lần đồng bộ CRM gần nhất KẾT THÚC THÀNH CÔNG (completed/completed_with_conflicts)"
    )
    last_attempted_sync: datetime | None = Field(
        default=None, description="Lần đồng bộ CRM gần nhất được ghi nhận, dù thành công hay thất bại"
    )
    last_sync_status: str | None = Field(
        default=None,
        description="Trạng thái của lần đồng bộ CRM gần nhất (pending|processing|completed|completed_with_conflicts|"
        "partially_completed|failed); None nếu dự án CHƯA TỪNG đồng bộ",
    )


# --- Sửa nội dung hiển thị: dự án và phân khu -------------------------------
# Phase D (§D7): KHÔNG còn `ProjectCreate`/`AreaCreate` — Project/Area CHỈ được
# TẠO qua ingestion (`src/services/domain_projection.py`). `ProjectDetail`/
# `AreaDetail` vẫn là response của PATCH — đọc đầy đủ dòng sau khi sửa nội dung
# hiển thị (KHÔNG phải sau khi tạo, dù tên field/response giữ nguyên để không
# phá phía đọc đang tồn tại).

# Chuỗi bắt buộc có nội dung: strip trước rồi mới đo độ dài, nên "   " bị loại
# đúng như "" — CHECK `<> ''` dưới DB cũng hiểu theo nghĩa đó.
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProjectDetail(BaseModel):
    """Dự án sau khi PATCH nội dung hiển thị."""

    project_id: str = Field(..., description="UUID dự án")
    name: str = Field(..., description="Tên dự án")
    launch_date: date = Field(..., description="Ngày mở bán")
    status: str = Field(..., description="pending | active | rejected | archived")
    headline: str = Field(default="", description="Tiêu đề hiển thị")
    introduce: str = Field(default="", description="Mô tả dự án")
    cover_image_url: str | None = Field(default=None, description="Ảnh bìa")
    created_at: datetime = Field(..., description="Thời điểm tạo (UTC)")
    external_id: str | None = Field(default=None, description="external_id ở Mini CRM — None nếu là dự án di sản")
    source_revision: int | None = Field(default=None, description="Phiên bản nguồn gần nhất đã soi gương")


class AreaDetail(BaseModel):
    """Phân khu sau khi PATCH nội dung hiển thị."""

    area_id: str = Field(..., description="UUID phân khu")
    project_id: str = Field(..., description="UUID dự án cha")
    area_name: str = Field(..., description="Tên phân khu")
    unit_type: str = Field(..., description="Loại căn")
    bedrooms: int = Field(..., description="Số phòng ngủ")
    area_sqm: Decimal = Field(..., description="Diện tích m2")
    total_units: int = Field(..., description="Tổng số căn")
    status: str = Field(..., description="pending | active | rejected | archived")
    headline: str = Field(default="", description="Tiêu đề hiển thị")
    introduce: str = Field(default="", description="Mô tả phân khu")
    cover_image_url: str | None = Field(default=None, description="Ảnh bìa")
    created_at: datetime = Field(..., description="Thời điểm tạo (UTC)")
    external_id: str | None = Field(default=None, description="external_id ở Mini CRM — None nếu là phân khu di sản")
    source_revision: int | None = Field(default=None, description="Phiên bản nguồn gần nhất đã soi gương")


# --- Cập nhật nội dung hiển thị ----------------------------------------------
# PATCH: mọi trường đều tuỳ chọn, chỉ ghi trường CÓ MẶT trong body. Phân biệt
# "không gửi" với "gửi null" bằng `model_fields_set`, nên gửi thiếu trường không
# vô tình xoá dữ liệu đang có.
#
# Phase D (§D7): CHỈ còn `headline`/`introduce` — nội dung hiển thị mà backend
# SỞ HỮU RIÊNG (`phase_a_domain_freeze.md` §A2.3). `name`/`launch_date` (Project)
# và `area_name`/`unit_type`/`bedrooms`/`area_sqm`/`total_units` (Area) — CANONICAL,
# do Mini CRM sở hữu — đã bị RÚT: PATCH không còn sửa được chúng, CHỈ ingestion
# (`POST /sync/{entity}`) mới ghi được. `project_id` và `status` vẫn không có ở
# đây — cùng lý do cũ (đổi dự án làm sai số liệu hấp thụ; đổi trạng thái là việc
# của archive qua ingestion).


class ProjectUpdate(BaseModel):
    """Body của PATCH /projects/{project_id}. CHỈ nội dung hiển thị."""

    # Cho phép chuỗi rỗng: đây là nội dung hiển thị, người dùng phải xoá được.
    headline: str | None = Field(default=None, max_length=255, description="Tiêu đề hiển thị")
    introduce: str | None = Field(default=None, description="Mô tả dự án")


class AreaUpdate(BaseModel):
    """Body của PATCH /areas/{area_id}. CHỈ nội dung hiển thị."""

    headline: str | None = Field(default=None, max_length=255, description="Tiêu đề hiển thị")
    introduce: str | None = Field(default=None, description="Mô tả phân khu")


# --- Ảnh bìa dự án / phân khu ----------------------------------------------


class ImageDetail(BaseModel):
    """Ảnh bìa hiện tại. `public_id` cần cho phía client biết ảnh đã đổi chưa."""

    owner_id: str = Field(..., description="UUID dự án hoặc phân khu")
    url: str = Field(..., description="URL ảnh (secure_url của Cloudinary)")
    public_id: str = Field(..., description="public_id trên Cloudinary")


# --- Đồng bộ CRM → ứng dụng (S2) --------------------------------------------
# Phong bì JSON được kiểm tra ở `src/services/json_payload.py` chứ không ở
# pydantic: parser phải phân biệt được "sai phong bì" (hỏng cả lô) với "sai một
# bản ghi" (chỉ bản ghi đó hỏng), còn pydantic thì từ chối nguyên request. Các
# model dưới đây chỉ mô tả phần TRẢ VỀ.

SyncRunStatus = Literal[
    "pending", "processing", "completed", "completed_with_conflicts", "partially_completed", "failed"
]


class SyncRunAccepted(BaseModel):
    """Kết quả một lần gọi `POST /sync/{entity}`."""

    sync_run_id: str = Field(..., description="UUID bản ghi lô (upload_files)")
    status: SyncRunStatus = Field(..., description="Trạng thái lô sau khi xử lý")
    replayed: bool = Field(
        default=False,
        description="True khi external_batch_id đã được xử lý trước đó — lô cũ được trả lại, không xử lý lại",
    )
    rows_received: int = Field(default=0, description="Số bản ghi trong phong bì")
    rows_ok: int = Field(default=0, description="Số bản ghi qua được kiểm tra và có quyết định")
    rows_failed: int = Field(default=0, description="Số bản ghi bị từ chối khi kiểm tra")
    decisions: dict[str, int] = Field(
        default_factory=dict,
        description="Đếm theo quyết định: insert · update · skip_stale · duplicate_noop · conflict · tombstone",
    )
    # 0007 — bảng nghiệp vụ (`units`/`deals`) đã đổi thế nào. Tách khỏi `decisions`
    # vì hai câu hỏi khác nhau: quyết định nói bản đến có được chấp nhận không,
    # còn đây nói bản sao đổi ra sao.
    projections: dict[str, int] = Field(
        default_factory=dict,
        description="Đếm theo tác động lên bản sao: inserted · updated · tombstoned · untouched · rejected",
    )


class SyncRunDetail(BaseModel):
    """Trạng thái đầy đủ của một lô đồng bộ (`GET /sync-runs/{id}`)."""

    sync_run_id: str = Field(..., description="UUID bản ghi lô")
    project_id: str = Field(..., description="Dự án mà lô này thuộc về")
    source_system: str = Field(..., description="Hệ nguồn, ví dụ mini_crm")
    source_instance_id: str = Field(..., description="Định danh kết nối tới hệ nguồn")
    source_entity: str | None = Field(default=None, description="Thực thể được đồng bộ")
    external_batch_id: str | None = Field(default=None, description="Mã lô do hệ nguồn cấp")
    input_format: str = Field(..., description="csv | xlsx | json")
    transport_mode: str = Field(..., description="file_upload | api_push")
    sync_mode: str = Field(..., description="full_snapshot | incremental")
    schema_version: int = Field(..., description="Phiên bản phong bì")
    status: SyncRunStatus = Field(..., description="Trạng thái lô")
    rows_received: int = Field(default=0, description="Số bản ghi nhận được")
    rows_ok: int = Field(default=0, description="Số bản ghi xử lý được")
    rows_failed: int = Field(default=0, description="Số bản ghi bị từ chối")
    decisions: dict[str, int] = Field(default_factory=dict, description="Đếm theo quyết định")
    projections: dict[str, int] = Field(default_factory=dict, description="Đếm theo tác động lên bản sao")
    started_at: datetime = Field(..., description="Thời điểm nhận lô")
    finished_at: datetime | None = Field(default=None, description="Thời điểm kết thúc, NULL khi chưa xong")
    last_source_cursor: str | None = Field(default=None, description="Mốc đọc tiếp cho lần đồng bộ sau")
    error_summary: dict = Field(default_factory=dict, description="Tổng hợp quyết định và lỗi theo nhóm")


class SyncRecordError(BaseModel):
    """Một lỗi của lô đồng bộ. `row_number` NULL với bản ghi JSON."""

    error_category: str = Field(..., description="transport | schema | field | business | conflict")
    error_code: str = Field(..., description="Mã lỗi để frontend i18n")
    message: str = Field(..., description="Mô tả lỗi, đã lược bỏ dữ liệu nhạy cảm")
    json_path: str | None = Field(default=None, description="Vị trí trong phong bì, ví dụ $.records[3].data")
    row_number: int | None = Field(default=None, description="Số dòng — chỉ có với lô nạp từ file")
    source_record_id: str | None = Field(default=None, description="Mã bản ghi ở hệ nguồn")
    field_name: str | None = Field(default=None, description="Tên trường lỗi")
    raw_value_redacted: str | None = Field(default=None, description="Giá trị đã che, không bao giờ là nguyên văn")
    retry_status: str = Field(..., description="open | retrying | resolved | permanent")
    created_at: datetime = Field(..., description="Thời điểm ghi nhận lỗi")


class SyncRunErrorList(BaseModel):
    """Lỗi của một lô đồng bộ (`GET /sync-runs/{id}/errors`)."""

    sync_run_id: str = Field(..., description="UUID bản ghi lô")
    errors: list[SyncRecordError] = Field(default_factory=list)
    total: int = Field(default=0, description="Tổng số lỗi đã lưu cho lô này")
    limit: int = Field(..., description="Số bản ghi tối đa mỗi trang")
    offset: int = Field(..., description="Vị trí bắt đầu")


# --- Phase 5.5 P0 — Step 4: mặt đọc vận hành (bảo vệ bằng RBAC) -------------


class SyncRunSummary(BaseModel):
    """Một dòng trong `GET /sync-runs` — bản rút gọn của `SyncRunDetail`.

    Cùng quan hệ với `SyncRunDetail` như `FileSummary` với `FileStatus`: danh
    sách không kéo theo `error_summary` đầy đủ, tránh một trang liệt kê kéo theo
    hàng trăm KB JSON không ai đọc hết.
    """

    sync_run_id: str = Field(..., description="UUID bản ghi lô")
    project_id: str = Field(..., description="Dự án mà lô này thuộc về")
    source_system: str = Field(..., description="Hệ nguồn, ví dụ mini_crm")
    source_instance_id: str = Field(..., description="Định danh kết nối tới hệ nguồn")
    source_entity: str | None = Field(default=None, description="Thực thể được đồng bộ")
    external_batch_id: str | None = Field(default=None, description="Mã lô do hệ nguồn cấp")
    status: SyncRunStatus = Field(..., description="Trạng thái lô")
    rows_received: int = Field(default=0)
    rows_ok: int = Field(default=0)
    rows_failed: int = Field(default=0)
    started_at: datetime = Field(..., description="Thời điểm nhận lô")
    finished_at: datetime | None = Field(default=None, description="NULL khi chưa xong")


class SyncRunList(BaseModel):
    """`GET /sync-runs` — mặt tổng quan pipeline cho người trực (pipeline_operator+)."""

    items: list[SyncRunSummary] = Field(default_factory=list)
    total: int = Field(default=0, description="Tổng số lô khớp bộ lọc")
    limit: int = Field(..., description="Số bản ghi tối đa mỗi trang")
    offset: int = Field(..., description="Vị trí bắt đầu")


class SyncErrorEntryOut(BaseModel):
    """Một lỗi trong `GET /sync-errors` — cùng các trường với `SyncRecordError`,
    thêm ngữ cảnh LÔ (mà một lỗi bên TRONG một lô đã biết không cần tới), vì đây
    là tra cứu XUYÊN LÔ.

    KHÔNG kế thừa `SyncRecordError`: tên đó kết thúc bằng "Error" nên một lớp con
    sẽ bị `ruff` (N818) hiểu nhầm thành một lớp exception.
    """

    sync_run_id: str = Field(..., description="UUID bản ghi lô chứa lỗi này")
    error_category: str = Field(..., description="transport | schema | field | business | conflict")
    error_code: str = Field(..., description="Mã lỗi để frontend i18n")
    message: str = Field(..., description="Mô tả lỗi, đã lược bỏ dữ liệu nhạy cảm")
    json_path: str | None = Field(default=None)
    row_number: int | None = Field(default=None)
    source_record_id: str | None = Field(default=None)
    field_name: str | None = Field(default=None)
    raw_value_redacted: str | None = Field(default=None)
    retry_status: str = Field(..., description="open | retrying | resolved | permanent")
    created_at: datetime = Field(..., description="Thời điểm ghi nhận lỗi")
    external_batch_id: str | None = Field(default=None)
    source_system: str | None = Field(default=None)
    source_entity: str | None = Field(default=None)


class SyncErrorList(BaseModel):
    """`GET /sync-errors` — tra lỗi XUYÊN LÔ (khác `/sync-runs/{id}/errors`, vốn chỉ
    một lô), cho câu hỏi "gần đây có kiểu lỗi nào lặp lại không"."""

    errors: list[SyncErrorEntryOut] = Field(default_factory=list)
    total: int = Field(default=0)
    limit: int = Field(..., description="Số bản ghi tối đa mỗi trang")
    offset: int = Field(..., description="Vị trí bắt đầu")


class SyncPayloadOut(BaseModel):
    """`GET /sync-runs/{id}/payload` — payload thô của một lô, CHE mặc định.

    `view='redacted'` (mặc định): không trả `payload`, chỉ trả metadata — đủ để
    xác nhận lô có payload lưu hay không mà không lộ nội dung. `view='raw'`: trả
    nguyên văn, và CHỈ tới được đây sau khi router đã kiểm vai trò + điều kiện
    (xem `src/api/sync.py`).
    """

    sync_run_id: str = Field(..., description="UUID bản ghi lô")
    view: Literal["redacted", "raw"] = Field(..., description="Chế độ đã trả — không suy ngược được từ payload=None")
    payload_sha256: str = Field(..., description="Băm SHA-256 của payload thô — kiểm được cả khi view=redacted")
    payload_bytes: int = Field(..., description="Kích thước payload thô, byte")
    record_count: int = Field(..., description="Số bản ghi trong payload")
    received_at: datetime = Field(..., description="Thời điểm payload được lưu")
    payload: dict | None = Field(default=None, description="Nguyên văn — chỉ có khi view=raw")


# --- Mô hình miền S3: tồn kho theo từng căn ---------------------------------

CalculatorName = Literal["legacy_aggregate", "domain_units_deals"]


class InventoryAreaOut(BaseModel):
    """Tồn kho hiện tại của một phân khu, tính từ `units` + `deals`."""

    area_id: str = Field(..., description="UUID phân khu")
    area_name: str = Field(..., description="Tên phân khu")
    unit_type: str = Field(..., description="Loại căn")
    total_units: int = Field(..., description="Số căn bán được (đã trừ căn blocked)")
    units_sold: int = Field(..., description="Số căn có giao dịch `sold` còn hiệu lực")
    units_reserved: int = Field(..., description="Số căn có giao dịch `reserved` còn hiệu lực")
    units_remaining: int = Field(..., description="Còn lại = bán được − đã bán − đang giữ chỗ")
    units_blocked: int = Field(..., description="Số căn nằm ngoài quỹ hàng")


class InventoryUnitOut(BaseModel):
    """Một căn trong bản sao, kèm trạng thái giao dịch đang giữ nếu có."""

    unit_id: str = Field(..., description="UUID căn trong bản sao")
    external_unit_id: str = Field(..., description="Mã căn ở hệ nguồn")
    area_id: str = Field(..., description="UUID phân khu")
    unit_code: str = Field(..., description="Mã căn")
    unit_type: str = Field(..., description="Loại căn")
    status: str = Field(..., description="available | reserved | sold | blocked (CRM sở hữu)")
    active_deal_status: str | None = Field(
        default=None, description="Trạng thái giao dịch đang giữ căn, NULL nếu không có"
    )
    deleted_at: datetime | None = Field(default=None, description="Mốc tombstone, NULL nếu còn hiệu lực")


class InventoryOut(BaseModel):
    """`GET /inventory` — tồn kho suy ra từ bản sao CRM, không phải từ dữ liệu tổng hợp."""

    project_id: str = Field(..., description="UUID dự án")
    calculator: CalculatorName = Field(..., description="Bộ tính đã dùng")
    totals: InventoryAreaOut | None = Field(default=None, description="Cộng dồn toàn dự án")
    areas: list[InventoryAreaOut] = Field(default_factory=list)
    units: list[InventoryUnitOut] = Field(default_factory=list, description="Chỉ trả khi có bộ lọc chi tiết")
    anomalies: list[dict] = Field(
        default_factory=list, description="Quan hệ căn/giao dịch không hợp lệ — không bị đếm im lặng"
    )


class DealOut(BaseModel):
    """Một giao dịch trong bản sao CRM (`GET /deals`).

    Theo ĐÚNG quy ước của `InventoryUnitOut`/`InventoryAreaOut`: một dòng, đọc
    thẳng từ bảng `deals` — không dựng lại từ dữ liệu tổng hợp cũ.
    """

    deal_id: str = Field(..., description="UUID giao dịch trong bản sao")
    external_deal_id: str = Field(..., description="Mã giao dịch ở hệ nguồn")
    unit_id: str = Field(..., description="UUID căn liên quan")
    status: str = Field(..., description="Trạng thái đã ánh xạ: reserved | sold | lost | …")
    source_status: str = Field(..., description="Trạng thái NGUYÊN VĂN của hệ nguồn")
    reserved_at: datetime | None = Field(default=None)
    sold_at: datetime | None = Field(default=None)
    lost_at: datetime | None = Field(default=None)
    source_revision: int | None = Field(default=None)
    updated_at: datetime = Field(..., description="Lần cuối bản sao này được ghi")
    deleted_at: datetime | None = Field(default=None, description="Mốc tombstone, NULL nếu còn hiệu lực")


class DealList(BaseModel):
    """`GET /deals` — cùng khuôn phân trang với `/inventory` (S3)."""

    project_id: str = Field(..., description="UUID dự án")
    items: list[DealOut] = Field(default_factory=list)
    total: int = Field(default=0, description="Tổng số giao dịch khớp bộ lọc")
    limit: int = Field(..., description="Số bản ghi tối đa mỗi trang")
    offset: int = Field(..., description="Vị trí bắt đầu")


class ParallelRunOut(BaseModel):
    """Kết quả chạy song song hai bộ tính. Không đổi số liệu sản xuất."""

    project_id: str = Field(..., description="UUID dự án")
    matches: bool = Field(..., description="True khi hai bộ tính cho cùng kết quả và không có bất thường")
    legacy_units_sold: int = Field(..., description="Đã bán theo bộ tính cũ")
    domain_units_sold: int = Field(..., description="Đã bán theo bộ tính mới")
    legacy_units_remaining: int = Field(..., description="Còn lại theo bộ tính cũ")
    domain_units_remaining: int = Field(..., description="Còn lại theo bộ tính mới")
    domain_units_reserved: int = Field(..., description="Đang giữ chỗ — chỉ bộ tính mới có")
    differences: list[dict] = Field(default_factory=list, description="Chênh lệch theo từng chỉ số")
    anomalies: list[dict] = Field(default_factory=list, description="Bất thường quan hệ dữ liệu")
    production_calculator: CalculatorName = Field(
        ..., description="Bộ tính mà dashboard đang thực sự đọc — chạy song song KHÔNG đổi giá trị này"
    )


# --- Phase 5: đối soát -------------------------------------------------------

ReconciliationScope = Literal["internal", "snapshot", "source"]
FindingSeverity = Literal["info", "warning", "error"]


class ReconciliationRunOut(BaseModel):
    """Kết quả một lượt đối soát (`POST /reconciliation/runs`).

    `passed` chỉ đúng khi `error_count == 0` — ràng buộc này được DB canh, không
    chỉ nằm ở tầng ứng dụng.
    """

    reconciliation_run_id: str = Field(..., description="UUID lượt đối soát")
    scope: ReconciliationScope = Field(..., description="Phạm vi: nội bộ, ảnh chụp, hay so với nguồn")
    status: str = Field(..., description="running | completed | failed")
    passed: bool = Field(..., description="Chỉ true khi không có phát hiện mức 'error'")
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    checks_run: int = 0
    tombstoned: int = Field(default=0, description="Số bản ghi bị tombstone theo ảnh chụp, nếu có")
    summary: dict = Field(default_factory=dict, description="Tổng hợp theo mức độ và theo loại kiểm tra")


class ReconciliationFindingOut(BaseModel):
    """Một phát hiện, dạng máy đọc được."""

    check_code: str = Field(..., description="Mã loại kiểm tra, ví dụ ORPHAN_DEAL")
    severity: FindingSeverity
    entity: str | None = None
    external_id: str | None = None
    message: str
    details: dict = Field(default_factory=dict, description="Đủ dữ kiện để dựng lại vấn đề, không cần mở log")
    created_at: datetime


class ReconciliationFindingList(BaseModel):
    reconciliation_run_id: str
    findings: list[ReconciliationFindingOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class StaleProjectOut(BaseModel):
    """Một dự án có lô đồng bộ đã áp dụng mà lineage miền chưa theo kịp."""

    project_id: str
    project_name: str
    last_applied_sync_at: str = Field(..., description="Lô api_push gần nhất có thay đổi bản sao")
    last_domain_computed_at: str | None = Field(None, description="None = chưa tính lần nào")
    applied_runs: int = Field(..., description="Số lô đã áp dụng thay đổi cho dự án này")
    never_computed: bool


class DomainRecomputeAuditOut(BaseModel):
    """Trạng thái lineage miền cho giám sát vận hành.

    `stale_projects > 0` là tín hiệu cảnh báo. Endpoint luôn trả HTTP 200 khi
    được xác thực — mã lỗi HTTP dành cho lỗi của chính endpoint, không dành cho
    kết quả nghiệp vụ mà nó báo cáo.
    """

    status: Literal["ok", "degraded"]
    stale_projects: int = 0
    projects: list[StaleProjectOut] = Field(default_factory=list)
    checked_at: datetime


class CalculatorComparisonOut(BaseModel):
    """Một lần so sánh hai bộ tính (`calculator_comparisons`, 0013).

    Cột chỉ số là `None` khi lineage tương ứng KHÔNG CÓ dữ liệu — khác hẳn với 0,
    nghĩa là có dữ liệu và tổng bằng không. Đọc nhầm hai thứ này sẽ biến một dự án
    rỗng thành một lần chạy song song "thành công".
    """

    comparison_id: str
    project_id: str
    compared_at: datetime
    trigger: Literal["schedule", "manual"]
    legacy_units_sold: int | None = None
    legacy_units_remaining: int | None = None
    domain_units_sold: int | None = None
    domain_units_remaining: int | None = None
    domain_units_reserved: int | None = None
    legacy_has_data: bool
    domain_has_data: bool
    matches: bool
    difference_count: int = 0
    anomaly_count: int = 0
    differences: list[dict] = Field(default_factory=list)
    anomalies: list[dict] = Field(default_factory=list)


class CalculatorComparisonList(BaseModel):
    project_id: str
    comparisons: list[CalculatorComparisonOut] = Field(default_factory=list)
    total: int = 0
    gate_eligible_only: bool = False
    # Đi kèm MỌI phản hồi. Một dòng `matches: true` bị tách khỏi ngữ cảnh sẽ được
    # đọc thành "đã sẵn sàng cắt sang".
    disclaimer: str = (
        "Lịch sử so sánh này sinh ra từ fixture TỔNG HỢP. Nó KHÔNG phải bằng chứng "
        "tương thích với Mini CRM và KHÔNG đủ để cắt sang bộ tính miền; Mini CRM chưa tồn tại. "
        "So sánh chạy TUẦN TỰ (không phải một ảnh chụp repeatable-read) — xem docs/crm/parallel_run.md."
    )


class ClassifiedDifferenceOut(BaseModel):
    """Một chênh lệch đã được phân loại (Phase 8E)."""

    metric: str
    legacy: int | None = None
    domain: int | None = None
    delta: int | None = None
    classification: str
    blocking: bool
    reason: str


class ComparisonVerdictOut(BaseModel):
    """Phán quyết cho một lần so sánh.

    `is_cutover_evidence` là trường mà cổng cắt sang (8G) sẽ đọc. Nó KHÔNG đồng
    nghĩa với `matches`: một dòng thiếu dữ liệu ở một bên vẫn có thể `matches=true`
    (cả hai cùng bằng 0) nhưng không bao giờ là bằng chứng.
    """

    comparison_id: str
    project_id: str
    verdict: Literal["clean", "accepted_differences", "blocked", "no_data"]
    is_cutover_evidence: bool
    blocking_count: int = 0
    counts_by_class: dict[str, int] = Field(default_factory=dict)
    differences: list[ClassifiedDifferenceOut] = Field(default_factory=list)
    anomalies: list[ClassifiedDifferenceOut] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ComparisonVerdictList(BaseModel):
    project_id: str
    verdicts: list[ComparisonVerdictOut] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    gate_eligible_only: bool = False
    disclaimer: str = (
        "Phán quyết này áp bộ quy tắc phân loại hiện hành lên lịch sử so sánh sinh ra từ "
        "fixture TỔNG HỢP. `is_cutover_evidence=true` KHÔNG có nghĩa là đã sẵn sàng cắt sang: "
        "điều kiện cắt sang cần dữ liệu THẬT và toàn bộ danh sách ở docs/crm/activation_prerequisites.md."
    )
