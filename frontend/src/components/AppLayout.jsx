// frontend/src/components/AppLayout.jsx
// Khung chung cho các trang ĐÃ đăng nhập: sidebar + topbar + ChatWidget.
// HomePage (S00) và LoginPage (S01) KHÔNG dùng khung này (chúng full-screen riêng).
import React from "react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import Brand from "./Brand";
import ConnectPanel from "./ConnectPanel";
import ChatWidget from "./ChatWidget";
import Icon from "./ui/Icon";
import { USE_MOCK } from "../api/client";
import { color, size, radius, space, font, layout } from "../styles/tokens";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";

// "Dashboard" không còn là route độc lập — dashboard nghiệp vụ giờ khoá theo
// MỘT dự án cụ thể (/projects/:externalId/dashboard), tới từ trang Dự án. Một
// mục nav riêng chỉ trỏ vào /dashboard (nay chỉ redirect sang /projects) sẽ
// khiến người dùng bấm hai lần cho cùng một điểm đến — bỏ, không giữ mục
// trùng lặp.
const NAV = [
  { to: "/inventory", label: "Giỏ hàng", icon: "cart", end: true },
  { to: "/ai-agent", label: "AI tư vấn", icon: "bot", end: true },
  { to: "/ranking", label: "Xếp hạng", icon: "rate", end: true },
  { to: "/audit", label: "Nhật ký", icon: "calendar", end: true },
  { to: "/projects", label: "Dự án", icon: "folder" },
  { to: "/catalog", label: "Danh mục", icon: "catalog", end: true },
  { to: "/import", label: "Nạp dữ liệu", icon: "upload" },
];

export default function AppLayout() {
  const { bp, isMobile, isNarrow } = useBreakpoint();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [hoveredNav, setHoveredNav] = useState(null);
  const location = useLocation();
  const navigate = useNavigate();
  const gutter = pick(bp, layout.gutter);

  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!isNarrow || !drawerOpen) return undefined;

    function onKeyDown(event) {
      if (event.key === "Escape") setDrawerOpen(false);
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen, isNarrow]);

  const sidebarStyle = {
    ...S.sidebar,
    ...(isNarrow
      ? {
          position: "fixed",
          transform: drawerOpen ? "translateX(0)" : "translateX(-100%)",
          visibility: drawerOpen ? "visible" : "hidden",
          pointerEvents: drawerOpen ? "auto" : "none",
        }
      : { position: "sticky" }),
  };

  return (
    <div style={S.shell}>
      <aside
        id="app-sidebar"
        aria-label="Thanh điều hướng"
        aria-hidden={isNarrow ? !drawerOpen : undefined}
        style={sidebarStyle}
      >
        <button type="button" style={S.brandButton} onClick={() => navigate("/projects")} aria-label="Mở trang Dự án">
          <Brand size={32} wordSize={19} light />
        </button>

        <nav id="app-navigation" aria-label="Điều hướng chính" style={S.nav}>
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              onClick={() => isNarrow && setDrawerOpen(false)}
              onMouseEnter={() => setHoveredNav(n.to)}
              onMouseLeave={() => setHoveredNav(null)}
              onFocus={() => setHoveredNav(n.to)}
              onBlur={() => setHoveredNav(null)}
              style={({ isActive }) => ({
                ...S.link,
                ...(hoveredNav === n.to && !isActive ? S.linkHover : null),
                ...(isActive ? S.linkActive : null),
              })}
            >
              <Icon name={n.icon} size={19} color="currentColor" />
              <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      {isNarrow && drawerOpen && (
        <button type="button" style={S.overlay} onClick={() => setDrawerOpen(false)} aria-label="Đóng thanh điều hướng" />
      )}

      <div style={S.content}>
        <header style={S.bar}>
          <div style={{ ...S.barInner, maxWidth: layout.maxWidth, padding: `0 ${gutter}px` }}>
            {isNarrow && (
              <>
                <button
                  type="button"
                  style={S.menuButton}
                  onClick={() => setDrawerOpen((open) => !open)}
                  aria-label={drawerOpen ? "Đóng menu" : "Mở menu"}
                  aria-expanded={drawerOpen}
                  aria-controls="app-sidebar"
                >
                  <span aria-hidden="true">☰</span>
                </button>
                <button type="button" style={S.mobileBrand} onClick={() => navigate("/projects")} aria-label="Mở trang Dự án">
                  <Brand size={28} wordSize={17} />
                </button>
              </>
            )}

            <div style={S.right}>
              {USE_MOCK && !isMobile && (
                <span style={S.mockTag} title="Đang chạy dữ liệu giả — đổi USE_MOCK trong api/client.js khi backend sẵn sàng">dữ liệu giả</span>
              )}
              <ConnectPanel />
            </div>
          </div>
        </header>

        <main style={S.main}>
          <div style={{ ...S.mainInner, maxWidth: layout.maxWidth, padding: `${space(7)}px ${gutter}px ${space(16)}px` }}>
            <Outlet />
          </div>
        </main>
      </div>

      <ChatWidget />
    </div>
  );
}

