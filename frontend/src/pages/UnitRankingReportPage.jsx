import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getUnitRankingReport } from "../api/endpoints";
import { ErrorState, Skeleton } from "../components/ui/States";
import { useAsync } from "../hooks/useAsync";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

const STATE_COPY = {
  feature_disabled: ["Chế độ xem AHP đang tắt", "Backend chưa công bố hierarchical result."],
  not_run: ["Chưa có lần xếp hạng", "Căn này chưa thuộc ranking run hoàn tất."],
  not_computed: ["Chưa có điểm AHP", "Ranking run hiện tại chưa lưu hierarchical contribution cho căn này."],
  legal_gated: ["Không được xếp hạng", "Dự án đang chịu HIGH_RISK legal gate; hệ thống không tạo điểm thay thế."],
};

export default function UnitRankingReportPage() {
  const { projectId, areaId, unitId } = useParams();
  const navigate = useNavigate();
  const request = useAsync(() => getUnitRankingReport(projectId, areaId, unitId), [projectId, areaId, unitId]);
  if (request.loading) return <LoadingPage />;
  if (request.error) return <ErrorState error={request.error} onRetry={request.reload} />;
  if (!request.data) return null;
  const report = request.data;
  const apartment = report.apartment;
  const stateCopy = STATE_COPY[report.state];

  return (
    <main style={S.page}>
      <button type="button" style={S.back} onClick={() => navigate(`/ranking/${encodeURIComponent(projectId)}/areas/${encodeURIComponent(areaId)}`)}>← Về xếp hạng {report.area.name}</button>
      <header style={S.header}>
        <p style={S.eyebrow}>{report.project.name} · {report.area.name}</p>
        <h1 style={S.h1}>{apartment.code}</h1>
        <p style={S.sub}>Báo cáo giải thích từ ranking run {report.ranking_run_id || "chưa có"}.</p>
      </header>

      <section style={S.heroGrid}>
        <article style={S.scoreCard}><span style={S.metricLabel}>Tổng điểm AHP</span><strong style={S.scoreValue}>{formatScore(report.total_score)}</strong><span style={S.metricHint}>Cấu hình v{report.config_version ?? "—"}</span></article>
        <article style={S.rankCard}><span style={S.metricLabel}>Hạng trong phân khu</span><strong style={S.rankValue}>{report.rank ? `#${report.rank}` : "—"}</strong><span style={S.metricHint}>trên {report.ranked_apartments_in_area || 0} căn có điểm</span></article>
        <article style={S.contextCard}><Context label="Tầng" value={apartment.floor} /><Context label="Hướng" value={apartment.orientation} /><Context label="Diện tích" value={formatArea(apartment.area_sqm)} /><Context label="Giá tham khảo" value={formatPrice(apartment.price_vnd)} /></article>
      </section>

      {report.state !== "ready" ? (
        <section style={S.notice} aria-live="polite"><strong>{stateCopy?.[0] || "Báo cáo chưa sẵn sàng"}</strong><span>{stateCopy?.[1] || report.reason}</span><code>{report.reason}</code></section>
      ) : (
        <>
          <section style={S.explanation} aria-label="Giải thích xếp hạng"><span style={S.spark}>✦</span><p>{report.explanation}</p></section>
          <section style={S.card} aria-labelledby="criteria-title">
            <div style={S.sectionHead}><div><h2 id="criteria-title" style={S.h2}>Đóng góp theo tiêu chí</h2><p style={S.sub}>Weight, normalized score và contribution đều do backend dựng từ cấu hình và snapshot đã lưu.</p></div><span style={S.count}>{report.criteria.length} tiêu chí</span></div>
            <div style={S.tableWrap}><table style={S.table}><thead><tr><th>Tiêu chí</th><th>Grain</th><th>Trọng số hiệu lực</th><th>Điểm chuẩn hóa</th><th>Đóng góp</th><th aria-label="Biểu đồ đóng góp" /></tr></thead><tbody>{report.criteria.map((criterion) => <CriterionRow key={`${criterion.grain}:${criterion.name}`} criterion={criterion} />)}</tbody></table></div>
          </section>
          <section style={S.disclosure}><strong>Ranh giới dữ liệu</strong><span>Floor, hướng, diện tích, giá và view ở đầu trang là dữ liệu tham khảo từ unit_enrichment_attributes, không phải tiêu chí chấm điểm trừ khi chúng xuất hiện rõ trong bảng đóng góp backend.</span></section>
        </>
      )}
    </main>
  );
}

