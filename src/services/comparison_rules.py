"""Phân loại chênh lệch giữa bộ tính cũ và bộ tính miền — quyết định TRƯỚC khi nhìn số.

## Vì sao phải có quy tắc trước

Quy tắc viết SAU khi nhìn số sẽ bị bẻ cho vừa với số. Một chênh lệch 3 căn trông
"chắc là do làm tròn" khi người đọc đang muốn cắt sang, và trông "sai nghiêm
trọng" khi họ đang lo. Nên tập phân loại, và quan trọng hơn — phán quyết CHẶN hay
KHÔNG của từng loại — được chốt ở đây, trước khi có bất kỳ dữ liệu thật nào.

## Vì sao KHÔNG đòi hai bên bằng nhau

Hai bộ tính đọc HAI NGUỒN KHÁC NHAU:

* `legacy_aggregate` đọc `sales_records` + `inventory_snapshots` — các dòng TỔNG
  HỢP nạp từ Excel/CSV.
* `domain_units_deals` đọc `units` + `deals` — từng căn, từng giao dịch.

Đòi chúng bằng nhau tuyệt đối là đòi sai thứ. Câu hỏi đúng không phải "có bằng
nhau không" mà là **"mọi chênh lệch có GIẢI THÍCH ĐƯỢC không"**. Một chênh lệch
lớn mà giải thích được thì chấp nhận; một chênh lệch 1 căn mà không ai giải thích
được thì CHẶN.

## Năm loại, và phán quyết của từng loại

| Loại | Nghĩa | Phán quyết |
|---|---|---|
| `coverage` | Một bên KHÔNG CÓ dữ liệu | chấp nhận, nhưng **không tính là bằng chứng cắt sang** |
| `capability_gain` | Bộ tính miền đo được thứ bộ cũ về mặt cấu trúc không đo được | chấp nhận |
| `approximation` | Chênh đúng bằng phần xấp xỉ giữ chỗ ĐÃ BIẾT | chấp nhận |
| `definition_drift` | Hai bên cùng có dữ liệu mà bất đồng về một sự kiện đếm được | **CHẶN** |
| `anomaly` | Bộ tính miền phát hiện dữ liệu tự mâu thuẫn | **CHẶN** |
| `unexplained` | Không rơi vào loại nào ở trên | **CHẶN** |

`unexplained` là loại quan trọng nhất và nó tồn tại vì đúng một lý do: mặc định
phải là CHẶN. Nếu tập phân loại không đủ, cái thiếu phải hiện ra thành một
blocker, chứ không được lặng lẽ rơi vào nhóm "chấp nhận được".

## Dung sai

`units_sold` dung sai **0**. Một căn đã bán là một sự kiện đếm được; hai hệ thống
đếm cùng một tập sự kiện mà ra hai số thì một trong hai đang đếm sai thứ khác.

`units_remaining` chỉ được lệch ĐÚNG BẰNG `domain_units_reserved` — xấp xỉ giữ
chỗ đã biết (bộ tính miền trừ số căn đang giữ chỗ HIỆN TẠI ra khỏi tồn kho, còn
bộ cũ không có khái niệm đó). Lệch nhiều hơn, ít hơn, hay ngược dấu: **không phải**
xấp xỉ đó, và rơi vào `unexplained`.

## Vì sao phân loại lúc ĐỌC, không lưu vào bảng

Phân loại là **hàm thuần** của một dòng `calculator_comparisons`. Lưu nhãn xuống
sẽ đóng băng nó theo bộ quy tắc tại thời điểm ghi — và bộ quy tắc này chắc chắn
còn được siết trước khi cắt sang. Lúc đó lịch sử cũ sẽ mang nhãn theo luật cũ,
trong khi cổng cắt sang đọc theo luật mới, và không ai biết dòng nào theo luật nào.

Tính lại mỗi lần đọc thì toàn bộ lịch sử luôn được đọc theo CÙNG một bộ luật —
bộ luật hiện hành.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Tên loại ----------------------------------------------------------------

CLASS_COVERAGE = "coverage"
CLASS_CAPABILITY_GAIN = "capability_gain"
CLASS_APPROXIMATION = "approximation"
CLASS_DEFINITION_DRIFT = "definition_drift"
CLASS_ANOMALY = "anomaly"
CLASS_UNEXPLAINED = "unexplained"

ALL_CLASSES = (
    CLASS_COVERAGE,
    CLASS_CAPABILITY_GAIN,
    CLASS_APPROXIMATION,
    CLASS_DEFINITION_DRIFT,
    CLASS_ANOMALY,
    CLASS_UNEXPLAINED,
)

# Loại CHẶN. Mặc định của một loại mới phải là CHẶN, nên tập này được liệt kê
# tường minh còn tập "chấp nhận" thì suy ra bằng phép trừ — thêm một loại mà quên
# xếp chỗ thì nó tự động là blocker, chứ không tự động được tha.
BLOCKING_CLASSES = frozenset({CLASS_DEFINITION_DRIFT, CLASS_ANOMALY, CLASS_UNEXPLAINED})
ACCEPTED_CLASSES = frozenset(ALL_CLASSES) - BLOCKING_CLASSES

# --- Phán quyết chung ---------------------------------------------------------

VERDICT_CLEAN = "clean"  # không chênh lệch nào
VERDICT_ACCEPTED = "accepted_differences"  # có chênh lệch, tất cả giải thích được
VERDICT_BLOCKED = "blocked"  # có ít nhất một blocker
VERDICT_NO_DATA = "no_data"  # thiếu một bên, không so được gì có ý nghĩa

# Chỉ số mà bộ tính CŨ về mặt cấu trúc không đo được. `legacy` là None ở đây
# không phải "bằng 0" mà là "không có khái niệm này".
CAPABILITY_ONLY_METRICS = frozenset({"units_reserved"})


@dataclass(frozen=True, slots=True)
class ClassifiedDifference:
    metric: str
    legacy: int | None
    domain: int | None
    delta: int | None
    classification: str
    reason: str

    @property
    def blocking(self) -> bool:
        return self.classification in BLOCKING_CLASSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "legacy": self.legacy,
            "domain": self.domain,
            "delta": self.delta,
            "classification": self.classification,
            "blocking": self.blocking,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ComparisonVerdict:
    """Phán quyết cho MỘT dòng so sánh."""

    comparison_id: str
    project_id: str
    verdict: str
    is_cutover_evidence: bool
    differences: list[ClassifiedDifference] = field(default_factory=list)
    anomalies: list[ClassifiedDifference] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def blockers(self) -> list[ClassifiedDifference]:
        return [item for item in [*self.differences, *self.anomalies] if item.blocking]

    def counts_by_class(self) -> dict[str, int]:
        counts = dict.fromkeys(ALL_CLASSES, 0)
        for item in [*self.differences, *self.anomalies]:
            counts[item.classification] += 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "project_id": self.project_id,
            "verdict": self.verdict,
            "is_cutover_evidence": self.is_cutover_evidence,
            "blocking_count": len(self.blockers),
            "counts_by_class": self.counts_by_class(),
            "differences": [d.as_dict() for d in self.differences],
            "anomalies": [a.as_dict() for a in self.anomalies],
            "reasons": self.reasons,
        }


def classify(row: dict[str, Any]) -> ComparisonVerdict:
    """Phân loại một dòng `calculator_comparisons`. HÀM THUẦN, không chạm DB.

    Thứ tự luật là một phần của định nghĩa, không phải chi tiết cài đặt: luật đầu
    tiên khớp sẽ thắng, nên đổi thứ tự là đổi phân loại.
    """
    legacy_has_data = bool(row.get("legacy_has_data"))
    domain_has_data = bool(row.get("domain_has_data"))
    both_sides = legacy_has_data and domain_has_data

    differences = [
        _classify_difference(entry, row=row, both_sides=both_sides) for entry in (row.get("differences") or [])
    ]
    anomalies = [
        ClassifiedDifference(
            metric=str(entry.get("code", "UNKNOWN")),
            legacy=None,
            domain=None,
            delta=None,
            classification=CLASS_ANOMALY,
            reason=(
                f"Bộ tính miền phát hiện dữ liệu tự mâu thuẫn ({entry.get('code', 'UNKNOWN')}). "
                f"Bất thường ở nguồn không tự khỏi khi cắt sang — nó theo sang."
            ),
        )
        for entry in (row.get("anomalies") or [])
    ]

    reasons: list[str] = []
    blocking = [item for item in [*differences, *anomalies] if item.blocking]

    # `domain_has_data=false` KHÔNG BAO GIỜ là bằng chứng cắt sang. Một dự án chưa
    # có units/deals cho ra 0 ở mọi chỉ số; nếu bên cũ cũng 0 thì hai bên "khớp"
    # hoàn hảo — một cái khớp rỗng tuếch. Đây là chốt thứ hai, sau view
    # `calculator_comparisons_gate` ở tầng database.
    if not domain_has_data:
        reasons.append("Bộ tính miền KHÔNG CÓ dữ liệu — không có gì để so, và cái 'khớp' này không chứng minh gì.")
    if not legacy_has_data:
        reasons.append("Bộ tính cũ KHÔNG CÓ dữ liệu — không có đường cơ sở để đối chiếu.")

    if not both_sides:
        verdict = VERDICT_NO_DATA
    elif blocking:
        verdict = VERDICT_BLOCKED
        reasons.append(f"{len(blocking)} hạng mục CHẶN: {', '.join(sorted({b.classification for b in blocking}))}.")
    elif differences:
        verdict = VERDICT_ACCEPTED
        reasons.append("Mọi chênh lệch đều giải thích được.")
    else:
        verdict = VERDICT_CLEAN
        reasons.append("Hai bộ tính khớp nhau hoàn toàn.")

    return ComparisonVerdict(
        comparison_id=str(row.get("id", "")),
        project_id=str(row.get("project_id", "")),
        verdict=verdict,
        # Bằng chứng cắt sang đòi CẢ BA: hai bên đều có dữ liệu, và không blocker nào.
        is_cutover_evidence=both_sides and not blocking,
        differences=differences,
        anomalies=anomalies,
        reasons=reasons,
    )


def _classify_difference(entry: dict[str, Any], *, row: dict[str, Any], both_sides: bool) -> ClassifiedDifference:
    metric = str(entry.get("metric", ""))
    legacy = entry.get("legacy")
    domain = entry.get("domain")
    delta = entry.get("delta")

    def build(classification: str, reason: str) -> ClassifiedDifference:
        return ClassifiedDifference(
            metric=metric, legacy=legacy, domain=domain, delta=delta, classification=classification, reason=reason
        )

    # LUẬT 1 — thiếu một bên. Xét trước mọi luật khác: khi một bên không có dữ
    # liệu thì MỌI chênh lệch đều là hệ quả của việc thiếu đó, không phải bằng
    # chứng về việc bộ tính nào sai.
    if not both_sides:
        return build(
            CLASS_COVERAGE,
            "Một trong hai bộ tính không có dữ liệu cho dự án này; chênh lệch là hệ quả của việc thiếu dữ liệu.",
        )

    # LUẬT 2 — năng lực mới. `units_reserved` không tồn tại trong mô hình cũ;
    # `legacy=None` ở đây nghĩa là "không có khái niệm", không phải "bằng 0".
    if metric in CAPABILITY_ONLY_METRICS and legacy is None:
        return build(
            CLASS_CAPABILITY_GAIN,
            "Bộ tính cũ không có khái niệm này (dòng tổng hợp không mang trạng thái giữ chỗ). "
            "Đây là năng lực MỚI, không phải số liệu sai.",
        )

    # LUẬT 3 — xấp xỉ giữ chỗ đã biết. Phải khớp CHÍNH XÁC: bộ tính miền trừ số
    # căn đang giữ chỗ hiện tại ra khỏi tồn kho, bộ cũ thì không. Lệch một đơn vị
    # so với con số đó là một hiện tượng KHÁC, và nó không được mượn lời giải
    # thích này.
    reserved = row.get("domain_units_reserved")
    if (
        metric == "units_remaining"
        and isinstance(legacy, int)
        and isinstance(domain, int)
        and isinstance(reserved, int)
        and reserved > 0
        and legacy - domain == reserved
    ):
        return build(
            CLASS_APPROXIMATION,
            f"Chênh đúng bằng {reserved} căn đang giữ chỗ — bộ tính miền trừ chúng khỏi tồn kho, "
            f"bộ cũ không có khái niệm đó. Xấp xỉ ĐÃ BIẾT (quyết định 3).",
        )

    # LUẬT 4 — bất đồng về một sự kiện đếm được. Dung sai bằng 0.
    if metric == "units_sold":
        return build(
            CLASS_DEFINITION_DRIFT,
            "Hai bên cùng có dữ liệu mà đếm ra hai số căn đã bán khác nhau. Một căn đã bán là sự kiện "
            "đếm được, dung sai bằng 0 — chênh ở đây nghĩa là hai bên đang đếm hai thứ khác nhau.",
        )

    # LUẬT 5 — mặc định là CHẶN. Không có nhánh nào tha một chênh lệch chưa được
    # đặt tên; tập phân loại thiếu thì phải hiện ra, không được lặng lẽ đi qua.
    return build(
        CLASS_UNEXPLAINED,
        "Chênh lệch không rơi vào loại nào đã định nghĩa. Mặc định là CHẶN — "
        "phải giải thích được rồi mới phân loại lại.",
    )


def summarise(verdicts: list[ComparisonVerdict]) -> dict[str, Any]:
    """Tổng hợp nhiều phán quyết — thứ cổng cắt sang (8G) sẽ đọc.

    `cutover_evidence_count` là số dòng ĐỦ TƯ CÁCH làm bằng chứng, không phải số
    dòng "khớp". Hai con số đó khác nhau đúng ở chỗ nguy hiểm nhất: dòng thiếu dữ
    liệu.
    """
    counts = dict.fromkeys(ALL_CLASSES, 0)
    for verdict in verdicts:
        for name, value in verdict.counts_by_class().items():
            counts[name] += value

    return {
        "comparisons": len(verdicts),
        "cutover_evidence_count": sum(1 for v in verdicts if v.is_cutover_evidence),
        "blocked_count": sum(1 for v in verdicts if v.verdict == VERDICT_BLOCKED),
        "no_data_count": sum(1 for v in verdicts if v.verdict == VERDICT_NO_DATA),
        "counts_by_class": counts,
        "blocking_classes": sorted(BLOCKING_CLASSES),
    }
