# Phase A — Bản ghi quyết định: đóng băng mô hình miền và hợp đồng

> **Trạng thái: ĐÃ ĐÓNG BĂNG (FROZEN), bản sửa đổi 2026-08-12 (g).**
>
> **Mô hình sở hữu: HỆ NGUỒN LÀ NGUỒN SỰ THẬT.**
> Mini CRM sở hữu **cả bốn** tầng `Project → Area → Unit → Deal`. Backend là
> **bản sao chỉ đọc** (mirror/projection) cộng đường nhập và các API đọc. FE đọc
> từ backend, ghi qua Mini CRM.
>
> Bản này **thay thế** mô hình "backend sở hữu Project/Area + quy trình đề
> xuất–duyệt" của bản 2026-08-12 (f). Nội dung bị thay thế được giữ nguyên ở
> §S — Quyết định đã bị thay thế, và ở `docs/roadmap.md` §Archived.
>
> Mọi hành vi runtime mô tả ở đây là **`PROPOSED — NOT IMPLEMENTED`** trừ khi có
> dẫn chứng `VERIFIED` kèm `file:dòng`.
>
> Phase A **không sửa một dòng mã chạy nào**: `SUPPORTED_SCHEMA_VERSIONS` vẫn là
> `{1}` (`src/services/json_payload.py:28`) và `SUPPORTED_ENTITIES` vẫn là
> `{"units","deals"}` (`:33`). Phép canh: `tests/test_services/test_phase_a_contract_freeze.py`.
>
> Tài liệu đi kèm: [`sync_contract_v2_draft.md`](sync_contract_v2_draft.md) (A6),
> [`authorization_matrix.json`](authorization_matrix.json) (A7),
> [`sync_contract_v1_draft.md`](sync_contract_v1_draft.md) (v1, **bất biến**).

| Nhãn | Nghĩa |
|---|---|
| `VERIFIED` | Có dẫn chứng mã nguồn / migration / test trong repo |
| `FROZEN` | Quyết định của Phase A, ràng buộc Phase B–H |
| `PROPOSED — NOT IMPLEMENTED` | Đã thiết kế, chưa có mã |
| `REQUIRED IN PHASE B/C/D — NOT IMPLEMENTED NOW` | Cần runtime hoặc migration, KHÔNG làm ở Phase A |
| `DECISION REQUIRED` | Chưa chốt, thuộc chủ dự án |

---

## A0 — Nguyên tắc nền

```text
1.  Mini CRM sở hữu CRUD và phiên bản của Project, Area, Unit, Deal.
2.  Backend CHỈ kiểm tra và soi gương chúng qua đường nhập.
3.  Backend KHÔNG BAO GIỜ tự thay đổi bốn thực thể nghiệp vụ đó.
4.  FE ĐỌC từ backend. FE GHI qua Mini CRM (hoặc một cổng ghi Mini CRM tường minh).
```

Điều 3 là điều dễ vi phạm nhất, vì hôm nay backend **đang** có bốn đường ghi vào
`projects`/`areas` (`src/services/projects.py:110,170,222,249`) `VERIFIED`. Gỡ hoặc
chuyển hướng chúng là **`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`**; xem §A2.4.

### A0.1 Ranh giới "thực thể nghiệp vụ" so với "chú thích cục bộ"

Điều 3 nói về **thực thể nghiệp vụ**. Nó không có nghĩa là mọi cột trên bảng
`projects`/`areas` đều thuộc hệ nguồn. Hai nhóm cột, tách dứt khoát:

| Nhóm | Ai sở hữu | Cột |
|---|---|---|
| **Nghiệp vụ, soi gương** | **Hệ nguồn** | `projects`: `name`, `launch_date` · `areas`: `area_name`, `unit_type`, `bedrooms`, `area_sqm`, `total_units`, `project_id` (qua tham chiếu) · `units`/`deals`: toàn bộ trường nghiệp vụ |
| **Chú thích cục bộ của phía nhận** | **Backend** | `headline`, `introduce`, `cover_image_url`, `cover_image_public_id`, `absorption_calculator`, `created_at` (giờ NHẬN), `id` (UUID nội bộ) |
| **Suy ra, không ai tự quyết** | Backend, **dẫn xuất** | `status` — xem §A1.9 |
| **Di sản, không còn đường ghi** | — | `created_by`, `reviewed_by`, `reviewed_at`, `review_reason` |

`FROZEN`: hệ nguồn **không bao giờ** ghi đè nhóm hai; backend **không bao giờ** tự
đặt giá trị nhóm một. Bốn cột nhóm bốn là tàn dư của quy trình duyệt đã bị bỏ
(§S-2) — chúng ở lại vì gỡ cột cần migration, nhưng **không đường ghi nào của
Phase B–H được dùng chúng**.

---

## A1 — Mô hình miền đã đóng băng

### A1.0 Phân cấp

```text
Project
└── Area
    └── Unit
        └── Deal
```

`FROZEN`. Đã tồn tại đầy đủ ở tầng khoá ngoại — Phase A khai báo, không tạo ra:

| Quan hệ | Ràng buộc | Bằng chứng |
|---|---|---|
| Area → Project | `fk_areas_project_id` | `alembic/versions/0001_initial_schema.py:73` `VERIFIED` |
| Unit → Area | `fk_units_area_id` | `alembic/versions/0007_s3_domain_model.py:88` `VERIFIED` |
| Deal → Unit | `fk_deals_unit_id` | `alembic/versions/0007_s3_domain_model.py:130` `VERIFIED` |

**`units` và `deals` KHÔNG có cột `project_id`** `VERIFIED`. Dự án suy ra qua
`area_id → areas.project_id`, và của giao dịch qua `unit_id → units.area_id →
areas.project_id`. Quyết định đóng băng, không phải thiếu sót — §A3.6.

### A1.1 Project

```text
business meaning     Một đợt mở bán. Đơn vị lập kế hoạch và báo cáo của đội bán.
canonical owner      MINI CRM (hệ nguồn).
Mini CRM             Tác giả. CRUD đầy đủ, sở hữu phiên bản.
backend              BẢN SAO CHỈ ĐỌC. Kiểm + soi gương. Không tự tạo, không tự sửa.
FE                   Đọc từ backend; ghi qua Mini CRM.
lifecycle            active  →  archived        (do hệ nguồn điều khiển)
parent entity        KHÔNG CÓ. Project là gốc.
stable identifiers   external_id  (+ source_instance_id)         ← danh tính CHUẨN
                     projects.id (UUID)  — CHỈ NỘI BỘ phía nhận
mutable fields       name, launch_date          (hệ nguồn sở hữu)
backend-local        headline, introduce, cover_image_*, absorption_calculator
delete/archive       status='archived'. KHÔNG có deleted_at, KHÔNG xoá vật lý.
```

`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`: `projects.external_id`,
`projects.source_system`, `projects.source_instance_id`, `projects.source_revision`,
`projects.source_updated_at`, `uq_projects_source_identity`.

### A1.2 Area

