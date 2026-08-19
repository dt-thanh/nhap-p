// frontend/src/components/dashboard/AreaDetailTable.jsx
// Bảng chi tiết: Area | Total | Sold | Remaining | Absorption | Velocity | Latest Data | Status
// Hiện đại, dễ đọc, cuộn ngang trên màn hẹp. Thiếu giá trị -> N/A.
import React from "react";
import { color, size, radius, shadow, space, font } from "../../styles/tokens";
import { SectionState, fmt } from "../ui/States";
import { STATUS } from "./statusMap";

const COLS = [
  { key: "name", label: "Area", align: "left" },
  { key: "total_units", label: "Total", align: "right" },
  { key: "sold", label: "Sold", align: "right" },
  { key: "remaining", label: "Remaining", align: "right" },
  { key: "absorption_rate", label: "Absorption", align: "right", suffix: "%", digits: 1 },
  { key: "velocity", label: "Velocity", align: "right", digits: 1 },
  { key: "latest_data", label: "Latest Data", align: "left" },
  { key: "status", label: "Status", align: "left" },
];

export default function AreaDetailTable({ areas, loading, error, onRetry, onSelectArea }) {
  const empty = !loading && !error && (!areas || areas.length === 0);

  return (
    <section style={S.card}>
      <div style={S.head}>
        <h2 style={S.title}>Area Detail</h2>
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
                    <td style={S.tdNum}>{fmt(a.total_units)}</td>
                    <td style={S.tdNum}>{fmt(a.sold)}</td>
                    <td style={S.tdNum}>{fmt(a.remaining)}</td>
                    <td style={{ ...S.tdNum, fontWeight: 600, color: color.ink }}>{fmt(a.absorption_rate, { suffix: "%", digits: 1 })}</td>
                    <td style={S.tdNum}>{fmt(a.velocity, { digits: 1 })}</td>
                    <td style={{ ...S.td, color: color.muted }}>{a.latest_data ? new Date(a.latest_data).toLocaleDateString("vi-VN") : "N/A"}</td>
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
