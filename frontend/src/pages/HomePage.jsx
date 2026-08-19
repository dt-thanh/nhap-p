// frontend/src/pages/HomePage.jsx
// S00 — Trang chủ khi CHƯA đăng nhập.
// Giới thiệu sản phẩm: AbsorbIQ tổng hợp tín hiệu thị trường (booking, đăng ký
// quan tâm, hành vi online, độ khan hiếm...) để tính INTEREST SCORE và xếp hạng
// MỨC ĐỘ QUAN TÂM của từng căn/nhóm căn — KHÔNG dự đoán xác suất bán.
// KHÔNG hiển thị dữ liệu bán hàng thật (bảo mật) — số liệu chỉ là "Demo data".
//
// Nối backend: trang này không gọi API. Nút Đăng nhập/Đăng ký điều hướng
// tới /login, /register (auth thật ở MVP3).
import React from "react";
import { useNavigate } from "react-router-dom";
import Brand from "../components/Brand";
import { color, size, radius, shadow, space, font } from "../styles/tokens";
import { useBreakpoint } from "../hooks/useBreakpoint";

// Điểm quan tâm minh hoạ cho thẻ xếp hạng ở hero (Interest Score /100, KHÔNG phải %).
const DEMO_RANK = [
  { unit: "A-12.05", spec: "2PN · 61m²", score: 92, tone: "ok" },
  { unit: "B-08.02", spec: "2PN · 58m²", score: 87, tone: "ok" },
  { unit: "A-15.11", spec: "3PN · 86m²", score: 81, tone: "ok" },
  { unit: "C-03.07", spec: "Studio · 31m²", score: 64, tone: "warn" },
  { unit: "B-11.04", spec: "3PN · 84m²", score: 49, tone: "warn" },
];

const FEATURES = [
  { icon: "🎯", title: "Xếp hạng mức độ quan tâm", desc: "Tổng hợp tín hiệu thị trường và xếp hạng từng căn hoặc nhóm căn theo Interest Score." },
  { icon: "📈", title: "Theo dõi hấp thụ thực tế", desc: "Theo dõi lượng bán, tồn kho và tỷ lệ hấp thụ sau khi dự án bắt đầu mở bán." },
  { icon: "💡", title: "Giải thích tín hiệu thị trường", desc: "Hiểu những tín hiệu chính đang làm mức độ quan tâm của từng căn tăng hoặc giảm." },
];

// Các bước hoạt động (section #how-it-works)
const STEPS = [
  {
    n: 1, icon: "📡", title: "Thu thập tín hiệu thị trường",
    desc: "Tổng hợp booking, đăng ký quan tâm, hành vi online, độ khan hiếm và các tín hiệu thị trường.",
  },
  {
    n: 2, icon: "⚙️", title: "Tính Interest Score",
    desc: "Chuẩn hóa các tín hiệu và áp dụng trọng số để tạo điểm quan tâm thống nhất cho từng căn hoặc nhóm căn.",
  },
  {
    n: 3, icon: "🏆", title: "Xếp hạng & giải thích",
    desc: "Xếp hạng các căn theo mức độ quan tâm và giải thích những tín hiệu chính khiến điểm cao hoặc thấp.",
  },
];

const SIGNALS = ["Booking", "Online interest", "Scarcity", "Market signals"];

