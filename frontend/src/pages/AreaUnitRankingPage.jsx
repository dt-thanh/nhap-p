import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getAreaByExternalId, getProjectRankingReport, getRanking } from "../api/endpoints";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/States";
import { useAsync } from "../hooks/useAsync";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

export default function AreaUnitRankingPage() {
  const { projectId, areaId } = useParams();
  const navigate = useNavigate();
  const request = useAsync(async () => {
    const [projectReport, area, ranking] = await Promise.all([
      getProjectRankingReport(projectId),
      getAreaByExternalId(areaId),
      getRanking(projectId, { external_area_id: areaId, sort_by: "hierarchical_score", limit: 200 }),
    ]);
    return { projectReport, area, ranking };
  }, [projectId, areaId]);

  if (request.loading) return <LoadingPage />;
  if (request.error) return <ErrorState error={request.error} onRetry={request.reload} />;
  if (!request.data) return null;
  const { projectReport, area, ranking } = request.data;
  const project = projectReport.project;

  return (
    <main style={S.page}>
      <button type="button" style={S.back} onClick={() => navigate(`/ranking/${encodeURIComponent(projectId)}/report`)}>← Chọn phân khu khác</button>
      <header style={S.header}>
        <p style={S.eyebrow}>{project.name} · {area.external_id}</p>
        <h1 style={S.h1}>{area.area_name}</h1>
        <p style={S.sub}>Căn hộ được sắp theo hierarchical AHP score đã lưu, cao xuống thấp.</p>
      </header>

      <section style={S.metaGrid} aria-label="Thông tin phân khu">
        <Meta label="Tổng căn có kết quả" value={ranking.total} />
        <Meta label="Ranking run" value={ranking.ranking_run_id || "Chưa có"} />
        <Meta label="Config" value={ranking.config_version ?? "Chưa có"} />
        <Meta label="Thời điểm tính" value={formatTimestamp(ranking.computed_at)} />
      </section>

      {ranking.state !== "ready" ? (
        <EmptyState title="Xếp hạng chưa sẵn sàng" hint={ranking.reason || "Không có ranking run phù hợp."} />
      ) : !ranking.items.length ? (
        <EmptyState title="Chưa có căn được xếp hạng" hint="Phân khu này chưa có kết quả phù hợp." />
      ) : (
        <section style={S.card} aria-labelledby="unit-table-title">
          <div style={S.sectionHead}><div><h2 id="unit-table-title" style={S.h2}>Xếp hạng căn hộ</h2><p style={S.sub}>Floor, hướng, diện tích và giá là thuộc tính tham khảo; chúng không được coi là tiêu chí AHP nếu backend không chấm chúng.</p></div><span style={S.count}>{ranking.items.length}/{ranking.total}</span></div>
          <div style={S.tableWrap}>
            <table style={S.table}>
              <thead><tr><th>Hạng AHP</th><th>Mã căn</th><th>Tầng</th><th>Hướng</th><th>Diện tích</th><th>Giá</th><th>Điểm AHP</th></tr></thead>
              <tbody>{ranking.items.map((unit) => (
                <tr
                  key={unit.unit_id}
                  tabIndex={unit.external_unit_id ? 0 : undefined}
                  role={unit.external_unit_id ? "link" : undefined}
                  aria-label={unit.external_unit_id ? `Mở báo cáo AHP của căn ${unit.unit_code}` : undefined}
                  style={unit.external_unit_id ? S.clickableRow : undefined}
                  onClick={() => openUnit(unit)}
                  onKeyDown={(event) => { if ((event.key === "Enter" || event.key === " ") && unit.external_unit_id) { event.preventDefault(); openUnit(unit); } }}
                >
                  <td><strong>#{unit.hierarchical_rank_in_area ?? "—"}</strong></td>
                  <td><strong style={S.unitCode}>{unit.unit_code}</strong><small style={S.small}>{unit.unit_type}</small></td>
                  <td>{unit.floor ?? "—"}</td><td>{unit.orientation || "—"}</td><td>{formatArea(unit.area_sqm)}</td><td>{formatPrice(unit.price_vnd)}</td>
                  <td><Score value={unit.hierarchical?.score} available={unit.hierarchical?.available} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );

  function openUnit(unit) {
    if (!unit.external_unit_id) return;
    navigate(`/ranking/${encodeURIComponent(projectId)}/areas/${encodeURIComponent(areaId)}/units/${encodeURIComponent(unit.external_unit_id)}/report`);
  }
}

function Score({ value, available }) { return <span style={available && value !== null ? S.score : S.missing}>{available && value !== null ? Number(value).toFixed(4) : "Chưa có"}</span>; }
function Meta({ label, value }) { return <div><dt style={S.metaLabel}>{label}</dt><dd style={S.metaValue}>{String(value)}</dd></div>; }
function LoadingPage() { return <main style={S.page}><Skeleton width={130} /><Skeleton width="45%" height={38} style={{ marginTop: space(4) }} /><Skeleton height={320} style={{ marginTop: space(4) }} /></main>; }
function formatTimestamp(value) { return value ? new Date(value).toLocaleString("vi-VN") : "Chưa có"; }
function formatArea(value) { return value === null || value === undefined ? "—" : `${Number(value).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} m²`; }
function formatPrice(value) { return value === null || value === undefined ? "—" : `${(Number(value) / 1_000_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tỷ`; }

const S = {
  page: { maxWidth: 1280, margin: "0 auto", paddingBottom: space(8) }, back: { border: 0, background: "transparent", color: color.accent, padding: 0, fontFamily: "inherit", fontWeight: 700, cursor: "pointer" },
  header: { margin: `${space(4)}px 0` }, eyebrow: { margin: 0, color: color.muted, fontSize: size.tiny, fontWeight: 700, textTransform: "uppercase" }, h1: { margin: "5px 0 0", color: color.ink, fontFamily: font.display, fontSize: size.h1 }, h2: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 }, sub: { margin: "5px 0 0", color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  metaGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: space(3), padding: space(4), marginBottom: space(4), borderRadius: radius.md, background: color.surface, boxShadow: shadow }, metaLabel: { margin: 0, color: color.muted, fontSize: size.tiny }, metaValue: { margin: "4px 0 0", color: color.ink, fontWeight: 800, overflowWrap: "anywhere" },
  card: { padding: space(4), borderRadius: radius.md, background: color.surface, boxShadow: shadow }, sectionHead: { display: "flex", justifyContent: "space-between", gap: space(3), marginBottom: space(3) }, count: { alignSelf: "start", padding: "5px 9px", borderRadius: radius.pill, color: color.accent, background: color.accentSoft, fontSize: size.tiny, fontWeight: 800 },
  tableWrap: { overflowX: "auto" }, table: { width: "100%", borderCollapse: "collapse", color: color.body, fontSize: size.tiny }, clickableRow: { cursor: "pointer", transition: "background-color 160ms ease" }, unitCode: { color: color.accent }, small: { display: "block", marginTop: 3, color: color.muted }, score: { display: "inline-block", padding: "4px 8px", borderRadius: radius.pill, background: color.okSoft, color: color.ok, fontWeight: 800 }, missing: { color: color.muted },
};
