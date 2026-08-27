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

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Đường dẫn Compose `secrets:` mount vào MỌI container có khai secret đó —
# cố định theo quy ước Docker, không cấu hình được qua biến môi trường. Xem
# `docker-compose.yml` (khối `secrets:` + `services.minicrm.secrets`) và
# `scripts/dev-reset.sh`.
SYNC_API_KEY_SECRET_FILE = Path("/run/secrets/minicrm_sync_api_key")


def _read_sync_api_key_secret_file(path: Path | None = None) -> str | None:
    """Đọc khoá từ file bí mật Compose. `None` nếu file không tồn tại hoặc rỗng
    sau khi cắt newline cuối — KHÔNG cắt khoảng trắng bên trong, chỉ cắt đúng
    newline mà `docker compose` (hoặc `printf`) có thể để lại ở cuối file.

    `path=None` đọc lại biến MODULE `SYNC_API_KEY_SECRET_FILE` NGAY LÚC GỌI
    (không phải lúc định nghĩa hàm) — cố ý, để test monkeypatch được nó. Một
    default parameter kiểu `path: Path = SYNC_API_KEY_SECRET_FILE` sẽ bind
    GIÁ TRỊ ban đầu một lần duy nhất lúc nạp module, và monkeypatch sau đó sẽ
    không có tác dụng gì với hàm này — đúng lỗi đã bắt được khi viết test.
    """
    if path is None:
        path = SYNC_API_KEY_SECRET_FILE
    try:
        raw = path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None
    value = raw.rstrip("\n")
    return value or None

