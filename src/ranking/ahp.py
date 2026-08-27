"""XẾP HẠNG V2 — trọng số suy ra bằng AHP (Saaty) từ so sánh cặp.

Dòng đời công thức xếp hạng:

    V1  trọng số do kỹ sư ĐẶT TAY      (0014, rồi 0022)
    V2  trọng số SUY RA từ phán đoán chuyên gia, kèm bằng chứng nhất quán  ← đây

╔══════════════════════════════════════════════════════════════════════════════╗
║  "V2" ở đây là phiên bản CÔNG THỨC, KHÔNG phải `ranking_configs.version`.    ║
╚══════════════════════════════════════════════════════════════════════════════╝

Hai trục đếm khác nhau và sẽ lệch nhau ngay lập tức. `ranking_configs.version`
là bộ đếm chỉ-tăng của từng bộ trọng số được soạn: 0014 ghi version 1, 0022 ghi
version 2 (đang phát hành). Bộ trọng số AHP ĐẦU TIÊN vì thế sẽ nằm ở
`ranking_configs` **version 3** — dù nó là công thức **V2**. Docstring của
0022 gọi bản thân nó là "Config v2"; đó là trục kia, không mâu thuẫn.

Module này KHÔNG xếp hạng căn nào. Nó trả lời đúng một câu hỏi mà V1 để ngỏ:
bốn con số `0.35 / 0.25 / 0.20 / 0.20` ở `0022_ranking_config_v2.py` từ đâu ra?
Dưới V1 câu trả lời là "kỹ sư chọn". Dưới V2 là "chuyên gia so sánh từng cặp
tiêu chí, và CR chứng minh các phán đoán đó nhất quán".

V1 và V2 dùng CHUNG một bộ máy tính điểm: `src/ranking/engine.py` không đổi một
dòng nào, vì phép tổng có trọng số ở đó chính là bước tổng hợp của AHP. Khác
biệt duy nhất giữa hai công thức là trọng số ở đâu ra. Đó là toàn bộ phạm vi
của file này.

╔══════════════════════════════════════════════════════════════════════════════╗
║  Hàm THUẦN — không I/O, không mạng, không DB. Cùng ràng buộc với engine.py.  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Cố ý KHÔNG import `src.services.ranking_config` (nó kéo theo `src/db.py`). Việc
kiểm khoá đặc trưng có nằm trong `KNOWN_FEATURES` hay không thuộc về tầng API.

## Công thức

Ma trận so sánh cặp `A` với `a_ij` = "tiêu chí i quan trọng gấp mấy lần j" trên
thang Saaty 1–9, `a_ji = 1/a_ij`, `a_ii = 1`. Chỉ TAM GIÁC TRÊN được hỏi:
`n(n-1)/2` câu, không phải `n²` — người dùng không thể tự mâu thuẫn kiểu
"A hơn B" và đồng thời "B hơn A".

**Trọng số — Row Geometric Mean Method (RGMM):**

    w_i = (∏_j a_ij)^(1/n)   rồi chuẩn hoá để Σw = 1

Saaty gốc dùng véc-tơ riêng chính. Ở đây CỐ Ý dùng RGMM, ba lý do theo thứ tự
quan trọng:

1. Nó là biểu thức đóng — không giải trị riêng lặp, không LAPACK, nên kết quả
   GIỐNG HỆT trên mọi máy. `ranking_configs` là hồ sơ kiểm toán; sáu tháng sau
   phải suy lại được đúng con số cũ.
2. Không cần thêm phụ thuộc. `requirements.txt` không có numpy/scipy.
3. Hợp với kỷ luật `Decimal` mà `engine.py` đã theo.

**RGMM KHÔNG phải xấp xỉ của véc-tơ riêng — đừng coi hai bên là thay thế được.**
Chúng TRÙNG NHAU tuyệt đối chỉ khi ma trận nhất quán hoàn hảo (đo được: lệch
1.1e-16, tức sai số máy). Ngoài điểm đó chúng tách ra, và tách nhanh hơn nhiều
so với trực giác — đo trên 60k ma trận ngẫu nhiên, đối chiếu `numpy.linalg.eig`:

    dải CR        lệch trọng số TB    lệch LỚN NHẤT
    ≤ 0.01            1.3e-05            1.6e-03
    0.05 – 0.08       2.8e-03            1.8e-02
    0.08 – 0.10       6.0e-03            2.6e-02

Ngay bộ phán đoán tham chiếu của dự án (CR = 0.0038) hai bên đã lệch ~4e-04 —
tức LỚN HƠN chữ số thập phân thứ 4 mà ta lưu. Gần ngưỡng n=4 thì lệch tới ~2
điểm phần trăm trọng số, đủ để đảo thứ tự vài căn.

Chọn RGMM vẫn là quyết định CÓ CHỦ Ý (nó là ước lượng hợp lý cực đại dưới sai số
log-chuẩn, Crawford & Williams 1985), nhưng con số công bố phải nói đúng nó là
RGMM. Ai cần khớp với một bảng tính chạy phương pháp véc-tơ riêng sẽ thấy lệch ở
chữ số thứ tư, và đó là hành vi ĐÚNG, không phải lỗi. `test_ahp.py` ghim quan hệ
này lại để không ai lỡ coi hai phương pháp là một.

Căn bậc n tính bằng `exp(Σ ln(a_ij) / n)` — `Decimal` có sẵn `.ln()`/`.exp()`
được làm tròn đúng, nên vẫn tất định.

**Nhất quán:**

    λmax = (1/n) · Σ_i (A·w)_i / w_i
    CI   = (λmax − n) / (n − 1)
    CR   = CI / RI(n)

`RI` là bảng chỉ số ngẫu nhiên của Saaty. Ngưỡng KHÔNG phải 0.10 phẳng: n=3 →
0.05, n=4 → 0.08, n≥5 → 0.10. Với n ≤ 2 thì RI = 0 — mọi ma trận 2×2 nghịch đảo
đều nhất quán tuyệt đối, nên CR ≡ 0 và phải chặn TRƯỚC phép chia.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Thang Saaty: 1 = ngang nhau ... 9 = vượt trội tuyệt đối, kèm nghịch đảo.
SAATY_MAX = Decimal("9")
SAATY_MIN = Decimal("1") / Decimal("9")

# Dung sai khi kiểm biên thang đo — cùng lý do với `WEIGHT_SUM_TOLERANCE` ở
# `ranking_config.py`: giá trị đi qua JSON dưới dạng float. Nghịch đảo 1/9 tới
# nơi là `0.1111111111111111` (16 chữ số) trong khi `Decimal(1)/Decimal(9)` có
# 28 chữ số, nên so sánh thẳng sẽ TỪ CHỐI một phán đoán hoàn toàn hợp lệ.
SCALE_TOLERANCE = Decimal("1e-9")

# Chỉ số ngẫu nhiên (Saaty). n ≤ 2 để 0 và được chặn riêng — không bao giờ chia cho nó.
RANDOM_INDEX: dict[int, Decimal] = {
    1: Decimal("0"),
    2: Decimal("0"),
    3: Decimal("0.58"),
    4: Decimal("0.90"),
    5: Decimal("1.12"),
    6: Decimal("1.24"),
    7: Decimal("1.32"),
    8: Decimal("1.41"),
    9: Decimal("1.45"),
    10: Decimal("1.49"),
}
MAX_CRITERIA = max(RANDOM_INDEX)

# Trần cứng của CR. Cổng ba mức được CƯỠNG CHẾ ở `src/api/ahp.py`, không ở đây:
# module này chỉ ĐO độ nhất quán, còn "có chấp nhận hay không" là chính sách.
# Dưới `threshold_for(n)` thì qua; từ đó tới trần này thì cần override kèm lý do;
# trên trần thì từ chối hẳn.
CR_HARD_LIMIT = Decimal("0.20")

# Trọng số ghi vào `ranking_configs` ở 4 chữ số thập phân, khớp `score` của engine.
WEIGHT_EXPONENT = Decimal("0.0001")

# Số hotspot trả về khi phán đoán lệch — đủ để sửa, không đủ để làm ngợp.
HOTSPOT_LIMIT = 3

# Nhãn dòng đời CÔNG THỨC, đi kèm mọi bộ trọng số do module này sinh ra để sau
# này đọc `ranking_configs.note` là biết ngay bộ đó do người đặt tay (V1) hay
# suy ra từ so sánh cặp (V2). Lại nhắc: khác `ranking_configs.version`.
FORMULA_VERSION = "V2-AHP"


class AHPError(ValueError):
    """Cùng hình dạng với `ConfigError`: có `code` để API ánh xạ thẳng ra lỗi."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Judgment:
    """Một ô của tam giác TRÊN: `a` quan trọng gấp `value` lần `b`."""

    a: str
    b: str
    value: Decimal


