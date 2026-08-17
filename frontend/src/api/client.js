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
// Backend nghiệp vụ chưa xong nên tạm để true. Xong thì đổi thành false.
export const USE_MOCK = true;

const BASE = "/api";

// Access token giữ trong bộ nhớ (không localStorage) — SRS NFR-S11
let _accessToken = null;
export function setAccessToken(t) { _accessToken = t; }
export function getAccessToken() { return _accessToken; }

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
    const msg = (data && (data.detail || data.message)) || res.statusText;
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

export const api = {
  get: (p, o) => request(p, { ...o, method: "GET" }),
  post: (p, b, o) => request(p, { ...o, method: "POST", body: b }),
  put: (p, b, o) => request(p, { ...o, method: "PUT", body: b }),
};