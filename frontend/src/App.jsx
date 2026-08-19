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

import DashboardPage from "./pages/DashboardPage";
import InventoryPage from "./pages/InventoryPage";
import AgentPage from "./pages/AgentPage";
import AuditPage from "./pages/AuditPage";

import ProjectsPage from "./pages/ProjectsPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import ProjectDashboardPage from "./pages/ProjectDashboardPage";
import AreaDetailPage from "./pages/AreaDetailPage";
import CatalogPage from "./pages/CatalogPage";

import ImportSelectPage from "./pages/ImportSelectPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Các trang full-screen */}
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Các trang nằm trong app layout */}
        <Route element={<AppLayout />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/inventory" element={<InventoryPage />} />
          <Route path="/ai-agent" element={<AgentPage />} />
          <Route path="/audit" element={<AuditPage />} />

          {/* Danh sách dự án */}
          <Route path="/projects" element={<ProjectsPage />} />

          {/* Danh mục: tạo/sửa dự án & phân khu qua Mini CRM */}
          <Route path="/catalog" element={<CatalogPage />} />

          {/* Giới thiệu chi tiết dự án */}
          <Route
            path="/projects/:externalId"
            element={<ProjectDetailPage />}
          />

          {/* Dashboard tổng của một dự án */}
          <Route
            path="/projects/:externalId/dashboard"
            element={<ProjectDashboardPage />}
          />

          {/* Chi tiết phân khu */}
          <Route
            path="/projects/:id/areas/:areaId"
            element={<AreaDetailPage />}
          />

          {/* Import dữ liệu */}
          <Route path="/import" element={<ImportSelectPage />} />
          <Route path="/import/upload" element={<UploadPage />} />
        </Route>

        {/* Route không tồn tại */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}