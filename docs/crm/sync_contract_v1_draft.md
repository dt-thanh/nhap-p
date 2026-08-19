# Hợp đồng đồng bộ Mini CRM — v1 (DỰ THẢO)

> **Trạng thái: DỰ THẢO. Mini CRM chưa tồn tại.**
>
> Tài liệu này định nghĩa hợp đồng mà phía nhận (hệ thống này) sẽ áp dụng cho dữ
> liệu của một Mini CRM **sẽ được xây sau**. Không có CRM thật nào đã xác nhận
> bất kỳ điều gì ở đây. Mọi mục đánh dấu **[CẦN XÁC NHẬN]** là một giả định phải
> được người xây Mini CRM kiểm chứng trước khi bật đồng bộ thật (Phase 11).
>
> Hợp đồng dùng **tên khái niệm chuẩn hoá của phía nhận**, không dùng tên field
> của CRM. Adapter phía CRM có nhiệm vụ ánh xạ tên riêng của nó sang tên ở đây.
> Điều này cố ý: hợp đồng phải sống sót qua việc CRM đổi tên cột.

**File đi kèm**

| Đường dẫn | Nội dung |
|---|---|
| [`src/contracts/crm_sync_v1.schema.json`](../../src/contracts/crm_sync_v1.schema.json) | JSON Schema 2020-12, bản chuẩn máy đọc được |
| [`fixtures/`](fixtures/) | 12 payload tổng hợp, đã dán nhãn |
| [`fixtures/README.md`](fixtures/README.md) | Vì sao fixture không phải bằng chứng tương thích |

---

## 1. Vị trí trong hệ thống

```
Mini CRM (tương lai)
    │  HTTPS + API key, đẩy theo lô
    ▼
POST /api/v1/sync/{entity}          ← xác thực, giới hạn kích thước, lưu payload thô
    ▼
kiểm hợp đồng (JSON Schema + quy tắc nghiệp vụ)
    ▼
crm_source_records                  ← danh tính nguồn, phiên bản, quyết định
    ▼
units / deals                       ← bản sao một chiều, CRM sở hữu trường nghiệp vụ
    ▼
DomainAbsorptionCalculatorService
    ▼
absorption_daily (calculator='domain_units_deals')
```

Đường Excel/CSV **không đi qua sơ đồ này** và không bao giờ chạm `units`/`deals`.
Nó vẫn ghi vào `sales_records`/`inventory_snapshots`/`areas` như cũ. Ranh giới đó
được khoá bằng test — xem `tests/test_services/test_legacy_boundary.py`.

## 2. Sở hữu dữ liệu

| Dữ liệu | Ai sở hữu | Ai được ghi |
|---|---|---|
| Trường nghiệp vụ của `units`, `deals` | Mini CRM tương lai | **Chỉ** tầng chiếu đồng bộ |
| `deleted_at`, mốc ghi nhận, `payload_hash` | Hệ thống này | Tầng chiếu đồng bộ |
| `projects`, `areas` | **Hệ thống này** | API danh mục. CRM **tham chiếu được, không tạo được** |
| `sales_records`, `inventory_snapshots` | File Excel/CSV | Tầng nạp file |
| `absorption_daily` | Dẫn xuất | Bộ tính, theo `calculator` |

Hệ quả trực tiếp: **`area_ref` không tra được thì TỪ CHỐI bản ghi**, không tự tạo
phân khu. Một phân khu do CRM tự sinh sẽ mang `total_units` mà không ai đặt — mà
`total_units` chính là mẫu số của tỷ lệ hấp thụ. Sai mẫu số thì mọi con số phía
sau đều sai một cách im lặng.

---

## 3. Phong bì lô (batch envelope)

