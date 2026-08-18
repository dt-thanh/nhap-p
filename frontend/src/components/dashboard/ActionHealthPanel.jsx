// Deterministic operational signals. These are signals for follow-up, not
// automatic pricing, incentive, or phase decisions.
import React from "react";
import { color, size, radius, shadow, space, font } from "../../styles/tokens";
import { SectionState, Skeleton } from "../ui/States";
import { deriveVelocityDirection, VELOCITY_DIRECTION_LABEL } from "../../utils/velocity";
import { DASHBOARD_TEXT, formatDashboardNumber } from "./labels";

const INVENTORY_WATCH_RATIO = 0.5;
const INVENTORY_HIGH_RATIO = 0.75;

export function deriveInventoryStatus(summary) {
  const total = toFinite(summary?.total_units);
  const remaining = toFinite(summary?.remaining_units);
  if (total === null || total <= 0 || remaining === null || remaining < 0) return "insufficient";
  if (remaining <= 0) return "sold_out";
  const ratio = remaining / total;
  if (ratio >= INVENTORY_HIGH_RATIO) return "high_remaining";
  if (ratio >= INVENTORY_WATCH_RATIO) return "watch";
  return "stable";
}

const INVENTORY_LABEL = {
  stable: DASHBOARD_TEXT.stable,
  watch: DASHBOARD_TEXT.watch,
  high_remaining: DASHBOARD_TEXT.highRemaining,
  sold_out: DASHBOARD_TEXT.soldOut,
  insufficient: DASHBOARD_TEXT.insufficientData,
};

export default function ActionHealthPanel({ summary, loading, error, onRetry }) {
  const direction = deriveVelocityDirection(summary?.velocity_7d, summary?.velocity_30d);
  const inventory = deriveInventoryStatus(summary);
  const directionLabel = VELOCITY_DIRECTION_LABEL[direction];
  const directionText = direction === "unknown" ? DASHBOARD_TEXT.insufficientData : directionLabel.text;

  return (
    <section style={S.card} aria-label="Tín hiệu vận hành">
      <div style={S.head}>
        <div>
          <h2 style={S.title}>{DASHBOARD_TEXT.actionSignals}</h2>
          <p style={S.sub}>{DASHBOARD_TEXT.actionSubtitle}</p>
        </div>
      </div>
      <SectionState loading={loading} error={error} empty={!loading && !error && !summary} onRetry={onRetry} compact
        skeleton={<div style={S.skeleton}><Skeleton height={18} /><Skeleton height={18} /><Skeleton height={18} /></div>}
      >
        <div style={S.grid}>
          <Signal label={DASHBOARD_TEXT.velocity} value={directionText} tone={direction === "decreasing" ? "warn" : "normal"}
            hint="So sánh tốc độ bán 7 ngày với 30 ngày; thiếu một trong hai thì không kết luận." />
          <Signal label={DASHBOARD_TEXT.estimatedSellOut} value={formatWeeks(summary?.estimated_weeks_to_sell_out)}
            hint="Tồn kho còn lại chia cho tốc độ bán 30 ngày quy đổi theo tuần." />
          <Signal label={DASHBOARD_TEXT.inventoryStatus} value={INVENTORY_LABEL[inventory]} tone={inventory === "high_remaining" ? "warn" : "normal"}
            hint="Ngưỡng tham chiếu: tồn kho từ 50% là cần theo dõi, từ 75% là còn cao." />
        </div>
        <p style={S.guidance}>{guidance(direction, inventory)}</p>
      </SectionState>
    </section>
  );
}

function Signal({ label, value, hint, tone = "normal" }) {
  return (
    <div style={S.signal} title={hint}>
      <span style={S.label}>{label}</span>
      <strong style={{ ...S.value, color: tone === "warn" ? color.warn : color.ink }}>{value}</strong>
    </div>
  );
}

function guidance(direction, inventory) {
  if (direction === "unknown" || inventory === "insufficient") {
    return DASHBOARD_TEXT.noAutomaticDecision;
  }
  if (direction === "decreasing") {
    return DASHBOARD_TEXT.velocityDownSignal;
  }
  if (inventory === "high_remaining") {
    return DASHBOARD_TEXT.inventoryHighSignal;
  }
  return DASHBOARD_TEXT.noActionSignal;
}

function formatWeeks(value) {
  const number = toFinite(value);
  return number === null || number < 0 ? "N/A" : formatDashboardNumber(number, { suffix: " tuần", digits: 1 });
}

function toFinite(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

const S = {
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(5), boxShadow: shadow, marginBottom: space(5) },
  head: { marginBottom: space(4) },
  title: { fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0, fontFamily: font.sans },
  sub: { fontSize: size.tiny, color: color.muted, margin: "3px 0 0" },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: space(4) },
  signal: { display: "flex", flexDirection: "column", gap: space(1), padding: space(3), background: color.canvas, borderRadius: radius.sm },
  label: { fontSize: size.tiny, color: color.muted, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em" },
  value: { fontSize: size.small, fontWeight: 700 },
  guidance: { margin: `${space(4)}px 0 0`, color: color.body, fontSize: size.tiny, lineHeight: 1.6 },
  skeleton: { display: "grid", gap: space(2) },
};
