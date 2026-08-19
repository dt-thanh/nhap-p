// frontend/src/components/dashboard/KpiCards.jsx
// 5 thẻ KPI: Total Units · Units Sold · Remaining · Absorption Rate · Avg Velocity.
// KHÔNG hard-code số — lấy từ summary; thiếu -> N/A (không hiện 0 giả).
import React from "react";
import { color, size, radius, shadow, space, font } from "../../styles/tokens";
import Icon from "../ui/Icon";
import { fmt, Skeleton, ErrorState } from "../ui/States";
import { VELOCITY_DIRECTION_LABEL } from "../../utils/velocity";

const CARDS = [
  { key: "total_units",     label: "Total Units",     icon: "units",     tint: color.accent },
  { key: "units_sold",      label: "Units Sold",      icon: "sold",      tint: color.ok },
  { key: "remaining_units", label: "Remaining Units", icon: "remaining", tint: color.muted },
  { key: "absorption_rate", label: "Absorption Rate", icon: "rate",      tint: color.accent, suffix: "%", digits: 1 },
  { key: "avg_velocity",    label: "Avg Velocity",    icon: "velocity",  tint: color.warn, suffix: " căn/tuần", digits: 1 },
];

const DIRECTION_COLOR = { ok: color.ok, warn: color.warn, muted: color.muted };

/** `velocityDirection`: "increasing"|"decreasing"|"stable"|"unknown" (xem
 *  `utils/velocity.js`) — so vận tốc 7 ngày với 30 ngày của phân khu đang
 *  chọn. "unknown" (thiếu lịch sử) KHÔNG vẽ mũi tên nào, đúng yêu cầu không
 *  đưa ra tuyên bố hướng khi không có dữ liệu để tính. */
export default function KpiCards({ summary, velocityDirection, loading, error, onRetry }) {
  if (error) {
    return <div style={S.errorWrap}><ErrorState error={error} onRetry={onRetry} compact /></div>;
  }
  const direction = velocityDirection ? VELOCITY_DIRECTION_LABEL[velocityDirection] : null;
  return (
    <div style={S.grid}>
      {CARDS.map((c) => (
        <div key={c.key} style={S.card}>
          <div style={S.top}>
            <span style={{ ...S.iconBox, background: c.tint + "18" }}>
              <Icon name={c.icon} size={17} color={c.tint} />
            </span>
            <span style={S.label}>{c.label}</span>
          </div>
          <div style={S.valueRow}>
            <span style={S.value}>
              {loading ? <Skeleton height={28} width="70%" />
                : fmt(summary?.[c.key], { suffix: c.suffix || "", digits: c.digits })}
            </span>
            {c.key === "avg_velocity" && !loading && direction?.arrow && (
              <span
                style={{ ...S.direction, color: DIRECTION_COLOR[direction.tone] || color.muted }}
                title={`${direction.text} — so vận tốc 7 ngày với 30 ngày gần nhất`}
              >
                {direction.arrow} {direction.text}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

const S = {
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: space(3), marginBottom: space(5) },
  card: {
    background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md,
    padding: `${space(4)}px ${space(4)}px`, boxShadow: shadow, minHeight: 104,
  },
  top: { display: "flex", alignItems: "center", gap: space(2), marginBottom: space(3) },
  iconBox: { width: 32, height: 32, borderRadius: radius.sm, display: "grid", placeItems: "center", flex: "none" },
  label: { fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em" },
  valueRow: { display: "flex", alignItems: "baseline", gap: space(2), flexWrap: "wrap" },
  value: { fontSize: 28, fontWeight: 700, color: color.ink, fontFamily: font.sans, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 },
  direction: { fontSize: size.tiny, fontWeight: 700, whiteSpace: "nowrap" },
  errorWrap: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, marginBottom: space(5) },
};
