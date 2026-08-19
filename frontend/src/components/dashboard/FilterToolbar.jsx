// frontend/src/components/dashboard/FilterToolbar.jsx
// Toolbar lọc: Project · Area · Date range. Dạng card sạch.
import React from "react";
import { color, size, radius, shadow, space } from "../../styles/tokens";
import Icon from "../ui/Icon";

const RANGES = [
  { key: "30d", label: "30 ngày" },
  { key: "90d", label: "90 ngày" },
  { key: "12m", label: "12 tháng" },
];

export default function FilterToolbar({
  projects = [], areas = [],
  projectId, areaId, range,
  onProject, onArea, onRange, loading, showProjectSelector = true,
}) {
  return (
    <div style={S.card}>
      {showProjectSelector && (
        <>
          <Field label="Dự án" icon="database">
            <select value={projectId ?? ""} onChange={(e) => onProject(e.target.value)} style={S.select} disabled={loading}>
              {projects.length === 0 && <option value="">— Không có dự án —</option>}
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <span style={S.divider} />
        </>
      )}

      <Field label="Phân khu" icon="filter">
        <select value={areaId ?? "all"} onChange={(e) => onArea(e.target.value)} style={S.select} disabled={loading}>
          <option value="all">Tất cả phân khu</option>
          {areas.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>

      <span style={S.divider} />

      <Field label="Khoảng thời gian" icon="calendar">
        <div style={S.rangeGroup}>
          {RANGES.map((r) => (
            <button key={r.key} onClick={() => onRange(r.key)}
              style={{ ...S.rangeBtn, ...(range === r.key ? S.rangeOn : null) }}>
              {r.label}
            </button>
          ))}
        </div>
      </Field>
    </div>
  );
}

function Field({ label, icon, children }) {
  return (
    <div style={S.field}>
      <label style={S.label}><Icon name={icon} size={13} color={color.muted} /> {label}</label>
      {children}
    </div>
  );
}

const S = {
  card: {
    display: "flex", alignItems: "flex-end", gap: space(4), flexWrap: "wrap",
    background: color.surface, border: `1px solid ${color.border}`,
    borderRadius: radius.md, padding: `${space(4)}px ${space(5)}px`,
    boxShadow: shadow, marginBottom: space(5),
  },
  field: { display: "flex", flexDirection: "column", gap: space(1), minWidth: 160 },
  label: { display: "flex", alignItems: "center", gap: 5, fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em" },
  select: {
    border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm,
    padding: `${space(2)}px ${space(3)}px`, fontSize: size.small, color: color.ink,
    fontFamily: "inherit", background: color.surface, cursor: "pointer", minWidth: 180,
  },
  divider: { width: 1, alignSelf: "stretch", background: color.border, margin: `0 ${space(1)}px` },
  rangeGroup: { display: "flex", gap: 4, background: color.canvas, padding: 3, borderRadius: radius.sm },
  rangeBtn: {
    border: "none", background: "transparent", color: color.body,
    fontSize: size.tiny, fontWeight: 600, padding: "6px 12px",
    borderRadius: radius.sm - 2, cursor: "pointer", fontFamily: "inherit",
  },
  rangeOn: { background: color.surface, color: color.accent, boxShadow: shadow },
};
