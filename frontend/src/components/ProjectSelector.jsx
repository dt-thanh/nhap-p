// frontend/src/components/ProjectSelector.jsx
// Phase F: chọn Project — chỉ liệt kê những gì `GET /v1/projects` trả về, và
// route đó ĐÃ tự lọc theo phạm vi của token hiện tại (Phase E, tầng truy vấn).
// Component này không tự lọc thêm gì — lọc ở FE không phải là bảo mật.
import React from "react";
import { color, size, radius, space } from "../styles/tokens";

export default function ProjectSelector({ projects = [], value, onChange, loading, status }) {
  if (loading) return <div style={{ ...S.select, ...S.skeleton }}>Đang tải dự án…</div>;

  if (status === "unauthorized") {
    return (
      <div style={S.unauthorized} role="alert">
        Chưa đăng nhập hoặc token đã hết hạn — không thấy được dự án nào. Kết nối lại ở góc trên bên phải.
      </div>
    );
  }

  if (status === "error") {
    return <div style={S.unauthorized} role="alert">Không tải được danh sách dự án. Thử lại sau.</div>;
  }

  if (!projects.length) {
    return <div style={S.empty}>Chưa có dự án nào trong phạm vi của bạn.</div>;
  }

  return (
    <label style={S.label}>
      Dự án
      <select
        style={S.select}
        value={value || ""}
        onChange={(e) => onChange(e.target.value || null)}
        aria-label="Chọn dự án"
      >
        <option value="" disabled>
          — Chọn dự án —
        </option>
        {projects.map((p) => (
          <option key={p.external_id || p.project_id} value={p.external_id || ""} disabled={!p.external_id}>
            {p.name}
            {!p.external_id ? " (di sản — chưa có external_id)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

const S = {
  label: { display: "flex", flexDirection: "column", gap: 4, fontSize: size.small, fontWeight: 600, color: color.ink },
  select: {
    padding: `${space(2)}px ${space(3)}px`,
    borderRadius: radius.sm,
    border: `1px solid ${color.borderStrong}`,
    fontSize: size.small,
    fontFamily: "inherit",
    background: color.surface,
    minWidth: 220,
  },
  skeleton: { opacity: 0.5, color: color.muted },
  empty: { fontSize: size.small, color: color.muted },
  unauthorized: {
    fontSize: size.small,
    color: color.danger,
    background: color.dangerSoft,
    padding: `${space(2)}px ${space(3)}px`,
    borderRadius: radius.sm,
  },
};
