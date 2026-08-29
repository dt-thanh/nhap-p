// ============================================================================
// CP4/CP5 — Trạng thái xác thực dựa trên Keycloak (OIDC).
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
import { apiGet, ApiError, startLogin, startLogout } from "../services/api";

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
  /** Điều hướng sang Keycloak. Không nhận email/password — Mini CRM không bao
   *  giờ nhìn thấy mật khẩu người dùng nữa; Keycloak giữ phần đó. */
  login: (returnTo?: string) => void;
  loginWithToken: (token: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Quyền tối thiểu — dùng để ẩn nút. KHÔNG phải ranh giới bảo mật: backend
   *  vẫn enforce độc lập (CP11), ẩn nút chỉ là tiện dụng. */
  hasRole: (minimum: AuthUser["role"]) => boolean;
}

/** Đọc bởi `Login.tsx` sau khi trình duyệt quay lại từ chuỗi redirect đăng
 *  xuất (có thể đi qua Keycloak). `sessionStorage` sống sót qua điều hướng
 *  sang origin khác trong CÙNG tab — đây là lý do dùng nó thay vì state React,
 *  vốn mất sạch ngay khi rời trang. */
export const LOGOUT_FLAG_KEY = "minicrm_logout_flag";
const LEGACY_AUTH_STORAGE_KEY = "crm_auth";

function clearLegacyAuthStorage(): void {
  // CP4 không còn lưu token trong Web Storage. Xoá khoá cũ khi logout để một
  // tab đã mở từ trước khi chuyển sang BFF không thể giữ artefact đăng nhập cũ.
  // Không xoá toàn bộ storage vì đó có thể chứa trạng thái không liên quan.
  try {
    localStorage.removeItem(LEGACY_AUTH_STORAGE_KEY);
    sessionStorage.removeItem(LEGACY_AUTH_STORAGE_KEY);
  } catch {
    // Storage có thể bị browser chặn; backend vẫn xoá cookie HttpOnly.
  }
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

  const loginWithToken = useCallback(async (token: string) => {
    localStorage.setItem("minicrm_access_token", token);
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
      localStorage.removeItem("minicrm_access_token");
      setUser(null);
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    setUser(null);
    clearLegacyAuthStorage();
    localStorage.removeItem("minicrm_access_token");
    try {
      sessionStorage.setItem(LOGOUT_FLAG_KEY, "single");
    } catch {
      // Riêng tư/ẩn danh có thể chặn sessionStorage — bỏ qua, chỉ mất thông
      // báo, không mất chức năng đăng xuất.
    }
    startLogout();
  }, []);

  const hasRole = useCallback(
    (minimum: AuthUser["role"]) => !!user && ROLE_LEVEL[user.role] >= ROLE_LEVEL[minimum],
    [user],
  );

  return (
    <AuthContext.Provider value={{ user, loading, login, loginWithToken, logout, refresh, hasRole }}>
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
