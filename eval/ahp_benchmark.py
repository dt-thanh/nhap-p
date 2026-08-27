"""Benchmark công thức xếp hạng V2 (AHP) đối chiếu V1 và hai mốc so sánh.

    python -m eval.ahp_benchmark

Không DB, không mạng, không LLM. Đầu vào là ma trận đặc trưng ĐÃ XUẤT ở
`datasets/synthetic_v1/exports/units_ranking.csv` (200 căn × 4 đặc trưng), nên
chạy lại lúc nào cũng ra đúng một kết quả — cùng tinh thần TC-02 của
`eval/results/report.md` ("cùng input ⇒ cùng output").

╔══════════════════════════════════════════════════════════════════════════════╗
║  Chấm điểm bằng CHÍNH `src/ranking/engine.py`, không viết lại công thức.     ║
╚══════════════════════════════════════════════════════════════════════════════╝

Viết lại phép tổng có trọng số ở đây sẽ tạo bản sao thứ hai của cùng một quy
tắc, và benchmark sẽ đo BẢN SAO chứ không đo hệ thống thật — đúng cái bẫy mà
`src/api/ranking.py` đã từ chối khi không dịch ngưỡng mức sang SQL. Mức
(high/medium/low) cũng lấy từ `src/ranking/bands.py` vì lý do y hệt.

## Bốn bộ trọng số được so

    V1        kỹ sư đặt tay (0022) — ĐÂY là mốc thật, không phải rơm
    V2-AHP    suy từ so sánh cặp, có bằng chứng nhất quán (CR)
    Đều nhau  0.25 mỗi tiêu chí — mốc "không có mô hình"
    Entropy   suy từ chính dữ liệu, KHÔNG có ý kiến chuyên gia

Hai mốc cuối tồn tại để trả lời câu hỏi đầu tiên mà bất kỳ người phản biện nào
cũng hỏi: AHP có thật sự đổi gì không, hay chỉ tái tạo lại con số đã có?

## Ba khối kết quả

    A. ĐỒNG THUẬN   AHP đổi bảng xếp hạng nhiều hay ít (ρ, τ, chồng lấn top-k, đổi mức)
    B. ỔN ĐỊNH      nhiễu trọng số ±10/20% thì thứ hạng đổi bao nhiêu
    C. TẤT ĐỊNH     chạy hai lần ra kết quả giống hệt

Khối B là khẳng định MẠNH NHẤT có thể đưa ra một cách trung thực trên dữ liệu
tổng hợp: nó không cần nhãn thực tế nào. Vì sao KHÔNG có khối "dự báo" — xem
mục Hạn chế trong báo cáo sinh ra.
"""

from __future__ import annotations

import csv
import math
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# `src` KHÔNG được cài như một gói — repo không có pyproject.toml/setup.py, và
# Dockerfile còn xoá trắng PYTHONPATH. Nên ba dòng `from src...` bên dưới chỉ
# chạy được khi gốc repo TÌNH CỜ nằm trên sys.path, tức là khi gọi `python -m`
# đứng đúng ở gốc repo. Gọi từ chỗ khác — `cd eval && python ahp_benchmark.py`,
# nút Run của IDE, container có WORKDIR khác — chết bằng ModuleNotFoundError,
# và thông báo lỗi ("No module named 'src'") không hề nói ra rằng thủ phạm là
# thư mục hiện hành.
#
# File này VỐN đã biết gốc repo nằm ở đâu (`REPO_ROOT`, dùng cho DATASET/REPORT).
# Chỉ cần biết SỚM hơn một chút là lệnh chạy được từ mọi thư mục.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ranking.ahp import Judgment, compute, round_weights  # noqa: E402
from src.ranking.bands import band_for  # noqa: E402
from src.ranking.engine import FeatureWeight, UnitFeatureInput, rank_scores, score_unit  # noqa: E402

DATASET = REPO_ROOT / "datasets" / "synthetic_v1" / "exports" / "units_ranking.csv"
REPORT = REPO_ROOT / "eval" / "results" / "ahp_benchmark.md"

FEATURES = ("unit_available", "unit_demand_norm", "area_velocity_norm", "area_conversion_norm")

