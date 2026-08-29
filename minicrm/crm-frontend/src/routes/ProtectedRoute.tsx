import { Navigate, Outlet } from "react-router-dom";
import { LoaderCircle } from "lucide-react";
import { useAuth } from "../context/AuthContext";

/** CP4: cổng vào dựa trên DANH TÍNH đã được backend xác nhận (`/auth/me`), không
 *  còn dựa trên sự tồn tại của một chuỗi token trong localStorage. Khác biệt
 *  quan trọng: một chuỗi bất kỳ tự nhét vào localStorage từng đủ để qua cổng
 *  này; bây giờ thì không. */
export function ProtectedRoute() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-surface-page text-ink-muted">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-white text-primary shadow-card ring-1 ring-line"><LoaderCircle className="h-5 w-5 animate-spin" /></div>
        <p className="text-sm font-medium">Đang kiểm tra phiên đăng nhập…</p>
      </div>
    );
  }
  return user ? <Outlet /> : <Navigate to="/login" replace />;
}
