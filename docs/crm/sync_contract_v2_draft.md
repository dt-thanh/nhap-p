# Hợp đồng đồng bộ CRM — v2 (DỰ THẢO ĐÃ ĐÓNG BĂNG Ở PHASE A, bản sửa đổi)

> **`PROPOSED — NOT IMPLEMENTED`.**
> `src/services/json_payload.py:28` vẫn là `SUPPORTED_SCHEMA_VERSIONS = frozenset({1})`
> và `:33` vẫn là `SUPPORTED_ENTITIES = frozenset({"units", "deals"})`. **Không phong
> bì v2 nào được phía nhận chấp nhận tại thời điểm này.** Bật v2 là
> `REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`.
>
> Schema: `src/contracts/crm_sync_v2.schema.json` và bản sao byte-identical
> `minicrm/contracts/crm_sync_v2.schema.json`.
> Quyết định miền: [`phase_a_domain_freeze.md`](phase_a_domain_freeze.md).
> Hợp đồng v1 (**bất biến**): [`sync_contract_v1_draft.md`](sync_contract_v1_draft.md).
>
> **Đây là bản sửa đổi.** Bản v2 trước (2026-08-12, đợt (f)) mô hình hoá backend
> sở hữu Project/Area với quy trình đề xuất–duyệt. Bản này **thay thế** nó theo
> chỉ đạo kiến trúc mới: **Mini CRM là nguồn sự thật cho cả bốn tầng.** Nội dung bị
> thay thế được giữ nguyên ở `phase_a_domain_freeze.md` §S.

---

## 1. v1 là bất biến

`FROZEN`. Không đổi, không bị ảnh hưởng bởi bản sửa đổi này.

```text
src/contracts/crm_sync_v1.schema.json
minicrm/contracts/crm_sync_v1.schema.json
SHA-256 (cả hai, KHÔNG ĐỔI qua cả hai đợt v2):
  e15fd9c5e685923fcf3f537c7dba4e900632ae7d6723df654e35b55efb49a92a
```

---

## 2. Khác biệt NỀN TẢNG giữa v1 và v2 — không phải một bản mở rộng cộng thêm

`FROZEN`, và đây là điều quan trọng nhất của tài liệu này.

```text
v1:  projects/areas THUỘC PHÍA NHẬN.  Hệ nguồn chỉ THAM CHIẾU chúng.
     entity ∈ {unit, deal}

v2:  projects/areas THUỘC HỆ NGUỒN.  Phía nhận CHỈ SOI GƯƠNG.
     entity ∈ {project, area, unit, deal}
```

Vì hai mô hình sở hữu là hai thứ khác nhau — không phải cùng một mô hình với thêm
trường — **v2 không phải là "v1 cộng area"**. Nó là một hợp đồng riêng mang cùng
khung xương (phong bì, phiên bản, tombstone, thứ tự) nhưng khác hẳn ở tầng sở hữu.

Hệ quả thực dụng: một hệ nguồn **không** chuyển từ v1 sang v2 bằng cách thêm bản
ghi `area`. Nó chuyển bằng cách **đổi vai trò của mình** từ "khách tham chiếu"
sang "chủ sở hữu", và điều đó phải là một quyết định tường minh của người vận
hành hệ nguồn đó, không phải một nâng cấp phiên bản im lặng.

---

## 3. v2 thay đổi những gì so với v1

```text
1.  record.entity :  ["unit","deal"]  →  ["project","area","unit","deal"]

2.  project_ref   :  {project_id}  →  {external_project_id} | {project_id}
                     Hình dạng CHUẨN đổi sang external_project_id: hệ nguồn
                     không thể biết UUID nội bộ của một dự án chính nó vừa tạo.

3.  area_ref      :  {area_id} | {area_name,unit_type}  →  {external_area_id}
                     CHỈ MỘT hình dạng. Hai hình dạng cũ BỊ BỎ, không giữ song
                     song — xem §4.

4.  $defs/project_payload, $defs/project_payload_partial     MỚI
    $defs/area_payload, $defs/area_payload_partial            MỚI, và khác hẳn
                     bản nháp trước: BA TRƯỜNG KẾ HOẠCH LÀ BẮT BUỘC VÀ CÓ THẨM
                     QUYỀN, không còn tiền tố `proposed_` và không còn tính
                     "gợi ý không ràng buộc".

5.  record.allOf  →  hai nhánh MỚI: entity="project", entity="area"

6.  records.description : thứ tự BỐN tầng thay vì hai/ba
                          project → area → unit → deal  (đảo khi xoá)

7.  snapshot_scope.entities : thêm "project", "area"
```

---