@dataclass(frozen=True)
class Hotspot:
    """Phán đoán lệch xa nhất khỏi trọng số suy ra — để người dùng biết sửa ô nào."""

    a: str
    b: str
    judged: Decimal
    implied: Decimal
    deviation: Decimal


@dataclass(frozen=True)
class AHPResult:
    weights: dict[str, Decimal]
    lambda_max: Decimal
    consistency_index: Decimal
    consistency_ratio: Decimal
    threshold: Decimal
    consistent: bool
    hotspots: list[Hotspot]


def threshold_for(n: int) -> Decimal:
    """Ngưỡng CR của Saaty phụ thuộc n. Dùng 0.10 phẳng là nới lỏng sai cho n nhỏ."""
    if n <= 2:
        return Decimal("0")
    if n == 3:
        return Decimal("0.05")
    if n == 4:
        return Decimal("0.08")
    return Decimal("0.10")


def validate(criteria: list[str], judgments: list[Judgment]) -> None:
    """Chặn mọi ma trận không dựng được, TRƯỚC khi tính.

    Ma trận thiếu ô hay lặp ô vẫn cho ra một con số trông hợp lệ nếu ta âm thầm
    điền 1 vào chỗ trống — và con số đó là trọng số SAI mà không có lỗi nào bật
    lên. Cùng kiểu hỏng mà `validate_weights` ở `ranking_config.py` được viết ra
    để chặn.
    """
    if len(criteria) < 2:
        raise AHPError("CRITERIA_TOO_FEW", "Cần ít nhất 2 tiêu chí để so sánh cặp")
    if len(criteria) > MAX_CRITERIA:
        raise AHPError(
            "CRITERIA_TOO_MANY",
            f"Tối đa {MAX_CRITERIA} tiêu chí — bảng RI của Saaty không định nghĩa quá mức này",
        )
    if any(not isinstance(key, str) or not key.strip() for key in criteria):
        raise AHPError("CRITERION_BLANK", "Tên tiêu chí không được rỗng")
    if len(set(criteria)) != len(criteria):
        raise AHPError("CRITERIA_DUPLICATE", "Danh sách tiêu chí có phần tử trùng")

    n = len(criteria)
    expected = n * (n - 1) // 2
    if len(judgments) != expected:
        raise AHPError(
            "JUDGMENTS_INCOMPLETE",
            f"{n} tiêu chí cần đúng {expected} so sánh (tam giác trên), nhận được {len(judgments)}",
        )

    known = set(criteria)
    seen: set[frozenset[str]] = set()
    for j in judgments:
        if j.a not in known or j.b not in known:
            raise AHPError("JUDGMENT_UNKNOWN_CRITERION", f"So sánh '{j.a}' vs '{j.b}' nhắc tiêu chí lạ")
        if j.a == j.b:
            raise AHPError("JUDGMENT_SELF", f"'{j.a}' không thể tự so sánh với chính nó")
        pair = frozenset((j.a, j.b))
        if pair in seen:
            # Cả hai chiều đều tính là TRÙNG: nhập cả a_ij và a_ji là tự mở đường
            # cho mâu thuẫn nghịch đảo, thứ mà tam giác trên sinh ra để loại bỏ.
            raise AHPError("JUDGMENT_DUPLICATE", f"Cặp '{j.a}'/'{j.b}' được nhập nhiều lần")
        seen.add(pair)
        if not (SAATY_MIN - SCALE_TOLERANCE) <= j.value <= (SAATY_MAX + SCALE_TOLERANCE):
            raise AHPError(
                "JUDGMENT_OUT_OF_SCALE",
                f"Giá trị so sánh '{j.a}'/'{j.b}' = {j.value} nằm ngoài thang Saaty [1/9, 9]",
            )


