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
  navy: "#1C1C1C",
  navySoft: "#F1F1EE",
  panel: "rgba(255,255,255,.92)",
  white: "#FFFFFF",
  muted: "#6B6B6B",
  gold: "#E9C83E",
  green: "#45D19A",
  border: "rgba(22,22,22,.14)",
  cream: "#F8F8F5",
  ink: "#161616",
  inkMuted: "#6B6B6B",
  lightBorder: "#E5E7EB",
  footerBorder: "rgba(255,255,255,.16)",
  footerText: "#F8F8F5",
  footerMuted: "#B8B8B8",
};

const FOOTER_LINK_GROUPS = [
  {
    label: "Nền tảng",
    links: [
      { label: "Tổng quan", href: "#overview" },
      { label: "Nền tảng", href: "#platform" },
      { label: "Phân tích", href: "#insights" },
    ],
  },
  {
    label: "Khám phá",
    links: [
      { label: "Tài nguyên", href: "#resources" },
      { label: "Yêu cầu bản demo", href: "#contact" },
      { label: "Dự án", href: "/projects" },
    ],
  },
  {
    label: "Tài khoản",
    links: [
      { label: "Đăng nhập", href: "/login" },
    ],
  },
];

const tooltipStyle = {
  background: "#1C1C1C",
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
          <button className="home-gold-button" type="button" onClick={() => scrollTo("#contact")}>Xem bản demo</button>
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
          <button className="home-gold-button" type="button" onClick={() => scrollTo("#contact")}>Xem bản demo</button>
        </nav>
      )}

      <main>
        <section id="overview" style={S.hero}>
          <BackgroundCarousel label="Hình nền giới thiệu AbsorpIQ" />
          <div style={{ ...S.heroOverlay, ...(isNarrow ? S.heroOverlayNarrow : null) }} />
          <div style={{ ...S.heroInner, ...(isNarrow ? S.heroInnerNarrow : null) }}>
            <div style={{ ...S.heroCopy, marginLeft: "80px", ...(isNarrow ? S.heroCopyNarrow : null) }}>
              <h1 style={S.heroTitle}>Biết căn nào cần ưu tiên<br />Biết nơi cần thúc đẩy bán hàng</h1>
              <p style={S.heroLead}>AbsorpIQ tổng hợp dữ liệu dự án, tồn kho và giao dịch để theo dõi nhịp độ hấp thụ, xếp hạng ưu tiên theo căn và hỗ trợ phân tích bằng AI. Đội ngũ kiểm tra dữ liệu trước khi hành động.</p>
              <div style={S.heroCtas}>
                <button className="home-gold-button" type="button" onClick={() => scrollTo("#platform")}>Khám phá dữ liệu bán hàng <span>↗</span></button>
                <button className="home-outline-button" type="button" onClick={() => scrollTo("#insights")}>Xem dữ liệu mẫu</button>
              </div>
              <div style={S.trustRow}>
                <span><i />Theo dõi hấp thụ</span>
                <span><i />Xếp hạng ưu tiên theo căn</span>
                <span><i />Kiểm tra trước khi hành động</span>
              </div>
            </div>
            <PreviewCard isMobile={isMobile} />
          </div>
        </section>

        <section id="platform" style={S.lightSection}>
          <SectionIntro title="Từ dữ liệu phân mảnh đến góc nhìn rõ ràng." copy="AbsorpIQ đưa các chỉ báo dự án và hấp thụ về cùng một nơi để đội ngũ kiểm tra dữ liệu trước khi hành động." />
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
              <h2 style={S.darkTitle}>Xem dữ liệu hiện có và dự đoán, phân tích bằng AI </h2>
              <p style={S.darkCopy}>Xem tồn kho, giao dịch lịch sử và vận tốc hấp thụ trong một góc nhìn rõ ràng. Được hỗ trợ dự đoán và phân tích bởi AI</p>
              <button className="home-outline-button" type="button" onClick={() => navigate("/projects")}>Khám phá dự án <span>↗</span></button>
              <p style={S.disclaimer}>Dữ liệu minh họa cho bản demo. Không hiển thị dữ liệu dự án thực tế.</p>
            </div>
            <InsightPanel chartMode={chartMode} setChartMode={setChartMode} isNarrow={isNarrow} />
          </div>
        </section>

        <section id="resources" style={{ ...S.metricSection, ...S.resourcesSection }}>
          <DataPipelineAgent isNarrow={isNarrow} />
        </section>

        <section id="contact" style={S.ctaSection}>
          <div style={S.ctaOrb} />
          <div style={S.ctaContent}><div style={S.eyebrow}>SẴN SÀNG XEM DỮ LIỆU CỦA BẠN?</div><h2 style={S.ctaTitle}>Bắt đầu bằng<br />góc nhìn rõ hơn.</h2><p style={S.ctaCopy}>Xem phạm vi dữ liệu hiện có và những phần còn thiếu dữ liệu.</p><div style={S.heroCtas}><button className="home-gold-button" type="button" onClick={() => navigate("/register")}>Yêu cầu bản demo <span>↗</span></button><button className="home-light-button" type="button" onClick={() => scrollTo("#methodology")}>Cách thức hoạt động</button></div></div>
        </section>
      </main>

      <footer style={S.footer}>
        <div style={{ ...S.footerTop, ...(isNarrow ? S.footerTopNarrow : null) }}>
          <div style={S.footerBrand}>
            <Brand size={28} wordSize={17} light />
            <p style={S.footerCopy}>AbsorpIQ giúp đội ngũ bất động sản tập trung dữ liệu hiện có để theo dõi các chỉ báo hấp thụ và đưa ra quyết định rõ ràng hơn.</p>
          </div>
          <div style={{ ...S.footerLinks, ...(isNarrow ? S.footerLinksNarrow : null), ...(isMobile ? S.footerLinksMobile : null) }}>
            {FOOTER_LINK_GROUPS.map((group) => (
              <nav aria-label={group.label} key={group.label} style={S.footerNav}>
                <h2 style={S.footerGroupTitle}>{group.label}</h2>
                <div style={S.footerNavList}>
                  {group.links.map((link) => (
                    <a
                      className="home-footer-link"
                      href={link.href}
                      key={link.href}
                      onClick={(event) => {
                        if (!link.href.startsWith("#")) return;
                        event.preventDefault();
                        scrollTo(link.href);
                      }}
                      style={S.footerLink}
                    >
                      {link.label}
                    </a>
                  ))}
                </div>
              </nav>
            ))}
          </div>
        </div>
        <div style={{ ...S.footerBottom, ...(isNarrow ? S.footerBottomNarrow : null) }}>
          <span>© 2026 AbsorpIQ. Bảo lưu mọi quyền.</span>
          <span>Quyết định dựa trên dữ liệu hiện có.</span>
        </div>
      </footer>
    </div>
  );
}