## 4. Vì sao v2 BỎ hai hình dạng `area_ref` cũ thay vì giữ cả ba

Bản nháp trước giữ `{area_id}` và `{area_name, unit_type}` bên cạnh hình dạng mới,
lý luận rằng nhiều hình dạng cho phép hệ nguồn chọn cái ổn định nhất. Dưới mô hình
sở hữu mới, lý do đó không còn đúng:

* **`{area_id}`** là UUID **nội bộ của phía nhận**. Ở v1, phân khu thuộc phía nhận
  nên hệ nguồn *có thể* đã được cấu hình biết UUID đó. Ở v2, phân khu thuộc hệ
  nguồn — hệ nguồn tạo phân khu bằng `external_id` của chính nó và **không có lý
  do gì để biết** UUID nội bộ của phía nhận. Giữ hình dạng này sẽ mời một cách
  tham chiếu vòng: hệ nguồn phải hỏi ngược phía nhận UUID của một thứ chính nó sở
  hữu.
* **`{area_name, unit_type}`** là khoá tự nhiên. Ở v1 nó an toàn vì phía nhận sở
  hữu và duyệt tên phân khu — tên hiếm khi đổi và khi đổi thì có kiểm soát. Ở v2,
  `area_name`/`unit_type` là trường **hệ nguồn sở hữu và sửa được tuỳ ý**
  (§A1.2). Tham chiếu theo tên sẽ đứt ngay lần đổi tên đầu tiên, và không có cách
  nào phân biệt "đổi tên phân khu" với "phân khu mới trùng cách gọi cũ".

`FROZEN`: v2 chấp nhận **duy nhất** `{external_area_id}`. Đơn giản hơn, và đúng
với việc ai sở hữu danh tính nào.

`project_ref` giữ lại `{project_id}` như hình dạng **tương thích**, không phải
hình dạng **chuẩn** — dùng được khi một cài đặt cụ thể đã được cấu hình ánh xạ
UUID sẵn, nhưng **không dùng được** trong lô đầu tiên tạo ra dự án đó (lúc đó chưa
có UUID nào để tham chiếu).

---

## 5. `project_payload` và `area_payload`

### 5.1 `project_payload`

```json
{ "name": "Khu do thi Ben Xanh", "launch_date": "2026-06-01" }
```

Hai trường, cả hai **bắt buộc và có thẩm quyền**. `launch_date` là **ngày lịch**
(`format: date`, không có offset múi giờ) — nó là một sự kiện thương mại công bố
theo lịch địa phương, không phải một mốc có múi giờ; fixture `27` chứng minh
`format: date-time` bị chặn ở đây.

Tập trường **cố ý hẹp**. Các cột phía nhận đang có mà không nằm trong hợp đồng
(`headline`, `introduce`, `cover_image_url`, `absorption_calculator`) là chú thích
cục bộ của phía nhận — xem `phase_a_domain_freeze.md` §A2.3.

### 5.2 `area_payload`

```json
{
  "area_name": "A1",
  "unit_type": "2PN",
  "bedrooms": 2,
  "area_sqm": 68.5,
  "total_units": 120
}
```

Năm trường, **tất cả bắt buộc và có thẩm quyền**. Đây là khác biệt lớn nhất so
với bản nháp trước: **không còn tiền tố `proposed_`, không còn bước duyệt.**
Phía nhận ghi thẳng cả năm trường vào bản sao.

`FROZEN`: **`total_units` là số KẾ HOẠCH do hệ nguồn công bố, không phải số đếm
bản ghi `unit` đã gửi.** Phía nhận không bao giờ suy nó bằng cách đếm — một phân
khu 120 căn mới gửi 40 căn `unit` vẫn có mẫu số 120.

Ba cột kế hoạch **NOT NULL** ở phía nhận với CHECK dương (`ck_areas_area_sqm_positive`
v.v., `alembic/versions/0001_initial_schema.py` `VERIFIED`), nên hệ nguồn **phải**
cung cấp cả năm trường trong một `upsert` đầy đủ; không có giá trị mặc định nào
được điền hộ. Fixture `23` chứng minh thiếu trường kế hoạch bị chặn ở tầng schema.

---

## 6. Ma trận tương thích v1 / v2

