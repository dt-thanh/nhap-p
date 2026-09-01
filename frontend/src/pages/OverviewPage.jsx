// frontend/src/pages/OverviewPage.jsx
// Tổng quan DANH MỤC — sống ở /overview.
//
// KHÁC HẲN dashboard của MỘT dự án (/projects/:externalId/dashboard, xem
// pages/ProjectDashboardPage.jsx). Trang này KHÔNG dùng AbsorptionDashboard:
// không có bộ lọc dự án/phân khu, không có chuỗi hấp thụ của một dự án, không
// có bảng phân khu. Nó trả lời ba câu hỏi ở cấp danh mục:
//   1. Toàn hệ thống đang có bao nhiêu dự án/phân khu/căn/deal (KPI danh mục).
//   2. Chỗ nào cần chú ý, xuyên dự án (biểu đồ tín hiệu tròn).
//   3. Căn nào đáng ưu tiên nhất, bất kể thuộc dự án nào (xếp hạng căn toàn cục).
//
// Fan-out CÓ TRẦN: backend chỉ có endpoint hấp thụ/xếp hạng theo TỪNG dự án,
// nên trang tự đặt trần request và NÓI RA phần chưa quét — im lặng ở đây sẽ bị
// đọc thành "không có vấn đề". Hai trần tách riêng (xếp hạng rộng hơn hấp thụ)
// vì kết quả xếp hạng còn được bảng xếp hạng toàn cục dùng lại, còn mỗi lượt
// hấp thụ là một request chỉ phục vụ tín hiệu.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getAbsorptionSummary,
  getPortfolioSummary,
  getRanking,
  listProjects,
} from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import GlobalUnitRanking from "../components/dashboard/GlobalUnitRanking";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import Icon from "../components/ui/Icon";
import { SectionState, Skeleton, fmt } from "../components/ui/States";
import { SIGNAL_PROJECT_LIMIT, deriveSignals } from "../utils/signals";
import {
  RANKING_PROJECT_LIMIT,
  UNITS_PER_PROJECT_LIMIT,
  buildGlobalRanking,
} from "../utils/globalUnitRanking";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

/** Sáu KPI danh mục, đọc từ ĐÚNG MỘT aggregate endpoint. Thiếu field nào thì
 *  `fmt` hiện "Chưa có dữ liệu" — không thay bằng 0. */
const PORTFOLIO_KPIS = [
  { key: "project_count", label: "Tổng dự án", icon: "folder" },
  { key: "area_count", label: "Tổng phân khu", icon: "catalog" },
  { key: "unit_count", label: "Tổng unit", icon: "units" },
  { key: "deal_count", label: "Tổng deals", icon: "sold" },
  { key: "booking_count", label: "Đang booking", icon: "remaining" },
  { key: "selling_project_count", label: "Đang bán", icon: "rate" },
];

/** Chuẩn hoá lỗi ĐÚNG như `useAsync` để `utils/signals.js` đọc được
 *  `status`/`message` — nó phân biệt 4xx với lỗi mạng khi dựng bằng chứng. */
function toFeedError(e) {
  return {
    message: e?.message || "Đã xảy ra lỗi",
    status: e?.status,
    network: e?.name === "TypeError" || e?.status === 0 || e?.status === undefined,
  };
}

const settle = (promise) =>
  promise.then(
    (data) => ({ data, error: null }),
    (e) => ({ data: null, error: toFeedError(e) }),
  );

const EMPTY_FEED = {
  loading: true,
  projectsError: null,
  entries: [],
  skippedProjectCount: 0,
  absorptionSkippedCount: 0,
};

/**
 * Nạp danh mục rồi fan-out theo dự án, MỘT lần cho cả tín hiệu lẫn xếp hạng.
 *
 * Lỗi của từng dự án KHÔNG làm hỏng cả trang: mỗi lượt gọi được `settle` riêng
 * và đi vào `entries` dưới dạng `rankingError`/`absorptionError`, để tầng suy
 * luận biến nó thành một tín hiệu nêu đích danh dự án. Chỉ khi chính
 * `GET /projects` hỏng thì mới không còn gì để quét — lúc đó `projectsError`
 * là trạng thái của cả trang.
 */
