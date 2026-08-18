"""Chia điểm thành mức khả năng bán cho giao diện/agent (high / medium / low).

Cổng vào từ `feature/NguyenDucDat/ranking-engine` (nhánh đó xây một ranking
engine khác kiến trúc, không mergeable — xem `pipeline_status.md` đợt hoà giải
2026-08-15 — nhưng lớp trình bày THUẦN này không phụ thuộc kiến trúc, chỉ cần
`Decimal | None`, nên chuyển sang được nguyên vẹn, đổi type cho khớp
`UnitScore.score` của `src/ranking/engine.py`).

Hàm THUẦN, cùng kỷ luật với `engine.py` (§10.1: không I/O, không mạng). Đây là
lớp trình bày đặt TRÊN engine — engine chỉ sinh điểm liên tục trong [0, 1] và
thứ hạng, KHÔNG biết đến khái niệm "mức".

Ngưỡng cắt theo giá trị tuyệt đối chứ không theo phân vị: một unit không được
đổi mức chỉ vì unit khác trong phân khu thay đổi. Chia theo phân vị sẽ luôn tạo
ra một nhóm "cao" kể cả khi cả phân khu đều bán kém — nhãn khi đó không còn
nghĩa là "dễ bán" mà thành "đỡ tệ nhất", dễ gây hiểu nhầm cho đội bán hàng.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

BAND_HIGH = "high"
BAND_MEDIUM = "medium"
BAND_LOW = "low"
BAND_HIGH_MIN = Decimal("0.66")
BAND_MEDIUM_MIN = Decimal("0.33")

PERCENT_QUANTUM = Decimal("0.1")

# Cố định, không sinh động, không do LLM tạo — gắn kèm mọi ngữ cảnh xếp hạng
# đưa vào agent tư vấn (AGENTS.md: khuyến nghị của agent luôn CHỜ DUYỆT, không
# phải cam kết kết quả bán hàng).
DISCLAIMER = (
    "Xếp hạng là một phép tính tất định dựa trên dữ liệu vận hành đã cấu hình, "
    "không phải cam kết hay dự đoán kết quả bán hàng."
)


def band_for(score: Decimal | None) -> str | None:
    """Mức khả năng bán của một điểm trong [0, 1].

    score = None nghĩa là unit không đạt min_weight_coverage (§10.1 tài liệu kế
    hoạch — xem `engine.py:score_unit`) — không đủ dữ liệu để xếp mức. Trả None
    chứ KHÔNG hạ về "low": thiếu dữ liệu và thật sự khó bán là hai chuyện khác nhau.
    """
    if score is None:
        return None
    if score >= BAND_HIGH_MIN:
        return BAND_HIGH
    if score >= BAND_MEDIUM_MIN:
        return BAND_MEDIUM
    return BAND_LOW


def as_percent(score: Decimal | None) -> float | None:
    """Đổi điểm [0, 1] sang thang phần trăm 0-100, làm tròn 1 chữ số thập phân."""
    if score is None:
        return None
    return float((score * 100).quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP))
