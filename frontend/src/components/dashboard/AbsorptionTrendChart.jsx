// frontend/src/components/dashboard/AbsorptionTrendChart.jsx
// Absorption Trend — số căn bán mỗi ngày và velocity rolling:
//   • Units Sold per Day -> cột (trục trái)
//   • 7-day / 30-day Moving Average -> đường (trục trái)
// Sell-through và cumulative sold chỉ nằm trong tooltip để không trộn đơn vị
// căn và phần trăm vào cùng một trục.
// Có legend, tooltip, 2 trục, responsive, và xử lý loading/empty/error.
import React from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { color, size, radius, shadow, space, font, layout } from "../../styles/tokens";
import { useBreakpoint, pick } from "../../hooks/useBreakpoint";
import { SectionState, Skeleton } from "../ui/States";
import { DASHBOARD_TEXT, formatDashboardDate, formatDashboardNumber, formatDashboardUnits } from "./labels";

export default function AbsorptionTrendChart({ series, loading, error, onRetry, dataStatus, emptyMessage, granularity = "day", dataSource }) {
  const { bp } = useBreakpoint();
  const empty = !loading && !error && (!series || series.length === 0);
  const emptyTitle = emptyMessage || (dataStatus === "no_units"
    ? DASHBOARD_TEXT.noUnitsInScope
    : dataStatus === "no_data" ? DASHBOARD_TEXT.noDomainDataInPeriod : "Chưa có dữ liệu");
  const periodLabel = granularity === "month" ? DASHBOARD_TEXT.unitsSoldPerMonth : DASHBOARD_TEXT.unitsSoldPerDay;
  const subtitle = dataSource === "domain_units_deals"
    ? `${periodLabel} · ${DASHBOARD_TEXT.dataSourceDomain}`
    : granularity === "day" ? DASHBOARD_TEXT.trendSubtitle : periodLabel;

  return (
    <section style={S.card}>
      <header style={S.head}>
        <div>
          <h2 style={S.title}>{DASHBOARD_TEXT.trendTitle}</h2>
          <p style={S.sub}>{subtitle}</p>
        </div>
      </header>

      <SectionState
        loading={loading} error={error} empty={empty} onRetry={onRetry}
        emptyTitle={emptyTitle}
        emptyHint={dataStatus === "no_data" ? DASHBOARD_TEXT.noDomainDataInPeriod : undefined}
        skeleton={<div style={{ padding: space(2) }}><Skeleton height={pick(bp, layout.chartHeight)} /></div>}
      >
        <ResponsiveContainer width="100%" height={pick(bp, layout.chartHeight)}>
          <ComposedChart data={series || []} margin={{ top: 12, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke={color.border} vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: size.tiny, fill: color.muted }}
              tickFormatter={shortDate} minTickGap={pick(bp, { mobile: 60, tablet: 44, laptop: 36, desktop: 30 })}
              axisLine={{ stroke: color.border }} tickLine={false} />
            <YAxis yAxisId="left" allowDecimals={false} tickFormatter={formatInteger}
              tick={{ fontSize: size.tiny, fill: color.muted }} axisLine={false} tickLine={false} width={44} />
            <Tooltip content={<CustomTip />} />
            <Legend wrapperStyle={{ fontSize: size.tiny, paddingTop: 8 }} iconType="circle" />
            <Bar yAxisId="left" dataKey="units_sold" name={periodLabel} fill={color.accent} fillOpacity={0.22} radius={[3, 3, 0, 0]} maxBarSize={22} />
            <Line yAxisId="left" type="monotone" dataKey="moving_average_7d" name={DASHBOARD_TEXT.movingAverage7d} stroke={color.ok} strokeWidth={2} dot={false} />
            <Line yAxisId="left" type="monotone" dataKey="moving_average_30d" name={DASHBOARD_TEXT.movingAverage30d} stroke={color.ink} strokeWidth={2} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </SectionState>
    </section>
  );
}

function formatDate(value, options = { day: "2-digit", month: "2-digit", year: "numeric" }) {
  return formatDashboardDate(value, options);
}

function formatInteger(value) {
  if (value === null || value === undefined) return "";
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number).toLocaleString("vi-VN") : "";
}

function formatUnits(value) {
  return formatDashboardUnits(value);
}

function formatTooltipPercent(value) {
  return formatDashboardNumber(value, { digits: 2, suffix: "%" });
}

function shortDate(d) {
  return formatDate(d, { day: "2-digit", month: "2-digit" });
}

export function CustomTip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  const line = (name, value) => (
    <div style={S.tipRow}><span>{name}</span><b>{value}</b></div>
  );
  return (
    <div style={S.tip}>
      {line("Ngày", formatDate(row.date || label))}
      {line(DASHBOARD_TEXT.unitsSold, formatUnits(row.units_sold))}
      {line(DASHBOARD_TEXT.movingAverage7d, formatUnitsPerDay(row.moving_average_7d))}
      {line(DASHBOARD_TEXT.movingAverage30d, formatUnitsPerDay(row.moving_average_30d))}
      {line(DASHBOARD_TEXT.cumulativeSold, formatUnits(row.cumulative_sold))}
      {line(DASHBOARD_TEXT.sellThrough, formatTooltipPercent(row.sell_through ?? row.absorption_rate))}
    </div>
  );
}

function formatUnitsPerDay(value) {
  return formatDashboardUnits(value, { digits: 1, perDay: true });
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