function usePortfolioFeed() {
  const [feed, setFeed] = useState(EMPTY_FEED);
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setFeed(EMPTY_FEED);

    (async () => {
      let projects;
      try {
        projects = await listProjects();
      } catch (e) {
        if (!cancelled) setFeed({ ...EMPTY_FEED, loading: false, projectsError: toFeedError(e) });
        return;
      }

      const list = Array.isArray(projects) ? projects : [];
      // Dự án di sản (chưa có `external_id`) không gọi được endpoint có phạm vi
      // nào, nên chúng được tính chung vào phần "chưa quét" thay vì biến mất.
      const scanned = list.filter((p) => p?.external_id).slice(0, RANKING_PROJECT_LIMIT);
      const skippedProjectCount = Math.max(list.length - scanned.length, 0);
      const absorptionIds = new Set(
        scanned.filter((p) => p.project_id).slice(0, SIGNAL_PROJECT_LIMIT).map((p) => p.project_id),
      );
      const absorptionSkippedCount = Math.max(scanned.length - absorptionIds.size, 0);

      const entries = await Promise.all(
        scanned.map(async (project) => {
          // Cờ TƯỜNG MINH: "không gọi" khác "gọi xong không có dữ liệu", và
          // `deriveSignals` phải phân biệt được hai chuyện đó.
          const absorptionRequested = absorptionIds.has(project.project_id);
          const [ranking, absorption] = await Promise.all([
            settle(getRanking(project.external_id, { limit: UNITS_PER_PROJECT_LIMIT, offset: 0 })),
            absorptionRequested
              ? settle(getAbsorptionSummary(project.project_id))
              : Promise.resolve({ data: null, error: null }),
          ]);
          return {
            project,
            ranking: ranking.data,
            rankingError: ranking.error,
            absorptionRequested,
            absorption: absorption.data,
            absorptionError: absorption.error,
          };
        }),
      );

      if (!cancelled) {
        setFeed({ loading: false, projectsError: null, entries, skippedProjectCount, absorptionSkippedCount });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  return { ...feed, reload };
}

export default function OverviewPage() {
  const feed = usePortfolioFeed();
  const portfolio = useAsync(() => getPortfolioSummary(), []);
  const [rankingPage, setRankingPage] = useState(0);

  const { entries, loading, projectsError, skippedProjectCount, absorptionSkippedCount } = feed;

  const signals = useMemo(
    () =>
      loading
        ? []
        : deriveSignals({ entries, projectsError, skippedProjectCount, absorptionSkippedCount }),
    [loading, entries, projectsError, skippedProjectCount, absorptionSkippedCount],
  );

  const { rows, meta } = useMemo(
    () => buildGlobalRanking(entries, { projectsNotScanned: skippedProjectCount }),
    [entries, skippedProjectCount],
  );

  // Danh sách đổi độ dài thì trang hiện tại có thể trỏ ra ngoài — về đầu bảng
  // thay vì hiện một trang rỗng.
  useEffect(() => {
    setRankingPage(0);
  }, [rows.length]);

  return (
    <>
      <GlobalKeyframes />

      <header style={S.header}>
        <div style={S.eyebrow}>PORTFOLIO OVERVIEW</div>
        <h1 style={S.h1}>Tổng quan danh mục</h1>
        <p style={S.sub}>
          Mọi dự án trong phạm vi được cấp quyền, gộp thành một bức tranh. Số liệu của TỪNG dự án
          nằm ở dashboard riêng của dự án đó.
        </p>
      </header>

      <section style={S.card} aria-labelledby="portfolio-kpi-title">
        <div style={S.cardHeader}>
          <div>
            <div style={S.cardEyebrow}>PORTFOLIO KPI</div>
            <h2 id="portfolio-kpi-title" style={S.sectionTitle}>Chỉ số danh mục</h2>
          </div>
        </div>
        <SectionState
          loading={portfolio.loading}
          error={portfolio.error}
          onRetry={portfolio.reload}
          compact
          skeleton={
            <div style={S.kpiGrid}>
              {PORTFOLIO_KPIS.map((kpi) => (
                <Skeleton key={kpi.key} height={82} radius={radius.md} />
              ))}
            </div>
          }
        >
          <dl style={S.kpiGrid}>
            {PORTFOLIO_KPIS.map((kpi) => (
              <div key={kpi.key} style={S.kpi}>
                <span style={S.kpiIcon}>
                  <Icon name={kpi.icon} size={15} color={color.accent} />
                </span>
                <dt style={S.kpiLabel}>{kpi.label}</dt>
                <dd style={S.kpiValue}>{fmt(portfolio.data?.[kpi.key] ?? null)}</dd>
              </div>
            ))}
          </dl>
        </SectionState>
      </section>

      <div style={S.stack}>
        <AttentionChart signals={signals} entries={entries} loading={loading} error={projectsError} onRetry={feed.reload} />

        <GlobalUnitRanking
          rows={rows}
          meta={meta}
          loading={loading}
          error={projectsError}
          onRetry={feed.reload}
          page={rankingPage}
          onPageChange={setRankingPage}
        />
      </div>
    </>
  );
}

const ATTENTION_CATEGORY_DEFS = {
  missing_score: {
    label: "Chưa có điểm AHP",
    description: "Chưa có hierarchical AHP score được lưu.",
    meaning: "Chưa có điểm AHP được lưu; đây là tín hiệu sẵn sàng dữ liệu, không phải điểm thấp.",
    action: "Kiểm tra trạng thái ranking và dữ liệu đầu vào.",
    color: color.warn,
  },
  partial_score: {
    label: "Phủ điểm AHP chưa đủ",
    description: "Một phần căn chưa có đủ dữ liệu ranking.",
    meaning: "Một phần item chưa có dữ liệu ranking hoàn chỉnh để so sánh toàn diện.",
    action: "Kiểm tra độ đầy đủ dữ liệu trước khi dùng score để so sánh.",
    color: color.accent,
  },
  inventory_risk: {
    label: "Nguy cơ tồn kho",
    description: "Tồn kho hoặc hấp thụ cho thấy cần đánh giá.",
    meaning: "Tồn kho, vận tốc hoặc hấp thụ cho thấy cần đánh giá can thiệp.",
    action: "Rà soát pricing, ưu đãi, thông điệp bán hàng hoặc kênh lead.",
    color: color.danger,
  },
  watch: {
    label: "Cần theo dõi",
    description: "Tín hiệu xếp hạng hoặc hấp thụ chưa thuận lợi.",
    meaning: "Điểm AHP hoặc tín hiệu hấp thụ thấp hơn các item cùng phạm vi.",
    action: "Theo dõi trong chu kỳ bán tiếp theo và kiểm tra dữ liệu liên quan.",
    color: color.warn,
  },
  opportunity: {
    label: "Cơ hội ưu tiên",
    description: "Điểm AHP cao trong phạm vi có thể so sánh.",
    meaning: "Các căn này có điểm AHP cao trong nhóm dữ liệu có thể so sánh.",
    action: "Ưu tiên đưa vào danh sách tư vấn hoặc phân bổ lead.",
    color: color.ok,
  },
};

function coverageFromSignal(signal) {
  const processed = Number(String(signal?.evidence?.baselineValue || "").match(/\d+/)?.[0]);
  const skipped = Number(signal?.evidence?.currentValue);
  if (!Number.isFinite(processed) || !Number.isFinite(skipped)) return null;
  return { scored: Math.max(processed - skipped, 0), total: processed };
}

function attentionCategoryFor(signal) {
  const rule = String(signal?.ruleId || signal?.id || "");
  if (/ranking:(unavailable|never-computed)/.test(rule)) return "missing_score";
  if (/ranking:skipped-units/.test(rule)) {
    const coverage = coverageFromSignal(signal);
    return coverage && coverage.scored === 0 ? "missing_score" : "partial_score";
  }
  if (/absorption:(low|target-gap|sellout-horizon|velocity-decreasing|velocity-zero)/.test(rule)) return "inventory_risk";
  if (/absorption:(unavailable|freshness)|ranking:low-band-units|forecasting:|portfolio:/.test(rule)) return "watch";
  return null;
}

function opportunitySignals(entries = []) {
  const opportunities = [];
  for (const entry of Array.isArray(entries) ? entries : []) {
    const project = entry?.project || {};
    const items = Array.isArray(entry?.ranking?.items) ? entry.ranking.items : [];
    for (const item of items) {
      const score = Number(item?.score);
      const coverage = Number(item?.weight_coverage);
      const isOpen = item?.unit_status === "available";
      if (!isOpen || !Number.isFinite(score) || item?.band !== "high" || !Number.isFinite(coverage) || coverage < 1) continue;
      const projectExternalId = project.external_id;
      const unitCode = item.unit_code || "Căn chưa có mã";
      opportunities.push({
        id: `ranking:opportunity:${item.unit_id || unitCode}`,
        ruleId: "ranking:opportunity-high",
        scope: "unit",
        title: `${unitCode} · ${project.name || "Dự án"}`,
        whatHappened: `Điểm AHP ${score.toFixed(4)} · hạng #${item.rank_in_project ?? "—"} trong dự án.`,
        whyItMatters: "Có tín hiệu AHP tốt, phù hợp để ưu tiên tư vấn hoặc phân bổ lead; không hàm ý chắc chắn bán được.",
        severity: "info",
        confidence: "high",
        status: "open",
        affectedUnits: 1,
        evidence: {
          currentValue: `AHP ${score.toFixed(4)}`,
          baselineValue: item.rank_in_project == null ? null : `Hạng #${item.rank_in_project}`,
          scoreCoverage: "Đủ dữ liệu",
          externalId: projectExternalId,
        },
        contributions: Array.isArray(item.contributions) ? item.contributions : [],
        links: [],
        projectName: project.name,
        areaName: item.area_name,
        unitCode,
      });
    }
  }
  return opportunities;
}

/** Pure, deterministic grouping for the circular chart. Aggregate signals
 * retain their real affected-project count and children; no business score is
 * invented or averaged here. */
export function deriveCircularAttention(signals = [], entries = []) {
  const groups = new Map();
  const add = (signal, key) => {
    const def = ATTENTION_CATEGORY_DEFS[key];
    const itemCount = Math.max(1, Number(signal.affectedProjectCount) || 1);
    const current = groups.get(key) || { key, ...def, count: 0, items: [] };
    current.count += itemCount;
    current.items.push(signal);
    groups.set(key, current);
  };
  for (const signal of Array.isArray(signals) ? signals : []) {
    const key = attentionCategoryFor(signal);
    if (key) add(signal, key);
  }
  for (const signal of opportunitySignals(entries)) add(signal, "opportunity");
  const categories = [...groups.values()];
  return { categories, total: categories.reduce((sum, category) => sum + category.count, 0) };
}

function AttentionChart({ signals = [], entries = [], loading = false, error = null, onRetry }) {
  const { categories, total } = useMemo(() => deriveCircularAttention(signals, entries), [signals, entries]);
  const [selectedKey, setSelectedKey] = useState(null);
  const [reportOpen, setReportOpen] = useState(false);
  const triggerRef = useRef(null);
  const selected = categories.find((category) => category.key === selectedKey) || null;

  useEffect(() => {
    if (selectedKey && !selected) setSelectedKey(null);
  }, [selected, selectedKey]);

  const openReport = useCallback((key, trigger) => {
    setSelectedKey(key);
    setReportOpen(true);
    if (trigger) triggerRef.current = trigger;
  }, []);

  const closeReport = useCallback(() => {
    setReportOpen(false);
    triggerRef.current?.focus?.();
  }, []);

  if (loading) {
    return <section style={S.card} aria-labelledby="attention-chart-title"><ChartHeader /><div data-testid="signals-loading" style={S.circularLoading} aria-busy="true"><div style={S.loadingDonut}><Skeleton width={168} height={168} radius={radius.pill} /></div><div style={S.loadingAside}><Skeleton width="86%" height={18} /><Skeleton width="70%" height={18} /><Skeleton width="78%" height={18} /><Skeleton width="64%" height={18} /><Skeleton width="82%" height={18} /></div></div></section>;
  }
  if (error) {
    return <section style={S.card} aria-labelledby="attention-chart-title"><ChartHeader /><p style={S.chartEmpty}>Không thể tải tín hiệu cần chú ý. Vui lòng thử lại.</p><button type="button" style={S.retryButton} onClick={onRetry}>Thử lại</button></section>;
  }

  return <section style={S.card} aria-labelledby="attention-chart-title">
    <ChartHeader />
    {total === 0 ? <EmptyCircularState /> : <div style={S.circularLayout} data-testid="signals-list">
      <div style={S.circularVisual}><CircularDonut categories={categories} total={total} selectedKey={selectedKey} onSelect={openReport} /><div style={S.chartLegend} aria-label="Chú giải loại tín hiệu">{categories.map((category) => <button key={category.key} data-testid={`category-legend-${category.key}`} type="button" style={{ ...S.legendButton, ...(category.key === selectedKey ? S.legendButtonSelected : {}) }} onClick={(event) => openReport(category.key, event.currentTarget)} aria-pressed={category.key === selectedKey}><i style={{ ...S.legendDot, background: category.color }} /> <span style={S.legendButtonText}><strong>{category.label}</strong><small style={S.legendButtonSmall}>{category.count} mục · {category.description}</small></span></button>)}</div></div>
    </div>}
    {reportOpen && selected ? <AttentionReportModal category={selected} onClose={closeReport} onClear={() => { setSelectedKey(null); closeReport(); }} /> : null}
    <p style={S.chartPolicy}><strong>Phân loại minh bạch:</strong> mỗi mục chỉ vào một nhóm theo thứ tự ưu tiên dữ liệu; vòng tròn biểu diễn số mục thật, không phải điểm rủi ro tổng hợp.</p>
    <p style={S.readonlyNotice} data-testid="signals-readonly-notice">Chỉ đọc: tín hiệu lấy từ dữ liệu hiện có; không có thao tác ghi nhận hay tính lại trong dashboard.</p>
  </section>;
}

function ChartHeader() {
  return <div style={S.cardHeader}><div><div style={S.cardEyebrow}>AHP ATTENTION INTELLIGENCE</div><h2 id="attention-chart-title" style={S.sectionTitle}>Tín hiệu cần chú ý</h2><p style={S.chartSubtitle}>Ưu tiên căn hộ và phân khu theo điểm AHP đã lưu, độ phủ dữ liệu và tín hiệu hấp thụ hiện có.</p></div><span style={S.scopeBadge}>Phạm vi danh mục</span></div>;
}

function CircularDonut({ categories, total, selectedKey, onSelect }) {
  let offset = 0;
  const single = categories.length === 1;
  const [focusedKey, setFocusedKey] = useState(null);
  const renderSlice = (category, percent, start) => <circle key={category.key} data-testid={`category-slice-${category.key}`} cx="90" cy="90" r="58" fill="none" stroke={category.color} strokeWidth={category.key === selectedKey || category.key === focusedKey ? 30 : 26} pathLength="100" strokeDasharray={`${percent} ${100 - percent}`} strokeDashoffset={-start} onClick={(event) => onSelect(category.key, event.currentTarget)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(category.key, event.currentTarget); } }} onFocus={() => setFocusedKey(category.key)} onBlur={() => setFocusedKey(null)} tabIndex={0} role="button" aria-label={`${category.label}: ${category.count} mục`} aria-pressed={category.key === selectedKey} style={{ cursor: "pointer", opacity: category.key === selectedKey ? 1 : .72, filter: category.key === focusedKey ? `drop-shadow(0 0 3px ${category.color})` : "none", transition: "opacity 160ms ease, stroke-width 160ms ease, filter 160ms ease" }} />;
  return <div style={S.donutWrap}><svg data-testid="attention-donut" viewBox="0 0 180 180" style={S.donut} role="group" aria-label={`${total} mục cần xem xét theo loại tín hiệu`}><circle cx="90" cy="90" r="58" fill="none" stroke={color.border} strokeWidth="26" />{single ? renderSlice(categories[0], 100, 0) : <g transform="rotate(-90 90 90)">{categories.map((category) => { const percent = (category.count / total) * 100; const start = offset; offset += percent; return renderSlice(category, percent, start); })}</g>}<text x="90" y="84" textAnchor="middle" style={S.donutCount}>{total}</text><text x="90" y="105" textAnchor="middle" style={S.donutLabel}>mục cần xem xét</text></svg><p style={S.donutHint}>Dựa trên AHP và dữ liệu hấp thụ</p></div>;
}

function EmptyCircularState() {
  return <div style={S.emptyCircular} data-testid="signals-list"><div style={S.emptyDonut}><span>0</span></div><strong>Chưa có tín hiệu</strong><p>Không có tín hiệu nào có thể suy ra từ dữ liệu hiện tại.</p></div>;
}

function AttentionReportModal({ category, onClose, onClear }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    const previousFocus = document.activeElement;
    dialogRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previousFocus && previousFocus !== document.body) previousFocus.focus?.();
    };
  }, [category.key, onClose]);
  return <div style={S.modalBackdrop} data-testid="attention-report-backdrop" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} style={S.modal} data-testid="attention-report-dialog" role="dialog" aria-modal="true" aria-labelledby="attention-report-title" tabIndex={-1}>
      <div style={S.modalHeader}><span style={S.cardEyebrow}>CHI TIẾT TÍN HIỆU</span><button type="button" style={S.modalClose} aria-label="Đóng báo cáo tín hiệu" onClick={onClose}>×</button></div>
      <AttentionCategoryDetail category={category} onClear={onClear} />
    </section>
  </div>;
}

