# Fixture tổng hợp cho hợp đồng đồng bộ CRM v1

> **ĐÂY LÀ DỮ LIỆU TỔNG HỢP DO CHÚNG TÔI TỰ BỊA RA.**
>
> Tại thời điểm viết, **Mini CRM chưa tồn tại**. Không có payload thật nào từ một
> CRM thật được dùng ở đây. Mọi mã căn, mã giao dịch, tên phân khu và mốc thời
> gian trong thư mục này đều do người viết đặt ra để kiểm thử phía nhận.
>
> Các fixture này chứng minh **phía nhận cư xử đúng như hợp đồng mô tả**.
> Chúng **không** chứng minh — và không bao giờ có thể chứng minh — rằng một
> Mini CRM tương lai sẽ gửi được đúng hình dạng này. Điều đó chỉ được chứng minh
> khi có payload thật, ở Phase 11.

## Quy ước đặt tên để không nhầm với dữ liệu thật

| Thành phần | Tiền tố bắt buộc | Ví dụ |
|---|---|---|
| `source_instance_id` | `synthetic-` | `synthetic-mini-crm` |
| `external_batch_id` | `SYNTH-BATCH-` | `SYNTH-BATCH-0001` |
| `external_id` của căn | `SYNTH-U-` | `SYNTH-U-0001` |
| `external_id` của giao dịch | `SYNTH-D-` | `SYNTH-D-0001` |
| `snapshot_id` | `SYNTH-SNAP-` | `SYNTH-SNAP-0001` |

Tiền tố không phải để cho đẹp. Nó là thứ khiến một câu truy vấn duy nhất phân
biệt được dữ liệu kiểm thử với dữ liệu vận hành, và khiến việc vô tình mô tả
fixture như "dữ liệu CRM" trở nên khó xảy ra hơn.

## Danh sách fixture

| File | Mô tả | Kết quả mong đợi |
|---|---|---|
| `01_units_incremental.json` | Ba căn, lô tăng dần | 3 × `insert` |
| `02_deals_incremental.json` | Hai giao dịch trên các căn ở lô 01 | 2 × `insert` |
| `03_replay_same_batch.json` | Đúng `external_batch_id` của lô 01 | trả kết quả cũ, không xử lý lại |
| `04_stale_update.json` | Bản ghi có `source_revision` CŨ hơn | `skip_stale` |
| `05_same_version_conflict.json` | Cùng `source_revision`, nội dung KHÁC | `conflict`, giữ nguyên trạng thái đã nhận |
| `06_explicit_delete.json` | `operation: delete` | `tombstone` |
| `07_snapshot_complete.json` | Ảnh chụp đủ mảnh, thiếu một căn | căn vắng mặt bị tombstone |
| `08_snapshot_incomplete.json` | `snapshot_complete: false` | KHÔNG tombstone gì cả |
| `09_deal_before_unit.json` | Giao dịch trỏ tới căn chưa tồn tại | từ chối có cấu trúc |
| `10_unknown_area.json` | `area_ref` không tra được | từ chối, KHÔNG tự tạo phân khu |
| `11_unknown_status.json` | Giá trị trạng thái ngoài bảng ánh xạ | từ chối, KHÔNG mặc định |
| `12_naive_timestamp.json` | Timestamp thiếu offset múi giờ | từ chối |
| `13_deal_history_preserved.json` | `reserved → sold`, VẪN mang `reserved_at` | `update` — hành vi A4 đòi hỏi |
| `14_deal_history_dropped.json` | `reserved → sold`, ĐÁNH RƠI `reserved_at` | từ chối `HISTORY_TIMESTAMP_DROPPED` |
| `15_deal_history_cleared.json` | `"reserved_at": null` tường minh | nhận, xoá mốc, kèm cảnh báo |
| `16_deal_partial_update.json` | `payload_completeness: partial` | `update`, `reserved_at` cũ giữ nguyên |
| `17_partial_without_base.json` | Bản ghi partial cho giao dịch chưa từng có | từ chối `PARTIAL_UPDATE_WITHOUT_BASE` |

## Fixture hợp đồng v2 (Phase A) — `PROPOSED — NOT IMPLEMENTED`

