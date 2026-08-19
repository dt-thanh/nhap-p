# AbsorptionForecast — Brief rút gọn

> **Định vị sản phẩm.** AbsorptionForecast dựng một **tầng dữ liệu canonical đã được
> kiểm tra** từ các nguồn bán hàng / tồn kho đã được duyệt. **PostgreSQL là nguồn sự
> thật duy nhất cho dữ liệu đã chuẩn hoá.** Dashboard, phân tích, dự báo và AI agent
> đều đọc từ chính nguồn canonical đó.
>
> Sản phẩm **không phải** chỉ là một dashboard, và **không** coi file thô là hệ thống
> ghi nhận (system of record).

**Tài liệu đầy đủ:** [PRD.md](PRD.md) · [SRS.md](SRS.md) ·
[Brief đầy đủ](AbsorptionForecast_AI_Agent_Brief.md)

---

## 1. Bài toán

Khách hàng đã có dữ liệu bán hàng và tồn kho, và đã có quy trình báo cáo của riêng
mình. Cái họ **chưa** có là một tầng dữ liệu đã chuẩn hoá, đã kiểm tra, có truy vết
nguồn — thứ mà phân tích, dự báo và AI agent có thể dựa vào mà không phải xử lý lại
file mỗi lần.

| # | Vấn đề | Sản phẩm giải quyết bằng |
| --- | --- | --- |
| **P1** | Dữ liệu nạp vào không được kiểm tra theo dòng; lỗi định dạng, thiếu trường, trùng bản ghi chỉ lộ ra ở khâu dùng số | Ingestion + validation theo dòng, báo lỗi kèm số dòng / tên cột, chặn nạp trùng theo checksum |
| **P2** | Không có một biểu diễn chuẩn hoá dùng chung cho phân tích, dự báo và AI — mỗi tiêu dùng lại đọc lại file theo cách riêng | Bảng canonical trong PostgreSQL; mọi tiêu dùng đọc cùng một nguồn |
| **P3** | Không có tầm nhìn dự báo kèm mức độ tin cậy | Prophet dự báo ngày dự kiến hết hàng + CI 90% + giải thích tiếng Việt (MVP 2) |
| **P4** | Đề xuất chính sách không có phê duyệt và không truy ngược được về dữ liệu nguồn | Luồng duyệt của Manager + audit log append-only + chuỗi truy vết tới lô dữ liệu gốc (MVP 3) |

**Không thuộc phạm vi:** data warehouse, tích hợp CRM/ERP, multi-tenant, agent tự động
thực thi thay đổi giá / chiết khấu.

---

## 2. Kiến trúc

```text
Excel/CSV hoặc nguồn dữ liệu đã được duyệt
        ↓
Ingestion (đường ghi duy nhất)
        ↓
Validation & normalization
        ↓
Canonical PostgreSQL data          ← nguồn sự thật duy nhất
        ↓
Absorption analytics
        ↓
Forecasting & AI explanation
        ↓
Alerts & recommendations
        ↓
Human approval & audit trail
```

**4 layer (đắp chồng theo MVP):**

| Layer | MVP | Thành phần |
| --- | --- | --- |
| L1 — Ingestion & Canonical Data | MVP 1 | Upload → Validation/Normalization → PostgreSQL canonical → Absorption Calculator |
| L2 — Analytics & AI | MVP 2 | Scheduler → Job Queue (RQ) → Prophet → LangGraph + LLM → alerts / đề xuất |
| L3 — Governance | MVP 3 | Auth (JWT) → RBAC → duyệt / từ chối → Audit Service |
| L4 — Realtime | song song L2/L3 | PostgreSQL `LISTEN/NOTIFY` → WebSocket Manager → React (fallback polling) |

**Stack thật trong repo:** Python 3.11 · FastAPI · PostgreSQL 15 · SQLAlchemy Core +
asyncpg · Alembic · Redis + RQ · APScheduler · React (Vite) · Prophet (MVP 2) ·
LangGraph + LLM (MVP 2) · Docker Compose cho dev.

