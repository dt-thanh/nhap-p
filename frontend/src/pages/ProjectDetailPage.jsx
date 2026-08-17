// frontend/src/pages/ProjectDetailPage.jsx
// S03 — Chi tiết dự án. Header dự án (breadcrumb, tên, trạng thái) + NHÚNG
// Dashboard hấp thụ (khoá theo dự án này, ẩn bộ chọn dự án).
import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getProject } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import AbsorptionDashboard from "../components/dashboard/AbsorptionDashboard";
import { color, size, radius, space, font } from "../styles/tokens";
import { Skeleton } from "../components/ui/States";

const STATUS = {
  active:   { label: "Đang bán", fg: color.ok, bg: color.okSoft },
  upcoming: { label: "Sắp mở", fg: color.warn, bg: color.warnSoft },
  archived: { label: "Lưu trữ", fg: color.muted, bg: color.canvas },
};

export default function ProjectDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data: project, loading } = useAsync(() => getProject(id), [id]);
  const st = project ? (STATUS[project.status] || STATUS.active) : null;

  return (
    <>
      <div style={S.crumb}>
        <span style={S.crumbLink} onClick={() => navigate("/projects")}>Dự án</span>
        <span style={S.sep}>/</span>
        <span style={S.crumbCur}>{project?.name || "…"}</span>
      </div>

      <header style={S.head}>
        <div>
          <div style={S.titleRow}>
            <h1 style={S.h1}>{loading ? <Skeleton width={220} height={26} /> : project?.name}</h1>
            {st && <span style={{ ...S.badge, color: st.fg, background: st.bg }}>{st.label}</span>}
            <span style={S.canon}>● Dữ liệu canonical</span>
          </div>
          {project && (
            <p style={S.sub}>
              {project.zone_count} phân khu · {(project.total_units ?? 0).toLocaleString("vi-VN")} căn
              {project.launch_date ? ` · Mở bán ${new Date(project.launch_date).toLocaleDateString("vi-VN")}` : ""}
            </p>
          )}
        </div>
        <button style={S.addBtn} onClick={() => navigate("/import")}>Nạp dữ liệu</button>
      </header>

      {/* Dashboard nhúng: khoá theo dự án, ẩn bộ chọn dự án + header riêng */}
      <AbsorptionDashboard fixedProjectId={id} showProjectSelector={false} showHeader={false} />
    </>
  );
}

const S = {
  crumb: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.small, color: color.muted, marginBottom: space(3) },
  crumbLink: { color: color.accent, fontWeight: 600, cursor: "pointer" },
  crumbCur: { color: color.body, fontWeight: 600 },
  sep: { color: color.borderStrong },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  titleRow: { display: "flex", alignItems: "center", gap: space(3), flexWrap: "wrap" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  badge: { fontSize: size.tiny, fontWeight: 700, padding: "4px 11px", borderRadius: radius.pill },
  canon: { fontSize: size.tiny, fontWeight: 600, color: color.accent, background: color.accentSoft, borderRadius: radius.pill, padding: "4px 11px" },
  sub: { fontSize: size.small, color: color.muted, margin: "6px 0 0" },
  addBtn: { background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(91,82,230,.28)" },
};