def build_matrix(criteria: list[str], judgments: list[Judgment]) -> list[list[Decimal]]:
    """Dựng ma trận đầy đủ từ tam giác trên: đường chéo = 1, nửa dưới = nghịch đảo."""
    n = len(criteria)
    index = {key: i for i, key in enumerate(criteria)}
    matrix = [[Decimal("1") for _ in range(n)] for _ in range(n)]
    for j in judgments:
        i, k = index[j.a], index[j.b]
        matrix[i][k] = j.value
        matrix[k][i] = Decimal("1") / j.value
    return matrix


def derive_weights(criteria: list[str], matrix: list[list[Decimal]]) -> dict[str, Decimal]:
    """RGMM: trung bình nhân từng hàng, rồi chuẩn hoá.

    Tính qua `exp(Σ ln / n)` thay vì luỹ thừa phân số vì `Decimal` không có căn
    bậc n, còn `ln`/`exp` thì có và được làm tròn đúng — giữ tính tất định.
    """
    n = len(criteria)
    means: list[Decimal] = []
    for row in matrix:
        log_sum = sum((value.ln() for value in row), Decimal("0"))
        means.append((log_sum / n).exp())
    total = sum(means, Decimal("0"))
    return {key: means[i] / total for i, key in enumerate(criteria)}


