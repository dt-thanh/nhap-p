# Canonical ID, quyền sở hữu dữ liệu, và chính sách seed data (CP1)

## 1. Nguồn sự thật

| Thực thể | Chủ sở hữu vận hành | Vai trò của Product/AbsorbIQ |
|---|---|---|
| Project | **Mini CRM** | soi gương (mirror), phân tích |
| Area | **Mini CRM** | soi gương, phân tích |
| Unit | **Mini CRM** | soi gương, xếp hạng/hấp thụ |
| Deal | **Mini CRM** | soi gương, doanh số/dự báo |
| Ranking / forecast / absorption result | **Product** | Mini CRM KHÔNG ghi |
| Reference/config (calculator flags, band) | **Product** | Mini CRM KHÔNG ghi |

Một chiều: `Mini CRM → outbox → relay → Product sync API → Product DB → ACK`.
Product **không bao giờ** ghi ngược vào Mini CRM.

## 2. Canonical ID

Khoá liên kết giữa hai hệ là bộ ba:

```
(source_system, source_instance_id, external_id)
```

- `external_id` do **Mini CRM** cấp và bất biến trong vòng đời bản ghi.
- Khoá chính (`UUID`) của mỗi DB là **cục bộ**, không mang qua ranh giới.
- **Không bao giờ khớp bằng `name`.** Tên đổi được và không duy nhất; hai dự án
  cùng tên ở hai chủ đầu tư là chuyện bình thường.

Product DB đã có sẵn ba cột này trên `projects`/`areas`/`units` (migration 0017,
`src/models/tables.py`), nên CP1 **không cần schema mới** — chỉ cần siết dữ liệu
đang có về đúng bất biến đó.

## 3. Ánh xạ

| Mini CRM | Product | Khoá khớp |
|---|---|---|
| `crm_projects.external_id` | `projects.external_id` | `(source_system, source_instance_id, external_id)` |
| `crm_areas.external_id` | `areas.external_id` | như trên, + `projects.id` làm cha |
| `crm_units.external_id` | `units.external_id` | như trên, + `areas.id` làm cha |
| `crm_deals.external_id` | biểu diễn bán hàng của unit | qua `external_unit_id` |

Trạng thái đồng bộ ở phía Mini CRM: `source_revision` (tăng mỗi lần ghi),
`mirrored_revision` + `mirrored_at` (đóng dấu khi Product ACK). Bất biến:
`mirrored_revision <= source_revision`; bằng nhau ⇒ đã đồng bộ. Cập nhật dùng
`GREATEST(...)` nên một ACK đến muộn không kéo trạng thái lùi.

## 4. Chính sách seed / demo data ở Product DB

Phân loại (áp dụng khi chạy `scripts/reconcile_product_seed.py`):

1. **Config/reference** — `absorption_calculator`, band, ngưỡng. **GIỮ.**
   Không có `external_id` và không thuộc quyền sở hữu của Mini CRM.
2. **Kết quả tính toán** — ranking, forecast, absorption snapshot. **GIỮ.**
   Sinh lại được từ dữ liệu nguồn, nhưng xoá sẽ làm hỏng lịch sử đối chiếu.
3. **Bản ghi vận hành do Mini CRM sở hữu** (`source_system = 'mini_crm'`) —
   **để đồng bộ quản lý.** Không sửa tay.
4. **Bản ghi di sản** (`external_id IS NULL`) — tạo trước Phase D bởi
   `ProjectService`. **KHÔNG bịa danh tính cho chúng.** Hoặc gán `external_id`
   một cách CÓ CHỦ ĐÍCH (đối chiếu thủ công), hoặc để yên và chấp nhận chúng
   nằm ngoài phạm vi đồng bộ.
5. **Xung đột** — cùng `external_id` nhưng khác `source_instance_id`, hoặc một
   bản ghi di sản trùng tên với một dự án Mini CRM. **Phải xử lý bằng tay**;
   script chỉ báo cáo, không tự quyết.

Quy tắc chống trùng `P001`: bản ghi Mini CRM luôn mang `source_system='mini_crm'`
+ `external_id='P001'`; một seed cũ tên `P001` mà `external_id IS NULL` là một
**thực thể khác** dưới mắt hệ thống. Script sẽ báo cặp này là nghi vấn trùng để
người vận hành quyết định gộp hay giữ riêng — nó **không** tự gộp.

## 5. RUNTIME_VERIFICATION_REQUIRED

Mọi kết luận ở §4 phải được xác nhận bằng `--dry-run` trên Product DB thật trước
khi chạy chế độ ghi. Sandbox phát triển không có PostgreSQL nên chưa chạy được.
