# Frontend — AbsorbIQ AI (MVP1)

React 18 + Vite 6 + React Router + Recharts. Giao diện xếp hạng khả năng bán
căn hộ + theo dõi tốc độ hấp thụ, cho đội ngũ kinh doanh & ban lãnh đạo.

**Trạng thái:** MVP1 hoàn thành, đang chạy trên **mock**, sẵn sàng nối backend thật.

---

## 1. Chạy

```bash
docker compose up -d --build      # cả stack
# http://localhost:5173           frontend
# http://localhost:8000/docs      Swagger (đối chiếu field ở đây)
```
Vite proxy sẵn `/api` và `/ws` → `api:8000`. Trong code chỉ dùng `/api/...`, không hardcode host.

**Cần thêm vào `frontend/index.html`** (nhận diện AbsorbIQ dùng 2 font):
```html
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap" rel="stylesheet">
```

---

## 2. Các màn & route

| Route | Màn | Ghi chú |
|---|---|---|
| `/` | S00 Trang chủ (chưa đăng nhập) | landing, không lộ dữ liệu |
| `/login` `/register` | S01 Đăng nhập | auth thật ở MVP3 |
| `/dashboard` | Dashboard hấp thụ | có bộ chọn dự án |
| `/projects` | S02 Danh sách dự án | lưới thẻ, lọc, tìm |
| `/projects/:id` | S03 Chi tiết dự án | **nhúng Dashboard** khoá theo dự án |
| `/projects/:id/areas/:areaId` | S04 Chi tiết phân khu | tab **Xếp hạng khả năng bán** (lõi) |
| `/import` → `/import/upload` | S05 Nạp dữ liệu | chọn dự án→phân khu rồi upload |

---

## 3. ⚠️ HỢP ĐỒNG API — cho Backend & AI

Frontend gọi các endpoint sau với **đúng tên field này**. Đổi tên → sửa 1 chỗ
trong `src/api/endpoints.js`.

### Đã dùng (MVP1)
```
GET  /api/projects
→ [{ id, name, location, zone_count, total_units, sold_pct, status }]
     status ∈ active | upcoming | archived

GET  /api/projects/{id}
→ { ...project, launch_date }

GET  /api/projects/{id}/zones
→ [{ id, name, total_units, units_remaining, status }]

GET  /api/dashboard/summary?project_id=&area_id=&from=&to=
→ { total_units, units_sold, remaining_units, absorption_rate, avg_velocity, updated_at }
   (dữ liệu CANONICAL — đã chuẩn hoá; avg_velocity có thể null → UI hiện N/A)

GET  /api/dashboard/trend?project_id=&area_id=&from=&to=
→ [{ date, units_sold, cumulative_sold, absorption_rate }]

GET  /api/dashboard/areas?project_id=
→ [{ id, name, total_units, sold, remaining, absorption_rate, velocity, latest_data, status }]
   velocity có thể null (phân khu mới) → UI hiện N/A, KHÔNG hiện 0.

GET  /api/dashboard/data-quality?project_id=
→ { latest_data, source, date_range:{from,to}, error_records, status, warnings:[] }
```

### 🎯 Bài toán lõi — cho AI
```
GET  /api/areas/{areaId}/ranking
→ [{ unit_code, unit_type, area_sqm, score, band }]
     score : 0–100 (khả năng bán)
     band  : high | medium | low
```
Frontend chỉ hiển thị `score` (thanh %) + `band` (nhãn màu). **AI thay handler
mock bằng model thật** — không cần đổi frontend nếu giữ đúng schema này.

### Nạp dữ liệu (MVP1)
```
POST /api/files/upload   (multipart, field "file")  → { file_id, status }  (409 nếu trùng)
GET  /api/files                                       → [{ id, filename, status, rows_ok, rows_failed, uploaded_at }]
GET  /api/files/{id}/status                           → { status, rows_ok, rows_failed }
GET  /api/files/{id}/errors                           → [{ row_number, column_name, error_code, message }]
```

### Đã nối API thật
```
POST /api/v1/chat   { message } → { response, analysis }   (ChatWidget, dùng forceReal)
```

### MVP2/MVP3 (đã khai báo sẵn trong endpoints.js, chờ backend)
`/api/forecasts*` · `/api/alerts` · `/api/auth/*` · `/api/proposals*` · `WS /ws/*`

---

## 4. Bật backend thật

Mock ở `src/api/mock.js`. Hai cách chuyển:

**Toàn bộ:**
```js
// src/api/client.js
export const USE_MOCK = false;
```
**Từng endpoint** (khuyến nghị khi backend xong dần):
```js
export const getProject = (id) => api.get(`/projects/${id}`, { forceReal: true });
```
Khi `USE_MOCK = true`, topbar hiện nhãn vàng **"dữ liệu giả"** (tự mất khi tắt mock).

---

## 5. Kiến trúc (giữ giúp khi đóng góp code)

```
src/
├── App.jsx                    router tổng
├── api/
│   ├── client.js              fetch chung · USE_MOCK · forceReal · ApiError · setAccessToken
│   ├── endpoints.js           ★ MỌI endpoint — sửa ở đây khi API đổi
│   └── mock.js                backend giả (schema khớp mục 3)
├── styles/tokens.js           ★ màu/chữ/khoảng cách — sửa ở đây khi đổi nhận diện
├── hooks/                     useAsync (loading/error) · useBreakpoint (responsive)
├── components/
│   ├── AppLayout.jsx          topbar + ChatWidget (bọc trang đã đăng nhập)
│   ├── Brand.jsx              logo dùng chung
│   ├── dashboard/             AbsorptionDashboard + 7 section (tái dùng ở /dashboard và S03)
│   └── ui/                    Icon · States (Skeleton/Empty/Error + fmt) · GlobalKeyframes
└── pages/                     Home · Login · Projects · ProjectDetail · AreaDetail · Import · Upload
```

**Quy ước:** component KHÔNG tự fetch (chỉ page/AbsorptionDashboard gọi API) ·
không hardcode màu (lấy từ tokens) · thiếu dữ liệu → `fmt()` hiện **N/A**, giữ số 0 thật ·
token giữ trong bộ nhớ, không localStorage (NFR-S11).

---

## 6. Cần thống nhất với backend

- `avg_velocity`, `velocity`, `sellout_date` có thể `null` không? (UI đã xử lý N/A)
- Định dạng ngày: ISO `YYYY-MM-DD`?
- Lỗi: `{detail}` hay `{message}`? (client.js đọc cả hai)
- Upload trùng: HTTP 409?
- Dashboard đọc **dữ liệu canonical** (đã chuẩn hoá), không phải file thô.

---

## 7. Giới hạn hiện tại (frontend)

- Số liệu từ mock; chờ backend cắm endpoint mục 3.
- Auth/RBAC là MVP3 — hiện mọi route vào được (chưa chặn quyền).
- Date range là preset (30d/90d/12m), chưa có date-picker tuỳ chọn.
- S04 hiện vào bằng URL; nối link từ bảng phân khu ở S03 là bước tiếp theo.
```
```