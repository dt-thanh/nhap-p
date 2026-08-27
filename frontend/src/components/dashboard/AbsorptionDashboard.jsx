// frontend/src/components/dashboard/AbsorptionDashboard.jsx
// Thân Dashboard hấp thụ của MỘT dự án — sống ở /projects/:externalId/dashboard
// (xem pages/ProjectDashboardPage.jsx, nơi xác nhận dự án tồn tại VÀ trong
// phạm vi trước khi render component này).
//
// Ngữ cảnh dự án/phân khu ĐẾN TỪ URL qua useProjectScope — dự án khoá theo
// `projectExternalId` (path), phân khu theo `?area=` (query). Không có state
// project/area cục bộ nào sống song song với URL nữa (khác bản cũ, dùng
// `useState` — xem lịch sử ở git blame nếu cần so sánh).
//
// Frontend-only: mọi số liệu từ API (dữ liệu canonical), không hard-code.
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  getDashboardSummary, getDashboardTrend, getDashboardAreas, getDataQuality, getMarketDashboard,
} from "../../api/endpoints";
import { useProjectScope } from "../../hooks/useProjectScope";
import { useAsync } from "../../hooks/useAsync";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import { areaLabel } from "../../utils/areaLabel";
import GlobalKeyframes from "../ui/GlobalKeyframes";
import OverviewDashboard from "./OverviewDashboard";

const RANGE_DAYS = { "7d": 7, "30d": 30, "90d": 90, "12m": 365 };
const iso = (d) => d.toISOString().slice(0, 10);