function flattenReportSignals(signals = []) {
  const result = [];
  const visit = (signal) => {
    if (!signal || result.some((item) => item.id === signal.id)) return;
    result.push(signal);
    (Array.isArray(signal.children) ? signal.children : []).forEach(visit);
  };
  (Array.isArray(signals) ? signals : []).forEach(visit);
  return result;
}

function reportBadge(category) {
  const meta = {
    missing_score: { label: "Thiếu dữ liệu", fg: color.warn, bg: color.warnSoft },
    partial_score: { label: "Thiếu dữ liệu", fg: color.accent, bg: color.accentSoft },
    inventory_risk: { label: "Cảnh báo", fg: color.danger, bg: color.dangerSoft },
    watch: { label: "Theo dõi", fg: color.warn, bg: color.warnSoft },
    opportunity: { label: "Cơ hội", fg: color.ok, bg: color.okSoft },
  };
  return meta[category.key] || { label: "Thông tin", fg: color.muted, bg: color.canvas };
}

function reportScope(category) {
  const scope = category.items.find((item) => item?.scope)?.scope;
  return { unit: "căn hộ", area: "phân khu", project: "dự án", portfolio: "danh mục" }[scope] || "danh mục";
}

function reportConfidence(category) {
  const values = category.items.map((item) => item?.confidence).filter(Boolean);
  if (!values.length) return null;
  if (category.key === "missing_score" || category.key === "partial_score" || values.includes("low")) return "Hạn chế do thiếu dữ liệu";
  if (values.includes("medium")) return "Trung bình";
  return "Cao";
}