function Filter({ label, value, onChange, options, labels = options }) {
  return <label style={S.filterField}><span>{label}</span><select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option, index) => <option key={option} value={option}>{labels[index]}</option>)}</select></label>;
}

function PreviewCard({ isMobile }) {
  return (
    <div style={{ ...S.previewWrap, ...(isMobile ? S.previewWrapMobile : null) }}>
      <article aria-label="Dữ liệu CRM" style={S.previewCard}>
        <div style={S.crmCardHeader}>
          <div style={S.crmCardLabel}>
            <span style={{ ...S.crmIcon, fontSize: '20px' }}>
              <Icon name="database" size={22} color={P.ink} />
            </span>
            Dữ liệu dự án từ CRM
          </div>
        </div>
        <h3 style={{ ...S.crmTitle, fontSize: '24px', lineHeight: 1.25 }}>Dữ liệu bán hàng cho góc nhìn dự án chính xác hơn</h3>
        <p style={{ ...S.crmCopy, fontSize: '16px', lineHeight: 1.6 }}>
          Dự án, phân khu, căn hộ và giao dịch từ Mini CRM được chuẩn hóa.
          Dữ liệu này được dùng để cập nhật tồn kho, tốc độ bán và các chỉ số phân tích trên dashboard.
        </p>
      </article>
    </div>
  );
}

const PIPELINE_STAGES = [
  { id: "sync", label: "Đồng bộ", icon: "database", tone: "sync" },
  { id: "validate", label: "Kiểm tra", icon: "filter", tone: "check" },
  { id: "analyze", label: "Phân tích", icon: "rate", tone: "ready" },
  { id: "rank", label: "Xếp hạng", icon: "catalog", tone: "rank" },
  { id: "forecast", label: "Dự đoán", icon: "velocity", tone: "forecast" },
  { id: "advise", label: "Tư vấn", icon: "bot", tone: "advise" },
];

const PIPELINE_STAGE_PAUSES_MS = [3200, 800, 3800, 3800, 3800, 1200];

function prefersReducedMotion() {
  return typeof window !== "undefined"
    && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
}