```text
business meaning     Một (phân khu × loại căn) trong một dự án. `total_units` của
                     nó LÀ mẫu số của tỷ lệ hấp thụ.
canonical owner      MINI CRM (hệ nguồn).
Mini CRM             Tác giả. CRUD đầy đủ, sở hữu phiên bản VÀ ba trường kế hoạch.
backend              BẢN SAO CHỈ ĐỌC.
FE                   Đọc từ backend; ghi qua Mini CRM.
lifecycle            active  →  archived
parent entity        Project (bắt buộc, BẤT BIẾN — §A1.6)
stable identifiers   external_id (+ source_instance_id)          ← danh tính CHUẨN
                     areas.id (UUID) — CHỈ NỘI BỘ
mutable fields       area_name, unit_type, bedrooms, area_sqm, total_units
backend-local        headline, introduce, cover_image_*
delete/archive       status='archived'.
```

**Ba trường kế hoạch (`bedrooms`, `area_sqm`, `total_units`) là BẮT BUỘC trong
payload và CÓ THẨM QUYỀN.** Ba cột đó `NOT NULL` ở phía nhận với
`ck_areas_area_sqm_positive`, `ck_areas_bedrooms_nonnegative`,
`ck_areas_total_units_nonnegative` `VERIFIED`, nên hệ nguồn **phải** cung cấp;
phía nhận **không điền hộ giá trị mặc định nào**.

`FROZEN`: **`total_units` là số KẾ HOẠCH do hệ nguồn công bố, KHÔNG phải số đếm
bản ghi `unit`.** Phía nhận không bao giờ được suy nó ra bằng `COUNT(*)` — một
phân khu 120 căn mới gửi 40 căn vẫn có mẫu số 120, và đếm sẽ cho tỷ lệ hấp thụ
sai gấp ba.

`FROZEN`: `area_name` và `unit_type` **có thể sửa** (hệ nguồn sở hữu), nhưng cặp
`(project_id, area_name, unit_type)` vẫn bị `uq_areas_project_name_unit_type` khoá
`VERIFIED` — đổi sang một cặp đã tồn tại trong cùng dự án bị từ chối
(`AREA_NATURAL_KEY_CONFLICT`). Hệ quả: **tham chiếu phân khu theo TÊN không còn
an toàn ở v2**, và v2 bỏ hẳn hình dạng đó — §A3.7.

`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`: `areas.external_id` + bốn cột
`source_*` + `uq_areas_source_identity`.

### A1.3 Unit

```text
business meaning     Một căn hộ bán được, trong một phân khu.
canonical owner      MINI CRM.
Mini CRM             Tác giả. CRUD đầy đủ.
backend              BẢN SAO CHỈ ĐỌC.
FE                   Đọc từ backend; ghi qua Mini CRM.
lifecycle            available | reserved | sold | blocked  →  tombstone
parent entity        Area (bắt buộc; đổi được TRONG cùng dự án — §A1.7)
stable identifiers   units.external_unit_id + units.source_instance_id
                     (uq_units_source_identity)                       VERIFIED
mutable fields       area_id, unit_code, status, source_revision, deleted_at
delete/archive       TOMBSTONE — units.deleted_at.
```

Từ vựng bị cưỡng chế bởi `ck_units_status` `VERIFIED`; backend **không có bảng
alias cho căn**. Xem §D-8.

### A1.4 Deal

```text
business meaning     Một giao dịch trên một căn: từ quan tâm tới chốt hoặc mất.
canonical owner      MINI CRM.
Mini CRM             Tác giả. CRUD đầy đủ.
backend              BẢN SAO CHỈ ĐỌC.
FE                   Đọc từ backend; ghi qua Mini CRM.
lifecycle            lead|qualified|interested|viewing|reserved|sold|lost → tombstone
parent entity        Unit (bắt buộc, BẤT BIẾN — §A1.7)
stable identifiers   deals.external_deal_id + deals.source_instance_id  VERIFIED
mutable fields       status, source_status, reserved_at, sold_at, lost_at, deleted_at
immutable            unit_id
delete/archive       TOMBSTONE — deals.deleted_at.
```

Backend **có** bảng alias cho giao dịch (`cancelled → lost`) `VERIFIED`. Mốc lịch
sử bị cưỡng chế bởi bốn CHECK `ck_deals_*_requires_*` `VERIFIED`.

### A1.5 Project là thực thể nghiệp vụ hay ranh giới bảo mật?

`FROZEN`: **thực thể NGHIỆP VỤ**, do hệ nguồn sở hữu. Ranh giới bảo mật mô hình
**riêng**, dạng `project_scope` gắn vào token vai trò của backend (§A7).

Lý do càng đúng hơn dưới mô hình mới: trục cô lập của luồng nhập là
`source_instance_id` — `uq_units_source_identity` khoá trên
`(source_instance_id, external_unit_id)`, **không có `project_id`** `VERIFIED`.
Giờ cả bốn tầng đều dùng trục đó, nên biến Project thành tenant sẽ tạo hai trục
cạnh tranh nhau ở cả bốn tầng thay vì hai.

`FROZEN`: **KHÔNG có tầng tổ chức/tenant.** Xem §D-6.

### A1.6 Area có được chuyển sang Project khác không?

`FROZEN`: **KHÔNG** (`AREA_CROSS_PROJECT_MOVE`). `absorption_daily`,
`sales_records`, `inventory_snapshots` đều khoá theo `area_id` và **không mang
`project_id`** `VERIFIED` — chuyển phân khu sẽ im lặng dời toàn bộ lịch sử hấp thụ
theo nó. Thay thế: archive phân khu cũ + tạo phân khu mới.

Ở v2 điều này **cưỡng chế được rẻ hơn v1**: một lô thuộc đúng một dự án
(`project_ref` ở phong bì), nên một `area` xuất hiện dưới hai `project_ref` khác
nhau là phát hiện được ngay ở tầng nhận.

### A1.7 Unit đổi Area? Deal đổi Unit?

`FROZEN`:

* **Unit → Area khác: CÓ, chỉ trong cùng dự án.** Hành vi này **đã tồn tại nhưng
  chưa khai báo và chưa có test** — `units.area_id` là cột thường, và một `upsert`
  mang `area_ref` khác đã ghi `area_id` mới ngay hôm nay. Cross-project bị chặn vì
  phân giải luôn giới hạn theo dự án của phong bì `VERIFIED`.
* **Deal → Unit khác: KHÔNG.** `deals.unit_id` bất biến. Gắn sang căn khác không
  phải là sửa một giao dịch — đó là một giao dịch khác. Thay thế: tombstone + tạo mới.
* `areas.total_units` **không tự điều chỉnh** khi căn chuyển đi — nó là số kế
  hoạch của hệ nguồn (§A1.2), không phải số đếm.

### A1.8 Cha có được archive khi con còn sống không?

`FROZEN`: **KHÔNG** (`PARENT_HAS_LIVE_CHILDREN`), kèm danh sách `external_id` con
còn sống trong `message`.

`FROZEN`: **KHÔNG cascade xuống con.** Cascade ở tầng chiếu sẽ đánh dấu đã xoá
những dòng mà hệ nguồn vẫn tin là sống; lần đồng bộ sau hệ nguồn gửi lại con với
revision cao hơn và backend hồi sinh nó — một vòng dao động không hội tụ.

