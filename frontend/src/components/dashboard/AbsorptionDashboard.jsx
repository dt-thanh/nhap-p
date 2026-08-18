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
  getDashboardSummary, getDashboardTrend, getDashboardAreas, getDataQuality,
} from "../../api/endpoints";
import { useProjectScope } from "../../hooks/useProjectScope";
import { useAsync } from "../../hooks/useAsync";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import { space } from "../../styles/tokens";
import { deriveVelocityDirection } from "../../utils/velocity";
import { DASHBOARD_TEXT } from "./labels";
import { areaLabel } from "../../utils/areaLabel";

import DashboardHeader from "./DashboardHeader";
import FilterToolbar from "./FilterToolbar";
import KpiCards from "./KpiCards";
import AbsorptionTrendChart from "./AbsorptionTrendChart";
import ActionHealthPanel from "./ActionHealthPanel";
import AreaComparison from "./AreaComparison";
import AreaDetailTable from "./AreaDetailTable";
import DataQualityPanel from "./DataQualityPanel";
import GlobalKeyframes from "../ui/GlobalKeyframes";

const RANGE_DAYS = { "30d": 30, "90d": 90, "12m": 365 };
const RANGE_LABELS = {
  "30d": "Xu hướng 30 ngày", "90d": "Xu hướng 90 ngày", "12m": "Xu hướng 12 tháng",
  currentYear: "Năm hiện tại", all: "Toàn bộ lịch sử", year: "Năm đã chọn", custom: "Khoảng tùy chỉnh",
};
const iso = (d) => d.toISOString().slice(0, 10);

export default function AbsorptionDashboard({ projectExternalId }) {
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
  const dq = useAsync(
    () =>
      projectInternalId
        ? getDataQuality({ projectId: projectInternalId, externalProjectId: scope.projectExternalId, from, to, calculator: "domain_units_deals" })
        : Promise.resolve(null),
    [projectInternalId, scope.projectExternalId, from, to],
  );

  const refreshing = summary.loading || trend.loading || areas.loading || dq.loading;
  const refreshAll = () => { summary.reload(); trend.reload(); areas.reload(); dq.reload(); };

  const velocityDirection = deriveVelocityDirection(summary.data?.velocity_7d, summary.data?.velocity_30d);
  const headerTitle = scope.currentProject ? `${scope.currentProject.name} · ${DASHBOARD_TEXT.dashboardTitle}` : undefined;
  const availableYears = summary.data?.available_years?.length ? summary.data.available_years : (trend.data?.availableYears || []);
  const dashboardContext = `${DASHBOARD_TEXT.dataSourceDomain} · ${currentArea ? areaLabel(currentArea) : DASHBOARD_TEXT.allAreas} · ${RANGE_LABELS[range] || RANGE_LABELS["90d"]}`;

  // `AreaComparison`/`AreaDetailTable` chỉ có `area_id` (UUID nội bộ, từ
  // `InventoryAreaOut` — không có `external_id`). Tra ngược qua `scope.areas`
  // (nguồn CÓ `external_id`, đã tải sẵn) để điều hướng đúng quy ước URL của
  // app (external_id, không phải UUID nội bộ).
  const onSelectArea = (internalAreaId) => {
    const external = (scope.areas || []).find((a) => a.area_id === internalAreaId)?.external_id;
    if (external) navigate(`/projects/${scope.projectExternalId}/areas/${external}`);
  };

  const twoCol = bp === "desktop" || bp === "laptop";

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
      <DashboardHeader title={headerTitle} updatedAt={summary.data?.updated_at} onRefresh={refreshAll} refreshing={refreshing} />

      <FilterToolbar
        projects={toolbarProjects}
        areas={toolbarAreas}
        projectId={scope.projectExternalId}
        areaId={scope.areaExternalId ?? "all"}
        range={range}
        onProject={(nextExternalId) => navigate(`/projects/${nextExternalId}/dashboard`)}
        onArea={(nextId) => scope.setAreaExternalId(nextId === "all" ? null : nextId)}
        onRange={setRange}
        availableYears={availableYears}
        selectedYear={selectedYear}
        onYear={(nextYear) => { setSelectedYear(nextYear); setRange(nextYear ? "year" : "90d"); }}
        customFrom={customFrom}
        customTo={customTo}
        onCustomFrom={setCustomFrom}
        onCustomTo={setCustomTo}
        loading={scope.loadingProjects}
        showProjectSelector
      />

      <KpiCards
        summary={summary.data}
        velocityDirection={velocityDirection}
        loading={summary.loading}
        error={summary.error}
        onRetry={summary.reload}
        context={dashboardContext}
      />

      <AbsorptionTrendChart
        series={dateRangeReady ? trend.data?.points : []}
        loading={trend.loading}
        error={trend.error}
        onRetry={trend.reload}
        dataStatus={trend.data?.dataStatus}
        emptyMessage={trend.data?.message}
        granularity={trend.data?.granularity || granularity}
        dataSource={trend.data?.dataSource}
      />

      <ActionHealthPanel summary={summary.data} loading={summary.loading} error={summary.error} onRetry={summary.reload} />

      <div style={{ display: "grid", gridTemplateColumns: twoCol ? "1.4fr 1fr" : "1fr", gap: space(5) }}>
        <AreaComparison areas={areas.data} loading={areas.loading} error={areas.error} onRetry={areas.reload} onSelectArea={onSelectArea} />
        <DataQualityPanel dq={dq.data} loading={dq.loading} error={dq.error} onRetry={dq.reload} />
      </div>

      <AreaDetailTable areas={areas.data} loading={areas.loading} error={areas.error} onRetry={areas.reload} onSelectArea={onSelectArea} />
    </>
  );
}

function daysBetween(from, to) {
  return Math.round((new Date(`${to}T00:00:00Z`) - new Date(`${from}T00:00:00Z`)) / 86400000);
}
