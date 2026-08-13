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
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    cors_origins: str = "http://localhost:3000"

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

    # Database
    database_url: SecretStr = SecretStr("postgresql+asyncpg://app:app@db:5432/absorption")

    # Queue & scheduler
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    forecast_cron: str = "0 2 * * *"  # 02:00 hằng ngày (SRS §2.4)
    scheduler_timezone: str = "Asia/Ho_Chi_Minh"

    # Auth (MVP 3)
    jwt_secret: SecretStr = SecretStr("change-me-in-env")
    jwt_algorithm: str = "HS256"
    access_token_ttl: int = 1800  # 30 phút
    refresh_token_ttl: int = 604800  # 7 ngày

    # Ingestion & nghiệp vụ
    upload_max_size: int = 20 * 1024 * 1024  # 20 MB
    upload_dir: str = "./uploads"
    alert_threshold_days: int = 30

    @property
    def resolved_llm_api_key(self) -> str:
        return self.llm_api_key.get_secret_value() or self.openai_api_key.get_secret_value()

    @property
    def resolved_llm_model(self) -> str:
        return self.llm_model or self.model_name or "gpt-4o-mini"

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
