// ============================================================
// API CLIENT — single point of contact with MiniCRM backend.
// Handles auth headers, error normalisation, base URL.
// ============================================================

const BASE = "/api";

/** Retrieve the stored JWT access token (if any). */
function getToken(): string | null {
  if (import.meta.env.DEV) return "mca_6NnhdFPLj4jZRRwcpB4JLz5XDxrBQuhD";
  try {
    const raw =
      localStorage.getItem("crm_auth") ||
      sessionStorage.getItem("crm_auth");
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed.token ?? parsed.access_token ?? null;
  } catch {
    return null;
  }
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
