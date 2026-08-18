"""Đầu dò sức khoẻ của Mini CRM, và ranh giới cô lập ở mức mã nguồn.

Test cô lập nằm ở đây chứ không ở backend vì đây là bên có nghĩa vụ giữ ranh giới:
Mini CRM là bên mới, và là bên dễ "tiện tay" import model của backend nhất.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.main import app
from fastapi.testclient import TestClient

MINICRM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MINICRM_ROOT.parent


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_declares_the_source_instance_the_backend_will_see(client):
    """`source_instance_id` là ranh giới cô lập ở phía backend, và khoá API bị
    buộc vào đúng giá trị này. Phơi nó ra để người vận hành đối chiếu được."""
    body = client.get("/health").json()
    assert body["source_instance_id"]


def test_health_carries_the_synthetic_disclaimer(client):
    """Một Mini CRM có service và endpoint trông thuyết phục hơn hẳn một file JSON.

    Nhãn phải đi cùng SẢN PHẨM, không nằm trong tài liệu — ai mở service này lên
    cũng phải thấy ngay đây là hạ tầng tổng hợp do chính dự án viết.
    """
    disclaimer = client.get("/health").json()["disclaimer"]
    assert "TỔNG HỢP" in disclaimer
    assert "KHÔNG phải CRM của khách hàng" in disclaimer


def test_minicrm_never_imports_backend_modules():
    """RANH GIỚI CÔ LẬP. `minicrm/` không được import `src/`.

    Import chéo biến hai hệ thống thành một hệ thống có hai giao diện, và mọi bảo
    đảm cô lập khác (database riêng, Alembic riêng, cấu hình riêng) thành lời hứa
    suông — vì lúc đó Mini CRM không còn triển khai độc lập được nữa.
    """
    # So theo TỪNG DÒNG và đòi dòng BẮT ĐẦU bằng câu import. Tìm theo chuỗi con
    # trên cả file sẽ khiến chính test này khớp với chuỗi nó đang tìm.
    #
    # Chuỗi được GHÉP thay vì viết thẳng, và đây không phải trò khéo tay: phép
    # kiểm cô lập của quy trình là `grep -rn "from src\." minicrm/` phải trả về
    # RỖNG. Viết thẳng chuỗi đó vào đây làm chính file này thành một kết quả khớp,
    # và người vận hành sẽ phải học cách bỏ qua một dòng cảnh báo — thói quen đó
    # rồi sẽ bỏ qua luôn một lần import thật.
    _src = "src"
    markers = (f"from {_src}.", f"import {_src}.", f"from {_src} import")
    offenders = []
    for path in MINICRM_ROOT.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip().startswith(markers):
                offenders.append(f"{path.relative_to(MINICRM_ROOT)}:{number}")
    assert offenders == [], f"minicrm/ đang import backend: {offenders}"


def test_backend_never_imports_minicrm():
    """Chiều ngược lại. Backend CHỈ NHẬN request HTTP; nó không biết Mini CRM tồn tại.

    Backend gọi vào Mini CRM sẽ đảo chiều phụ thuộc và biến một hệ nguồn thành một
    thành phần của hệ thống nhận — đúng thứ mà mọi phase trước đã tránh.
    """
    src = REPO_ROOT / "src"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in src.rglob("*.py")
        if "minicrm" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"src/ đang tham chiếu minicrm: {offenders}"


def test_config_uses_the_minicrm_prefix(monkeypatch):
    """Tiền tố `MINICRM_` là thứ ngăn Mini CRM đọc trúng cấu hình của backend.

    Đặt `DATABASE_URL` (biến của backend) rồi kiểm rằng Mini CRM KHÔNG nhặt nó.
    """
    from app.config import Settings

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://backend:backend@db:5432/AbsorptionForecast")
    monkeypatch.setenv("MINICRM_DATABASE_URL", "postgresql+asyncpg://minicrm:minicrm@minicrm_db:5432/minicrm")

    settings = Settings(_env_file=None)
    assert "minicrm" in settings.database_dsn
    assert "AbsorptionForecast" not in settings.database_dsn