export default function HomePage() {
  const navigate = useNavigate();
  const { isNarrow } = useBreakpoint();

  // "Tìm hiểu thêm": cuộn mượt xuống #how-it-works, không đổi route, không reload.
  // Tôn trọng prefers-reduced-motion; hoạt động bằng bàn phím vì là <button>.
  function scrollToHowItWorks() {
    const el = document.getElementById("how-it-works");
    if (!el) return;
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  return (
    <div style={{ minHeight: "100vh", background: color.canvas, fontFamily: font.sans }}>
      {/* topbar landing */}
      <header style={S.bar}>
        <Brand size={32} wordSize={19} withAI />
        <div style={S.barRight}>
          <button style={S.ghost} onClick={() => navigate("/login")}>Đăng nhập</button>
          <button style={S.primary} onClick={() => navigate("/register")}>Đăng ký</button>
        </div>
      </header>

      {/* hero */}
      <section style={{ ...S.hero, gridTemplateColumns: isNarrow ? "1fr" : "1.05fr .95fr" }}>
        <div>
          <span style={S.eyebrow}>Xếp hạng mức độ quan tâm · Theo dữ liệu thị trường</span>
          <h1 style={S.h1}>Đo lường nhu cầu.<br />Xếp hạng mức độ quan tâm.</h1>
          <p style={S.lede}>
            AbsorbIQ tổng hợp booking, đăng ký quan tâm, hành vi online và các tín hiệu
            thị trường để tính <b style={{ color: color.ink }}>Interest Score</b> và{" "}
            <b style={{ color: color.ink }}>xếp hạng</b> từng căn hộ hoặc nhóm căn — giúp đội ngũ
            kinh doanh hiểu <b style={{ color: color.ink }}>nhu cầu thị trường</b> trước và trong
            quá trình mở bán.
          </p>
          <div style={S.ctaRow}>
            <button style={{ ...S.primary, padding: "14px 30px", fontSize: size.small }} onClick={() => navigate("/login")}>Đăng nhập</button>
            <button style={{ ...S.ghost, border: `1px solid ${color.borderStrong}`, padding: "14px 24px", fontSize: size.small }} onClick={scrollToHowItWorks}>Tìm hiểu thêm</button>
          </div>
          <div style={S.trust}>
            <span style={S.dot} /> Xây dựng cho đội ngũ kinh doanh &amp; ban lãnh đạo
          </div>
        </div>

        {/* thẻ xếp hạng minh hoạ (Interest Score) */}
        {!isNarrow && (
          <div style={{ position: "relative" }}>
            <div style={S.rankCard}>
              <div style={S.rankHead}>
                <span style={S.rankTitle}>Bảng xếp hạng mức độ quan tâm</span>
                <span style={S.demoTag}>Demo data</span>
              </div>
              <div style={S.rankScaleRow}>
                <span style={S.rankScaleLabel}>Interest Score</span>
                <span style={S.rankScaleUnit}>/ 100</span>
              </div>
              {DEMO_RANK.map((r, i) => {
                const c = r.tone === "ok" ? color.ok : color.warn;
                return (
                  <div key={r.unit} style={{ ...S.rankRow, borderBottom: i < DEMO_RANK.length - 1 ? `1px solid ${color.border}` : "none" }}>
                    <span style={S.rankNum}>{i + 1}</span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={S.unit}>Căn {r.unit}</span>
                      <span style={S.spec}>{r.spec}</span>
                    </span>
                    <span style={S.track}><span style={{ ...S.fill, width: `${r.score}%`, background: c }} /></span>
                    <span style={{ ...S.score, color: c }}>{r.score}<span style={S.scoreMax}>/100</span></span>
                  </div>
                );
              })}
            </div>
            <div style={S.floatCard}>
              <div style={S.floatLabel}>Mức quan tâm cao nhất</div>
              <div style={S.floatVal}>A-12.05 · <span style={{ color: color.ok }}>92/100</span></div>
            </div>
          </div>
        )}
      </section>

      {/* features */}
      <section style={{ ...S.features, gridTemplateColumns: isNarrow ? "1fr" : "repeat(3, 1fr)" }}>
        {FEATURES.map((f) => (
          <div key={f.title} style={S.featCard}>
            <div style={{ fontSize: 22 }}>{f.icon}</div>
            <div style={S.featTitle}>{f.title}</div>
            <div style={S.featDesc}>{f.desc}</div>
          </div>
        ))}
      </section>

      {/* ============ SECTION: AbsorbIQ hoạt động như thế nào? ============ */}
      <section id="how-it-works" style={S.hiw}>
        <div style={S.hiwHead}>
          <h2 style={S.hiwTitle}>AbsorbIQ hoạt động như thế nào?</h2>
          <p style={S.hiwSub}>Từ tín hiệu thị trường đến bảng xếp hạng dễ hiểu trong 3 bước.</p>
        </div>

        <div style={{ ...S.steps, gridTemplateColumns: isNarrow ? "1fr" : "repeat(3, 1fr)" }}>
          {STEPS.map((s) => (
            <div key={s.n} style={S.stepCard}>
              <div style={S.stepTop}>
                <span style={S.stepBadge}>{s.n}</span>
                <span style={{ fontSize: 20 }}>{s.icon}</span>
              </div>
              <div style={S.stepTitle}>{s.title}</div>
              <div style={S.stepDesc}>{s.desc}</div>

              {/* Bước 2: minh hoạ tín hiệu -> Interest Score */}
              {s.n === 2 && (
                <div style={S.miniFlow}>
                  {SIGNALS.map((sig) => <span key={sig} style={S.signalChip}>{sig}</span>)}
                  <span style={S.flowArrow}>↓</span>
                  <span style={S.scorePill}>Interest Score</span>
                </div>
              )}

              {/* Bước 3: ví dụ nhỏ */}
              {s.n === 3 && (
                <div style={S.example}>
                  <div style={S.exUnit}>Căn A-12.05</div>
                  <div style={S.exScore}>Interest Score: <b>92/100</b></div>
                  <div style={S.exKeyLabel}>Tín hiệu chính</div>
                  <ul style={S.exList}>
                    <li>Booking cao</li>
                    <li>Online interest cao</li>
                    <li>Nguồn cung căn tương tự thấp</li>
                  </ul>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* mini flow tổng: tín hiệu -> score -> ranking -> giải thích */}
        <div style={S.pipeline}>
          {["Tín hiệu thị trường", "Interest Score", "Xếp hạng", "Giải thích"].map((step, i, arr) => (
            <React.Fragment key={step}>
              <span style={{ ...S.pipeNode, ...(i === arr.length - 1 ? S.pipeNodeLast : null) }}>{step}</span>
              {i < arr.length - 1 && <span style={S.pipeArrow}>→</span>}
            </React.Fragment>
          ))}
        </div>
      </section>

      <footer style={S.footer}>© 2026 AbsorbIQ AI · VinUni × Vingroup</footer>
    </div>
  );
}

const S = {
  bar: { height: 64, display: "flex", alignItems: "center", padding: "0 32px", background: color.surface, borderBottom: `1px solid ${color.border}` },
  barRight: { marginLeft: "auto", display: "flex", gap: 10 },
  ghost: { background: "transparent", border: "none", color: color.body, fontSize: size.small, fontWeight: 600, padding: "9px 18px", borderRadius: radius.sm, cursor: "pointer", fontFamily: "inherit" },
  primary: { background: color.accent, border: "none", color: "#fff", fontSize: size.small, fontWeight: 600, padding: "9px 18px", borderRadius: radius.sm, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(91,82,230,.28)" },

  hero: { maxWidth: 1180, margin: "0 auto", display: "grid", gap: 40, alignItems: "center", padding: "52px 44px", background: "radial-gradient(900px 520px at 88% -8%, #efeefe, transparent)" },
  eyebrow: { display: "inline-block", fontSize: size.tiny, fontWeight: 700, color: color.accent, background: color.accentSoft, borderRadius: radius.pill, padding: "4px 11px", marginBottom: 18 },
  h1: { fontFamily: font.display, fontSize: 44, fontWeight: 700, color: color.ink, letterSpacing: "-.025em", lineHeight: 1.12, margin: 0 },
  lede: { fontSize: 16.5, color: color.body, margin: "20px 0 28px", maxWidth: "48ch", lineHeight: 1.65 },
  ctaRow: { display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" },
  trust: { display: "flex", alignItems: "center", gap: 8, marginTop: 22, fontSize: size.small, color: color.muted },
  dot: { width: 6, height: 6, borderRadius: "50%", background: color.ok, display: "inline-block" },

  rankCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: 20, boxShadow: "0 22px 54px rgba(91,82,230,.18)" },
  rankHead: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 },
  rankTitle: { fontFamily: font.display, fontSize: size.small, fontWeight: 700, color: color.ink },
  demoTag: { fontSize: 10.5, fontWeight: 700, color: color.muted, background: color.canvas, border: `1px solid ${color.border}`, borderRadius: radius.pill, padding: "2px 8px" },
  rankScaleRow: { display: "flex", alignItems: "baseline", gap: 5, marginBottom: 10 },
  rankScaleLabel: { fontSize: 11, fontWeight: 700, color: color.accent, textTransform: "uppercase", letterSpacing: ".05em" },
  rankScaleUnit: { fontSize: 11, color: color.muted },
  rankRow: { display: "flex", alignItems: "center", gap: 12, padding: "9px 0" },
  rankNum: { width: 22, height: 22, borderRadius: 6, background: color.canvas, color: color.muted, fontSize: size.tiny, fontWeight: 700, display: "grid", placeItems: "center", flex: "none" },
  unit: { display: "block", fontSize: 13.5, fontWeight: 600, color: color.ink },
  spec: { display: "block", fontSize: 11.5, color: color.muted },
  track: { width: 60, height: 6, background: color.canvas, borderRadius: radius.pill, overflow: "hidden" },
  fill: { display: "block", height: "100%", borderRadius: radius.pill },
  score: { fontSize: 13.5, fontWeight: 700, minWidth: 52, textAlign: "right", fontVariantNumeric: "tabular-nums" },
  scoreMax: { fontSize: 10.5, fontWeight: 500, color: color.muted },
  floatCard: { position: "absolute", bottom: -20, left: -20, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "11px 15px", boxShadow: "0 14px 32px rgba(26,26,46,.14)" },
  floatLabel: { fontSize: 11, color: color.muted },
  floatVal: { fontFamily: font.display, fontSize: 15, fontWeight: 700, color: color.ink },

  features: { maxWidth: 1180, margin: "0 auto", display: "grid", gap: 16, padding: "8px 44px 40px" },
  featCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: 22, boxShadow: shadow },
  featTitle: { fontFamily: font.display, fontSize: 16, fontWeight: 700, color: color.ink, margin: "10px 0 6px" },
  featDesc: { fontSize: 13, color: color.muted, lineHeight: 1.6 },

  // section how-it-works
  hiw: { maxWidth: 1180, margin: "0 auto", padding: "28px 44px 56px", scrollMarginTop: 80 },
  hiwHead: { textAlign: "center", marginBottom: 28 },
  hiwTitle: { fontFamily: font.display, fontSize: 28, fontWeight: 700, color: color.ink, letterSpacing: "-.02em", margin: 0 },
  hiwSub: { fontSize: 15, color: color.muted, margin: "8px 0 0" },
  steps: { display: "grid", gap: 16, marginBottom: 28 },
  stepCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: 22, boxShadow: shadow },
  stepTop: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  stepBadge: { width: 28, height: 28, borderRadius: "50%", background: color.accent, color: "#fff", fontFamily: font.display, fontSize: 14, fontWeight: 700, display: "grid", placeItems: "center" },
  stepTitle: { fontFamily: font.display, fontSize: 16.5, fontWeight: 700, color: color.ink, marginBottom: 6 },
  stepDesc: { fontSize: 13.5, color: color.body, lineHeight: 1.6 },

  miniFlow: { marginTop: 16, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 6, background: color.canvas, borderRadius: radius.sm, padding: 14 },
  signalChip: { fontSize: 12, fontWeight: 600, color: color.accent, background: color.accentSoft, borderRadius: radius.pill, padding: "3px 10px" },
  flowArrow: { color: color.muted, fontSize: 14, alignSelf: "center", margin: "2px 0" },
  scorePill: { fontSize: 12.5, fontWeight: 700, color: "#fff", background: color.accent, borderRadius: radius.pill, padding: "5px 14px", alignSelf: "center" },

  example: { marginTop: 16, background: color.canvas, borderRadius: radius.sm, padding: 14 },
  exUnit: { fontFamily: font.display, fontSize: 14, fontWeight: 700, color: color.ink },
  exScore: { fontSize: 13, color: color.body, margin: "3px 0 10px" },
  exKeyLabel: { fontSize: 11, fontWeight: 700, color: color.muted, textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 },
  exList: { margin: 0, paddingLeft: 18, fontSize: 12.5, color: color.body, lineHeight: 1.7 },

  pipeline: { display: "flex", alignItems: "center", justifyContent: "center", gap: 10, flexWrap: "wrap", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "16px 20px", boxShadow: shadow },
  pipeNode: { fontSize: 13, fontWeight: 600, color: color.body, background: color.canvas, borderRadius: radius.pill, padding: "7px 16px" },
  pipeNodeLast: { color: color.accent, background: color.accentSoft, fontWeight: 700 },
  pipeArrow: { color: color.muted, fontSize: 16 },

  footer: { textAlign: "center", padding: "24px", fontSize: size.tiny, color: color.muted, borderTop: `1px solid ${color.border}` },
};