# Phải khớp `app/session.py::CANONICAL_APP_ROLES` — trùng lặp CÓ CHỦ Ý (không
# import: `config.py` không được phụ thuộc vào tầng service) để canh đúng một
# việc: MINICRM_OIDC_ROLE_MAP không được định nghĩa lại ba khoá vai trò chuẩn
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
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")

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
    # nguyên vẹn ở đây — Keycloak (bên dưới) là hệ THỨ HAI sống song song, không
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
    # Ngưỡng rộng hơn login: logout hợp lệ có thể bị gọi lặp (nhiều tab, nút bấm
    # kép) mà không phải tấn công — nhưng vẫn cần một trần cứng để một client
    # không dội hàng nghìn request thu hồi vào Redis + endpoint revoke của
    # Keycloak mỗi giây.
    logout_rate_limit_attempts: int = Field(default=20, ge=1)
    logout_rate_limit_window_seconds: float = Field(default=60.0, gt=0)
    # "global_visibility" = mọi human đã xác thực đọc được toàn bộ (Phase 4a).
    authorization_mode: str = "global_visibility"
    # Cửa dev CHỈ cho localhost + APP_ENV=development; mặc định TẮT.
    dev_auth_bypass: bool = False

    @field_validator("auth_algorithm")
    @classmethod
    def _only_hs256_for_human_auth(cls, value: str) -> str:
        # JWT nội bộ của human_auth ký ĐỐI XỨNG bằng auth_signing_secret. Chỉ
        # HS* hợp lệ; RS256 (bất đối xứng) là của đường Keycloak, không dùng ở
        # đây. Test contract của đội canh đúng điểm này.
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

    @model_validator(mode="after")
    def _reject_conflicting_canonical_role_map(self) -> "Settings":
        """CRM.CEO/CRM.ADVISOR/CRM.SALES là vai trò nghiệp vụ chuẩn, dùng chung
        với Product/AbsorbIQ (`app/session.py::CANONICAL_APP_ROLES`) — cố định
        trong code. Nếu `MINICRM_OIDC_ROLE_MAP` cố tình gán một trong ba khoá
        này cho một vai trò nội bộ KHÁC, đó là một cấu hình mâu thuẫn: từ chối
        ngay lúc khởi động thay vì để `resolve_role` phải âm thầm quyết định bên
        nào thắng.
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
                    f"MINICRM_OIDC_ROLE_MAP đặt '{key}' thành '{configured_value}', nhưng đây là "
                    f"vai trò chuẩn cố định trong code ('{canonical_value}'). Xoá khoá này khỏi "
                    "MINICRM_OIDC_ROLE_MAP, hoặc dùng đúng giá trị chuẩn."
                )
        return self

    # --- Auth provider -----------------------------------------------------
    # Chỉ Keycloak được hỗ trợ ở runtime. Khai báo TƯỜNG MINH — không còn Entra
    # để chọn — để một biến môi trường thất lạc/kiểu cũ không bao giờ có thể ÂM
    # THẦM bật một đường xác thực khác: giá trị nào khác "keycloak" bị Pydantic
    # từ chối ngay lúc khởi động.
    auth_provider: Literal["keycloak"] = "keycloak"

    # --- OIDC / Keycloak (SSO) -----------------------------------------------
    # Tiền tố env MINICRM_ (env_prefix). ISSUER = URL CÔNG KHAI/front-channel;
    # INTERNAL_BASE_URL = URL back-channel qua Docker DNS. Xem app/oidc.py.
    # KHÔNG hard-code — rỗng nghĩa là đường đăng nhập người dùng TẮT (503 rõ
    # ràng ở route auth, không phải "mở").
    oidc_issuer: str = ""
    oidc_internal_base_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""
    oidc_post_logout_redirect_uri: str = ""
    oidc_scopes: str = "openid profile email offline_access"
    oidc_audience: str = ""
    oidc_allowed_algorithms: str = "RS256"
    oidc_clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    # JSON {"<claim|group-id>": "admin|pipeline_operator|business_viewer"}.
    # Không khớp claim nào = KHÔNG có vai trò (403), xem app/session.py.
    oidc_role_map: SecretStr = SecretStr("")
    # JSON {"<claim|group-id>": ["P-0001"] | "ALL"}. Vắng mặt = phạm vi RỖNG.
    oidc_project_scope: SecretStr = SecretStr("")

    # --- Phiên đăng nhập (cookie HttpOnly do Mini CRM tự ký) ------------------
    session_secret: SecretStr = SecretStr("")
    session_ttl_seconds: int = Field(default=3600, ge=60)
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"

    # --- Tương thích ngược cho token TĨNH (D-14) -----------------------------
    # MẶC ĐỊNH TẮT. Bật chỉ cho dev/CI hoặc đường máy-với-máy; production dùng
    # Keycloak. Đây là cờ opt-in TƯỜNG MINH, không phải fallback âm thầm.
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
        """Khoá `X-API-Key` gửi sang AbsorpIQ.

        Ưu tiên file bí mật Compose (`/run/secrets/minicrm_sync_api_key`,
        mount READ-ONLY qua `secrets:` — không lộ qua `docker inspect`, không
        nằm trong biến môi trường container nên không lộ qua
        `docker compose config`/log biến môi trường). `MINICRM_SYNC_API_KEY`
        (env, từ `.env`) là đường TƯƠNG THÍCH NGƯỢC — dùng khi chạy host-mode
        hoặc test, nơi không có Compose secrets. Thiếu cả hai -> lỗi cấu hình
        RÕ RÀNG ngay lúc gọi, không âm thầm gửi khoá rỗng (chuỗi rỗng vẫn là
        `str`, sẽ lọt qua mọi kiểm `is None`).
        """
        from_file = _read_sync_api_key_secret_file()
        if from_file is not None:
            return from_file
        from_env = self.sync_api_key.get_secret_value()
        if from_env:
            return from_env
        raise RuntimeError(
            "Thiếu sync API key: không thấy /run/secrets/minicrm_sync_api_key "
            "và MINICRM_SYNC_API_KEY cũng rỗng. Chạy `make dev-reset` (hoặc "
            "`./scripts/dev-reset.sh --yes`) để cấp credential cho dev cục bộ, "
            "hoặc đặt MINICRM_SYNC_API_KEY thủ công khi chạy ngoài Compose."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
