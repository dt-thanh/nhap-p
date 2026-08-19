// frontend/src/pages/ProjectDetailPage.jsx
// S03 — Giới thiệu dự án. Xác nhận đang xem đúng dự án nào, vài sự thật CÓ
// THẬT, và một CTA duy nhất sang dashboard nghiệp vụ
// (/projects/:externalId/dashboard). KHÔNG còn nhúng dashboard trực tiếp ở
// đây — đó là một bước riêng, có route riêng, để URL luôn phản ánh đúng đang
// xem "giới thiệu" hay "dashboard" (xem docs/ux proposal, mục Project introduction).
import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getProjectByExternalId, listAreasScoped } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { color, size, radius, space, font } from "../styles/tokens";
import { Skeleton } from "../components/ui/States";

const STATUS = {
  active: { label: "Đang bán", fg: color.ok, bg: color.okSoft },
  archived: { label: "Lưu trữ", fg: color.muted, bg: color.canvas },
};

export default function ProjectDetailPage() {
  const { externalId } = useParams();
  const navigate = useNavigate();
  const { data: project, loading, error } = useAsync(() => getProjectByExternalId(externalId), [externalId]);
  const { data: areas } = useAsync(
    () => (project ? listAreasScoped(externalId) : Promise.resolve(null)),
    [externalId, Boolean(project)],
  );

  if (error) {
    // Lỗi qua `useAsync` là object đã bọc `{message, status, network}` — so
    // `status` trực tiếp, không dùng `isAuthError()` (kiểm `instanceof
    // ApiError`, không còn khớp sau khi đã bị bọc lại).
    const notFound = error.status === 404;
    const forbidden = error.status === 401 || error.status === 403;
    return (
      <div style={S.stateWrap}>
        <h1 style={S.h1}>
          {notFound ? "Không tìm thấy dự án" : forbidden ? "Bạn không có quyền xem dự án này" : "Không tải được dự án"}
        </h1>
        <p style={S.sub}>
          {notFound
            ? `Không có dự án nào khớp "${externalId}".`
            : forbidden
              ? "Token hiện tại không nằm trong phạm vi dự án này."
              : error.message}
        </p>
        <button style={S.addBtn} onClick={() => navigate("/projects")}>Về danh sách dự án</button>
      </div>
    );
  }

  const st = project ? STATUS[project.status] : null;

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
          </div>
          {project && (
            <p style={S.sub}>
              {areas ? `${areas.length} phân khu` : "…"}
              {project.launch_date ? ` · Mở bán ${new Date(project.launch_date).toLocaleDateString("vi-VN")}` : ""}
            </p>
          )}
          {project?.introduce && <p style={S.introduce}>{project.introduce}</p>}
        </div>
        <div style={S.actions}>
          <button style={S.secondaryBtn} onClick={() => navigate("/import")}>Nạp dữ liệu</button>
          <button style={S.primaryBtn} onClick={() => navigate(`/projects/${externalId}/dashboard`)}>
            Xem dashboard
          </button>
        </div>
      </header>

      {project?.cover_image_url && (
        <div style={S.coverWrap}>
          {/* 404/lỗi ảnh -> ẩn khối, không hiện icon ảnh vỡ, không bịa placeholder. */}
          <img
            src={project.cover_image_url}
            alt=""
            style={S.cover}
            onError={(e) => { e.currentTarget.parentElement.style.display = "none"; }}
          />
        </div>
      )}
    </>
  );
}

const S = {
  crumb: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.small, color: color.muted, marginBottom: space(3) },
  crumbLink: { color: color.accent, fontWeight: 600, cursor: "pointer" },
  crumbCur: { color: color.body, fontWeight: 600 },
  sep: { color: color.borderStrong },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  titleRow: { display: "flex", alignItems: "center", gap: space(3), flexWrap: "wrap" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  badge: { fontSize: size.tiny, fontWeight: 700, padding: "4px 11px", borderRadius: radius.pill },
  sub: { fontSize: size.small, color: color.muted, margin: "6px 0 0" },
  introduce: { fontSize: size.small, color: color.body, margin: `${space(3)}px 0 0`, maxWidth: "70ch", lineHeight: 1.6 },
  actions: { display: "flex", gap: space(2), flex: "none" },
  secondaryBtn: {
    background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm,
    padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit",
  },
  primaryBtn: {
    background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm,
    padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer",
    fontFamily: "inherit", boxShadow: "0 4px 12px rgba(91,82,230,.28)",
  },
  coverWrap: { borderRadius: radius.md, overflow: "hidden", marginBottom: space(5), maxHeight: 280 },
  cover: { width: "100%", height: 280, objectFit: "cover", display: "block" },
  stateWrap: { maxWidth: 560, margin: "80px auto", textAlign: "center" },
  addBtn: {
    background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm,
    padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600,
    cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(91,82,230,.28)",
  },
};
