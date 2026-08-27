"""XẾP HẠNG V2 — `src/ranking/ahp.py` (hàm thuần) + `POST /ranking/ahp/weights`.

Không cần DB: module toán là hàm thuần, còn endpoint CỐ Ý không ghi gì nên
`ASGITransport` là đủ. Đó cũng là một khẳng định về thiết kế — ngày nào file này
cần `TEST_DATABASE_URL` thì nghĩa là bước tính trọng số đã mọc ra một đường ghi.

Nhắc lại một lần cho người đọc sau: "V2" ở đây là phiên bản CÔNG THỨC (V1 = trọng
số đặt tay ở 0014/0022, V2 = suy ra bằng AHP), KHÔNG phải `ranking_configs.version`.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.ranking.ahp import (
    CR_HARD_LIMIT,
    FORMULA_VERSION,
    AHPError,
    Judgment,
    as_config_weights,
    build_matrix,
    compute,
    round_weights,
    threshold_for,
)
from src.services.ranking_config import validate_weights
from tests.conftest import DASHBOARD_AUTH_HEADER, DASHBOARD_VIEWER_TOKEN

API = "/api/v1/ranking/ahp/weights"

C4 = ["unit_available", "unit_demand_norm", "area_velocity_norm", "area_conversion_norm"]

# Bộ phán đoán tham chiếu, dùng xuyên suốt file. Giá trị kỳ vọng ở
# `test_reference_matrix_*` được tính ĐỘC LẬP bằng số học float trước khi viết
# `ahp.py`, rồi mới đối chiếu — không phải chép lại đầu ra của chính module.
J4 = [
    Judgment("unit_available", "unit_demand_norm", Decimal("2")),
    Judgment("unit_available", "area_velocity_norm", Decimal("3")),
    Judgment("unit_available", "area_conversion_norm", Decimal("3")),
    Judgment("unit_demand_norm", "area_velocity_norm", Decimal("2")),
    Judgment("unit_demand_norm", "area_conversion_norm", Decimal("2")),
    Judgment("area_velocity_norm", "area_conversion_norm", Decimal("1")),
]

SPECS = {
    "unit_available": {"direction": "positive", "missing_value_policy": "zero", "min_confidence": 0.0},
    "unit_demand_norm": {"direction": "positive", "missing_value_policy": "zero", "min_confidence": 0.0},
    "area_velocity_norm": {"direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0.0},
    "area_conversion_norm": {"direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0.0},
}


def _six(dp: Decimal) -> Decimal:
    return dp.quantize(Decimal("0.000001"))


def _payload(judgments: list[tuple[str, str, float]], criteria=None, **extra) -> dict:
    return {
        "criteria": criteria or C4,
        "judgments": [{"a": a, "b": b, "value": v} for a, b, v in judgments],
        "feature_specs": SPECS,
        **extra,
    }


# --- Toán: đối chiếu với giá trị tính độc lập --------------------------------


def test_reference_matrix_weights():
    """Trọng số RGMM của bộ phán đoán tham chiếu."""
    weights = compute(C4, J4).weights
    assert _six(weights["unit_available"]) == Decimal("0.455010")
    assert _six(weights["unit_demand_norm"]) == Decimal("0.262700")
    assert _six(weights["area_velocity_norm"]) == Decimal("0.141145")
    assert _six(weights["area_conversion_norm"]) == Decimal("0.141145")


def test_reference_matrix_consistency():
    """λmax / CI / CR của cùng bộ đó, và nó nằm dưới ngưỡng n=4."""
    result = compute(C4, J4)
    assert _six(result.lambda_max) == Decimal("4.010356")
    assert _six(result.consistency_index) == Decimal("0.003452")
    assert _six(result.consistency_ratio) == Decimal("0.003836")
    assert result.threshold == Decimal("0.08")
    assert result.consistent is True


# Trọng số của CÙNG ma trận tham chiếu, tính bằng PHƯƠNG PHÁP KHÁC: véc-tơ riêng
# chính qua `numpy.linalg.eig` (Saaty gốc). Ghim thành hằng số thay vì gọi numpy
# lúc chạy test — numpy chỉ có mặt nhờ pandas kéo theo, không phải phụ thuộc ta khai.
EIGENVECTOR_REFERENCE = {
    "unit_available": Decimal("0.455408"),
    "unit_demand_norm": Decimal("0.262833"),
    "area_velocity_norm": Decimal("0.140880"),
    "area_conversion_norm": Decimal("0.140880"),
}


def test_rgmm_is_not_interchangeable_with_the_eigenvector_method():
    """RGMM và véc-tơ riêng KHÔNG phải hai cách viết của cùng một con số.

    Test này ghim một sự thật dễ bị hiểu nhầm. Hai phương pháp chỉ TRÙNG khi ma
    trận nhất quán hoàn hảo (xem test kế tiếp). Với ma trận thật — kể cả bộ tham
    chiếu rất nhất quán này, CR = 0.0038 — chúng đã lệch ở chữ số thập phân thứ
    tư, đúng chữ số mà `ranking_configs` lưu.

    Ai đối chiếu kết quả với một bảng tính chạy phương pháp véc-tơ riêng sẽ thấy
    lệch, và đó là hành vi ĐÚNG. Nếu ai đó sau này đổi `derive_weights` sang véc-
    tơ riêng, test này ĐỎ — và đó là tín hiệu phải cập nhật tài liệu công bố,
    không phải một hồi quy cần vá.
    """
    weights = compute(C4, J4).weights
    deltas = {key: abs(weights[key] - EIGENVECTOR_REFERENCE[key]) for key in C4}

    assert max(deltas.values()) > Decimal("0.0001"), (
        "hai phương pháp đang trùng tới 4 chữ số — `derive_weights` có thể đã đổi "
        "sang véc-tơ riêng; cập nhật docstring `ahp.py` trước khi sửa test này"
    )
    assert max(deltas.values()) < Decimal("0.001"), (
        f"RGMM lệch quá xa véc-tơ riêng trên một ma trận rất nhất quán: {deltas}"
    )


def test_perfectly_consistent_matrix_has_zero_cr():
    """Dựng ma trận từ chính một véc-tơ trọng số: a_ij = w_i/w_j.

    Đây là bất biến đại số, không phải giá trị chép tay — ma trận nhất quán
    tuyệt đối BẮT BUỘC cho λmax = n, do đó CI = 0 và CR = 0. Nếu test này đỏ thì
    công thức nhất quán sai, bất kể các con số tham chiếu ở trên có khớp hay không.
    """
    ideal = {"unit_available": Decimal("0.5"), "unit_demand_norm": Decimal("0.3"), "area_velocity_norm": Decimal("0.2")}
    keys = list(ideal)
    judgments = [
        Judgment(keys[i], keys[k], ideal[keys[i]] / ideal[keys[k]])
        for i in range(len(keys))
        for k in range(i + 1, len(keys))
    ]
    result = compute(keys, judgments)
    assert abs(result.consistency_ratio) < Decimal("1e-20")
    assert result.consistent is True
    for key, expected in ideal.items():
        assert _six(result.weights[key]) == _six(expected)


def test_two_criteria_never_divides_by_zero_random_index():
    """RI(2) = 0. Mọi ma trận 2×2 nghịch đảo đều nhất quán tuyệt đối, nên nhánh
    này phải trả CR = 0 chứ không được chạm tới phép chia."""
    result = compute(
        ["unit_available", "unit_demand_norm"], [Judgment("unit_available", "unit_demand_norm", Decimal("7"))]
    )
    assert result.consistency_ratio == Decimal("0")
    assert result.threshold == Decimal("0")
    assert result.consistent is True
    assert result.weights["unit_available"] > result.weights["unit_demand_norm"]


def test_circular_triad_is_wildly_inconsistent():
    """A≫B, B≫C, nhưng C≫A — mâu thuẫn kinh điển, phải vượt xa trần cứng."""
    keys = ["unit_available", "unit_demand_norm", "area_velocity_norm"]
    result = compute(
        keys,
        [
            Judgment(keys[0], keys[1], Decimal("5")),
            Judgment(keys[0], keys[2], Decimal("1") / Decimal("5")),
            Judgment(keys[1], keys[2], Decimal("5")),
        ],
    )
    assert result.consistency_ratio > CR_HARD_LIMIT
    assert result.consistent is False


def test_saaty_thresholds_are_n_dependent():
    """Dùng 0.10 phẳng là nới lỏng SAI cho n nhỏ."""
    assert threshold_for(2) == Decimal("0")
    assert threshold_for(3) == Decimal("0.05")
    assert threshold_for(4) == Decimal("0.08")
    assert threshold_for(5) == Decimal("0.10")
    assert threshold_for(9) == Decimal("0.10")


def test_matrix_is_reciprocal_with_unit_diagonal():
    matrix = build_matrix(C4, J4)
    n = len(C4)
    for i in range(n):
        assert matrix[i][i] == Decimal("1")
        for k in range(n):
            assert abs(matrix[i][k] * matrix[k][i] - Decimal("1")) < Decimal("1e-25")


def test_weights_do_not_depend_on_criteria_order():
    """Đảo thứ tự tiêu chí không được đổi trọng số của từng tiêu chí — nếu có,
    kết quả phụ thuộc thứ tự người dùng gõ, và hai lần nhập cùng phán đoán sẽ ra
    hai bộ trọng số khác nhau."""
    reordered = list(reversed(C4))
    base = compute(C4, J4).weights
    flipped = compute(reordered, J4).weights
    for key in C4:
        assert _six(base[key]) == _six(flipped[key])
    # Phải phủ CẢ trọng số đã làm tròn — đó mới là thứ được phát hành. Kiểm mỗi
    # trọng số thô là bỏ sót việc phá hoà lúc dồn phần dư.
    assert round_weights(base) == round_weights(flipped)


def test_rounding_tie_break_does_not_depend_on_typing_order():
    """Hai trọng số lớn nhất BẰNG NHAU: phần dư phải rơi vào cùng một tiêu chí
    bất kể người dùng gõ thứ tự nào, nếu không cùng một bộ phán đoán sẽ đẻ ra hai
    config khác nhau mà không ai giải thích được."""
    # CÙNG một bộ phán đoán, chỉ đổi thứ tự danh sách `criteria`. `a` và `b` hoà
    # nhau ở đỉnh, nên đây đúng là ca phá hoà.
    judgments = [
        Judgment("a", "b", Decimal("1")),
        Judgment("a", "c", Decimal("3")),
        Judgment("b", "c", Decimal("3")),
    ]
    forward = round_weights(compute(["a", "b", "c"], judgments).weights)
    backward = round_weights(compute(["c", "b", "a"], judgments).weights)
    assert forward == backward, f"trọng số phát hành đổi theo thứ tự gõ: {forward} vs {backward}"


def test_hotspot_points_at_the_most_deviant_judgment():
    top = compute(C4, J4).hotspots[0]
    assert {top.a, top.b} == {"unit_available", "unit_demand_norm"}
    assert top.judged == Decimal("2")
    assert _six(top.implied) == Decimal("1.732051")


# --- Làm tròn: hợp đồng với `validate_weights` -------------------------------


def test_rounding_residual_is_absorbed_so_weights_sum_to_exactly_one():
    """Ca có THẬT, không phải giả định: bộ tham chiếu làm tròn ngây thơ ra
    0.4550 + 0.2627 + 0.1411 + 0.1411 = 0.9999, và `validate_weights` chỉ dung
    sai 1e-9. Phần dư phải được dồn vào trọng số lớn nhất."""
    weights = compute(C4, J4).weights
    naive = sum(w.quantize(Decimal("0.0001")) for w in weights.values())
    assert naive == Decimal("0.9999"), "tiền đề của test đã đổi — ca dồn phần dư không còn được canh"

    rounded = round_weights(weights)
    assert sum(rounded.values()) == Decimal("1.0000")
    assert rounded["unit_available"] == Decimal("0.4551")  # phần dư vào trọng số LỚN NHẤT


def test_rounded_weights_always_sum_to_one_for_random_matrices():
    """Bất biến làm tròn không được phụ thuộc vào bộ phán đoán may mắn nào."""
    rng = random.Random(20260823)
    scale = [Decimal(1) / Decimal(9), Decimal(1) / Decimal(3), Decimal(1), Decimal(2), Decimal(5), Decimal(9)]
    for _ in range(200):
        n = rng.randint(3, 7)
        keys = [f"c{i}" for i in range(n)]
        judgments = [Judgment(keys[i], keys[k], rng.choice(scale)) for i in range(n) for k in range(i + 1, n)]
        assert sum(round_weights(compute(keys, judgments).weights).values()) == Decimal("1.0000")


def test_config_weights_pass_the_real_validate_weights():
    """Hợp đồng tích hợp: đầu ra của AHP phải post thẳng được sang
    `/ranking/configs`. Khẳng định bằng CHÍNH hàm bước đó gọi, không phải bằng
    một bản chép lại luật."""
    config_weights = as_config_weights(compute(C4, J4).weights, SPECS)
    validate_weights(config_weights)  # ném ConfigError nếu sai
    assert config_weights["area_velocity_norm"]["direction"] == "positive"
    assert config_weights["area_velocity_norm"]["missing_value_policy"] == "neutral"


def test_ahp_never_invents_direction_or_missing_policy():
    """AHP cho ĐỘ LỚN. Thiếu `direction`/`missing_value_policy` phải là lỗi, vì
    đoán chúng là âm thầm đảo ngược một tiêu chí."""
    with pytest.raises(AHPError) as exc:
        as_config_weights(compute(C4, J4).weights, {"unit_available": SPECS["unit_available"]})
    assert exc.value.code == "SPEC_MISSING"


# --- Kiểm đầu vào ------------------------------------------------------------


@pytest.mark.parametrize(
    "criteria,judgments,code",
    [
        (["a"], [], "CRITERIA_TOO_FEW"),
        ([f"c{i}" for i in range(11)], [], "CRITERIA_TOO_MANY"),
        (["a", "a"], [Judgment("a", "a", Decimal("1"))], "CRITERIA_DUPLICATE"),
        (["a", "b", "c"], [Judgment("a", "b", Decimal("2"))], "JUDGMENTS_INCOMPLETE"),
        (["a", "b"], [Judgment("a", "z", Decimal("2"))], "JUDGMENT_UNKNOWN_CRITERION"),
        (
            ["a", "b", "c"],
            [Judgment("a", "a", Decimal("1")), Judgment("a", "b", Decimal("2")), Judgment("b", "c", Decimal("2"))],
            "JUDGMENT_SELF",
        ),
        (["a", "b"], [Judgment("a", "b", Decimal("15"))], "JUDGMENT_OUT_OF_SCALE"),
        (["a", "b"], [Judgment("a", "b", Decimal("0.05"))], "JUDGMENT_OUT_OF_SCALE"),
    ],
)
def test_invalid_input_is_rejected_with_a_code(criteria, judgments, code):
    with pytest.raises(AHPError) as exc:
        compute(criteria, judgments)
    assert exc.value.code == code


def test_duplicate_pair_is_rejected_in_either_orientation():
    """Nhập cả a_ij lẫn a_ji là tự mở đường cho mâu thuẫn nghịch đảo — đúng thứ
    mà việc chỉ hỏi tam giác trên sinh ra để loại bỏ."""
    with pytest.raises(AHPError) as exc:
        compute(
            ["a", "b", "c"],
            [Judgment("a", "b", Decimal("2")), Judgment("b", "a", Decimal("3")), Judgment("b", "c", Decimal("2"))],
        )
    assert exc.value.code == "JUDGMENT_DUPLICATE"


def test_saaty_reciprocal_arriving_as_a_json_float_is_accepted():
    """Hồi quy: `Decimal(str(1/9))` có 16 chữ số còn `Decimal(1)/Decimal(9)` có
    28, nên so sánh biên thẳng tay TỪ CHỐI nghịch đảo 1/9 hợp lệ."""
    result = compute(["a", "b"], [Judgment("a", "b", Decimal(str(1 / 9)))])
    assert result.weights["b"] > result.weights["a"]


# --- Endpoint ----------------------------------------------------------------


async def _post(body: dict, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else DASHBOARD_AUTH_HEADER
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        return await client.post(API, json=body)


@pytest.mark.asyncio
async def test_endpoint_returns_config_ready_weights():
    response = await _post(_payload([(j.a, j.b, float(j.value)) for j in J4]))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["formula_version"] == FORMULA_VERSION
    assert body["consistent"] is True
    assert body["override_applied"] is False
    assert sum(spec["weight"] for spec in body["weights"].values()) == pytest.approx(1.0, abs=1e-9)
    assert body["weights"]["unit_available"]["weight"] == pytest.approx(0.4551)
    # `note` là nơi DUY NHẤT còn giữ các phán đoán gốc — mất nó thì AHP chỉ thay
    # bốn con số không giải thích được bằng bốn con số khác cũng vậy.
    assert FORMULA_VERSION in body["note"]
    assert "unit_available>unit_demand_norm=2" in body["note"]


@pytest.mark.asyncio
async def test_endpoint_output_is_accepted_by_validate_weights():
    """Endpoint trả 200 thì kết quả CHẮC CHẮN tạo được config — không có chuyện
    qua bước này rồi bước sau mới báo tổng ≠ 1.0."""
    response = await _post(_payload([(j.a, j.b, float(j.value)) for j in J4]))
    validate_weights(response.json()["weights"])


MID_BAND = [
    ("unit_available", "unit_demand_norm", 0.2),
    ("unit_available", "area_velocity_norm", 0.2),
    ("unit_available", "area_conversion_norm", 0.2),
    ("unit_demand_norm", "area_velocity_norm", 0.2),
    ("unit_demand_norm", "area_conversion_norm", 0.2),
    ("area_velocity_norm", "area_conversion_norm", 1.0),
]


@pytest.mark.asyncio
async def test_cr_above_threshold_is_refused_without_override():
    response = await _post(_payload(MID_BAND))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "CR_ABOVE_THRESHOLD"
    # Từ chối phải DÙNG ĐƯỢC: chỉ đúng ô nào lệch, không chỉ ném ra một con số.
    assert detail["hotspots"], "từ chối mà không kèm hotspot thì người dùng không biết sửa gì"


@pytest.mark.asyncio
async def test_override_without_a_reason_is_refused():
    response = await _post(_payload(MID_BAND, override=True, override_reason="   "))
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "OVERRIDE_REASON_REQUIRED"


@pytest.mark.asyncio
async def test_override_with_a_reason_passes_and_records_it_in_the_note():
    reason = "Đợt đẩy hàng Q3 — giám đốc kinh doanh chấp nhận"
    response = await _post(_payload(MID_BAND, override=True, override_reason=reason))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["consistent"] is False
    assert body["override_applied"] is True
    assert reason in body["note"], "lý do vượt ngưỡng phải đi vào vết kiểm toán, nếu không override là vô hình"


@pytest.mark.asyncio
async def test_hard_limit_has_no_override_path():
    """Trên trần cứng thì không có đường vòng — kể cả kèm lý do."""
    keys = ["unit_available", "unit_demand_norm", "area_velocity_norm"]
    body = _payload(
        [(keys[0], keys[1], 5.0), (keys[0], keys[2], 0.2), (keys[1], keys[2], 5.0)],
        criteria=keys,
        override=True,
        override_reason="cứ cho qua đi",
    )
    response = await _post(body)
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "CR_HARD_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_unknown_feature_is_refused():
    """Cùng lý do với `validate_weights`: khoá không bộ tính nào sản xuất sẽ luôn
    MISSING, và bảng xếp hạng sai mà không có lỗi nào bật lên."""
    keys = ["unit_available", "mau_son_ban_cong"]
    response = await _post(_payload([(keys[0], keys[1], 3.0)], criteria=keys))
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "UNKNOWN_FEATURE"


@pytest.mark.asyncio
async def test_incomplete_matrix_is_refused_by_the_endpoint():
    response = await _post(_payload([(j.a, j.b, float(j.value)) for j in J4[:3]]))
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "JUDGMENTS_INCOMPLETE"


@pytest.mark.asyncio
async def test_setting_ranking_weights_requires_admin():
    """Ai được đặt trọng số thì mới được suy ra trọng số — cùng vai với việc
    soạn config."""
    response = await _post(_payload([(j.a, j.b, float(j.value)) for j in J4]), token=DASHBOARD_VIEWER_TOKEN)
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_non_numeric_min_confidence_is_a_422_not_a_500():
    """`float("abc")` ném `ValueError`. Không bắt nó thì một lỗi NHẬP LIỆU trả về
    500 — người dùng tưởng hệ thống hỏng, còn log thì đầy stack trace vô nghĩa."""
    specs = {key: dict(spec) for key, spec in SPECS.items()}
    specs["unit_available"]["min_confidence"] = "abc"
    body = _payload([(j.a, j.b, float(j.value)) for j in J4])
    body["feature_specs"] = specs
    response = await _post(body)
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "FEATURE_SPEC_INVALID"


@pytest.mark.asyncio
async def test_note_records_judgments_that_can_be_replayed():
    """`note` là bản ghi DUY NHẤT của phán đoán gốc — nó phải NẠP LẠI được.

    Hồi quy: định dạng `:g` giữ 6 chữ số, nên nghịch đảo 1/9 vào note thành
    "0.111111", và nạp lại chuỗi đó thì `validate` từ chối vì ngoài thang. Một
    vết kiểm toán không tái lập được thì không phải vết kiểm toán.
    """
    keys = ["unit_available", "unit_demand_norm"]
    response = await _post(_payload([(keys[0], keys[1], 1 / 9)], criteria=keys))
    assert response.status_code == 200, response.text

    recorded = response.json()["note"].split("So sánh cặp: ")[1].rstrip(".")
    replayed = []
    for entry in recorded.split("; "):
        pair, _, value = entry.partition("=")
        left, _, right = pair.partition(">")
        replayed.append(Judgment(left, right, Decimal(value)))

    again = compute(keys, replayed)  # ném AHPError nếu note không tái lập được
    assert again.weights[keys[1]] > again.weights[keys[0]]
