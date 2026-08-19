// ============================================================
// API CLIENT — single point of contact with MiniCRM backend.
// Handles auth headers, error normalisation, base URL.
// ============================================================

const BASE = "/api";

/**
 * Chọn token cho request:
 *   1. Token JWT sau khi user đăng nhập (localStorage/sessionStorage).
 *   2. Fallback DEV-only từ Vite env: VITE_DEV_BEARER_TOKEN.
 *      - CHỈ đọc khi import.meta.env.DEV === true (vite build production sẽ
 *        loại nhánh này bằng dead-code elimination).
 *      - Giá trị lấy từ file `.env.local` của thư mục crm-frontend, KHÔNG
 *        commit vào git. Xem `.env.example` để biết cách khai báo.
 *   Nếu cả hai đều rỗng: request đi ra không có Authorization → backend trả
 *   401/403 và UI hiện lỗi rõ ràng, thay vì âm thầm dùng token mặc định.
 */
function getToken(): string | null {
  try {
    const raw =
      localStorage.getItem("crm_auth") ||
      sessionStorage.getItem("crm_auth");
    if (raw) {
      const parsed = JSON.parse(raw);
      const stored = parsed.token ?? parsed.access_token ?? null;
      if (stored) return stored;
    }
  } catch {
    /* ignore corrupt storage */
  }

  if (import.meta.env.DEV) {
    const devToken = import.meta.env.VITE_DEV_BEARER_TOKEN;
    if (typeof devToken === "string" && devToken.length > 0) {
      return devToken;
    }
  }

  return null;
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

export async function apiFetch<T = unknown>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(opts.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (opts.body && typeof opts.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${BASE}${path}`, { ...opts, headers });

  if (!res.ok) {
    let body: Record<string, unknown> = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(
      res.status,
      (body.error_code as string) ?? `HTTP_${res.status}`,
      body,
    );
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