Áp cho **cả ba** cặp: Project↛Area, Area↛Unit, Unit↛Deal. Ca Unit↛Deal là **thay
đổi so với hành vi hôm nay** (backend hiện cho phép); đóng băng để luật cha–con
nhất quán ở cả ba tầng. Thi hành: Phase B (cục bộ) + Phase D (phía nhận).

Dưới mô hình mới, nghĩa vụ chính nằm ở **Mini CRM**: nó là nguồn sự thật, nên nó
phải từ chối tại chỗ, trước khi tạo outbox — §A5.3.

### A1.9 Xoá: vật lý, tombstone, hay `status=archived`?

`FROZEN`: **KHÔNG BAO GIỜ xoá vật lý bởi một lô đồng bộ.**

| Thực thể | Biểu diễn | Cột |
|---|---|---|
| Project | archive | `projects.status = 'archived'` |
| Area | archive | `areas.status = 'archived'` |
| Unit | tombstone | `units.deleted_at` |
| Deal | tombstone | `deals.deleted_at` |

Hợp đồng giữ **một** tên thao tác: `operation: "delete"`. Hệ nguồn phát biểu **ý
định** ("bản ghi này không còn"); phía nhận chọn **cách biểu diễn**.

`FROZEN`: **`projects.status`/`areas.status` là trường DẪN XUẤT.** Backend đặt
`archived` khi và chỉ khi nhận `operation: delete`, và `active` khi và chỉ khi
nhận `upsert`. Backend **không tự quyết** trạng thái. Hai giá trị `pending` và
`rejected` của `ck_projects_status`/`ck_areas_status` **không còn đường ghi nào**
sau khi quy trình duyệt bị bỏ (§S-2) — chúng ở lại trong CHECK vì gỡ cần migration
và vì dữ liệu cũ có thể đang mang chúng.

---

## A2 — Nguồn sự thật (đã đóng băng)

### A2.1 Ma trận sở hữu

| Entity | Canonical owner | Mini CRM role | Backend role | FE role | Allowed writer |
|---|---|---|---|---|---|
| **Project** | **Mini CRM** | Tác giả — CRUD + phiên bản | **Bản sao chỉ đọc** — kiểm + soi gương | Đọc từ backend; ghi qua Mini CRM | Mini CRM; backend **chỉ** qua tầng chiếu |
| **Area** | **Mini CRM** | Tác giả — CRUD + phiên bản + 3 trường kế hoạch | **Bản sao chỉ đọc** | Đọc backend; ghi Mini CRM | Mini CRM; backend **chỉ** qua tầng chiếu |
| **Unit** | **Mini CRM** | Tác giả | **Bản sao chỉ đọc** | Đọc backend; ghi Mini CRM | Mini CRM; backend **chỉ** qua tầng chiếu |
| **Deal** | **Mini CRM** | Tác giả | **Bản sao chỉ đọc** | Đọc backend; ghi Mini CRM | Mini CRM; backend **chỉ** qua tầng chiếu |
| **Sync state** | **Chia đôi theo nửa** | Sở hữu nửa GỬI (`crm_outbox`) | Sở hữu nửa NHẬN (`upload_files`, `crm_source_records`, `sync_payloads`) | Đọc nửa NHẬN từ backend | Mỗi bên ghi nửa của mình |
| **Analytics** (`absorption_daily`, `sales_records`, `inventory_snapshots`, `feature_snapshots`, ranking) | **Backend** | Không vai trò | Chủ sở hữu — **dẫn xuất**, tính từ bản sao | Đọc từ backend | Bộ tính của backend; luồng nạp file cho `sales_records`/`inventory_snapshots` |

Hai điều đọc kỹ:

* **Backend là chủ sở hữu của Analytics và điều đó KHÔNG mâu thuẫn với A0 điều 3.**
  Analytics là dữ liệu **dẫn xuất**, không phải thực thể nghiệp vụ. Backend tính nó
  *từ* bản sao; nó không bao giờ chảy ngược lên hệ nguồn.
* **`sales_records`/`inventory_snapshots` đến từ file Excel/CSV, không từ CRM**
  `VERIFIED`. Chúng nằm ngoài phân cấp bốn tầng và Phase A không đụng tới.

### A2.2 Luồng bắt buộc

```text
GHI:   FE  →  Mini CRM (CRUD cục bộ)  →  outbox  →  HTTP ingestion
              →  backend projection
ĐỌC:   FE  ←  backend read APIs
```

`FROZEN`:

* **FE ĐỌC chỉ từ backend.** Không đọc thẳng Mini CRM.
* **FE GHI chỉ qua Mini CRM** — trực tiếp, hoặc qua một **cổng ghi Mini CRM
  tường minh**. Chọn cơ chế nào là `DECISION REQUIRED` §D-4; ràng buộc đã đóng
  băng là: **backend không bao giờ là đích ghi của một thực thể nghiệp vụ.**
* Hệ quả: FE nói chuyện với **hai** đích. Đó là cái giá tường minh của mô hình
  nguồn-sự-thật-ở-hệ-nguồn, và nó phải nhìn thấy được ở FE (một thao tác ghi
  thành công **chưa** có nghĩa là backend đã thấy) — §A7.5.

### A2.3 Vì sao tập trường của hợp đồng HẸP hơn tập cột của phía nhận

Không phải mọi cột trên `projects`/`areas` đều là dữ liệu của hệ nguồn (§A0.1).
`absorption_calculator` là cấu hình phân tích của backend; `cover_image_url` trỏ
vào Cloudinary do backend quản lý; `headline`/`introduce` là nội dung trình bày.
Một CRM bán hàng không có khái niệm nào trong số đó, và ép chúng vào hợp đồng sẽ
buộc hệ nguồn phát biểu về những thứ nó không biết.

`FROZEN`: hợp đồng chở **đúng** những gì phía nhận cần để soi gương thực thể và
tính hấp thụ. Cột còn lại là chú thích cục bộ, backend sở hữu, **và hệ nguồn không
bao giờ ghi đè**.

### A2.4 Đường ghi hôm nay mâu thuẫn với mô hình mới

`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`:

| Đường ghi hiện có | Bằng chứng | Việc phải làm ở Phase D |
|---|---|---|
| `ProjectService.create_project` | `src/services/projects.py:110` `VERIFIED` | Gỡ, hoặc chuyển thành công cụ chỉ dùng cho bootstrap/di trú |
| `ProjectService.create_area` | `:170` `VERIFIED` | Như trên |
| `ProjectService.update_project` | `:222` `VERIFIED` | Giới hạn còn cột **backend-local** |
| `ProjectService.update_area` | `:249` `VERIFIED` | Như trên |
| `POST /projects`, `POST /areas` | `src/api/dashboard.py:157,175` `VERIFIED` | Ngừng dùng cho đường nghiệp vụ |
| `PATCH /projects/{id}`, `PATCH /areas/{id}` | `:200,215` `VERIFIED` | Giới hạn còn cột backend-local |
| API ảnh bìa | `:404–453` `VERIFIED` | **GIỮ** — `cover_image_*` là backend-local |

Phase A **không gỡ, không sửa, không vô hiệu hoá** đường nào trong số trên.

---

## A3 — Định danh và quan hệ (đã đóng băng)

### A3.1 Bảng định danh

