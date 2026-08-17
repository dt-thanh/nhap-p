// frontend/src/pages/ProjectsPage.jsx
// S02 — Danh sách dự án (đã đăng nhập). Lưới thẻ, tìm kiếm, lọc theo trạng thái.
// Bấm thẻ -> /projects/:id (S03). Dữ liệu từ GET /api/projects.
import React, { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { listProjects } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";
import { color, size, radius, shadow, space, font } from "../styles/tokens";
import { SectionState } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";

const STATUS = {
  active:   { label: "Đang bán", fg: color.ok, bg: color.okSoft },
  upcoming: { label: "Sắp mở", fg: color.warn, bg: color.warnSoft },
  archived: { label: "Lưu trữ", fg: color.muted, bg: color.canvas },
};
const FILTERS = [
  { key: "all", label: "Tất cả" },
  { key: "active", label: "Đang bán" },
  { key: "upcoming", label: "Sắp mở" },
  { key: "archived", label: "Lưu trữ" },
];

export default function ProjectsPage() {
  const navigate = useNavigate();
  const { bp } = useBreakpoint();
  const { data, loading, error, reload } = useAsync(() => listProjects(), []);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");

  const list = useMemo(() => {
    let l = data || [];
    if (filter !== "all") l = l.filter((p) => (p.status || "active") === filter);
    if (q.trim()) l = l.filter((p) => (p.name + p.location).toLowerCase().includes(q.toLowerCase()));
    return l;
  }, [data, filter, q]);

  const cols = pick(bp, { mobile: 1, tablet: 2, laptop: 3, desktop: 3 });

  return (
    <>
      <GlobalKeyframes />
      <div style={S.head}>
        <div>
          <h1 style={S.h1}>Dự án</h1>
          <p style={S.sub}>{data ? `${data.length} dự án đang theo dõi` : "Đang tải…"}</p>
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm dự án, khu vực…" style={S.search} />
      </div>

      <div style={S.filters}>
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilter(f.key)}
            style={{ ...S.chip, ...(filter === f.key ? S.chipOn : null) }}>
            {f.label}
          </button>
        ))}
      </div>

      <SectionState loading={loading} error={error} empty={!loading && list.length === 0} onRetry={reload}>
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: space(4) }}>
          {list.map((p) => {
            const st = STATUS[p.status] || STATUS.active;
            return (
              <button key={p.id} style={S.card} onClick={() => navigate(`/projects/${p.id}`)}>
                <div style={S.cardTop}>
                  <span style={{ ...S.badge, color: st.fg, background: st.bg }}>{st.label}</span>
                </div>
                <div style={S.cardBody}>
                  <div style={S.name}>{p.name}</div>
                  <div style={S.loc}>{p.location}</div>
                  <div style={S.statRow}>
                    <span>{(p.total_units ?? 0).toLocaleString("vi-VN")} căn</span>
                    <span style={S.abs}>{p.sold_pct != null ? `${p.sold_pct}% hấp thụ` : "—"}</span>
                  </div>
                  <div style={S.track}><span style={{ ...S.fill, width: `${p.sold_pct ?? 0}%` }} /></div>
                </div>
              </button>
            );
          })}
        </div>
      </SectionState>
    </>
  );
}

const S = {
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "4px 0 0" },
  search: { border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontFamily: "inherit", minWidth: 240, color: color.ink, outline: "none" },
  filters: { display: "flex", gap: space(2), marginBottom: space(5), flexWrap: "wrap" },
  chip: { background: color.surface, border: `1px solid ${color.borderStrong}`, color: color.body, fontSize: size.small, fontWeight: 600, padding: "7px 15px", borderRadius: radius.pill, cursor: "pointer", fontFamily: "inherit" },
  chipOn: { background: color.ink, color: "#fff", borderColor: color.ink },
  card: { textAlign: "left", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, overflow: "hidden", cursor: "pointer", fontFamily: "inherit", boxShadow: shadow, padding: 0 },
  cardTop: { height: 96, background: `linear-gradient(135deg, ${color.accent}22, ${color.ok}18)`, position: "relative", display: "flex", justifyContent: "flex-end", alignItems: "flex-start", padding: space(3) },
  badge: { fontSize: size.tiny, fontWeight: 700, padding: "4px 11px", borderRadius: radius.pill },
  cardBody: { padding: space(4) },
  name: { fontFamily: font.display, fontSize: size.body + 1, fontWeight: 700, color: color.ink },
  loc: { fontSize: size.tiny, color: color.muted, marginTop: 2 },
  statRow: { display: "flex", justifyContent: "space-between", alignItems: "center", margin: `${space(3)}px 0 ${space(2)}px`, fontSize: size.small, color: color.body },
  abs: { fontWeight: 700, color: color.ok },
  track: { height: 6, background: color.canvas, borderRadius: radius.pill, overflow: "hidden" },
  fill: { display: "block", height: "100%", background: color.ok, borderRadius: radius.pill },
};