export default function AbsorptionDashboard({ projectExternalId, standalone = false, preview = false }) {
  const { bp } = useBreakpoint();
  const navigate = useNavigate();
  const scope = useProjectScope({ projectExternalId });
  const [range, setRange] = useState("90d");
  const [selectedYear, setSelectedYear] = useState("");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");

  const { from, to, year, granularity } = useMemo(() => {
    if (range === "all") return { from: undefined, to: undefined, year: undefined, granularity: "month" };
    if (range === "currentYear") {
      const now = new Date();
      return { from: `${now.getUTCFullYear()}-01-01`, to: iso(now), year: undefined, granularity: "month" };
    }
    if (range === "year" && selectedYear) {
      return { from: undefined, to: undefined, year: selectedYear, granularity: "month" };
    }
    if (range === "custom") {
      return { from: customFrom || undefined, to: customTo || undefined, year: undefined, granularity: customFrom && customTo && daysBetween(customFrom, customTo) > 90 ? "month" : "day" };
    }
    const days = RANGE_DAYS[range] || 90;
    return { from: iso(new Date(Date.now() - days * 86400000)), to: iso(new Date()), year: undefined, granularity: range === "12m" ? "month" : "day" };
  }, [range, selectedYear, customFrom, customTo]);
  const dateRangeReady = range !== "custom" || Boolean(from && to);

  // Chưa chọn phân khu nào qua URL -> tự chọn phân khu ĐẦU TIÊN của dự án
  // này, và ghi lựa chọn đó vào URL (không phải một mặc định ẩn trong state)
  // — cùng tinh thần "project_id phải nằm trong URL" áp cho cả area. Bỏ qua
  // phân khu di sản không có `external_id` (không thể đặt vào query param).
  useEffect(() => {
    if (scope.areaExternalId || scope.areasStatus !== "ok") return;
    const firstSelectable = (scope.areas || []).find((a) => a.external_id);
    if (firstSelectable) scope.setAreaExternalId(firstSelectable.external_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.areaExternalId, scope.areas, scope.areasStatus]);

  const projectInternalId = scope.currentProject?.project_id ?? null;
  const currentArea = scope.currentArea;
  const areaInternalId = currentArea?.area_id ?? null;
  const areaTotalUnits = currentArea?.total_units ?? null;

  const summary = useAsync(
    () =>
      projectInternalId
        ? getDashboardSummary({ projectId: projectInternalId, areaId: areaInternalId, areas: scope.areas, calculator: "domain_units_deals" })
        : Promise.resolve(null),
    [projectInternalId, areaInternalId, scope.areas],
  );
  const trend = useAsync(
    () =>
      projectInternalId && dateRangeReady
        ? getDashboardTrend({ projectId: projectInternalId, areaId: areaInternalId, areaTotalUnits, totalSold: summary.data?.units_sold, from, to, year, granularity, calculator: "domain_units_deals" })
        : Promise.resolve({ points: [], latestVelocity7d: null, latestVelocity30d: null, dataStatus: "no_data" }),
    [projectInternalId, areaInternalId, areaTotalUnits, summary.data?.units_sold, from, to, year, granularity, dateRangeReady],
  );
  const areas = useAsync(
    () => getDashboardAreas({ externalProjectId: scope.projectExternalId }),
    [scope.projectExternalId],
  );
  const market = useAsync(
    () => scope.projectExternalId ? getMarketDashboard(scope.projectExternalId) : Promise.resolve(null),
    [scope.projectExternalId],
  );
  const dq = useAsync(
    () =>
      projectInternalId
        ? getDataQuality({ projectId: projectInternalId, externalProjectId: scope.projectExternalId, from, to, calculator: "domain_units_deals" })
        : Promise.resolve(null),
    [projectInternalId, scope.projectExternalId, from, to],
  );

  const refreshing = summary.loading || trend.loading || areas.loading || market.loading || dq.loading;
  const refreshAll = () => { summary.reload(); trend.reload(); areas.reload(); market.reload(); dq.reload(); };

  const availableYears = summary.data?.available_years?.length ? summary.data.available_years : (trend.data?.availableYears || []);

  // `AreaComparison`/`AreaDetailTable` chỉ có `area_id` (UUID nội bộ, từ
  // `InventoryAreaOut` — không có `external_id`). Tra ngược qua `scope.areas`
  // (nguồn CÓ `external_id`, đã tải sẵn) để điều hướng đúng quy ước URL của
  // app (external_id, không phải UUID nội bộ).
  const onSelectArea = (internalAreaId) => {
    const external = (scope.areas || []).find((a) => a.area_id === internalAreaId)?.external_id;
    if (!external) return;
    if (standalone) scope.setAreaExternalId(external);
    else navigate(`/projects/${scope.projectExternalId}/areas/${external}`);
  };

  // FilterToolbar (dùng lại NGUYÊN VẸN, không sửa) chỉ hiểu {id, name} — ánh
  // xạ đúng hình dạng đó ở đây thay vì đổi widget.
  const toolbarProjects = useMemo(
    () => (scope.projects || []).filter((p) => p.external_id).map((p) => ({ id: p.external_id, name: p.name })),
    [scope.projects],
  );
  const toolbarAreas = useMemo(
    () =>
      (scope.areas || [])
        .filter((a) => a.external_id)
        .map((a) => ({ id: a.external_id, name: areaLabel(a) })),
    [scope.areas],
  );

  return (
    <>
      <GlobalKeyframes />
      <OverviewDashboard
        project={scope.currentProject}
        area={currentArea}
        summary={summary.data}
        summaryLoading={summary.loading}
        summaryError={summary.error}
        onSummaryRetry={summary.reload}
        market={market.data}
        marketLoading={market.loading}
        marketError={market.error}
        trend={dateRangeReady ? trend.data?.points : []}
        trendLoading={trend.loading}
        trendError={trend.error}
        onTrendRetry={trend.reload}
        trendStatus={trend.data?.dataStatus}
        trendMessage={trend.data?.message}
        areas={areas.data}
        areasLoading={areas.loading}
        areasError={areas.error}
        onAreasRetry={areas.reload}
        dataQuality={dq.data}
        dataQualityLoading={dq.loading}
        dataQualityError={dq.error}
        onDataQualityRetry={dq.reload}
        refreshing={refreshing}
        onRefresh={refreshAll}
        toolbarProjects={toolbarProjects}
        toolbarAreas={toolbarAreas}
        projectExternalId={scope.projectExternalId}
        areaExternalId={scope.areaExternalId}
        range={range}
        onProject={(nextExternalId) => standalone
          ? scope.setProjectExternalId(nextExternalId)
          : navigate(`/projects/${nextExternalId}/dashboard`)}
        onArea={(nextId) => scope.setAreaExternalId(nextId === "all" ? null : nextId)}
        onRange={setRange}
        availableYears={availableYears}
        selectedYear={selectedYear}
        onYear={(nextYear) => { setSelectedYear(nextYear); setRange(nextYear ? "year" : "90d"); }}
        customFrom={customFrom}
        customTo={customTo}
        onCustomFrom={setCustomFrom}
        onCustomTo={setCustomTo}
        onSelectArea={onSelectArea}
        isWide={bp === "desktop" || bp === "laptop"}
        preview={preview}
      />
    </>
  );
}

function daysBetween(from, to) {
  return Math.round((new Date(`${to}T00:00:00Z`) - new Date(`${from}T00:00:00Z`)) / 86400000);
}