function CriterionRow({ criterion }) {
  const width = Math.max(2, Math.min(100, Number(criterion.contribution) * 100));
  return <tr><td><strong>{labelFor(criterion.name)}</strong></td><td>{criterion.grain}</td><td>{formatPercent(criterion.weight)}</td><td>{Number(criterion.normalized_score).toFixed(4)}</td><td>{Number(criterion.contribution).toFixed(4)}</td><td style={S.barCell}><span style={S.barTrack}><span style={{ ...S.barFill, width: `${width}%` }} /></span></td></tr>;
}
function Context({ label, value }) { return <div><small>{label}</small><strong>{value ?? "—"}</strong></div>; }
function LoadingPage() { return <main style={S.page}><Skeleton width={130} /><Skeleton width="35%" height={38} style={{ marginTop: space(4) }} /><Skeleton height={150} style={{ marginTop: space(4) }} /><Skeleton height={300} style={{ marginTop: space(4) }} /></main>; }
function labelFor(value) { return String(value).replaceAll("_", " "); }
function formatScore(value) { return value === null || value === undefined ? "—" : Number(value).toFixed(4); }
function formatPercent(value) { return `${(Number(value) * 100).toFixed(1)}%`; }
function formatArea(value) { return value === null || value === undefined ? "—" : `${Number(value).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} m²`; }
function formatPrice(value) { return value === null || value === undefined ? "—" : `${(Number(value) / 1_000_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tỷ`; }

const S = {
  page: { maxWidth: 1180, margin: "0 auto", paddingBottom: space(8) }, back: { border: 0, background: "transparent", color: color.accent, padding: 0, fontFamily: "inherit", fontWeight: 700, cursor: "pointer" }, header: { margin: `${space(4)}px 0` }, eyebrow: { margin: 0, color: color.muted, fontSize: size.tiny, fontWeight: 700, textTransform: "uppercase" }, h1: { margin: "5px 0 0", color: color.ink, fontFamily: font.display, fontSize: size.h1 }, h2: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 }, sub: { margin: "5px 0 0", color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  heroGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: space(3), marginBottom: space(4) }, scoreCard: { display: "grid", gap: space(2), padding: space(4), borderRadius: radius.md, background: color.accentSoft, boxShadow: shadow }, rankCard: { display: "grid", gap: space(2), padding: space(4), borderRadius: radius.md, background: color.okSoft, boxShadow: shadow }, contextCard: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: space(3), padding: space(4), borderRadius: radius.md, background: color.surface, boxShadow: shadow }, metricLabel: { color: color.muted, fontSize: size.tiny, fontWeight: 700, textTransform: "uppercase" }, scoreValue: { color: color.accent, fontFamily: font.display, fontSize: 40 }, rankValue: { color: color.ok, fontFamily: font.display, fontSize: 40 }, metricHint: { color: color.muted, fontSize: size.tiny },
  notice: { display: "grid", gap: space(2), padding: space(4), borderRadius: radius.md, background: color.warnSoft }, explanation: { display: "flex", alignItems: "flex-start", gap: space(3), marginBottom: space(4), padding: space(4), borderRadius: radius.md, background: color.accentSoft, color: color.body, lineHeight: 1.6 }, spark: { color: color.accent, fontSize: 22 }, card: { padding: space(4), marginBottom: space(4), borderRadius: radius.md, background: color.surface, boxShadow: shadow }, sectionHead: { display: "flex", justifyContent: "space-between", gap: space(3), marginBottom: space(3) }, count: { alignSelf: "start", padding: "5px 9px", borderRadius: radius.pill, background: color.accentSoft, color: color.accent, fontSize: size.tiny, fontWeight: 800 },
  tableWrap: { overflowX: "auto" }, table: { width: "100%", borderCollapse: "collapse", color: color.body, fontSize: size.tiny }, barCell: { minWidth: 130 }, barTrack: { display: "block", height: 8, overflow: "hidden", borderRadius: radius.pill, background: color.canvas }, barFill: { display: "block", height: "100%", borderRadius: radius.pill, background: color.accent }, disclosure: { display: "grid", gap: space(1), padding: space(4), border: `1px solid ${color.border}`, borderRadius: radius.md, color: color.muted, fontSize: size.tiny },
};
