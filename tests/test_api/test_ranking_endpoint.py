"""`GET /api/v1/ranking` + `POST /api/v1/ranking/run` — đường ĐỌC của Phase 6.

Dùng lại nguyên bộ dữ liệu đã dựng cho `tests/test_agent_e2e.py` (5 căn, điểm
tính TAY, xem docstring file đó) thay vì dựng một bộ thứ hai: hai bộ dữ liệu cho
cùng một công thức sẽ lệch nhau ngay lần đầu ai đó sửa trọng số, và khi đó không
biết bên nào mới là bên sai.

    u1 0.5900 · u2 0.8400 · u3 0.5900 · u4 0.2400 (đã bán) · u5 0.5900

Bốn điểm được canh:

1. **`GET` KHÔNG tính lại.** Gọi hai lần phải ra cùng `computed_at`. Nếu một lần
   đọc cũng ghi, hai người mở trang cùng lúc sẽ ghi đè điểm của nhau.
2. **`POST /ranking/run` cần vai trò cao hơn `GET`.** Nó thay thế TOÀN BỘ
   `ranking_scores` của dự án, không phải làm mới bộ nhớ đệm.
3. **`POST /ranking/run` KHÔNG tạo `agent_recommendations`.** Xem bảng xếp hạng
   mà đẻ ra một đề xuất chờ duyệt là làm loãng chính vòng duyệt của AGENTS.md.
4. **`band_counts` không bị chính bộ lọc `band` thu hẹp** — nếu có, chọn một mức
   sẽ làm số đếm trên các chip mức khác tụt về 0 ngay khi người dùng bấm vào.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.main import app
from src.models.tables import agent_recommendations, ranking_scores, units
from tests.conftest import (
    DASHBOARD_ADMIN_TOKEN,
    DASHBOARD_OPERATOR_TOKEN,
    DASHBOARD_VIEWER_TOKEN,
    db_skip_reason,
)
from tests.ranking_fixture import PROJECT_ID, UNIT_IDS, _insert_config, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

API = "/api/v1/ranking"
PROJECT = "P-AGENT-TEST-1"
AREA = "A-AGENT-TEST-1"
ADMIN_HEADER = {"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}
OPERATOR_HEADER = {"Authorization": f"Bearer {DASHBOARD_OPERATOR_TOKEN}"}
# `tests/conftest.py` CỐ Ý không cấp phạm vi dự án nào cho token viewer — nó là
# token dùng để kiểm đúng nhánh "ngoài phạm vi", không phải một viewer hợp lệ.
VIEWER_HEADER = {"Authorization": f"Bearer {DASHBOARD_VIEWER_TOKEN}"}


@pytest_asyncio.fixture
async def http(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    for target in (
        "src.api.ranking.get_session_factory",
        "src.ranking.service.get_session_factory",
    ):
        monkeypatch.setattr(target, lambda factory=factory: factory, raising=False)

    await _insert_config(factory)
    await _insert_dataset(factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory  # type: ignore[attr-defined]
        yield client


async def _run(http, **params):
    return await http.post(
        f"{API}/run", params={"external_project_id": PROJECT, **params}, headers=ADMIN_HEADER
    )


# --- Xác thực và phân quyền --------------------------------------------------


async def test_unauthenticated_read_is_rejected(http):
    response = await http.get(API, params={"external_project_id": PROJECT})
    assert response.status_code == 401


async def test_reading_does_not_require_admin(http):
    """Đọc bảng xếp hạng là việc của đội bán hàng, không phải đặc quyền admin."""
    await _run(http)
    response = await http.get(API, params={"external_project_id": PROJECT}, headers=OPERATOR_HEADER)
    assert response.status_code == 200


async def test_a_token_without_this_project_in_scope_is_403_not_404(http):
    """403 chứ không phải 404: "không có quyền" và "không tồn tại" là hai sự
    thật khác nhau, và giao diện phải hiện hai thông báo khác nhau
    (`src/services/dashboard_auth.py::require_project_in_scope`)."""
    response = await http.get(API, params={"external_project_id": PROJECT}, headers=VIEWER_HEADER)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


def test_recompute_requires_a_higher_role_than_reading():
    """Kiểm THẲNG bậc vai trò thay vì qua HTTP.

    Bản trước của test này gọi `POST /ranking/run` bằng token viewer rồi khẳng
    định 403 — nhưng token viewer của conftest KHÔNG có phạm vi dự án nào, nên nó
    nhận 403 vì SAI PHẠM VI, không phải vì thiếu vai trò. Test đó vẫn xanh kể cả
    khi cổng vai trò bị gỡ bỏ hoàn toàn: một khẳng định đúng vì lý do sai.

    Cùng khuôn với `tests/test_ranking_boundary.py::
    test_approving_requires_a_higher_role_than_read_only_viewing`.
    """
    from src.api.ranking import require_operator, require_viewer
    from src.services.dashboard_auth import _ROLE_LEVEL

    viewer_minimum = require_viewer.__closure__[0].cell_contents
    operator_minimum = require_operator.__closure__[0].cell_contents
    assert _ROLE_LEVEL[operator_minimum] > _ROLE_LEVEL[viewer_minimum]


# --- Chưa từng xếp hạng KHÁC đã xếp hạng nhưng rỗng --------------------------


async def test_a_project_never_ranked_reports_null_computed_at(http):
    """Giao diện phải phân biệt "chưa chạy lần nào" với "chạy rồi mà không có
    căn nào" — nên đây là một trường riêng, không phải một danh sách rỗng."""
    response = await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)
    body = response.json()
    assert response.status_code == 200
    assert body["computed_at"] is None
    assert body["ranking_run_id"] is None
    assert body["state"] == "not_run"
    assert body["reason"] == "RANKING_NOT_RUN"
    assert body["items"] == []
    assert body["units_ranked"] == 0


