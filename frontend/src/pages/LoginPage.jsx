// frontend/src/pages/LoginPage.jsx
// S01 — Đăng nhập bằng TOKEN VAI TRÒ (mô hình hiện tại của đội).
//
// GHI CHÚ TÍCH HỢP (đọc kỹ trước khi đổi):
// File này khớp hợp đồng trong `LoginPage.test.jsx` của đội: người dùng DÁN một
// token vai trò → gọi `getMePermissions()` để xác thực token → vào app. KHÔNG
// đổi sang luồng redirect Entra ở ĐÂY: bản redirect từng được thử đã làm hỏng cả
// 4 test của đội. Khi bật SSO Entra thật cho Product frontend, điểm bám đúng là
// một nút/hook RIÊNG (xem `src/api/auth.js::startLogin` và `src/hooks/useAuth.js`)
// — thêm nút đó CẠNH ô token, không thay thế ô token, để hợp đồng test vẫn xanh.
import React, { useState, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Brand from "../components/Brand";
import { color, size, radius, space, font } from "../styles/tokens";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { ApiError, setAccessToken } from "../api/client";
import { getMePermissions } from "../api/endpoints";
import { startLogin, fetchMe } from "../api/auth";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { isNarrow } = useBreakpoint();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const from = location.state?.from?.pathname || "/overview";

  // SSO Entra — nếu người dùng đã có phiên (vừa đăng nhập ở Mini CRM cùng tenant,
  // hoặc quay lại app khi cookie còn sống), KHÔNG bắt họ dừng ở trang login. Hỏi
  // `/auth/me` một lần khi mount; có danh tính thì chuyển thẳng vào `from`. Đây
  // là mảnh khiến deep-link "Xem trong AbsorbIQ" mở đúng project mà không hỏi
  // lại mật khẩu: ProtectedRoute đá về /login, /login thấy cookie hợp lệ và bật
  // ngược vào ngay. 401 = chưa đăng nhập (bình thường) → ở lại trang, không lỗi.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await fetchMe();
        if (!cancelled && me) navigate(from, { replace: true });
      } catch {
        /* chưa có phiên → ở lại trang login */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [from, navigate]);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSubmitting(true);

    // Đặt token TRƯỚC khi hỏi quyền: `getMePermissions()` đi qua api/client.js,
    // vốn tự đính token đang giữ. Token sai → 401 → gỡ token ra để không kẹt một
    // token hỏng trong bộ nhớ.
    setAccessToken(token);
    try {
      await getMePermissions();
      navigate(from, { replace: true });
    } catch (err) {
      setAccessToken(null);
      if (err instanceof ApiError) {
        setError(err.status === 401 ? "Token không hợp lệ." : (err.message || "Đăng nhập thất bại."));
      } else {
        setError("Không kết nối được máy chủ, thử lại sau.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", gridTemplateColumns: isNarrow ? "1fr" : "1.1fr 1fr", fontFamily: font.sans, background: color.surface }}>
      {!isNarrow && (
        <div style={S.brandPanel}>
          <Brand size={32} wordSize={19} withAI light />
          <div>
            <div style={S.brandHead}>Xếp hạng khả năng bán<br />của từng căn hộ</div>
            <p style={S.brandSub}>
              Biết căn nào dễ bán nhất trong mỗi phân khu, kèm theo dõi tốc độ hấp thụ
              theo thời gian thực.
            </p>
          </div>
          <div style={S.brandFoot}>© 2026 AbsorbIQ AI · VinUni × Vingroup</div>
        </div>
      )}

      <div style={S.formWrap}>
        <form style={S.form} onSubmit={handleSubmit}>
          {isNarrow && <div style={{ marginBottom: space(6) }}><Brand size={30} wordSize={18} withAI /></div>}
          <h1 style={S.h1}>Đăng nhập</h1>
          <p style={S.sub}>Dán token vai trò được cấp để xem bảng xếp hạng và dữ liệu bán hàng.</p>

          <label style={S.label}>Token vai trò</label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Dán token vai trò được cấp"
            style={S.input}
            required
          />

          {error && <p style={S.error}>{error}</p>}

          <button type="submit" style={{ ...S.submit, opacity: submitting ? 0.7 : 1 }} disabled={submitting}>
            {submitting ? "Đang đăng nhập…" : "Đăng nhập"}
          </button>

          {/* CP5 — SSO Microsoft Entra, ĐẶT CẠNH ô token chứ không thay thế nó.
              Ô token vẫn là đường chính (hợp đồng LoginPage.test.jsx dựa vào nó);
              nút này là đường thứ hai, chỉ hiện khi backend đã cấu hình Entra.
              Nhãn nút CỐ Ý khác chuỗi "Đăng nhập" để `getByRole("button", { name:
              "Đăng nhập" })` trong test không bắt nhầm hai nút. */}
          <div style={S.ssoWrap}>
            <div style={S.divider}><span style={S.dividerText}>hoặc</span></div>
            <button type="button" onClick={() => startLogin(from)} style={S.ssoBtn}>
              Đăng nhập SSO
            </button>
            <p style={S.ssoHint}>
              Đăng nhập/Đăng ký qua SSO. Nếu bạn đã đăng nhập ở Mini CRM, bước
              này sẽ không hỏi lại mật khẩu.
            </p>
          </div>

          <p style={S.back} onClick={() => navigate("/")}>‹ Quay lại trang chủ</p>
        </form>
      </div>
    </div>
  );
}

const S = {
  brandPanel: { background: `linear-gradient(150deg, ${color.sidebar}, ${color.sidebarSurface})`, padding: 48, display: "flex", flexDirection: "column", justifyContent: "space-between", color: "#fff" },
  brandHead: { fontFamily: font.display, fontSize: 30, fontWeight: 700, lineHeight: 1.2, letterSpacing: "-.02em" },
  brandSub: { fontSize: 15, opacity: 0.85, marginTop: 14, lineHeight: 1.6, maxWidth: "40ch" },
  brandFoot: { fontSize: 12, opacity: 0.6 },
  formWrap: { display: "flex", alignItems: "center", justifyContent: "center", padding: 48 },
  form: { width: "100%", maxWidth: 400 },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "8px 0 26px", lineHeight: 1.5 },
  label: { display: "block", fontSize: size.tiny + 0.5, fontWeight: 600, color: color.body, marginBottom: 6 },
  input: { width: "100%", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "11px 14px", fontSize: size.small, color: color.ink, fontFamily: "inherit", marginBottom: 16, outline: "none" },
  error: { color: "#c0392b", fontSize: size.small, marginBottom: 14, marginTop: -4 },
  submit: { width: "100%", background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm, padding: 13, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(199,167,58,.24)" },
  link: { color: color.accent, fontWeight: 600, cursor: "pointer", fontSize: size.small },
  back: { fontSize: size.small, color: color.muted, textAlign: "center", marginTop: 18, cursor: "pointer" },
  ssoWrap: { marginTop: 22 },
  divider: { borderTop: `1px solid ${color.border}`, textAlign: "center", margin: "0 0 16px" },
  dividerText: { position: "relative", top: -9, background: color.surface, padding: "0 10px", fontSize: size.tiny, color: color.muted },
  ssoBtn: { width: "100%", background: "#fff", color: color.ink, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: 12, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit" },
  ssoHint: { fontSize: size.tiny, color: color.muted, textAlign: "center", marginTop: 10, lineHeight: 1.5 },
};
