import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Brand from "../components/Brand";
import BackgroundCarousel from "../components/ui/BackgroundCarousel";
import Icon from "../components/ui/Icon";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { font } from "../styles/tokens";
import {
  INSIGHT_SERIES,
  METRICS,
  NAV_ITEMS,
  PLATFORM_FEATURES,
  REGION_BARS,
  WORKFLOW,
} from "./homepageData";

const P = {
  navy: "#142033",
  navySoft: "#EEF1F0",
  panel: "rgba(255,255,255,.92)",
  white: "#FFFFFF",
  muted: "#5C6B7C",
  gold: "#E9C83E",
  green: "#45D19A",
  border: "rgba(20,32,51,.14)",
  cream: "#F8F8F5",
  ink: "#142033",
  inkMuted: "#5E6A78",
  lightBorder: "#E5E7EB",
};

const tooltipStyle = {
  background: "#132236",
  border: `1px solid ${P.border}`,
  borderRadius: 10,
  color: P.white,
  fontSize: 12,
};

export default function HomePage() {
  const navigate = useNavigate();
  const { isNarrow, isMobile } = useBreakpoint();
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [chartMode, setChartMode] = useState("both");

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  function scrollTo(href) {
    setMenuOpen(false);
    const target = document.querySelector(href);
    if (!target) return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }

  return (
    <div className="home-shell" style={S.shell}>
      <style>{CSS}</style>
      <header style={{ ...S.nav, ...(scrolled ? S.navScrolled : null) }}>
        <a href="#overview" aria-label="Trang chủ AbsorpIQ" style={S.brandLink}>
          <Brand size={30} wordSize={18} />
        </a>
        <nav aria-label="Điều hướng chính" style={{ ...S.desktopNav, ...(isNarrow ? S.hidden : null) }}>
          {NAV_ITEMS.map((item) => (
            <a className="home-nav-link" key={item.href} href={item.href} onClick={(event) => { event.preventDefault(); scrollTo(item.href); }}>
              {item.label}
            </a>
          ))}
        </nav>
        <div style={{ ...S.navActions, ...(isNarrow ? S.hidden : null) }}>
          <span style={S.language}>VI</span>
          <button className="home-text-button" type="button" onClick={() => navigate("/login")}>Đăng nhập</button>
          <button className="home-gold-button" type="button" onClick={() => scrollTo("#contact")}>Yêu cầu bản demo</button>
        </div>
        {isNarrow && (
          <button className="home-menu-button" type="button" aria-label={menuOpen ? "Đóng menu" : "Mở menu"} aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
            <span /><span /><span />
          </button>
        )}
      </header>
      {isNarrow && menuOpen && (
        <nav aria-label="Điều hướng trên thiết bị di động" style={S.mobileMenu}>
          {NAV_ITEMS.map((item) => <a key={item.href} href={item.href} onClick={(event) => { event.preventDefault(); scrollTo(item.href); }}>{item.label}</a>)}
          <button className="home-text-button" type="button" onClick={() => navigate("/login")}>Đăng nhập</button>
          <button className="home-gold-button" type="button" onClick={() => scrollTo("#contact")}>Yêu cầu bản demo</button>
        </nav>
      )}

      <main>
        <section id="overview" style={S.hero}>
          <BackgroundCarousel label="Hình nền giới thiệu AbsorpIQ" />
          <div style={{ ...S.heroOverlay, ...(isNarrow ? S.heroOverlayNarrow : null) }} />
          <div style={{ ...S.heroInner, ...(isNarrow ? S.heroInnerNarrow : null) }}>
            <div style={{ ...S.heroCopy, marginLeft: "80px", ...(isNarrow ? S.heroCopyNarrow : null) }}>
              <div style={S.eyebrow}>NẠP DỮ LIỆU VÀO MỘT GÓC NHÌN</div>
              <h1 style={S.heroTitle}>Tập trung dữ liệu bán hàng.<br />Nhìn rõ nhịp độ hấp thụ.</h1>
              <p style={S.heroLead}>AbsorpIQ kết nối trực tiếp với CRM của doanh nghiệp, tự động đồng bộ liên tục dữ liệu tồn kho và giao dịch.
                Mọi thay đổi trên CRM đều được cập nhật ngay lập tức vào dashboard và mô hình dự báo, giúp đội ngũ kinh doanh luôn ra quyết định trên số liệu mới nhất.</p>
              <div style={S.heroCtas}>
                <button className="home-gold-button" type="button" onClick={() => scrollTo("#platform")}>Khám phá luồng dữ liệu <span>↗</span></button>
                <button className="home-outline-button" type="button" onClick={() => scrollTo("#insights")}>Xem dữ liệu mẫu</button>
              </div>
              <div style={S.trustRow}>
                <span><i />Dữ liệu theo phạm vi</span>
                <span><i />Chỉ báo hấp thụ</span>
                <span><i />Đồng bộ có kiểm soát</span>
              </div>
            </div>
            <PreviewCard isMobile={isMobile} onConnect={() => navigate("/import")} />
          </div>
        </section>

        <section id="platform" style={S.lightSection}>
          <SectionIntro eyebrow="MỘT GÓC NHÌN. QUYẾT ĐỊNH CÓ CƠ SỞ." title="Từ dữ liệu phân mảnh đến góc nhìn rõ ràng." copy="AbsorpIQ đưa các chỉ báo dự án và hấp thụ về cùng một nơi để đội ngũ kiểm tra dữ liệu trước khi hành động." />
          <div style={{ ...S.featureGrid, ...(isNarrow ? S.featureGridNarrow : null) }}>
            {PLATFORM_FEATURES.map((feature) => <FeatureCard key={feature.title} {...feature} />)}
          </div>
        </section>

        <section id="insights" style={S.insightSection}>
          <BackgroundCarousel label="Hình nền phân tích thị trường" />
          <div style={{ ...S.insightOverlay, ...(isNarrow ? S.insightOverlayNarrow : null) }} />
          <div style={{ ...S.insightLayout, ...(isNarrow ? S.insightLayoutNarrow : null) }}>
            <div style={{
              ...(isNarrow ? S.insightCopyNarrow : S.insightCopy),
              marginLeft: isNarrow ? "0" : "80px",
            }}>
              <h2 style={S.darkTitle}>Xem dữ liệu hiện có trước khi quyết định bước tiếp theo.</h2>
              <p style={S.darkCopy}>Xem tồn kho, giao dịch lịch sử và vận tốc hấp thụ trong một góc nhìn rõ ràng. Các số liệu tại đây là minh họa, không đại diện cho dữ liệu dự án thật.</p>
              <button className="home-outline-button" type="button" onClick={() => navigate("/projects")}>Khám phá dự án <span>↗</span></button>
              <p style={S.disclaimer}>Dữ liệu minh họa cho bản demo. Không hiển thị dữ liệu dự án thực tế.</p>
            </div>
            <InsightPanel chartMode={chartMode} setChartMode={setChartMode} isNarrow={isNarrow} />
          </div>
        </section>

        <section id="methodology" style={S.lightSection}>
          <SectionIntro eyebrow="CÁCH XEM DỮ LIỆU" title="Từ dữ liệu được nạp đến quyết định có cơ sở." copy="Một nhịp làm việc rõ ràng cho đội ngũ kinh doanh cần biết dữ liệu nào đã có và dữ liệu nào còn thiếu." />
          <div style={{ ...S.workflow, ...(isNarrow ? S.workflowNarrow : null) }}>
            {WORKFLOW.map((step) => <WorkflowStep key={step.number} {...step} />)}
          </div>
        </section>

        <section id="resources" style={S.metricSection}>
          <div style={S.metricKicker}>ABSORPIQ QUA PHẠM VI HIỂN THỊ <span>· BẢN DEMO MINH HỌA</span></div>
          <div style={{ ...S.metrics, ...(isNarrow ? S.metricsNarrow : null) }}>
            {METRICS.map((metric) => <div key={metric.label}><strong>{metric.value}</strong><span>{metric.label}</span></div>)}
          </div>
        </section>

        <section id="contact" style={S.ctaSection}>
          <div style={S.ctaOrb} />
          <div style={S.ctaContent}><div style={S.eyebrow}>SẴN SÀNG XEM DỮ LIỆU CỦA BẠN?</div><h2 style={S.ctaTitle}>Bắt đầu bằng<br />góc nhìn rõ hơn.</h2><p style={S.ctaCopy}>Xem phạm vi dữ liệu hiện có và những gì còn chưa được kết nối.</p><div style={S.heroCtas}><button className="home-gold-button" type="button" onClick={() => navigate("/register")}>Yêu cầu bản demo <span>↗</span></button><button className="home-light-button" type="button" onClick={() => scrollTo("#methodology")}>Cách thức hoạt động</button></div></div>
        </section>
      </main>

      <footer style={S.footer}>
        <div style={S.footerTop}><div><Brand size={28} wordSize={17} light /><p style={S.footerCopy}>Góc nhìn hấp thụ rõ hơn cho các đội ngũ bất động sản.</p></div><div style={S.footerLinks}><div><b>Nền tảng</b><a href="#platform">Tổng quan</a><a href="#insights">Phân tích</a><a href="#methodology">Phương pháp</a></div><div><b>Tài nguyên</b><a href="#resources">Phạm vi hiển thị</a><a href="#contact">Yêu cầu bản demo</a><a href="/login">Đăng nhập</a></div><div><b>Pháp lý</b><a href="#privacy">Chính sách riêng tư</a><a href="#terms">Điều khoản</a></div></div></div>
        <div style={S.footerBottom}><span>© 2026 AbsorpIQ. Bảo lưu mọi quyền.</span><span>Ra quyết định dựa trên dữ liệu hiện có.</span></div>
      </footer>
    </div>
  );
}

