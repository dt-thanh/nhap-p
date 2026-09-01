// frontend/src/components/ui/Icon.jsx
// Bộ icon SVG nội tuyến — KHÔNG cài thư viện icon (tuân thủ "no unnecessary libraries").
// Dùng stroke mảnh, phong cách analytics/fintech.
import React from "react";

const PATHS = {
  bot:       "M12 2v3M8 9h.01M16 9h.01M7 13h10M6 6h12a2 2 0 012 2v9a2 2 0 01-2 2H6a2 2 0 01-2-2V8a2 2 0 012-2z",
  folder:    "M3 6h6l2 2h10v10a2 2 0 01-2 2H5a2 2 0 01-2-2V6z",
  catalog:   "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  upload:    "M12 16V4m0 0L8 8m4-4l4 4M5 14v5h14v-5",
  units:     "M3 7l9-4 9 4-9 4-9-4zm0 5l9 4 9-4M3 17l9 4 9-4",
  sold:      "M20 6L9 17l-5-5",
  remaining: "M12 8v4l3 3M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  rate:      "M3 17l6-6 4 4 8-8M21 7v4M21 7h-4",
  velocity:  "M13 2L3 14h7l-1 8 10-12h-7l1-8z",
  refresh:   "M21 12a9 9 0 11-3-6.7M21 3v6h-6",
  calendar:  "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z",
  database:  "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6",
  warning:   "M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  filter:    "M22 3H2l8 9.5V19l4 2v-8.5L22 3z",
  chevron:   "M9 18l6-6-6-6",
  inbox:     "M22 12h-6l-2 3h-4l-2-3H2M5.5 5h13l3.5 7v6a2 2 0 01-2 2H4a2 2 0 01-2-2v-6l3.5-7z",
  search:    "M11 4a7 7 0 105.1 11.8L21 20.7M16.1 15.8L21 20.7",
  bell:      "M18 8a6 6 0 00-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4",
  overview:  "M4 19V5M4 19h16M7 15l3-4 3 2 5-6",
};

export default function Icon({ name, size = 18, color = "currentColor", strokeWidth = 1.8, style }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
      style={style} aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
