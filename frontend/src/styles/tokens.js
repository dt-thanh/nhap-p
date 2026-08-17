// frontend/src/styles/tokens.js
// ===========================================================================
// HỆ THỐNG THIẾT KẾ AbsorbIQ AI — nguồn sự thật duy nhất về giao diện.
// Mọi component lấy màu/chữ/khoảng cách từ đây; KHÔNG hardcode mã màu ở nơi khác.
// Đổi nhận diện toàn app = sửa file này.
//
// Nhận diện: indigo (trí tuệ/AI) chủ đạo · emerald cho tín hiệu tích cực
// (đã bán / khả năng bán cao) · amber cảnh báo · rose rủi ro.
// Chữ: Space Grotesk (tiêu đề, số) + Inter (nội dung) — nạp trong index.html.
// ===========================================================================

export const color = {
  canvas:   "#f6f6fb",
  surface:  "#ffffff",
  border:   "#e8e8f0",
  borderStrong: "#d8d8e6",

  ink:      "#1a1a2e",
  body:     "#4a4a63",
  muted:    "#8888a0",

  accent:     "#5b52e6",
  accentSoft: "#efeefe",

  danger:   "#d24d63",
  dangerSoft: "#fbe9ec",
  warn:     "#c8880f",
  warnSoft: "#fbf1dd",
  ok:       "#0fae7a",
  okSoft:   "#e3f7ef",
};

export const font = {
  display: '"Space Grotesk", "Inter", system-ui, sans-serif',
  sans:    '"Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  mono:    '"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
};

export const size = {
  display: 36,
  h1: 26,
  h2: 18,
  body: 15,
  small: 14,
  tiny: 12.5,
};

export const radius = { sm: 8, md: 12, lg: 16, pill: 999 };

export const space = (n) => n * 4;

export const shadow = "0 1px 3px rgba(26,24,46,.05), 0 1px 10px rgba(26,24,46,.03)";

export const layout = {
  maxWidth: 1400,
  gutter:      { mobile: 16, tablet: 24, laptop: 28, desktop: 32 },
  chartHeight: { mobile: 220, tablet: 280, laptop: 320, desktop: 380 },
};