# `direction` / `missing_value_policy` giữ nguyên theo 0022 cho MỌI bộ trọng số.
# Chỉ ĐỘ LỚN được thay đổi giữa các bộ — nếu đổi cả chiều thì ta đang so hai mô
# hình khác nhau chứ không phải so cách chọn trọng số.
SPECS = {
    "unit_available": ("positive", "zero"),
    "unit_demand_norm": ("positive", "zero"),
    "area_velocity_norm": ("positive", "neutral"),
    "area_conversion_norm": ("positive", "neutral"),
}
MIN_COVERAGE = Decimal("0.5")

V1_WEIGHTS = {
    "unit_available": Decimal("0.35"),
    "unit_demand_norm": Decimal("0.25"),
    "area_velocity_norm": Decimal("0.20"),
    "area_conversion_norm": Decimal("0.20"),
}

# Bộ phán đoán chuyên gia, GHIM lại để benchmark tái lập được. Cùng bộ dùng ở
# `tests/test_ranking/test_ahp.py`.
AHP_JUDGMENTS = [
    ("unit_available", "unit_demand_norm", "2"),
    ("unit_available", "area_velocity_norm", "3"),
    ("unit_available", "area_conversion_norm", "3"),
    ("unit_demand_norm", "area_velocity_norm", "2"),
    ("unit_demand_norm", "area_conversion_norm", "2"),
    ("area_velocity_norm", "area_conversion_norm", "1"),
]

# Dự án nhỏ nhất chỉ có 28 căn — top-20 sẽ là 71% của cả dự án, không còn nghĩa
# "nhóm ưu tiên đầu bảng". Giữ top-10.
TOP_K = (10,)
PERTURBATIONS = (Decimal("0.10"), Decimal("0.20"))


# --- Nạp dữ liệu -------------------------------------------------------------


def load_units() -> tuple[dict[str, list[UnitFeatureInput]], dict[str, str]]:
    """Đọc ma trận đặc trưng đã xuất, NHÓM THEO DỰ ÁN.

    Nhóm theo dự án là bắt buộc, không phải tuỳ chọn: pipeline thật chạy
    `run_ranking` cho TỪNG dự án, nên `rank_in_project` chạy 1..N trong mỗi dự
    án. Gộp cả 200 căn vào một rổ sẽ cho một bảng xếp hạng chưa từng tồn tại
    trong hệ thống, và "top-10" khi đó là top-10 của ba dự án trộn lẫn — thứ mà
    đội bán hàng không bao giờ nhìn thấy.

    Phát hiện được nhờ đối chiếu lại chính `rank_in_project` đã xuất; xem
    `tests/test_ranking/test_ahp_benchmark.py`.
    """
    by_project: dict[str, list[UnitFeatureInput]] = {}
    labels: dict[str, str] = {}
    with DATASET.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            unit_id = row["unit_id"]
            labels[unit_id] = row["unit_code"]
            by_project.setdefault(row["project_external_id"], []).append(
                UnitFeatureInput(
                    unit_id=unit_id,
                    area_id=row["area_id"],
                    # Cùng trường mà `src/ranking/service.py` dùng để phá hoà.
                    # Chuỗi ISO so sánh được theo thứ tự thời gian.
                    tie_break_created_at=row["unit_created_at"],
                    values={key: Decimal(row[f"feat_{key}_value"]) for key in FEATURES},
                )
            )
    return by_project, labels


# --- Bộ trọng số -------------------------------------------------------------


def ahp_weights() -> tuple[dict[str, Decimal], Decimal]:
    result = compute(list(FEATURES), [Judgment(a, b, Decimal(v)) for a, b, v in AHP_JUDGMENTS])
    return round_weights(result.weights), result.consistency_ratio


def equal_weights() -> dict[str, Decimal]:
    return round_weights({key: Decimal(1) / len(FEATURES) for key in FEATURES})


