import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

/** CP4: cổng vào dựa trên DANH TÍNH đã được backend xác nhận (`/auth/me`), không
 *  còn dựa trên sự tồn tại của một chuỗi token trong localStorage. Khác biệt
 *  quan trọng: một chuỗi bất kỳ tự nhét vào localStorage từng đủ để qua cổng
 *  này; bây giờ thì không. */
export function ProtectedRoute() {
  const { user, loading } = useAuth();
  if (loading) {
    return <div className="flex min-h-screen items-center justify-center text-ink-muted">Đang kiểm tra phiên đăng nhập…</div>;
  }
  return user ? <Outlet /> : <Navigate to="/login" replace />;
}
