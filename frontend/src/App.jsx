// frontend/src/App.jsx
// Router tổng của AbsorbIQ.
//
// "/" và "/login", "/register": full-screen, không có AppLayout.
// Các route còn lại: bọc trong AppLayout gồm topbar.
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
import AdvisorAnalysisRoute from "./components/AdvisorAnalysisRoute";
import ProtectedRoute from "./components/ProtectedRoute";

import DashboardPage from "./pages/DashboardPage";
import AgentPage from "./pages/AgentPage";
import RankingPage from "./pages/RankingPage";
import RankingDashboardPage from "./pages/RankingDashboardPage";
import RankingProjectPage from "./pages/RankingProjectPage";
import ProjectRankingReportPage from "./pages/ProjectRankingReportPage";
import AreaUnitRankingPage from "./pages/AreaUnitRankingPage";
import UnitRankingReportPage from "./pages/UnitRankingReportPage";
import RankingConfigPage from "./pages/RankingConfigPage";
import ExpertAnalysisPage from "./pages/ExpertAnalysisPage";
import AdvisorAnalysisReviewPage from "./pages/AdvisorAnalysisReviewPage";

import ProjectsPage from "./pages/ProjectsPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import ProjectDashboardPage from "./pages/ProjectDashboardPage";
import AreaDetailPage from "./pages/AreaDetailPage";

import ImportSelectPage from "./pages/ImportSelectPage";
import UploadPage from "./pages/UploadPage";
import OverviewPage from "./pages/OverviewPage";
import PreviewOverviewPage from "./pages/PreviewOverviewPage";

export function AppRoutes() {
  return (
    <Routes>
        {/* Các trang full-screen */}
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Các trang nằm trong app layout — bọc ProtectedRoute (Keycloak SSO).
            "/", "/login", "/register" CỐ Ý nằm ngoài để trang đăng nhập và
            landing vẫn mở được khi chưa có phiên. */}
        <Route element={<ProtectedRoute />}>
          <Route path="/preview/overview" element={<PreviewOverviewPage />} />
          <Route element={<AppLayout />}>
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/ai-agent" element={<AgentPage />} />
          <Route path="/ranking" element={<RankingDashboardPage />} />
          <Route path="/ranking/configs" element={<RankingConfigPage />} />
          <Route path="/ranking/:projectId/report" element={<ProjectRankingReportPage />} />
          <Route path="/ranking/:projectId/areas/:areaId" element={<AreaUnitRankingPage />} />
          <Route path="/ranking/:projectId/areas/:areaId/units/:unitId/report" element={<UnitRankingReportPage />} />
          <Route path="/ranking/:projectId" element={<RankingProjectPage />} />
          <Route element={<AdvisorAnalysisRoute capability="advisor_analysis_authoring" />}>
            <Route path="/expert-analysis" element={<ExpertAnalysisPage />} />
          </Route>
          <Route element={<AdvisorAnalysisRoute capability="advisor_analysis_review" />}>
            <Route path="/advisor-analysis/review" element={<AdvisorAnalysisReviewPage />} />
          </Route>

          {/* Danh sách dự án */}
          <Route path="/projects" element={<ProjectsPage />} />

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
