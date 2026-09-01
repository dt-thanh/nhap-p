import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getProjectRankingReport } from "../api/endpoints";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/States";
import { useAsync } from "../hooks/useAsync";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

const STATE_COPY = {
  feature_disabled: ["Chế độ xem AHP đang tắt", "Các phân khu vẫn được liệt kê nhưng điểm AHP không được công bố."],
  not_run: ["Chưa có lần xếp hạng", "Mở ranking workspace để chủ động chạy khi dữ liệu đã sẵn sàng."],
  unavailable: ["Kết quả xếp hạng chưa khả dụng", "Dữ liệu đầu vào chưa đủ để tạo kết quả đã lưu."],
  stale: ["Báo cáo cần được làm mới", "Backend đã đánh dấu snapshot này là cũ."],
  no_scored_units: [
    "Ranking hiện hành đang dùng dữ liệu CRM theo cấu hình v2.",
    "AHP phân cấp chưa được công bố cho dự án này.",
  ],
};

const SCORE_MODE_LABEL = {
  unit_only: "Chỉ cấp căn",
  partial_hierarchical: "Phân cấp một phần",
  full_hierarchical: "Phân cấp đầy đủ",
  legal_gated: "Bị chặn bởi cổng pháp lý",
};

function HierarchyStatusBanner({ report }) {
  const status = report.hierarchy_status || "not_published";
  if (status === "crm_only") {
    return (
      <section style={S.hierarchyBanner} aria-live="polite">
        <strong>Ranking hiện hành dùng cấu hình v{report.config_version}.</strong>
        <span>Điểm hiện tại được tính từ dữ liệu CRM.</span>
        <span>Chưa có đánh giá cố vấn hợp lệ được áp dụng.</span>
      </section>
    );
  }
  if (status === "expert_enriched") {
    return (
      <section style={S.hierarchyBanner} aria-live="polite">
        <strong>Ranking hiện hành dùng cấu hình v{report.config_version}.</strong>
        <span>Điểm hiện tại kết hợp dữ liệu CRM và đánh giá cố vấn đã được CEO duyệt.</span>
        {report.expert_criteria_applied?.length > 0 && (
          <span>Tiêu chí cố vấn đang áp dụng: {report.expert_criteria_applied.join(", ")}.</span>
        )}
      </section>
    );
  }
  return null;
}

function HierarchyDisclosureDetail({ report }) {
  const excluded = Object.entries(report.representative_excluded_grains || {});
  const modeCounts = Object.entries(report.score_mode_counts || {});
  if (!modeCounts.length && !excluded.length && !report.representative_effective_grain_weights) return null;
  return (
    <section style={S.disclosureGrid} aria-label="Chi tiết phân bổ điểm AHP">
      {modeCounts.length > 0 && (
        <div>
          <dt style={S.metaLabel}>Phân bổ theo chế độ tính</dt>
          <dd style={S.metaValue}>
            {modeCounts.map(([mode, count]) => `${SCORE_MODE_LABEL[mode] || mode}: ${count}`).join(" · ")}
          </dd>
        </div>
      )}
      {report.representative_effective_grain_weights && (
        <div>
          <dt style={S.metaLabel}>Trọng số hiệu lực (đại diện)</dt>
          <dd style={S.metaValue}>
            {Object.entries(report.representative_effective_grain_weights)
              .map(([grain, weight]) => `${grain}: ${Number(weight).toFixed(2)}`)
              .join(" · ")}
          </dd>
        </div>
      )}
      {excluded.length > 0 && (
        <div>
          <dt style={S.metaLabel}>Nhóm bị loại (lý do an toàn)</dt>
          <dd style={S.metaValue}>
            {excluded.map(([grain, info]) => `${grain}: ${info?.reason || "không xác định"}`).join(" · ")}
          </dd>
        </div>
      )}
    </section>
  );
}