| Khía cạnh | v1 | v2 | Quan hệ |
|---|---|---|---|
| Sở hữu Project/Area | Phía nhận | **Hệ nguồn** | **KHÁC MÔ HÌNH**, không tương thích ngữ nghĩa |
| `schema_version` | `const: 1` | `const: 2` | Loại trừ lẫn nhau ở tầng schema |
| `entity` | `unit`, `deal` | `project`, `area`, `unit`, `deal` | Siêu tập về mặt CÚ PHÁP, khác hẳn về NGỮ NGHĨA |
| `project_ref` | `{project_id}` | `{external_project_id}` \| `{project_id}` | Hình dạng chuẩn ĐỔI |
| `area_ref` | 2 hình dạng | 1 hình dạng (`external_area_id`) | Hình dạng ĐỔI, không phải mở rộng |
| `area_payload` | *(không tồn tại — area thuộc phía nhận)* | 5 trường bắt buộc, có thẩm quyền | Mới hoàn toàn |
| `unit_payload` | có | không đổi | Giống hệt |
| `deal_payload` | có | không đổi | Giống hệt |
| Luật phiên bản | §5 v1 | y hệt, mở rộng lên 4 tầng | Giống hệt về LUẬT |
| Thứ tự `records[]` | unit → deal | project → area → unit → deal (đảo khi xoá) | Mở rộng |
| Trần lô | 5000 | 5000 | Giống hệt |
| `additionalProperties` | `false` khắp nơi | `false` khắp nơi | Giống hệt |

### 6.1 Hai phiên bản LOẠI TRỪ LẪN NHAU ở tầng schema

`schema_version` là `const`, nên **một phong bì v1 không hợp lệ theo schema v2, và
ngược lại.** Không có nâng cấp ngầm: một hệ nguồn muốn chuyển sang sở hữu Project/
Area phải đổi `schema_version` thành `2` một cách tường minh.

### 6.2 Quy tắc chấp nhận của phía nhận

```text
schema_version ∉ SUPPORTED_SCHEMA_VERSIONS  →  UNSUPPORTED_SCHEMA_VERSION, 422   VERIFIED
schema_version không phải số nguyên          →  INVALID_SCHEMA_VERSION            VERIFIED

Hôm nay (Phase A):     SUPPORTED_SCHEMA_VERSIONS = {1}
Sau Phase D (đề xuất): SUPPORTED_SCHEMA_VERSIONS = {1, 2}
```

`FROZEN`: **hệ nguồn v1 không bao giờ phải sửa gì.** v1 tiếp tục hoạt động vô thời
hạn dưới mô hình sở hữu cũ của nó (phía nhận sở hữu Project/Area) — bật v2 không
ép ai chuyển đổi.

### 6.3 Chính sách ngừng dùng

Không đổi so với bản trước: v1 không có ngày hết hạn ở thời điểm này (chưa có hệ
nguồn THẬT nào tồn tại); gỡ nó khỏi `SUPPORTED_SCHEMA_VERSIONS` là một thay đổi
phá vỡ, cần một entry `pipeline_status.md` riêng, không bao giờ là tác dụng phụ.

---

## 7. Quy tắc băm và song hành hai bản sao

`FROZEN`, không đổi:

```text
src/contracts/crm_sync_v1.schema.json  ==  minicrm/contracts/crm_sync_v1.schema.json
src/contracts/crm_sync_v2.schema.json  ==  minicrm/contracts/crm_sync_v2.schema.json
```

SHA-256 v2 sau bản sửa đổi này: `9620614a46536515fabeae1e9ba1e032c30deb02a74656e11818b1951fe10efb`
(đổi so với bản (f) — đúng như mong đợi, vì v2 còn là dự thảo và **được phép** sửa
cho tới khi Phase D bật nó).

---

## 8. Ví dụ phong bì

Fixture trong `docs/crm/fixtures/`, tiền tố `SYNTH-` / `synthetic-` theo quy ước có sẵn.

### 8.1 Hợp lệ theo schema v2

| Fixture | Nội dung |
|---|---|
| `18_v2_project_created.json` | Một dự án, lần đầu — chứng minh Project ĐI QUA đường đồng bộ ở v2 |
| `19_v2_full_hierarchy_ordered.json` | Lô trộn bốn tầng, ĐÚNG thứ tự `project → area → unit → deal` |
| `20_v2_delete_reverse_order.json` | Lô xoá, đúng thứ tự ngược `deal → unit → area → project` |
| `21_v2_partial_updates_each_tier.json` | Cập nhật `partial` ở cả bốn tầng cùng một lô |
| `22_v2_project_ref_by_backend_uuid.json` | `project_ref` dùng hình dạng tương thích `{project_id}` |

### 8.2 KHÔNG hợp lệ theo schema v2