async def test_completed_run_with_no_live_units_is_not_reported_as_never_run(http):
    """Tombstone là chính sách loại trừ nguồn hiện hành. Run vẫn phải để lại
    audit metadata và lý do máy-đọc, thay vì làm UI hiểu nhầm là chưa chạy."""
    async with http.session_factory() as session:
        await session.execute(sa.update(units).values(deleted_at=sa.func.now()))
        await session.commit()

    response = await _run(http)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "insufficient_data"
    assert body["reason"] == "NO_LIVE_UNITS"
    assert body["ranking_run_id"] is not None
    assert body["computed_at"] is not None
    assert body["items"] == []
    assert body["units_ranked"] == 0


async def test_unknown_project_is_404(http):
    response = await http.get(API, params={"external_project_id": "KHONG-CO"}, headers=ADMIN_HEADER)
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"


# --- Nội dung xếp hạng -------------------------------------------------------


async def test_run_then_read_returns_units_ordered_by_rank(http):
    assert (await _run(http)).status_code == 200

    body = (await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)).json()

    assert body["units_ranked"] == 5
    assert sum(body["band_counts"].values()) == body["total"]
    assert body["config_version"] == 2
    assert body["computed_at"] is not None
    ranks = [item["rank_in_project"] for item in body["items"]]
    assert ranks == sorted(ranks), "phải trả về theo đúng thứ hạng, không phụ thuộc thứ tự của Postgres"
    assert body["items"][0]["unit_id"] == str(UNIT_IDS["u2"]), "u2 có nhu cầu cao nhất nên phải đứng đầu"


async def test_scores_match_the_hand_computed_values(http):
    """Cùng con số với `tests/test_agent_e2e.py` — đường đọc không được biến đổi
    điểm trên đường ra."""
    await _run(http)
    body = (await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)).json()
    by_unit = {item["unit_id"]: item for item in body["items"]}

    assert by_unit[str(UNIT_IDS["u2"])]["score"] == "0.8400"
    assert by_unit[str(UNIT_IDS["u1"])]["score"] == "0.5900"
    assert by_unit[str(UNIT_IDS["u4"])]["score"] == "0.2400"
    assert by_unit[str(UNIT_IDS["u2"])]["score_percent"] == 84.0
    assert by_unit[str(UNIT_IDS["u2"])]["band"] == "high"
    assert by_unit[str(UNIT_IDS["u4"])]["band"] == "low"