> Fixture 18–29 mang `schema_version: 2`. Phía nhận **CHƯA chấp nhận v2**
> (`SUPPORTED_SCHEMA_VERSIONS = {1}`), nên gửi bất kỳ cái nào trong số chúng sẽ
> nhận `UNSUPPORTED_SCHEMA_VERSION` (422). Chúng tồn tại để ĐÓNG BĂNG hình dạng
> hợp đồng v2, không phải để chạy qua đường nhận. Xem
> [`../sync_contract_v2_draft.md`](../sync_contract_v2_draft.md).
>
> **Bản sửa đổi (đợt (g)): mô hình sở hữu đổi thành HỆ NGUỒN LÀ NGUỒN SỰ THẬT**
> cho cả bốn tầng Project/Area/Unit/Deal — backend chỉ soi gương. Bộ fixture 18–26
> của đợt (f) (mô hình backend sở hữu Area, hệ nguồn chỉ đề xuất) đã bị **thay
> thế hoàn toàn**, không giữ song song. Xem
> [`../phase_a_domain_freeze.md`](../phase_a_domain_freeze.md) §S để đọc quyết
> định cũ.

| File | Mô tả | Kết quả mong đợi |
|---|---|---|
| `18_v2_project_created.json` | Một dự án, lần đầu | hợp lệ — Project ĐI QUA đường đồng bộ ở v2 |
| `19_v2_full_hierarchy_ordered.json` | Lô trộn bốn tầng, ĐÚNG thứ tự `project → area → unit → deal` | hợp lệ |
| `20_v2_delete_reverse_order.json` | Lô xoá, đúng thứ tự NGƯỢC `deal → unit → area → project` | hợp lệ |
| `21_v2_partial_updates_each_tier.json` | Cập nhật `partial` ở cả bốn tầng cùng một lô | hợp lệ |
| `22_v2_project_ref_by_backend_uuid.json` | `project_ref` dùng hình dạng tương thích `{project_id}` | hợp lệ |
| `23_v2_area_without_planning_fields.json` | Thiếu `bedrooms`/`area_sqm`/`total_units` | từ chối — cả năm trường `area_payload` đều bắt buộc |
| `24_v2_area_ref_by_name_removed_in_v2.json` | Dùng hình dạng `{area_name, unit_type}` của v1 | từ chối — v2 CHỈ chấp nhận `external_area_id` |
| `25_v2_project_payload_with_parent_ref.json` | `project_payload` mang `project_ref` | từ chối — Project là gốc, không có cha |
| `26_v2_delete_carrying_payload.json` | `operation: delete` mang `payload` | từ chối |
| `27_v2_launch_date_with_timezone.json` | `launch_date` mang offset múi giờ | từ chối — đó là NGÀY LỊCH, không phải mốc thời gian |
| `28_v2_child_before_parent.json` | Căn đứng TRƯỚC phân khu của nó | **hợp lệ theo schema, SAI theo hợp đồng** |
| `29_v2_project_record_mismatches_envelope.json` | Bản ghi `project` có `external_id` khác `project_ref` | **hợp lệ theo schema, SAI theo hợp đồng** |

Fixture `28` và `29` là hai cái đáng đọc kỹ nhất trong nhóm này. JSON Schema
**không diễn đạt được** thứ tự phần tử theo `entity`, cũng không so được hai giá
trị ở hai vị trí khác nhau trong cùng tài liệu — nên cả hai đi qua bộ kiểm schema
trong khi vẫn vi phạm quy tắc nghiệp vụ. Ai đọc "schema hợp lệ" rồi kết luận
"phong bì hợp lệ" sẽ sai đúng ở chỗ này — bắt chúng là việc của tầng nghiệp vụ
phía nhận (Phase D).

Fixture 03–12 và 14/17 mô tả các đường HỎNG. Chúng quan trọng ngang fixture đường
tốt: phần lớn thiệt hại của một tầng đồng bộ đến từ việc xử lý sai bản ghi hỏng,
chứ không phải từ việc xử lý sai bản ghi tốt.

Cặp `14` / `15` là cặp đáng đọc kỹ nhất: cùng một kết quả trên bản sao
(`reserved_at` trống), nhưng một cái bị từ chối còn một cái được nhận. Khác biệt
nằm ở Ý ĐỊNH — vắng mặt không phải một khẳng định, null tường minh thì có. Xem
hợp đồng mục 4.3.
