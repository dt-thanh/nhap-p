"""`DEV_AUTH_BYPASS` + `RUN_MIGRATIONS`: mặc định an toàn, và bị chặn khi kết
hợp sai (Phase: đóng lỗ hổng docker-compose.yml hardcode `"true"`).

`dashboard_auth.authenticate_dashboard` đã tự gác `dev_auth_bypass` bằng điều
kiện `app_env == "development"` (xem `src/services/dashboard_auth.py`), nhưng
đó là một chốt LÚC CHẠY: tiến trình vẫn khởi động bình thường với cờ bypass
nằm chờ, và chỉ cần `APP_ENV` bị đổi (hoặc bỏ trống — mặc định rơi về
"production") là bật lại mà không ai hay. `Settings` giờ có thêm một chốt LÚC
KHỞI ĐỘNG: từ chối tạo `Settings` nếu tổ hợp này xuất hiện, thay vì âm thầm bỏ
qua.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _settings(**overrides) -> Settings:
    # Không qua `.env`/biến môi trường thật: constructor trực tiếp cô lập test
    # khỏi bất kỳ giá trị nào tình cờ có trong môi trường chạy test.
    return Settings(_env_file=None, **overrides)


def test_development_with_bypass_true_is_allowed():
    settings = _settings(app_env="development", dev_auth_bypass=True)
    assert settings.dev_auth_bypass is True
    assert settings.app_env == "development"


def test_development_with_bypass_false_is_allowed():
    settings = _settings(app_env="development", dev_auth_bypass=False)
    assert settings.dev_auth_bypass is False


@pytest.mark.parametrize("app_env", ["staging", "production", "test"])
def test_non_development_with_bypass_true_is_rejected(app_env):
    with pytest.raises(ValidationError, match="DEV_AUTH_BYPASS=true"):
        _settings(app_env=app_env, dev_auth_bypass=True)


def test_default_app_env_with_bypass_true_is_rejected(monkeypatch):
    """Không truyền `app_env` → mặc định "production" (xem field). Cờ bypass
    bật trong tổ hợp đó phải bị chặn giống hệt production tường minh.

    Release-hardening pass: `_settings()` đã cô lập khỏi FILE `.env` bằng
    `_env_file=None`, nhưng KHÔNG cô lập khỏi biến MÔI TRƯỜNG THẬT —
    `scripts/test_db.sh` tự nạp toàn bộ `.env` (kể cả `APP_ENV=development`)
    thành biến môi trường TRƯỚC khi gọi pytest, và pydantic-settings đọc biến
    môi trường ở độ ưu tiên CAO HƠN giá trị mặc định của field. Test này là
    test DUY NHẤT trong file cố tình không truyền `app_env` (để kiểm giá trị
    mặc định "production"), nên nó là test DUY NHẤT bị lộ — mọi test khác đều
    tự truyền `app_env=...` tường minh, thắng biến môi trường nên không sao.
    `monkeypatch.delenv` xoá đúng biến bị lộ, tự khôi phục sau test."""
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(ValidationError, match="DEV_AUTH_BYPASS=true"):
        _settings(dev_auth_bypass=True)


@pytest.mark.parametrize("app_env", ["development", "staging", "production", "test"])
def test_bypass_false_is_always_allowed_regardless_of_environment(app_env):
    settings = _settings(app_env=app_env, dev_auth_bypass=False)
    assert settings.dev_auth_bypass is False


def test_settings_default_bypass_is_false():
    """Giá trị mặc định của field, không truyền gì — đóng, không phải mở."""
    settings = _settings()
    assert settings.dev_auth_bypass is False


# --- docker-compose.yml: không hardcode "true" cho hai cờ này ---------------


def _api_service_block() -> str:
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"\n  api:\n(.*?)\n  worker:\n", text, re.DOTALL)
    assert match, "không tìm thấy khối service 'api' trong docker-compose.yml"
    return match.group(1)


def test_compose_dev_auth_bypass_defaults_closed():
    block = _api_service_block()
    assert 'DEV_AUTH_BYPASS: "${DEV_AUTH_BYPASS:-false}"' in block


def test_compose_run_migrations_defaults_closed():
    block = _api_service_block()
    assert 'RUN_MIGRATIONS: "${RUN_MIGRATIONS:-false}"' in block
    # Không còn dòng hardcode "true" trần cho hai cờ này trong khối api:.
    assert 'DEV_AUTH_BYPASS: "true"' not in block
    assert 'RUN_MIGRATIONS: "true"' not in block


# --- OIDC_ROLE_MAP: không được định nghĩa lại CRM.CEO/CRM.ADVISOR/CRM.SALES ---
#
# Các khoá này là vai trò nghiệp vụ chuẩn, dùng chung với Mini CRM
# (`src/services/oidc.py::CANONICAL_APP_ROLES`) — cố định trong code, để một
# `OIDC_ROLE_MAP` cấu hình sai không thể âm thầm đổi ý nghĩa "CEO".


def test_role_map_empty_is_allowed():
    settings = _settings(oidc_role_map="")
    assert settings.oidc_role_map.get_secret_value() == ""


def test_role_map_with_unrelated_keys_is_allowed():
    settings = _settings(oidc_role_map=json.dumps({"custom.group": "admin", "Some.Group.Id": "business_viewer"}))
    assert settings.oidc_role_map.get_secret_value()


@pytest.mark.parametrize(
    ("key", "canonical_value"),
    [
        ("CRM.CEO", "admin"),
        ("CRM.Admin", "admin"),
        ("CRM.ADVISOR", "business_viewer"),
        ("CRM.Viewer", "business_viewer"),
        ("CRM.SALES", "pipeline_operator"),
        ("CRM.Operator", "pipeline_operator"),
    ],
)
def test_role_map_matching_the_canonical_value_is_allowed(key, canonical_value):
    settings = _settings(oidc_role_map=json.dumps({key: canonical_value}))
    assert settings.oidc_role_map.get_secret_value()


@pytest.mark.parametrize(
    ("key", "wrong_value"),
    [
        ("CRM.CEO", "business_viewer"),
        ("CRM.Admin", "business_viewer"),
        ("CRM.ADVISOR", "admin"),
        ("CRM.Viewer", "admin"),
        ("CRM.SALES", "admin"),
        ("CRM.Operator", "admin"),
    ],
)
def test_role_map_redefining_a_canonical_key_is_rejected_at_startup(key, wrong_value):
    with pytest.raises(ValidationError, match=key):
        _settings(oidc_role_map=json.dumps({key: wrong_value}))


def test_role_map_malformed_json_does_not_crash_settings():
    """Cấu hình sai KHÔNG được là một cách để chặn tiến trình khởi động qua một
    ngoại lệ không liên quan — `resolve_role`/`_json_setting` vốn đã coi JSON
    hỏng là rỗng, validator này giữ đúng ứng xử đó."""
    settings = _settings(oidc_role_map="not valid json{{{")
    assert settings.oidc_role_map.get_secret_value() == "not valid json{{{"


# --- AUTH_PROVIDER: chỉ "keycloak" được chấp nhận -----------------------------


def test_auth_provider_defaults_to_keycloak():
    assert _settings().auth_provider == "keycloak"


def test_auth_provider_rejects_unsupported_value():
    """Không còn Entra để chọn — một giá trị lạ (kể cả "entra") bị Pydantic từ
    chối ngay lúc khởi động, không bao giờ âm thầm bật một đường xác thực khác."""
    with pytest.raises(ValidationError):
        _settings(auth_provider="entra")
