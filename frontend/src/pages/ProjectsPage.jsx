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

// Bốn giá trị THẬT của `ProjectSummary.status` (src/models/schemas.py) —
// khớp CHECK constraint `projects.status`, không phải nhãn tự chọn.
const STATUS = {
  pending:  { label: "Chờ duyệt", fg: color.warn, bg: color.warnSoft },
  active:   { label: "Đang bán", fg: color.ok, bg: color.okSoft },
  rejected: { label: "Bị từ chối", fg: color.danger, bg: color.dangerSoft },
  archived: { label: "Lưu trữ", fg: color.muted, bg: color.canvas },
};
const FILTERS = [
  { key: "all", label: "Tất cả" },
  { key: "pending", label: "Chờ duyệt" },
  { key: "active", label: "Đang bán" },
  { key: "rejected", label: "Bị từ chối" },
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
    if (filter !== "all") l = l.filter((p) => p.status === filter);
    if (q.trim()) l = l.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()));
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
            // `ProjectSummary` (GET /projects) không có `.id` — danh tính điều
            // hướng là `external_id` (Mini CRM). Dự án DI SẢN (chưa có
            // external_id, tạo trước Phase D) chưa có route để xem — vô hiệu
            // hoá thẻ thay vì điều hướng tới một URL vỡ (`/projects/undefined`).
            const hasIdentity = Boolean(p.external_id);
            return (
              <button
                key={p.external_id || p.project_id}
                style={{ ...S.card, ...(hasIdentity ? null : S.cardDisabled) }}
                disabled={!hasIdentity}
                title={hasIdentity ? undefined : "Dự án di sản, chưa có danh tính Mini CRM — chưa xem được"}
                onClick={() => hasIdentity && navigate(`/projects/${p.external_id}`)}
              >
                <div style={S.cardTop}>
                  <span style={{ ...S.badge, color: st.fg, background: st.bg }}>{st.label}</span>
                </div>
                <div style={S.cardBody}>
                  <div style={S.name}>{p.name}</div>
                  {/* `ProjectSummary` không có location/total_units/sold_pct —
                      không bịa số ở màn danh sách; những con số này chỉ có
                      Ở MỘT dự án cụ thể (ProjectDetailPage/AbsorptionDashboard). */}
                  {p.launch_date && (
                    <div style={S.loc}>Mở bán {new Date(p.launch_date).toLocaleDateString("vi-VN")}</div>
                  )}
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
  cardDisabled: { cursor: "not-allowed", opacity: 0.55 },
  cardTop: { height: 96, background: `linear-gradient(135deg, ${color.accent}22, ${color.ok}18)`, position: "relative", display: "flex", justifyContent: "flex-end", alignItems: "flex-start", padding: space(3) },
  badge: { fontSize: size.tiny, fontWeight: 700, padding: "4px 11px", borderRadius: radius.pill },
  cardBody: { padding: space(4) },
  name: { fontFamily: font.display, fontSize: size.body + 1, fontWeight: 700, color: color.ink },
  loc: { fontSize: size.tiny, color: color.muted, marginTop: space(2) },
};
