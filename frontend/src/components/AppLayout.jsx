// frontend/src/components/AppLayout.jsx
// Khung chung cho các trang ĐÃ đăng nhập: topbar thương hiệu + điều hướng + ChatWidget.
// HomePage (S00) và LoginPage (S01) KHÔNG dùng khung này (chúng full-screen riêng).
import React from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import Brand from "./Brand";
import { USE_MOCK } from "../api/client";
import { color, size, radius, space, font, layout } from "../styles/tokens";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";

const NAV = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/inventory", label: "Giỏ hàng" },
  { to: "/ai-agent", label: "AI Agent" },
  { to: "/audit", label: "Nhật ký" },
  { to: "/projects", label: "Dự án" },
  { to: "/import", label: "Nạp dữ liệu" },
];

export default function AppLayout() {
  const { bp, isMobile } = useBreakpoint();
  const navigate = useNavigate();
  const gutter = pick(bp, layout.gutter);

  return (
    <div style={{ minHeight: "100vh", background: color.canvas, fontFamily: font.sans, color: color.body }}>
      <header style={S.bar}>
        <div style={{ ...S.barInner, maxWidth: layout.maxWidth, padding: `0 ${gutter}px` }}>
          <span style={{ cursor: "pointer" }} onClick={() => navigate("/dashboard")}>
            <Brand size={30} wordSize={18} />
          </span>

          <nav style={S.nav}>
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to}
                style={({ isActive }) => ({ ...S.link, ...(isActive ? S.linkActive : null) })}>
                {n.label}
              </NavLink>
            ))}
          </nav>

          <div style={S.right}>
            {USE_MOCK && !isMobile && (
              <span style={S.mockTag} title="Đang chạy dữ liệu giả — đổi USE_MOCK trong api/client.js khi backend sẵn sàng">dữ liệu giả</span>
            )}
            <span style={S.avatar}>T</span>
          </div>
        </div>
      </header>

      <main>
        <div style={{ maxWidth: layout.maxWidth, margin: "0 auto", padding: `${space(7)}px ${gutter}px ${space(16)}px` }}>
          <Outlet />
        </div>
      </main>

    </div>
  );
}

const S = {
  bar: { background: color.surface, borderBottom: `1px solid ${color.border}`, position: "sticky", top: 0, zIndex: 20 },
  barInner: { margin: "0 auto", height: 64, display: "flex", alignItems: "center", gap: space(8) },
  nav: { display: "flex", gap: space(1), alignItems: "center", overflowX: "auto" },
  link: { fontSize: size.small, color: color.body, textDecoration: "none", padding: `${space(2)}px ${space(3)}px`, borderRadius: radius.sm, fontWeight: 500, whiteSpace: "nowrap" },
  linkActive: { background: color.accentSoft, color: color.accent, fontWeight: 600 },
  right: { marginLeft: "auto", display: "flex", alignItems: "center", gap: space(3), flex: "none" },
  mockTag: { fontSize: size.tiny, color: color.warn, background: color.warnSoft, border: `1px solid ${color.warn}33`, padding: "3px 9px", borderRadius: radius.pill, fontWeight: 600 },
  avatar: { width: 34, height: 34, borderRadius: "50%", background: color.accentSoft, color: color.accent, display: "grid", placeItems: "center", fontWeight: 700, fontSize: size.tiny, fontFamily: font.display },
};
