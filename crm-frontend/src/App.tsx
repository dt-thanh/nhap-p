import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { ProtectedRoute } from "./routes/ProtectedRoute";
import { AppLayout } from "./components/layout/AppLayout";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { Projects } from "./pages/Projects";
import { ProjectDetail } from "./pages/ProjectDetail";
import { Units } from "./pages/Units";
import { Deals } from "./pages/Deals";
import { DealDetail } from "./pages/DealDetail";
import { SalesTeam } from "./pages/SalesTeam";
import { ComingSoon } from "./pages/ComingSoon";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public */}
          <Route path="/login" element={<Login />} />

          {/* Được bảo vệ — cần đăng nhập */}
          <Route element={<ProtectedRoute />}>
            <Route element={<AppLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="projects" element={<Projects />} />
              <Route path="projects/:projectId" element={<ProjectDetail />} />
              <Route path="units" element={<Units />} />
              <Route path="deals" element={<Deals />} />
              <Route path="deals/:dealId" element={<DealDetail />} />
              <Route path="sales-team" element={<SalesTeam />} />

              {/* Chưa có mockup — placeholder để nav hoạt động */}
              <Route path="absorption" element={<ComingSoon title="Tỷ lệ hấp thụ" />} />
              <Route path="forecast" element={<ComingSoon title="Dự báo" />} />
              <Route path="ai-suggestions" element={<ComingSoon title="Gợi ý AI" />} />
              <Route path="users" element={<ComingSoon title="Người dùng" />} />
              <Route path="audit-logs" element={<ComingSoon title="Nhật ký hệ thống" />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