function DataPipelineAgent({ isNarrow }) {
  const [reducedMotion, setReducedMotion] = useState(prefersReducedMotion);
  const [activeStageIndex, setActiveStageIndex] = useState(() => (prefersReducedMotion() ? 1 : 0));
  const [completedStages, setCompletedStages] = useState(() => (prefersReducedMotion() ? [0] : []));
  const [isPaused, setIsPaused] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mediaQuery) return undefined;

    const updatePreference = () => setReducedMotion(mediaQuery.matches);
    updatePreference();
    mediaQuery.addEventListener?.("change", updatePreference);
    return () => mediaQuery.removeEventListener?.("change", updatePreference);
  }, []);

  useEffect(() => {
    if (reducedMotion) {
      setActiveStageIndex(1);
      setCompletedStages([0]);
    } else {
      setActiveStageIndex(0);
      setCompletedStages([]);
    }
  }, [reducedMotion]);

  useEffect(() => {
    if (reducedMotion || isPaused) return undefined;

    const isFinalStage = activeStageIndex === PIPELINE_STAGES.length - 1;
    const timer = window.setTimeout(() => {
      if (isFinalStage) {
        setActiveStageIndex(0);
        setCompletedStages([]);
        return;
      }

      setCompletedStages((stages) => (stages.includes(activeStageIndex) ? stages : [...stages, activeStageIndex]));
      setActiveStageIndex((index) => index + 1);
    }, PIPELINE_STAGE_PAUSES_MS[activeStageIndex]);

    return () => window.clearTimeout(timer);
  }, [activeStageIndex, isPaused, reducedMotion]);

  const stagePosition = (index) => {
    if (!isNarrow) return { column: index, row: 0 };
    return index < 3 ? { column: index, row: 0 } : { column: 5 - index, row: 1 };
  };

  const agentPosition = stagePosition(activeStageIndex);
  const activeStage = PIPELINE_STAGES[activeStageIndex];
  const handleBlur = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setIsPaused(false);
  };

  return (
    <div style={{ ...S.pipelineLayout, ...(isNarrow ? S.pipelineLayoutNarrow : null) }}>
      <div style={S.pipelineIntro}>
        <h2 style={S.pipelineTitle}>Từ CRM đến góc nhìn sẵn sàng hành động</h2>
        <p style={S.pipelineCopy}>
          AbsorpIQ tiếp nhận dữ liệu dự án, tồn kho và giao dịch; kiểm tra tính nhất quán trước khi đưa vào các chỉ báo phân tích.
        </p>
        <div style={S.pipelineLegend} role="list" aria-label="Các giai đoạn quy trình dữ liệu">
          {PIPELINE_STAGES.map((stage) => (
            <span role="listitem" key={stage.id}>
              <i className={`pipeline-legend-dot pipeline-legend-${stage.tone}`} />{stage.label}
            </span>
          ))}
        </div>
      </div>

      <div
        className="pipeline-card"
        role="group"
        aria-label="Luồng dữ liệu qua sáu giai đoạn từ đồng bộ đến tư vấn"
        tabIndex="0"
        data-paused={isPaused ? "true" : "false"}
        onMouseEnter={() => setIsPaused(true)}
        onMouseLeave={() => setIsPaused(false)}
        onFocus={() => setIsPaused(true)}
        onBlur={handleBlur}
        style={S.pipelineCard}
      >
        <div className="pipeline-track-viewport">
          <div className="pipeline-track" aria-hidden="true">
            <span className="pipeline-track-line" />
            <span
              className="pipeline-agent"
              style={{ "--agent-column": agentPosition.column, "--agent-row": agentPosition.row }}
            >
              <span className="pipeline-agent-module"><Icon name="bot" size={16} color={P.ink} /></span>
            </span>
            {PIPELINE_STAGES.map((stage, index) => {
              const position = stagePosition(index);
              const stageState = index === activeStageIndex
                ? "active"
                : completedStages.includes(index) ? "completed" : "future";
              return (
                <div
                  className={`pipeline-node pipeline-node-${stageState}`}
                  key={stage.id}
                  style={{ gridColumn: position.column + 1, gridRow: position.row + 1 }}
                >
                  <span className="pipeline-node-marker"><Icon name={stage.icon} size={17} color={P.ink} /></span>
                  <span className="pipeline-node-label">{stage.label}</span>
                </div>
              );
            })}
          </div>
        </div>
        <p style={S.pipelineStatus} aria-live="polite"> {activeStage.label}</p>
        <p style={S.pipelineSummary}>
          Dữ liệu được đồng bộ từ CRM, kiểm tra tính nhất quán và chuẩn bị cho phân tích hấp thụ.
        </p>
      </div>
    </div>
  );
}

