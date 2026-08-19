# Chạy song song hai bộ tính, và lịch sử so sánh

> **Trạng thái: Phase 8D.** Hai bộ tính chạy song song và kết quả được GHI LẠI.
> **Chưa dự án nào cắt sang** — `projects.absorption_calculator` vẫn là
> `legacy_aggregate` cho tất cả, dashboard vẫn phục vụ lineage cũ.

## Nó trả lời câu hỏi gì

Không phải *"hôm nay hai bộ tính có khớp không"* — `ParallelRunComparator` (Phase
6) đã trả lời được câu đó từ lâu, nhưng nó tính xong rồi trả về, không lưu gì.

Câu hỏi thật là **"có khớp ỔN ĐỊNH không"**, và một xu hướng thì không đọc được
từ một lần đo. Bảng `calculator_comparisons` (0013) là chỗ lưu lại từng lần đo.

## Cách chạy

| Đường | Cách | Ghi chú |
|---|---|---|
| Theo lịch | scheduler → `INGEST_QUEUE` → `run_parallel_run_capture` | `PARALLEL_RUN_CAPTURE_CRON`, mặc định `30 3 * * *` (mọi dự án) |
| Theo yêu cầu | `POST /api/v1/parallel-run/{project_id}` | cần `X-Ops-Token` |
| Đọc lịch sử | `GET /api/v1/parallel-run/{project_id}` | mới nhất trước; `?gate_eligible_only=true` |

```bash
curl -X POST -H "X-Ops-Token: $OPS_API_TOKEN" \
     http://localhost:8000/api/v1/parallel-run/<project_id>
```

`OPS_API_TOKEN` rỗng ⇒ endpoint **đóng** (503). Cùng chốt, cùng hàm
`require_ops_token` với đầu dò ở Phase 8A: đây là dữ liệu phân tích nội bộ theo
từng dự án, và khoá đồng bộ của CRM (cấp cho một `source_instance_id` để GHI) là
sai loại quyền cho việc đọc nó.

## Nó KHÔNG chạm vào gì

* **Không ghi `absorption_daily`.** Bộ so sánh dùng `compute()` — tính trong bộ
  nhớ — chứ không bao giờ dùng `persist()`. Bất biến này được kiểm bằng **dấu vân
  toàn dòng** (mọi cột, cả hai lineage) ở ba tầng: service, worker thật, và
  endpoint.
* **Không đổi `projects.absorption_calculator`.** Không mã nào trong 8D ghi cột đó.
* **Không đụng đường nạp Excel/CSV.**
* Ghi đúng **một dòng** vào `calculator_comparisons` mỗi lần đo.

## Lịch sử chỉ THÊM

Không hàm nào phát ra `UPDATE` hay `DELETE` trên bảng này, và không có ràng buộc
UNIQUE nào trên `project_id` để cản việc thêm. Một lần đo bị ghi đè là một lần đo
biến mất — và thứ biến mất bao giờ cũng là thứ khó chịu nhất: lần khớp hụt mà ai
đó vừa "chạy lại cho chắc".

## "Bằng không" KHÁC "không có gì"

Đây là điểm dễ sai nhất của cả sub-phase.

Một dự án chưa có `units`/`deals` khiến bộ tính miền ra `units_sold = 0`. Nếu bên
cũ cũng 0 thì `matches = true` — **một cái khớp rỗng tuếch**: hai bên khớp nhau vì
cả hai đều không có gì để nói. Mười bốn ngày như thế trông y hệt mười bốn ngày
chạy song song thành công, và đó đúng là thứ sẽ được dùng để quyết định cắt sang.

Nên:

| | |
|---|---|
| `domain_has_data` / `legacy_has_data` | lưu TƯỜNG MINH, xác định bằng câu đếm ĐỘC LẬP với bộ so sánh |
| Cột chỉ số khi `has_data = false` | **NULL**, không phải 0 |
| Ràng buộc CHECK | DB không cho phép trạng thái lửng lơ (`has_data=false` mà chỉ số vẫn có giá trị) |
| View `calculator_comparisons_gate` | chỉ chứa dòng `domain_has_data = true` |

**Cổng chạy song song 14 ngày (8G) BẮT BUỘC đọc view, không đọc thẳng bảng.** Một
quy ước ghi trong tài liệu sẽ được suy diễn lại — và suy diễn sai — khi cổng đó
thực sự được viết. Việc loại trừ nằm ở database để muốn đếm nhầm thì phải cố ý đi
vòng. `GET ...?gate_eligible_only=true` là đường đọc view đó qua API.

## Giới hạn: so sánh là TUẦN TỰ, không phải một ảnh chụp nhất quán

`compare()` gọi bộ tính cũ, **rồi** gọi bộ tính miền — hai truy vấn khác nhau,
không nằm trong một transaction `REPEATABLE READ`.

Nếu một lô đồng bộ commit đúng giữa hai lời gọi, hai bên đọc hai trạng thái khác
nhau và sinh ra một **chênh lệch không có thật**.

Ở quy mô hiện tại cửa sổ đó là vài mili giây, và lần đo theo lịch chạy lúc 03:30
sáng khi không ai ghi — nên đây là đánh đổi chấp nhận được. Nhưng nó CÓ THẬT, và
nó là giả thuyết **đầu tiên** cần loại trừ khi một chênh lệch lẻ loi xuất hiện rồi
biến mất ở lần đo sau.

Cách sửa, nếu có bằng chứng cần: bọc `compare()` trong một transaction
`REPEATABLE READ`. Chưa làm vì chưa có bằng chứng.

## Giới hạn: `units_reserved` là con số HIỆN TẠI

Bộ tính miền không dựng lại được trạng thái giữ chỗ theo từng ngày (`deals` không
có nhật ký sự kiện). Chênh lệch ở `units_remaining` giữa hai bộ tính vì thế có
phần đến từ xấp xỉ này, không phải từ bất đồng thật. Quy tắc phân loại chênh lệch
là việc của **8E**; 8D chỉ ghi lại đủ dữ kiện để 8E phân loại được.

## Dữ liệu tổng hợp KHÔNG phải bằng chứng cắt sang

Mọi dòng sinh ra ở Phase 8 đều bắt nguồn từ **fixture tổng hợp** do chính hệ thống
này viết. Một dòng `matches = true` đọc tách khỏi ngữ cảnh không chứng minh gì về
Mini CRM — Mini CRM **chưa tồn tại**, và chưa dòng dữ liệu thật nào từng đi qua hệ
thống.

Câu này đi kèm **mọi** phản hồi của `GET /parallel-run/{id}` (trường `disclaimer`),
nằm trong docstring của migration 0013, và ở đây. Ba chỗ, vì một dòng
`conforms`/`matches` bị dán vào chat sẽ mất hết ngữ cảnh.

Mục **C3** trong `activation_prerequisites.md` (≥14 ngày chạy song song liên tục,
không có chênh lệch mức chặn) chỉ đóng được bằng dữ liệu THẬT.

## Gỡ bỏ

```bash
alembic downgrade 0012   # xoá view + bảng, không chạm gì khác
```

Hoặc dừng không cần deploy: `PARALLEL_RUN_CAPTURE_ENABLED=false` (tắt lịch), hoặc
xoá `OPS_API_TOKEN` (đóng endpoint). Mất bảng này chỉ mất lịch sử quan sát — không
phép tính nào phụ thuộc vào nó.
