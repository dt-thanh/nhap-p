"""`Settings.sync_api_key_value`: ưu tiên file bí mật Compose, fallback env.

Mini CRM cần khoá `X-API-Key` thô lúc gửi relay. Trước đây nó luôn đọc từ
`MINICRM_SYNC_API_KEY` (biến môi trường, từ `.env`) — nhưng `.env` không tự
đồng bộ được với credential mà AbsorpIQ vừa cấp khi chạy bootstrap TRONG
container (xem `scripts/bootstrap_dev.py`/`scripts/dev-reset.sh`). Đường mới:
file bí mật Compose tại `/run/secrets/minicrm_sync_api_key`, mount qua khối
`secrets:` trong `docker-compose.yml` — không cần copy tay, không lộ qua
`docker inspect`/biến môi trường.

Test ở đây KHÔNG cần database — chỉ kiểm logic đọc file/fallback/lỗi cấu hình
của `app.config`.
"""

from __future__ import annotations

import pytest
from app import config as config_module
from app.config import Settings, _read_sync_api_key_secret_file
from pydantic import SecretStr


def _settings(*, sync_api_key: str = "") -> Settings:
    """`Settings` tối giản, không đọc `.env` thật — tránh phụ thuộc môi trường CI."""
    return Settings(_env_file=None, sync_api_key=SecretStr(sync_api_key))


# --- đọc file trực tiếp -------------------------------------------------------


def test_secret_file_reader_returns_none_when_file_missing(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert _read_sync_api_key_secret_file(missing) is None


def test_secret_file_reader_strips_only_the_trailing_newline(tmp_path):
    path = tmp_path / "key"
    path.write_text("afsk_realkey123\n")
    assert _read_sync_api_key_secret_file(path) == "afsk_realkey123"


def test_secret_file_reader_strips_only_one_trailing_newline_not_internal_whitespace(tmp_path):
    path = tmp_path / "key"
    # Không có khoá thật nào chứa khoảng trắng — đây chỉ kiểm ta không cắt quá tay.
    path.write_text("afsk_ab cd\n")
    assert _read_sync_api_key_secret_file(path) == "afsk_ab cd"


def test_secret_file_reader_returns_none_for_an_empty_file(tmp_path):
    path = tmp_path / "key"
    path.write_text("")
    assert _read_sync_api_key_secret_file(path) is None


def test_secret_file_reader_returns_none_for_a_file_with_only_a_newline(tmp_path):
    path = tmp_path / "key"
    path.write_text("\n")
    assert _read_sync_api_key_secret_file(path) is None


# --- sync_api_key_value: ưu tiên file, fallback env, lỗi rõ ràng -------------


def test_sync_api_key_value_prefers_the_secret_file_over_env(tmp_path, monkeypatch):
    path = tmp_path / "minicrm_sync_api_key"
    path.write_text("afsk_from_file\n")
    monkeypatch.setattr(config_module, "SYNC_API_KEY_SECRET_FILE", path)

    settings = _settings(sync_api_key="afsk_from_env_should_be_ignored")

    assert settings.sync_api_key_value == "afsk_from_file"


def test_sync_api_key_value_falls_back_to_env_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "SYNC_API_KEY_SECRET_FILE", tmp_path / "does-not-exist")

    settings = _settings(sync_api_key="afsk_from_env")

    assert settings.sync_api_key_value == "afsk_from_env"


def test_sync_api_key_value_falls_back_to_env_when_file_is_empty(tmp_path, monkeypatch):
    path = tmp_path / "minicrm_sync_api_key"
    path.write_text("\n")
    monkeypatch.setattr(config_module, "SYNC_API_KEY_SECRET_FILE", path)

    settings = _settings(sync_api_key="afsk_from_env")

    assert settings.sync_api_key_value == "afsk_from_env"


def test_sync_api_key_value_raises_a_clear_error_when_neither_source_is_set(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "SYNC_API_KEY_SECRET_FILE", tmp_path / "does-not-exist")

    settings = _settings(sync_api_key="")

    with pytest.raises(RuntimeError, match="Thiếu sync API key"):
        settings.sync_api_key_value


# --- không rò rỉ khoá thô ------------------------------------------------------


def test_sync_api_key_value_error_message_never_contains_a_key_value(tmp_path, monkeypatch):
    """Khi CÓ khoá (từ env) nhưng file bị hỏng theo cách khác (vd. thư mục thay vì
    file), lỗi nếu có không được lặp lại khoá thô trong thông điệp."""
    monkeypatch.setattr(config_module, "SYNC_API_KEY_SECRET_FILE", tmp_path / "does-not-exist")
    settings = _settings(sync_api_key="")

    with pytest.raises(RuntimeError) as exc_info:
        settings.sync_api_key_value

    assert "afsk_" not in str(exc_info.value)


def test_settings_repr_never_contains_the_raw_sync_api_key():
    """`SecretStr` che giá trị trong `repr()`/`str()` theo mặc định của pydantic —
    kiểm lại tường minh vì đây là hàng rào cuối trước khi vô tình log cả object."""
    settings = _settings(sync_api_key="afsk_should_never_appear_in_repr")

    assert "afsk_should_never_appear_in_repr" not in repr(settings)
    assert "afsk_should_never_appear_in_repr" not in str(settings.sync_api_key)
