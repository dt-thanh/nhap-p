// frontend/src/components/ProtectedRoute.jsx
// Chặn quyền ở tầng route (MVP3). Không tự xác thực token với backend ở đây —
// chỉ kiểm token có mặt trong bộ nhớ hay không; mọi request THẬT phía sau vẫn tự
// chịu 401/403 nếu token sai/hết hạn (xem api/client.js). Bọc quanh AppLayout
// trong App.jsx, không bọc "/", "/login", "/register".
// Dev-only: khi VITE_DEV_AUTH_BYPASS=true (local/dev), bỏ qua cả kiểm token lẫn
// điều hướng /login — xem config/devAuth.js. Tắt cờ này thì hành vi y hệt cũ.
import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { getAccessToken } from "../api/client";
import { isDevAuthBypassEnabled } from "../config/devAuth";

export default function ProtectedRoute() {
  const location = useLocation();
  if (isDevAuthBypassEnabled()) {
    return <Outlet />;
  }
  if (!getAccessToken()) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}
