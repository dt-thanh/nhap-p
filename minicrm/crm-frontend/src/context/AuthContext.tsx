// ============================================================================
// CP4/CP5 — Trạng thái xác thực dựa trên Microsoft Entra ID.
//
// THAY ĐỔI CỐT LÕI so với bản trước: KHÔNG còn access token nào nằm trong
// JavaScript. Trước đây `AuthContext` cất `{user, token}` vào localStorage và
// `api.ts` gắn nó vào mỗi request — một lỗ XSS bất kỳ ở frontend đọc được token
// đó. Bây giờ backend phát một cookie `HttpOnly` (`minicrm_session`); trình
// duyệt tự gửi kèm mỗi request nhờ `credentials: "include"`, còn JS thì không
// đọc nổi. Đó là lý do context này KHÔNG có trường `token` nữa.
//
// Hệ quả: JS không tự biết mình đã đăng nhập hay chưa, nên phải HỎI backend qua
// `GET /auth/me`. Một vòng gọi mạng lúc khởi động là cái giá đã cân nhắc, đổi
// lấy việc token không bao giờ chạm tới `localStorage`.
// ============================================================================
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { apiGet, apiPost, ApiError, startLogin } from "../services/api";

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  role: "business_viewer" | "pipeline_operator" | "admin";
  projectScope: "ALL" | string[];
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  /** Điều hướng sang Entra. Không nhận email/password — Mini CRM không bao giờ
   *  nhìn thấy mật khẩu người dùng nữa; Microsoft giữ phần đó. */
  login: (returnTo?: string) => void;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Quyền tối thiểu — dùng để ẩn nút. KHÔNG phải ranh giới bảo mật: backend
   *  vẫn enforce độc lập (CP11), ẩn nút chỉ là tiện dụng. */
  hasRole: (minimum: AuthUser["role"]) => boolean;
}

const ROLE_LEVEL: Record<AuthUser["role"], number> = {
  business_viewer: 0,
  pipeline_operator: 1,
  admin: 2,
};

const AuthContext = createContext<AuthState | undefined>(undefined);

interface MeResponse {
  id?: string;
  email?: string;
  name?: string;
  role: AuthUser["role"];
  project_scope: "ALL" | string[];
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await apiGet<MeResponse>("/auth/me");
      setUser({
        id: me.id ?? "unknown",
        name: me.name ?? me.email ?? "Người dùng",
        email: me.email ?? "",
        role: me.role,
        projectScope: me.project_scope,
      });
    } catch (err) {
      // 401 = chưa đăng nhập (trạng thái BÌNH THƯỜNG lúc mở app lần đầu),
      // không phải lỗi cần hiển thị. 403 = đã đăng nhập nhưng chưa được gán
      // vai trò — cũng coi là chưa có phiên dùng được, nhưng thông báo khác
      // hẳn nên để `Login` tự phân biệt qua query string từ backend.
      if (!(err instanceof ApiError) || (err.status !== 401 && err.status !== 403)) {
        console.error("Không kiểm tra được phiên đăng nhập:", err);
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback((returnTo?: string) => {
    startLogin(returnTo ?? window.location.pathname);
  }, []);

  const logout = useCallback(async () => {
    try {
      const res = await apiPost<{ entra_logout_url: string | null }>("/auth/logout", {});
      setUser(null);
      // Kết thúc luôn phiên SSO ở phía Microsoft. Bỏ bước này thì lần bấm
      // "đăng nhập" kế tiếp vào thẳng lại và người dùng tưởng mình chưa thoát.
      if (res?.entra_logout_url) {
        window.location.href = res.entra_logout_url;
        return;
      }
    } catch {
      /* xoá trạng thái cục bộ dù backend có lỗi */
    }
    setUser(null);
    window.location.href = "/login";
  }, []);

  const hasRole = useCallback(
    (minimum: AuthUser["role"]) => !!user && ROLE_LEVEL[user.role] >= ROLE_LEVEL[minimum],
    [user],
  );

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, hasRole }}>
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth phải dùng trong AuthProvider");
  return ctx;
}
