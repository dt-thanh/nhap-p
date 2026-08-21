"""Cấu hình Mini CRM. TIỀN TỐ `MINICRM_` cho MỌI biến, không có ngoại lệ.

Đây là ranh giới cô lập quan trọng nhất của cả ứng dụng này, và nó nằm ở đúng một
dòng: `env_prefix="MINICRM_"`.

Vì sao nó quan trọng đến thế. Mini CRM và backend chạy trong cùng một mạng Compose
và có thể cùng đọc một file `.env`. Không có tiền tố, `DATABASE_URL` của backend
sẽ được Mini CRM đọc trúng, và Mini CRM sẽ migrate cây schema của MÌNH lên database
của backend — trộn hai lịch sử Alembic vào một bảng `alembic_version` và phá hỏng
cả hai. Tiền tố khiến việc đó không xảy ra được, thay vì trông cậy vào việc không
ai đặt sai biến.

**Module này KHÔNG được import bất cứ thứ gì từ `src/`.** Mini CRM là một ứng dụng
riêng: database riêng, lịch sử migration riêng, vòng đời triển khai riêng. Import
chéo sẽ biến hai hệ thống thành một hệ thống có hai giao diện, và mọi bảo đảm cô
lập ở đây thành lời hứa suông.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINICRM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Mini CRM (synthetic)"
    # Môi trường chạy. "production" siết một số cấu hình chỉ-cho-dev (xem
    # validator authorization_mode bên dưới).
    app_env: str = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)

    # Database RIÊNG. Không bao giờ trỏ vào database của backend.
    database_url: SecretStr = SecretStr("postgresql+asyncpg://minicrm:minicrm@minicrm_db:5432/minicrm")

    # --- Đường đẩy sang backend (MỘT CHIỀU) ---------------------------------
    # Tên SERVICE của Compose, không phải `localhost`: trong container,
    # `localhost` là chính Mini CRM.
    sync_base_url: str = "http://api:8000"
    sync_api_key: SecretStr = SecretStr("")
    sync_timeout_seconds: float = Field(default=10.0, gt=0)

    # Danh tính hệ nguồn mà backend sẽ thấy. `source_instance_id` là ranh giới cô
    # lập ở phía backend, và khoá API bị buộc vào đúng giá trị này.
    source_system: str = "mini_crm"
    source_instance_id: str = "mini-crm-dev"

    # Dự án ĐÃ CÓ ở backend. Mini CRM không tạo được dự án — backend sở hữu chúng.
    project_id: str = ""

    # Chỉ dùng ở dev. Production migrate bằng lệnh tường minh, giống backend.
    run_migrations: bool = False

    # --- Relay tự động (Phase C.5) ------------------------------------------
    # Đọc lại crm_outbox, gửi lại đúng payload đã ký — KHÔNG ghi dòng mới, KHÔNG
    # đổi hợp đồng. Xem app/relay.py cho lý do từng giá trị mặc định.
    relay_enabled: bool = True
    relay_interval_seconds: float = Field(default=5.0, gt=0)
    relay_batch_size: int = Field(default=20, ge=1)

    # --- Xác thực GHI (D-14) -------------------------------------------------
    # Ba token tĩnh, mirror ĐÚNG nguyên tắc `dashboard_*_token` của backend
    # (`src/config.py`) — rỗng = vai trò đó CHƯA cấu hình, xem `app/auth.py`.
    auth_business_viewer_token: SecretStr = SecretStr("")
    auth_pipeline_operator_token: SecretStr = SecretStr("")
    auth_admin_token: SecretStr = SecretStr("")
    # JSON: {"<token>": ["P-0001", "P-0002"]} hoặc {"<token>": "ALL"}. Token vắng
    # mặt trong map này = phạm vi RỖNG, kể cả khi bản thân token đó hợp lệ.
    auth_project_scope: SecretStr = SecretStr("")

    # --- Human auth (Checkpoint 1) — HỆ CHÍNH THỨC CỦA ĐỘI -------------------
    # Các field này thuộc về `app/human_auth.py` + `app/auth_contract.py` (JWT
    # nội bộ HS256, lifecycle mời/đăng nhập/đặt lại mật khẩu). Chúng bị mất khi
    # merge/conflict đè config, nên `test_auth_contract.py` fail. Khôi phục
    # nguyên vẹn ở đây — Entra (bên dưới) là hệ THỨ HAI sống song song, không
    # thay thế hệ này. Ràng buộc lấy từ chính test contract của đội:
    #   - algorithm PHẢI là HS256 (JWT ký đối xứng bằng auth_signing_secret)
    #   - mọi TTL PHẢI > 0
    #   - dev_auth_bypass mặc định False (không mở cửa ngầm ở production)
    auth_issuer: str = "http://localhost:8000"
    auth_audience: str = "absorbiq-api"
    auth_algorithm: str = "HS256"
    auth_signing_secret: SecretStr = SecretStr("")
    access_token_ttl_seconds: int = Field(default=900, gt=0)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, gt=0)
    invite_token_ttl_seconds: int = Field(default=604_800, gt=0)
    password_reset_token_ttl_seconds: int = Field(default=3_600, gt=0)
    login_rate_limit_attempts: int = Field(default=5, ge=1)
    login_rate_limit_window_seconds: float = Field(default=60.0, gt=0)
    # "global_visibility" = mọi human đã xác thực đọc được toàn bộ (Phase 4a).
    authorization_mode: str = "global_visibility"
    # Cửa dev CHỈ cho localhost + APP_ENV=development; mặc định TẮT.
    dev_auth_bypass: bool = False

    @field_validator("auth_algorithm")
    @classmethod
    def _only_hs256_for_human_auth(cls, value: str) -> str:
        # JWT nội bộ của human_auth ký ĐỐI XỨNG bằng auth_signing_secret. Chỉ
        # HS* hợp lệ; RS256 (bất đối xứng) là của đường Entra, không dùng ở đây.
        # Test contract của đội canh đúng điểm này.
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError(f"auth_algorithm phải là HS256/HS384/HS512, nhận '{value}'")
        return value

    @model_validator(mode="after")
    def _global_visibility_is_dev_only(self) -> "Settings":
        # global_visibility mở toàn bộ quyền ĐỌC cho mọi human đã đăng nhập —
        # tiện cho demo/dev, NGUY HIỂM ở production. Chặn ngay tại tầng cấu hình
        # để không ai vô tình bật nó khi deploy thật. Test của đội canh điểm này.
        if self.app_env == "production" and self.authorization_mode == "global_visibility":
            raise ValueError(
                "authorization_mode='global_visibility' không được phép khi app_env='production'"
            )
        return self


    # --- Microsoft Entra ID (CP4) -------------------------------------------
    # KHÔNG hard-code bất cứ giá trị nào dưới đây. Rỗng = đường Entra CHƯA bật
    # (`app/entra.py::entra_configured()`), không phải "mở".
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: SecretStr = SecretStr("")
    entra_redirect_uri: str = ""
    entra_post_logout_redirect_uri: str = ""
    entra_authority_host: str = "https://login.microsoftonline.com"
    # Chỉ đặt khi tenant phát `iss`/`aud` khác mặc định (B2C/CIAM, hoặc IdP giả
    # lập trong test). Rỗng ⇒ suy ra từ tenant/client id.
    entra_issuer: str = ""
    entra_audience: str = ""
    entra_scopes: str = "openid profile email offline_access"
    entra_allowed_algorithms: str = "RS256"
    entra_clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    # JSON {"<app-role|group-id>": "admin|pipeline_operator|business_viewer"}.
    # Không khớp claim nào = KHÔNG có vai trò (403), xem app/session.py.
    entra_role_map: SecretStr = SecretStr("")
    # JSON {"<app-role|group-id>": ["P-0001"] | "ALL"}. Vắng mặt = phạm vi RỖNG.
    entra_project_scope: SecretStr = SecretStr("")

    # --- Phiên đăng nhập (cookie HttpOnly do Mini CRM tự ký) ------------------
    session_secret: SecretStr = SecretStr("")
    session_ttl_seconds: int = Field(default=3600, ge=60)
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"

    # --- Tương thích ngược cho token TĨNH (D-14) -----------------------------
    # MẶC ĐỊNH TẮT. Bật chỉ cho dev/CI hoặc đường máy-với-máy; production dùng
    # Entra. Đây là cờ opt-in TƯỜNG MINH, không phải fallback âm thầm.
    legacy_token_auth_enabled: bool = False

    # --- Liên kết sang Product/AbsorbIQ (CP6) --------------------------------
    # URL gốc của Product Frontend để dựng deep-link "View in AbsorbIQ".
    product_frontend_base_url: str = ""

    # CORS: origin của CRM frontend (cookie phiên cần credentials nên KHÔNG
    # dùng "*" — trình duyệt từ chối wildcard khi allow_credentials=True).
    cors_origins: str = "http://localhost:5174"

    @property
    def database_dsn(self) -> str:
        """DSN dạng chuỗi. KHÔNG log giá trị này — trong đó có mật khẩu."""
        return self.database_url.get_secret_value()

    @property
    def sync_api_key_value(self) -> str:
        return self.sync_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