function Filter({ label, value, onChange, options, labels = options }) {
  return <label style={S.filterField}><span>{label}</span><select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option, index) => <option key={option} value={option}>{labels[index]}</option>)}</select></label>;
}

function PreviewCard({ isMobile, onConnect }) {
  return (
    <div style={{ ...S.previewWrap, ...(isMobile ? S.previewWrapMobile : null) }}>
      <article aria-label="Tích hợp CRM" style={S.previewCard}>
        <div style={S.crmCardHeader}>
          <div style={S.crmCardLabel}>
            <span style={{ ...S.crmIcon, fontSize: '20px' }}>
              <Icon name="database" size={22} color={P.ink} />
            </span>
            Đồng bộ dữ liệu từ CRM theo chu kỳ
          </div>
        </div>
        <h3 style={{ ...S.crmTitle, fontSize: '24px', lineHeight: 1.25 }}>Kết nối dữ liệu bán hàng để theo dõi dự án chính xác hơn</h3>
        <p style={{ ...S.crmCopy, fontSize: '16px', lineHeight: 1.6 }}>
          Đồng bộ dự án, phân khu, căn hộ và giao dịch từ CRM của doanh nghiệp.
          Dữ liệu sau khi đồng bộ được dùng để cập nhật tồn kho, tốc độ bán và các chỉ số phân tích trên dashboard.
        </p>
        <p style={{ ...S.crmAvailability, fontSize: '15px' }}>Kết nối CRM để bắt đầu xem số liệu thực tế.</p>
      </article>
    </div>
  );
}