| Field | Bắt buộc | Ý nghĩa | Ghi chú |
|---|---|---|---|
| `schema_version` | **Bắt buộc** | Phiên bản hợp đồng, v1 = `1` | Không nhận ra → từ chối cả lô |
| `source_system` | **Bắt buộc** | LOẠI hệ nguồn, vd `mini_crm` | |
| `source_instance_id` | **Bắt buộc** | Một CÀI ĐẶT cụ thể, vd `mini-crm-prod` | Ranh giới cô lập; credential buộc vào đúng một giá trị |
| `external_batch_id` | **Bắt buộc** | Danh tính lô do CRM đặt | Nền tảng của idempotency mức lô |
| `sync_mode` | **Bắt buộc** | `incremental` \| `full_snapshot` | |
| `project_ref` | **Bắt buộc** | `{project_id}` — xem 3.1 | Phải trỏ tới dự án đã có |
| `source_extracted_at` | **Bắt buộc** | Thời điểm CRM TRÍCH dữ liệu | Không phải lúc gửi, không phải lúc nhận |
| `snapshot` | **Có điều kiện** | Bắt buộc khi `full_snapshot`, cấm khi `incremental` | |
| `records` | **Bắt buộc** | Mảng, tối đa 5000 phần tử | Có thể rỗng (ảnh chụp của một phạm vi rỗng) |
| `_comment` | Tuỳ chọn | Chú thích tự do, phía nhận bỏ qua | Để fixture tự dán nhãn |

**[CẦN XÁC NHẬN]** CRM có tự sinh và ghi nhớ được `external_batch_id` để gửi lại
đúng giá trị cũ khi retry hay không. Nếu mỗi lần retry sinh id mới thì idempotency
mức lô mất tác dụng và ta phải dựa hoàn toàn vào idempotency mức bản ghi.

### 3.1 Danh tính dự án — CHẶN TRƯỚC KÍCH HOẠT

> **Đây là hạng mục CHẶN, phải giải quyết trước Phase 11. Nó KHÔNG chặn việc xây
> tầng danh tính/idempotency ở Phase 4.**

Hiện tại `project_ref` **chỉ chấp nhận `project_id`** — một UUID do hệ thống này
cấp. `project_code` **không tra được** vì `projects` không có cột nào như thế:

```
projects: id, name, launch_date, created_at, status, headline, introduce,
          cover_image_url, created_by, reviewed_by, reviewed_at,
          review_reason, cover_image_public_id
```

**Không tự bịa ra một ánh xạ `project_code`.** Thêm một cột `project_code` rồi tự
điền giá trị đoán được sẽ tạo ra một khoá mà chỉ phía nhận biết, và khoá đó sẽ
không khớp với bất cứ thứ gì Mini CRM thực sự dùng — cùng loại sai lầm với việc
bịa danh tính căn từ dòng tổng hợp.

Câu hỏi phải trả lời khi Mini CRM được thiết kế:

1. Mini CRM có biết `projects.id` (UUID) của hệ thống này không? Nếu có thì
   `project_ref.project_id` là đủ và không cần thêm gì.
2. Nếu không, CRM định danh dự án bằng cái gì — mã nội bộ, tên, hay id riêng?
3. Ai giữ bảng ánh xạ giữa hai bên, và bảng đó được cập nhật lúc nào?

Cho tới khi có câu trả lời: **`project_id` tổng hợp chỉ dùng cho test và trình mô
phỏng cục bộ** (`5117d1c0-0000-4000-8000-000000000001`). Không có dự án thật nào
được nối vào đường đồng bộ trước khi mục này được chốt.

## 4. Bản ghi

| Field | Bắt buộc | Ý nghĩa |
|---|---|---|
| `entity` | **Bắt buộc** | `unit` \| `deal` |
| `operation` | **Bắt buộc** | `upsert` \| `delete` |
| `external_id` | **Bắt buộc** | Danh tính bản ghi ở CRM |
| `source_revision` | **Có điều kiện** | Bộ đếm tăng dần; ưu tiên hơn timestamp |
| `source_updated_at` | **Có điều kiện** | Có offset múi giờ |
| `payload_completeness` | Tuỳ chọn | `full` (mặc định) \| `partial` — xem 4.3 |
| `payload` | **Có điều kiện** | Bắt buộc khi `upsert`, **cấm** khi `delete` |