---

## 3. Quyền sở hữu dữ liệu

Đây là ràng buộc kiến trúc, không phải hướng dẫn tuỳ chọn:

1. **Ingestion là đường ghi duy nhất** cho dữ liệu bán hàng / tồn kho nạp từ ngoài.
   Không có đường nào khác được phép ghi vào `sales_records`, `inventory_snapshots`.
2. **Bảng canonical là nguồn đọc** cho dashboard, phân tích và dự báo — cùng một
   nguồn, không có bản sao thứ hai.
3. **File thô là artifact nguồn và bản ghi lineage**: giữ lại để kiểm toán và nạp
   lại, **không** phải hệ thống ghi nhận.
4. **AI agent không đọc file thô** sau khi ingest — chỉ đọc canonical.
5. **Dự báo và đề xuất là bản ghi dẫn xuất**, không bao giờ ghi đè dữ liệu bán hàng /
   tồn kho.
6. **Audit log chỉ ghi thêm** (append-only).
7. **Seed data chỉ dùng cho dev/test**, không bao giờ trình bày như dữ liệu khách hàng.

---

## 4. Ba MVP

**MVP 1 — Canonical Data Store, Data Ingestion, Validation & Absorption Dashboard**
- Nạp dữ liệu bán hàng / tồn kho đã được duyệt (Excel/CSV theo template).
- Kiểm tra từng dòng và báo lỗi kèm số dòng / tên cột.
- Chuẩn hoá và lưu vào bảng canonical trong PostgreSQL.
- Chặn nạp trùng (checksum theo dự án).
- Giữ lineage tới file nguồn và lô nạp.
- Tính tốc độ hấp thụ **từ dữ liệu canonical**.
- Dashboard và (về sau) dự báo đọc cùng nguồn canonical đó.
- Hiển thị độ tươi và trạng thái chất lượng dữ liệu.

**MVP 2 — Dự báo, giải thích & cảnh báo**
- Prophet: tốc độ bán, ngày dự kiến hết hàng, khoảng tin cậy 90%.
- LangGraph + LLM: giải thích tiếng Việt (yếu tố chính + giả định).
- Cảnh báo cạn hàng theo ngưỡng cấu hình; xếp hạng phân khu theo rủi ro.
- Đầu vào của dự báo và của agent **chỉ là bảng canonical**.

**MVP 3 — Phê duyệt, phân quyền & kiểm toán**
- Đăng nhập, RBAC 3 vai trò.
- Đề xuất mặc định *Chờ duyệt*; chỉ có hiệu lực sau khi Manager duyệt; từ chối bắt
  buộc nêu lý do.
- Audit log append-only.
- Truy vết: quyết định → dự báo → dữ liệu canonical → lô/file nguồn.

**Nguyên tắc xuyên suốt:** HITL bắt buộc — đề xuất chỉ có hiệu lực sau khi Manager
duyệt; agent không tự thực thi thay đổi giá / chính sách.

---

## 5. Trạng thái hiện tại (tóm tắt)

Đọc từ `pipeline_status.md` và mã nguồn, không đọc từ tài liệu:

| Hạng mục | Trạng thái |
| --- | --- |
| Canonical schema (21 bảng, Alembic 0001–0004) | Implemented |
| Ingestion → validation → canonical → absorption | Implemented |
| Dashboard đọc canonical (`/absorption`, `/absorption/summary`) | Implemented |
| Chặn nạp trùng, lineage file/lô | Implemented |
| Dự báo Prophet, giải thích LLM, cảnh báo | Planned (MVP 2 — job hiện là stub) |
| Xác thực, RBAC, duyệt, audit log | Planned (MVP 3 — chưa có tầng auth nào) |

Bảng truy vết đầy đủ: [PRD.md §18](PRD.md) và [SRS.md §9](SRS.md).
