// frontend/src/App.jsx
// Router tổng của AbsorbIQ.
//
// "/" và "/login", "/register": full-screen, không có AppLayout.
// Các route còn lại: bọc trong AppLayout gồm topbar và ChatWidget.
//
// MVP1 chưa chặn quyền.
// MVP3 sẽ thêm ProtectedRoute trước AppLayout.

import React from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";

import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import AppLayout from "./components/AppLayout";
import ProtectedRoute from "./components/ProtectedRoute";

import DashboardPage from "./pages/DashboardPage";
import InventoryPage from "./pages/InventoryPage";
import AgentPage from "./pages/AgentPage";
import RankingPage from "./pages/RankingPage";
import RankingConfigPage from "./pages/RankingConfigPage";

import ProjectsPage from "./pages/ProjectsPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import ProjectDashboardPage from "./pages/ProjectDashboardPage";
import AreaDetailPage from "./pages/AreaDetailPage";
import SettingsPage from "./pages/SettingsPage";

import ImportSelectPage from "./pages/ImportSelectPage";
import UploadPage from "./pages/UploadPage";
import OverviewPage from "./pages/OverviewPage";

export function AppRoutes() {
  return (
    <Routes>
        {/* Các trang full-screen */}
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Các trang nằm trong app layout — bọc ProtectedRoute (Entra SSO).
            "/", "/login", "/register" CỐ Ý nằm ngoài để trang đăng nhập và
            landing vẫn mở được khi chưa có phiên. */}
        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/inventory" element={<InventoryPage />} />
          <Route path="/ai-agent" element={<AgentPage />} />
          <Route path="/ranking" element={<RankingPage />} />
          <Route path="/ranking/configs" element={<RankingConfigPage />} />

          {/* Danh sách dự án */}
          <Route path="/projects" element={<ProjectsPage />} />

          <Route path="/settings" element={<SettingsPage />} />

          {/* Giới thiệu chi tiết dự án + danh sách phân khu */}
          <Route path="/projects/:externalId" element={<ProjectDetailPage />} />

          {/* Dashboard độc lập của một phân khu */}
          <Route path="/projects/:id/areas/:areaId" element={<AreaDetailPage />} />

          {/* Dashboard tổng của một dự án */}
          <Route
            path="/projects/:externalId/dashboard"
            element={<ProjectDashboardPage />}
          />

          {/* Import dữ liệu */}
          <Route path="/import" element={<ImportSelectPage />} />
          <Route path="/import/upload" element={<UploadPage />} />
          </Route>
        </Route>

        {/* Route không tồn tại */}
        <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