Phải có **ít nhất một trong** `source_revision` / `source_updated_at`. Bản ghi
không mang phiên bản là bản ghi không xếp thứ tự được, và một tầng đồng bộ không
xếp được thứ tự thì sớm muộn sẽ ghi đè bản mới bằng bản cũ.

### 4.1 `payload` của `unit`

| Field | Bắt buộc | Ghi chú |
|---|---|---|
| `area_ref` | **Bắt buộc** | `{area_id}` hoặc `{area_name, unit_type}` |
| `unit_code` | **Bắt buộc** | Duy nhất trong phân khu, xét trên căn còn sống |
| `unit_status` | **Bắt buộc** | Giá trị NGUYÊN VĂN của CRM |

### 4.2 `payload` của `deal`

| Field | Bắt buộc | Ghi chú |
|---|---|---|
| `external_unit_id` | **Bắt buộc** | Căn phải tồn tại trước |
| `deal_status` | **Bắt buộc** | Giá trị NGUYÊN VĂN của CRM |
| `reserved_at` | **Có điều kiện** | Bắt buộc khi trạng thái ánh xạ về `reserved`. Nhận `null` tường minh |
| `sold_at` | **Có điều kiện** | Bắt buộc khi ánh xạ về `sold`. Nhận `null` tường minh |
| `lost_at` | **Có điều kiện** | Bắt buộc khi ánh xạ về `lost`. Nhận `null` tường minh |

Ba ràng buộc điều kiện này không phải quy ước tuỳ ý — chúng đã là CHECK constraint
trong migration `0007`. Payload vi phạm sẽ bị DB chặn kể cả nếu tầng chiếu bỏ sót.

### 4.3 Độ đầy đủ của payload, và chốt A4

> **Trạng thái: ĐÃ HIỆN THỰC HOÁ (Phase 8B).** Mục này không còn là điều cần xác
> nhận về phía ta — nó là hành vi đang chạy. Câu hỏi dành cho đội Mini CRM vẫn
> còn nguyên (xem cuối mục).

Một trường trong `payload` có **ba** trạng thái, không phải hai:

| Trạng thái | Cách viết | Ý nghĩa |
|---|---|---|
| **Vắng mặt** | không có khoá | phụ thuộc `payload_completeness` |
| **Null tường minh** | `"reserved_at": null` | hệ nguồn khẳng định mốc này **không còn** |
| **Có giá trị** | `"reserved_at": "…"` | đặt bằng giá trị này |

**Vắng mặt không phải một khẳng định.** Một hệ nguồn không theo dõi trường đó và
một hệ nguồn vừa xoá nó tạo ra payload GIỐNG HỆT NHAU. Vì vậy bên gửi phải tự
khai mình đang gửi kiểu gì.

#### `payload_completeness: "full"` (mặc định)

Bản ghi mang **trạng thái đầy đủ hiện tại**.

| Trường | Bản sao đang có | Kết quả |
|---|---|---|
| vắng mặt | **có giá trị** | **TỪ CHỐI `HISTORY_TIMESTAMP_DROPPED`** |
| vắng mặt | trống | nhận, vẫn trống |
| null tường minh | bất kỳ | xoá có chủ đích, kèm cảnh báo |
| có giá trị | bất kỳ | đặt |

#### `payload_completeness: "partial"`

Bản ghi chỉ mang **những trường đã đổi**.

| Trường | Kết quả |
|---|---|
| vắng mặt | **giữ nguyên** giá trị cũ |
| null tường minh | xoá (chỉ với trường nhận NULL) |
| có giá trị | đặt |