export default function ProjectRankingReportPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { data: report, loading, error, reload } = useAsync(
    () => getProjectRankingReport(projectId),
    [projectId],
  );

  if (loading) return <LoadingPage />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!report) return null;
  const stateCopy = STATE_COPY[report.state];
  const flatRankingHref = `/ranking/${encodeURIComponent(report.project.external_id)}`;

  return (
    <main style={S.page}>
      <button type="button" style={S.back} onClick={() => navigate("/ranking")}>← Về ranking dashboard</button>
      <header style={S.header}>
        <p style={S.eyebrow}>Xếp hạng căn hộ · {report.project.external_id}</p>
        <h1 style={S.h1}>{report.project.name}</h1>
        <p style={S.sub}>Chọn phân khu để xem danh sách căn được xếp theo điểm AHP đã lưu.</p>
      </header>

      <section style={S.metaGrid} aria-label="Thông tin ranking run">
        <Meta label="Phân khu" value={report.areas.length} />
        <Meta label="Căn có điểm AHP" value={report.persisted_hierarchical_scores} />
        <Meta label="Phiên bản cấu hình" value={report.config_version ?? "Chưa có"} />
        <Meta label="Thời điểm tính" value={formatTimestamp(report.computed_at)} />
      </section>

      {report.state !== "ready" ? (
        <section style={S.notice} aria-live="polite">
          <strong>{stateCopy?.[0] || "Kết quả chưa sẵn sàng"}</strong>
          <span>{stateCopy?.[1] || "Không có dữ liệu phù hợp trong báo cáo hiện tại."}</span>
          <code style={S.reason}>{report.reason || "UNAVAILABLE"}</code>
          {report.state === "no_scored_units" && (report.hierarchy_status || "not_published") === "not_published" && (
            <button type="button" style={S.ctaButton} onClick={() => navigate(flatRankingHref)}>
              Xem điểm CRM v2 tại đây →
            </button>
          )}
        </section>
      ) : (
        <HierarchyStatusBanner report={report} />
      )}
      {report.state === "ready" && <HierarchyDisclosureDetail report={report} />}

      <section style={S.compareLink}>
        <a
          href={flatRankingHref}
          onClick={(event) => { event.preventDefault(); navigate(flatRankingHref); }}
        >
          Điểm CRM v2 — dùng để đối chiếu →
        </a>
      </section>

      <section aria-labelledby="areas-title">
        <div style={S.sectionHead}>
          <div><h2 id="areas-title" style={S.h2}>Phân khu / zone</h2><p style={S.sub}>Điểm trung bình chỉ tổng hợp các hierarchical score cấp căn đã lưu.</p></div>
          <span style={S.count}>{report.areas.length} phân khu</span>
        </div>
        {!report.areas.length ? <EmptyState title="Chưa có phân khu" hint="Dữ liệu phân khu chưa được đồng bộ cho dự án này." /> : (
          <div style={S.areaGrid}>
            {report.areas.map((area) => (
              <button
                key={area.area_id}
                type="button"
                disabled={!area.external_id}
                aria-label={`Mở xếp hạng căn hộ tại ${area.name}`}
                style={S.areaCard}
                onClick={() => navigate(`/ranking/${encodeURIComponent(report.project.external_id)}/areas/${encodeURIComponent(area.external_id)}`)}
              >
                <span style={S.areaName}>{area.name}</span>
                <span style={S.areaStats}><span><small>Số căn</small><strong>{area.apartment_count}</strong></span><span><small>Điểm AHP TB</small><strong>{formatScore(area.average_ahp_score)}</strong></span></span>
                <span style={S.areaFoot}>{area.scored_apartment_count}/{area.apartment_count} căn có điểm <b>→</b></span>
              </button>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function Meta({ label, value }) { return <div><dt style={S.metaLabel}>{label}</dt><dd style={S.metaValue}>{String(value)}</dd></div>; }
function LoadingPage() { return <main style={S.page}><Skeleton width={130} /><Skeleton width="42%" height={38} style={{ marginTop: space(4) }} /><Skeleton height={110} style={{ marginTop: space(4) }} /><Skeleton height={260} style={{ marginTop: space(4) }} /></main>; }
function formatTimestamp(value) { return value ? new Date(value).toLocaleString("vi-VN") : "Chưa có"; }
function formatScore(value) { return value === null || value === undefined ? "—" : Number(value).toFixed(4); }

const S = {
  page: { maxWidth: 1180, margin: "0 auto", paddingBottom: space(8) },
  back: { border: 0, background: "transparent", color: color.accent, padding: 0, fontFamily: "inherit", fontWeight: 700, cursor: "pointer" },
  header: { margin: `${space(4)}px 0` },
  eyebrow: { margin: 0, color: color.muted, fontSize: size.tiny, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase" },
  h1: { margin: "5px 0 0", color: color.ink, fontFamily: font.display, fontSize: size.h1, letterSpacing: "-.03em" },
  h2: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },
  metaGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: space(3), padding: space(4), marginBottom: space(4), background: color.surface, borderRadius: radius.md, boxShadow: shadow },
  metaLabel: { margin: 0, color: color.muted, fontSize: size.tiny }, metaValue: { margin: "4px 0 0", color: color.ink, fontWeight: 800 },
  notice: { display: "grid", gap: space(1), marginBottom: space(4), padding: space(4), borderRadius: radius.md, background: color.warnSoft, color: color.body, fontSize: size.small },
  reason: { justifySelf: "start", marginTop: space(1), padding: "3px 6px", borderRadius: radius.sm, background: color.surface },
  ctaButton: { justifySelf: "start", marginTop: space(2), border: 0, borderRadius: radius.sm, padding: "9px 14px", background: color.accent, color: "#fff", fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  hierarchyBanner: { display: "grid", gap: space(1), marginBottom: space(4), padding: space(4), borderRadius: radius.md, background: color.okSoft, color: color.body, fontSize: size.small },
  disclosureGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: space(3), padding: space(4), marginBottom: space(4), background: color.canvas, borderRadius: radius.md },
  compareLink: { marginBottom: space(4) },
  sectionHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), marginBottom: space(3) },
  count: { padding: "5px 9px", borderRadius: radius.pill, background: color.accentSoft, color: color.accent, fontSize: size.tiny, fontWeight: 800 },
  areaGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: space(3) },
  areaCard: { display: "grid", gap: space(4), padding: space(4), border: `1px solid ${color.border}`, borderRadius: radius.md, background: color.surface, boxShadow: shadow, color: color.body, textAlign: "left", cursor: "pointer", fontFamily: "inherit", transition: "transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease" },
  areaName: { color: color.ink, fontFamily: font.display, fontSize: size.h2, fontWeight: 800 },
  areaStats: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: space(3) },
  areaFoot: { display: "flex", justifyContent: "space-between", color: color.muted, fontSize: size.tiny },
};