| Định danh | Phạm vi duy nhất | Trong hợp đồng? | Trạng thái |
|---|---|---|---|
| `project.external_id` | `(source_instance_id, 'project')` | **CÓ (v2)** — `record.external_id`, và `project_ref.external_project_id` | `REQUIRED IN PHASE D` |
| `area.external_id` | `(source_instance_id, 'area')` | **CÓ (v2)** — `record.external_id`, và `area_ref.external_area_id` | `REQUIRED IN PHASE D` |
| `unit.external_unit_id` | `(source_instance_id, 'unit')` | **CÓ** — `record.external_id`, và `deal_payload.external_unit_id` | `VERIFIED` |
| `deal.external_deal_id` | `(source_instance_id, 'deal')` | **CÓ** — `record.external_id` | `VERIFIED` |
| `source_instance_id` | Toàn cục | **CÓ** — phong bì | `VERIFIED` |
| `projects.id` | UUID nội bộ | Chỉ ở hình dạng tương thích `project_ref.project_id` | `VERIFIED` |
| `areas.id` | UUID nội bộ | **KHÔNG ở v2** (v1 có `area_ref.area_id`) | `VERIFIED` |
| `units.id`, `deals.id` | UUID nội bộ | **KHÔNG, và sẽ không bao giờ** | `VERIFIED` |

### A3.2 Định danh chỉ nội bộ

`FROZEN`: UUID nội bộ của **cả bốn** bảng là chi tiết cài đặt của phía nhận. Hệ
nguồn không cần biết chúng để vận hành. Ngoại lệ duy nhất là hình dạng tương thích
`project_ref.project_id`, giữ cho cài đặt đã được cấu hình ánh xạ sẵn (§A3.7).

### A3.3 `external_id` có được tái sử dụng không?

`FROZEN`: **KHÔNG BAO GIỜ**, kể cả sau tombstone/archive, ở **cả bốn** tầng. Đây
là giả định A1 của v1 `VERIFIED`, đã được thi hành bằng sequence không lùi
(`crm_unit_external_seq`, `crm_deal_external_seq`) `VERIFIED`. Phase B thêm
`crm_project_external_seq` và `crm_area_external_seq` theo đúng khuôn —
`REQUIRED IN PHASE B — NOT IMPLEMENTED NOW`.

`external_id` cũng **BẤT BIẾN**: đổi nó = tạo một bản ghi khác.

### A3.4 Duy nhất toàn cục hay theo `source_instance_id`?

`FROZEN`: **theo `(source_instance_id, entity)`.** Bằng chứng:
`uq_units_source_identity`, `uq_deals_source_identity` `VERIFIED`.
`uq_projects_source_identity` và `uq_areas_source_identity` của Phase D theo đúng
khuôn, dạng **partial unique `WHERE external_id IS NOT NULL`** để dòng cũ do
backend tạo (chưa có `external_id`) không đụng nhau trong lúc di trú.

### A3.5 Tham chiếu cha

```text
Project → (gốc, không có cha)
Area    → PHONG BÌ project_ref          (không lặp ở bản ghi)
Unit    → payload.area_ref.external_area_id
Deal    → payload.external_unit_id      (đây LÀ `unit_ref`, giữ tên v1)
Deal    → Area, Project : KHÔNG trỏ. Suy ra qua unit.
```

`FROZEN`: **`area` KHÔNG mang `project_ref` riêng** — một lô thuộc đúng một dự án,
nên tham chiếu nằm ở phong bì. Cho phép `project_ref` ở từng bản ghi sẽ mở ra lô
trộn nhiều dự án, mà credential (buộc vào `source_instance_id`) và phép cưỡng chế
cross-project (§A1.6) đều giả định điều đó không xảy ra.

`FROZEN`: **KHÔNG thêm `project_ref`/`area_ref` vào `deal_payload`.** Hai đường tới
cùng một sự thật thì có ngày lệch nhau mà không có luật nào phân xử.

`FROZEN`: khi lô chứa bản ghi `entity='project'`, `external_id` của bản ghi đó
**phải khớp** `project_ref.external_project_id`. JSON Schema không diễn đạt được
quan hệ này — nó là luật tầng nghiệp vụ, có fixture `29` canh.

### A3.6 Project suy bằng JOIN hay phi chuẩn hoá?

`FROZEN`: **JOIN.**

```sql
units JOIN areas ON units.area_id = areas.id                    -- areas.project_id
deals JOIN units ON deals.unit_id = units.id
      JOIN areas ON units.area_id = areas.id                    -- areas.project_id
```

Thêm `project_id` vào `units`/`deals` tạo đúng loại trùng lặp §A3.5 vừa cấm. Nếu
Phase D **đo được** rằng đường ba tầng chậm, câu trả lời là index.
`ix_units_area_id_status` và `ix_deals_unit_id` đã có `VERIFIED`; đường ba tầng
**chưa ai đo**.

### A3.7 Tham chiếu ổn định thay vì tên

`FROZEN`. v2 **bỏ hẳn** tham chiếu theo tên:

| Tham chiếu | v1 | v2 | Lý do đổi |
|---|---|---|---|
| `project_ref` | `{project_id}` (UUID) | `{external_project_id}` **hoặc** `{project_id}` | Hệ nguồn không thể biết UUID của một dự án chính nó vừa tạo. UUID giữ lại cho cài đặt đã cấu hình ánh xạ sẵn |
| `area_ref` | `{area_id}` \| `{area_name, unit_type}` | **chỉ** `{external_area_id}` | Ở v2 hệ nguồn sở hữu phân khu nên luôn có `external_id`; và `area_name` giờ **sửa được**, nên trỏ theo tên sẽ đứt ngay lần đổi tên đầu |

`FROZEN`: **`project_name`/`area_name` không bao giờ là tham chiếu.** `projects.name`
không có ràng buộc duy nhất — có chủ đích, cùng khu đô thị mở bán nhiều đợt
`VERIFIED`.

### A3.8 Phạm vi phân quyền được giải thế nào?

```text
project_id nội bộ  ←  areas.project_id  ←  (đường JOIN §A3.6)
quyết định         =  project_id ∈ project_scope(token)
```

Cưỡng chế ở **tầng truy vấn** — §A7.

---

## A4 — Phiên bản và tombstone (đã đóng băng)

### A4.1 Luật phiên bản

```text
source_revision tăng ĐÚNG MỘT lần cho MỖI lần ghi thành công, KỂ CẢ xoá
source_revision có thẩm quyền CAO HƠN source_updated_at khi cả hai cùng có
không có cả hai            → TỪ CHỐI (MISSING_SOURCE_VERSION), không điền mặc định
payload_hash KHÔNG phải phiên bản, không dùng để xếp thứ tự thời gian
mỗi thực thể có DÃY PHIÊN BẢN RIÊNG
```

`VERIFIED` — luật của v1 §5/§5.1, đã thi hành. Phase A mở rộng nguyên xi lên
`project` và `area`.

Dòng cuối đáng nói rõ: revision của một dự án **không nói gì** về revision của
phân khu thuộc nó. Sửa tên dự án không làm phân khu "mới hơn", và một lô chở dự án
revision 7 cùng phân khu revision 2 là hoàn toàn bình thường.

### A4.2 Bảng quyết định — áp cho cả bốn tầng