Bản ghi `partial` mà bản sao **chưa có dòng nào** cho danh tính đó bị từ chối với
`PARTIAL_UPDATE_WITHOUT_BASE`: không có gì để vá, và bịa nốt phần thiếu là bịa dữ
liệu. Null tường minh trên trường **không nhận NULL** (`unit_code`, `unit_status`,
`deal_status`, `external_unit_id`) bị từ chối với `NULL_NOT_ALLOWED`.

Dấu vân của bản ghi `partial` được tính lại theo **trạng thái đã hợp nhất**, không
phải theo payload thô: nếu không thì hai hệ nguồn diễn đạt cùng một kết quả — một
bên gửi đủ, một bên gửi phần đổi — sẽ ra hai dấu vân khác nhau và sinh đụng độ giả.

#### Vì sao TỪ CHỐI chứ không âm thầm giữ lại

Tự động chép giá trị cũ sang sẽ **bịa ra lịch sử** trong trường hợp CRM cố ý xoá
nó, và tệ hơn: nó che đúng cái giới hạn của CRM mà A4 sinh ra để phơi bày. A4 là
giả định CHẶN; hỏng thì phải hỏng to. Cái giá của việc từ chối là một bản ghi bị
loại; cái giá của việc im lặng là một con số sai không ai truy được.

Có một cờ thoát hiểm `SYNC_PRESERVE_DROPPED_TIMESTAMPS` (**mặc định TẮT**) chép
giá trị cũ sang kèm cảnh báo. Nó tồn tại cho đúng một tình huống: đội CRM xác nhận
hệ nguồn không giữ được lịch sử và cả đội chấp nhận số liệu xấp xỉ. **Bật cờ này
thì cổng cắt sang phải hỏng** — xem `activation_prerequisites.md`.

> **[CẦN XÁC NHẬN — câu hỏi vẫn còn nguyên]** Mini CRM có giữ được
> `reserved_at`/`sold_at`/`lost_at` qua các lần chuyển trạng thái không? Phía nhận
> giờ đã **phát hiện được** khi nó không giữ, nhưng phát hiện không thay thế được
> câu trả lời: nếu CRM không giữ, mọi giao dịch chuyển trạng thái sẽ bị từ chối
> cho tới khi cờ thoát hiểm được bật, và lúc đó lịch sử đặt cọc là xấp xỉ. Đây vẫn
> là câu hỏi **phải hỏi đầu tiên** khi Mini CRM bắt đầu được thiết kế, vì nó ràng
> buộc schema của CRM, không phải của ta.

## 5. Quy tắc phiên bản

Thứ tự xác định bản nào MỚI HƠN, xét trên cùng một `external_id`:

1. **`source_revision`** nếu có — số lớn hơn là mới hơn.
2. **`source_updated_at`** nếu không có `source_revision` — thời điểm sau là mới hơn.
3. **Không có cả hai → TỪ CHỐI bản ghi** với lý do *không mang phiên bản*
   (`error_category = business`, `error_code = MISSING_SOURCE_VERSION`). Không có
   bước thứ ba nào khác, và không có giá trị mặc định nào được điền vào.

Quy tắc này áp cho **mọi** bản ghi, kể cả `operation: delete`. Lệnh xoá không cần
`payload`, nhưng không mang phiên bản thì không phân biệt được "xoá mới" với "lệnh
xoá cũ đến muộn" — và áp nhầm một lệnh xoá cũ lên bản ghi vừa được tạo lại là mất
dữ liệu một cách im lặng.

### 5.1 `payload_hash` KHÔNG phải là phiên bản

`payload_hash` **không bao giờ** được dùng để quyết định bản ghi nào mới hơn.
Nó chỉ được dùng cho bốn việc, tất cả đều là so sánh BẰNG/KHÁC, không phải
so sánh TRƯỚC/SAU:

