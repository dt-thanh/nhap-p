// frontend/src/components/dashboard/DataQualityPanel.jsx
// Data Quality: latest data · source · date range · error records · status/warnings.
// KHÔNG bịa giá trị — thiếu thì N/A.
import React from "react";
import { color, size, radius, shadow, space, font } from "../../styles/tokens";
import Icon from "../ui/Icon";
import { SectionState, fmt } from "../ui/States";
import { DASHBOARD_TEXT, formatDashboardDate } from "./labels";

const STATUS_MAP = {
  ok:                { label: DASHBOARD_TEXT.qualityOk, fg: () => color.ok, bg: () => color.okSoft },
  ok_with_warnings:  { label: DASHBOARD_TEXT.qualityWarnings, fg: () => color.warn, bg: () => color.warnSoft },
  error:             { label: DASHBOARD_TEXT.qualityError, fg: () => color.danger, bg: () => color.dangerSoft },
};

export default function DataQualityPanel({ dq, loading, error, onRetry }) {
  const empty = !loading && !error && !dq;
  const st = dq ? (STATUS_MAP[dq.status] || STATUS_MAP.ok) : null;
  const range = dq?.date_range
    ? `${fmtDate(dq.date_range.from)} → ${fmtDate(dq.date_range.to)}`
    : "Chưa có dữ liệu";

  return (
    <section style={S.card}>
      <div style={S.head}>
        <div style={S.headLeft}>
          <Icon name="database" size={16} color={color.muted} />
          <h2 style={S.title}>{DASHBOARD_TEXT.dataQuality}</h2>
        </div>
        {st && <span style={{ ...S.status, color: st.fg(), background: st.bg() }}>{st.label}</span>}
      </div>

      <SectionState loading={loading} error={error} empty={empty} onRetry={onRetry} compact>
        {dq && (
          <>
            <div style={S.grid}>
              <Item label={DASHBOARD_TEXT.latestData} value={dq.latest_data ? fmtDate(dq.latest_data) : "Chưa có dữ liệu"} />
              <Item label={DASHBOARD_TEXT.dataSource} value={dq.source || "Chưa có dữ liệu"} />
              <Item label={DASHBOARD_TEXT.dateRangeValue} value={range} />
              <Item label={DASHBOARD_TEXT.errorRecords} value={fmt(dq.error_records)}
                accent={dq.error_records > 0 ? color.danger : undefined} />
            </div>

            {dq.warnings?.length > 0 && (
              <div style={S.warnBox}>
                {dq.warnings.map((w, i) => (
                  <div key={i} style={S.warnRow}>
                    <Icon name="warning" size={14} color={color.warn} />
                    <span>{w}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </SectionState>
    </section>
  );
}

function Item({ label, value, accent }) {
  return (
    <div>
      <div style={S.itemLabel}>{label}</div>
      <div style={{ ...S.itemValue, color: accent || color.ink }}>{value}</div>
    </div>
  );
}

function fmtDate(d) {
  return formatDashboardDate(d);
}

const S = {
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(5), boxShadow: shadow, marginBottom: space(5) },
  head: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: space(4) },
  headLeft: { display: "flex", alignItems: "center", gap: space(2) },
  title: { fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0, fontFamily: font.sans },
  status: { fontSize: size.tiny, fontWeight: 700, padding: "4px 11px", borderRadius: radius.pill },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: space(4) },
  itemLabel: { fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 3 },
  itemValue: { fontSize: size.small, fontWeight: 600, color: color.ink },
  warnBox: { marginTop: space(4), display: "flex", flexDirection: "column", gap: space(2), background: color.warnSoft, borderRadius: radius.sm, padding: space(3) },
  warnRow: { display: "flex", alignItems: "flex-start", gap: space(2), fontSize: size.tiny, color: color.warn, lineHeight: 1.5 },
};
