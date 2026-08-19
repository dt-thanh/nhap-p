// frontend/src/components/dashboard/AbsorptionTrendChart.jsx
// Absorption Trend — 3 series trên 1 biểu đồ (Recharts ComposedChart):
//   • Units Sold        -> cột (trục trái)
//   • Cumulative Sold   -> đường (trục trái)
//   • Absorption Rate % -> đường (trục phải)
// Có legend, tooltip, 2 trục, responsive, và xử lý loading/empty/error.
import React from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { color, size, radius, shadow, space, font, layout } from "../../styles/tokens";
import { useBreakpoint, pick } from "../../hooks/useBreakpoint";
import { SectionState, Skeleton } from "../ui/States";

export default function AbsorptionTrendChart({ series, loading, error, onRetry }) {
  const { bp } = useBreakpoint();
  const empty = !loading && !error && (!series || series.length === 0);

  return (
    <section style={S.card}>
      <header style={S.head}>
        <div>
          <h2 style={S.title}>Absorption Trend</h2>
          <p style={S.sub}>Số căn bán theo ngày · luỹ kế · tỷ lệ hấp thụ</p>
        </div>
      </header>

      <SectionState
        loading={loading} error={error} empty={empty} onRetry={onRetry}
        skeleton={<div style={{ padding: space(2) }}><Skeleton height={pick(bp, layout.chartHeight)} /></div>}
      >
        <ResponsiveContainer width="100%" height={pick(bp, layout.chartHeight)}>
          <ComposedChart data={series || []} margin={{ top: 12, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke={color.border} vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: size.tiny, fill: color.muted }}
              tickFormatter={shortDate} minTickGap={pick(bp, { mobile: 60, tablet: 44, laptop: 36, desktop: 30 })}
              axisLine={{ stroke: color.border }} tickLine={false} />
            <YAxis yAxisId="left" tick={{ fontSize: size.tiny, fill: color.muted }} axisLine={false} tickLine={false} width={44} />
            <YAxis yAxisId="right" orientation="right" domain={[0, 100]} unit="%"
              tick={{ fontSize: size.tiny, fill: color.muted }} axisLine={false} tickLine={false} width={44} />
            <Tooltip content={<CustomTip />} />
            <Legend wrapperStyle={{ fontSize: size.tiny, paddingTop: 8 }} iconType="circle" />
            <Bar yAxisId="left" dataKey="units_sold" name="Units Sold" fill={color.accent} fillOpacity={0.22} radius={[3, 3, 0, 0]} maxBarSize={22} />
            <Line yAxisId="left" type="monotone" dataKey="cumulative_sold" name="Cumulative Sold" stroke={color.ink} strokeWidth={2} dot={false} />
            <Line yAxisId="right" type="monotone" dataKey="absorption_rate" name="Absorption Rate" stroke={color.ok} strokeWidth={2} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </SectionState>
    </section>
  );
}

function shortDate(d) {
  if (!d) return "";
  const [, m, day] = d.split("-");
  return `${day}/${m}`;
}

function CustomTip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  const line = (name, val, suffix = "") => (
    <div style={S.tipRow}><span>{name}</span><b>{val ?? "N/A"}{val != null ? suffix : ""}</b></div>
  );
  return (
    <div style={S.tip}>
      <div style={S.tipDate}>{label}</div>
      {line("Units Sold", row.units_sold)}
      {line("Cumulative", row.cumulative_sold)}
      {line("Absorption", row.absorption_rate, "%")}
    </div>
  );
}

const S = {
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(5), boxShadow: shadow, marginBottom: space(5) },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: space(3) },
  title: { fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0, fontFamily: font.sans },
  sub: { fontSize: size.tiny, color: color.muted, margin: "3px 0 0" },
  tip: { background: color.surface, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(3)}px`, boxShadow: "0 4px 14px rgba(16,24,32,.12)", fontSize: size.tiny, minWidth: 180 },
  tipDate: { color: color.muted, marginBottom: 4, fontFamily: font.mono },
  tipRow: { display: "flex", justifyContent: "space-between", gap: space(4), color: color.body, lineHeight: 1.7 },
};