| Được dùng để | Cách dùng |
|---|---|
| Phát hiện trùng | Cùng phiên bản + cùng hash → `duplicate_noop` |
| Phát hiện xung đột cùng phiên bản | Cùng phiên bản + khác hash → `conflict` |
| Kiểm toàn vẹn payload | Phát hiện payload đã lưu bị hỏng |
| So sánh trường đã ánh xạ | Đối soát bản sao ↔ bản ghi nguồn |

Lý do cấm tuyệt đối: hash là hàm **băm**, không phải hàm **đơn điệu**. Nó được
thiết kế để phân tán giá trị đầu ra, nên `hash(A) > hash(B)` không mang bất kỳ
thông tin nào về việc A hay B xảy ra trước. Dùng nó để xếp thứ tự sẽ cho ra một
thứ tự trông ổn định, lặp lại được, và **sai ngẫu nhiên** — loại lỗi tệ nhất, vì
nó không bao giờ tự lộ ra. Nếu hai bản ghi cùng phiên bản mà khác nội dung, câu
trả lời đúng là `conflict` — *không biết bản nào mới hơn* — chứ không phải chọn
bừa một bản theo hash.

**Thời điểm NHẬN cũng không bao giờ được dùng làm phiên bản.** Lô đến sau có thể
chứa dữ liệu cũ hơn — retry, gửi lại, chạy bù đều tạo ra tình huống đó.

Nói gọn: **chỉ hệ nguồn mới nói được cái gì mới hơn.** Phía nhận không được suy
ra thứ tự thời gian từ bất cứ thứ gì nó tự tính ra.

**[CẦN XÁC NHẬN]** Mini CRM có bộ đếm phiên bản tăng dần đơn điệu theo từng bản
ghi hay không. Nếu không, ta rơi về `source_updated_at`, và khi đó **[CẦN XÁC
NHẬN]** độ phân giải của nó: nếu chỉ tới giây, hai lần sửa trong cùng một giây sẽ
không phân biệt được và sẽ hiện ra thành `conflict` giả.

## 6. Quy tắc múi giờ

- Mọi timestamp phải là ISO-8601 **có offset**. Thiếu offset → từ chối
  (`error_category = schema`).
- Phía nhận lưu bằng `timestamptz`; hiển thị theo `Asia/Ho_Chi_Minh`.
- **Mốc nghiệp vụ** (`reserved_at`, `sold_at`, `lost_at`, `source_extracted_at`)
  và **mốc hệ thống** (`created_at`, `updated_at` của bản sao) là hai thứ khác
  nhau và không bao giờ được dùng thay cho nhau.

Không suy diễn múi giờ hộ hệ nguồn. Đoán sai 7 tiếng sẽ đẩy một giao dịch sang
ngày khác, và ngày là khoá phân nhóm của toàn bộ chuỗi hấp thụ.

## 7. Ánh xạ trạng thái

Phía nhận **không đoán**. Giá trị không có trong bảng → từ chối bản ghi
(`error_category = field`). Giá trị nguyên văn luôn được lưu vào `deals.source_status`.

**Căn** — tập đích cố định: `available`, `reserved`, `sold`, `blocked`.

**Giao dịch** — tập đích cố định: `lead`, `qualified`, `interested`, `viewing`,
`reserved`, `sold`, `lost`.

Bảng ánh xạ khởi điểm (**[CẦN XÁC NHẬN] toàn bộ — đây là phỏng đoán, không phải
từ vựng của một CRM có thật**):

| Giá trị nguồn (giả định) | → đích | Ghi chú |
|---|---|---|
| `available`, `con_trong` | `available` | |
| `reserved`, `da_dat_coc` | `reserved` | |
| `sold`, `da_ban` | `sold` | |
| `blocked`, `khoa` | `blocked` | |
| `cancelled`, `canceled`, `huy` | `lost` | Giữ nguyên văn ở `source_status` |

