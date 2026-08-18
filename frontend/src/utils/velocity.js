// frontend/src/utils/velocity.js
// So sánh vận tốc bán 7 ngày với 30 ngày (điểm gần nhất trên chuỗi hấp thụ của
// MỘT phân khu, `AbsorptionPointOut.velocity_7d`/`velocity_30d` — backend đã
// tính sẵn) để biết xu hướng bán đang TĂNG hay GIẢM. Không suy đoán gì khác
// ngoài hai con số này, và không đưa ra tuyên bố về hướng khi một trong hai
// con số không có — thiếu dữ liệu phải hiện "chưa rõ", không phải một mũi tên
// bịa ra để lấp chỗ trống.
//
// Cùng khuôn với utils/freshness.js: một hàm phân loại thuần (không gọi
// mạng, không đọc Date()) + một bảng nhãn/tông màu tra cứu MỘT LẦN cho mọi nơi
// hiển thị, để nhãn không lệch nhau giữa các màn hình.

/**
 * @param {number|string|null|undefined} velocity7d
 * @param {number|string|null|undefined} velocity30d
 * @returns {"increasing"|"decreasing"|"stable"|"unknown"}
 */
export function deriveVelocityDirection(velocity7d, velocity30d) {
  const v7 = toFiniteNumber(velocity7d);
  const v30 = toFiniteNumber(velocity30d);
  if (v7 === null || v30 === null || v7 < 0 || v30 < 0) return "unknown";
  if (v7 > v30) return "increasing";
  if (v7 < v30) return "decreasing";
  return "stable";
}

function toFiniteNumber(value) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export const VELOCITY_DIRECTION_LABEL = {
  increasing: { text: "Đang tăng", arrow: "↑", tone: "ok" },
  decreasing: { text: "Đang giảm", arrow: "↓", tone: "warn" },
  stable: { text: "Ổn định", arrow: "→", tone: "muted" },
  // Không có mũi tên/chữ để hiện — nơi gọi phải tự bỏ qua render khi gặp trạng
  // thái này, không phải hiện một hàng trống.
  unknown: { text: null, arrow: null, tone: "muted" },
};
