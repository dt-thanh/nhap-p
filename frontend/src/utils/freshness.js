// frontend/src/utils/freshness.js
// Phase 5.5 P0 (Bước 1B — F-2): suy ra TRẠNG THÁI hiển thị từ các mốc thời gian
// BACKEND trả về trong AbsorptionSummaryOut. KHÔNG có Date.now()/new Date() ở
// đây ngoài khối classify — và khối đó chỉ dùng "bây giờ" để so sánh KHOẢNG
// CÁCH, không bao giờ tự XƯNG là một mốc đồng bộ.
//
// Backend CHỦ ĐỘNG không tự phán "cũ" hay "mới" (xem AbsorptionSummaryOut trong
// src/models/schemas.py) — đó là quyết định nghiệp vụ, chưa có chủ sở hữu chốt
// ngưỡng. Hằng số dưới đây là NGƯỠNG TẠM, dễ đổi ở MỘT chỗ, và phải được thay
// bằng cấu hình thật khi có quyết định — xem DECISION REQUIRED trong
// docs/roadmap.md Phase 5.5.
export const STALE_AFTER_MS = 24 * 60 * 60 * 1000; // 24 giờ — NGƯỠNG TẠM, chưa phải quyết định nghiệp vụ

/**
 * @param {object} summary - AbsorptionSummaryOut (có thể null khi đang tải)
 * @param {Date} [now] - chỉ để test tiêm giờ giả; mặc định giờ thật
 * @returns {"loading"|"never_synced"|"sync_failed"|"calculation_outdated"|"stale"|"fresh"|"timestamp_unknown"}
 */
export function classifyFreshness(summary, now = new Date()) {
  if (!summary) return "loading";

  const { last_successful_sync, last_attempted_sync, last_sync_status, updated_at } = summary;

  if (!last_attempted_sync && !updated_at) return "timestamp_unknown";
  if (!last_attempted_sync) return "never_synced";

  const FAILURE_STATUSES = new Set(["failed", "partially_completed"]);
  if (last_sync_status && FAILURE_STATUSES.has(last_sync_status)) return "sync_failed";

  if (!last_successful_sync) return "never_synced";

  // Tính lại (`updated_at`) trễ hơn hẳn lần đồng bộ thành công gần nhất: dữ
  // liệu ĐÃ vào backend nhưng thẻ số liệu chưa phản ánh — khác "cũ vì lâu
  // không đồng bộ" (stale), đây là "đã đồng bộ nhưng chưa tính lại".
  if (updated_at) {
    const syncedAt = new Date(last_successful_sync).getTime();
    const calculatedAt = new Date(updated_at).getTime();
    if (syncedAt - calculatedAt > 5 * 60 * 1000) return "calculation_outdated";
  }

  const ageMs = now.getTime() - new Date(last_successful_sync).getTime();
  return ageMs > STALE_AFTER_MS ? "stale" : "fresh";
}

// Nhãn + tông màu cho từng trạng thái — MỘT bảng tra duy nhất, để mọi màn hình
// (Dashboard, và sau này Catalog/Inventory) hiển thị GIỐNG NHAU cho cùng một
// trạng thái, không lệch chữ giữa các nơi.
export const FRESHNESS_LABEL = {
  loading: { text: "Đang tải…", tone: "muted" },
  fresh: { text: "Dữ liệu mới", tone: "ok" },
  stale: { text: "Dữ liệu cũ", tone: "warn" },
  sync_failed: { text: "Đồng bộ gần nhất thất bại", tone: "danger" },
  never_synced: { text: "Chưa từng đồng bộ CRM", tone: "muted" },
  calculation_outdated: { text: "Đã đồng bộ, chưa tính lại", tone: "warn" },
  timestamp_unknown: { text: "Không rõ thời điểm", tone: "muted" },
};

/** Định dạng một mốc thời gian BACKEND — không bao giờ nhận Date() của trình
 *  duyệt làm tham số. `null`/`undefined` -> "Không rõ", đúng yêu cầu "null
 *  timestamps render as Unknown/Not available". */
export function formatBackendTimestamp(iso) {
  if (!iso) return "Không rõ";
  return new Date(iso).toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