Từ vựng thật của Mini CRM phải được điền vào bảng này **trước** khi bật đồng bộ.
Một giá trị chưa ánh xạ mà lại được mặc định về `available` sẽ biến một căn đã bán
thành căn còn trống — sai theo hướng nguy hiểm nhất.

## 8. Idempotency

Hai tầng độc lập, cố ý:

**Mức lô** — khoá `(source_system, source_instance_id, external_batch_id)`.
Gửi lại lô đã xử lý xong → trả **kết quả cũ**, không xử lý lại. Đây là thứ khiến
retry sau timeout trở nên an toàn.

**Mức bản ghi** — khoá `(source_system, source_instance_id, source_entity, source_record_id)`.
Đây là lưới an toàn khi CRM đổi `external_batch_id` giữa các lần retry.

Mỗi lần chạm tới một bản ghi cho ra **đúng một** quyết định, ghi vào
`crm_source_records.last_decision`:

| Quyết định | Khi nào |
|---|---|
| `insert` | Lần đầu thấy `external_id` này |
| `update` | Phiên bản mới hơn bản đã nhận |
| `skip_stale` | Phiên bản CŨ hơn bản đã nhận |
| `duplicate_noop` | Cùng phiên bản, cùng nội dung |
| `conflict` | Cùng phiên bản, **khác** nội dung |
| `tombstone` | Xoá tường minh, hoặc vắng mặt trong ảnh chụp đủ mảnh |

## 9. Xoá và tombstone

- Đồng bộ **không bao giờ xoá vật lý**. `delete` đặt `deleted_at`.
- Bản ghi đã tombstone vẫn giữ danh tính; CRM gửi lại `upsert` thì nó **sống lại**.
- Căn đã tombstone không chặn CRM tạo lại cùng `unit_code` — partial unique index
  của `0007` chỉ áp cho căn còn sống.

**Chốt an toàn cho xoá suy ra từ ảnh chụp** (quyết định 4): nếu tập vắng mặt vượt
`max(5% số bản ghi còn sống, 25 bản ghi)` thì **không tombstone gì cả** — đánh dấu
lần đối soát là thất bại, tạo một finding có cấu trúc, và chờ người duyệt.

Chốt này bảo vệ đúng một tình huống, và là tình huống tệ nhất: một ảnh chụp bị
cắt cụt vì lỗi truy vấn phía CRM sẽ trông y hệt như "phần lớn hàng tồn vừa bị xoá".

## 10. Ảnh chụp và đồng bộ tăng dần

| | `incremental` | `full_snapshot` |
|---|---|---|
| Nội dung | Chỉ bản ghi đã đổi | TOÀN BỘ trạng thái của phạm vi khai báo |
| Suy ra xoá | Không — chỉ `operation: delete` | Có, nếu `snapshot_complete` |
| Cần khai phạm vi | Không | **Có, bắt buộc** |
| Chốt an toàn | Không áp dụng | Áp dụng |

Ảnh chụp gửi nhiều mảnh gộp lại bằng `snapshot_id`. **Chỉ khi nhận đủ
`chunk_total` mảnh và mảnh cuối mang `snapshot_complete: true`** thì việc suy ra
xoá mới được phép chạy. Ảnh chụp thiếu mảnh vẫn cập nhật được các bản ghi có mặt —
nó chỉ không được phép kết luận gì về bản ghi vắng mặt.

`scope` phải khai tường minh. "Mọi thứ không có trong file này" là một tập không
có biên nếu không nói rõ biên nằm ở đâu.

**[CẦN XÁC NHẬN]** Mini CRM có xuất được ảnh chụp đầy đủ theo dự án không, và ở
nhịp nào. Nếu không xuất được, đối soát với nguồn (mục 13) sẽ không bao giờ chạy
được và ta chỉ còn đối soát nội bộ.

## 11. Thứ tự trong lô