function Kpi({ value, label, tone }) { return <div><strong style={{ color: tone }}>{value}</strong><span>{label}</span></div>; }

function SectionIntro({ eyebrow, title, copy }) { return <div style={S.sectionIntro}><div style={S.lightEyebrow}>{eyebrow}</div><h2 style={S.lightTitle}>{title}</h2><p style={S.lightCopy}>{copy}</p></div>; }

function FeatureCard({ icon, number, title, description }) { return <article className="home-feature-card" style={S.featureCard}><div style={S.featureTop}><span style={S.featureIcon}><Icon name={icon} size={21} color={P.ink} /></span><span style={S.featureNumber}>{number}</span></div><h3 style={S.featureTitle}>{title}</h3><p style={S.featureCopy}>{description}</p><span className="home-card-arrow" style={S.cardArrow}>↗</span></article>; }

function InsightPanel({ chartMode, setChartMode, isNarrow }) {
  return <div style={S.insightPanel} aria-label="Biểu đồ xu hướng hấp thụ minh họa"><div style={S.panelHeader}><div><span style={S.panelEyebrow}>RIVERSTONE RESIDENCES · BẢN DEMO</span><h3 style={S.panelTitle}>Xu hướng hấp thụ minh họa</h3></div><span style={S.livePill}><i />Minh họa</span></div><div style={{ ...S.panelKpis, ...(isNarrow ? S.panelKpisNarrow : null) }}><Kpi value="412" label="Căn còn lại · minh họa" tone={P.gold} /><Kpi value="68,4%" label="Tỷ lệ hấp thụ · minh họa" tone={P.green} /><Kpi value="—" label="Giá bán · chưa có" tone={P.gold} /><Kpi value="—" label="Dự báo · chưa có" tone={P.green} /></div><div className="chart-toolbar" style={S.chartToolbar}><span>Tỷ lệ hấp thụ · minh họa 12 tháng</span><div>{[{ value: "both", label: "Cả hai" }, { value: "actual", label: "Đã ghi nhận" }, { value: "forecast", label: "Tham chiếu minh họa" }].map(({ value, label }) => <button className="chart-toggle" type="button" key={value} aria-pressed={chartMode === value} onClick={() => setChartMode(value)}>{label}</button>)}</div></div><div style={S.chart} role="img" aria-label="Tỷ lệ hấp thụ minh họa tăng từ 18 đến 82 phần trăm trong mười hai tháng; không phải dự báo"><ResponsiveContainer width="100%" height="100%"><AreaChart data={INSIGHT_SERIES} margin={{ top: 8, right: 4, left: -24, bottom: 0 }}><defs><linearGradient id="actualFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={P.gold} stopOpacity={0.3} /><stop offset="100%" stopColor={P.gold} stopOpacity={0} /></linearGradient><linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={P.green} stopOpacity={0.22} /><stop offset="100%" stopColor={P.green} stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke={P.border} vertical={false} /><XAxis dataKey="month" tick={{ fill: P.muted, fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis domain={[0, 100]} tick={{ fill: P.muted, fontSize: 10 }} axisLine={false} tickLine={false} /><Tooltip contentStyle={tooltipStyle} /><Area type="monotone" dataKey="actual" stroke={P.gold} fill="url(#actualFill)" strokeWidth={2} connectNulls={false} hide={chartMode === "forecast"} name="Đã ghi nhận" /><Area type="monotone" dataKey="forecast" stroke={P.green} fill="url(#forecastFill)" strokeWidth={2} strokeDasharray="5 4" hide={chartMode === "actual"} name="Tham chiếu minh họa" /></AreaChart></ResponsiveContainer></div><div style={S.regionHeading}>Nhóm tham chiếu minh họa <span>không phải chỉ số so sánh</span></div><div style={S.regionRows}>{REGION_BARS.map((item) => <div key={item.label} style={S.regionRow}><span>{item.label}</span><div style={S.regionTrack}><span style={{ ...S.regionFill, width: `${item.value}%` }} /></div><b>{item.value}</b></div>)}</div><div style={S.panelLegend}><span><i style={{ background: P.gold }} />Đã ghi nhận</span><span><i style={{ background: P.green }} />Minh họa</span><span style={S.demoNote}>Không phải dữ liệu thực tế</span></div></div>;
}

function WorkflowStep({ number, title, description }) { return <article style={S.workflowStep}><span style={S.workflowNumber}>{number}</span><h3 style={S.workflowTitle}>{title}</h3><p style={S.workflowCopy}>{description}</p></article>; }

const CSS = `
  .home-shell button, .home-shell a, .home-shell select { font: inherit; }
  .home-shell button { cursor: pointer; }
  .home-gold-button, .home-outline-button, .home-light-button { display: inline-flex; align-items: center; gap: 10px; border-radius: 7px; padding: 12px 16px; font-size: 12px; font-weight: 600; }
  .home-gold-button { border: 1px solid ${P.gold}; background: ${P.gold}; color: ${P.ink}; }
  .home-outline-button { border: 1px solid rgba(255,255,255,.62); background: transparent; color: ${P.white}; }
  .home-light-button { border: 1px solid rgba(16,32,51,.35); background: transparent; color: ${P.ink}; }
  .home-text-button { border: 0; background: transparent; color: ${P.inkMuted}; padding: 7px 2px; }
  .home-menu-button { display: grid; gap: 4px; margin-left: auto; padding: 8px; border: 0; background: transparent; }
  .home-menu-button span { display: block; width: 21px; height: 1px; background: ${P.ink}; }
  .home-shell select { min-width: 0; border: 0; background: transparent; color: ${P.ink}; font-size: 12px; }
  .home-shell option { color: ${P.ink}; }
  .panelKpis strong { display: block; font-family: ${font.display}; font-size: 21px; font-weight: 500; }
  .panelKpis span { display: block; color: ${P.muted}; font-size: 10px; margin-top: 4px; }
  .livePill i, .trustRow i, .panelLegend i { display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: ${P.green}; }
  .heroTitle em { color: ${P.gold}; font-style: normal; }
  .home-shell a { color: inherit; text-decoration: none; }
  .home-shell button:focus-visible, .home-shell a:focus-visible, .home-shell select:focus-visible { outline: 3px solid ${P.gold}; outline-offset: 3px; }
  .background-carousel { position: absolute; inset: 0; overflow: hidden; background: ${P.cream}; }
  .background-carousel-fallback, .background-carousel-image, .background-carousel-overlay { position: absolute; inset: 0; width: 100%; height: 100%; }
  .background-carousel-fallback { background: linear-gradient(135deg, #F8F8F5 0%, #EEF1F0 52%, #F8F8F5 100%); }
  .background-carousel-image { object-fit: cover; opacity: 0; transition: opacity 800ms ease; }
  .background-carousel-image.is-active { opacity: 1; }
  .background-carousel-overlay { background: transparent; pointer-events: none; }
  .background-carousel-controls { position: absolute; z-index: 3; inset: 0; display: flex; align-items: center; justify-content: space-between; padding: 0 22px; pointer-events: none; }
  .background-carousel-controls > button { display: grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 1px solid rgba(255,255,255,.72); border-radius: 50%; background: rgba(20,32,51,.52); color: ${P.white}; font-size: 24px; line-height: 1; pointer-events: auto; }
  .background-carousel-controls > button:hover { border-color: ${P.gold}; color: ${P.gold}; }
  .background-carousel-dots { position: absolute; left: 50%; bottom: 25px; display: flex; gap: 8px; transform: translateX(-50%); pointer-events: auto; }
  .background-carousel-dots button { width: 24px; height: 4px; padding: 0; border: 1px solid rgba(255,255,255,.72); border-radius: 4px; background: rgba(20,32,51,.48); }
  .background-carousel-dots button.is-active { background: ${P.gold}; }
  .home-nav-link, .home-text-button, .home-gold-button, .home-outline-button, .home-light-button, .home-menu-button, .chart-toggle, .home-feature-card { transition: transform .2s ease, background .2s ease, border-color .2s ease, color .2s ease; }
  .home-nav-link:hover { color: ${P.ink} !important; }
  .home-text-button:hover { color: ${P.gold} !important; }
  .home-gold-button:hover { transform: translateY(-2px); background: #f4d75c !important; }
  .home-outline-button:hover, .home-light-button:hover { border-color: ${P.gold} !important; color: ${P.gold} !important; }
  .home-feature-card:hover { transform: translateY(-5px); border-color: ${P.gold} !important; box-shadow: 0 18px 44px rgba(16,32,51,.12); }
  .home-card-arrow { transition: transform .2s ease; }
  .home-feature-card:hover .home-card-arrow { transform: translate(3px, -3px); }
  @media (max-width: 639px) { .chart-toolbar { flex-wrap: wrap; gap: 8px; } .chart-toolbar > div { margin-left: auto; } }
  .chart-toggle:hover { color: ${P.ink} !important; border-color: ${P.gold} !important; }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
`;

const S = {
  shell: { minHeight: "100vh", background: P.cream, color: P.ink, fontFamily: font.sans },
  hidden: { display: "none" },
  nav: { position: "fixed", zIndex: 20, top: 16, left: "50%", transform: "translateX(-50%)", width: "min(1180px, calc(100% - 36px))", minHeight: 66, display: "flex", alignItems: "center", padding: "0 20px", border: `1px solid ${P.border}`, borderRadius: 14, background: "rgba(255,255,255,.82)", boxShadow: "0 10px 30px rgba(20,32,51,.08)", transition: "background .2s ease, box-shadow .2s ease" },
  navScrolled: { background: "rgba(255,255,255,.96)", boxShadow: "0 12px 34px rgba(20,32,51,.12)" },
  brandLink: { display: "inline-flex" },
  desktopNav: { display: "flex", gap: 24, marginLeft: 46, color: P.muted, fontSize: 12.5 },
  navActions: { display: "flex", alignItems: "center", gap: 15, marginLeft: "auto", fontSize: 12.5 },
  language: { color: P.muted, fontFamily: font.mono, fontSize: 11, paddingRight: 5, borderRight: `1px solid ${P.border}` },
  mobileMenu: { position: "fixed", zIndex: 19, top: 91, left: 18, right: 18, display: "grid", gap: 17, padding: 22, border: `1px solid ${P.border}`, borderRadius: 14, background: "rgba(255,255,255,.98)", color: P.ink, boxShadow: "0 16px 36px rgba(20,32,51,.14)" },
  menuLink: { color: P.muted },
  hero: { position: "relative", minHeight: 790, overflow: "hidden", display: "flex", alignItems: "center", padding: "130px max(24px, calc((100% - 1180px) / 2)) 148px", color: P.white },
  heroOverlay: { position: "absolute", zIndex: 0, top: 0, bottom: 0, left: "max(24px, calc((100% - 1180px) / 2))", width: "42vw", maxWidth: "42vw", background: "linear-gradient(90deg, rgba(8,20,35,.52) 0%, rgba(8,20,35,.22) 55%, rgba(8,20,35,0) 100%)", pointerEvents: "none" },
  heroOverlayNarrow: { display: "none" },
  heroInner: { position: "relative", zIndex: 1, width: "100%", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(360px, .78fr)", gap: 60, alignItems: "center" },
  heroInnerNarrow: { gridTemplateColumns: "1fr", paddingTop: 35, gap: 42 },
  heroCopy: { maxWidth: 650 },
  heroCopyNarrow: { padding: 18, borderRadius: 14, background: "rgba(8,20,35,.42)" },
  eyebrow: { color: P.gold, fontFamily: font.mono, fontSize: 10.5, letterSpacing: ".16em", fontWeight: 600 },
  heroTitle: { margin: "18px 0 22px", fontFamily: font.display, fontSize: "clamp(42px, 5vw, 73px)", lineHeight: 1.02, letterSpacing: "-.055em", fontWeight: 500 },
  heroLead: { maxWidth: 570, color: "rgba(255,255,255,.88)", fontSize: 16, lineHeight: 1.7, margin: 0, textShadow: "0 1px 14px rgba(8,20,35,.18)" },
  heroCtas: { display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", marginTop: 32 },
  trustRow: { display: "flex", flexWrap: "wrap", gap: "10px 24px", color: P.muted, fontSize: 11.5, marginTop: 40 },
  previewWrap: { justifySelf: "end", width: "min(100%, 430px)" },
  previewWrapMobile: { justifySelf: "stretch", width: "100%" },
  previewCard: { padding: 24, border: `1px solid ${P.border}`, borderRadius: 16, background: "rgba(255,255,255,.9)", color: P.ink, boxShadow: "0 20px 56px rgba(20,32,51,.16)" },
  crmCardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 },
  crmCardLabel: { display: "inline-flex", alignItems: "center", gap: 9 },
  crmIcon: { width: 32, height: 32, display: "grid", placeItems: "center", borderRadius: 9, background: P.navySoft, color: P.ink },
  previewLabel: { color: P.muted, fontFamily: font.mono, fontSize: 9.5, letterSpacing: ".13em" },
  livePill: { color: P.green, fontSize: 10.5, display: "inline-flex", alignItems: "center", gap: 6 },
  crmCycle: { maxWidth: 150, color: P.muted, fontSize: 10, lineHeight: 1.35, textAlign: "right" },
  crmTitle: { fontFamily: font.display, fontSize: 23, lineHeight: 1.12, fontWeight: 500, margin: "20px 0 12px" },
  crmCopy: { color: P.muted, fontSize: 13, lineHeight: 1.6, margin: 0 },
  crmFlow: { display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", margin: "20px 0", padding: "14px 0", borderTop: `1px solid ${P.border}`, borderBottom: `1px solid ${P.border}` },
  crmFlowItem: { padding: "6px 8px", borderRadius: 7, background: P.navySoft, color: P.ink, fontSize: 10.5, fontWeight: 600 },
  crmFlowArrow: { color: P.muted, fontSize: 14 },
  crmAvailability: { color: P.inkMuted, fontSize: 11.5, lineHeight: 1.5, margin: "0 0 16px" },
  filterPanel: { position: "absolute", zIndex: 2, left: "50%", bottom: 38, transform: "translateX(-50%)", width: "min(1080px, calc(100% - 48px))", display: "grid", gridTemplateColumns: "1fr 1.4fr 1fr auto", gap: 1, padding: 7, border: `1px solid ${P.border}`, borderRadius: 12, background: "rgba(255,255,255,.9)", color: P.ink, boxShadow: "0 16px 40px rgba(20,32,51,.12)" },
  filterPanelNarrow: { position: "relative", left: "auto", bottom: "auto", transform: "none", width: "calc(100% - 48px)", margin: "34px auto 0", gridTemplateColumns: "1fr", gap: 8 },
  filterField: { display: "grid", gap: 5, padding: "8px 14px", borderRight: `1px solid ${P.border}`, color: P.inkMuted, fontSize: 10 },
  analyzeButton: { borderRadius: 8, margin: 2, whiteSpace: "nowrap" },
  lightSection: { background: P.cream, color: P.ink, padding: "112px max(24px, calc((100% - 1080px) / 2))" },
  sectionIntro: { maxWidth: 680, marginBottom: 54 },
  lightEyebrow: { color: "#8F7A17", fontFamily: font.mono, fontSize: 10.5, letterSpacing: ".14em", fontWeight: 600 },
  lightTitle: { maxWidth: 680, margin: "16px 0 14px", fontFamily: font.display, fontSize: "clamp(34px, 4vw, 54px)", lineHeight: 1.04, letterSpacing: "-.05em", fontWeight: 500 },
  lightCopy: { maxWidth: 540, color: P.inkMuted, lineHeight: 1.7, fontSize: 15 },
  featureGrid: { display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 },
  featureGridNarrow: { gridTemplateColumns: "repeat(2,1fr)" },
  featureCard: { position: "relative", minHeight: 250, padding: 24, border: `1px solid ${P.lightBorder}`, borderRadius: 12, background: "rgba(255,255,255,.42)" },
  featureTop: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  featureIcon: { display: "grid", placeItems: "center", width: 42, height: 42, borderRadius: 10, background: "#E7E0C3" },
  featureNumber: { color: "#9C9B93", fontFamily: font.mono, fontSize: 11 },
  featureTitle: { margin: "44px 0 12px", fontFamily: font.display, fontSize: 20, fontWeight: 500 },
  featureCopy: { color: P.inkMuted, fontSize: 13, lineHeight: 1.65, maxWidth: 220 },
  cardArrow: { position: "absolute", right: 24, bottom: 22, color: "#8F7A17", fontSize: 20 },
  insightSection: { position: "relative", background: P.cream, color: P.ink, padding: "120px max(24px, calc((100% - 1080px) / 2))" },
  insightOverlay: { position: "absolute", zIndex: 1, top: 0, bottom: 0, left: "max(24px, calc((100% - 1080px) / 2))", width: "42vw", maxWidth: "42vw", background: "linear-gradient(90deg, rgba(8,20,35,.52) 0%, rgba(8,20,35,.22) 55%, rgba(8,20,35,0) 100%)", pointerEvents: "none" },
  insightOverlayNarrow: { display: "none" },
  insightLayout: { position: "relative", zIndex: 2, display: "grid", gridTemplateColumns: "minmax(230px,.72fr) minmax(480px,1.28fr)", gap: 70, alignItems: "center" },
  insightLayoutNarrow: { gridTemplateColumns: "1fr", gap: 45 },
  insightCopy: {},
  insightCopyNarrow: { padding: 16, borderRadius: 14, background: "rgba(8,20,35,.42)" },
  darkTitle: { margin: "17px 0", color: P.white, fontFamily: font.display, fontSize: "clamp(34px, 4vw, 52px)", lineHeight: 1.06, letterSpacing: "-.05em", fontWeight: 500, textShadow: "0 1px 14px rgba(8,20,35,.18)" },
  darkCopy: { color: "rgba(255,255,255,.88)", lineHeight: 1.7, maxWidth: 420, fontSize: 15, marginBottom: 30, textShadow: "0 1px 12px rgba(8,20,35,.18)" },
  disclaimer: { color: "rgba(255,255,255,.72)", fontSize: 10.5, marginTop: 24 },
  insightPanel: { padding: 23, border: `1px solid ${P.border}`, borderRadius: 14, background: P.panel, color: P.ink, boxShadow: "0 20px 56px rgba(20,32,51,.16)" },
  panelHeader: { display: "flex", justifyContent: "space-between", alignItems: "start" },
  panelEyebrow: { color: P.inkMuted, fontFamily: font.mono, letterSpacing: ".12em", fontSize: 9.5 },
  panelTitle: { margin: "8px 0 0", color: P.ink, fontFamily: font.display, fontSize: 22, fontWeight: 500 },
  panelKpis: { display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, margin: "26px 0 22px", paddingBottom: 18, borderBottom: `1px solid ${P.border}` },
  panelKpisNarrow: { gridTemplateColumns: "repeat(2,1fr)", gap: "16px 10px" },
  chartToolbar: { display: "flex", justifyContent: "space-between", alignItems: "center", color: P.inkMuted, fontSize: 11, marginBottom: 6 },
  chart: { width: "100%", height: 230 },
  chartToggle: { border: `1px solid ${P.border}`, borderRadius: 6, background: "rgba(255,255,255,.7)", color: P.inkMuted, fontSize: 10, padding: "4px 7px", marginLeft: 4 },
  regionHeading: { color: P.ink, fontSize: 11, margin: "18px 0 10px" },
  regionRows: { display: "grid", gap: 8 },
  regionRow: { display: "grid", gridTemplateColumns: "48px 1fr 25px", gap: 8, alignItems: "center", color: P.inkMuted, fontSize: 10 },
  regionTrack: { height: 5, background: "rgba(20,32,51,.12)", borderRadius: 8, overflow: "hidden" },
  regionFill: { display: "block", height: "100%", borderRadius: 8, background: `linear-gradient(90deg, ${P.gold}, ${P.green})` },
  panelLegend: { display: "flex", gap: 15, alignItems: "center", color: P.inkMuted, fontSize: 10, marginTop: 17 },
  demoNote: { marginLeft: "auto", color: P.inkMuted },
  workflow: { display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 0, borderTop: `1px solid ${P.lightBorder}` },
  workflowNarrow: { gridTemplateColumns: "1fr" },
  workflowStep: { minHeight: 220, padding: "28px 30px 20px 0", borderBottom: `1px solid ${P.lightBorder}`, borderRight: `1px solid ${P.lightBorder}` },
  workflowNumber: { color: "#A08A28", fontFamily: font.mono, fontSize: 11 },
  workflowTitle: { margin: "40px 0 10px", fontFamily: font.display, fontSize: 25, fontWeight: 500 },
  workflowCopy: { maxWidth: 245, color: P.inkMuted, fontSize: 13, lineHeight: 1.65, margin: 0 },
  metricSection: { background: P.navySoft, color: P.ink, padding: "65px max(24px, calc((100% - 1080px) / 2))" },
  metricKicker: { color: P.inkMuted, fontFamily: font.mono, fontSize: 10, letterSpacing: ".13em" },
  metrics: { display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 20, marginTop: 32 },
  metricsNarrow: { gridTemplateColumns: "repeat(2,1fr)", gap: "32px 20px" },
  ctaSection: { position: "relative", overflow: "hidden", textAlign: "center", background: P.gold, color: P.ink, padding: "125px 24px" },
  ctaOrb: { position: "absolute", width: 500, height: 500, borderRadius: "50%", border: "1px solid rgba(16,32,51,.14)", left: "50%", top: "50%", transform: "translate(-50%,-50%)", boxShadow: "0 0 0 60px rgba(255,255,255,.08), 0 0 0 120px rgba(255,255,255,.06)" },
  ctaContent: { position: "relative" },
  ctaTitle: { fontFamily: font.display, fontSize: "clamp(39px, 5vw, 68px)", lineHeight: 1, letterSpacing: "-.06em", fontWeight: 500, margin: "18px 0" },
  ctaCopy: { color: "rgba(16,32,51,.7)", margin: 0 },
  footer: { background: P.navy, padding: "60px max(24px, calc((100% - 1080px) / 2)) 22px" },
  footerTop: { display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 40, paddingBottom: 55 },
  footerCopy: { color: P.muted, fontSize: 12, lineHeight: 1.6, maxWidth: 220, marginTop: 18 },
  footerLinks: { display: "flex", flexWrap: "wrap", gap: 50 },
  footerBottom: { display: "flex", justifyContent: "space-between", borderTop: `1px solid ${P.border}`, color: "#667486", fontSize: 10.5, paddingTop: 18 },
};
