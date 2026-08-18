# Báo cáo cấu trúc cơ sở dữ liệu hiện tại

- Ngày kiểm tra: 2026-08-15
- Nguồn dữ liệu: schema thực tế đang chạy trong PostgreSQL container của project
- Phương pháp: truy vấn trực tiếp `information_schema.tables` và `information_schema.columns`

---

## 1. Database chính: absorption

- Tổng số bảng: 40

### Nhóm bảng nghiệp vụ chính

1. `projects`
2. `areas`
3. `units`
4. `deals`
5. `sales_records`
6. `inventory_snapshots`
7. `absorption_daily`
8. `crm_source_records`
9. `upload_files`
10. `upload_errors`
11. `sync_credentials`
12. `sync_payloads`

### Danh sách đầy đủ các bảng

- absorption_daily
- agent_recommendations
- alembic_version
- alerts
- approvals
- areas
- audit_logs
- calculator_comparisons
- calculator_comparisons_gate
- crm_source_records
- deals
- explanations
- feature_snapshots
- forecast_jobs
- forecast_points
- forecasts
- inventory_snapshots
- llm_calls
- projects
- proposals
- ranking_configs
- ranking_runs
- ranking_scores
- reconciliation_findings
- reconciliation_runs
- refresh_tokens
- sales_records
- settings
- suggestions
- sync_credentials
- sync_payloads
- units
- upload_errors
- upload_files
- user_areas
- users

### Ý nghĩa sơ đồ

- `projects`, `areas`: dữ liệu dự án và phân khu
- `units`, `deals`: dữ liệu căn hộ và giao dịch
- `sales_records`, `inventory_snapshots`: dữ liệu bán hàng và tồn kho theo thời gian
- `absorption_daily`: số liệu hấp thụ theo ngày
- `ranking_*`: cấu hình, chạy và kết quả xếp hạng
- `agent_recommendations`: đề xuất AI chờ duyệt
- `forecast_*`: dữ liệu dự báo
- `upload_*`, `sync_*`, `crm_source_records`: luồng ingest, sync, và kiểm soát dữ liệu nguồn
- `audit_logs`, `alerts`, `approvals`, `settings`: tính năng vận hành và kiểm soát hệ thống

---

## 2. Database Mini CRM: minicrm

- Tổng số bảng: 6

### Danh sách bảng

- alembic_version
- crm_areas
- crm_deals
- crm_outbox
- crm_projects
- crm_units

### Ý nghĩa sơ đồ

- `crm_projects`: dự án trong hệ thống Mini CRM
- `crm_areas`: phân khu trong Mini CRM
- `crm_units`: căn hộ trong Mini CRM
- `crm_deals`: giao dịch trong Mini CRM
- `crm_outbox`: lịch sử đẩy dữ liệu ra ngoài / sync log

---

## 3. Kết luận

Hiện tại project đang chạy 2 database riêng biệt:

1. Database chính cho ứng dụng AI / absorption: `absorption`
2. Database riêng cho Mini CRM: `minicrm`

Hai hệ thống này tách riêng, không trộn schema với nhau. Dữ liệu nghiệp vụ chính của product nằm trong database `absorption`, còn database `minicrm` phục vụ dữ liệu nguồn / CRM mẫu.

---

## 4. Lưu ý kỹ thuật

- DB chính đang chạy trên service `db` với port 5432
- Mini CRM DB đang chạy trên service `minicrm_db` với port 5433
- File này được tạo dựa trên kết quả kiểm tra schema thật, không phải suy đoán từ code

Nếu cần, tôi có thể tiếp tục tạo thêm:
- file báo cáo theo từng nhóm chức năng,
- file Excel CSV export mẫu từ từng bảng,
- hoặc file SQL để xem 10 bản ghi đầu tiên của mỗi bảng.
