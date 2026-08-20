// frontend/src/styles/tokens.js
// ===========================================================================
// HỆ THỐNG THIẾT KẾ AbsorbIQ AI — nguồn sự thật duy nhất về giao diện.
// Mọi component lấy màu/chữ/khoảng cách từ đây; KHÔNG hardcode mã màu ở nơi khác.
// Đổi nhận diện toàn app = sửa file này.
//
// Nhận diện: graphite/charcoal chủ đạo · champagne gold cho điểm nhấn
// · emerald cho tín hiệu tích cực · amber cảnh báo · rose rủi ro.
// Chữ: Space Grotesk (tiêu đề, số) + Inter (nội dung) — nạp trong index.html.
// ===========================================================================

export const color = {
  canvas:   "#F7F7F4",
  surface:  "#FFFFFF",
  border:   "#E1E1DE",
  borderStrong: "#C8C8C2",

  ink:      "#161616",
  body:     "#4A4A4A",
  muted:    "#707070",

  accent:     "#C7A73A",
  accentHover: "#B49328",
  accentSoft: "#F4EBC7",

  danger:   "#B8474F",
  dangerSoft: "#F9E7E8",
  warn:     "#B7791F",
  warnSoft: "#FBF1D8",
  ok:       "#248A62",
  okSoft:   "#E4F3EC",

  sidebar:            "#111111",
  sidebarSurface:    "#2A2A2A",
  sidebarText:       "#F7F7F7",
  sidebarMuted:      "#B8B8B8",
  sidebarActiveSoft: "rgba(199,167,58,.16)",
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
