// frontend/src/components/dashboard/AreaDetailTable.jsx
// Bảng chi tiết: Area | Total | Sold | Total Remaining | Available | Reserved | Absorption | Velocity | Latest Data | Status
// Hiện đại, dễ đọc, cuộn ngang trên màn hẹp. Thiếu giá trị -> N/A.
import React from "react";
import { color, size, radius, shadow, space, font } from "../../styles/tokens";
import { SectionState } from "../ui/States";
import { STATUS } from "./statusMap";
import { DASHBOARD_TEXT, formatDashboardDate, formatDashboardNumber, formatDashboardUnits } from "./labels";

const COLS = [
  { key: "name", label: "Phân khu", align: "left" },
  { key: "total_units", label: DASHBOARD_TEXT.totalUnits, align: "right" },
  { key: "sold", label: DASHBOARD_TEXT.unitsSold, align: "right" },
  { key: "remaining", label: DASHBOARD_TEXT.remainingUnits, align: "right" },
  { key: "available_remaining_units", label: DASHBOARD_TEXT.availableRemainingUnits, align: "right" },
  { key: "reserved_units", label: DASHBOARD_TEXT.reservedUnits, align: "right" },
  { key: "absorption_rate", label: DASHBOARD_TEXT.sellThrough, align: "right" },
  { key: "velocity", label: DASHBOARD_TEXT.velocity, align: "right" },
  { key: "latest_data", label: DASHBOARD_TEXT.latestDataColumn, align: "left" },
  { key: "status", label: DASHBOARD_TEXT.status, align: "left" },
];

export default function AreaDetailTable({ areas, loading, error, onRetry, onSelectArea }) {
  const empty = !loading && !error && (!areas || areas.length === 0);

  return (
    <section style={S.card}>
      <div style={S.head}>
        <h2 style={S.title}>{DASHBOARD_TEXT.areaDetail}</h2>
        <span style={S.count}>{areas ? `${areas.length} phân khu` : ""}</span>
      </div>

      <SectionState loading={loading} error={error} empty={empty} onRetry={onRetry} compact>
        <div style={S.scroll}>
          <table style={S.table}>
            <thead>
              <tr>
                {COLS.map((c) => (
                  <th key={c.key} style={{ ...S.th, textAlign: c.align }}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(areas || []).map((a) => {
                const st = STATUS[a.status] || STATUS.normal;
                return (
                  <tr
                    key={a.id}
                    style={onSelectArea ? { ...S.tr, ...S.trClickable } : S.tr}
                    onClick={onSelectArea ? () => onSelectArea(a.id) : undefined}
                  >
                    <td style={{ ...S.td, fontWeight: 600, color: color.ink }}>{a.name}</td>
                    <td style={S.tdNum}>{formatDashboardUnits(a.total_units)}</td>
                    <td style={S.tdNum}>{formatDashboardUnits(a.sold)}</td>
                    <td style={S.tdNum}>{formatInventoryUnits(a.remaining)}</td>
                    <td style={S.tdNum}>{formatInventoryUnits(a.available_remaining_units)}</td>
                    <td style={S.tdNum}>{formatInventoryUnits(a.reserved_units)}</td>
                    <td style={{ ...S.tdNum, fontWeight: 600, color: color.ink }}>{formatDashboardNumber(a.absorption_rate, { suffix: "%", digits: 1 })}</td>
                    <td style={S.tdNum}>{formatDashboardUnits(a.velocity, { digits: 1, perWeek: true })}</td>
                    <td style={{ ...S.td, color: color.muted }}>{formatDashboardDate(a.latest_data)}</td>
                    <td style={S.td}>
                      <span style={{ ...S.badge, color: st.fg(), background: st.bg() }}>{st.label}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </SectionState>
    </section>
  );
}

function formatInventoryUnits(value) {
  return value === null || value === undefined ? DASHBOARD_TEXT.insufficientData : formatDashboardUnits(value);
}

const S = {
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, marginBottom: space(5), overflow: "hidden" },
  head: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: `${space(4)}px ${space(5)}px ${space(3)}px` },
  title: { fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0, fontFamily: font.sans },
  count: { fontSize: size.tiny, color: color.muted },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", minWidth: 720 },
  th: { fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em", padding: `${space(2)}px ${space(4)}px`, borderTop: `1px solid ${color.border}`, borderBottom: `1px solid ${color.border}`, background: color.canvas, whiteSpace: "nowrap" },
  tr: {},
  trClickable: { cursor: "pointer" },
  td: { padding: `${space(3)}px ${space(4)}px`, fontSize: size.small, color: color.body, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  tdNum: { padding: `${space(3)}px ${space(4)}px`, fontSize: size.small, color: color.body, borderBottom: `1px solid ${color.border}`, textAlign: "right", fontVariantNumeric: "tabular-nums", fontFamily: font.mono, whiteSpace: "nowrap" },
  badge: { fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: radius.pill, whiteSpace: "nowrap" },
};
