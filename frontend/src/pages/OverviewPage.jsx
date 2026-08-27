// frontend/src/pages/OverviewPage.jsx
// Tổng quan DANH MỤC — sống ở /overview.
//
// KHÁC HẲN dashboard của MỘT dự án (/projects/:externalId/dashboard, xem
// pages/ProjectDashboardPage.jsx). Trang này KHÔNG dùng AbsorptionDashboard:
// không có bộ lọc dự án/phân khu, không có chuỗi hấp thụ của một dự án, không
// có bảng phân khu. Nó trả lời ba câu hỏi ở cấp danh mục:
//   1. Toàn hệ thống đang có bao nhiêu dự án/phân khu/căn/deal (KPI danh mục).
//   2. Chỗ nào cần chú ý, xuyên dự án (Signals Center).
//   3. Căn nào đáng ưu tiên nhất, bất kể thuộc dự án nào (xếp hạng căn toàn cục).
//
// Fan-out CÓ TRẦN: backend chỉ có endpoint hấp thụ/xếp hạng theo TỪNG dự án,
// nên trang tự đặt trần request và NÓI RA phần chưa quét — im lặng ở đây sẽ bị
// đọc thành "không có vấn đề". Hai trần tách riêng (xếp hạng rộng hơn hấp thụ)
// vì kết quả xếp hạng còn được bảng xếp hạng toàn cục dùng lại, còn mỗi lượt
// hấp thụ là một request chỉ phục vụ tín hiệu.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  getAbsorptionSummary,
  getPortfolioSummary,
  getRanking,
  listProjects,
} from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import SignalsCenter from "../components/dashboard/SignalsCenter";
import GlobalUnitRanking from "../components/dashboard/GlobalUnitRanking";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import Icon from "../components/ui/Icon";
import { EmptyState, SectionState, Skeleton, fmt } from "../components/ui/States";
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

      <section style={S.card} aria-labelledby="portfolio-trend-title">
        <div style={S.cardHeader}>
          <div>
            <div style={S.cardEyebrow}>PORTFOLIO TREND</div>
            <h2 id="portfolio-trend-title" style={S.sectionTitle}>Xu hướng hấp thụ toàn danh mục</h2>
          </div>
        </div>
        <EmptyState
          icon="rate"
          title="Chưa có dữ liệu xu hướng tổng hợp đa dự án"
          hint="Backend mới có chuỗi hấp thụ theo TỪNG dự án. Ghép các chuỗi đó ở frontend sẽ vẽ ra một đường không đo được từ nguồn nào — mở dashboard của một dự án để xem xu hướng của dự án đó."
          compact
        />
      </section>

      <div style={S.stack}>
        {/* Lỗi nguồn KHÔNG đẩy Signals Center vào trạng thái lỗi: chúng đã là
            tín hiệu có bằng chứng, hiện cùng hàng với các tín hiệu khác. */}
        <SignalsCenter signals={signals} loading={loading} />

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
  cardHeader: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: space(4), marginBottom: space(4) },
  cardEyebrow: {
    fontSize: 10, fontWeight: 700, letterSpacing: ".12em",
    textTransform: "uppercase", color: color.muted, marginBottom: space(1),
  },
  sectionTitle: { fontFamily: font.display, fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0 },

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
