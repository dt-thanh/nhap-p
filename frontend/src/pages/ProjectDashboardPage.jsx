// frontend/src/pages/ProjectDashboardPage.jsx
// S03b — Dashboard nghiệp vụ của MỘT dự án, sống ở /projects/:externalId/dashboard.
//
// Trang này CHỈ làm một việc trước khi vẽ dashboard: xác nhận dự án đó THẬT
// SỰ tồn tại VÀ nằm trong phạm vi của token hiện tại. Trang có thể được vào
// bằng deep link trực tiếp (không nhất thiết đi qua trang giới thiệu dự án
// trước) — nếu không kiểm ở đây, một dự án không tồn tại hoặc ngoài phạm vi sẽ
// chỉ hiện một dashboard rỗng thay vì một lỗi rõ ràng.
import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getProjectByExternalId } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import AbsorptionDashboard from "../components/dashboard/AbsorptionDashboard";
import { color, size, radius, space, font } from "../styles/tokens";
import { Skeleton } from "../components/ui/States";

export default function ProjectDashboardPage() {
  const { externalId } = useParams();
  const navigate = useNavigate();
  const { data: project, loading, error } = useAsync(() => getProjectByExternalId(externalId), [externalId]);

  if (error) {
    // Lỗi qua `useAsync` là object đã bọc `{message, status, network}`, KHÔNG
    // còn là instance `ApiError` — `isAuthError()` (kiểm `instanceof`) sẽ
    // không khớp ở đây, nên so `status` trực tiếp.
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
        <button style={S.backBtn} onClick={() => navigate("/projects")}>Về danh sách dự án</button>
      </div>
    );
  }

  return (
    <>
      <div style={S.crumb}>
        <span style={S.crumbLink} onClick={() => navigate("/projects")}>Dự án</span>
        <span style={S.sep}>/</span>
        <span style={S.crumbLink} onClick={() => navigate(`/projects/${externalId}`)}>
          {loading ? <Skeleton width={100} height={14} /> : project?.name}
        </span>
        <span style={S.sep}>/</span>
        <span style={S.crumbCur}>Dashboard</span>
      </div>
      <AbsorptionDashboard projectExternalId={externalId} />
    </>
  );
}

const S = {
  crumb: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.small, color: color.muted, marginBottom: space(3) },
  crumbLink: { color: color.accent, fontWeight: 600, cursor: "pointer" },
  crumbCur: { color: color.body, fontWeight: 600 },
  sep: { color: color.borderStrong },
  stateWrap: { maxWidth: 560, margin: "80px auto", textAlign: "center" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0 },
  sub: { fontSize: size.small, color: color.muted, margin: "10px 0 20px" },
  backBtn: {
    background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm,
    padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600,
    cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(91,82,230,.28)",
  },
};