| Fixture | Vì sao bị chặn |
|---|---|
| `23_v2_area_without_planning_fields.json` | Thiếu `bedrooms`/`area_sqm`/`total_units` — cả năm trường của `area_payload` đều bắt buộc |
| `24_v2_area_ref_by_name_removed_in_v2.json` | Dùng hình dạng `{area_name, unit_type}` của v1 — v2 KHÔNG chấp nhận, chỉ `external_area_id` |
| `25_v2_project_payload_with_parent_ref.json` | `project_payload` mang `project_ref` — Project là gốc, không có cha |
| `26_v2_delete_carrying_payload.json` | `operation: delete` mang `payload` — bị cấm |
| `27_v2_launch_date_with_timezone.json` | `launch_date` mang offset múi giờ — nó là NGÀY LỊCH, không phải mốc thời gian |

### 8.3 Hợp lệ theo schema, SAI theo quy tắc nghiệp vụ

| Fixture | Vì sao vẫn sai |
|---|---|
| `28_v2_child_before_parent.json` | Căn đứng trước phân khu của nó — thứ tự là hợp đồng nhưng không phải schema |
| `29_v2_project_record_mismatches_envelope.json` | Bản ghi `project` có `external_id` KHÁC `project_ref.external_project_id` của phong bì — quy tắc §A3.5, không diễn đạt được bằng JSON Schema thuần |

---

## 9. Mã lỗi mới do v2 sinh ra

`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`.

| `error_code` | HTTP | `error_category` | Khi nào |
|---|---|---|---|
| `PROJECT_NOT_FOUND` | 422 | `business` | `project_ref` không tra được VÀ lô không chứa bản ghi `project` tạo nó |
| `PROJECT_REF_MISMATCH` | 422 | `business` | Bản ghi `project` có `external_id` khác `project_ref` của phong bì |
| `AREA_NOT_FOUND` | — | `business` | `area_ref` không tra được (giữ tên từ v1, hành vi mở rộng) |
| `AREA_NATURAL_KEY_CONFLICT` | 409 | `business` | Đổi `area_name`/`unit_type` trùng một cặp đã tồn tại trong cùng dự án |
| `PARENT_ARCHIVED` | — | `business` | Upsert một con vào cha đã archive |
| `AREA_CROSS_PROJECT_MOVE` | — | `business` | `area_ref` trỏ phân khu thuộc dự án khác |
| `PARENT_HAS_LIVE_CHILDREN` | 409 | `business` | Archive/tombstone cha khi còn con sống |

**Bị bỏ so với bản nháp trước** (thuộc mô hình đề xuất–duyệt đã thay thế):
`AREA_PENDING_APPROVAL`, `AREA_IDENTITY_IMMUTABLE`.

Mã đã có và không đổi: `UNSUPPORTED_SCHEMA_VERSION`, `INVALID_SCHEMA_VERSION`,
`UNSUPPORTED_ENTITY`, `ENTITY_MISMATCH`, `MISSING_SOURCE_VERSION`,
`UNKNOWN_UNIT_REFERENCE`, `HISTORY_TIMESTAMP_DROPPED`,
`DUPLICATE_SOURCE_RECORD_ID`, `CONSTRAINT_VIOLATION` `VERIFIED`.

Mọi lỗi ánh xạ vào cột đã có của `upload_errors` — v2 không đòi thêm cột lỗi nào.

---

## 10. Việc Phase D phải làm để bật v2

`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`:

```text
1.  SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})     src/services/json_payload.py:28
2.  SUPPORTED_ENTITIES        += {"projects","areas"} src/services/json_payload.py:33
3.  Chọn schema theo schema_version của phong bì      src/services/contract_validation.py
4.  Đường nhận POST /sync/projects, POST /sync/areas  src/api/sync.py
5.  DomainProjector._project_project, _project_area
    (soi gương, KHÔNG duyệt)                          src/services/domain_projection.py
6.  Bốn cột source_* + external_id + ràng buộc duy
    nhất cho CẢ projects VÀ areas                     MIGRATION REQUIRED (0017)
7.  Cưỡng chế thứ tự bốn tầng ở tầng nhận              src/services/sync_runs.py
8.  Bảy mã lỗi mới ở §9
9.  GỠ/GIỚI HẠN bốn đường ghi hiện có vào projects/areas
    (ProjectService.create_project/create_area/
    update_project/update_area)                        src/services/projects.py
    — xem phase_a_domain_freeze.md §A2.4
10. Kế hoạch DI TRÚ cho dự án/phân khu hiện có KHÔNG có
    external_id (§D-10 của phase_a_domain_freeze.md)
```

Điểm 6 và điểm 10 là khác biệt lớn nhất so với bản nháp trước: bản trước chỉ cần
migration cho `areas`; bản này cần cho **cả hai** bảng, và cần thêm một kế hoạch di
trú vì dữ liệu hiện có ở backend không có `external_id`.
