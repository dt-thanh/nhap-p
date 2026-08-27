// frontend/src/components/ProtectedRoute.jsx
// Chặn quyền ở tầng route (MVP3). Hai cơ chế đăng nhập cùng tồn tại: token vai
// trò giữ trong bộ nhớ (đồng bộ, xem api/client.js) và phiên SSO nằm ở cookie
// HttpOnly `absorbiq_session` (chỉ biết được qua gọi `/auth/me`, xem useAuth.js).
// Token có mặt thì vào ngay, không chờ; không có token thì hỏi `/auth/me` một
// lần trước khi quyết định — thiếu nhánh này thì user đăng nhập SSO xong bị đá
// ngược lại /login vì component chỉ nhìn thấy token cũ.
// Bọc quanh AppLayout trong App.jsx, không bọc "/", "/login", "/register".
// Dev-only: khi VITE_DEV_AUTH_BYPASS=true (local/dev), bỏ qua mọi kiểm tra —
// xem config/devAuth.js. Tắt cờ này thì hành vi y hệt cũ.
import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { getAccessToken } from "../api/client";
import { isDevAuthBypassEnabled } from "../config/devAuth";
import { useAuth } from "../hooks/useAuth";

export default function ProtectedRoute() {
  const location = useLocation();
  const { isAuthenticated, loading } = useAuth();

  if (isDevAuthBypassEnabled()) {
    return <Outlet />;
  }
  if (getAccessToken()) {
    return <Outlet />;
  }
  if (loading) {
    return null;
  }
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}