def consistency(
    criteria: list[str], matrix: list[list[Decimal]], weights: dict[str, Decimal]
) -> tuple[Decimal, Decimal, Decimal]:
    """Trả (λmax, CI, CR). Với n ≤ 2 trả thẳng (n, 0, 0) — RI = 0, không được chia."""
    n = len(criteria)
    if n <= 2:
        return Decimal(n), Decimal("0"), Decimal("0")

    weighted_sums = [sum((matrix[i][k] * weights[criteria[k]] for k in range(n)), Decimal("0")) for i in range(n)]
    lambda_max = sum((weighted_sums[i] / weights[criteria[i]] for i in range(n)), Decimal("0")) / n
    index = (lambda_max - n) / (n - 1)
    ratio = index / RANDOM_INDEX[n]
    return lambda_max, index, ratio


def find_hotspots(criteria: list[str], matrix: list[list[Decimal]], weights: dict[str, Decimal]) -> list[Hotspot]:
    """Phán đoán nào lệch xa nhất khỏi trọng số suy ra, đo bằng |ln a_ij − ln(w_i/w_j)|.

    "CR = 0.31, mời nhập lại" là một lời từ chối không dùng được. "Bạn chấm A hơn
    B gấp 2, nhưng các câu trả lời còn lại của bạn hàm ý 1.73" thì sửa được.
    """
    n = len(criteria)
    spots: list[Hotspot] = []
    for i in range(n):
        for k in range(i + 1, n):
            judged = matrix[i][k]
            implied = weights[criteria[i]] / weights[criteria[k]]
            spots.append(
                Hotspot(
                    a=criteria[i],
                    b=criteria[k],
                    judged=judged,
                    implied=implied,
                    deviation=abs(judged.ln() - implied.ln()),
                )
            )
    # Phá hoà bằng tên tiêu chí để thứ tự trả về là tất định khi độ lệch bằng nhau.
    spots.sort(key=lambda s: (-s.deviation, s.a, s.b))
    return spots[:HOTSPOT_LIMIT]