async def test_contributions_are_returned_and_sum_to_the_score(http):
    """Phần "vì sao" phải CỘNG LẠI đúng bằng điểm — nếu không, giao diện đang
    giải thích một con số khác với con số nó hiển thị."""
    await _run(http)
    body = (await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)).json()
    unit = next(item for item in body["items"] if item["unit_id"] == str(UNIT_IDS["u2"]))

    assert {c["feature_key"] for c in unit["contributions"]} == {
        "unit_available", "unit_demand_norm", "area_velocity_norm", "area_conversion_norm",
    }
    total = sum(float(c["contribution"]) for c in unit["contributions"])
    assert abs(total - float(unit["score"])) < 1e-4

    ordered = [float(c["contribution"]) for c in unit["contributions"]]
    assert ordered == sorted(ordered, reverse=True), "đóng góp phải giảm dần, để hai màn hình không hiện hai thứ tự"


async def test_every_response_carries_the_fixed_disclaimer(http):
    body = (await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)).json()
    assert "không phải cam kết" in body["disclaimer"]


# --- Bộ lọc và phân trang ----------------------------------------------------


async def test_band_filter_narrows_items_but_not_the_counts(http):
    await _run(http)
    all_units = (await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)).json()
    high_only = (
        await http.get(API, params={"external_project_id": PROJECT, "band": "high"}, headers=ADMIN_HEADER)
    ).json()

    assert all(item["band"] == "high" for item in high_only["items"])
    assert high_only["total"] < all_units["total"]
    # Số đếm phải GIỮ NGUYÊN — nó mô tả phạm vi đang xem, không phải bộ lọc mức.
    assert high_only["band_counts"] == all_units["band_counts"]


async def test_invalid_band_is_422(http):
    response = await http.get(
        API, params={"external_project_id": PROJECT, "band": "cực-cao"}, headers=ADMIN_HEADER
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "INVALID_BAND"


async def test_unit_status_filter_keeps_only_sellable_units(http):
    await _run(http)
    body = (
        await http.get(
            API, params={"external_project_id": PROJECT, "unit_status": "available"}, headers=ADMIN_HEADER
        )
    ).json()
    assert body["items"], "phải còn căn nào đó"
    assert all(item["unit_status"] == "available" for item in body["items"])
    assert str(UNIT_IDS["u4"]) not in {item["unit_id"] for item in body["items"]}
    assert sum(body["band_counts"].values()) == body["total"]


async def test_area_filter_scopes_to_one_area(http):
    await _run(http)
    body = (
        await http.get(
            API, params={"external_project_id": PROJECT, "external_area_id": AREA}, headers=ADMIN_HEADER
        )
    ).json()
    assert len(body["items"]) == 5


async def test_unknown_area_is_404(http):
    response = await http.get(
        API, params={"external_project_id": PROJECT, "external_area_id": "KHONG-CO"}, headers=ADMIN_HEADER
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "AREA_NOT_FOUND"


async def test_pagination_walks_the_whole_set_without_repeating(http):
    await _run(http)
    seen: list[str] = []
    for offset in (0, 2, 4):
        page = (
            await http.get(
                API,
                params={"external_project_id": PROJECT, "limit": 2, "offset": offset},
                headers=ADMIN_HEADER,
            )
        ).json()
        assert page["total"] == 5
        seen.extend(item["unit_id"] for item in page["items"])
    assert len(seen) == 5
    assert len(set(seen)) == 5, "phân trang không được lặp lại căn nào"


# --- Ranh giới của đường đọc -------------------------------------------------


async def test_reading_twice_does_not_recompute(http):
    """`GET` phải THUẦN ĐỌC: `computed_at` không được đổi giữa hai lượt đọc."""
    await _run(http)
    first = (await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)).json()
    second = (await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)).json()
    assert first["computed_at"] == second["computed_at"]


async def test_recompute_replaces_scores_instead_of_appending(http):
    """`_persist_scores` xoá-rồi-chèn. Chạy hai lần mà số dòng nhân đôi nghĩa là
    bảng điểm đã có hai thế hệ sống chung."""
    await _run(http)
    await _run(http)
    async with http.session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(ranking_scores).where(ranking_scores.c.project_id == PROJECT_ID)
        )
    assert count == 5


async def test_recompute_creates_no_recommendation_awaiting_approval(http):
    """Bước duyệt người của AGENTS.md gắn với KHUYẾN NGHỊ, không gắn với một
    phép tính lại tất định."""
    await _run(http)
    async with http.session_factory() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(agent_recommendations))
    assert count == 0
