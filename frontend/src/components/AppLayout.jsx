// frontend/src/components/AppLayout.jsx
// Khung chung cho các trang ĐÃ đăng nhập: sidebar + topbar + ChatWidget.
// HomePage (S00) và LoginPage (S01) KHÔNG dùng khung này (chúng full-screen riêng).
import React from "react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import Brand from "./Brand";
import ChatWidget from "./ChatWidget";
import Icon from "./ui/Icon";
import LogoutButton from "./LogoutButton";
import { USE_MOCK } from "../api/client";
import { getMePermissions } from "../api/endpoints";
import { color, size, radius, space, layout } from "../styles/tokens";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";

// "Dashboard" không còn là route độc lập — dashboard nghiệp vụ giờ khoá theo
// MỘT dự án cụ thể (/projects/:externalId/dashboard), tới từ trang Dự án. Một
// mục nav riêng chỉ trỏ vào /dashboard (nay chỉ redirect sang /projects) sẽ
// khiến người dùng bấm hai lần cho cùng một điểm đến — bỏ, không giữ mục
// trùng lặp.
const NAV = [
  { to: "/overview", label: "Tổng quan", icon: "overview", end: true },
  { to: "/inventory", label: "Tồn kho", icon: "units", end: true },
  { to: "/ai-agent", label: "AI tư vấn", icon: "bot", end: true },
  { to: "/ranking", label: "Xếp hạng", icon: "rate", end: true },
  { to: "/projects", label: "Dự án", icon: "folder" },
];

export default function AppLayout() {
  const { bp, isMobile, isNarrow } = useBreakpoint();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [hoveredNav, setHoveredNav] = useState(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const gutter = pick(bp, layout.gutter);
  const navItems = isAdmin ? [...NAV, { to: "/settings", label: "Cài đặt", icon: "settings", end: true }] : NAV;

  useEffect(() => {
    let active = true;
    getMePermissions()
      .then((permissions) => {
        if (active) setIsAdmin(permissions?.role === "admin");
      })
      .catch(() => {
        if (active) setIsAdmin(false);
      });
    return () => {
      active = false;
    };
  }, []);

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
    ...(isNarrow ? S.sidebarNarrow : S.sidebarDesktop),
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
        <div aria-hidden="true" style={S.sidebarNotch} />
        <div aria-hidden="true" style={S.sidebarReflection} />
        <button type="button" style={S.brandButton} onClick={() => navigate("/projects")} aria-label="Mở trang Dự án">
          <Brand size={32} wordSize={19} light />
        </button>

        <nav id="app-navigation" aria-label="Điều hướng chính" style={S.nav}>
          {navItems.map((n) => (
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

      <div style={{ ...S.content, ...(isNarrow ? S.contentNarrow : S.contentDesktop) }}>
        <header style={{ ...S.bar, ...(isNarrow ? S.barNarrow : null) }}>
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
              <LogoutButton />
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
  shell: { height: "100vh", minHeight: 0, display: "flex", overflow: "hidden", background: color.canvas, fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif', color: color.body },
  sidebar: {
    top: 0,
    left: 0,
    width: 292,
    height: "100vh",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    flex: "none",
    position: "relative",
    overflow: "hidden",
    background: "#0B1220",
    border: "4px solid #050A12",
    boxShadow: "inset 1px 1px 0 rgba(255,255,255,.08), inset -1px -1px 0 rgba(0,0,0,.3), 0 22px 44px rgba(32,28,24,.18), 8px 14px 10px -10px rgba(32,28,24,.34)",
    zIndex: 40,
    transition: "transform 180ms ease, visibility 180ms ease",
  },
  sidebarDesktop: { width: 292, height: "calc(100vh - 32px)", margin: "16px 0 16px 16px", borderRadius: 34 },
  sidebarNarrow: { width: "min(292px, 86vw)", height: "100vh", margin: 0, border: "3px solid #050A12", borderRadius: "0 28px 28px 0", boxShadow: "inset 1px 1px 0 rgba(255,255,255,.07), 0 10px 24px rgba(32,28,24,.2)" },
  sidebarNotch: { position: "absolute", top: 14, left: "50%", width: 86, height: 19, borderRadius: radius.pill, background: "#050A12", boxShadow: "0 1px 0 rgba(255,255,255,.1)", transform: "translateX(-50%)", pointerEvents: "none", zIndex: 1 },
  sidebarReflection: { position: "absolute", top: -40, right: -72, width: "42%", height: "115%", background: "linear-gradient(116deg, transparent 0%, rgba(255,255,255,.045) 45%, transparent 70%)", transform: "skewX(-8deg)", pointerEvents: "none", zIndex: 1 },
  brandButton: {
    display: "flex",
    alignItems: "center",
    width: "100%",
    position: "relative",
    zIndex: 2,
    padding: `${space(13)}px ${space(5)}px ${space(8)}px`,
    border: "none",
    background: "transparent",
    cursor: "pointer",
    textAlign: "left",
  },
  nav: { display: "flex", flexDirection: "column", gap: space(1), flex: 1, position: "relative", zIndex: 2, overflowY: "auto", padding: `0 ${space(3)}px ${space(5)}px` },
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
  content: { flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" },
  contentDesktop: { height: "calc(100vh - 32px)", margin: "16px 16px 16px 20px", boxSizing: "border-box", border: "8px solid #2F3949", borderRadius: 32, background: "#F8F8F5", boxShadow: "inset 0 0 0 1px rgba(255,255,255,.52), inset 1px 1px 0 rgba(255,255,255,.7), inset -1px -1px 0 rgba(15,23,42,.3), 0 22px 44px rgba(32,28,24,.17), 8px 14px 10px -10px rgba(32,28,24,.32)" },
  contentNarrow: { height: "100vh", margin: 0, border: "none", borderRadius: 0, background: color.canvas, boxShadow: "none" },
  bar: { flexShrink: 0, background: "rgba(255,255,255,.94)", borderBottom: `1px solid ${color.border}`, borderRadius: "30px 30px 0 0", position: "sticky", top: 0, zIndex: 20 },
  barNarrow: { borderRadius: 0 },
  barInner: { margin: "0 auto", width: "100%", height: 64, display: "flex", alignItems: "center", gap: space(3), boxSizing: "border-box" },
  menuButton: { width: 38, height: 38, display: "grid", placeItems: "center", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, color: color.body, cursor: "pointer", fontSize: 19, lineHeight: 1 },
  mobileBrand: { border: "none", background: "transparent", padding: 0, cursor: "pointer" },
  main: { minWidth: 0, minHeight: 0, flex: 1, overflowY: "auto" },
  mainInner: { width: "100%", margin: "0 auto", boxSizing: "border-box" },
  right: { marginLeft: "auto", display: "flex", alignItems: "center", gap: space(3), flex: "none" },
  mockTag: { fontSize: size.tiny, color: color.warn, background: color.warnSoft, border: `1px solid ${color.warn}33`, padding: "3px 9px", borderRadius: radius.pill, fontWeight: 600 },
};
