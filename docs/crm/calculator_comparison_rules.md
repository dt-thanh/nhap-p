# Quy tắc phân loại chênh lệch: bộ tính cũ ↔ bộ tính miền

> **Trạng thái: Phase 8E.** Bộ quy tắc được chốt **trước khi** có bất kỳ dữ liệu
> thật nào. Chưa dự án nào cắt sang.

## Vì sao quy tắc phải có trước số liệu

Quy tắc viết SAU khi nhìn số sẽ bị bẻ cho vừa với số. Một chênh lệch 3 căn trông
"chắc là do làm tròn" khi người đọc đang muốn cắt sang, và trông "sai nghiêm
trọng" khi họ đang lo. Nên tập phân loại — và quan trọng hơn, phán quyết CHẶN hay
KHÔNG của từng loại — được chốt ở đây, trước khi Mini CRM tồn tại.

## Vì sao không đòi hai bên bằng nhau

Hai bộ tính đọc **hai nguồn khác nhau**:

| Bộ tính | Đọc từ | Bản chất |
|---|---|---|
| `legacy_aggregate` | `sales_records` + `inventory_snapshots` | dòng TỔNG HỢP nạp từ Excel/CSV |
| `domain_units_deals` | `units` + `deals` | từng căn, từng giao dịch |

Đòi chúng bằng nhau tuyệt đối là đòi sai thứ. Câu hỏi đúng không phải *"có bằng
nhau không"* mà là **"mọi chênh lệch có giải thích được không"**. Một chênh lệch
lớn mà giải thích được thì chấp nhận; một chênh lệch **1 căn** mà không ai giải
thích được thì **CHẶN**.

## Năm loại, và phán quyết

| Loại | Nghĩa | Phán quyết |
|---|---|---|
| `coverage` | Một bên KHÔNG CÓ dữ liệu | chấp nhận — nhưng **không tính là bằng chứng cắt sang** |
| `capability_gain` | Bộ tính miền đo được thứ bộ cũ về mặt cấu trúc không đo được | chấp nhận |
| `approximation` | Chênh đúng bằng phần xấp xỉ giữ chỗ đã biết | chấp nhận |
| `definition_drift` | Hai bên cùng có dữ liệu mà bất đồng về một sự kiện đếm được | **CHẶN** |
| `anomaly` | Bộ tính miền phát hiện dữ liệu tự mâu thuẫn | **CHẶN** |
| `unexplained` | Không rơi vào loại nào ở trên | **CHẶN** |

`unexplained` là loại quan trọng nhất, và nó tồn tại vì đúng một lý do: **mặc định
phải là CHẶN**. Nếu tập phân loại không đủ, cái thiếu phải hiện ra thành một
blocker chứ không được lặng lẽ rơi vào nhóm "chấp nhận được".

Trong mã, `BLOCKING_CLASSES` được liệt kê tường minh còn `ACCEPTED_CLASSES` suy ra
bằng phép trừ — thêm một loại mới mà quên xếp chỗ thì nó **tự động là blocker**.

## Thứ tự luật (luật đầu tiên khớp sẽ thắng)

Thứ tự là một phần của định nghĩa, không phải chi tiết cài đặt.

1. **Thiếu một bên → `coverage`.** Xét trước mọi luật khác: khi một bên không có
   dữ liệu thì MỌI chênh lệch đều là hệ quả của việc thiếu đó, kể cả chênh ở
   `units_sold`.
2. **`units_reserved` mà bên cũ là `null` → `capability_gain`.** `legacy=None` ở
   đây nghĩa là "không có khái niệm", không phải "bằng 0". Nếu bên cũ *có* giá trị
   cho chỉ số này thì lời giải thích không còn đúng và luật này không áp dụng.
3. **`units_remaining` lệch ĐÚNG BẰNG `domain_units_reserved` → `approximation`.**
   Bộ tính miền trừ số căn đang giữ chỗ hiện tại ra khỏi tồn kho; bộ cũ không có
   khái niệm đó. Phải khớp **chính xác** và đúng chiều (`legacy − domain = reserved`,
   với `reserved > 0`). Lệch nhiều hơn, ít hơn, hay ngược dấu: **không phải** xấp
   xỉ đó.
