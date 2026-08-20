// ============================================================================
// CP6 — Deep-link từ Mini CRM sang Product Frontend (AbsorbIQ).
//
// KHOÁ LIÊN KẾT LÀ `external_id`, không phải tên dự án và không phải khoá chính
// nội bộ của một trong hai DB. Lý do: tên đổi được (và sẽ đổi), còn khoá chính
// của Mini CRM không có ý nghĩa gì ở Product DB — hai hệ có hai không gian ID
// riêng. `external_id` là thứ DUY NHẤT đi qua đường đồng bộ và tồn tại y hệt ở
// cả hai phía, nên nó là canonical ID (xem `docs/integration/canonical_ids.md`).
//
// KHÔNG kèm token vào URL. SSO đã lo phần danh tính: Product Frontend tự chạy
// vòng OIDC của nó với CÙNG tenant Entra, và vì phiên SSO ở phía Microsoft đã
// tồn tại, người dùng không bị hỏi lại mật khẩu. Một access token trong query
// string sẽ nằm lại trong lịch sử trình duyệt, log của proxy và header
// `Referer` — ba nơi không ai kiểm soát được.
// ============================================================================

/** Rỗng nếu chưa cấu hình — UI phải ẩn nút thay vì dựng một link hỏng. */
export function absorbiqBaseUrl(): string {
  return (import.meta.env.VITE_PRODUCT_FRONTEND_URL ?? "").replace(/\/$/, "");
}

export function isAbsorbiqConfigured(): boolean {
  return absorbiqBaseUrl().length > 0;
}

export function absorbiqProjectUrl(externalProjectId: string): string | null {
  const base = absorbiqBaseUrl();
  if (!base || !externalProjectId) return null;
  // Khớp route CÓ SẴN của Product Frontend (`/projects/:externalId` trong
  // frontend/src/App.jsx) — không tự áp đặt một quy ước URL mới.
  return `${base}/projects/${encodeURIComponent(externalProjectId)}`;
}

export function absorbiqProjectDashboardUrl(externalProjectId: string): string | null {
  const url = absorbiqProjectUrl(externalProjectId);
  return url ? `${url}/dashboard` : null;
}

export function absorbiqAreaUrl(externalProjectId: string, externalAreaId: string): string | null {
  const base = absorbiqBaseUrl();
  if (!base || !externalProjectId || !externalAreaId) return null;
  return `${base}/projects/${encodeURIComponent(externalProjectId)}/areas/${encodeURIComponent(externalAreaId)}`;
}
