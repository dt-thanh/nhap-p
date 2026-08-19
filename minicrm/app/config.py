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

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINICRM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Mini CRM (synthetic)"
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