def entropy_weights(by_project: dict[str, list[UnitFeatureInput]]) -> dict[str, Decimal]:
    """Trọng số entropy Shannon — mốc KHÁCH QUAN, không dùng ý kiến chuyên gia.

    Tiêu chí mà các căn phân tán nhiều thì mang nhiều thông tin phân biệt hơn,
    nên được trọng số cao hơn. Đây cố ý là một triết lý NGƯỢC với AHP (dữ liệu
    quyết định, không phải con người quyết định) — có nó thì phần so sánh mới
    nói được điều gì đó, thay vì chỉ so AHP với một biến thể của chính nó.
    """
    units = [unit for group in by_project.values() for unit in group]
    m = len(units)
    diversity: dict[str, float] = {}
    for key in FEATURES:
        column = [float(unit.values[key]) for unit in units]
        total = sum(column)
        if total <= 0:
            diversity[key] = 0.0
            continue
        entropy = -sum((v / total) * math.log(v / total) for v in column if v > 0) / math.log(m)
        diversity[key] = 1.0 - entropy

    spread = sum(diversity.values())
    if spread <= 0:  # mọi tiêu chí phân bố đều như nhau — không có gì để phân biệt
        return equal_weights()
    return round_weights({key: Decimal(str(diversity[key] / spread)) for key in FEATURES})


# --- Chấm điểm ---------------------------------------------------------------


def rank_with(weights: dict[str, Decimal], by_project: dict[str, list[UnitFeatureInput]]) -> dict[str, list]:
    """Xếp hạng TỪNG dự án riêng, đúng như `run_ranking` làm."""
    feature_weights = [
        FeatureWeight(key=key, weight=weights[key], direction=SPECS[key][0], missing_value_policy=SPECS[key][1])
        for key in FEATURES
    ]
    return {
        project: rank_scores([score_unit(unit, feature_weights, MIN_COVERAGE) for unit in units])
        for project, units in by_project.items()
    }


def weighted(values: list[tuple[float, int]]) -> float:
    """Trung bình có trọng số theo số căn — dự án 106 căn phải nặng hơn dự án 28 căn."""
    total = sum(n for _, n in values)
    return sum(v * n for v, n in values) / total if total else 0.0


def orderings(ranked: dict[str, list]) -> dict[str, list[str]]:
    """unit_id theo thứ hạng tăng dần, TRONG TỪNG dự án. Căn bị bỏ qua loại ra."""
    return {
        project: [s.unit_id for s in sorted((s for s in group if not s.skipped), key=lambda s: s.rank_in_project)]
        for project, group in ranked.items()
    }


def rank_maps(ranked: dict[str, list]) -> dict[str, dict[str, int]]:
    return {
        project: {s.unit_id: s.rank_in_project for s in group if not s.skipped} for project, group in ranked.items()
    }


# --- Chỉ số so sánh (thuần Python — không thêm scipy) -------------------------


def spearman(a: dict[str, int], b: dict[str, int]) -> float:
    """ρ = Pearson trên thứ hạng. Thứ hạng ở đây là hoán vị nên không có hoà."""
    keys = sorted(set(a) & set(b))
    n = len(keys)
    mean = (n + 1) / 2
    xa = [a[k] - mean for k in keys]
    xb = [b[k] - mean for k in keys]
    denom = math.sqrt(sum(v * v for v in xa) * sum(v * v for v in xb))
    return sum(p * q for p, q in zip(xa, xb)) / denom if denom else 0.0


def kendall_tau(a: dict[str, int], b: dict[str, int]) -> float:
    """τ-a trên hoán vị: (thuận − nghịch) / số cặp."""
    keys = sorted(set(a) & set(b))
    concordant = discordant = 0
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            sign = (a[keys[i]] - a[keys[j]]) * (b[keys[i]] - b[keys[j]])
            if sign > 0:
                concordant += 1
            elif sign < 0:
                discordant += 1
    pairs = concordant + discordant
    return (concordant - discordant) / pairs if pairs else 0.0


def overlap_at(order_a: list[str], order_b: list[str], k: int) -> float:
    """|A ∩ B| / k — quy ước quen thuộc cho "chồng lấn top-k".

    Cố ý KHÔNG dùng Jaccard |A∩B|/|A∪B|: với hai tập cùng cỡ k, trùng 9/10 cho
    Jaccard 0.82, và người đọc báo cáo sẽ hiểu nhầm thành "trùng 82%".
    """
    top_a, top_b = set(order_a[:k]), set(order_b[:k])
    return len(top_a & top_b) / k if k else 0.0


