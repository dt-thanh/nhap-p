// frontend/src/App.jsx
// Router tổng của AbsorbIQ.
//   • "/" và "/login", "/register" : full-screen, không có topbar app.
//   • Còn lại: bọc trong <AppLayout/> (topbar thương hiệu + ChatWidget).
//
// NỐI BACKEND: MVP1 chưa chặn quyền — mọi route đều vào được (đang chạy mock).
// MVP3 sẽ thêm <ProtectedRoute> kiểm tra đăng nhập trước AppLayout.
import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

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
import AreaDetailPage from "./pages/AreaDetailPage";
import ImportSelectPage from "./pages/ImportSelectPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Không có khung app */}
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Có khung app (topbar + chat) */}
        <Route element={<AppLayout />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/inventory" element={<InventoryPage />} />
          <Route path="/ai-agent" element={<AgentPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/projects/:id/areas/:areaId" element={<AreaDetailPage />} />
          <Route path="/import" element={<ImportSelectPage />} />
          <Route path="/import/upload" element={<UploadPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
