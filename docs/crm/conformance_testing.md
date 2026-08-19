# Kiểm phù hợp payload (conformance)

> **Trạng thái: Phase 8C.** Công cụ đã có và đã được kiểm. Nó **chưa từng** chạy
> trên payload thật, vì Mini CRM chưa tồn tại.

## Dùng để làm gì

Trả lời câu hỏi *"payload này có đi qua được đường đồng bộ không, và nếu nạp thì
chuyện gì xảy ra?"* — **trước khi** cấp khoá API cho ai, và **trước khi** một dòng
nào được ghi.

```bash
python -m scripts.conformance_check payload.json                 # bản cho người đọc
python -m scripts.conformance_check payload.json --json          # bản cho máy
python -m scripts.conformance_check a.json b.json --json-out r.json
cat payload.json | python -m scripts.conformance_check -
```

| Mã thoát | Nghĩa |
|---|---|
| `0` | đạt |
| `1` | có vi phạm |
| `2` | không đọc được tệp / sai cách dùng |
| `3` | **TỪ CHỐI CHẠY** — `APP_ENV=production` |

Mã `1` dùng được trong CI và trong cổng kiểm tra trước khi cắt sang (8G).

## Bốn cổng — đúng thứ tự của endpoint thật

| # | Cổng | Thành phần |
|---|---|---|
| 1 | Kích thước | `sync_payloads.measure`, đo trên **byte thô** |
| 2 | Hợp đồng | `ContractValidator` (JSON Schema 2020-12) |
| 3 | Phong bì | `contract_adapter.adapt` + `JsonPayloadParser` |
| 4 | Bản ghi | `sync_runs.apply_records` — danh tính, phiên bản, chốt A4, tra dự án/phân khu/căn, ràng buộc DB |

Cổng 4 gọi **đúng hàm** mà `POST /api/v1/sync/{entity}` gọi. Một bộ kiểm đi đường
riêng sẽ trôi khỏi đường thật đúng vào lúc nó cần chính xác nhất — lần đầu có
payload CRM thật.

**Cổng xác thực không áp dụng** khi kiểm một tệp trên đĩa: không có người gọi nào
để xác thực. Báo cáo nói rõ điều đó thay vì im lặng bỏ qua.

## Vì sao là CLI, không phải cờ `?dry_run=1` trên endpoint

Một cờ boolean trên đường ghi thật chỉ cách một lần đặt sai giá trị là biến toàn
bộ luồng nạp thành no-op im lặng — dữ liệu vẫn được nhận HTTP 202, và không ai
biết gì cho tới khi có người hỏi vì sao dashboard không đổi.

Ở đây, thứ phân biệt "kiểm" với "ghi" không phải một cờ mà là **quyền sở hữu
transaction**: bộ kiểm mở transaction và luôn rollback nó trong `finally`.

## "Không ghi gì" được kiểm chứng bằng số, không bằng lời hứa

Số dòng của 9 bảng (`units`, `deals`, `crm_source_records`, `upload_files`,
`upload_errors`, `sync_payloads`, `absorption_daily`, `areas`, `projects`) được
đếm **trước và sau**, ở một session KHÁC transaction của lần kiểm. Lệch một dòng
là báo cáo hỏng ngay với `CONFORMANCE_LEFT_WRITES`, và đó được đánh dấu là lỗi
của **bộ kiểm**, không phải của payload.

Lý do gắt như vậy: một bộ kiểm để lại dữ liệu còn tệ hơn không có bộ kiểm. Nó bơm
bản ghi lạ vào bản sao, và lần gửi THẬT sau đó sẽ thành `duplicate_noop` — dữ
liệu thật bị bỏ qua vì bản giả đã chiếm chỗ.

Bộ kiểm **có** chèn một dòng `upload_files` tạm trong transaction đó, vì
`crm_source_records.first_sync_run_id` có khoá ngoại tới nó. Việc dòng đó *cần*
tồn tại là bằng chứng bộ kiểm đang đi qua ràng buộc thật, không phải một bản mô
phỏng dễ dãi.

## Nhận payload KHÔNG phải dữ liệu tổng hợp

Khác `scripts/sync_simulator.py` — công cụ đó từ chối gửi bất cứ thứ gì không
mang tiền tố `synthetic-`, vì nó **gửi thật**. Bộ kiểm không gửi đi đâu và không
commit gì, nên nó nhận được payload thật của một Mini CRM tương lai. Đó chính là
lý do nó tồn tại.

## Hai giới hạn phải biết trước khi đọc kết quả

**1. Không có gì tích luỹ giữa các lần kiểm.** Mọi thứ đều rollback, nên mỗi
payload được soi trên trạng thái database ĐANG CÓ. Một chuỗi phụ thuộc lẫn nhau
(căn ở lô 1, giao dịch ở lô 2) sẽ báo `UNKNOWN_UNIT_REFERENCE` ở lô 2 — **đó là
kết quả đúng, không phải lỗi công cụ**. Muốn kiểm cả chuỗi thì phải nạp thật ở
một môi trường phi sản xuất.

**2. Chạy sạch trên fixture tổng hợp không chứng minh gì về CRM.** Fixture do
chính hệ thống này viết, theo đúng cách hệ thống này hiểu hợp đồng. Chúng chứng
minh **bộ kiểm** chạy đúng. Câu hỏi "Mini CRM có gửi đúng không" chỉ payload thật
mới trả lời được. Mọi báo cáo — kể cả báo cáo ĐẠT — đều mang sẵn câu này trong
trường `disclaimer`, để một dòng `conforms: true` bị dán vào chat không bị đọc
thành "đã tương thích".

## Kết quả hiện tại trên bộ fixture tổng hợp

Chạy trên database dev chỉ có dự án tổng hợp + phân khu A1/2PN, **không có** căn
hay giao dịch nào:

| Fixture | Kết quả | Ghi chú |
|---|---|---|
| 01, 03–08 | ĐẠT | `03` là lô replay; báo cáo ghi chú lô đã xử lý |
| 02, 09, 13–15 | `UNKNOWN_UNIT_REFERENCE` | **đúng** — căn ở lô 01 chưa được commit (mọi thứ rollback) |
| 10 | `UNKNOWN_AREA` | fixture cố ý sai |
| 11 | `UNKNOWN_UNIT_STATUS` | fixture cố ý sai |
| 12 | `SCHEMA_*` | dừng ở cổng 2, chưa chạm database |
| 16, 17 | `PARTIAL_UPDATE_WITHOUT_BASE` | **đúng** — không có bản sao nền để vá |

Cả 17 lần chạy đều báo `database_untouched: true` (fixture 12 báo `null` vì chưa
tới cổng 4).

## Trước khi kích hoạt Mini CRM

Bộ kiểm này phải chạy **sạch trên payload THẬT** của CRM (mục C1 trong
`activation_prerequisites.md`). Chạy sạch trên fixture tổng hợp không đóng được
mục đó và không được ghi nhận như thể đã đóng.
