// frontend/src/pages/LoginPage.jsx
// S01 — Đăng nhập. Bố cục chia đôi: panel thương hiệu (trái) + form (phải).
//
// NỐI BACKEND (MVP3): hiện submit chỉ điều hướng tạm về /dashboard.
// Khi có auth thật, thay handleSubmit bằng:
//   const res = await login(email, password);   // POST /api/auth/login
//   setAccessToken(res.access_token);            // giữ token trong bộ nhớ
//   navigate("/dashboard");
// (xem api/endpoints.js -> login, api/client.js -> setAccessToken)
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import Brand from "../components/Brand";
import { color, size, radius, space, font } from "../styles/tokens";
import { useBreakpoint } from "../hooks/useBreakpoint";

export default function LoginPage() {
  const navigate = useNavigate();
  const { isNarrow } = useBreakpoint();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    // TODO(MVP3): gọi login() thật. Tạm điều hướng để xem luồng.
    setTimeout(() => { setSubmitting(false); navigate("/dashboard"); }, 400);
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", gridTemplateColumns: isNarrow ? "1fr" : "1.1fr 1fr", fontFamily: font.sans, background: color.surface }}>
      {/* panel thương hiệu */}
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

      {/* form */}
      <div style={S.formWrap}>
        <form style={S.form} onSubmit={handleSubmit}>
          {isNarrow && <div style={{ marginBottom: space(6) }}><Brand size={30} wordSize={18} withAI /></div>}
          <h1 style={S.h1}>Đăng nhập</h1>
          <p style={S.sub}>Đăng nhập bằng tài khoản nội bộ để xem bảng xếp hạng và dữ liệu bán hàng.</p>

          <label style={S.label}>Email</label>
          <input
            type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder="ban.kinhdoanh@congty.vn" style={S.input} required
          />

          <label style={S.label}>Mật khẩu</label>
          <div style={S.pwWrap}>
            <input
              type={showPw ? "text" : "password"} value={pw} onChange={(e) => setPw(e.target.value)}
              placeholder="••••••••" style={{ ...S.input, marginBottom: 0, border: "none", flex: 1 }} required
            />
            <button type="button" style={S.pwToggle} onClick={() => setShowPw((v) => !v)}>
              {showPw ? "Ẩn" : "Hiện"}
            </button>
          </div>

          <div style={S.forgot}><span style={S.link}>Quên mật khẩu?</span></div>

          <button type="submit" style={{ ...S.submit, opacity: submitting ? 0.7 : 1 }} disabled={submitting}>
            {submitting ? "Đang đăng nhập…" : "Đăng nhập"}
          </button>

          <p style={S.reg}>Chưa có tài khoản? <span style={S.link} onClick={() => navigate("/register")}>Đăng ký</span></p>
          <p style={S.back} onClick={() => navigate("/")}>‹ Quay lại trang chủ</p>
        </form>
      </div>
    </div>
  );
}

const S = {
  brandPanel: { background: "linear-gradient(150deg, #5b52e6, #8b7ff0)", padding: 48, display: "flex", flexDirection: "column", justifyContent: "space-between", color: "#fff" },
  brandHead: { fontFamily: font.display, fontSize: 30, fontWeight: 700, lineHeight: 1.2, letterSpacing: "-.02em" },
  brandSub: { fontSize: 15, opacity: 0.85, marginTop: 14, lineHeight: 1.6, maxWidth: "40ch" },
  brandFoot: { fontSize: 12, opacity: 0.6 },

  formWrap: { display: "flex", alignItems: "center", justifyContent: "center", padding: 48 },
  form: { width: "100%", maxWidth: 400 },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "8px 0 26px", lineHeight: 1.5 },
  label: { display: "block", fontSize: size.tiny + 0.5, fontWeight: 600, color: color.body, marginBottom: 6 },
  input: { width: "100%", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "11px 14px", fontSize: size.small, color: color.ink, fontFamily: "inherit", marginBottom: 16, outline: "none" },
  pwWrap: { display: "flex", alignItems: "center", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, paddingRight: 8, marginBottom: 10 },
  pwToggle: { background: "transparent", border: "none", color: color.accent, fontSize: size.tiny, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", padding: "4px 8px" },
  forgot: { textAlign: "right", marginBottom: 20 },
  link: { color: color.accent, fontWeight: 600, cursor: "pointer", fontSize: size.small },
  submit: { width: "100%", background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm, padding: 13, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(91,82,230,.28)" },
  reg: { fontSize: size.small, color: color.muted, textAlign: "center", marginTop: 18 },
  back: { fontSize: size.small, color: color.muted, textAlign: "center", marginTop: 8, cursor: "pointer" },
};
