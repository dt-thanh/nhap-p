// frontend/src/components/dashboard/AreaComparison.jsx
// So sánh phân khu bằng thanh ngang absorption — trực quan, đọc nhanh thứ hạng.
import React from "react";
import { color, size, radius, shadow, space, font } from "../../styles/tokens";
import { SectionState, fmt } from "../ui/States";
import { STATUS } from "./statusMap";

export default function AreaComparison({ areas, loading, error, onRetry, onSelectArea }) {
  const empty = !loading && !error && (!areas || areas.length === 0);
  const max = Math.max(100, ...(areas || []).map((a) => a.absorption_rate || 0));

  return (
    <section style={S.card}>
      <h2 style={S.title}>Area Comparison</h2>
      <p style={S.sub}>Tỷ lệ hấp thụ theo phân khu</p>

      <SectionState loading={loading} error={error} empty={empty} onRetry={onRetry} compact>
        <div style={S.list}>
          {(areas || []).map((a) => {
            const st = STATUS[a.status] || STATUS.normal;
            const pct = a.absorption_rate != null ? (a.absorption_rate / max) * 100 : 0;
            return (
              <div
                key={a.id}
                style={onSelectArea ? { ...S.row, ...S.rowClickable } : S.row}
                onClick={onSelectArea ? () => onSelectArea(a.id) : undefined}
              >
                <div style={S.rowHead}>
                  <span style={S.name}>{a.name}</span>
                  <span style={{ ...S.chip, color: st.fg(), background: st.bg() }}>{st.label}</span>
                </div>
                <div style={S.barRow}>
                  <div style={S.track}>
                    <div style={{ ...S.fill, width: `${pct}%`, background: st.fg() }} />
                  </div>
                  <span style={S.pct}>{fmt(a.absorption_rate, { suffix: "%", digits: 1 })}</span>
                </div>
                <div style={S.meta}>
                  Sold {fmt(a.sold)} / {fmt(a.total_units)} · Velocity {fmt(a.velocity, { suffix: " căn/tuần", digits: 1 })}
                </div>
              </div>
            );
          })}
        </div>
      </SectionState>
    </section>
  );
}

const S = {
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(5), boxShadow: shadow, marginBottom: space(5) },
  title: { fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0, fontFamily: font.sans },
  sub: { fontSize: size.tiny, color: color.muted, margin: "3px 0 16px" },
  list: { display: "flex", flexDirection: "column", gap: space(4) },
  row: { display: "flex", flexDirection: "column", gap: space(1) },
  rowClickable: { cursor: "pointer" },
  rowHead: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  name: { fontSize: size.small, fontWeight: 600, color: color.ink },
  chip: { fontSize: 11, fontWeight: 700, padding: "2px 9px", borderRadius: radius.pill },
  barRow: { display: "flex", alignItems: "center", gap: space(3) },
  track: { flex: 1, height: 10, background: color.canvas, borderRadius: radius.pill, overflow: "hidden" },
  fill: { height: "100%", borderRadius: radius.pill },
  pct: { fontSize: size.small, fontWeight: 700, color: color.ink, minWidth: 52, textAlign: "right", fontVariantNumeric: "tabular-nums" },
  meta: { fontSize: size.tiny, color: color.muted },
};