4. **`units_sold` → `definition_drift`.** Dung sai **0**.
5. **Còn lại → `unexplained`.**

Bất thường (`anomalies`) luôn là `anomaly`, không đi qua chuỗi luật trên.

## Dung sai

`units_sold` dung sai **0**. Một căn đã bán là một sự kiện đếm được; hai hệ thống
đếm cùng một tập sự kiện mà ra hai số thì một trong hai đang đếm **thứ khác**, và
đó là điều phải hiểu trước khi cắt, không phải làm tròn cho qua.

`units_remaining` chỉ được lệch đúng bằng số căn đang giữ chỗ. Đây là **xấp xỉ đã
biết** (quyết định 3 — hoãn `unit_status_events`): `deals` không có nhật ký sự
kiện nên trạng thái giữ chỗ theo từng ngày không dựng lại được, và bộ tính miền
dùng con số HIỆN TẠI cho mọi ngày trong chuỗi.

## Phán quyết chung của một lần so sánh

| Phán quyết | Khi nào |
|---|---|
| `clean` | không chênh lệch nào |
| `accepted_differences` | có chênh lệch, tất cả giải thích được |
| `blocked` | có ít nhất một blocker |
| `no_data` | thiếu dữ liệu ở một bên |

`is_cutover_evidence = legacy_has_data AND domain_has_data AND không blocker nào`.

**Nó KHÔNG đồng nghĩa với `matches`.** Một dòng thiếu dữ liệu ở cả hai bên vẫn có
thể `matches = true` (cả hai cùng bằng 0) — nhưng nó không bao giờ là bằng chứng.
Đây là chốt thứ hai, sau view `calculator_comparisons_gate` ở tầng database.

## Vì sao phân loại lúc ĐỌC, không lưu xuống bảng

Phân loại là **hàm thuần** của một dòng `calculator_comparisons`. Lưu nhãn xuống
sẽ đóng băng nó theo bộ quy tắc tại thời điểm ghi — mà bộ quy tắc này chắc chắn
còn được siết trước khi cắt sang. Lúc đó lịch sử cũ mang nhãn theo luật cũ, cổng
cắt sang đọc theo luật mới, và không ai biết dòng nào theo luật nào.

Tính lại mỗi lần đọc thì toàn bộ lịch sử luôn được đọc theo **cùng một bộ luật** —
bộ luật hiện hành. Cái giá là vài mili giây cho mỗi lần đọc, và đó là cái giá rẻ.

## Đọc phán quyết

```bash
curl -H "X-Ops-Token: $OPS_API_TOKEN" \
  "http://localhost:8000/api/v1/parallel-run/<project_id>/verdicts?gate_eligible_only=true"
```

```json
{
  "verdicts": [
    {
      "verdict": "blocked",
      "is_cutover_evidence": false,
      "blocking_count": 1,
      "differences": [
        {"metric": "units_reserved", "classification": "capability_gain", "blocking": false, "reason": "…"},
        {"metric": "units_sold", "classification": "definition_drift", "blocking": true, "reason": "…"}
      ]
    }
  ],
  "summary": {"comparisons": 1, "cutover_evidence_count": 0, "blocked_count": 1, "no_data_count": 0}
}
```

`cutover_evidence_count` — **không phải** số dòng "khớp" — là con số mà cổng cắt
sang ở 8G sẽ đếm.

## Dữ liệu tổng hợp không phải bằng chứng cắt sang

Mọi phán quyết sinh ra ở Phase 8 đều áp lên lịch sử bắt nguồn từ **fixture tổng
hợp**. `is_cutover_evidence: true` ở đây chỉ có nghĩa *"dòng này đủ tư cách được
đếm"*, không có nghĩa *"đã sẵn sàng cắt sang"* — điều kiện cắt sang cần dữ liệu
THẬT và toàn bộ danh sách ở `activation_prerequisites.md`. Câu này đi kèm mọi phản
hồi của endpoint (trường `disclaimer`).
