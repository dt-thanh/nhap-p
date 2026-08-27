// frontend/src/pages/RankingPage.jsx
// Bảng xếp hạng căn — đọc kết quả và tự động chạy lại qua `POST /ranking/run`
// khi người dùng đổi phạm vi.
//
// Nguyên tắc trình bày của trang này:
//
// 1. **Điểm không bao giờ đứng một mình.** Mỗi dòng mở ra được phần "vì sao":
//    từng đặc trưng, trọng số, và phần điểm nó đóng góp — lấy thẳng từ
//    `ranking_scores.contributions`. Một con số 65.9% không nói cho đội bán hàng
//    biết phải làm gì; "nhu cầu 0.33 × 0.25" thì có.
//
// 2. Việc chọn phạm vi là hành động đủ rõ để tự động tính lại xếp hạng. Mỗi
//    lần chạy vẫn đi qua endpoint có phân quyền; đề xuất bán hàng không được
//    tạo ra từ đây và vẫn giữ nguyên human-in-the-loop boundary.
//
// 3. **Mức (`band`) hiển thị đúng như backend trả về.** Ngưỡng nằm ở
//    `src/ranking/bands.py`; tính lại ngưỡng ở đây sẽ tạo bản sao thứ hai của
//    một quy tắc nghiệp vụ và hai bên sẽ lệch nhau.
//
// 4. **Miễn trừ trách nhiệm luôn hiện.** Backend gửi kèm chuỗi cố định đó ở mọi
//    phản hồi; AGENTS.md coi xếp hạng là đầu vào cho người quyết định, không
//    phải cam kết kết quả bán hàng.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as rankingApi from "../api/endpoints";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { useProjectScope } from "../hooks/useProjectScope";
import ProjectSelector from "../components/ProjectSelector";
import DemandChart from "../components/DemandChart";
import RankingGroupTable from "../components/ranking/RankingGroupTable";
import { DataReliability, RankingDecisionHeader } from "../components/ranking/RankingDecisionHeader";
import RankingSearchBar, { filterRankingUnits } from "../components/RankingSearchBar";
import EmptyState from "../components/EmptyState";
import { RankingSkeleton } from "../components/RankingSkeleton";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { SectionState, fmt } from "../components/ui/States";
import { areaLabel } from "../utils/areaLabel";
import { color, font, radius, shadow, size, space } from "../styles/tokens";
import "./RankingPage.css";

const PAGE_SIZE = 50;

const getRanking = (...args) => rankingApi.getRanking(...args);
const runRanking = (...args) => rankingApi.runRanking(...args);

const BAND_STYLE = {
  high: { label: "Hot", threshold: "Điểm ≥ 66%", fg: color.ok, bg: color.okSoft },
  medium: { label: "Normal", threshold: "Điểm 33–65,9%", fg: color.warn, bg: color.warnSoft },
  low: { label: "Slow", threshold: "Điểm < 33%", fg: color.muted, bg: color.canvas },
};

const UNIT_STATUS_LABEL = {
  available: "Còn trống",
  reserved: "Đang giữ",
  sold: "Đã bán",
  blocked: "Tạm khoá",
};

// Tên đặc trưng bằng tiếng Việt cho phần giải thích. Khoá nào chưa có ở đây thì
// hiện nguyên khoá kỹ thuật — thà lộ `unit_demand_norm` còn hơn giấu mất một
// đặc trưng vừa được thêm vào config mà bảng này chưa kịp cập nhật.
const FEATURE_LABEL = {
  unit_available: "Căn còn trống",
  unit_demand_norm: "Nhu cầu trên căn (deal đang trong phễu)",
  has_active_deal: "Đang có giao dịch giữ căn",
  area_velocity_norm: "Tốc độ bán của phân khu (30 ngày)",
  area_conversion_norm: "Tỉ lệ chốt của phân khu",
};

/** Chuỗi Decimal của backend dài tới 28 chữ số (`0.3333333333333333333333333333`).
 *  Hiển thị nguyên văn là vô nghĩa với người đọc; cắt còn 2 chữ số thập phân. */
