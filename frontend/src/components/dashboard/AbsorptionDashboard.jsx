// frontend/src/components/dashboard/AbsorptionDashboard.jsx
// Thân Dashboard hấp thụ — TÁI DÙNG ở 2 nơi:
//   • Trang /dashboard (có bộ chọn dự án)
//   • Nhúng trong S03 Chi tiết dự án (khoá theo 1 dự án, ẩn bộ chọn dự án)
//
// Props:
//   fixedProjectId    : nếu có -> khoá dashboard theo dự án này
//   showProjectSelector: hiện/ẩn ô chọn dự án trong toolbar
//   showHeader        : hiện/ẩn header "Absorption Dashboard" (S03 tự có header dự án)
//
// Frontend-only: mọi số liệu từ API/mock (dữ liệu canonical), không hard-code.
import React, { useMemo, useState } from "react";
import {
  listProjects, listProjectZones,
  getDashboardSummary, getDashboardTrend, getDashboardAreas, getDataQuality,
} from "../../api/endpoints";
import { useAsync } from "../../hooks/useAsync";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import { space } from "../../styles/tokens";

import DashboardHeader from "./DashboardHeader";
import FilterToolbar from "./FilterToolbar";
import KpiCards from "./KpiCards";
import AbsorptionTrendChart from "./AbsorptionTrendChart";
import AreaComparison from "./AreaComparison";
import AreaDetailTable from "./AreaDetailTable";
import DataQualityPanel from "./DataQualityPanel";
import GlobalKeyframes from "../ui/GlobalKeyframes";

const RANGE_DAYS = { "30d": 30, "90d": 90, "12m": 365 };
const iso = (d) => d.toISOString().slice(0, 10);

export default function AbsorptionDashboard({ fixedProjectId = null, showProjectSelector = true, showHeader = true }) {
  const { bp } = useBreakpoint();
  const [projectId, setProjectId] = useState(fixedProjectId);
  const [areaId, setAreaId] = useState("all");
  const [range, setRange] = useState("90d");

  const { from, to } = useMemo(() => {
    const days = RANGE_DAYS[range] || 90;
    return { from: iso(new Date(Date.now() - days * 86400000)), to: iso(new Date()) };
  }, [range]);

  const projects = useAsync(() => listProjects(), []);
  const activeProject = fixedProjectId ?? projectId ?? projects.data?.[0]?.id ?? null;

  const zones = useAsync(
    () => (activeProject ? listProjectZones(activeProject) : Promise.resolve([])),
    [activeProject]
  );

  const args = { projectId: activeProject, areaId: areaId === "all" ? undefined : areaId, from, to };
  const summary = useAsync(() => getDashboardSummary(args), [activeProject, areaId, from, to]);
  const trend   = useAsync(() => getDashboardTrend(args), [activeProject, areaId, from, to]);
  const areas   = useAsync(() => getDashboardAreas({ projectId: activeProject }), [activeProject]);
  const dq      = useAsync(() => getDataQuality({ projectId: activeProject }), [activeProject]);

  const refreshing = summary.loading || trend.loading || areas.loading;
  const refreshAll = () => { summary.reload(); trend.reload(); areas.reload(); dq.reload(); };

  const twoCol = bp === "desktop" || bp === "laptop";

  return (
    <>
      <GlobalKeyframes />
      {showHeader && (
        <DashboardHeader updatedAt={summary.data?.updated_at} onRefresh={refreshAll} refreshing={refreshing} />
      )}

      <FilterToolbar
        projects={projects.data || []}
        areas={zones.data || []}
        projectId={activeProject}
        areaId={areaId}
        range={range}
        onProject={(v) => { setProjectId(v); setAreaId("all"); }}
        onArea={setAreaId}
        onRange={setRange}
        loading={projects.loading}
        showProjectSelector={showProjectSelector}
      />

      <KpiCards summary={summary.data} loading={summary.loading} error={summary.error} onRetry={summary.reload} />

      <AbsorptionTrendChart series={trend.data} loading={trend.loading} error={trend.error} onRetry={trend.reload} />

      <div style={{ display: "grid", gridTemplateColumns: twoCol ? "1.4fr 1fr" : "1fr", gap: space(5) }}>
        <AreaComparison areas={areas.data} loading={areas.loading} error={areas.error} onRetry={areas.reload} />
        <DataQualityPanel dq={dq.data} loading={dq.loading} error={dq.error} onRetry={dq.reload} />
      </div>

      <AreaDetailTable areas={areas.data} loading={areas.loading} error={areas.error} onRetry={areas.reload} />
    </>
  );
}
