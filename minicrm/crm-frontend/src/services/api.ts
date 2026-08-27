// ============================================================
// API CLIENT — single point of contact with MiniCRM backend.
// Handles auth headers, error normalisation, base URL.
// ============================================================

const BASE = "/api";

/**
 * CP4: KHÔNG còn hàm chọn token nào ở đây.
 *
 * Trước: token đọc từ `localStorage.crm_auth`, kèm fallback
 * `VITE_DEV_BEARER_TOKEN`. Cả hai đã bị GỠ — token phiên bây giờ nằm trong
 * cookie `HttpOnly` do Mini CRM phát sau khi Keycloak xác thực, và trình duyệt
 * tự đính kèm nhờ `credentials: "include"` bên dưới. JavaScript không đọc được
 * nó, nên cũng không có gì để một script chèn vào trang đánh cắp.
 *
 * Hệ quả cho dev: không còn "token dán tay". Chạy `docker compose up` với
 * MINICRM_OIDC_* đã cấu hình rồi đăng nhập thật, hoặc bật đường token tĩnh
 * bằng MINICRM_LEGACY_TOKEN_AUTH_ENABLED=true (opt-in TƯỜNG MINH, mặc định tắt,
 * KHÔNG dùng cho production).
 */

/** Điều hướng cả trang sang `/auth/login` của backend, nơi bắt đầu vòng OIDC.
 *  Không dùng `fetch`: bước kế tiếp là một 302 sang tên miền của Keycloak, và
 *  người dùng PHẢI nhìn thấy thanh địa chỉ đó để biết mình đang gõ mật khẩu ở
 *  đâu. Một luồng đăng nhập chạy ngầm trong XHR là đúng hình dạng của một trang
 *  lừa đảo. */
export function startLogin(returnTo?: string): void {
  const qs = returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : "";
  window.location.href = `${BASE}/auth/login${qs}`;
}

/**
 * Kết thúc phiên bằng một navigation toàn trang: backend phải xoá cookie
 * HttpOnly trước, rồi mới chuyển browser sang Keycloak RP-initiated logout.
 * Giữ đường dẫn dưới `/api` để Vite/reverse proxy luôn chuyển nó tới Mini CRM,
 * giống hệt đường `/auth/login` ở trên.
 */
export function startLogout(): void {
  window.location.replace(`${BASE}/auth/logout`);
}

export class ApiError extends Error {
  status: number;
  errorCode: string;
  detail?: unknown;

  constructor(status: number, errorCode: string, detail?: unknown) {
    super(`[${status}] ${errorCode}`);
    this.status = status;
    this.errorCode = errorCode;
    this.detail = detail;
  }
}

/** Chống "bão refresh": nhiều request 401 cùng lúc phải dùng CHUNG một lần gọi
 *  `/auth/refresh`, không mỗi cái một lần. */
let refreshInFlight: Promise<boolean> | null = null;

async function tryRefreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

export async function apiFetch<T = unknown>(
  path: string,
  opts: RequestInit = {},
  skipAuthRecovery = false,
): Promise<T> {
  const headers: Record<string, string> = {
    ...(opts.headers as Record<string, string>),
  };
  if (opts.body && typeof opts.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  // `credentials: "include"` là thứ khiến cookie phiên `HttpOnly` được gửi kèm.
  // Thiếu nó, MỌI request đều 401 dù đã đăng nhập thành công.
  const res = await fetch(`${BASE}${path}`, { ...opts, headers, credentials: "include" });

  if (!res.ok) {
    let body: Record<string, unknown> = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error */
    }
    const errorCode = (body.error_code as string) ?? `HTTP_${res.status}`;

    // 401 trên một request BẤT KỲ = phiên đã hết hạn giữa chừng. Thử gia hạn
    // im lặng đúng MỘT lần rồi phát lại request; vẫn hỏng thì mới đẩy người
    // dùng về Keycloak. Không thử lại vòng hai: hai lần 401 liên tiếp nghĩa là
    // refresh token cũng đã chết, và lặp thêm chỉ tạo vòng redirect vô hạn.
    if (res.status === 401 && !skipAuthRecovery && !path.startsWith("/auth/")) {
      const recovered = await tryRefreshSession();
      if (recovered) {
        return apiFetch<T>(path, opts, true);
      }
      startLogin(window.location.pathname);
    }

    throw new ApiError(res.status, errorCode, body);
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;

  return res.json();
}

export function apiGet<T>(path: string): Promise<T> {
  return apiFetch<T>(path);
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: "DELETE" });
}