| Tình huống | Quyết định | Trạng thái |
|---|---|---|
| Revision cũ hơn bản đang giữ | `skip_stale` — không ghi đè | `VERIFIED` |
| Cùng revision, cùng `payload_hash` | `duplicate_noop` | `VERIFIED` |
| Cùng revision, khác `payload_hash` | `conflict` — GHI NHẬN, **giữ bản cũ** | `VERIFIED` |
| Chưa từng thấy | `insert` | `VERIFIED` |
| Revision mới hơn | `update` | `VERIFIED` |
| `operation: delete` | tombstone (unit/deal) · archive (project/area) | `VERIFIED` / `PROPOSED` |

Lô kết thúc `completed_with_conflicts` khi có `conflict` mà không có `rejected`
`VERIFIED` (migration `0016`).

### A4.3 Project/Area dùng cùng cơ chế danh tính và khoá?

`FROZEN`: **CÓ, y hệt Unit/Deal, cho cả bốn tầng.**

**Phát hiện khiến Phase A không cần migration:** `crm_source_records.source_entity`
là `sa.Text()` với CHECK **duy nhất là `<> ''`** — không enum
(`alembic/versions/0006_sync_foundation.py:224`) `VERIFIED`. Bản ghi `project` và
`area` ghi vào sổ danh tính **không cần migration nào cho `crm_source_records`**,
và toàn bộ máy sáu nhánh cùng khoá hàng `SELECT ... FOR UPDATE` +
`lock_identities()` của Phase 5 áp dụng **không sửa một dòng nào** `VERIFIED`.

`FROZEN`: **Phase A không đổi khoá runtime.** Cơ chế đã có được kế thừa nguyên trạng.

### A4.4 Năm tình huống cạnh

```text
1. XOÁ CHA KHI CÒN CON SỐNG
   → TỪ CHỐI. PARENT_HAS_LIVE_CHILDREN + danh sách external_id con còn sống.
     Không cascade. Mini CRM phải từ chối TRƯỚC khi tạo outbox (§A5.3).

2. XOÁ CON SAU KHI CHA ĐÃ ARCHIVE
   → CHẤP NHẬN. Tombstone một căn thuộc phân khu đã archive là cách hệ nguồn dọn
     nốt phần con. Ngược lại, UPSERT một con vào cha đã archive → TỪ CHỐI
     (PARENT_ARCHIVED).

3. HỒI SINH
   → CHẤP NHẬN, và là hành vi ĐÚNG. Upsert revision CAO HƠN trên bản ghi đã
     tombstone/archive đặt lại trạng thái sống: hệ nguồn — chủ sở hữu — đang nói
     "bản ghi này sống lại", và external_id không tái sử dụng nên không nhầm sang
     thực thể khác. Revision THẤP HƠN rơi vào skip_stale: lệnh cũ đến muộn KHÔNG
     hồi sinh được gì.
     Hồi sinh một CON khi CHA vẫn archived → TỪ CHỐI (PARENT_ARCHIVED): thứ tự
     hồi sinh phải là cha trước, đúng chiều §A5.2.

4. ĐỤNG ĐỘ CÙNG REVISION
   → conflict. Ghi nhận, giữ bản cũ, KHÔNG tự chọn bên thắng. Lô kết thúc
     completed_with_conflicts.
     Dưới mô hình nguồn-sự-thật-ở-hệ-nguồn, cách giải quyết ĐÚNG là hệ nguồn phát
     lại với revision CAO HƠN — backend không có thẩm quyền phân xử. §D-9.

5. SỰ KIỆN CHA/CON SAI THỨ TỰ
   → §A5.3. Con đến trước cha: TỪ CHỐI bản ghi con, giữ phần còn lại của lô.
```

---

## A5 — Danh mục sự kiện và thứ tự (đã đóng băng)

### A5.1 Repo dùng `(entity, operation)`, không dùng tên sự kiện

`FROZEN`: giữ quy ước của repo — không phát minh một lớp tên sự kiện thứ hai. Ánh
xạ tường minh, **cả 12 sự kiện giờ đều là sự kiện đồng bộ do hệ nguồn phát**:

| Tên logic | Biểu diễn chuẩn | Đường | Trạng thái |
|---|---|---|---|
| `project_created` | `{entity:"project", operation:"upsert"}` (lần đầu) | `POST /sync/projects` | `PROPOSED` (v2) |
| `project_updated` | `{entity:"project", operation:"upsert"}` (đã có) | `POST /sync/projects` | `PROPOSED` (v2) |
| `project_deleted` | `{entity:"project", operation:"delete"}` → archive | `POST /sync/projects` | `PROPOSED` (v2) |
| `area_created` | `{entity:"area", operation:"upsert"}` (lần đầu) | `POST /sync/areas` | `PROPOSED` (v2) |
| `area_updated` | `{entity:"area", operation:"upsert"}` (đã có) | `POST /sync/areas` | `PROPOSED` (v2) |
| `area_deleted` | `{entity:"area", operation:"delete"}` → archive | `POST /sync/areas` | `PROPOSED` (v2) |
| `unit_created` | `{entity:"unit", operation:"upsert"}` (lần đầu) | `POST /sync/units` | `VERIFIED` |
| `unit_updated` | `{entity:"unit", operation:"upsert"}` (đã có) | `POST /sync/units` | `VERIFIED` |
| `unit_deleted` | `{entity:"unit", operation:"delete"}` → tombstone | `POST /sync/units` | `VERIFIED` |
| `deal_created` | `{entity:"deal", operation:"upsert"}` (lần đầu) | `POST /sync/deals` | `VERIFIED` |
| `deal_updated` | `{entity:"deal", operation:"upsert"}` (đã có) | `POST /sync/deals` | `VERIFIED` |
| `deal_deleted` | `{entity:"deal", operation:"delete"}` → tombstone | `POST /sync/deals` | `VERIFIED` |

Ba điều rút ra:

* **`created` và `updated` không phân biệt được ở nguồn**, có chủ đích: `upsert` để
  phía nhận quyết định dựa trên sổ danh tính. Hệ nguồn tự khai "đây là lần tạo" sẽ
  sai mỗi khi một lô được phát lại.
* **`deleted` là MỘT thao tác hợp đồng, HAI biểu diễn** — archive cho hai tầng
  trên, tombstone cho hai tầng dưới (§A1.9).
* Từ vựng đã có: đường dẫn **số nhiều** (`/sync/units`), bản ghi **số ít**
  (`entity:"unit"`); lệch nhau ⇒ `ENTITY_MISMATCH` (409) `VERIFIED`.

`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`: hai đường `POST /sync/projects` và
`POST /sync/areas`, cùng `SUPPORTED_ENTITIES += {"projects","areas"}`.

### A5.2 Thứ tự

```text
upsert:  project  →  area  →  unit  →  deal        (cha trước con)
delete:  deal     →  unit  →  area  →  project     (con trước cha)
```

`FROZEN`: **thứ tự của `records[]` LÀ MỘT PHẦN CỦA HỢP ĐỒNG.** Phía nhận xử lý
tuần tự theo đúng thứ tự mảng và **không sắp xếp lại hộ** — sắp hộ sẽ che mất việc
hệ nguồn không đảm bảo được thứ tự, đúng thứ cần phơi bày.

Mở rộng của luật v1: *"mọi `unit` phải đứng trước mọi `deal`"* `VERIFIED`.