def compute(criteria: list[str], judgments: list[Judgment]) -> AHPResult:
    """Đường chính: phán đoán vào, trọng số + báo cáo nhất quán ra."""
    validate(criteria, judgments)
    matrix = build_matrix(criteria, judgments)
    weights = derive_weights(criteria, matrix)
    lambda_max, index, ratio = consistency(criteria, matrix, weights)
    threshold = threshold_for(len(criteria))
    return AHPResult(
        weights=weights,
        lambda_max=lambda_max,
        consistency_index=index,
        consistency_ratio=ratio,
        threshold=threshold,
        consistent=ratio <= threshold,
        hotspots=find_hotspots(criteria, matrix, weights),
    )


def round_weights(weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Làm tròn 4 chữ số RỒI dồn phần dư vào trọng số lớn nhất.

    Không phải chuyện lý thuyết. Ví dụ có thật với 4 tiêu chí của config v2 cho
    ra 0.4550 + 0.2627 + 0.1411 + 0.1411 = 0.9999, còn `validate_weights` chỉ
    dung sai 1e-9 quanh 1.0 — làm tròn ngây thơ sẽ TRƯỢT ở bước cuối với những
    phán đoán hoàn toàn hợp lệ.

    Dồn vào trọng số LỚN NHẤT vì ở đó sai lệch tương đối là nhỏ nhất.

    Hoà thì lấy khoá NHỎ NHẤT THEO TÊN, cố ý KHÔNG lấy khoá gõ trước. Phá hoà
    theo vị trí nghe có vẻ vô hại nhưng làm bộ trọng số PHÁT HÀNH phụ thuộc thứ
    tự người dùng gõ tiêu chí: cùng một bộ phán đoán, gõ [a,b,c] cho a=0.4285
    b=0.4286, gõ [c,b,a] cho a=0.4286 b=0.4285. Hai lần nhập giống hệt nhau ra
    hai config khác nhau, và không ai giải thích được vì sao.
    """
    rounded = {key: value.quantize(WEIGHT_EXPONENT, rounding=ROUND_HALF_UP) for key, value in weights.items()}
    residual = Decimal("1") - sum(rounded.values(), Decimal("0"))
    if residual != 0:
        peak = max(rounded.values())
        rounded[min(key for key, value in rounded.items() if value == peak)] += residual
    return rounded


def as_config_weights(weights: dict[str, Decimal], specs: dict[str, dict]) -> dict[str, dict]:
    """Ghép trọng số AHP với `direction` / `missing_value_policy` để ra `ranking_configs.weights`.

    AHP cho ĐỘ LỚN, không bao giờ cho CHIỀU. `direction` và
    `missing_value_policy` là quyết định riêng, phải do người gọi đưa vào — suy
    đoán chúng ở đây là âm thầm đảo ngược một tiêu chí.
    """
    missing = sorted(set(weights) - set(specs))
    if missing:
        raise AHPError(
            "SPEC_MISSING",
            f"Thiếu direction/missing_value_policy cho {missing} — AHP không suy ra được các trường này",
        )
    rounded = round_weights(weights)
    return {
        key: {
            "weight": float(rounded[key]),
            "direction": specs[key]["direction"],
            "missing_value_policy": specs[key]["missing_value_policy"],
            "min_confidence": float(specs[key].get("min_confidence", 0) or 0),
        }
        for key in weights
    }