function reportConclusion(category) {
  const signals = flattenReportSignals(category.items);
  if (category.key === "missing_score") return "Chưa có điểm AHP được lưu cho phạm vi đang xem; không nên dùng score để quyết định ưu tiên cho đến khi ranking hoàn tất.";
  if (category.key === "partial_score") return "Kết quả xếp hạng chưa đủ độ phủ để so sánh toàn diện các căn trong phạm vi này.";
  if (category.key === "opportunity") return "Nhóm căn này có điểm AHP tương đối tốt trong phạm vi dữ liệu hiện tại và phù hợp để đội sales ưu tiên xem xét.";
  if (category.key === "inventory_risk" && signals.some((signal) => /velocity-zero/.test(signal.ruleId || ""))) {
    return `Chưa ghi nhận giao dịch sold trong cửa sổ 30 ngày ở một phần phạm vi; ${category.count} mục đang cần được kiểm tra thêm.`;
  }
  if (category.key === "inventory_risk") return "Tồn kho hoặc nhịp hấp thụ đang phát tín hiệu cần đánh giá; đây không phải kết luận về khả năng bán chắc chắn.";
  return "Phạm vi đang có tín hiệu cần theo dõi từ dữ liệu ranking hoặc hấp thụ hiện có.";
}

function reportFindingRows(category) {
  const signals = flattenReportSignals(category.items);
  const rows = [];
  const push = (title, value, interpretation, marker = "•") => {
    if (rows.some((row) => row.title === title)) return;
    const shown = displayEvidenceValue(value);
    rows.push({ title, value: shown == null || shown === "" ? "Chưa có dữ liệu" : String(shown), interpretation, marker });
  };
  if (category.key === "partial_score") {
    const coverage = coverageFromSignal(category.items[0]);
    if (coverage) push("Độ phủ điểm AHP", `${coverage.scored}/${coverage.total} căn`, "Một phần dữ liệu chưa hoàn chỉnh để so sánh toàn diện.", "◐");
  }
  if (category.key === "missing_score") push("Trạng thái điểm AHP", "Chưa có dữ liệu", "Chưa có hierarchical score được lưu cho phạm vi này.", "!");
  for (const signal of signals) {
    if (isForecastSignal(signal)) continue;
    const rule = String(signal.ruleId || "");
    const evidence = signal.evidence || {};
    if (/velocity-zero/.test(rule)) push("Hoạt động bán hàng", evidence.currentValue, "Chưa ghi nhận giao dịch trong cửa sổ 30 ngày; đây là số quan sát được, không phải kết luận khả năng hấp thụ.", "◷");
    else if (/velocity-decreasing/.test(rule)) push("Vận tốc bán", evidence.currentValue, "Nhịp bán 7 ngày thấp hơn nền 30 ngày của phạm vi.", "↓");
    else if (/sellout-horizon/.test(rule)) push("Tầm nhìn tồn kho", evidence.currentValue, "Con số được đọc từ tồn kho và vận tốc đã xảy ra; hệ thống chưa có mốc mục tiêu.", "◇");
    else if (/low-band-units/.test(rule)) push("Căn ở mức xếp hạng thấp", signal.affectedUnits == null ? evidence.currentValue : `${signal.affectedUnits} căn`, "Đây là số căn ở band thấp trong phạm vi dự án, không phải điểm của cả dự án.", "↓");
    else if (/skipped-units/.test(rule)) push("Độ phủ điểm AHP", evidence.baselineValue, "Một phần căn chưa có điểm để so sánh toàn diện.", "◐");
    else if (/freshness|data-status|unavailable/.test(rule)) push("Tình trạng dữ liệu", evidence.currentValue, "Báo cáo dựa trên trạng thái dữ liệu hiện có và cần được kiểm tra trước khi kết luận.", "!");
    else if (/opportunity/.test(rule)) push("Điểm AHP", evidence.currentValue, "Điểm được lấy từ ranking đã lưu trong phạm vi hiện tại.", "↑");
  }
  if (!rows.length) push("Bằng chứng hiện có", null, "Không có thêm chỉ số phù hợp để hiển thị trong phạm vi này.");
  return rows.slice(0, 5);
}

