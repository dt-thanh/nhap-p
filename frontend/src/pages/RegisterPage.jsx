// frontend/src/pages/RegisterPage.jsx
// Trang ĐĂNG KÝ riêng (tách khỏi đăng nhập). Cùng bố cục chia đôi với Login.
//
// NỐI BACKEND (MVP3): hiện submit chỉ điều hướng tạm về /login.
// Khi có auth thật, thay handleSubmit bằng gọi POST /api/auth/register,
// rồi chuyển sang /login để đăng nhập.
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import Brand from "../components/Brand";
import { color, size, radius, space, font } from "../styles/tokens";
import { useBreakpoint } from "../hooks/useBreakpoint";

export default function RegisterPage() {
  const navigate = useNavigate();
  const { isNarrow } = useBreakpoint();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    if (pw !== pw2) { setError("Mật khẩu nhập lại không khớp."); return; }
    if (pw.length < 6) { setError("Mật khẩu cần tối thiểu 6 ký tự."); return; }
    setSubmitting(true);
    // TODO(MVP3): await register({ fullName, email, password: pw }); // POST /api/auth/register
    setTimeout(() => { setSubmitting(false); navigate("/login"); }, 400);
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", gridTemplateColumns: isNarrow ? "1fr" : "1.1fr 1fr", fontFamily: font.sans, background: color.surface }}>
      {!isNarrow && (
        <div style={S.brandPanel}>
          <Brand size={32} wordSize={19} withAI light />
          <div>
            <div style={S.brandHead}>Tạo tài khoản<br />AbsorbIQ AI</div>
            <p style={S.brandSub}>
              Truy cập bảng xếp hạng khả năng bán và dữ liệu hấp thụ theo dự án,
              phân khu — cập nhật theo thời gian thực.
            </p>
          </div>
          <div style={S.brandFoot}>© 2026 AbsorbIQ AI · VinUni × Vingroup</div>
        </div>
      )}

      <div style={S.formWrap}>
        <form style={S.form} onSubmit={handleSubmit}>
          {isNarrow && <div style={{ marginBottom: space(6) }}><Brand size={30} wordSize={18} withAI /></div>}
          <h1 style={S.h1}>Đăng ký</h1>
          <p style={S.sub}>Tạo tài khoản nội bộ để bắt đầu sử dụng AbsorbIQ.</p>

          <label style={S.label}>Họ và tên</label>
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Nguyễn Văn A" style={S.input} required />

          <label style={S.label}>Email</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ban.kinhdoanh@congty.vn" style={S.input} required />

          <label style={S.label}>Mật khẩu</label>
          <div style={S.pwWrap}>
            <input type={showPw ? "text" : "password"} value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Tối thiểu 6 ký tự" style={{ ...S.input, marginBottom: 0, border: "none", flex: 1 }} required />
            <button type="button" style={S.pwToggle} onClick={() => setShowPw((v) => !v)}>{showPw ? "Ẩn" : "Hiện"}</button>
          </div>

          <label style={S.label}>Nhập lại mật khẩu</label>
          <input type={showPw ? "text" : "password"} value={pw2} onChange={(e) => setPw2(e.target.value)} placeholder="Nhập lại mật khẩu" style={S.input} required />

          {error && <div style={S.error}>{error}</div>}

          <button type="submit" style={{ ...S.submit, opacity: submitting ? 0.7 : 1 }} disabled={submitting}>
            {submitting ? "Đang tạo tài khoản…" : "Đăng ký"}
          </button>

          <p style={S.reg}>Đã có tài khoản? <span style={S.link} onClick={() => navigate("/login")}>Đăng nhập</span></p>
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
  pwWrap: { display: "flex", alignItems: "center", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, paddingRight: 8, marginBottom: 16 },
  pwToggle: { background: "transparent", border: "none", color: color.accent, fontSize: size.tiny, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", padding: "4px 8px" },
  error: { background: color.dangerSoft, color: color.danger, borderRadius: radius.sm, padding: "9px 12px", fontSize: size.tiny, marginBottom: 16 },
  submit: { width: "100%", background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm, padding: 13, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(199,167,58,.24)" },
  reg: { fontSize: size.small, color: color.muted, textAlign: "center", marginTop: 18 },
  link: { color: color.accent, fontWeight: 600, cursor: "pointer" },
  back: { fontSize: size.small, color: color.muted, textAlign: "center", marginTop: 8, cursor: "pointer" },
};