Trong một lô, **mọi `unit` phải đứng trước mọi `deal`**. Giao dịch trỏ tới căn
chưa tồn tại bị **từ chối có cấu trúc** (`error_category = business`) — không có
bộ đệm chờ, theo quyết định 6.

Không xây bộ đệm là quyết định có chủ đích: một bộ đệm bản ghi chờ là một hàng đợi
thứ hai, có vòng đời riêng, có cách hỏng riêng, và phải tự trả lời "chờ bao lâu thì
bỏ". Chỉ xây nó khi hợp đồng CRM thật chứng minh rằng thứ tự không đảm bảo được.

**[CẦN XÁC NHẬN]** Mini CRM có đảm bảo được thứ tự này không.

## 12. Định dạng lỗi

Cấu trúc dưới đây **khớp đúng các cột đã có** của `upload_errors` (migration
`0006`) — hợp đồng không đòi thêm cột nào.

```json
{
  "sync_run_id": "8f14e45f-ea6d-4b1e-9c2a-7d3b5a1c0e42",
  "external_batch_id": "SYNTH-BATCH-0009",
  "batch_status": "partially_completed",
  "counts": {
    "received": 3, "accepted": 2, "rejected": 1,
    "skipped_stale": 0, "conflicts": 0, "tombstoned": 0
  },
  "errors": [
    {
      "error_category": "business",
      "error_code": "UNKNOWN_UNIT_REFERENCE",
      "json_path": "$.records[0].payload.external_unit_id",
      "source_record_id": "SYNTH-D-0099",
      "record_locator": "records[0]",
      "field_name": "external_unit_id",
      "raw_value_redacted": "SYNTH-U-KHONG…",
      "message": "Giao dịch trỏ tới căn chưa tồn tại. Gửi căn trước, rồi gửi lại giao dịch.",
      "retry_status": "open"
    }
  ]
}
```

`batch_status` ∈ `pending`, `processing`, `completed`, `partially_completed`, `failed`.
`error_category` ∈ `transport`, `schema`, `field`, `business`, `conflict`.
`retry_status` ∈ `open`, `retrying`, `resolved`, `permanent`.

`partially_completed` tồn tại vì một lô có thể vừa nhận được vài bản ghi vừa từ
chối vài bản ghi khác; gọi nó là `completed` sẽ che mất phần hỏng.

**Không bao giờ đưa giá trị thô đầy đủ vào `message`.** `raw_value_redacted` là
nơi duy nhất chứa mẩu giá trị, và đã bị cắt.

## 13. Đối soát: nội bộ và với nguồn

Hai loại này khác nhau về **thứ chúng chứng minh được**, và trộn chúng lại là cách
nhanh nhất để tuyên bố sai rằng đồng bộ đã đúng.

**Đối soát nội bộ** — chạy được **ngay bây giờ**, chỉ dùng DB của chính ta và
fixture tổng hợp: bản sao ↔ `crm_source_records` khớp nhau, không có giao dịch
đang giữ trùng trên một căn, không có giao dịch mồ côi, không có trạng thái lạ,
tombstone hành xử đúng, thứ tự phiên bản đúng, số bản ghi bị từ chối khớp.

**Đối soát với nguồn** — **không thể chạy cho tới khi Mini CRM tồn tại**: so số
lượng với số lượng CRM báo, hiệu tập `external_id`, so hash trường theo từng bản
ghi, đối chiếu ảnh chụp đầy đủ.

Đối soát nội bộ chứng minh bản sao **tự nhất quán**. Nó **không** chứng minh bản
sao **đúng với CRM**. Chỉ đối soát với nguồn làm được điều đó, và quyết định 8 yêu
cầu **ba lần chạy đối soát thành công liên tiếp trên dữ liệu CRM thật** trước khi
cắt sang.

## 14. Giới hạn

