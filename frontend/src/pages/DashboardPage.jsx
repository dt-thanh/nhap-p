// frontend/src/pages/DashboardPage.jsx
// Route /dashboard cũ không còn ngữ cảnh dự án nào để chọn mặc định — dashboard
// nghiệp vụ giờ sống ở /projects/:externalId/dashboard (một dự án, chọn tường
// minh qua URL, không đoán "dự án đầu tiên"). Giữ route này làm lối vào ngắn,
// chuyển thẳng sang trang chọn dự án thay vì 404 hay hiện một dashboard rỗng.
import React from "react";
import { Navigate } from "react-router-dom";

export default function DashboardPage() {
  return <Navigate to="/projects" replace />;
}
