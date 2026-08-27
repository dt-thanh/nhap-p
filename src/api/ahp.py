"""XẾP HẠNG V2 — endpoint suy ra trọng số bằng AHP.

Đúng MỘT endpoint, và nó KHÔNG ghi gì cả.

╔══════════════════════════════════════════════════════════════════════════════╗
║  Router này không thêm nơi ghi nào vào bốn bảng xếp hạng — nó không ghi gì.  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Cố ý dừng ở "trả về trọng số" thay vì tự tạo luôn bản nháp config. `ranking_configs`
ĐÃ có sẵn đường soạn–phát hành–quay lui với đúng một nơi ghi
(`src/services/ranking_config.py`, xem `tests/test_ranking_boundary.py`). Mở
thêm một đường ghi thứ hai chỉ để đỡ cho người dùng một lần gọi API là đánh đổi
một bất biến thật lấy một tiện nghi nhỏ.

Luồng đầy đủ, ba hành động NGƯỜI tách bạch:

    1. POST /ranking/ahp/weights      → chuyên gia nhập so sánh cặp, nhận trọng số
    2. POST /ranking/configs          → người duyệt tạo bản nháp (đã có sẵn)
    3. POST /ranking/configs/{v}/publish → phát hành + xếp hàng tính lại (đã có sẵn)

Bước 1 không chạm database. AGENTS.md coi bước duyệt của người là bắt buộc, và
cách rẻ nhất để không bao giờ lỡ tay bỏ qua nó là bước tính toán không có khả
năng ghi ngay từ đầu.

`weights` trả về đã đi qua `validate_weights` — chính hàm mà bước 2 sẽ gọi. Nên
nếu endpoint này trả 200 thì kết quả CHẮC CHẮN post được sang `/ranking/configs`,
không có chuyện tổng trọng số lệch 1.0 ở bước sau (xem `round_weights`).
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from src.logging_config import get_logger
from src.models.schemas import AHPHotspotOut, AHPWeightsIn, AHPWeightsOut
from src.ranking.ahp import (
    CR_HARD_LIMIT,
    FORMULA_VERSION,
    AHPError,
    Judgment,
    as_config_weights,
    compute,
)
from src.services.dashboard_auth import DashboardPrincipal, require_role
from src.services.ranking_config import KNOWN_FEATURES, ConfigError, validate_weights

router = APIRouter(tags=["ranking"])
# Cùng vai với việc soạn config: ai được đặt trọng số thì được suy ra trọng số.
require_admin = require_role("admin")
log = get_logger("src.api.ahp")


def _fail(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, "error_code": code, **extra})


def _hotspots_out(result) -> list[AHPHotspotOut]:
    return [
        AHPHotspotOut(
            a=spot.a,
            b=spot.b,
            judged=str(spot.judged),
            implied=f"{spot.implied:.4f}",
            deviation=f"{spot.deviation:.4f}",
        )
        for spot in result.hotspots
    ]


def _build_note(payload: AHPWeightsIn, judgments: list[Judgment], result, override_applied: bool) -> str:
    """Vết kiểm toán soạn sẵn, để người dùng dán vào `note` khi tạo config.

    Đây là chỗ các PHÁN ĐOÁN GỐC được lưu lại. Không có nó thì AHP chỉ thay bốn
    con số không giải thích được bằng bốn con số khác cũng không giải thích
    được — `ranking_configs.note` là nơi duy nhất còn ghi được "vì sao 0.4551".
    """
    # Ghi lại chính `Decimal` ĐÃ ĐEM ĐI TÍNH, không phải float gốc rút gọn bằng
    # `:g`. `:g` giữ 6 chữ số nên nghịch đảo 1/9 vào note thành "0.111111", và
    # nạp lại chuỗi đó thì `validate` TỪ CHỐI vì ngoài thang. Note là bản ghi
    # DUY NHẤT của các phán đoán gốc — một bản ghi không tái lập được thì vô dụng.
    pairs = "; ".join(f"{j.a}>{j.b}={j.value.normalize()}" for j in judgments)
    note = (
        f"Ranking {FORMULA_VERSION} — trọng số SUY RA bằng AHP (Saaty), không đặt tay. "
        f"CR={result.consistency_ratio:.4f} (ngưỡng {result.threshold}, n={len(payload.criteria)}). "
        f"So sánh cặp: {pairs}."
    )
    if override_applied:
        note += f" CHẤP NHẬN VƯỢT NGƯỠNG — lý do: {payload.override_reason.strip()}"
    return note


@router.post(
    "/ranking/ahp/weights",
    response_model=AHPWeightsOut,
    summary="Suy ra trọng số xếp hạng từ so sánh cặp (AHP) — không ghi gì",
)
async def compute_ahp_weights(
    payload: AHPWeightsIn,
    principal: DashboardPrincipal = Depends(require_admin),
) -> AHPWeightsOut:
    """So sánh cặp vào, trọng số + bằng chứng nhất quán ra.

    Cổng CR ba mức: dưới ngưỡng thì qua; từ ngưỡng tới 0.20 thì phải có
    `override` kèm lý do, và lý do đó đi thẳng vào `note`; trên 0.20 thì từ chối
    hẳn, không có đường vòng. Mức giữa tồn tại vì một giám đốc kinh doanh thật
    dưới áp lực thời gian sẽ có lúc hơi thiếu nhất quán — và một lần vượt ngưỡng
    được GHI LẠI thì tốt hơn nhiều so với việc họ sửa liều vài con số cho qua cổng.
    """
    unknown = sorted(set(payload.criteria) - KNOWN_FEATURES)
    if unknown:
        # Cùng lý do với `validate_weights`: một khoá không bộ tính nào sản xuất
        # sẽ luôn MISSING, và bảng xếp hạng sai mà không có lỗi nào bật lên.
        raise _fail(
            422,
            "UNKNOWN_FEATURE",
            f"{unknown} không phải đặc trưng nào hệ thống tính được. Đã biết: {sorted(KNOWN_FEATURES)}",
        )

    judgments = [Judgment(a=j.a, b=j.b, value=Decimal(str(j.value))) for j in payload.judgments]
    try:
        result = compute(payload.criteria, judgments)
    except AHPError as exc:
        raise _fail(422, exc.code, exc.message) from exc

    override_applied = False
    if not result.consistent:
        shared = {
            "consistency_ratio": f"{result.consistency_ratio:.4f}",
            "threshold": str(result.threshold),
            "hotspots": [spot.model_dump() for spot in _hotspots_out(result)],
        }
        if result.consistency_ratio > CR_HARD_LIMIT:
            raise _fail(
                422,
                "CR_HARD_LIMIT_EXCEEDED",
                f"CR={result.consistency_ratio:.4f} vượt giới hạn cứng {CR_HARD_LIMIT}. "
                "Các phán đoán mâu thuẫn tới mức trọng số suy ra không còn nghĩa — sửa lại, không có override.",
                **shared,
            )
        if not payload.override:
            raise _fail(
                422,
                "CR_ABOVE_THRESHOLD",
                f"CR={result.consistency_ratio:.4f} vượt ngưỡng {result.threshold}. "
                "Sửa các so sánh lệch nhất (xem hotspots), hoặc gửi lại với override=true kèm lý do.",
                **shared,
            )
        if not payload.override_reason.strip():
            raise _fail(422, "OVERRIDE_REASON_REQUIRED", "override=true bắt buộc kèm override_reason", **shared)
        override_applied = True

    try:
        config_weights = as_config_weights(result.weights, payload.feature_specs)
    except AHPError as exc:
        raise _fail(422, exc.code, exc.message) from exc
    except (KeyError, TypeError, ValueError) as exc:
        # `ValueError` KHÔNG thừa: `float(spec["min_confidence"])` ném nó khi giá
        # trị là chuỗi không phải số, và thiếu nó thì `min_confidence: "abc"` trả
        # 500 thay vì 422 — một lỗi NHẬP LIỆU bị báo thành lỗi máy chủ.
        raise _fail(422, "FEATURE_SPEC_INVALID", f"feature_specs sai định dạng: {exc}") from exc

    # Kiểm bằng CHÍNH hàm mà bước tạo config sẽ chạy. Nếu ở đây qua thì ở đó
    # cũng qua — không có chuyện trả 200 rồi bước sau mới báo tổng ≠ 1.0.
    try:
        validate_weights(config_weights)
    except ConfigError as exc:
        raise _fail(422, exc.code, f"Trọng số suy ra không hợp lệ cho config: {exc.message}") from exc

    log.info(
        "ranking.ahp.weights_computed",
        formula_version=FORMULA_VERSION,
        criteria=len(payload.criteria),
        consistency_ratio=f"{result.consistency_ratio:.4f}",
        override_applied=override_applied,
        actor=principal.role,
    )
    return AHPWeightsOut(
        formula_version=FORMULA_VERSION,
        weights=config_weights,
        raw_weights={key: str(value) for key, value in result.weights.items()},
        lambda_max=f"{result.lambda_max:.6f}",
        consistency_index=f"{result.consistency_index:.6f}",
        consistency_ratio=f"{result.consistency_ratio:.6f}",
        threshold=str(result.threshold),
        consistent=result.consistent,
        override_applied=override_applied,
        hotspots=_hotspots_out(result),
        note=_build_note(payload, judgments, result, override_applied),
    )
