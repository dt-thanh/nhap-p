// frontend/src/api/auth.js
// ---------------------------------------------------------------------------
// CP5/CP7 — Tích hợp xác thực SSO qua Keycloak (OIDC) cho AbsorbIQ.
//
// ĐIỂM BÁM TỐI THIỂU, CÔ LẬP. File này KHÔNG đụng vào UI mà teammate đang sửa:
// nó chỉ thêm bốn hàm gọi mặt `/auth/*` của Product backend (src/api/auth.py).
// Component nào cần biết trạng thái đăng nhập thì gọi `fetchMe()`; nút đăng
// nhập thì gọi `startLogin()`. Không có JSX, không có route ở đây.
//
// Vì sao KHÔNG lưu token: phiên nằm trong cookie HttpOnly `absorbiq_session` do
// backend phát sau vòng OIDC. `client.js` đã gửi `credentials: "include"` nên
// cookie tự đính kèm. JavaScript không giữ token nào — cùng mô hình với Mini
// CRM, và là lý do `setAccessToken()` cũ không còn cần cho luồng người dùng.
// ---------------------------------------------------------------------------

const API_URL = import.meta.env.VITE_API_URL || "";
const AUTH_BASE = API_URL 
  ? `${API_URL.replace(/\/$/, "")}/api/v1/auth` 
  : "/api/v1/auth";

/** Điều hướng CẢ TRANG sang backend để bắt đầu vòng OIDC. Không dùng fetch:
 *  bước kế tiếp là 302 sang Keycloak, và người dùng phải thấy thanh địa chỉ
 *  đó — một luồng đăng nhập chạy ngầm trong XHR là hình dạng của trang lừa đảo. */
export function startLogin(returnTo) {
  const rt = returnTo ?? window.location.pathname;
  window.location.href = `${AUTH_BASE}/login?return_to=${encodeURIComponent(rt)}`;
}


/** Danh tính hiện tại, hoặc null nếu chưa đăng nhập. Vì token ở cookie HttpOnly,
 *  JS không tự đọc được — phải hỏi backend. 401 = chưa đăng nhập (bình thường
 *  lúc mở app), không phải lỗi cần ném. */
export async function fetchMe() {
  const res = await fetch(`${AUTH_BASE}/me`, { credentials: "include" });
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) throw new Error(`/auth/me trả ${res.status}`);
  return res.json();
}

export async function refreshSession() {
  const res = await fetch(`${AUTH_BASE}/refresh`, {
    method: "POST",
    credentials: "include",
  });
  return res.ok;
}

/** Điều hướng tới backend để backend thu hồi phiên rồi chuyển tiếp tới
 *  Keycloak — fetch không phù hợp vì logout là một front-channel redirect. */
export async function logout() {
  window.location.href = `${AUTH_BASE}/logout`;
}