### A5.3 Thứ tự, mồ côi và phục hồi

```text
Lô trộn nhiều tầng?
  → ĐƯỢC PHÉP, đúng thứ tự A5.2. Cấm trộn sẽ buộc một thao tác nghiệp vụ ("mở
    dự án mới với 3 phân khu và 40 căn") thành nhiều lô, và tính nguyên tử của
    thao tác đó không còn ai giữ.

Thiếu Project (project_ref không tra được, và lô KHÔNG chứa bản ghi project đó)?
  → TỪ CHỐI CẢ PHONG BÌ. PROJECT_NOT_FOUND. project_ref là thuộc tính của phong
    bì; nếu nó sai thì mọi bản ghi đều sai.
    NGOẠI LỆ đã đóng băng: nếu lô CHỨA bản ghi entity='project' có external_id
    khớp project_ref, thì dự án được TẠO trong chính lô đó và phong bì hợp lệ.
    Đây là đường khởi tạo — không có nó thì không dự án nào sinh ra được.

Thiếu Area (area_ref không tra được)?
  → TỪ CHỐI CHỈ BẢN GHI ĐÓ, giữ phần còn lại. AREA_NOT_FOUND.

Bản ghi mồ côi: defer, reject, hay dead-letter?
  → REJECT. KHÔNG defer, KHÔNG dead-letter.
    Cả hai đòi hạ tầng mới (hàng đợi chờ, job quét lại, chính sách hết hạn) cho
    một tình huống mà hệ nguồn tự tránh được bằng cách sắp thứ tự — và nếu không
    sắp được thì resend đã là cơ chế phục hồi sẵn có. Đây cũng là quyết định 6 đã
    có của v1 §11: "không có bộ đệm chờ".

Backend có được TỰ TẠO cha không?
  → TUYỆT ĐỐI KHÔNG. Backend không bao giờ phát minh ra một dự án hay phân khu để
    làm cho một bản ghi con hợp lệ. Một cha do máy tạo sẽ mang `total_units` mà
    không ai đặt — và `total_units` là MẪU SỐ của tỷ lệ hấp thụ.

Sửa chữa sau khi cha đã tồn tại?
  → Hai đường, cả hai ĐÃ CÓ:
    (a) hệ nguồn gửi LÔ MỚI chứa bản ghi con — đường thường;
    (b) POST /sync-runs/{id}/reprocess (pipeline_operator+, confirm=true) chạy lại
        NGUYÊN VĂN payload cũ sau khi cha đã tồn tại.
    Đường (b) hoạt động được chính vì payload được lưu nguyên văn ở cả hai phía.
```

### A5.4 Bản ghi bị từ chối phải giữ đủ danh tính để phục hồi

`FROZEN`. Sáu trường bắt buộc, **cả sáu ánh xạ vào cột đã có** của `upload_errors`
(migration `0006`) `VERIFIED` — hợp đồng không đòi thêm cột nào:

| Trường | Cột `upload_errors` |
|---|---|
| `external_batch_id` | qua `file_id` → `upload_files.external_batch_id` |
| `entity` | qua `file_id` → `upload_files.source_entity` |
| `external_id` | `source_record_id` |
| `source_revision` | `raw_value_redacted` / `message` (đã cắt) |
| tham chiếu cha | `json_path` + `field_name` (vd `$.records[2].payload.area_ref`) |
| mã lỗi | `error_code` + `error_category` |

Cộng `record_locator` và `retry_status`. **Không bao giờ đưa giá trị thô đầy đủ vào
`message`** — luật v1 §12.

---

## A6 — Phiên bản hợp đồng

Tài liệu riêng: [`sync_contract_v2_draft.md`](sync_contract_v2_draft.md).

Tóm tắt:

* v1 **bất biến, byte-identical**. SHA-256:
  `e15fd9c5e685923fcf3f537c7dba4e900632ae7d6723df654e35b55efb49a92a`
* **v2 KHÔNG phải mở rộng cộng thêm của v1** — nó là một **mô hình sở hữu khác**.
  v1: `projects`/`areas` thuộc phía nhận. v2: cả bốn thuộc hệ nguồn.
* Cơ chế cùng tồn tại **đã có sẵn**: `SUPPORTED_SCHEMA_VERSIONS` là một `frozenset`
  (`src/services/json_payload.py:28`) `VERIFIED`. Bật v2 = thêm `2` —
  `REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`.
* Phiên bản lạ bị từ chối an toàn: `UNSUPPORTED_SCHEMA_VERSION` → 422 `VERIFIED`.

---

## A7 — Phân quyền

Dữ liệu chính sách: [`authorization_matrix.json`](authorization_matrix.json) —
**nguồn sự thật duy nhất**, đọc được bằng máy, có test.

### A7.1 Hai mặt phẳng phân quyền, KHÔNG phải một

`FROZEN`. Mô hình mới tách hẳn quyền GHI khỏi quyền ĐỌC, vì chúng nằm ở hai hệ:

```text
GHI  →  Mini CRM     : xác thực GHI của Mini CRM        ← DECISION REQUIRED §D-2
ĐỌC  →  Backend      : role × project_scope             ← đã đóng băng dưới đây
MÁY  →  /sync/*      : X-API-Key buộc vào source_instance_id     VERIFIED
```

Mặt phẳng thứ nhất là **rủi ro nghiêm trọng nhất mà bản sửa đổi này tạo ra** —
§A7.5.

### A7.2 Phân quyền ĐỌC ở backend

```text
authorization = role  ×  project_scope

role          ∈ { business_viewer(0) < pipeline_operator(1) < admin(2) }  VERIFIED
project_scope ∈ { ALL, tập project_id tường minh, tập RỖNG }              FROZEN
```

| Action | business_viewer | pipeline_operator | admin | Project scope enforced |
|---|---:|---:|---:|---:|
| View permitted Project | ✅ | ✅ | ✅ | ✅ |
| View permitted Area | ✅ | ✅ | ✅ | ✅ |
| View Units | ✅ | ✅ | ✅ | ✅ |
| View Deals | ✅ | ✅ | ✅ | ✅ |
| Create/update Project | ❌ | ❌ | ❌ | — |
| Create/update Area | ❌ | ❌ | ❌ | — |
| Create/update Unit | ❌ | ❌ | ❌ | — |
| Create/update Deal | ❌ | ❌ | ❌ | — |
| Read sync state | ❌ | ✅ | ✅ | ✅ |
| Reprocess sync | ❌ | ✅ | ✅ | ✅ |
| Cross-project access | ❌ | ❌ | ❌ | ✅ |

**Bốn dòng `Create/update` giờ là ❌ cho MỌI vai trò**, kể cả `admin`. Đây là thay
đổi trực tiếp so với bản (f), nơi `admin` ghi được Project/Area. Backend không phải
là đích ghi của một thực thể nghiệp vụ — nếu có một vai trò backend nào ghi được,
thì backend đã không còn là bản sao chỉ đọc, và điều A0.3 chỉ còn là một lời hứa.

**Cross-project: ❌ cho mọi vai trò.** `admin` xuyên mọi dự án **chỉ khi** được cấp
`ALL` tường minh — đó là mở rộng **phạm vi**, không phải bỏ qua phép kiểm. `FROZEN`:
**không có nhánh mã "nếu admin thì bỏ qua"**.