const S = {
  shell: { minHeight: "100vh", display: "flex", background: color.canvas, fontFamily: font.sans, color: color.body },
  sidebar: {
    top: 0,
    left: 0,
    width: 232,
    height: "100vh",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    flex: "none",
    background: color.sidebar,
    borderRight: `1px solid ${color.sidebarSurface}`,
    zIndex: 40,
    transition: "transform 180ms ease, visibility 180ms ease",
  },
  brandButton: {
    display: "flex",
    alignItems: "center",
    width: "100%",
    padding: `${space(7)}px ${space(5)}px ${space(8)}px`,
    border: "none",
    background: "transparent",
    cursor: "pointer",
    textAlign: "left",
  },
  nav: { display: "flex", flexDirection: "column", gap: space(1), flex: 1, overflowY: "auto", padding: `0 ${space(3)}px ${space(5)}px` },
  link: {
    display: "flex",
    alignItems: "center",
    gap: space(3),
    fontSize: size.small,
    color: color.sidebarMuted,
    textDecoration: "none",
    padding: `${space(3)}px ${space(3)}px`,
    borderRadius: radius.sm,
    fontWeight: 500,
    whiteSpace: "nowrap",
    transition: "background 140ms ease, color 140ms ease",
  },
  linkHover: { background: color.sidebarSurface, color: color.sidebarText },
  linkActive: { background: color.sidebarActiveSoft, color: color.accent, fontWeight: 600, boxShadow: `inset 3px 0 0 ${color.accent}` },
  overlay: { position: "fixed", inset: 0, width: "100%", height: "100%", padding: 0, border: "none", background: "rgba(15,17,23,.48)", zIndex: 30, cursor: "pointer" },
  content: { flex: 1, minWidth: 0, display: "flex", flexDirection: "column" },
  bar: { background: color.surface, borderBottom: `1px solid ${color.border}`, position: "sticky", top: 0, zIndex: 20 },
  barInner: { margin: "0 auto", width: "100%", height: 64, display: "flex", alignItems: "center", gap: space(3), boxSizing: "border-box" },
  menuButton: { width: 38, height: 38, display: "grid", placeItems: "center", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, color: color.body, cursor: "pointer", fontSize: 19, lineHeight: 1 },
  mobileBrand: { border: "none", background: "transparent", padding: 0, cursor: "pointer" },
  main: { minWidth: 0, flex: 1 },
  mainInner: { width: "100%", margin: "0 auto", boxSizing: "border-box" },
  right: { marginLeft: "auto", display: "flex", alignItems: "center", gap: space(3), flex: "none" },
  mockTag: { fontSize: size.tiny, color: color.warn, background: color.warnSoft, border: `1px solid ${color.warn}33`, padding: "3px 9px", borderRadius: radius.pill, fontWeight: 600 },
};
