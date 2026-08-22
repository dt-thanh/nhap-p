from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "staging", "test"] = "production"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    cors_origins: str = "http://localhost:3000"
    # Development-only local access for the dashboard while real local auth is pending.
    dev_auth_bypass: bool = False

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    llm_log_snippet: bool = False  # chỉ bật ở development

    # LLM — SecretStr để repr(settings) không lộ key
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "gpt-4o-mini"
    openai_api_key: SecretStr = SecretStr("")  # fallback tương thích ngược
    model_name: str = ""  # fallback tương thích ngược
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    llm_provider: str = "openai"

    # Database
    database_url: SecretStr = SecretStr("postgresql+asyncpg://app:app@db:5432/absorption")

    # Queue & scheduler
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    forecast_cron: str = "0 2 * * *"  # 02:00 hằng ngày (SRS §2.4)
    scheduler_timezone: str = "Asia/Ho_Chi_Minh"

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

    # --- Microsoft Entra ID / SSO (CP5) --------------------------------------
    # CÙNG TENANT với Mini CRM — đó là toàn bộ cơ chế SSO. Client id/secret có
    # thể (nên) khác: hai app registration tách bạch, thu hồi được độc lập.
    # KHÔNG hard-code; rỗng = đường Entra chưa bật.
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: SecretStr = SecretStr("")
    entra_redirect_uri: str = ""
    entra_post_logout_redirect_uri: str = ""
    entra_authority_host: str = "https://login.microsoftonline.com"
    entra_issuer: str = ""
    entra_audience: str = ""
    entra_scopes: str = "openid profile email offline_access"
    entra_allowed_algorithms: str = "RS256"
    entra_clock_skew_seconds: int = 60
    entra_role_map: SecretStr = SecretStr("")
    entra_project_scope: SecretStr = SecretStr("")

    # --- Generic OIDC (Keycloak local, hoặc bất kỳ IdP OIDC nào) --------------
    # §12: OIDC_* set ⇒ dùng OIDC provider; rỗng ⇒ rơi về ENTRA_* nếu hợp lệ.
    # KHÔNG hard-code — rỗng nghĩa là đường OIDC generic TẮT (đóng, không mở).
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

    @property
    def resolved_llm_api_key(self) -> str:
        return self.llm_api_key.get_secret_value() or self.openai_api_key.get_secret_value()

    @property
    def resolved_llm_model(self) -> str:
        return self.llm_model or self.model_name or "gpt-4o-mini"

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