### A7.3 Các quyết định đã chốt

```text
TĨNH hay ĐỘNG?          → TĨNH. Phạm vi gắn vào token, cấu hình qua biến môi
                          trường, cùng nơi và cùng lúc với việc cấp token. Động
                          (bảng project_members) đòi một hệ NGƯỜI DÙNG thật mà
                          repo chưa có.  HOÃN.

Ai gán phạm vi?         → Người vận hành triển khai, qua cấu hình.

Operator xuyên dự án?   → KHÔNG. Phạm vi áp cho cả ĐỌC và HÀNH ĐỘNG. Reprocess
                          một lô ngoài phạm vi → 403 + một dòng audit.

Admin bỏ qua phạm vi?   → KHÔNG. Admin được CẤP phạm vi ALL tường minh. Bỏ trống
                          KHÔNG có nghĩa là ALL.

Cưỡng chế ở tầng nào?   → TẦNG TRUY VẤN (WHERE/JOIN), không phải tầng route.
                          Route-level chỉ chặn được route có project_id tường
                          minh; unit/deal suy ra dự án qua JOIN 2–3 tầng, nên một
                          route nhận area_id hay unit_id vẫn phải giới hạn. Kiểm
                          ở tầng truy vấn thì đường JOIN không thể thành cửa hậu.

Không có phạm vi?       → KHÔNG DỰ ÁN NÀO (fail-closed). Soi gương nguyên tắc đã
                          chạy: chưa cấu hình token nào ⇒ mặt đọc ĐÓNG (503
                          DASHBOARD_AUTH_DISABLED).  VERIFIED

FE nhận phạm vi ra sao? → GET /api/v1/me/permissions  (PROPOSED — NOT IMPLEMENTED)
                          CHỈ để HIỂN THỊ, không bao giờ là nguồn cưỡng chế.

FE ghi vào đâu?         → Mini CRM (trực tiếp hoặc qua cổng ghi tường minh).
                          KHÔNG BAO GIỜ vào backend.  §D-4 chọn cơ chế.

FE đọc từ đâu?          → Backend, và chỉ backend.
```

### A7.4 Yêu cầu triển khai cho Phase E

`REQUIRED IN PHASE E — NOT IMPLEMENTED NOW`:

```text
1. project_scope vào DashboardPrincipal (src/services/dashboard_auth.py).
2. Cấu hình phạm vi cho mỗi token trong Settings (src/config.py) — fail-closed.
3. Mệnh đề phạm vi ở TẦNG TRUY VẤN cho mọi route đọc dữ liệu dự án.
4. Đóng GET /deals và GET /inventory — hôm nay đang MỞ (không Depends nào).
5. GET /me/permissions.
6. 403 PROJECT_OUT_OF_SCOPE (không phải 404) + audit.
7. Test liệt kê BẢNG ĐỊNH TUYẾN chứng minh không route dữ liệu dự án nào bị sót.
8. MỚI ở bản sửa đổi này: test chứng minh KHÔNG route backend nào GHI được
   projects/areas/units/deals ngoài tầng chiếu đồng bộ.
```

### A7.5 Rủi ro nghiêm trọng do bản sửa đổi này tạo ra

`DECISION REQUIRED` — §D-2, và nó **chặn phần FE-ghi của Phase F/G**, không chặn
Phase B.

Mini CRM **không có xác thực nào** — quyết định phạm vi tường minh của chủ dự án ở
đợt Phase 5.5 P0, và nó **đúng khi Mini CRM là một bộ sinh dữ liệu tổng hợp để
kiểm thử**.

Bản sửa đổi này đổi đúng tiền đề đó. Mini CRM giờ là **hệ thống bản ghi của toàn
bộ dữ liệu nghiệp vụ**, và FE ghi qua nó. Một Mini CRM không xác thực trong mô hình
mới nghĩa là: **ai chạm được cổng :8100 thì tạo, sửa và xoá được mọi dự án, phân
khu, căn và giao dịch** — và backend sẽ trung thành soi gương việc đó, vì soi gương
chính là việc của nó.

`FROZEN` (yêu cầu, không phải cơ chế): **phải có xác thực ghi ở Mini CRM trước khi
tồn tại bất kỳ đường ghi nào từ FE.** Cơ chế cụ thể là `DECISION REQUIRED`.
Phase A **không cài đặt gì** cho mục này.

---

## A8 — Test đã thêm

`tests/test_services/test_phase_a_contract_freeze.py`. Chỉ kiểm **hợp đồng đã đóng
băng và bảng quyết định**; không test runtime CRUD.

Bốn nhóm:

1. **Bất biến v1** — SHA-256 ghim vào giá trị tuyệt đối.
2. **v2 đúng như đã đóng băng** — schema hợp lệ, hai bản byte-identical, bốn thực
   thể, tham chiếu ổn định, trường kế hoạch bắt buộc, phong bì hợp lệ/hỏng, hai
   phiên bản loại trừ lẫn nhau.
3. **Ranh giới Phase A** — runtime CHƯA chấp nhận v2, CHƯA biết `projects`/`areas`
   như thực thể đồng bộ, CHƯA có `project_scope`. Nhóm này canh đúng thứ Phase A
   hứa: **không đổi runtime**. Khi Phase D/E bắt đầu, chính chúng sẽ đỏ — tín hiệu
   ĐÚNG, và mỗi test ghi rõ phase nào được phép làm nó đỏ.
4. **Ma trận phân quyền nhất quán** — vai trò khớp runtime, đơn điệu theo cấp,
   không vai trò con người nào ghi được thực thể nghiệp vụ, không ai xuyên dự án.

Cố tình **không** test ở Phase A:

```text
BLOCKED — runtime implementation belongs to Phase B/C/D/E
  - Mini CRM CRUD cho project/area                        → Phase B
  - từ chối cục bộ khi archive cha còn con sống           → Phase B
  - backend chiếu project/area                            → Phase D
  - backend TỪ CHỐI tự tạo cha                            → Phase D
  - thứ tự bốn tầng cưỡng chế ở tầng nhận                 → Phase D
  - phạm vi dự án chặn truy cập xuyên dự án               → Phase E
  - không route backend nào ghi được thực thể nghiệp vụ   → Phase E
```

---

## Bảng kiểm nghiệm thu Phase A

| # | Cổng | Câu trả lời | Trạng thái |
|---|---|---|---|
| 1 | Project là gì? | A1.1 | ✅ |
| 2 | Area là gì? | A1.2 | ✅ |
| 3 | Unit là gì? | A1.3 | ✅ |
| 4 | Deal là gì? | A1.4 | ✅ |
| 5 | Ai sở hữu từng thực thể? | A2.1 | ✅ |
| 6 | Nguồn sự thật chuẩn? | A0, A2.1, A2.2 | ✅ |
| 7 | Định danh ổn định? | A3.1–A3.4 | ✅ |
| 8 | Tham chiếu cha–con kiểm thế nào? | A3.5, A5.3 | ✅ |
| 9 | Phiên bản xếp thứ tự thế nào? | A4.1, A4.2 | ✅ |
| 10 | Xoá/tombstone biểu diễn thế nào? | A1.9, A4.4 | ✅ |
| 11 | Tên sự kiện? | A5.1 | ✅ |
| 12 | Thứ tự sự kiện cha/con? | A5.2, A5.3 | ✅ |
| 13 | v1 và v2 cùng tồn tại thế nào? | A6 + `sync_contract_v2_draft.md` | ✅ |
| 14 | Phân quyền theo dự án hoạt động thế nào? | A7 + `authorization_matrix.json` | ✅ |
| 15 | Mồ côi / cũ / trùng / đụng độ / sai thứ tự? | A4.2, A4.4, A5.3 | ✅ |

