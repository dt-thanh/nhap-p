import json
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Phải khớp `src/services/oidc.py::CANONICAL_APP_ROLES` — trùng lặp CÓ CHỦ Ý
# (không import: `config.py` không được phụ thuộc vào tầng service) để canh
# đúng một việc: OIDC_ROLE_MAP không được định nghĩa lại ba khoá vai trò chuẩn
# này thành một giá trị khác ở `_reject_conflicting_canonical_role_map` dưới đây.
_CANONICAL_APP_ROLES = {
    "CRM.CEO": "admin",
    "CRM.Admin": "admin",
    "CRM.ADVISOR": "business_viewer",
    "CRM.Viewer": "business_viewer",
    "CRM.SALES": "pipeline_operator",
    "CRM.Operator": "pipeline_operator",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "demo", "production", "staging", "test"] = "production"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    cors_origins: str = "http://localhost:3000"
    # Canonical browser origin for redirects back into the AbsorpIQ SPA.  This
    # is deliberately separate from CORS_ORIGINS: CORS can contain several
    # callers, whereas the OIDC callback must have exactly one safe target.
    frontend_base_url: str = "http://localhost:5173"
    # Development-only local access for the dashboard while real local auth is pending.
    dev_auth_bypass: bool = False

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    llm_log_snippet: bool = False  # chỉ bật ở development

    # LLM — SecretStr để repr(settings) không lộ key
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek/deepseek-v4-flash"
    llm_base_url: str = "https://api.xkiro.com/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = "https://api.openai.com/v1"
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    llm_provider: str = "deepseek"

    # Database
    database_url: SecretStr = SecretStr("postgresql+asyncpg://app:app@db:5432/absorption")

    # Queue & scheduler
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    forecast_cron: str = "0 2 * * *"  # 02:00 hằng ngày (SRS §2.4)
    scheduler_timezone: str = "Asia/Ho_Chi_Minh"
    # A queued run without a live RQ job is not failed merely because it is
    # old: dispatch and scheduler outages are recoverable.  This threshold is
    # used only by the explicit service-layer reconciliation path to classify a
    # demonstrably orphaned intent, never by a request/read path.
    ranking_run_stale_seconds: int = Field(default=900, ge=60, le=86400)
    # Evidence attempts are append-only; a pending row older than this window
    # is considered orphaned and may be superseded by an explicit extract
    # request. Fresh pending attempts remain idempotent no-ops.
    evidence_pending_stale_seconds: int = Field(default=900, ge=60, le=86400)

    # Kiểm lineage miền (Phase 8A). Hằng giờ: cửa sổ sự cố giữa COMMIT và enqueue
    # hiếm khi xảy ra, nhưng khi xảy ra thì độ trễ phát hiện chính là khoảng thời
    # gian số liệu sai — nên đo bằng giờ, không phải bằng ngày.
    domain_recompute_audit_enabled: bool = True
    domain_recompute_audit_cron: str = "15 * * * *"
    # Tự xếp lại hàng khi phát hiện lạc hậu. Tắt cờ này thì chỉ báo động. Cảnh báo
    # phát ra trong CẢ HAI trường hợp — sửa im lặng sẽ giấu mất một đường enqueue
    # hỏng vĩnh viễn.
    domain_recompute_audit_repair: bool = True

    # Chạy song song hai bộ tính và GHI LẠI kết quả (Phase 8D). Hằng ngày, sau
    # forecast 02:00 — một lần đo bị chậm vì Prophet chạy lâu không sao, nhưng
    # chậm sang ngày hôm sau thì chuỗi đo bị đứt.
    parallel_run_capture_enabled: bool = True
    parallel_run_capture_cron: str = "30 3 * * *"

    # PR-1 hierarchical ranking (D29/D37/D41, `docs/ranking/ranking_consultant.md`
    # §24). MẶC ĐỊNH TẮT: cột mới nullable và an toàn khi tắt, nhưng bước tính
    # bổ sung này chưa có Market/Project/Area/Legal nguồn thật (PR-1) — bật cờ
    # chỉ nên làm khi có `ranking_configs.hierarchical_weights` hợp lệ để đọc.
    hierarchical_ranking_enabled: bool = True

    # PR-7: the READ-surface kill switch, independent of the flag above.
    # `hierarchical_ranking_enabled` controls whether the post-run COMPUTE
    # step ever writes `ranking_scores.hierarchical_score`/`.contributions`;
    # this one controls whether `GET /ranking` DISCLOSES that already-written
    # data to API/UI callers at all. Separate flags let compute run quietly
    # in production (verify the data looks right) before the read surface is
    # switched on — and let the read surface be switched OFF instantly (no
    # migration, no touching stored scores) if something looks wrong post-launch.
    # MẶC ĐỊNH TẮT: same reasoning as `hierarchical_ranking_enabled` above.
    hierarchical_read_enabled: bool = False

    # Ranking v3: lets the already-computed `hierarchical_score` actually
    # DRIVE `rank_in_project`/`rank_in_area` (the columns every real consumer
    # — main dashboard, Hot Units, `GET /market/units` — already reads),
    # instead of staying a parallel, mostly-decorative column. Deliberately
    # independent of `hierarchical_ranking_enabled`/`hierarchical_read_enabled`
    # above (which are already `true`/`true` in some environments) — flipping
    # THIS flag is its own explicit rollout step, never piggybacked on those.
    # MẶC ĐỊNH TẮT: byte-identical legacy behavior until turned on deliberately.
    ranking_v3_composite_enabled: bool = True

    # Endpoint vận hành. RỖNG = endpoint tắt (503), không phải mở: dữ liệu lạc hậu
    # kèm project_id là thông tin nội bộ, mặc định phải là đóng.
    ops_api_token: SecretStr = SecretStr("")

    # Chốt A4 (Phase 8B). MẶC ĐỊNH TẮT — một bản ghi đầy đủ đánh rơi mốc lịch sử
    # bị TỪ CHỐI. Bật cờ này thì giá trị cũ được chép sang kèm cảnh báo, và mọi
    # con số hấp thụ tính ra là XẤP XỈ. Chỉ bật khi đội CRM đã xác nhận hệ nguồn
    # không giữ được lịch sử và cả đội chấp nhận điều đó — và khi bật thì cổng cắt
    # sang PHẢI hỏng. Xem docs/crm/activation_prerequisites.md.
    sync_preserve_dropped_timestamps: bool = False

    # Auth (MVP 3)
    jwt_secret: SecretStr = SecretStr("change-me-in-env")
    jwt_algorithm: str = "HS256"
    access_token_ttl: int = 1800  # 30 phút
    refresh_token_ttl: int = 604800  # 7 ngày

    # Bảng điều khiển vận hành (Phase 5.5 P0). BA token tĩnh, mỗi token ứng với
    # đúng MỘT vai trò — vai trò suy ra từ VIỆC TOKEN NÀO KHỚP, không bao giờ từ
    # một trường do client tự khai (`X-Role` hay tương đương). Cùng nguyên tắc
    # "rỗng = đóng" của `ops_api_token`: chưa cấu hình token nào thì toàn bộ mặt
    # đọc vận hành trả 503, không phải mở.
    #
    # Đây KHÔNG phải hệ quản lý người dùng: không có bảng, không có migration.
    # Token xoay bằng cách đổi biến môi trường và khởi động lại — đủ cho quy mô
    # pilot. Quản lý vai trò động (tạo/sửa/xoá người dùng qua API) cần một bảng
    # mới, nên bị hoãn — xem pipeline_status.md.
    dashboard_business_viewer_token: SecretStr = SecretStr("")
    dashboard_pipeline_operator_token: SecretStr = SecretStr("")
    dashboard_admin_token: SecretStr = SecretStr("")

    # Phạm vi dự án (Phase E). JSON: {"<token>": ["ext_id", ...]} hoặc
    # {"<token>": "ALL"}. TĨNH, gắn vào token — cùng cách D-14 làm ở Mini CRM.
    # Token vắng mặt trong map này = phạm vi RỖNG, kể cả khi bản thân token hợp
    # lệ (đã xác thực được vai trò) — không có "admin thì bỏ qua",
    # xem docs/crm/phase_a_domain_freeze.md §A7.3.
    dashboard_project_scope: SecretStr = SecretStr("")

    # --- Auth provider ---------------------------------------------------------
    # Chỉ Keycloak được hỗ trợ ở runtime. Field này KHÔNG chọn giữa nhiều nhà
    # cung cấp (không còn Entra để chọn) — nó là một khai báo TƯỜNG MINH, để một
    # biến môi trường thất lạc/kiểu cũ không bao giờ có thể ÂM THẦM bật một
    # đường xác thực khác: giá trị nào khác "keycloak" bị Pydantic từ chối ngay
    # lúc khởi động, không phải lúc có request đầu tiên.
    auth_provider: Literal["keycloak"] = "keycloak"

    # --- OIDC / Keycloak (SSO) -------------------------------------------------
    # KHÔNG hard-code — rỗng nghĩa là đường đăng nhập người dùng TẮT (đóng, không
    # mở); route auth trả 503 rõ ràng thay vì đoán một nhà cung cấp khác.
    # ISSUER là URL CÔNG KHAI/front-channel (browser mở được), INTERNAL_BASE_URL
    # là URL back-channel qua Docker DNS (container gọi). Xem src/services/oidc.py.
    oidc_issuer: str = ""
    oidc_internal_base_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""
    oidc_post_logout_redirect_uri: str = ""
    oidc_scopes: str = "openid profile email offline_access"
    oidc_audience: str = ""
    oidc_allowed_algorithms: str = "RS256"
    oidc_clock_skew_seconds: int = 60
    # JSON: {"<claim>": "<vai trò nội bộ>"}. Ba khoá CRM.CEO/CRM.ADVISOR/CRM.SALES
    # luôn được seed vào bản đồ này (xem oidc.py::resolve_role) — map này chỉ để
    # THÊM claim/nhóm tuỳ biến, không thể định nghĩa lại ba khoá chuẩn.
    oidc_role_map: SecretStr = SecretStr("")
    # JSON: {"<claim>": ["ext_id", ...]} hoặc {"<claim>": "ALL"}.
    oidc_project_scope: SecretStr = SecretStr("")

    session_secret: SecretStr = SecretStr("")
    session_ttl_seconds: int = 3600
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"

    # Cloudinary — lưu ảnh bìa dự án / phân khu.
    # SecretStr để repr(settings) không lộ key khi log hay debug.
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: SecretStr = SecretStr("")
    cloudinary_api_secret: SecretStr = SecretStr("")
    cloudinary_folder: str = "absorptionforecast"
    image_max_size: int = 5 * 1024 * 1024  # 5 MB

    # Ingestion & nghiệp vụ
    upload_max_size: int = 20 * 1024 * 1024  # 20 MB
    upload_dir: str = "./uploads"
    alert_threshold_days: int = 30
    # Tỷ lệ dòng lỗi tối đa còn chấp nhận nạp. Vượt ngưỡng thì ImportService từ
    # chối cả lô (SRS §5.2). Không đặt trong bảng `settings` được vì
    # settings.updated_by là NOT NULL -> users.id, mà MVP 1 chưa có users.
    import_error_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _reject_dev_bypass_outside_development(self) -> "Settings":
        """Chốt khởi động, không chỉ chốt lúc chạy. `dashboard_auth.py` đã tự
        gác `dev_auth_bypass` sau `app_env == "development"`, nhưng đó là một
        điều kiện ÂM THẦM lặng lẽ vô hiệu hoá cờ — tiến trình vẫn khởi động
        bình thường với một cờ bypass nằm chờ, chỉ cần `APP_ENV` đổi (hoặc bị
        bỏ trống, mặc định rơi về "production" — xem field phía trên) là bật
        lại. Từ chối ngay ở đây thay vì im lặng bỏ qua: một compose file dev bị
        tái sử dụng sai chỗ sẽ KHÔNG khởi động được, thay vì khởi động rồi âm
        thầm mở toang quyền admin cho request không kèm token.
        """
        if self.dev_auth_bypass and self.app_env != "development":
            raise ValueError(
                "DEV_AUTH_BYPASS=true chỉ được phép khi APP_ENV=development "
                f"(đang là '{self.app_env}'). Đặt DEV_AUTH_BYPASS=false, hoặc "
                "APP_ENV=development nếu đây thật sự là máy dev cục bộ."
            )
        return self

    @model_validator(mode="after")
    def _validate_frontend_base_url(self) -> "Settings":
        """Keep auth redirects pinned to one absolute SPA origin.

        Paths, queries, and fragments belong to the per-request relative
        return target, never to deployment configuration.  Rejecting them here
        prevents a malformed deployment value from silently creating a broken
        or open redirect surface.
        """
        oidc_is_configured = bool(
            self.oidc_issuer
            and self.oidc_client_id
            and self.oidc_client_secret.get_secret_value()
            and self.oidc_redirect_uri
            and self.session_secret.get_secret_value()
        )
        if (
            self.app_env in {"production", "staging"}
            and oidc_is_configured
            and "frontend_base_url" not in self.model_fields_set
        ):
            raise ValueError(
                "FRONTEND_BASE_URL bắt buộc khi OIDC được bật ở staging/production; "
                "không được suy diễn đích redirect từ CORS_ORIGINS."
            )

        parsed = urlparse(self.frontend_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "FRONTEND_BASE_URL phải là origin http(s) tuyệt đối, không kèm path, query hoặc fragment."
            )
        self.frontend_base_url = self.frontend_base_url.rstrip("/")
        return self

    @model_validator(mode="after")
    def _reject_conflicting_canonical_role_map(self) -> "Settings":
        """CRM.CEO/CRM.ADVISOR/CRM.SALES là vai trò nghiệp vụ chuẩn, dùng chung
        với Mini CRM (`oidc.py::CANONICAL_APP_ROLES`) — cố định trong code.
        Nếu `OIDC_ROLE_MAP` cố tình gán một trong ba khoá này cho một vai trò
        nội bộ KHÁC, đó là một cấu hình mâu thuẫn: từ chối ngay lúc khởi động
        thay vì để `resolve_role` phải âm thầm quyết định bên nào thắng.
        """
        raw = self.oidc_role_map.get_secret_value()
        if not raw:
            return self
        try:
            parsed = json.loads(raw)
        except ValueError:
            return self
        if not isinstance(parsed, dict):
            return self
        for key, canonical_value in _CANONICAL_APP_ROLES.items():
            configured_value = parsed.get(key)
            if configured_value is not None and configured_value != canonical_value:
                raise ValueError(
                    f"OIDC_ROLE_MAP đặt '{key}' thành '{configured_value}', nhưng đây là vai trò "
                    f"chuẩn cố định trong code ('{canonical_value}'). Xoá khoá này khỏi "
                    "OIDC_ROLE_MAP, hoặc dùng đúng giá trị chuẩn."
                )
        return self

    @property
    def resolved_llm_api_key(self) -> str:
        return self.llm_api_key.get_secret_value()

    @property
    def resolved_llm_model(self) -> str:
        return self.llm_model or "deepseek/deepseek-v4-flash"

    @property
    def cloudinary_configured(self) -> bool:
        """Đủ ba biến mới bật được tính năng ảnh. Thiếu -> API trả 503 rõ ràng."""
        return bool(
            self.cloudinary_cloud_name
            and self.cloudinary_api_key.get_secret_value()
            and self.cloudinary_api_secret.get_secret_value()
        )

    @property
    def ops_endpoint_enabled(self) -> bool:
        """Chưa cấu hình token thì endpoint vận hành đóng hẳn, không mở công khai."""
        return bool(self.ops_api_token.get_secret_value())

    @property
    def dashboard_auth_configured(self) -> bool:
        """Chưa cấu hình token nào thì mặt đọc vận hành đóng hẳn (503), không mở."""
        return bool(
            self.dashboard_business_viewer_token.get_secret_value()
            or self.dashboard_pipeline_operator_token.get_secret_value()
            or self.dashboard_admin_token.get_secret_value()
        )

    @property
    def database_dsn(self) -> str:
        """DSN dạng chuỗi. KHÔNG log giá trị này."""
        return self.database_url.get_secret_value()

    @property
    def redis_dsn(self) -> str:
        """DSN dạng chuỗi. KHÔNG log giá trị này."""
        return self.redis_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
