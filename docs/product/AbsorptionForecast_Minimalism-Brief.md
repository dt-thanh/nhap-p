# AbsorptionForecast AI Agent — Brief
---

## 1. Painpoint (4 lớp vấn đề)

| # | Vấn đề | Biểu hiện |
| --- | --- | --- |
| P1 | Dữ liệu phân tán | Excel rải rác theo từng sale, mỗi file một định dạng, lỗi/trùng chỉ lộ khi tổng hợp |
| P2 | Báo cáo chậm, thiếu nhất quán | Tổng hợp thủ công theo tuần, hai người ra hai số khác nhau |
| P3 | Không có tầm nhìn dự báo | Chỉ thấy quá khứ, không biết phân khu nào sắp hết hàng, phán đoán theo cảm tính |
| P4 | Thiếu phê duyệt & audit | Quyết định giá/chiết khấu qua miệng/chat, không truy được ai duyệt dựa trên số liệu gì |

→ Sản phẩm giải quyết **P1–P2** (chuẩn hoá dữ liệu) và **P3–P4** (dự báo + kiểm soát quyết định).
→ **Không** làm CRM/ERP, **không** tự động thực thi chính sách.

---

## 2. Workflow (3 bước chính, ứng với 3 MVP)

**Bước 1 — Nạp & kiểm tra dữ liệu (MVP1)**
- Manager upload Excel/CSV → validate từng dòng, báo lỗi → lưu DB
- Tính tốc độ hấp thụ → Sales Staff xem dashboard phân khu mình phụ trách

**Bước 2 — Dự báo & giải thích (MVP2)**
- Job chạy tự động 02:00 (hoặc Manager trigger thủ công)
- Prophet dự báo velocity + ngày hết hàng + CI 90%
- LangGraph + LLM sinh giải thích tiếng Việt + đề xuất hành động
- Cảnh báo cạn hàng + bảng xếp hạng rủi ro (chỉ hiển thị, chưa duyệt)

**Bước 3 — Duyệt & truy vết (MVP3)**
- Manager mở danh sách đề xuất "Chờ duyệt" → duyệt/từ chối (kèm lý do)
- Trạng thái đẩy real-time tới người đang mở dashboard
- Mọi hành động ghi vào audit log (append-only, không sửa/xoá)

**Nguyên tắc xuyên suốt:** HITL bắt buộc — đề xuất chỉ có hiệu lực sau khi Manager duyệt, Agent không tự thực thi.

---

## 3. Architecture

**Luồng dữ liệu tổng:**

```
File Excel/CSV → Import & Validate → PostgreSQL (nguồn sự thật duy nhất)
→ Prophet Forecast → LangGraph Agent (LLM giải thích + đề xuất)
→ FastAPI → React Dashboard → Manager duyệt → Audit Log
```

**4 layer (đắp chồng theo MVP):**

| Layer | MVP | Thành phần |
| --- | --- | --- |
| L1 – Ingest & Data | MVP1 | Import/Validation → Absorption Calculator → Postgres |
| L2 – Forecast & AI | MVP2 | Scheduler → Job Queue (RQ/Celery) → Prophet → LangGraph → LLM |
| L3 – Governance | MVP3 | Auth (JWT) → RBAC → duyệt/từ chối → Audit Service |
| L4 – Realtime | song song L2/L3 | Postgres `LISTEN/NOTIFY` → WebSocket Manager → đẩy vào React (fallback polling nếu mất kết nối) |

**Stack:** FastAPI (async) · Prophet · LangGraph + LLM · PostgreSQL (asyncpg) · React · WebSocket · Fly.io/Render, Docker Compose cho dev.