function Kpi({ value, label, tone }) { return <div><strong style={{ color: tone }}>{value}</strong><span>{label}</span></div>; }

function SectionIntro({ eyebrow, title, copy }) { return <div style={S.sectionIntro}><div style={S.lightEyebrow}>{eyebrow}</div><h2 style={S.lightTitle}>{title}</h2><p style={S.lightCopy}>{copy}</p></div>; }

function FeatureCard({ icon, number, title, description }) { return <article className="home-feature-card" style={S.featureCard}><div style={S.featureTop}><span style={S.featureIcon}><Icon name={icon} size={21} color={P.ink} /></span><span style={S.featureNumber}>{number}</span></div><h3 style={S.featureTitle}>{title}</h3><p style={S.featureCopy}>{description}</p><span className="home-card-arrow" style={S.cardArrow}>↗</span></article>; }

function InsightPanel({ chartMode, setChartMode, isNarrow }) {
  return <div style={S.insightPanel} aria-label="Biểu đồ xu hướng hấp thụ minh họa"><div style={S.panelHeader}><div><h3 style={S.panelTitle}>Xu hướng hấp thụ minh họa</h3></div></div><div style={{ ...S.panelKpis, ...(isNarrow ? S.panelKpisNarrow : null) }}><Kpi value="412 " label="Căn còn lại" tone={P.gold} /><Kpi value="68,4% " label="Tỷ lệ hấp thụ" tone={P.green} /><Kpi label="Giá bán: Chưa có" tone={P.gold} /><Kpi label="Dự báo: Chưa có" tone={P.green} /></div><div className="chart-toolbar" style={S.chartToolbar}><span>Tỷ lệ hấp thụ · minh họa 12 tháng</span><div>{[{ value: "both", label: "Cả hai" }, { value: "actual", label: "Đã ghi nhận" }, { value: "forecast", label: "Tham chiếu minh họa" }].map(({ value, label }) => <button className="chart-toggle" type="button" key={value} aria-pressed={chartMode === value} onClick={() => setChartMode(value)}>{label}</button>)}</div></div><div style={S.chart} role="img" aria-label="Tỷ lệ hấp thụ minh họa tăng từ 18 đến 82 phần trăm trong mười hai tháng; không phải dự báo"><ResponsiveContainer width="100%" height="100%"><AreaChart data={INSIGHT_SERIES} margin={{ top: 8, right: 4, left: -24, bottom: 0 }}><defs><linearGradient id="actualFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={P.gold} stopOpacity={0.3} /><stop offset="100%" stopColor={P.gold} stopOpacity={0} /></linearGradient><linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={P.green} stopOpacity={0.22} /><stop offset="100%" stopColor={P.green} stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke={P.border} vertical={false} /><XAxis dataKey="month" tick={{ fill: P.muted, fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis domain={[0, 100]} tick={{ fill: P.muted, fontSize: 10 }} axisLine={false} tickLine={false} /><Tooltip contentStyle={tooltipStyle} /><Area type="monotone" dataKey="actual" stroke={P.gold} fill="url(#actualFill)" strokeWidth={2} connectNulls={false} hide={chartMode === "forecast"} name="Đã ghi nhận" /><Area type="monotone" dataKey="forecast" stroke={P.green} fill="url(#forecastFill)" strokeWidth={2} strokeDasharray="5 4" hide={chartMode === "actual"} name="Tham chiếu minh họa" /></AreaChart></ResponsiveContainer></div><div style={S.regionHeading}>Nhóm tham chiếu minh họa <span>không phải chỉ số so sánh</span></div><div style={S.regionRows}>{REGION_BARS.map((item) => <div key={item.label} style={S.regionRow}><span>{item.label}</span><div style={S.regionTrack}><span style={{ ...S.regionFill, width: `${item.value}%` }} /></div><b>{item.value}</b></div>)}</div><div style={S.panelLegend}><span><i style={{ background: P.gold }} />Đã ghi nhận</span><span><i style={{ background: P.green }} />Minh họa</span><span style={S.demoNote}>Không phải dữ liệu thực tế</span></div></div>;
}

function WorkflowStep({ number, title, description }) { return <article style={S.workflowStep}><span style={S.workflowNumber}>{number}</span><h3 style={S.workflowTitle}>{title}</h3><p style={S.workflowCopy}>{description}</p></article>; }

const CSS = `
  .home-shell button, .home-shell a, .home-shell select { font: inherit; }
  .home-shell button { cursor: pointer; }
  .home-gold-button, .home-outline-button, .home-light-button { display: inline-flex; align-items: center; gap: 10px; border-radius: 7px; padding: 12px 16px; font-size: 12px; font-weight: 600; }
  .home-gold-button { border: 1px solid ${P.gold}; background: ${P.gold}; color: ${P.ink}; }
  .home-outline-button { border: 1px solid rgba(255,255,255,.62); background: transparent; color: ${P.white}; }
  .home-light-button { border: 1px solid rgba(22,22,22,.35); background: transparent; color: ${P.ink}; }
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
  .background-carousel-controls > button { display: grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 1px solid rgba(255,255,255,.72); border-radius: 50%; background: rgba(22,22,22,.52); color: ${P.white}; font-size: 24px; line-height: 1; pointer-events: auto; }
  .background-carousel-controls > button:hover { border-color: ${P.gold}; color: ${P.gold}; }
  .background-carousel-dots { position: absolute; left: 50%; bottom: 25px; display: flex; gap: 8px; transform: translateX(-50%); pointer-events: auto; }
  .background-carousel-dots button { width: 24px; height: 4px; padding: 0; border: 1px solid rgba(255,255,255,.72); border-radius: 4px; background: rgba(22,22,22,.48); }
  .background-carousel-dots button.is-active { background: ${P.gold}; }
  .home-nav-link, .home-text-button, .home-gold-button, .home-outline-button, .home-light-button, .home-menu-button, .chart-toggle, .home-feature-card { transition: transform .2s ease, background .2s ease, border-color .2s ease, color .2s ease; }
  .home-nav-link:hover { color: ${P.ink} !important; }
  .home-text-button:hover { color: ${P.gold} !important; }
  .home-gold-button:hover { transform: translateY(-2px); background: #f4d75c !important; }
  .home-outline-button:hover, .home-light-button:hover { border-color: ${P.gold} !important; color: ${P.gold} !important; }
  .home-feature-card:hover { transform: translateY(-5px); border-color: ${P.gold} !important; box-shadow: 0 18px 44px rgba(22,22,22,.12); }
  .home-card-arrow { transition: transform .2s ease; }
  .home-feature-card:hover .home-card-arrow { transform: translate(3px, -3px); }
  .home-footer-link:hover { color: ${P.gold} !important; text-decoration: underline !important; text-underline-offset: 4px; }
  @media (max-width: 639px) { .chart-toolbar { flex-wrap: wrap; gap: 8px; } .chart-toolbar > div { margin-left: auto; } }
  .chart-toggle:hover { color: ${P.ink} !important; border-color: ${P.gold} !important; }
  .pipeline-card { outline: none; }
  .pipeline-card:focus-visible { box-shadow: 0 0 0 3px rgba(233,200,62,.6), 0 20px 48px rgba(22,22,22,.12) !important; }
  .pipeline-track-viewport { min-width: 0; overflow-x: auto; overscroll-behavior-x: contain; scrollbar-width: thin; }
  .pipeline-track { position: relative; min-height: 154px; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); grid-template-rows: 1fr; align-items: center; min-width: 0; }
  .pipeline-track-line { position: absolute; z-index: 0; top: 50%; left: 8.333%; right: 8.333%; height: 1px; background: rgba(22,22,22,.2); }
  .pipeline-agent { position: absolute; z-index: 3; top: 0; left: 0; width: 16.666%; height: 100%; display: flex; align-items: center; justify-content: center; pointer-events: none; transform: translateX(calc(var(--agent-column) * 100%)) translateY(calc(var(--agent-row) * 100%)); transition: transform 850ms ease-in-out; }
  .pipeline-agent-module { display: grid; place-items: center; width: 36px; height: 36px; border: 1px solid ${P.gold}; border-radius: 10px; background: rgba(255,255,255,.96); color: ${P.ink}; box-shadow: 0 7px 18px rgba(22,22,22,.14); }
  .pipeline-node { position: relative; z-index: 1; min-width: 0; display: grid; justify-items: center; align-content: center; gap: 10px; color: ${P.ink}; font-size: 11px; text-align: center; scroll-snap-align: center; }
  .pipeline-node-marker { display: grid; place-items: center; width: 44px; height: 44px; border: 1px solid rgba(22,22,22,.18); border-radius: 12px; background: ${P.white}; box-shadow: 0 5px 14px rgba(22,22,22,.08); transition: border-color .25s ease, box-shadow .25s ease, background .25s ease; }
  .pipeline-node-active .pipeline-node-marker { border-color: ${P.gold}; background: #fffdf2; box-shadow: 0 0 0 5px rgba(233,200,62,.14), 0 5px 14px rgba(22,22,22,.08); }
  .pipeline-node-completed .pipeline-node-marker { border-color: ${P.green}; box-shadow: 0 0 0 4px rgba(69,209,154,.12), 0 5px 14px rgba(22,22,22,.08); animation: pipeline-stage-complete-pulse .85s ease-out; }
  .pipeline-node-label { max-width: 120px; }
  .pipeline-legend { display: flex; flex-wrap: wrap; gap: 10px 20px; margin-top: 28px; color: ${P.inkMuted}; font-size: 11px; }
  .pipeline-legend span { display: inline-flex; align-items: center; gap: 7px; }
  .pipeline-legend-dot { width: 6px; height: 6px; border-radius: 50%; background: ${P.muted}; }
  .pipeline-legend-check, .pipeline-legend-ready { background: ${P.green}; }
  .pipeline-legend-sync, .pipeline-legend-rank, .pipeline-legend-forecast, .pipeline-legend-advise { background: ${P.muted}; }
  .pipeline-card[data-paused="true"] .pipeline-agent { transition-duration: .01ms; }
  @keyframes pipeline-stage-complete-pulse { 0% { box-shadow: 0 0 0 0 rgba(69,209,154,0); } 45% { box-shadow: 0 0 0 6px rgba(69,209,154,.18), 0 5px 14px rgba(22,22,22,.08); } 100% { box-shadow: 0 0 0 4px rgba(69,209,154,.12), 0 5px 14px rgba(22,22,22,.08); } }
  @media (max-width: 900px) { .pipeline-track { min-height: 220px; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-template-rows: repeat(2, 1fr); } .pipeline-track-line { top: 25%; bottom: 25%; left: 16.666%; right: 16.666%; height: auto; background: linear-gradient(to bottom, transparent 0 24.5%, rgba(22,22,22,.2) 24.5% 25.5%, transparent 25.5% 74.5%, rgba(22,22,22,.2) 74.5% 75.5%, transparent 75.5% 100%); } .pipeline-track-line::after { content: ""; position: absolute; top: 25%; right: 0; bottom: 25%; width: 1px; background: rgba(22,22,22,.2); } .pipeline-agent { width: 33.333%; height: 50%; } .pipeline-node-label { max-width: 110px; } }
  @media (max-width: 639px) { .pipeline-track { min-width: 540px; min-height: 232px; } .pipeline-node-label { max-width: 92px; font-size: 10px; } .pipeline-card { padding: 18px !important; } }
  @media (prefers-reduced-motion: reduce) { .pipeline-agent { transition: none; } .pipeline-node-active .pipeline-node-marker { box-shadow: 0 0 0 5px rgba(233,200,62,.14), 0 5px 14px rgba(22,22,22,.08); } .pipeline-node-completed .pipeline-node-marker { animation: none; } }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
`;

const S = {
  shell: { minHeight: "100vh", background: P.cream, color: P.ink, fontFamily: font.sans },
  hidden: { display: "none" },
  nav: { position: "fixed", zIndex: 20, top: 16, left: "50%", transform: "translateX(-50%)", width: "min(1180px, calc(100% - 36px))", minHeight: 66, display: "flex", alignItems: "center", padding: "0 20px", border: `1px solid ${P.border}`, borderRadius: 14, background: "rgba(255,255,255,.82)", boxShadow: "0 10px 30px rgba(22,22,22,.08)", transition: "background .2s ease, box-shadow .2s ease" },
  navScrolled: { background: "rgba(255,255,255,.96)", boxShadow: "0 12px 34px rgba(22,22,22,.12)" },
  brandLink: { display: "inline-flex" },
  desktopNav: { display: "flex", gap: 24, marginLeft: 46, color: P.muted, fontSize: 12.5 },
  navActions: { display: "flex", alignItems: "center", gap: 15, marginLeft: "auto", fontSize: 12.5 },
  language: { color: P.muted, fontFamily: font.mono, fontSize: 11, paddingRight: 5, borderRight: `1px solid ${P.border}` },
  mobileMenu: { position: "fixed", zIndex: 19, top: 91, left: 18, right: 18, display: "grid", gap: 17, padding: 22, border: `1px solid ${P.border}`, borderRadius: 14, background: "rgba(255,255,255,.98)", color: P.ink, boxShadow: "0 16px 36px rgba(22,22,22,.14)" },
  menuLink: { color: P.muted },
  hero: { position: "relative", minHeight: 790, overflow: "hidden", display: "flex", alignItems: "center", padding: "130px max(24px, calc((100% - 1180px) / 2)) 148px", color: P.white },
  heroOverlay: { position: "absolute", zIndex: 0, top: 0, bottom: 0, left: "max(24px, calc((100% - 1180px) / 2))", width: "42vw", maxWidth: "42vw", background: "linear-gradient(90deg, rgba(10,10,10,.52) 0%, rgba(10,10,10,.22) 55%, rgba(10,10,10,0) 100%)", pointerEvents: "none" },
  heroOverlayNarrow: { display: "none" },
  heroInner: { position: "relative", zIndex: 1, width: "100%", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(360px, .78fr)", gap: 60, alignItems: "center" },
  heroInnerNarrow: { gridTemplateColumns: "1fr", paddingTop: 35, gap: 42 },
  heroCopy: { maxWidth: 650 },
  heroCopyNarrow: { padding: 18, borderRadius: 14, background: "rgba(10,10,10,.42)" },
  eyebrow: { color: P.gold, fontFamily: font.mono, fontSize: 10.5, letterSpacing: ".16em", fontWeight: 600 },
  heroTitle: { margin: "18px 0 22px", fontFamily: font.display, fontSize: "clamp(42px, 5vw, 73px)", lineHeight: 1.02, letterSpacing: "-.055em", fontWeight: 500 },
  heroLead: { maxWidth: 570, color: "rgba(255,255,255,.88)", fontSize: 16, lineHeight: 1.7, margin: 0, textShadow: "0 1px 14px rgba(10,10,10,.18)" },
  heroCtas: { display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", marginTop: 32 },
  trustRow: { display: "flex", flexWrap: "wrap", gap: "10px 24px", color: P.muted, fontSize: 11.5, marginTop: 40 },
  previewWrap: { justifySelf: "end", width: "min(100%, 430px)" },
  previewWrapMobile: { justifySelf: "stretch", width: "100%" },
  previewCard: { padding: 24, border: `1px solid ${P.border}`, borderRadius: 16, background: "rgba(255,255,255,.9)", color: P.ink, boxShadow: "0 20px 56px rgba(22,22,22,.16)" },
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
  filterPanel: { position: "absolute", zIndex: 2, left: "50%", bottom: 38, transform: "translateX(-50%)", width: "min(1080px, calc(100% - 48px))", display: "grid", gridTemplateColumns: "1fr 1.4fr 1fr auto", gap: 1, padding: 7, border: `1px solid ${P.border}`, borderRadius: 12, background: "rgba(255,255,255,.9)", color: P.ink, boxShadow: "0 16px 40px rgba(22,22,22,.12)" },
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
  insightOverlay: { position: "absolute", zIndex: 1, top: 0, bottom: 0, left: "max(24px, calc((100% - 1080px) / 2))", width: "42vw", maxWidth: "42vw", background: "linear-gradient(90deg, rgba(10,10,10,.52) 0%, rgba(10,10,10,.22) 55%, rgba(10,10,10,0) 100%)", pointerEvents: "none" },
  insightOverlayNarrow: { display: "none" },
  insightLayout: { position: "relative", zIndex: 2, display: "grid", gridTemplateColumns: "minmax(230px,.72fr) minmax(480px,1.28fr)", gap: 70, alignItems: "center" },
  insightLayoutNarrow: { gridTemplateColumns: "1fr", gap: 45 },
  insightCopy: {},
  insightCopyNarrow: { padding: 16, borderRadius: 14, background: "rgba(10,10,10,.42)" },
  darkTitle: { margin: "17px 0", color: P.white, fontFamily: font.display, fontSize: "clamp(34px, 4vw, 52px)", lineHeight: 1.06, letterSpacing: "-.05em", fontWeight: 500, textShadow: "0 1px 14px rgba(10,10,10,.18)" },
  darkCopy: { color: "rgba(255,255,255,.88)", lineHeight: 1.7, maxWidth: 420, fontSize: 15, marginBottom: 30, textShadow: "0 1px 12px rgba(10,10,10,.18)" },
  disclaimer: { color: "rgba(255,255,255,.72)", fontSize: 10.5, marginTop: 24 },
  insightPanel: { padding: 23, border: `1px solid ${P.border}`, borderRadius: 14, background: P.panel, color: P.ink, boxShadow: "0 20px 56px rgba(22,22,22,.16)" },
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
  regionTrack: { height: 5, background: "rgba(22,22,22,.12)", borderRadius: 8, overflow: "hidden" },
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
  resourcesSection: { paddingTop: 86, paddingBottom: 86 },
  pipelineLayout: { display: "grid", gridTemplateColumns: "minmax(250px, .78fr) minmax(420px, 1.22fr)", gap: 70, alignItems: "center" },
  pipelineLayoutNarrow: { gridTemplateColumns: "1fr", gap: 38 },
  pipelineIntro: { maxWidth: 520 },
  pipelineTitle: { margin: "16px 0 14px", color: P.ink, fontFamily: font.display, fontSize: "clamp(31px, 4vw, 48px)", lineHeight: 1.06, letterSpacing: "-.05em", fontWeight: 500 },
  pipelineCopy: { maxWidth: 500, color: P.inkMuted, fontSize: 14, lineHeight: 1.7, margin: 0 },
  pipelineLegend: { display: "flex", flexWrap: "wrap", gap: "10px 20px", marginTop: 28, color: P.inkMuted, fontSize: 11 },
  pipelineCard: { minWidth: 0, padding: 28, border: `1px solid ${P.lightBorder}`, borderRadius: 16, background: "rgba(255,255,255,.82)", color: P.ink, boxShadow: "0 20px 48px rgba(22,22,22,.1)" },
  pipelineStatus: { margin: "18px 0 0", color: P.ink, fontSize: 12, fontWeight: 600 },
  pipelineSummary: { margin: "20px 0 0", paddingTop: 17, borderTop: `1px solid ${P.border}`, color: P.inkMuted, fontSize: 11.5, lineHeight: 1.55 },
  metrics: { display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 20, marginTop: 32 },
  metricsNarrow: { gridTemplateColumns: "repeat(2,1fr)", gap: "32px 20px" },
  ctaSection: { position: "relative", overflow: "hidden", textAlign: "center", background: P.gold, color: P.ink, padding: "125px 24px" },
  ctaOrb: { position: "absolute", width: 500, height: 500, borderRadius: "50%", border: "1px solid rgba(22,22,22,.14)", left: "50%", top: "50%", transform: "translate(-50%,-50%)", boxShadow: "0 0 0 60px rgba(255,255,255,.08), 0 0 0 120px rgba(255,255,255,.06)" },
  ctaContent: { position: "relative" },
  ctaTitle: { fontFamily: font.display, fontSize: "clamp(39px, 5vw, 68px)", lineHeight: 1, letterSpacing: "-.06em", fontWeight: 500, margin: "18px 0" },
  ctaCopy: { color: "rgba(22,22,22,.7)", margin: 0 },
  footer: { background: P.navy, padding: "64px max(24px, calc((100% - 1080px) / 2)) 22px", borderTop: `1px solid ${P.footerBorder}` },
  footerTop: { display: "grid", gridTemplateColumns: "minmax(220px, .8fr) minmax(0, 1.2fr)", gap: 80, paddingBottom: 58 },
  footerTopNarrow: { gridTemplateColumns: "1fr", gap: 42 },
  footerBrand: { maxWidth: 320 },
  footerCopy: { color: P.footerMuted, fontSize: 12, lineHeight: 1.7, maxWidth: 300, margin: "18px 0 0" },
  footerLinks: { display: "grid", gridTemplateColumns: "repeat(3, minmax(100px, 1fr))", gap: 36 },
  footerLinksNarrow: { gridTemplateColumns: "repeat(2, minmax(120px, 1fr))", gap: 30 },
  footerLinksMobile: { gridTemplateColumns: "1fr", gap: 22 },
  footerNav: { minWidth: 0 },
  footerGroupTitle: { margin: 0, color: P.footerText, fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" },
  footerNavList: { display: "grid", gap: 2, marginTop: 12 },
  footerLink: { display: "flex", alignItems: "center", minHeight: 44, color: P.footerMuted, fontSize: 12, lineHeight: 1.4 },
  footerBottom: { display: "flex", justifyContent: "space-between", gap: 20, borderTop: `1px solid ${P.footerBorder}`, color: P.footerMuted, fontSize: 10.5, paddingTop: 18 },
  footerBottomNarrow: { flexDirection: "column", gap: 8 },
};