**Cổng ra Phase A:** *No ambiguous parent-child or source-of-truth rule remains.*
→ **ĐẠT.**

---

## §S — Quyết định đã bị thay thế (giữ nguyên làm lịch sử)

Bản 2026-08-12 (f) đóng băng một mô hình sở hữu **khác**. Giữ lại đầy đủ ở đây để
không ai phải đoán tại sao repo từng có `proposed_*` trong hợp đồng và một quy
trình duyệt phân khu.

```text
S-1  BỊ THAY THẾ: "Project — backend sở hữu tuyệt đối; v2 KHÔNG có thực thể
     `project`; dự án không bao giờ đi lên qua đường đồng bộ."
     THAY BẰNG: §A1.1 — Mini CRM sở hữu Project; v2 CÓ thực thể `project`.

S-2  BỊ THAY THẾ: "Area — mô hình 'hệ nguồn ĐỀ XUẤT, người vận hành DUYỆT'. Bản
     ghi hạ cánh ở status='pending', dùng được sau khi admin duyệt và đặt
     total_units/bedrooms/area_sqm. Ba trường kế hoạch đi trong hợp đồng dưới tên
     `proposed_*` và phía nhận bị cấm ghi thẳng chúng."
     THAY BẰNG: §A1.2 — Mini CRM sở hữu Area VÀ ba trường kế hoạch; chúng BẮT BUỘC
     và CÓ THẨM QUYỀN; không có bước duyệt nào.
     Ba mã lỗi AREA_PENDING_APPROVAL, AREA_IDENTITY_IMMUTABLE và endpoint
     POST /areas/{id}/approve BỊ BỎ.
     Cột status/reviewed_by/reviewed_at/review_reason của migration 0002 KHÔNG bị
     gỡ (gỡ cần migration), nhưng KHÔNG đường ghi nào của Phase B–H dùng chúng cho
     quy trình duyệt — §A0.1 nhóm bốn.

S-3  BỊ THAY THẾ: "area_name/unit_type BẤT BIẾN sau khi duyệt."
     THAY BẰNG: §A1.2 — hai trường này SỬA ĐƯỢC, hệ nguồn sở hữu. Ràng buộc còn
     lại là khoá tự nhiên uq_areas_project_name_unit_type.

S-4  BỊ THAY THẾ: "area_ref có BA hình dạng ở v2 (external_area_id | area_id |
     area_name+unit_type)."
     THAY BẰNG: §A3.7 — v2 chấp nhận DUY NHẤT external_area_id.

S-5  BỊ THAY THẾ: "project_ref chỉ có một hình dạng {project_id} UUID."
     THAY BẰNG: §A3.7 — v2 chấp nhận {external_project_id} (chuẩn) hoặc
     {project_id} (tương thích).

S-6  BỊ THAY THẾ: "admin ghi được Project/Area ở backend."
     THAY BẰNG: §A7.2 — KHÔNG vai trò backend nào ghi được bất kỳ thực thể nghiệp
     vụ nào.

S-7  BỊ THAY THẾ (một phần): "D-1 → chốt mô hình đề xuất–duyệt."
     D-1 giờ được chốt theo hướng NGƯỢC LẠI bởi chỉ đạo kiến trúc của chủ dự án:
     Mini CRM là nguồn sự thật chuẩn cho cả bốn tầng.
```

**Không bị thay thế** (giữ nguyên từ bản (f)): phân cấp bốn tầng; Project ≠ tenant;
không tầng tổ chức; luật `external_id` bất biến/không tái sử dụng; phạm vi duy nhất
theo `source_instance_id`; luật phiên bản và bảng quyết định sáu nhánh; không xoá
vật lý; không cascade; cha không archive được khi con còn sống; deal không mang
project_ref/area_ref; JOIN thay vì phi chuẩn hoá; reject thay vì defer/dead-letter;
v1 bất biến; phạm vi dự án tĩnh, fail-closed, cưỡng chế ở tầng truy vấn.

---

## Quyết định còn lại

Không mục nào chặn cổng ra Phase A. §D-2 **chặn phần FE-ghi của Phase F/G**.

```text
D-2  XÁC THỰC GHI CỦA MINI CRM — MỚI, NGHIÊM TRỌNG.  Xem §A7.5.
     Mini CRM giờ là hệ thống bản ghi và có đường ghi từ FE, nhưng không có xác
     thực nào. Yêu cầu đã đóng băng; CƠ CHẾ chưa chốt.

D-3  XOÁ UNIT KHI CÒN DEAL SỐNG — Phase A CHỐT là từ chối (§A1.8), nhưng đó là
     THAY ĐỔI so với hành vi hôm nay. Nên được chủ dự án xác nhận trước Phase B.

D-4  ĐÍCH GHI CỦA FE — ghi thẳng Mini CRM (:8100), hay qua một cổng ghi tường
     minh? Ràng buộc đã chốt: KHÔNG BAO GIỜ ghi vào backend. Cơ chế chưa chọn.
     Kèm theo: FE nói chuyện với hai đích, nên độ trễ giữa "ghi xong" và "backend
     đã thấy" phải nhìn thấy được ở giao diện.

D-5  RETRY TỰ ĐỘNG CHO OUTBOX — hôm nay resend là THỦ CÔNG, có chủ đích.

D-6  TẦNG TỔ CHỨC / TENANT — Phase A chốt KHÔNG có. Nếu nhiều chủ đầu tư sẽ dùng
     chung một cài đặt, câu trả lời phải đến TRƯỚC Phase E.

D-7  NGƯỠNG ĐỘ TƯƠI (STALE_AFTER_MS) — vẫn BLOCKED (pipeline_status 2026-08-12 (e)).

D-8  TỪ VỰNG TRẠNG THÁI CĂN — backend không có bảng alias cho căn.

D-9  ĐỤNG ĐỘ CÙNG REVISION — backend GHI NHẬN và giữ bản cũ. Dưới mô hình mới,
     cách giải ĐÚNG là hệ nguồn phát lại với revision cao hơn; ai theo dõi và làm
     việc đó thì chưa ai được giao.

D-10 DI TRÚ DỮ LIỆU HIỆN CÓ — MỚI. Dự án và phân khu đang có ở backend được tạo
     qua ProjectService và KHÔNG có external_id. Chúng trở thành dòng "không có
     hệ nguồn". Ai cấp external_id cho chúng, và Mini CRM có phải nhận nuôi chúng
     không?  REQUIRED IN PHASE D — NOT IMPLEMENTED NOW.

D-11 NGUỒN ẢNH CHỤP ĐẶC TRƯNG KHẢO SÁT — điều kiện tiên quyết của Phase 6.
```