function displayEvidenceValue(value) {
  if (value === null || value === undefined || value === "") return value;
  const text = String(value);
  if (/^HTTP\s+\d+/i.test(text) || /lỗi mạng/i.test(text)) return "Không đọc được dữ liệu";
  if (text === "NOT_IMPLEMENTED") return "Chưa triển khai";
  if (text === "no_data") return "Chưa có dữ liệu";
  if (text === "no_units") return "Chưa có căn trong phạm vi";
  return value;
}

function displaySignalTitle(signal) {
  const rule = String(signal?.ruleId || "");
  if (/ranking:(unavailable|never-computed)/.test(rule)) return "Xếp hạng chưa sẵn sàng";
  if (/absorption:unavailable/.test(rule)) return "Dữ liệu hấp thụ chưa sẵn sàng";
  return signal.title;
}

function displaySignalReason(signal) {
  const rule = String(signal?.ruleId || "");
  if (/ranking:never-computed/.test(rule)) return "Chưa có điểm AHP được lưu cho phạm vi này.";
  if (/ranking:unavailable/.test(rule)) return "Không đọc được dữ liệu ranking ở lần tải này.";
  if (/absorption:unavailable|absorption:data-status|absorption:freshness/.test(rule)) return "Dữ liệu hấp thụ chưa sẵn sàng hoặc cần được cập nhật.";
  if (/velocity-zero/.test(rule)) return "Chưa ghi nhận giao dịch trong cửa sổ 30 ngày; đây là số quan sát được.";
  if (/velocity-decreasing/.test(rule)) return "Nhịp bán 7 ngày thấp hơn nền 30 ngày của phạm vi.";
  if (/low-band-units/.test(rule)) return "Các căn này đang ở band xếp hạng thấp trong phạm vi dự án.";
  if (/skipped-units/.test(rule)) return "Một phần căn chưa có điểm để so sánh toàn diện.";
  return signal.whatHappened || "Không có mô tả tín hiệu.";
}