function decimal(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function freshness(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const minutes = Math.round((Date.now() - then.getTime()) / 60000);
  if (minutes < 1) return "vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  return `${Math.round(hours / 24)} ngày trước`;
}

function rankingErrorMessage(error) {
  const raw = String(error?.message || "").trim();
  const normalized = raw.toLowerCase();
  if (error?.status === 401 || normalized.includes("access denied") || normalized.includes("unauthorized")) {
    return "Từ chối truy cập";
  }
  if (error?.status === 404 || normalized.includes("project not found")) return "Không tìm thấy dự án";
  if (normalized.includes("area not found")) return "Không tìm thấy phân khu";
  if (normalized.includes("invalid project")) return "Dự án không hợp lệ";
  if (normalized.includes("invalid area")) return "Phân khu không hợp lệ";
  if (normalized.includes("network") || error?.status === 0) return "Lỗi kết nối. Vui lòng thử lại";
  if (normalized.includes("failed to load ranking")) return "Không thể tải xếp hạng. Vui lòng thử lại";
  return raw || "Không thể tải xếp hạng. Vui lòng thử lại";
}

export default function RankingPage({ projectExternalId }) {
  return (
    <div
      className="ranking-page"
      style={{
        "--ranking-canvas": color.canvas,
        "--ranking-surface": color.surface,
        "--ranking-border": color.border,
        "--ranking-border-strong": color.borderStrong,
        "--ranking-ink": color.ink,
        "--ranking-body": color.body,
        "--ranking-muted": color.muted,
        "--ranking-accent": color.accent,
        "--ranking-accent-soft": color.accentSoft,
      }}
    >
      <div
        className="ranking-tab-panel"
        id="ranking-panel-hot-units"
        role="tabpanel"
        aria-label="Xếp hạng căn"
      >
        <HotUnitsTab projectExternalId={projectExternalId} />
      </div>
    </div>
  );
}

// The existing unit-ranking implementation is intentionally kept intact in
// this extracted tab component. `components/HotUnitsTab.jsx` re-exports it
// for consumers/tests that use the component-level path.
export function HotUnitsTab({ projectExternalId: routeProjectExternalId } = {}) {
  const navigate = useNavigate();
  const scope = useProjectScope({ projectExternalId: routeProjectExternalId });
  const { isMobile } = useBreakpoint();
  const [availableOnly, setAvailableOnly] = useState(true);
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState(null);
  const [rankingData, setRankingData] = useState(null);
  const [rankingLoading, setRankingLoading] = useState(false);
  const [rankingError, setRankingError] = useState(null);
  const [rankingSearch, setRankingSearch] = useState("");
  const [selectedDemandLevel, setSelectedDemandLevel] = useState(null);
  const [selectedGroupKey, setSelectedGroupKey] = useState(null);
  const [decisionData, setDecisionData] = useState({ summary: null, trend: null, quality: null, loading: false });
  const requestId = useRef(0);

  const projectId = scope.projectExternalId;
  const areaId = scope.areaExternalId;
  const projectUuid = scope.currentProject?.project_id;
  const areaUuid = scope.currentArea?.area_id;
  const defaultsProjectHandled = useRef(false);
  const defaultsAreaHandled = useRef(false);

  // Preserve deep links, but make a first visit useful immediately: choose the
  // first scoped project, then its first scoped area once that list arrives.
  useEffect(() => {
    if (defaultsProjectHandled.current || scope.loadingProjects) return;
    defaultsProjectHandled.current = true;
    const firstProject = scope.projects.find((project) => project.external_id);
    if (!projectId && firstProject) {
      scope.setProjectExternalId(firstProject.external_id);
      defaultsAreaHandled.current = false;
    }
  }, [projectId, scope]);

  useEffect(() => {
    if (defaultsAreaHandled.current || scope.loadingAreas || !projectId || areaId || scope.areas.length === 0) return;
    const firstArea = scope.areas.find((area) => area.external_id);
    if (!firstArea) return;
    defaultsAreaHandled.current = true;
    scope.setAreaExternalId(firstArea.external_id);
  }, [areaId, projectId, scope]);

  // Đổi phạm vi thì quay về trang đầu và bỏ bộ lọc nhu cầu cũ. Bộ lọc còn
  // trống được giữ độc lập để có thể kết hợp với mức độ quan tâm.
  useEffect(() => {
    setOffset(0);
    setExpanded(null);
    setRankingSearch("");
    setSelectedDemandLevel(null);
    setSelectedGroupKey(null);
  }, [projectId, areaId]);

  // Keep the existing availability-filter behavior: changing it starts the
  // read-only request from page one and clears the text search, while leaving
  // an active demand row selected so the two filters remain combinable.
  useEffect(() => {
    setOffset(0);
    setExpanded(null);
    setRankingSearch("");
    setSelectedGroupKey(null);
  }, [availableOnly]);

  // Demand bands are filtered by the paginated ranking endpoint, so changing
  // the band must start from the first matching page.
  useEffect(() => {
    setOffset(0);
    setExpanded(null);
    setSelectedGroupKey(null);
  }, [selectedDemandLevel]);

  const prevScopeKey = useRef(null);

  // Only a project/area change is the recalculation action (`POST /ranking/run`,
  // which replaces `ranking_scores` for the project). "Chỉ còn trống" and
  // paging are read-only refinements over those SAME stored scores, so they
  // must go through `GET /ranking` instead — the only path that actually
  // forwards `unit_status`/`limit`/`offset` to the backend.
  const fetchRanking = useCallback(async (recompute) => {
    if (!projectId) {
      setRankingData(null);
      setRankingLoading(false);
      return;
    }
    const currentRequest = ++requestId.current;
    setRankingLoading(true);
    setRankingError(null);
    try {
      const scopeParams = areaId ? { external_area_id: areaId } : {};
      let data;
      if (recompute) {
        data = await runRanking(projectId, scopeParams);
        // POST /ranking/run deliberately returns the unfiltered first page.
        // Read it again under the same availability/band scope used by the
        // table and chart so the initial response cannot mix scopes.
        if (availableOnly || selectedDemandLevel) {
          data = await getRanking(projectId, {
            ...scopeParams,
            limit: PAGE_SIZE,
            offset: 0,
            ...(availableOnly ? { unit_status: "available" } : {}),
            ...(selectedDemandLevel ? { band: selectedDemandLevel } : {}),
          });
        }
      } else {
        data = await getRanking(projectId, {
          ...scopeParams,
          limit: PAGE_SIZE,
          offset,
          ...(availableOnly ? { unit_status: "available" } : {}),
          ...(selectedDemandLevel ? { band: selectedDemandLevel } : {}),
        });
      }
      if (currentRequest === requestId.current) setRankingData(data);
    } catch (error) {
      if (currentRequest !== requestId.current) return;
      setRankingError({ message: rankingErrorMessage(error), status: error?.status });
    } finally {
      if (currentRequest === requestId.current) setRankingLoading(false);
    }
  }, [projectId, areaId, offset, availableOnly, selectedDemandLevel]);

  useEffect(() => {
    if (!projectId) {
      setRankingData(null);
      setRankingLoading(false);
      return undefined;
    }
    // Wait for the dependent area list so the initial project selection and
    // first-area default collapse into one recalculation.
    if (scope.loadingAreas) {
      setRankingLoading(true);
      return undefined;
    }
    const scopeKey = `${projectId}:${areaId || ""}`;
    const scopeChanged = prevScopeKey.current !== scopeKey;
    prevScopeKey.current = scopeKey;
    if (scopeChanged) setRankingData(null);
    setRankingLoading(true);
    // A nonzero delay in both branches matters, not just for debounce: it lets
    // the offset-reset effect above (which also fires on an availability
    // change) cancel this timer and reschedule with `offset` already back at
    // 0, instead of racing a stale-offset request against the reset one.
    const timer = window.setTimeout(() => fetchRanking(scopeChanged), scopeChanged ? 300 : 150);
    return () => window.clearTimeout(timer);
  }, [projectId, areaId, scope.loadingAreas, availableOnly, offset, fetchRanking]);

  useEffect(() => {
    if (!projectId || !projectUuid || typeof rankingApi.getDashboardSummary !== "function") {
      setDecisionData({ summary: null, trend: null, quality: null, loading: false });
      return undefined;
    }
    let cancelled = false;
    setDecisionData((current) => ({ ...current, loading: true }));
    const loadDecisionData = async () => {
      try {
        const summary = await rankingApi.getDashboardSummary({ projectId: projectUuid, areaId: areaUuid || null });
        const [trend, quality] = await Promise.all([
          typeof rankingApi.getDashboardTrend === "function"
            ? rankingApi.getDashboardTrend({ projectId: projectUuid, areaId: areaUuid || null, areaTotalUnits: summary.total_units, totalSold: summary.units_sold, granularity: "month" })
            : Promise.resolve(null),
          typeof rankingApi.getDataQuality === "function"
            ? rankingApi.getDataQuality({ projectId: projectUuid, externalProjectId: projectId })
            : Promise.resolve(null),
        ]);
        if (!cancelled) setDecisionData({ summary, trend, quality, loading: false });
      } catch (error) {
        if (!cancelled) setDecisionData({ summary: null, trend: null, quality: { status: "error", warnings: [error?.message || "Không thể tải độ tin cậy dữ liệu."] }, loading: false });
      }
    };
    loadDecisionData();
    return () => { cancelled = true; };
  }, [areaUuid, projectId, projectUuid, areaId]);

  const data = rankingData;
  const items = data?.items ?? [];
  const applyRankingSearch = useCallback((searchTerm) => {
    setRankingSearch(searchTerm);
    return filterRankingUnits(items, searchTerm).length;
  }, [items]);
  const searchFilteredItems = useMemo(
    () => filterRankingUnits(items, rankingSearch),
    [items, rankingSearch],
  );
  const groupFilteredItems = selectedGroupKey
    ? searchFilteredItems.filter((unit) => `${unit.area_name || "Phân khu chưa rõ"}::${unit.unit_type || ""}` === selectedGroupKey)
    : searchFilteredItems;
  const visibleItems = groupFilteredItems;
  useEffect(() => {
    setOffset(0);
    setExpanded(null);
  }, [rankingSearch]);
  const total = data?.total ?? 0;
  // Backend owns the distinction between a project that has not run and a
  // completed run with insufficient inputs. Keep the legacy computed_at
  // fallback only for older API deployments during a rolling upgrade.
  const rankingState = data?.state ?? (data?.computed_at == null ? "not_run" : "ready");
  const unavailableRanking = rankingState !== "ready";
  const emptyRankingCopy = rankingState === "insufficient_data"
    ? {
      title: "Chưa có căn đủ điều kiện để xếp hạng",
      message: data?.reason === "NO_LIVE_UNITS"
        ? "Dữ liệu đồng bộ hiện không có căn còn hiệu lực cho dự án này."
        : "Các căn hiện có chưa đạt mức dữ liệu tối thiểu của cấu hình xếp hạng.",
    }
    : {
      title: "Dự án này chưa được xếp hạng lần nào",
      message: "Chọn lại dự án hoặc phân khu để tự động chạy bộ xếp hạng trên dữ liệu mới nhất.",
    };
  const hasNextPage = offset + PAGE_SIZE < total;

  return (
    <>
      <GlobalKeyframes />
      <header style={S.pageHead}>
        <div>
          <span className="ranking-eyebrow">INVESTOR DECISION VIEW</span>
          <h1 className="ranking-title" style={S.h1}>Ranking kinh tế căn hộ</h1>
          <p style={S.sub}>Tốc độ hấp thụ, MOS, rủi ro và nhóm hành động trong một màn hình.</p>
        </div>
        {data?.config_version != null && (
          <button
            style={S.configBadge}
            onClick={() => navigate("/ranking/configs")}
            title="Xem và đổi bộ trọng số xếp hạng"
          >
            cấu hình v{data.config_version} ›
          </button>
        )}
      </header>

      <RankingDecisionHeader
        summary={decisionData.summary}
        trend={decisionData.trend}
        quality={decisionData.quality}
        ranking={data}
        loading={decisionData.loading || rankingLoading}
      />

      <section className="ranking-card" style={S.scopeBar} aria-label="Phạm vi xếp hạng">
        <div className="ranking-controls-row">
          <ProjectSelector
            projects={scope.projects}
            value={projectId}
            onChange={scope.setProjectExternalId}
            loading={scope.loadingProjects}
            status={
              scope.projectsStatus === "unauthorized" ? "unauthorized"
                : scope.projectsStatus === "error" ? "error" : undefined
            }
          />
          {projectId && (
            <label className="ranking-label" style={S.label}>
              Phối cảnh
              <select
                style={S.select}
                value={areaId ?? "all"}
                aria-label="Chọn phân khu"
                onChange={(e) => scope.setAreaExternalId(e.target.value === "all" ? null : e.target.value)}
              >
                <option value="all">Toàn bộ dự án</option>
                {(scope.areas || []).filter((a) => a.external_id).map((a) => (
                  <option key={a.external_id} value={a.external_id}>{areaLabel(a)}</option>
                ))}
              </select>
            </label>
          )}
        </div>
        {projectId && (
          <div className="ranking-controls-status" style={S.runBox}>
            <span style={S.freshness}>
              {data?.computed_at ? `Tính lúc ${freshness(data.computed_at)}` : "Chưa từng tính"}
            </span>
            <span style={S.autoStatus} aria-live="polite">
              {rankingLoading ? <><span className="ranking-spinner" aria-hidden="true" /> Đang tự động cập nhật…</> : "Tự động cập nhật khi đổi phạm vi"}
            </span>
          </div>
        )}
      </section>

      {rankingError && <div style={S.error}>{rankingError.message}</div>}

      {!projectId ? (
        <EmptyState message={scope.loadingProjects ? "Đang tải danh sách dự án…" : "Chưa có dữ liệu xếp hạng"} />
      ) : (
        <>
          {data && !unavailableRanking && (
            <div style={S.summary}>
              <SummaryCard label="Đã xếp hạng" value={fmt(data.units_ranked)} />
              <SummaryCard label="Hot" value={fmt(data.band_counts?.high ?? 0)} tone={color.ok} />
              <SummaryCard label="Normal" value={fmt(data.band_counts?.medium ?? 0)} tone={color.warn} />
              <SummaryCard label="Slow" value={fmt(data.band_counts?.low ?? 0)} tone={color.muted} />
              {/* Bỏ qua vì thiếu dữ liệu — KHÁC với "điểm thấp". Trộn hai thứ này
                  vào một ô sẽ khiến một lỗi dữ liệu trông như một kết luận. */}
              <SummaryCard label="Bỏ qua (thiếu dữ liệu)" value={fmt(data.units_skipped)} tone={color.muted} />
            </div>
          )}

          {data && !unavailableRanking && (
            <RankingGroupTable items={items} selectedKey={selectedGroupKey} onSelect={setSelectedGroupKey} />
          )}

          <label style={S.availabilityFilter}>
            <input type="checkbox" checked={availableOnly} onChange={(e) => setAvailableOnly(e.target.checked)} />
            Chỉ căn còn trống
          </label>

          {data && !unavailableRanking && (
            <details className="ranking-secondary-panel">
              <summary>Phân bố điểm và bộ lọc Risk band</summary>
              <DemandChart
                units={items}
                activeCategory={selectedDemandLevel}
                onCategorySelect={setSelectedDemandLevel}
                categoryCounts={data.band_counts}
              />
            </details>
          )}

          {data && !unavailableRanking && (
            <div className="ranking-list-search">
              <RankingSearchBar
                onFilter={applyRankingSearch}
                totalUnits={items.length}
                resetKey={`${projectId}:${areaId || "all"}:${availableOnly ? "available" : "all"}`}
              />
            </div>
          )}

          {unavailableRanking ? (
            <div style={S.emptyCard}>
              <div style={S.emptyIcon}>▦</div>
              <b style={{ color: color.ink }}>{emptyRankingCopy.title}</b>
              <p style={{ margin: `${space(2)}px 0 ${space(4)}px` }}>
                {emptyRankingCopy.message}
              </p>
            </div>
          ) : (
            <SectionState
              loading={rankingLoading}
              error={rankingError}
              empty={!rankingLoading && !rankingError && visibleItems.length === 0}
              emptyTitle="Không tìm thấy căn nào phù hợp với bộ lọc"
              emptyHint="Thử điều chỉnh từ khoá hoặc các bộ lọc để xem thêm dữ liệu."
              onRetry={() => fetchRanking(true)}
              skeleton={<RankingSkeleton />}
            >
              <div className="ranking-card" style={S.tableCard}>
                <div style={S.scroll}>
                  <table className="ranking-table" style={S.table}>
                    <thead>
                      <tr>
                        <th style={{ ...S.th, width: 56 }}>#</th>
                        <th style={S.th}>Mã căn</th>
                        {!isMobile && <th style={S.th}>Phân khu</th>}
                        <th style={S.th}>Trạng thái</th>
                        <th style={{ ...S.th, minWidth: 180 }}>Điểm</th>
                        {!isMobile && <th style={S.th}>Drivers</th>}
                        <th style={S.th}>Mức độ quan tâm</th>
                        {!isMobile && <th style={S.th}>Hạng trong phân khu</th>}
                        <th style={{ ...S.th, width: 40 }} aria-label="Mở giải thích" />
                      </tr>
                    </thead>
                    <tbody>
                      {visibleItems.map((u) => {
                        const bandStyle = BAND_STYLE[u.band] || BAND_STYLE.low;
                        const open = expanded === u.unit_id;
                        const percent = u.score_percent ?? 0;
                        return (
                          <React.Fragment key={u.unit_id}>
                            <tr
                              onClick={() => setExpanded(open ? null : u.unit_id)}
                              style={{ ...S.row, background: open ? color.accentSoft : undefined }}
                            >
                              <td style={{ ...S.td, ...S.rank }}>{u.rank_in_project}</td>
                              <td style={{ ...S.td, fontWeight: 600, color: color.ink }}>
                                {u.unit_code}
                                <span style={S.unitType}>{u.unit_type}</span>
                              </td>
                              {!isMobile && <td style={S.td}>{u.area_name}</td>}
                              <td style={S.td}>{UNIT_STATUS_LABEL[u.unit_status] ?? u.unit_status}</td>
                              <td style={S.td}>
                                <div style={S.scoreCell}>
                                  <span style={S.track}>
                                    <span style={{ ...S.fill, width: `${percent}%`, background: bandStyle.fg }} />
                                  </span>
                                  <span className="ranking-score" style={S.percent}>{percent.toFixed(1)}%</span>
                                </div>
                              </td>
                              {!isMobile && <td style={S.td}><DriverChips contributions={u.contributions} /></td>}
                              <td style={S.td}>
                                <span className="ranking-badge" title={bandStyle.threshold} style={{ ...S.badge, color: bandStyle.fg, background: bandStyle.bg }}>
                                  {bandStyle.label}
                                </span>
                              </td>
                              {!isMobile && <td style={S.td}>#{u.rank_in_area}</td>}
                              <td style={{ ...S.td, ...S.caret }}>{open ? "▾" : "▸"}</td>
                            </tr>
                            {open && (
                              <tr>
                                <td colSpan={isMobile ? 6 : 9} style={S.explainCell}>
                                  <Explanation unit={u} />
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </SectionState>
          )}

          {(offset > 0 || hasNextPage) && (
            <div style={S.pager}>
              <button style={S.pageButton} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Trang trước
              </button>
              <span style={S.pageInfo}>
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} / {fmt(total)}
              </span>
              <button style={S.pageButton} disabled={!hasNextPage} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Trang sau
              </button>
            </div>
          )}

          {data?.disclaimer && <p style={S.disclaimer}>{data.disclaimer}</p>}
          {data && <DataReliability summary={decisionData.summary} quality={decisionData.quality} ranking={data} trend={decisionData.trend} />}
        </>
      )}
    </>
  );
}

/** Phần "vì sao căn này đứng ở đây" — trải từng đặc trưng ra thành một dòng.
 *  Backend đã sắp theo đóng góp giảm dần, nên thứ tự ở đây không được sắp lại. */
function Explanation({ unit }) {
  const contributions = unit.contributions || [];
  return (
    <div style={S.explain}>
      <div style={S.explainHead}>
        <b style={{ color: color.ink }}>Điểm {Number(unit.score).toFixed(4)} được tạo từ</b>
        <span style={S.coverage}>độ phủ trọng số {decimal(unit.weight_coverage)}</span>
      </div>
      <table style={S.explainTable}>
        <thead>
          <tr>
            <th style={S.explainTh}>Đặc trưng</th>
            <th style={S.explainTh}>Giá trị</th>
            <th style={S.explainTh}>Trọng số</th>
            <th style={S.explainTh}>Đóng góp</th>
          </tr>
        </thead>
        <tbody>
          {contributions.map((c) => (
            <tr key={c.feature_key}>
              <td style={S.explainTd}>
                {FEATURE_LABEL[c.feature_key] || c.feature_key}
                {c.direction === "negative" && <span style={S.negative}>ngược chiều</span>}
                {c.source !== "resolved" && <span style={S.missing}>thiếu dữ liệu</span>}
              </td>
              <td style={S.explainTd}>{c.value === null ? "—" : decimal(c.value)}</td>
              <td style={S.explainTd}>{decimal(c.weight)}</td>
              <td style={{ ...S.explainTd, fontWeight: 700, color: color.ink }}>{decimal(c.contribution, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {contributions.length === 0 && <p style={{ margin: 0, color: color.muted }}>Không có chi tiết đóng góp.</p>}
    </div>
  );
}

function DriverChips({ contributions = [] }) {
  return (
    <div className="ranking-driver-chips" aria-label="Ba drivers chính">
      {contributions.slice(0, 3).map((contribution) => (
        <span key={contribution.feature_key} title={`${FEATURE_LABEL[contribution.feature_key] || contribution.feature_key}: ${decimal(contribution.contribution, 3)}`}>
          {FEATURE_LABEL[contribution.feature_key] || contribution.feature_key}
        </span>
      ))}
      {!contributions.length && <span className="ranking-driver-chips__empty">Chưa có</span>}
    </div>
  );
}

function SummaryCard({ label, value, tone }) {
  return (
    <div style={S.summaryCard}>
      <span style={S.summaryLabel}>{label}</span>
      <b style={{ ...S.summaryValue, color: tone || color.ink }}>{value}</b>
    </div>
  );
}

const S = {
  pageHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(5) },
  h1: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: 28, fontWeight: 700, letterSpacing: "-.03em" },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.small },
  configBadge: { flex: "none", color: color.accent, background: color.accentSoft, border: 0, borderRadius: radius.pill, padding: "7px 12px", fontSize: size.tiny, fontWeight: 700, fontFamily: font.mono, cursor: "pointer" },

  scopeBar: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), marginBottom: space(4), boxShadow: shadow },
  label: { display: "flex", flexDirection: "column", gap: 5, color: color.ink, fontSize: 15, fontWeight: 500 },
  select: { minWidth: 190, padding: "9px 11px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, fontFamily: "inherit" },
  runBox: { display: "flex", alignItems: "center", justifyContent: "flex-end", gap: space(3) },
  freshness: { color: color.muted, fontSize: 15 },
  autoStatus: { color: color.muted, fontSize: 15, fontWeight: 600, whiteSpace: "nowrap" },

  summary: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: space(3), marginBottom: space(4) },
  summaryCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), display: "flex", flexDirection: "column", gap: 6, boxShadow: shadow },
  summaryLabel: { color: color.muted, fontSize: size.tiny },
  summaryValue: { fontFamily: font.display, fontSize: 24, fontVariantNumeric: "tabular-nums" },

  availabilityFilter: { display: "flex", alignItems: "center", gap: 6, color: color.body, fontSize: 15, cursor: "pointer", marginBottom: space(4) },

  tableCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflow: "hidden" },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 15 },
  th: { textAlign: "left", padding: "14px 18px", color: color.muted, fontSize: 15, fontWeight: 600, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  row: { cursor: "pointer", borderBottom: `1px solid ${color.border}` },
  td: { padding: "16px 18px", verticalAlign: "middle", fontSize: 15 },
  rank: { fontFamily: font.mono, color: color.muted, fontVariantNumeric: "tabular-nums" },
  unitType: { display: "block", color: color.muted, fontWeight: 400, fontSize: size.tiny, marginTop: 2 },
  caret: { color: color.muted, textAlign: "center" },

  scoreCell: { display: "flex", alignItems: "center", gap: space(2) },
  track: { flex: 1, minWidth: 80, height: 6, borderRadius: radius.pill, background: color.canvas, overflow: "hidden" },
  fill: { display: "block", height: "100%", borderRadius: radius.pill },
  percent: { fontVariantNumeric: "tabular-nums", fontWeight: 700, color: color.ink, minWidth: 52, textAlign: "right" },
  badge: { borderRadius: radius.pill, padding: "6px 14px", fontSize: 14, fontWeight: 700, whiteSpace: "nowrap" },

  explainCell: { padding: 0, background: color.accentSoft, borderBottom: `1px solid ${color.border}` },
  explain: { padding: space(4) },
  explainHead: { display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: space(3), marginBottom: space(3), flexWrap: "wrap" },
  coverage: { color: color.muted, fontSize: size.tiny, fontFamily: font.mono },
  explainTable: { width: "100%", borderCollapse: "collapse", fontSize: size.tiny, background: color.surface, borderRadius: radius.sm, overflow: "hidden" },
  explainTh: { textAlign: "left", padding: `${space(2)}px ${space(3)}px`, color: color.muted, fontWeight: 700, borderBottom: `1px solid ${color.border}` },
  explainTd: { padding: `${space(2)}px ${space(3)}px`, borderBottom: `1px solid ${color.border}`, fontVariantNumeric: "tabular-nums" },
  negative: { marginLeft: 6, color: color.danger, fontSize: 10, fontWeight: 700 },
  missing: { marginLeft: 6, color: color.warn, fontSize: 10, fontWeight: 700 },

  emptyCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(10), textAlign: "center", color: color.muted },
  emptyIcon: { color: color.accent, background: color.accentSoft, borderRadius: "50%", width: 54, height: 54, display: "grid", placeItems: "center", fontSize: 22, margin: `0 auto ${space(3)}px` },
  hint: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(8), textAlign: "center", color: color.muted, boxShadow: shadow },
  error: { background: color.dangerSoft, color: color.danger, borderRadius: radius.sm, padding: space(3), marginBottom: space(4), fontSize: size.small },

  pager: { display: "flex", alignItems: "center", justifyContent: "center", gap: space(4), marginTop: space(4) },
  pageButton: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "8px 14px", fontFamily: "inherit", fontSize: size.tiny, cursor: "pointer" },
  pageInfo: { color: color.muted, fontSize: size.tiny, fontVariantNumeric: "tabular-nums" },

  disclaimer: { marginTop: space(5), color: color.muted, fontSize: size.tiny, textAlign: "center", lineHeight: 1.6 },
};
