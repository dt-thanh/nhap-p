// frontend/src/hooks/useAuth.js
// ---------------------------------------------------------------------------
// CP7 — Hook đọc trạng thái đăng nhập AbsorbIQ. Cô lập, không phụ thuộc layout.
//
// KHÔNG tạo Context provider mới ở đây để tránh phải bọc lại cây component mà
// teammate đang chỉnh (dễ gây conflict). Hook tự gọi `/auth/me` một lần khi
// mount. Nếu về sau cần chia sẻ state, nâng lên Context là việc một dòng — còn
// bây giờ giữ nhỏ nhất có thể.
// ---------------------------------------------------------------------------
import { useEffect, useState, useCallback } from "react";
import { fetchMe, startLogin, logout as logoutFn } from "../api/auth";

export function useAuth() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      setUser(await fetchMe());
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return {
    user,
    loading,
    isAuthenticated: !!user,
    login: startLogin,
    logout: logoutFn,
    reload,
  };
}