function AttentionCategoryDetail({ category, onClear }) {
  const badge = reportBadge(category);
  const confidence = reportConfidence(category);
  const findings = reportFindingRows(category);
  const coverage = category.key === "partial_score" ? coverageFromSignal(category.items[0]) : null;
  const affectedItems = category.items.filter((signal) => !isForecastSignal(signal));
  return <section style={S.categoryDetail} data-testid="attention-category-detail" aria-live="polite">
    <div style={S.reportHeader}><div><div style={S.cardEyebrow}>BÁO CÁO TÍN HIỆU</div><h3 id="attention-report-title" style={S.categoryTitle}>Báo cáo: {category.label}</h3><p style={S.reportMeta}>Phạm vi {reportScope(category)} · {category.count} mục bị ảnh hưởng</p></div><div style={S.reportHeaderActions}><span style={{ ...S.signalSeverity, color: badge.fg, background: badge.bg }}>{badge.label}</span><button type="button" style={S.clearButton} onClick={onClear}>Xóa bộ lọc</button></div></div>
    <section style={S.reportConclusion} aria-labelledby="report-conclusion-title"><h4 id="report-conclusion-title" style={S.reportSectionTitle}>Kết luận nhanh</h4><p style={S.reportConclusionText}>{reportConclusion(category)}</p>{coverage ? <p style={S.reportConfidence}>Phủ điểm AHP: {coverage.scored}/{coverage.total} căn · Một số căn chưa có đủ dữ liệu để so sánh toàn diện.</p> : null}{confidence ? <p style={S.reportConfidence}>Độ tin cậy: <strong>{confidence}</strong></p> : null}</section>
    <section style={S.reportSection} aria-labelledby="report-findings-title"><h4 id="report-findings-title" style={S.reportSectionTitle}>Phát hiện chính</h4><div style={S.findingGrid}>{findings.map((finding) => <div key={finding.title} style={S.findingRow}><span style={S.findingMarker} aria-hidden="true">{finding.marker}</span><div><strong style={S.findingTitle}>{finding.title}</strong><strong style={S.findingValue}>{finding.value}</strong><p style={S.findingInterpretation}>{finding.interpretation}</p></div></div>)}</div></section>
    <section style={S.reportCaveat} aria-labelledby="report-caveat-title"><h4 id="report-caveat-title" style={S.reportSectionTitle}>Lưu ý về dữ liệu</h4><p>{category.key === "partial_score" ? "Một số căn chưa có đủ điểm AHP; không nên so sánh trực tiếp các căn có độ phủ dữ liệu khác nhau." : category.key === "inventory_risk" ? "Số liệu về giao dịch phản ánh những gì đã được ghi nhận trong cửa sổ dữ liệu; đây là tín hiệu cần kiểm tra thêm, không phải kết luận rằng thị trường không có khả năng hấp thụ." : category.key === "missing_score" ? "Chưa đủ dữ liệu để đưa ra kết luận đáng tin cậy về thứ tự ưu tiên." : "Báo cáo dựa trên các bằng chứng ranking và hấp thụ hiện có trong lần tải này."}</p></section>
    <section style={S.reportSection} aria-labelledby="report-items-title"><h4 id="report-items-title" style={S.reportSectionTitle}>Mục bị ảnh hưởng</h4><div style={S.detailList}>{affectedItems.slice(0, 5).map((signal) => <AttentionDetailItem key={signal.id} signal={signal} />)}</div>{category.count > 5 ? <p style={S.detailMore}>Hiển thị 5 mục đầu tiên trong nhóm này.</p> : null}</section>
  </section>;
}

function AttentionDetailItem({ signal }) {
  const evidence = signal.evidence || {};
  const children = (Array.isArray(signal.children) ? signal.children : []).filter((child) => !isForecastSignal(child));
  const statusLabel = { open: "Đang mở", acknowledged: "Đã ghi nhận", investigating: "Đang điều tra", resolved: "Đã xử lý", dismissed: "Đã bỏ qua" }[signal.status];
  const confidenceLabel = { high: "Cao", medium: "Trung bình", low: "Thấp" }[signal.confidence];
  const [expanded, setExpanded] = useState(Boolean(children.length || Object.keys(evidence).length));
  const reason = signal.affectedProjectCount
    ? `${signal.affectedProjectCount} dự án cùng phát tín hiệu này trong lần tải hiện tại.`
    : displaySignalReason(signal);
  const scopeLabel = { unit: "Căn hộ", area: "Phân khu", project: "Dự án", portfolio: "Danh mục" }[signal.scope] || "Mục dữ liệu";
  return <article role="listitem" data-testid={`signal-${signal.id}`} style={S.signalRow} aria-label={`${displaySignalTitle(signal)}. ${reason}`}>
    <button type="button" style={S.itemToggle} aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><span style={S.signalTop}><span style={S.signalHeading}><span style={S.signalScope}>{scopeLabel}</span><strong style={S.signalTitle}>{displaySignalTitle(signal)}</strong></span><span style={S.signalMeta}><span style={{ ...S.signalSeverity, color: signalTone(signal.severity).fg, background: signalTone(signal.severity).bg }}>{signalTone(signal.severity).label}</span><span style={S.signalValue}>{String(displayEvidenceValue(evidence.currentValue ?? (signal.affectedUnits == null ? "—" : `${signal.affectedUnits} căn`)))}</span></span><span style={S.itemChevron} aria-hidden="true">{expanded ? "⌃" : "⌄"}</span></span></button>
    {expanded ? <><p style={S.signalReason}>{reason}</p><dl style={S.signalFacts}><Fact label="Giá trị hiện tại" value={evidence.currentValue} /><Fact label="Giá trị nền" value={evidence.baselineValue} /><Fact label="Chênh lệch" value={evidence.delta} /><Fact label="Mục tiêu / mốc" value={evidence.threshold} /><Fact label="Độ phủ" value={evidence.scoreCoverage ?? evidence.coverage} /><Fact label="Trạng thái" value={statusLabel || signal.status} /><Fact label="Độ tin cậy" value={confidenceLabel || signal.confidence} /><Fact label="Số căn ảnh hưởng" value={signal.affectedUnits} /></dl><p style={S.signalCaveat}>{signal.whyItMatters || "Đọc tín hiệu cùng phạm vi và độ đầy đủ dữ liệu."}</p>{children.length ? <div data-testid={`signal-children-${signal.id}`} style={S.signalChildren}>{children.slice(0, 5).map((child) => <AttentionDetailItem key={child.id} signal={child} />)}</div> : null}{signal.affectedProjectCount ? <span data-testid={`signal-aggregate-${signal.id}`} style={S.aggregateLabel}>{signal.affectedProjectCount} dự án bị ảnh hưởng</span> : null}</> : null}
  </article>;
}