| Giới hạn | Giá trị đề xuất | Lý do |
|---|---|---|
| Kích thước body | 5 MB | Chặn trước khi parse, không sau |
| Số bản ghi mỗi lô | 5000 | Giữ một lô nằm gọn trong một transaction |
| Số lô đồng thời / instance | 1 | Hai lô song song cùng một instance sẽ đua nhau trên cùng bản ghi |
| Retry | Luỹ thừa lùi, 5 lần | Cùng `external_batch_id` mỗi lần |

**[CẦN XÁC NHẬN]** Khối lượng thật: bao nhiêu căn, bao nhiêu giao dịch, đổi bao
nhiêu mỗi ngày. Mọi con số trong bảng này là phỏng đoán cho tới lúc đó.

## 15. Xác thực

- API key server-to-server, **chỉ lưu hash**.
- Mỗi credential **buộc vào đúng một `source_instance_id`**; ghi chéo instance bị
  từ chối.
- Hỗ trợ xoay khoá và thu hồi.
- **Không bao giờ log khoá đầy đủ.**

---

## 16. Tổng hợp các giả định cần xác nhận

Danh sách này là đầu vào của Phase 11. Không mục nào được coi là đúng cho tới khi
người xây Mini CRM xác nhận.

| # | Giả định | Hỏng thì sao |
|---|---|---|
| A1 | `external_id` bền vững trọn đời, không dùng lại | Danh tính vỡ; bản sao trộn hai thực thể vào một |
| A2 | Có `source_revision` tăng dần đơn điệu | Rơi về timestamp, độ chính xác thứ tự giảm |
| A3 | `source_updated_at` có offset và đủ độ phân giải | Xuất hiện `conflict` giả |
| **A4** | **Mốc lịch sử được giữ khi chuyển trạng thái** | **Bản ghi bị TỪ CHỐI (`HISTORY_TIMESTAMP_DROPPED`) — vẫn CHẶN, xem 4.3** |
| A5 | CRM biết dự án/phân khu nào ứng với bản ghi của nó | Mọi bản ghi bị từ chối vì không tra được phân khu |
| A6 | CRM phát được `delete` tường minh | Chỉ còn dựa vào ảnh chụp để suy ra xoá |
| A7 | CRM xuất được ảnh chụp đầy đủ theo dự án | Không bao giờ đối soát được với nguồn |
| A8 | Đảm bảo thứ tự căn-trước-giao-dịch trong một lô | Phải xây bộ đệm bản ghi chờ (quyết định 6) |
| A9 | `external_batch_id` giữ nguyên khi retry | Mất idempotency mức lô |
| A10 | Từ vựng trạng thái đầy đủ đã biết | Bản ghi bị từ chối, hoặc tệ hơn: ánh xạ sai |
| A11 | CRM cung cấp được số lượng/hash để đối soát | Không chứng minh được bản sao đúng |
| A12 | Khối lượng nằm trong giới hạn mục 14 | Phải chỉnh giới hạn và chiến lược chia lô |

**A4 là giả định chặn — và từ Phase 8B nó KHÔNG còn hỏng im lặng được.** Quyết
định 2 đã nói rõ: nếu Mini CRM không giữ được `reserved_at`/`sold_at`/`lost_at`
qua các lần chuyển trạng thái thì phải **dừng và báo cáo giới hạn đó**, chứ không
lặng lẽ chấp nhận mất lịch sử. Phía nhận giờ đã cưỡng chế điều đó: bản ghi đầy đủ
đánh rơi một mốc đang có bị từ chối với `HISTORY_TIMESTAMP_DROPPED` (mục 4.3).

Hai điều **không** đổi:

* A4 vẫn **chưa được trả lời**. Việc phát hiện được không thay thế được câu trả
  lời từ đội CRM; nó chỉ bảo đảm câu trả lời "không giữ được" sẽ hiện ra dưới dạng
  một loạt bản ghi bị từ chối, thay vì một cột lịch sử rỗng dần mà không ai thấy.
* A4 vẫn **chặn việc cắt sang**. Xem `activation_prerequisites.md`.
