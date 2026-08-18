// frontend/src/components/dashboard/KpiCards.jsx
// 8 thẻ KPI: bán, tổng còn lại, khả bán, giữ chỗ, tỷ lệ, vận tốc và dự báo.
// KHÔNG hard-code số — lấy từ summary; thiếu -> N/A (không hiện 0 giả).
import React from "react";
import { color, size, radius, shadow, space, font } from "../../styles/tokens";
import Icon from "../ui/Icon";
import { Skeleton, ErrorState } from "../ui/States";
import { VELOCITY_DIRECTION_LABEL } from "../../utils/velocity";
import { DASHBOARD_TEXT, formatDashboardNumber, formatDashboardUnits } from "./labels";

const CARDS = [
  { key: "units_sold", label: DASHBOARD_TEXT.unitsSold, definition: "Tổng số căn đã bán trong phạm vi chọn", icon: "sold", tint: color.ok, formatter: (value) => formatDashboardUnits(value) },
  { key: "remaining_units", label: DASHBOARD_TEXT.remainingUnits, definition: DASHBOARD_TEXT.remainingUnitsDefinition, icon: "remaining", tint: color.muted, missingLabel: DASHBOARD_TEXT.insufficientData, formatter: (value) => formatDashboardUnits(value) },
  { key: "available_remaining_units", label: DASHBOARD_TEXT.availableRemainingUnits, definition: DASHBOARD_TEXT.availableRemainingUnitsDefinition, icon: "remaining", tint: color.ok, missingLabel: DASHBOARD_TEXT.insufficientData, formatter: (value) => formatDashboardUnits(value) },
  { key: "reserved_units", label: DASHBOARD_TEXT.reservedUnits, definition: DASHBOARD_TEXT.reservedUnitsDefinition, icon: "remaining", tint: color.warn, missingLabel: DASHBOARD_TEXT.insufficientData, formatter: (value) => formatDashboardUnits(value) },
  { key: "sell_through", label: DASHBOARD_TEXT.sellThrough, definition: "Tỷ lệ quỹ căn đã bán", icon: "rate", tint: color.accent, formatter: (value) => formatDashboardNumber(value, { digits: 1, suffix: "%" }) },
  { key: "velocity_7d", label: DASHBOARD_TEXT.velocity7d, definition: "Số căn/tuần quy đổi từ trung bình trượt 7 ngày", icon: "velocity", tint: color.warn, formatter: (value) => formatDashboardUnits(value, { digits: 1, perWeek: true }) },
  { key: "velocity_30d", label: DASHBOARD_TEXT.velocity30d, definition: "Số căn/tuần quy đổi từ trung bình trượt 30 ngày", icon: "velocity", tint: color.warn, formatter: (value) => formatDashboardUnits(value, { digits: 1, perWeek: true }) },
  { key: "estimated_weeks_to_sell_out", label: DASHBOARD_TEXT.estimatedWeeks, definition: "Số tuần ước tính để bán hết tồn kho ở vận tốc 30 ngày", icon: "calendar", tint: color.accent, formatter: (value) => formatDashboardNumber(value, { digits: 1, suffix: " tuần" }) },
];

const DIRECTION_COLOR = { ok: color.ok, warn: color.warn, muted: color.muted };

/** `velocityDirection`: "increasing"|"decreasing"|"stable"|"unknown" (xem
 *  `utils/velocity.js`) — so vận tốc 7 ngày với 30 ngày của phân khu đang
 *  chọn. "unknown" (thiếu lịch sử) KHÔNG vẽ mũi tên nào, đúng yêu cầu không
 *  đưa ra tuyên bố hướng khi không có dữ liệu để tính. */
export default function KpiCards({ summary, velocityDirection, loading, error, onRetry, context }) {
  if (error) {
    return <div style={S.errorWrap}><ErrorState error={error} onRetry={onRetry} compact /></div>;
  }
  const direction = velocityDirection ? VELOCITY_DIRECTION_LABEL[velocityDirection] : null;
  return (
    <section style={S.wrap} aria-label="Các chỉ số quyết định">
      {context && <p style={S.context}>{context}</p>}
      <div style={S.grid}>
        {CARDS.map((c) => (
          <div key={c.key} style={S.card}>
            <div style={S.top}>
              <span style={{ ...S.iconBox, background: c.tint + "18" }}>
                <Icon name={c.icon} size={17} color={c.tint} />
              </span>
              <span style={S.label} title={c.definition}>{c.label}</span>
            </div>
            <div style={S.valueRow}>
              <span style={S.value}>
                {loading ? <Skeleton height={28} width="70%" />
                  : formatMetric(summary?.[c.key], c.formatter, c.missingLabel)}
              </span>
              {c.key === "velocity_30d" && !loading && direction?.arrow && (
                <span
                  style={{ ...S.direction, color: DIRECTION_COLOR[direction.tone] || color.muted }}
                  title={`${direction.text} — so sánh vận tốc 7 ngày với 30 ngày`}
                >
                  {direction.arrow} {direction.text}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      <p style={S.inventoryNote}>{DASHBOARD_TEXT.inventoryBreakdown}</p>
    </section>
  );
}

function formatMetric(value, formatter, missingLabel = "N/A") {
  const number = Number(value);
  return value === null || value === undefined || !Number.isFinite(number) || number < 0
    ? missingLabel
    : formatter(number);
}

const S = {
  wrap: { marginBottom: space(5) },
  context: { margin: `0 0 ${space(2)}px`, color: color.muted, fontSize: size.tiny },
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
  inventoryNote: { margin: `${space(2)}px 0 0`, color: color.muted, fontSize: size.tiny },
};
