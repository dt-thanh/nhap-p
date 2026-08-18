// frontend/src/api/client.js
// ---------------------------------------------------------------------------
// TẦNG GỌI API DÙNG CHUNG. Mọi component/page đều đi qua đây, không tự fetch.
//
// Nhiệm vụ của file này:
//   1. Gắn base path "/api" (Vite đã proxy sang api:8000)
//   2. Gắn token khi có (MVP3)
//   3. Ném lỗi có cấu trúc (ApiError) để nơi gọi biết status mà xử lý
//   4. Công tắc USE_MOCK — chuyển giữa backend GIẢ và backend THẬT
// ---------------------------------------------------------------------------
import { mockFetch } from "./mock";

// 🔴 CÔNG TẮC: true = dùng backend giả · false = gọi backend thật
// Backend MVP 1 đã xong (files + areas + absorption) nên chạy thật.
// Đặt lại true khi cần phát triển giao diện mà không dựng Docker.
export const USE_MOCK = false;

const BASE = "/api";
// Phase F/G: cổng GHI của FE — proxy cùng-nguồn sang Mini CRM (xem vite.config.js).
// KHÔNG BAO GIỜ dùng BASE ("/api", backend) cho một lời ghi Project/Area/Unit/Deal.
const MINICRM_BASE = "/minicrm-api";

// Access token giữ trong bộ nhớ (không localStorage) — SRS NFR-S11
let _accessToken = null;
export function setAccessToken(t) { _accessToken = t; }
export function getAccessToken() { return _accessToken; }

// Token GHI của Mini CRM (D-14) — TÁCH RIÊNG khỏi token đọc của backend: hai hệ
// thống, hai từ vựng vai trò khác nhau (dù cùng tên business_viewer/
// pipeline_operator/admin, đây là hai bộ token vật lý khác nhau — xem
// `docs/crm/phase_a_domain_freeze.md` §A7.1, "hai mặt phẳng phân quyền").
let _minicrmToken = null;
export function setMiniCrmToken(t) { _minicrmToken = t; }
export function getMiniCrmToken() { return _minicrmToken; }

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(path, { method = "GET", body, headers = {}, forceReal = false } = {}) {
  // --- Nhánh MOCK: không đụng mạng, trả dữ liệu giả ---
  // forceReal = true -> luôn gọi backend THẬT, kể cả khi đang bật mock.
  // Dùng cho các endpoint backend ĐÃ có sẵn (vd /api/v1/chat của AI agent).
  if (USE_MOCK && !forceReal) {
    try {
      return await mockFetch(path, { method, body });
    } catch (e) {
      throw new ApiError(e.status || 500, e.message, null);
    }
  }

  // --- Nhánh THẬT ---
  const opts = { method, headers: { ...headers }, credentials: "include" };
  if (_accessToken) opts.headers["Authorization"] = `Bearer ${_accessToken}`;

  if (body instanceof FormData) {
    opts.body = body; // để trình duyệt tự set Content-Type + boundary
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 204) return null;

  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }

  if (!res.ok) {
    // Phase 5.5 P0 — Bước 2/3: một số endpoint vận hành (GET /sync-runs, ...)
    // giờ đòi token vai trò (`Authorization: Bearer`, xem setAccessToken()).
    // 401/403 nhận thông báo THÂN THIỆN, ổn định — KHÔNG suy diễn thêm gì về
    // việc "sửa lỗi thế nào": ẩn nút hay retry là việc của nơi gọi, tầng này
    // chỉ đảm bảo lỗi luôn có nghĩa, kể cả khi backend không trả `detail`.
    const backendMessage = (data && (data.detail?.message || data.detail)) || data?.message;
    const msg =
      res.status === 401
        ? backendMessage || "Thiếu hoặc sai thông tin xác thực."
        : res.status === 403
          ? backendMessage || "Bạn không có quyền thực hiện thao tác này."
          : backendMessage || res.statusText;
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

/** true khi lỗi là xác thực/quyền — nơi gọi dùng để tránh coi 401/403 như một
 *  lỗi dữ liệu thông thường (vd không nên hiển thị "Thử lại" cho lỗi quyền). */
export function isAuthError(err) {
  return err instanceof ApiError && (err.status === 401 || err.status === 403);
}

export const api = {
  get: (p, o) => request(p, { ...o, method: "GET" }),
  post: (p, b, o) => request(p, { ...o, method: "POST", body: b }),
  // PATCH cho luồng sửa dự án / phân khu: gửi kèm trường muốn đổi.
  patch: (p, b, o) => request(p, { ...o, method: "PATCH", body: b }),
  // `del` chứ không phải `delete`: delete là từ khoá của JavaScript.
  del: (p, o) => request(p, { ...o, method: "DELETE" }),
  put: (p, b, o) => request(p, { ...o, method: "PUT", body: b }),
};

// --- Cổng GHI Mini CRM (Phase F/G) ------------------------------------------
// KHÔNG dùng chung `request()` ở trên — hai lý do tách hẳn một hàm riêng:
//   1. Base path khác (MINICRM_BASE, không phải BASE) và không có nhánh MOCK.
//   2. Token khác (`_minicrmToken`, không phải `_accessToken`) — gộp chung sẽ
//      khiến một request GHI vô tình mang token ĐỌC của backend (hoặc ngược
//      lại), và lỗi đó chỉ lộ ra ở 401 khó hiểu tại nơi gọi.
async function minicrmRequest(path, { method = "GET", body, headers = {} } = {}) {
  const opts = { method, headers: { ...headers } };
  if (_minicrmToken) opts.headers["Authorization"] = `Bearer ${_minicrmToken}`;

  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(`${MINICRM_BASE}${path}`, opts);
  if (res.status === 204) return null;

  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }

  if (!res.ok) {
    const backendMessage = (data && (data.detail?.message || data.detail)) || data?.message;
    const msg =
      res.status === 401
        ? backendMessage || "Thiếu hoặc sai thông tin xác thực Mini CRM."
        : res.status === 403
          ? backendMessage || "Bạn không có quyền ghi vào dự án này."
          : backendMessage || res.statusText;
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

export const minicrm = {
  get: (p, o) => minicrmRequest(p, { ...o, method: "GET" }),
  post: (p, b, o) => minicrmRequest(p, { ...o, method: "POST", body: b }),
  patch: (p, b, o) => minicrmRequest(p, { ...o, method: "PATCH", body: b }),
  del: (p, o) => minicrmRequest(p, { ...o, method: "DELETE" }),
};