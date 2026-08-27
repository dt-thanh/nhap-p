import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getAbsorptionSummary } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { useProjectScope } from "../hooks/useProjectScope";
import ProjectCard from "../components/ProjectCard";
import ProjectSelector from "../components/ProjectSelector";
import { ErrorState, EmptyState } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

export default function RankingDashboardPage() {
  const navigate = useNavigate();
  const scope = useProjectScope();
  const [filter, setFilter] = useState("");
  const projects = useMemo(
    () => scope.projects.filter((project) => !filter || project.external_id === filter),
    [filter, scope.projects],
  );
  const summaries = useAsync(
    () => Promise.all(
      projects.map(async (project) => {
        try {
          return [project.project_id, await getAbsorptionSummary(project.project_id)];
        } catch (error) {
          return [project.project_id, { error }];
        }
      }),
    ),
    [projects],
  );
  const summaryMap = useMemo(() => new Map(summaries.data || []), [summaries.data]);

  return (
    <>
      <GlobalKeyframes />
      <header style={S.pageHead}>
        <div><h1 style={S.h1}>Tư vấn xếp hạng dự án</h1><p style={S.sub}>Tổng quan hấp thụ và đi sâu vào thứ tự ưu tiên từng căn.</p></div>
        <div style={S.headActions}><button type="button" style={S.secondary} onClick={() => navigate("/consultant/new/advisory")}>Mở advisory</button><button type="button" style={S.secondary} onClick={() => navigate("/consultant/new/evidence")}>Quản trị bằng chứng</button></div>
      </header>
      <section style={S.toolbar} aria-label="Bộ lọc dự án">
        <ProjectSelector
          projects={scope.projects}
          value={filter}
          onChange={setFilter}
          loading={scope.loadingProjects}
          status={scope.projectsStatus === "unauthorized" ? "unauthorized" : scope.projectsStatus === "error" ? "error" : undefined}
        />
        <div style={S.note}>Dữ liệu chỉ gồm dự án trong phạm vi tài khoản.</div>
      </section>
      {summaries.error ? <ErrorState error={summaries.error} onRetry={summaries.reload} /> : null}
      {!summaries.loading && !summaries.error && !projects.length ? <EmptyState title="Chưa có dự án trong phạm vi" /> : null}
      <section style={S.grid} aria-label="Danh sách dự án">
        {projects.map((project) => (
          <ProjectCard
            key={project.project_id}
            project={project}
            summary={summaryMap.get(project.project_id)}
            loading={summaries.loading}
            onOpen={() => navigate(`/ranking/${encodeURIComponent(project.external_id)}`)}
          />
        ))}
      </section>
    </>
  );
}

const S = {
  pageHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(5) },
  h1: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h1, letterSpacing: "-.03em" },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.small },
  toolbar: { display: "flex", alignItems: "flex-end", gap: space(4), flexWrap: "wrap", padding: space(4), marginBottom: space(4), background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow },
  note: { alignSelf: "center", color: color.muted, fontSize: size.tiny },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: space(4) },
  secondary: { border: `1px solid ${color.borderStrong}`, background: color.surface, color: color.body, borderRadius: radius.sm, padding: "9px 13px", fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  headActions: { display: "flex", gap: space(2), flexWrap: "wrap" },
};
