# Sổ lỗi

Ghi lại lỗi đã tái hiện được và cách sửa. Chỉ ghi nguyên nhân ĐÃ KIỂM CHỨNG,
không ghi phỏng đoán.

---

## BUG-CATALOG-CREATE-001 — Không tạo được dự án / phân khu

### Triệu chứng

Người dùng không có cách nào tạo dự án hay phân khu trong hệ thống.

Đã tái hiện và đo được:

- **Backend KHÔNG lỗi.** Gọi thẳng API trên stack Docker đang chạy:
  - `POST /api/v1/projects` → **201**, trả `project_id`, `status='active'`,
    bản ghi có thật trong PostgreSQL.
  - `POST /api/v1/areas` → **201**, `project_id` giữ đúng, `status='active'`.
  - Các ca lỗi đều đúng: thiếu `name` → 422 · `name` toàn khoảng trắng → 422 ·
    `launch_date` sai → 422 · `project_id` sai UUID → 422 · dự án không tồn tại
    → 404 `PROJECT_NOT_FOUND` · trùng phân khu → 409 `DUPLICATE_AREA`.
- **Frontend không có đường tạo.** Tìm toàn bộ `frontend/src`:
  - `api/endpoints.js` **không có** `createProject` / `createArea`; `api.post`
    chỉ dùng cho upload file và chat.
  - `pages/CatalogPage.jsx` chỉ gọi `updateProject` / `updateArea` (SỬA).
  - Không màn hình nào khác gọi `POST /projects` hay `POST /areas`.

Vì vậy dữ liệu chưa bao giờ được gửi đi — không phải request lỗi, mà là **không
có request nào cả**.

### Nguyên nhân gốc

Thiếu tính năng ở tầng giao diện, không phải lỗi backend.

Lượt phát triển trước đã làm endpoint tạo (backend) và trang sửa (frontend),
nhưng **chưa nối chức năng tạo vào giao diện**. Hệ quả là bế tắc con-gà-quả-trứng:
`CatalogPage` chỉ sửa được bản ghi đã có; với database rỗng thì danh sách dự án
trống, không chọn được gì, và không có nút nào để tạo dự án đầu tiên.

Đã loại trừ, có kiểm chứng:

| Nghi vấn | Kết quả kiểm tra |
|---|---|
| Sai đường dẫn / trùng `/api/v1` | `client.js` gắn `/api`, `endpoints.js` gắn `/v1` → `/api/v1/...` đúng |
| Router chưa đăng ký | `app.openapi()` trong container có đủ `POST /api/v1/projects`, `POST /api/v1/areas` |
| Schema Pydantic sai | Gọi thẳng API bằng payload đề bài → 201 |
| Service / transaction | 217 test xanh, gồm cả test rollback |
| Migration chưa chạy | `alembic current` = `alembic heads` = `0003_content_column_defaults` |
| Schema DB lệch | `headline`/`introduce` có `DEFAULT ''`, `status` có `DEFAULT 'active'`, đủ FK + `uq_areas_project_name_unit_type` |
| Docker chạy code cũ | Container có đủ route mới (compose mount `./src`) |
| CORS / URL | Gọi qua proxy Vite `localhost:5173/api/v1/...` → 201 |

### Ảnh hưởng

- Không tạo được dự án → **chặn toàn bộ luồng nạp dữ liệu**, vì
  `POST /files/upload` bắt buộc `project_id`.
- Không tạo được phân khu → `ImportService` không tra được `area_id`, mọi dòng
  bán hàng / tồn kho sẽ rớt với `AREA_NOT_FOUND`.
- Dashboard và Danh mục không có gì để hiển thị trên database mới.

### Cách sửa

| File | Thay đổi |
|---|---|
| `frontend/src/api/endpoints.js` | Thêm `createProject(payload)` và `createArea(payload)` gọi `POST /v1/projects` và `POST /v1/areas` |
| `frontend/src/pages/CatalogPage.jsx` | Thêm khối "Tạo dự án" và "Tạo phân khu"; tạo xong tự chọn dự án vừa tạo để thêm phân khu ngay; hiện dòng hướng dẫn khi chưa có dự án nào; khối sửa chỉ hiện khi đã có dữ liệu |
| `tests/test_api/test_catalog.py` | Thêm 3 test giá trị mặc định của `headline` / `introduce` / `cover_image_url` |

**Không sửa backend, không thêm migration** — cả hai đã đúng.

### Kiểm chứng

| Lệnh | Kết quả |
|---|---|
| `python -m compileall src` | OK |
| `alembic current` / `alembic heads` | `0003_content_column_defaults (head)` cả hai |
| `pytest -q` (có DB) | **217 passed** |
| `bash scripts/test_db.sh` | **217 passed** |
| `ruff check src/ tests/` | sạch |
| `cd frontend && npm run build` | xanh (`built in 2.52s`) |

E2E qua đúng đường người dùng đi (proxy Vite của container frontend):

```
POST localhost:5173/api/v1/projects  -> d1dd1e9d-… "Dự án từ UI"
POST localhost:5173/api/v1/areas     -> 459db99e-… "Tower UI" status=active
SELECT … FROM areas JOIN projects    -> Dự án từ UI | Tower UI | 3PN | active
GET  /api/v1/projects                -> 3 dự án
GET  /api/v1/areas?project_id=…      -> 1 phân khu của đúng dự án mới
```

### Test hồi quy

- `test_project_content_columns_default_to_empty_string`
- `test_area_content_columns_default_to_empty_string`
- `test_content_columns_are_saved_when_provided`

Các test tạo/sửa đã có từ trước vẫn giữ nguyên và xanh:
`test_create_project_returns_201_with_active_status`,
`test_created_project_is_actually_in_postgres`,
`test_project_name_is_trimmed_before_saving`, `test_blank_project_name_is_422`,
`test_invalid_launch_date_is_422`, `test_duplicate_project_names_are_allowed`,
`test_create_area_returns_201_with_parent_and_active_status`,
`test_created_area_is_actually_in_postgres`, `test_area_for_unknown_project_is_404`,
`test_area_under_inactive_project_is_409`, `test_invalid_area_field_is_422`,
`test_duplicate_area_is_409_not_a_database_traceback`,
`test_same_area_name_with_different_unit_type_is_allowed`,
`test_same_area_name_in_another_project_is_allowed`,
`test_failed_area_insert_leaves_nothing_behind`,
`test_update_area_keeps_its_project`.

### Trạng thái

**Đã sửa · Đã kiểm chứng** — commit `be4580c`.

Hạn chế còn lại:

- Chưa có xoá dự án / phân khu.
- Chưa có test tự động cho frontend (dự án chưa cài test runner JS); phần giao
  diện được kiểm bằng `npm run build` và gọi API thật qua proxy của FE.
- `ImportSelectPage` vẫn hiển thị `location`, `zone_count`, `sold_pct` bằng
  0 / rỗng vì backend chưa có endpoint tổng hợp.