def agg(metric, left: dict, right: dict) -> float:
    """Tính chỉ số trong TỪNG dự án rồi lấy trung bình có trọng số theo số căn.

    Không được gộp thứ hạng của ba dự án vào một véc-tơ rồi tính một lần: hạng 1
    của dự án 28 căn và hạng 1 của dự án 106 căn là hai đại lượng khác nhau, gộp
    lại sẽ tạo ra tương quan giả giữa các dự án không liên quan gì đến nhau.
    """
    return weighted([(metric(left[project], right[project]), len(left[project])) for project in left])


# --- Mức (ngưỡng tuyệt đối ⇒ không phụ thuộc dự án, gộp phẳng được) -----------


def band_of(ranked: dict[str, list]) -> dict[str, str | None]:
    return {s.unit_id: band_for(s.score) for group in ranked.values() for s in group}


def band_counts(ranked: dict[str, list]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for band in band_of(ranked).values():
        if band in counts:
            counts[band] += 1
    return counts


def band_moves(base: dict[str, list], other: dict[str, list]) -> int:
    left, right = band_of(base), band_of(other)
    return sum(1 for uid in left if left[uid] != right.get(uid))


# --- Khối B: độ nhạy ---------------------------------------------------------


def perturb(weights: dict[str, Decimal], key: str, factor: Decimal) -> dict[str, Decimal]:
    """Nhân trọng số `key` với (1 + factor) rồi chuẩn hoá lại về tổng 1."""
    bumped = dict(weights)
    bumped[key] = weights[key] * (Decimal("1") + factor)
    total = sum(bumped.values())
    return round_weights({k: v / total for k, v in bumped.items()})


def sensitivity(weights: dict[str, Decimal], by_project: dict, factor: Decimal) -> dict:
    """Nhiễu từng trọng số ±factor, đo phần trăm căn đổi thứ hạng / đổi mức."""
    base = rank_with(weights, by_project)
    base_rank, base_band = rank_maps(base), band_of(base)
    rank_changed = band_changed = trials = 0

    for key in FEATURES:
        for signed in (factor, -factor):
            moved = rank_with(perturb(weights, key, signed), by_project)
            moved_rank, moved_band = rank_maps(moved), band_of(moved)
            for project, ranks in base_rank.items():
                rank_changed += sum(1 for uid in ranks if moved_rank[project].get(uid) != ranks[uid])
                trials += len(ranks)
            band_changed += sum(1 for uid in base_band if moved_band.get(uid) != base_band[uid])

    return {
        "rank_pct": 100.0 * rank_changed / trials if trials else 0.0,
        "band_pct": 100.0 * band_changed / trials if trials else 0.0,
    }


def criticality(weights: dict[str, Decimal], by_project: dict) -> Decimal | None:
    """Nhiễu NHỎ NHẤT (bước 1%) làm đổi căn đứng đầu của BẤT KỲ dự án nào.

    Trả về ngưỡng MỎNG MANH NHẤT trong ba dự án — con số thận trọng, vì mỗi dự
    án có một đội bán hàng riêng nhìn vào khuyến nghị số 1 của riêng họ.
    """
    leaders = {project: order[0] for project, order in orderings(rank_with(weights, by_project)).items()}
    for step in range(1, 101):
        factor = Decimal(step) / 100
        for key in FEATURES:
            for signed in (factor, -factor):
                shifted = orderings(rank_with(perturb(weights, key, signed), by_project))
                if any(shifted[project][0] != leader for project, leader in leaders.items()):
                    return factor
    return None


# --- Báo cáo -----------------------------------------------------------------


def build_report(by_project: dict, labels: dict[str, str]) -> str:
    v2, cr = ahp_weights()
    schemes = {
        "V1 (đặt tay)": V1_WEIGHTS,
        "V2-AHP": v2,
        "Đều nhau": equal_weights(),
        "Entropy": entropy_weights(by_project),
    }
    ranked = {name: rank_with(w, by_project) for name, w in schemes.items()}
    orders = {name: orderings(r) for name, r in ranked.items()}
    ranks = {name: rank_maps(r) for name, r in ranked.items()}
    total_units = sum(len(group) for group in by_project.values())
    base = "V1 (đặt tay)"

    lines: list[str] = []
    add = lines.append

    add("# Benchmark công thức xếp hạng — V2 (AHP) đối chiếu V1\n")
    add("> Sinh tự động bởi `eval/ahp_benchmark.py`. **Không sửa tay.**\n")
    add("| | |")
    add("|---|---|")
    add(f"| **Đầu vào** | `{DATASET.relative_to(REPO_ROOT)}` |")
    add(f"| **Phạm vi** | {total_units} căn / {len(by_project)} dự án / {len(FEATURES)} đặc trưng |")
    add(f"| **Xếp hạng theo** | TỪNG dự án ({', '.join(f'{p}: {len(u)}' for p, u in sorted(by_project.items()))}) |")
    add("| **Bộ chấm điểm** | `src/ranking/engine.py` (dùng thẳng, không viết lại) |")
    add("| **Ngưỡng mức** | `src/ranking/bands.py` |")
    add(f"| **CR của bộ phán đoán AHP** | {cr:.4f} (ngưỡng 0.08, n=4) |")
    add("| **Cách tái lập** | `python -m eval.ahp_benchmark` |")
    add("")
    add("---\n")

    add("## 1. Bốn bộ trọng số\n")
    add("| Bộ | " + " | ".join(FEATURES) + " |")
    add("|---|" + "---|" * len(FEATURES))
    for name, weights in schemes.items():
        add(f"| {name} | " + " | ".join(str(weights[key]) for key in FEATURES) + " |")
    add("")
    add("`direction` và `missing_value_policy` GIỮ NGUYÊN theo 0022 ở cả bốn bộ — chỉ")
    add("độ lớn thay đổi, nếu không ta đang so hai mô hình khác nhau chứ không phải")
    add("so cách chọn trọng số.\n")
    add("---\n")

    add("## 2. Khối A — Đồng thuận: AHP có thật sự đổi gì không?\n")
    add("Mọi chỉ số thứ hạng tính TRONG từng dự án rồi lấy trung bình có trọng số theo số căn.\n")
    add("| So với V1 | Spearman ρ | Kendall τ | " + " | ".join(f"Chồng lấn top-{k}" for k in TOP_K) + " | Đổi mức |")
    add("|---|---|---|" + "---|" * (len(TOP_K) + 1))
    for name in schemes:
        if name == base:
            continue
        overlaps = " | ".join(
            f"{agg(lambda x, y, k=k: overlap_at(x, y, k), orders[base], orders[name]):.2f}" for k in TOP_K
        )
        add(
            f"| {name} | {agg(spearman, ranks[base], ranks[name]):.4f} "
            f"| {agg(kendall_tau, ranks[base], ranks[name]):.4f} | {overlaps} "
            f"| {band_moves(ranked[base], ranked[name])} căn |"
        )
    add("")
    add('ρ và τ đo THỨ TỰ; cột "Đổi mức" đo NHÃN. Hai thứ tách nhau: `Entropy` giữ')
    add("thứ tự gần như nguyên vẹn mà vẫn đẩy hơn một trăm căn sang mức khác, vì dồn")
    add("trọng số làm điểm co lại và tụt qua ngưỡng. Đội bán hàng nhìn NHÃN.\n")
    add("| Bộ | high | medium | low | Căn đứng đầu mỗi dự án |")
    add("|---|---|---|---|---|")
    for name in schemes:
        counts = band_counts(ranked[name])
        tops = ", ".join(f"`{labels[orders[name][p][0]]}`" for p in sorted(orders[name]))
        add(f"| {name} | {counts['high']} | {counts['medium']} | {counts['low']} | {tops} |")
    add("")
    add("---\n")

    add("## 3. Khối B — Ổn định: thứ hạng chịu được nhiễu trọng số tới đâu\n")
    add("Nhiễu từng trọng số ±x%, chuẩn hoá lại, xếp hạng lại. Không cần nhãn thực tế.\n")
    add("**Đọc bảng này cẩn thận: ổn định KHÔNG tự nó là điều tốt.** Hàng `Entropy` đứng")
    add("yên ở mọi mức nhiễu, nhưng đó là ổn định SUY BIẾN — entropy dồn 0.75 trọng số")
    add("vào một tiêu chí, nên bảng xếp hạng thực chất chỉ sắp theo `unit_demand_norm`")
    add("và không phép nhiễu nhỏ nào đảo được nó. Một mô hình một-tiêu-chí luôn ổn định")
    add("hoàn hảo. Con số cần tìm là ổn định ở một bộ trọng số VẪN CÒN dùng cả bốn tiêu chí.\n")
    add("| Bộ | ±10% đổi hạng | ±10% đổi mức | ±20% đổi hạng | ±20% đổi mức | Nhiễu nhỏ nhất đổi Top-1 |")
    add("|---|---|---|---|---|---|")
    for name in schemes:
        cells = []
        for factor in PERTURBATIONS:
            result = sensitivity(schemes[name], by_project, factor)
            cells += [f"{result['rank_pct']:.1f}%", f"{result['band_pct']:.1f}%"]
        crit = criticality(schemes[name], by_project)
        add(f"| {name} | " + " | ".join(cells) + f" | {'±' + str(crit * 100) + '%' if crit else '> ±100%'} |")
    add("")
    add("Cột cuối nhiễu MỖI LẦN MỘT trọng số và trả về ngưỡng mỏng manh nhất trong ba")
    add("dự án. Nó đo độ bền của KHUYẾN NGHỊ SỐ 1, không phân biệt được các bộ trọng số")
    add("với nhau khi cả bốn cùng xếp một căn lên đầu.\n")
    add("---\n")

    add("## 4. Khối C — Tất định\n")
    again = rank_with(schemes["V2-AHP"], by_project)
    add("| Kiểm | Kết quả |")
    add("|---|---|")
    add(f"| Suy trọng số hai lần ra cùng một bộ | {'✅' if ahp_weights()[0] == schemes['V2-AHP'] else '❌'} |")
    add(f"| Xếp hạng hai lần ra cùng thứ tự | {'✅' if orderings(again) == orders['V2-AHP'] else '❌'} |")
    add("")
    add("Cùng bất biến mà TC-02 của `eval/results/report.md` canh, đo ở tầng công thức.\n")
    add("---\n")

    add("## 5. Hạn chế — đọc trước khi trích số\n")
    add("**Dữ liệu là TỔNG HỢP.** Sinh bởi `scripts/generate_synthetic_dataset.py`.")
    add("Mọi con số trên đo *hành vi của công thức*, không đo độ chính xác trên thị trường thật.\n")
    add('**Cố ý KHÔNG có khối dự báo** ("căn xếp cao có bán nhanh hơn không?"), vì hai lý do:\n')
    add("1. **Rò rỉ nhãn.** `unit_available` mang trọng số lớn nhất và được suy TRỰC TIẾP")
    add("   từ `status` — chính kết quả cần dự báo. Căn đã bán có `unit_available = 0` nên")
    add("   bị đẩy xuống đáy *do cấu tạo*. Một backtest ngây thơ sẽ kết luận mô hình")
    add("   phản-dự-báo, và kết luận đó vô nghĩa.")
    add("2. **Cỡ mẫu.** Bộ dữ liệu chỉ có 42 lần bán. Precision@k trên 42 sự kiện có")
    add("   phương sai quá lớn để nói được điều gì.\n")
    add("Muốn làm khối dự báo khi có dữ liệu thật: cắt theo THỜI GIAN (đặc trưng tính")
    add("tới mốc *T*, đánh giá trên giao dịch trong *(T, T+90 ngày]*), và giới hạn tập")
    add("đánh giá vào những căn CÒN BÁN ĐƯỢC tại *T* — điều đó vô hiệu hoá rò rỉ ở trên")
    add("vì mọi căn trong tập đều có `unit_available = 1`.\n")
    add("**Khối B là khẳng định mạnh nhất ở đây**, vì nó không cần nhãn thực tế nào.")
    return "\n".join(lines) + "\n"


def main() -> None:
    by_project, labels = load_units()
    REPORT.write_text(build_report(by_project, labels), encoding="utf-8")
    total = sum(len(group) for group in by_project.values())
    print(f"đã ghi {REPORT.relative_to(REPO_ROOT)} ({total} căn / {len(by_project)} dự án)")


if __name__ == "__main__":
    main()