function isForecastSignal(signal) {
  return /^forecasting:/.test(String(signal?.ruleId || ""));
}

function Fact({ label, value }) {
  if (value === null || value === undefined || value === "") return null;
  return <div style={S.signalFact}><dt style={S.signalFactLabel}>{label}</dt><dd style={S.signalFactValue}>{String(displayEvidenceValue(value))}</dd></div>;
}

function signalTone(severity) {
  if (severity === "critical") return { fg: color.danger, bg: color.dangerSoft, label: "Nghiêm trọng" };
  if (severity === "warning") return { fg: color.warn, bg: color.warnSoft, label: "Cảnh báo" };
  return { fg: color.muted, bg: color.canvas, label: "Thông tin" };
}

const S = {
  header: { marginBottom: space(6) },
  eyebrow: {
    fontSize: 10, fontWeight: 700, letterSpacing: ".14em",
    textTransform: "uppercase", color: color.accent, marginBottom: space(2),
  },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0 },
  sub: { fontSize: size.small, color: color.muted, margin: `${space(2)}px 0 0`, maxWidth: "72ch" },

  card: {
    background: color.surface, border: `1px solid ${color.border}`,
    borderRadius: radius.lg, boxShadow: shadow,
    padding: space(5), marginBottom: space(5),
  },
  cardHeader: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: space(4), flexWrap: "wrap", marginBottom: space(4) },
  cardEyebrow: {
    fontSize: 10, fontWeight: 700, letterSpacing: ".12em",
    textTransform: "uppercase", color: color.muted, marginBottom: space(1),
  },
  sectionTitle: { fontFamily: font.display, fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0 },
  chartSubtitle: { margin: `${space(2)}px 0 0`, color: color.muted, fontSize: size.small },
  scopeBadge: { color: color.muted, background: color.canvas, borderRadius: radius.pill, padding: "4px 10px", fontSize: size.tiny, fontWeight: 700, whiteSpace: "nowrap" },
  circularLayout: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: space(5), alignItems: "start" },
  circularVisual: { minWidth: 0 },
  donutWrap: { display: "grid", justifyItems: "center", alignContent: "start", gap: space(2) },
  donut: { display: "block", width: "min(100%, 230px)", height: "auto", overflow: "visible" },
  donutCount: { fontFamily: font.display, fontSize: 28, fontWeight: 800, fill: color.ink },
  donutLabel: { fontSize: 10, fontWeight: 700, fill: color.muted },
  donutHint: { margin: 0, color: color.muted, fontSize: size.tiny, textAlign: "center" },
  chartLegend: { display: "grid", gap: space(2), marginBottom: space(4), color: color.muted, fontSize: size.tiny },
  legendDot: { display: "inline-block", width: 8, height: 8, borderRadius: radius.pill, marginRight: 6 },
  legendButton: { display: "flex", alignItems: "flex-start", gap: space(2), width: "100%", minHeight: 44, padding: space(2), border: `1px solid transparent`, borderRadius: radius.md, background: "transparent", color: color.body, textAlign: "left", cursor: "pointer", fontFamily: "inherit" },
  legendButtonSelected: { border: `1px solid ${color.border}`, background: color.canvas },
  legendButtonText: { display: "grid", gap: 2 },
  legendButtonSmall: { color: color.muted, fontSize: size.tiny, fontWeight: 400 },
  categoryDetail: { borderTop: `1px solid ${color.border}`, paddingTop: space(4) },
  modalBackdrop: { position: "fixed", inset: 0, zIndex: 1000, display: "grid", placeItems: "center", padding: space(3), background: "rgba(15, 23, 42, .46)" },
  modal: { width: "min(760px, 100%)", maxHeight: "calc(100vh - 24px)", overflowY: "auto", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.lg, boxShadow: "0 24px 70px rgba(15, 23, 42, .24)", padding: space(5), outline: "none" },
  modalHeader: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: space(3) },
  modalClose: { display: "grid", placeItems: "center", width: 36, height: 36, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, color: color.ink, cursor: "pointer", fontFamily: "inherit", fontSize: 24, lineHeight: 1 },
  categoryDetailHeader: { display: "flex", justifyContent: "space-between", gap: space(3), flexWrap: "wrap", marginBottom: space(3) },
  reportHeader: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), flexWrap: "wrap", marginBottom: space(4) },
  reportHeaderActions: { display: "flex", alignItems: "center", flexWrap: "wrap", gap: space(2) },
  reportMeta: { margin: `${space(1)}px 0 0`, color: color.muted, fontSize: size.tiny },
  reportConclusion: { borderLeft: `3px solid ${color.accent}`, padding: `${space(3)}px ${space(4)}px`, marginBottom: space(4), background: color.canvas, borderRadius: `0 ${radius.md} ${radius.md} 0` },
  reportSection: { borderTop: `1px solid ${color.border}`, paddingTop: space(4), marginTop: space(4) },
  reportSectionTitle: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.small, fontWeight: 800 },
  reportConclusionText: { margin: `${space(2)}px 0 0`, color: color.ink, fontSize: size.small, lineHeight: 1.6 },
  reportConfidence: { margin: `${space(2)}px 0 0`, color: color.muted, fontSize: size.tiny },
  findingGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: space(3), marginTop: space(3) },
  findingRow: { display: "grid", gridTemplateColumns: "20px minmax(0, 1fr)", gap: space(2), padding: space(3), border: `1px solid ${color.border}`, borderRadius: radius.md, background: color.surface },
  findingMarker: { color: color.accent, fontWeight: 800, fontSize: size.small },
  findingTitle: { display: "block", color: color.muted, fontSize: size.tiny, fontWeight: 700 },
  findingValue: { display: "block", marginTop: 2, color: color.ink, fontFamily: font.display, fontSize: size.small },
  findingInterpretation: { margin: `${space(1)}px 0 0`, color: color.body, fontSize: size.tiny, lineHeight: 1.5 },
  reportCaveat: { marginTop: space(4), padding: space(3), borderLeft: `3px solid ${color.warn}`, borderRadius: `0 ${radius.md} ${radius.md} 0`, background: color.warnSoft, color: color.body, fontSize: size.tiny, lineHeight: 1.5 },
  categoryTitle: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  categoryDescription: { margin: `${space(1)}px 0 0`, color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  signalMeaning: { margin: `${space(2)}px 0 0`, color: color.body, fontSize: size.tiny, lineHeight: 1.5 },
  clearButton: { alignSelf: "start", flex: "0 0 auto", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, color: color.accent, padding: "7px 10px", cursor: "pointer", fontFamily: "inherit", fontSize: size.tiny, fontWeight: 700 },
  detailList: { display: "grid", gap: space(3) },
  detailMore: { margin: `${space(3)}px 0 0`, color: color.muted, fontSize: size.tiny },
  emptyCircular: { display: "grid", justifyItems: "center", gap: space(2), padding: `${space(4)}px 0`, textAlign: "center", color: color.muted },
  emptyDonut: { display: "grid", placeItems: "center", width: 132, height: 132, border: `18px solid ${color.border}`, borderRadius: "50%", color: color.ink, fontFamily: font.display, fontSize: 28, fontWeight: 800 },
  circularLoading: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: space(5), alignItems: "center" },
  loadingDonut: { display: "grid", placeItems: "center" },
  loadingAside: { display: "grid", gap: space(3) },
  signalRow: { minWidth: 0, borderTop: `1px solid ${color.border}`, padding: `${space(3)}px 0 0` },
  itemToggle: { display: "block", width: "100%", minWidth: 0, minHeight: 44, padding: 0, border: 0, background: "transparent", color: color.body, textAlign: "left", cursor: "pointer", fontFamily: "inherit" },
  itemChevron: { gridColumn: 2, gridRow: "1 / span 2", alignSelf: "center", color: color.muted, fontSize: size.small, fontWeight: 800 },
  signalTop: { display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", alignItems: "center", gap: space(1), width: "100%", minWidth: 0 },
  signalHeading: { minWidth: 0, display: "grid", gap: 2 },
  signalScope: { color: color.muted, fontSize: size.tiny, lineHeight: 1.3, textTransform: "uppercase", letterSpacing: ".05em", fontWeight: 700, overflowWrap: "anywhere" },
  signalTitle: { minWidth: 0, color: color.ink, fontSize: size.small, lineHeight: 1.35, whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" },
  signalMeta: { gridColumn: 1, minWidth: 0, display: "flex", alignItems: "center", justifyContent: "flex-start", flexWrap: "wrap", gap: space(1) },
  signalSeverity: { padding: "3px 8px", borderRadius: radius.pill, fontSize: size.tiny, fontWeight: 700, whiteSpace: "nowrap" },
  signalValue: { minWidth: 0, maxWidth: "18ch", color: color.ink, fontSize: size.tiny, fontWeight: 800, fontVariantNumeric: "tabular-nums", lineHeight: 1.35, textAlign: "right", overflowWrap: "anywhere", wordBreak: "break-word" },
  signalReason: { margin: `${space(3)}px 0 0`, padding: `${space(2)}px ${space(3)}px`, borderLeft: `3px solid ${color.accent}`, borderRadius: `0 ${radius.md} ${radius.md} 0`, background: color.canvas, color: color.body, fontSize: size.tiny, lineHeight: 1.6, overflowWrap: "anywhere" },
  signalFacts: { display: "grid", gap: space(2), margin: `${space(3)}px 0` },
  signalFact: { display: "grid", gridTemplateColumns: "minmax(110px, 34%) minmax(0, 1fr)", alignItems: "start", columnGap: space(2), minWidth: 0 },
  signalFactLabel: { margin: 0, color: color.muted, fontSize: size.tiny, lineHeight: 1.45, fontWeight: 600, overflowWrap: "anywhere" },
  signalFactValue: { minWidth: 0, margin: 0, color: color.ink, fontSize: size.tiny, lineHeight: 1.45, overflowWrap: "anywhere", wordBreak: "break-word" },
  signalCaveat: { margin: `${space(2)}px 0`, color: color.muted, lineHeight: 1.5 },
  signalChildren: { display: "grid", gap: space(1), marginBottom: space(2), color: color.body },
  aggregateLabel: { color: color.muted, fontWeight: 700 },
  signalLoading: { display: "grid", gap: space(3) },
  chartEmpty: { margin: 0, color: color.muted, fontSize: size.small },
  retryButton: { marginTop: space(3), border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, padding: "7px 10px", cursor: "pointer", fontFamily: "inherit" },
  chartPolicy: { margin: `${space(4)}px 0 0`, color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  readonlyNotice: { margin: `${space(2)}px 0 0`, color: color.muted, fontSize: size.tiny },

  kpiGrid: {
    display: "grid", gap: space(3), margin: 0,
    gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  },
  kpi: {
    border: `1px solid ${color.border}`, borderRadius: radius.md,
    background: color.canvas, padding: space(4),
  },
  kpiIcon: {
    display: "grid", placeItems: "center", width: 28, height: 28,
    borderRadius: radius.sm, background: color.accentSoft, marginBottom: space(2),
  },
  kpiLabel: {
    fontSize: size.tiny, fontWeight: 600, color: color.muted,
    textTransform: "uppercase", letterSpacing: ".05em", margin: 0,
  },
  kpiValue: {
    fontFamily: font.display, fontSize: 24, lineHeight: 1.15,
    fontWeight: 700, color: color.ink, margin: `${space(1)}px 0 0`,
  },

  stack: { display: "grid", gap: space(5) },
};
