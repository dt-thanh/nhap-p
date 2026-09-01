import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getAbsorptionSummary } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { useProjectScope } from "../hooks/useProjectScope";
import ProjectSelector from "../components/ProjectSelector";
import { ErrorState, EmptyState } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { color, font, radius, shadow, space } from "../styles/tokens";

const TARGET_RATE = 90;
const STATUS = {
  onTrack: { color: "#10B981", soft: "#ECFDF5", label: "Tiếp tục" },
  onWatch: { color: "#F59E0B", soft: "#FFFBEB", label: "Theo dõi" },
  action: { color: "#EF4444", soft: "#FEF2F2", label: "Hành động" },
};

export default function RankingDashboardPage() {
  const navigate = useNavigate();
  const scope = useProjectScope();
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState({ key: "rate", direction: "desc" });
  const projects = useMemo(() => scope.projects.filter((project) => !filter || project.external_id === filter), [filter, scope.projects]);
  const summaries = useAsync(
    () => Promise.all(projects.map(async (project) => {
      try { return [project.project_id, await getAbsorptionSummary(project.project_id)]; }
      catch (error) { return [project.project_id, { error }]; }
    })),
    [projects],
  );
  const summaryMap = useMemo(() => new Map(summaries.data || []), [summaries.data]);
  const rows = useMemo(() => projects.map((project) => normalizeProject(project, summaryMap.get(project.project_id))), [projects, summaryMap]);
  const sortedRows = useMemo(() => [...rows].sort((a, b) => compareRows(a, b, sort)), [rows, sort]);
  const portfolio = useMemo(() => aggregatePortfolio(rows), [rows]);
  const selectedProject = useMemo(() => projects.find((project) => project.external_id === filter) || null, [filter, projects]);
  const context = useMemo(() => buildContext({ projects, selectedProject, rows, portfolio }), [portfolio, projects, rows, selectedProject]);
  const health = useMemo(() => buildRankingHealth(rows), [rows]);
  const attentionRows = useMemo(() => summaries.loading ? [] : rows.filter(isAttentionRow).sort((a, b) => attentionScore(b) - attentionScore(a)).slice(0, 5), [rows, summaries.loading]);
  const toggleSort = (key) => setSort((current) => ({ key, direction: current.key === key && current.direction === "desc" ? "asc" : "desc" }));

  return (
    <>
      <GlobalKeyframes />
      <header style={S.pageHead}>
        <div><p style={S.breadcrumb}>Portfolio analytics <span>/</span> Xếp hạng hấp thụ</p><h1 style={S.h1}>Ranking Units</h1><p style={S.sub}>Những dự án đang chuyển đổi tồn kho nhanh nhất — và lý do.</p><p style={S.context} aria-label="Ngữ cảnh dữ liệu">{context.scope}{context.details.length ? ` · ${context.details.join(" · ")}` : ""}</p></div>
      </header>
      <section style={S.toolbar} aria-label="Bộ lọc dự án"><ProjectSelector projects={scope.projects} value={filter} onChange={setFilter} loading={scope.loadingProjects} status={scope.projectsStatus === "unauthorized" ? "unauthorized" : scope.projectsStatus === "error" ? "error" : undefined} /><div style={S.note}>Dữ liệu chỉ gồm dự án trong phạm vi tài khoản.</div></section>
      {summaries.error ? <ErrorState error={summaries.error} onRetry={summaries.reload} /> : null}
      {!summaries.loading && !summaries.error && !projects.length ? <EmptyState title="Chưa có dự án trong phạm vi" /> : null}
      {projects.length > 0 ? <>
        <section style={S.kpiGrid} aria-label="Chỉ số danh mục">
          <KpiCard label="Tỷ lệ hấp thụ" value={formatPercent(portfolio.rate)} tone={statusFor(portfolio.rate).color} trend={portfolio.rateTrend} hint={`Mục tiêu ${TARGET_RATE}%`} />
          <KpiCard label="Tổng số căn" value={formatInteger(portfolio.totalUnits)} tone="#4F46E5" trend={portfolio.totalTrend} hint="Trong phạm vi đang chọn" />
          <KpiCard label="Đã hấp thụ" value={formatInteger(portfolio.soldUnits)} tone="#4F46E5" trend={portfolio.soldTrend} hint="Đã bán / đã ghi nhận" />
          <KpiCard label="Tồn kho mở" value={formatInteger(portfolio.remainingUnits)} tone="#4F46E5" hint="Chỉ hiển thị khi API cung cấp" />
          <KpiCard label="Ngày trên thị trường" value={formatDays(portfolio.daysOnMarket)} tone="#4F46E5" trend={portfolio.daysTrend} hint="Trung bình từ ngày mở bán" />
        </section>
        {health ? <RankingHealthBanner health={health} /> : null}
        <section style={S.card} aria-labelledby="ranking-title"><div style={S.sectionHead}><div><h2 id="ranking-title" style={S.sectionTitle}>Xếp hạng dự án theo hấp thụ</h2><p style={S.sectionSub}>Sắp xếp danh sách dự án theo chỉ số lựa chọn.</p></div><div style={{ display: "flex", alignItems: "center", gap: space(2) }}><label style={{ color: color.muted, fontSize: 11 }}>Sắp xếp <select value={sort.key} onChange={(event) => setSort({ key: event.target.value, direction: "desc" })} style={{ marginLeft: 4, padding: "4px 6px", border: `1px solid ${color.border}`, borderRadius: radius.sm, background: color.surface, color: color.body, fontFamily: "inherit", fontSize: 11 }}><option value="rate">Hấp thụ</option><option value="name">Tên dự án</option><option value="soldUnits">Đã hấp thụ</option><option value="totalUnits">Tổng căn</option><option value="daysOnMarket">Ngày trên thị trường</option></select></label><button type="button" style={S.sortButton} onClick={() => toggleSort(sort.key)} aria-label="Đổi thứ tự sắp xếp">{sort.direction === "desc" ? "↓" : "↑"}</button><span style={S.benchmark}>Mục tiêu {TARGET_RATE}%</span></div></div>{summaries.loading ? <LoadingRows count={5} /> : <div style={S.rankingList}>{sortedRows.map((row, index) => <RankingBar key={row.id} row={row} rank={index + 1} onOpen={() => navigate(`/ranking/${encodeURIComponent(row.externalId || row.id)}/report`)} />)}{!sortedRows.length ? <InlineEmpty text="Chưa có số liệu hấp thụ cho dự án này." /> : null}</div>}</section>
        <section style={S.twoColumn} aria-label="Cơ hội và rủi ro">
          <OpportunityPanel rows={summaries.loading ? [] : sortedRows.slice(0, 5)} onOpen={(row) => navigate(`/ranking/${encodeURIComponent(row.externalId || row.id)}/report`)} />
          <AttentionPanel rows={attentionRows} onOpen={(row) => navigate(`/ranking/${encodeURIComponent(row.externalId || row.id)}/report`)} />
        </section>
        <section style={S.twoColumn} aria-label="Minh bạch và chất lượng dữ liệu">
          <TransparencyPanel />
          <DataQualityPanel context={context} portfolio={portfolio} rows={rows} />
        </section>
      </> : null}
    </>
  );
}

function normalizeProject(project, summary) {
  const total = numberOrNull(summary?.total_units ?? firstValue(project, summary, ["totalUnits", "total_units"]));
  const sold = numberOrNull(summary?.units_sold ?? firstValue(project, summary, ["absorbedUnits", "absorbed_units", "unitsSold", "units_sold"]));
  const directRate = numberOrNull(summary?.sell_through ?? summary?.absorption_rate ?? firstValue(project, summary, ["absorptionRate"]));
  const rate = directRate ?? (total > 0 && sold !== null ? sold / total * 100 : null);
  const launchDate = project.launch_date ? new Date(project.launch_date) : null;
  const daysOnMarket = launchDate && !Number.isNaN(launchDate.getTime()) ? Math.max(0, Math.round((Date.now() - launchDate.getTime()) / 86400000)) : null;
  const remaining = numberOrNull(summary?.units_remaining ?? summary?.available_remaining_units ?? firstValue(project, summary, ["remainingUnits", "remaining_units", "unitsRemaining"]));
  const scored = numberOrNull(summary?.units_ranked ?? summary?.scored_units ?? summary?.score_coverage?.scored);
  const coverageTotal = numberOrNull(summary?.score_coverage?.total ?? summary?.ranking_total_units ?? total);
  return { id: project.project_id, externalId: project.external_id, name: project.name || "Chưa đặt tên", status: project.status || "Đang theo dõi", rate, totalUnits: total, soldUnits: sold, remainingUnits: remaining, daysOnMarket, velocity30d: numberOrNull(summary?.velocity_30d ?? summary?.avg_velocity_30d), dataStatus: summary?.data_status || null, summaryMessage: summary?.message || null, updatedAt: summary?.updated_at || null, lastSuccessfulSync: summary?.last_successful_sync || null, lastSyncStatus: summary?.last_sync_status || null, calculator: summary?.calculator || summary?.data_source || null, scoredUnits: scored, coverageTotal, rankingState: summary?.ranking_status || summary?.ranking_state || summary?.ranking_health || null, configVersion: summary?.config_version || null, rateTrend: trendDelta(summary, ["previous_sell_through", "last_month_sell_through", "previous_absorption_rate"]), totalTrend: trendDelta(summary, ["previous_total_units", "last_month_total_units"]), soldTrend: trendDelta(summary, ["previous_units_sold", "last_month_units_sold"]), daysTrend: trendDelta(summary, ["previous_days_on_market", "last_month_days_on_market"]) };
}

function firstValue(project, summary, keys) {
  for (const key of keys) {
    if (project?.[key] !== null && project?.[key] !== undefined) return project[key];
    if (summary?.[key] !== null && summary?.[key] !== undefined) return summary[key];
  }
  return null;
}

function aggregatePortfolio(rows) {
  const valid = rows.filter((row) => row.rate !== null || row.totalUnits !== null || row.soldUnits !== null);
  const totalUnits = sum(valid.map((row) => row.totalUnits)); const soldUnits = sum(valid.map((row) => row.soldUnits));
  const rates = valid.filter((row) => row.rate !== null);
  return { totalUnits, soldUnits, remainingUnits: sum(valid.map((row) => row.remainingUnits)), rate: totalUnits > 0 && soldUnits !== null ? soldUnits / totalUnits * 100 : rates.length ? average(rates.map((row) => row.rate)) : null, daysOnMarket: average(valid.map((row) => row.daysOnMarket)), rateTrend: average(valid.map((row) => row.rateTrend)), totalTrend: average(valid.map((row) => row.totalTrend)), soldTrend: average(valid.map((row) => row.soldTrend)), daysTrend: average(valid.map((row) => row.daysTrend)), scoredUnits: sum(valid.map((row) => row.scoredUnits)), coverageTotal: sum(valid.map((row) => row.coverageTotal)) };
}

function RankingBar({ row, rank, onOpen }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const status = statusFor(row.rate); const width = row.rate === null ? 0 : Math.min(Math.max(row.rate, 0), 100);
  const rowStyle = { ...S.rankingRow, ...(hovered ? S.rankingRowHover : {}), ...(focused ? S.rankingRowFocus : {}) };
  return <div role="button" tabIndex={0} style={rowStyle} aria-label={`Mở xếp hạng chi tiết cho ${row.name}`} onClick={onOpen} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); } }} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}><span style={{ ...S.rank, background: status.color }}>{rank}</span><span style={S.projectLabel}><span style={S.projectLink}>{row.name}</span><small>{row.status}</small><small style={S.rowMeta}>{row.totalUnits === null ? "Tổng căn —" : `Tổng ${formatInteger(row.totalUnits)}`} · {row.soldUnits === null ? "Đã hấp thụ —" : `Đã hấp thụ ${formatInteger(row.soldUnits)}`} {row.remainingUnits === null ? "" : ` · Mở ${formatInteger(row.remainingUnits)}`}</small></span><span style={S.barTrack}><span style={{ ...S.barFill, width: `${width}%`, background: status.color }} /></span><span style={S.barValue}>{formatPercent(row.rate)}</span><span style={{ ...S.statusBadge, color: status.color, background: status.soft }}>{status.label}</span></div>;
}

function KpiCard({ label, value, tone, trend, hint }) {
  return <article style={S.kpiCard}><div style={{ ...S.kpiAccent, background: tone }} /><span style={S.kpiLabel}>{label}</span><strong style={S.kpiValue}>{value}</strong><span style={{ ...S.kpiTrend, color: trend == null ? color.muted : trend >= 0 ? STATUS.onTrack.color : STATUS.action.color }}>{trend == null ? "" : `${trend >= 0 ? "↑" : "↓"} ${Math.abs(trend).toFixed(1)}% vs tháng trước`}</span><span style={S.kpiHint}>{hint}</span></article>;
}

function LoadingRows({ count }) { return <div style={S.loadingList}>{Array.from({ length: count }, (_, index) => <div key={index} style={S.loadingRow}><span /><span /><span /></div>)}</div>; }
function InlineEmpty({ text }) { return <div style={S.inlineEmpty}>{text}</div>; }

function OpportunityPanel({ rows, onOpen }) {
  return <section style={S.card} aria-labelledby="opportunity-title"><div style={S.panelHead}><div><h2 id="opportunity-title" style={S.panelTitle}>Cơ hội ưu tiên</h2><p style={S.sectionSub}>Các dự án đứng đầu theo hấp thụ hiện tại.</p></div></div>{rows.length ? <div style={S.compactList}>{rows.map((row, index) => <button key={row.id} type="button" style={S.compactItem} onClick={() => onOpen(row)}><span style={S.compactRank}>{index + 1}</span><span style={S.compactLabel}><strong>{row.name}</strong><small>{row.status}</small></span><span style={S.compactValue}>{formatPercent(row.rate)}</span></button>)}</div> : <InlineEmpty text="Chưa có dữ liệu cơ hội trong phạm vi." />}</section>;
}

function AttentionPanel({ rows, onOpen }) {
  return <section style={S.card} aria-labelledby="attention-title"><div style={S.panelHead}><div><h2 id="attention-title" style={S.panelTitle}>Cần xem xét</h2><p style={S.sectionSub}>Tín hiệu thực tế từ số liệu đang tải.</p></div></div>{rows.length ? <div style={S.compactList}>{rows.map((row) => <button key={row.id} type="button" style={S.compactItem} onClick={() => onOpen(row)}><span style={{ ...S.compactDot, background: statusFor(row.rate).color }} /><span style={S.compactLabel}><strong>{row.name}</strong><small>{attentionReason(row)}</small></span><span style={S.compactValue}>{formatPercent(row.rate)}</span></button>)}</div> : <InlineEmpty text="Chưa có tín hiệu rủi ro có thể hành động trong dữ liệu hiện tại." />}</section>;
}

function TransparencyPanel() {
  return <section style={S.card} aria-labelledby="transparency-title"><h2 id="transparency-title" style={S.panelTitle}>Minh bạch AHP</h2><p style={S.bodyText}>Điểm AHP được lưu từ lần chạy xếp hạng của hệ thống; dashboard này đang xếp dự án theo tỷ lệ hấp thụ.</p><div style={S.formula}>Contribution = weight × normalized score</div><p style={S.bodyText}>Giải thích cấp căn chỉ hiển thị sau khi đi sâu vào dự án/căn và khi có điểm đã lưu. Khả năng so sánh phụ thuộc độ đầy đủ của dữ liệu.</p></section>;
}

function DataQualityPanel({ context, portfolio, rows }) {
  const coverage = portfolio.scoredUnits !== null && portfolio.coverageTotal !== null && portfolio.coverageTotal > 0 ? `${formatInteger(portfolio.scoredUnits)} / ${formatInteger(portfolio.coverageTotal)}` : "—";
  return <section style={S.card} aria-labelledby="quality-title"><h2 id="quality-title" style={S.panelTitle}>Chất lượng dữ liệu</h2><dl style={S.qualityGrid}><dt style={S.qualityTerm}>Dự án trong phạm vi</dt><dd style={S.qualityValue}>{formatInteger(rows.length)}</dd><dt style={S.qualityTerm}>Độ phủ điểm</dt><dd style={S.qualityValue}>{coverage}</dd><dt style={S.qualityTerm}>Cấu hình / lần tính</dt><dd style={S.qualityValue}>{context.config || "—"}</dd><dt style={S.qualityTerm}>Đồng bộ gần nhất</dt><dd style={S.qualityValue}>{context.sync || "—"}</dd></dl>{context.unavailable ? <p style={S.unavailable}>Chi tiết sức khỏe ranking chưa có trong view này.</p> : null}</section>;
}

function RankingHealthBanner({ health }) {
  return <aside style={{ ...S.healthBanner, borderColor: health.tone.color, background: health.tone.soft }} role="status"><strong>{health.title}</strong><span>{health.message}</span></aside>;
}

function buildContext({ projects, selectedProject, rows, portfolio }) {
  const scope = selectedProject?.name || (projects.length ? "Tất cả dự án" : "Chưa có dự án");
  const config = rows.find((row) => row.configVersion)?.configVersion || null;
  const syncValues = rows.map((row) => row.lastSuccessfulSync).filter(Boolean).sort();
  const latestSync = syncValues.length ? syncValues[syncValues.length - 1] : null;
  const details = [];
  if (config) details.push(`AHP config ${config}`);
  if (latestSync) details.push(`Đồng bộ ${formatTimestamp(latestSync)}`);
  if (portfolio.scoredUnits !== null && portfolio.coverageTotal !== null) details.push(`Độ phủ ${formatInteger(portfolio.scoredUnits)}/${formatInteger(portfolio.coverageTotal)} căn`);
  return { scope, details, config, sync: latestSync ? formatTimestamp(latestSync) : null, unavailable: !config && !latestSync && portfolio.scoredUnits === null };
}

function buildRankingHealth(rows) {
  const healthRows = rows.filter((item) => item.rankingState || item.scoredUnits !== null || item.summaryMessage?.includes("ranking"));
  if (!healthRows.length) return null;
  const state = String(healthRows.find((item) => item.rankingState)?.rankingState || "").toLowerCase();
  if (["running", "pending", "processing"].includes(state)) return { title: "Ranking đang được tính", message: "Kết quả sẽ xuất hiện sau khi lần chạy hiện tại hoàn tất.", tone: { color: "#F59E0B", soft: "#FFFBEB" } };
  if (["failed", "error"].includes(state)) return { title: "Ranking chưa khả dụng", message: "Lần tính ranking thất bại; hãy xem chi tiết run theo quyền được cấp.", tone: { color: "#EF4444", soft: "#FEF2F2" } };
  const scored = sum(healthRows.map((item) => item.scoredUnits));
  const total = sum(healthRows.map((item) => item.coverageTotal));
  if (scored === null) return null;
  if (scored === 0) return { title: "Chưa có điểm AHP đã lưu", message: "Phạm vi được chọn chưa có điểm ranking đã lưu.", tone: { color: "#F59E0B", soft: "#FFFBEB" } };
  if (scored !== null && total !== null && scored < total) return { title: "Độ phủ AHP chưa đầy đủ", message: `Mới có ${formatInteger(scored)}/${formatInteger(total)} căn có điểm đã lưu.`, tone: { color: "#F59E0B", soft: "#FFFBEB" } };
  return { title: "Ranking đã có dữ liệu", message: `${formatInteger(scored)} căn có điểm đã lưu trong phạm vi.`, tone: { color: "#10B981", soft: "#ECFDF5" } };
}

function isAttentionRow(row) { return row.rate === null || row.dataStatus === "no_data" || row.dataStatus === "no_units" || (row.daysOnMarket !== null && row.daysOnMarket > 90) || (row.rate !== null && row.rate < TARGET_RATE * 0.7); }
function attentionScore(row) { return (row.rate === null ? 1000 : 100 - row.rate) + (row.daysOnMarket !== null && row.daysOnMarket > 90 ? 25 : 0) + (row.dataStatus && row.dataStatus !== "ready" ? 50 : 0); }
function attentionReason(row) { if (row.rate === null) return "Thiếu số liệu hấp thụ — kiểm tra dữ liệu"; if (row.dataStatus === "no_data" || row.dataStatus === "no_units") return "Chưa có dữ liệu nguồn"; if (row.daysOnMarket !== null && row.daysOnMarket > 90) return `${formatDays(row.daysOnMarket)} — theo dõi tốc độ`; return "Tỷ lệ hấp thụ thấp — đánh giá tiếp"; }
function formatTimestamp(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" }); }
function statusFor(rate) { return STATUS[statusKey(rate)]; }
function statusKey(rate) { if (rate === null || rate < TARGET_RATE * 0.7) return "action"; if (rate < TARGET_RATE) return "onWatch"; return "onTrack"; }
function numberOrNull(value) { const number = Number(value); return value === null || value === undefined || !Number.isFinite(number) ? null : number; }
function sum(values) { const valid = values.filter((value) => value !== null); return valid.length ? valid.reduce((total, value) => total + value, 0) : null; }
function average(values) { const valid = values.filter((value) => value !== null); return valid.length ? valid.reduce((total, value) => total + value, 0) / valid.length : null; }
function trendDelta(summary, keys) { for (const key of keys) { const previous = numberOrNull(summary?.[key]); if (previous !== null) { const current = numberOrNull(summary?.sell_through ?? summary?.absorption_rate ?? summary?.units_sold ?? summary?.total_units); return current === null ? null : current - previous; } } return null; }
function compareRows(a, b, sort) { const av = sort.key === "name" ? a.name : a[sort.key]; const bv = sort.key === "name" ? b.name : b[sort.key]; if (av === bv) return 0; if (av === null || av === undefined) return 1; if (bv === null || bv === undefined) return -1; const comparison = typeof av === "string" ? av.localeCompare(bv) : av - bv; return sort.direction === "asc" ? comparison : -comparison; }
function formatPercent(value) { return value === null ? "—" : `${Number(value).toFixed(1)}%`; }
function formatInteger(value) { return value === null ? "—" : Math.round(value).toLocaleString("vi-VN"); }
function formatDays(value) { return value === null ? "—" : `${Math.round(value)} ngày`; }
const S = {
  pageHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(4) },
  breadcrumb: { margin: 0, color: "#4F46E5", fontSize: 11, fontWeight: 700 }, h1: { margin: "5px 0 0", color: color.ink, fontFamily: font.display, fontSize: 30, letterSpacing: "-.04em" }, sub: { margin: "4px 0 0", color: color.muted, fontSize: 13 }, context: { margin: "7px 0 0", color: color.muted, fontSize: 11, fontVariantNumeric: "tabular-nums" }, headActions: { display: "flex", gap: space(2), flexWrap: "wrap" }, secondary: { border: `1px solid ${color.borderStrong}`, background: color.surface, color: color.body, borderRadius: radius.sm, padding: "9px 13px", fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  toolbar: { display: "flex", alignItems: "flex-end", gap: space(4), flexWrap: "wrap", padding: space(3), marginBottom: space(4), background: color.surface, borderRadius: radius.md, boxShadow: shadow }, note: { alignSelf: "center", color: color.muted, fontSize: 12 }, kpiGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: space(3), marginBottom: space(4) }, kpiCard: { position: "relative", overflow: "hidden", display: "grid", gap: 3, minHeight: 132, padding: space(4), background: color.surface, borderRadius: radius.md, boxShadow: shadow }, kpiAccent: { position: "absolute", inset: "0 0 auto", height: 4 }, kpiLabel: { color: color.muted, fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em" }, kpiValue: { color: color.ink, fontFamily: font.display, fontSize: 34, lineHeight: 1.1, letterSpacing: "-.04em" }, kpiTrend: { fontSize: 11, fontWeight: 700 }, kpiHint: { color: color.muted, fontSize: 11 },
  card: { background: color.surface, borderRadius: radius.md, padding: space(4), boxShadow: shadow, marginBottom: space(4) }, sectionHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), marginBottom: space(3) }, sectionTitle: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: 19, fontWeight: 800 }, sectionSub: { margin: "4px 0 0", color: color.muted, fontSize: 12 }, benchmark: { color: color.muted, fontSize: 11, whiteSpace: "nowrap" }, rankingList: { display: "grid", gap: space(2) }, rankingRow: { display: "grid", gridTemplateColumns: "24px minmax(145px, 1.1fr) minmax(120px, 2fr) 62px auto", gap: space(2), alignItems: "center", width: "100%", border: 0, padding: "7px 0", background: "transparent", textAlign: "left", cursor: "pointer", fontFamily: "inherit", transition: "background-color 160ms ease, box-shadow 160ms ease, transform 160ms ease" }, rankingRowHover: { background: "#F8FAFC", boxShadow: shadow, transform: "translateY(-1px)" }, rankingRowFocus: { outline: "2px solid #4F46E5", outlineOffset: 2 }, rank: { display: "grid", placeItems: "center", width: 22, height: 22, borderRadius: 6, color: "white", fontSize: 11, fontWeight: 800 }, projectLabel: { minWidth: 0, display: "grid", gap: 2 }, projectLink: { display: "block", overflow: "hidden", padding: 0, border: 0, background: "transparent", color: "#4F46E5", font: "inherit", fontWeight: 800, textAlign: "left", textOverflow: "ellipsis", whiteSpace: "nowrap", cursor: "pointer" }, rowMeta: { color: color.muted, fontSize: 10 }, barTrack: { height: 13, overflow: "hidden", background: "#E9EEF5", borderRadius: radius.pill }, barFill: { display: "block", height: "100%", borderRadius: radius.pill, transition: "width 240ms ease" }, barValue: { color: color.ink, fontSize: 13, fontWeight: 800, textAlign: "right", fontVariantNumeric: "tabular-nums" }, statusBadge: { justifySelf: "start", padding: "4px 8px", borderRadius: radius.pill, fontSize: 10, fontWeight: 800, whiteSpace: "nowrap" }, sortButton: { border: 0, background: "transparent", color: "inherit", font: "inherit", cursor: "pointer", padding: 0 }, inlineEmpty: { padding: `${space(6)}px ${space(3)}px`, color: color.muted, textAlign: "center", fontSize: 12 }, loadingList: { display: "grid", gap: space(3) }, loadingRow: { display: "grid", gridTemplateColumns: "20% 60% 10%", gap: space(2) }, twoColumn: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: space(4) }, panelHead: { marginBottom: space(3) }, panelTitle: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: 16, fontWeight: 800 }, compactList: { display: "grid", gap: space(2) }, compactItem: { display: "grid", gridTemplateColumns: "24px minmax(0, 1fr) auto", gap: space(2), alignItems: "center", width: "100%", border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: "9px 10px", background: color.surface, color: color.body, textAlign: "left", cursor: "pointer", fontFamily: "inherit", transition: "background-color 160ms ease, box-shadow 160ms ease" }, compactRank: { display: "grid", placeItems: "center", width: 20, height: 20, borderRadius: radius.pill, background: "#EEF2FF", color: "#4F46E5", fontSize: 10, fontWeight: 800 }, compactDot: { width: 10, height: 10, borderRadius: radius.pill }, compactLabel: { minWidth: 0, display: "grid", gap: 2 }, compactValue: { color: color.ink, fontWeight: 800, fontSize: 12 }, healthBanner: { display: "flex", flexWrap: "wrap", gap: space(2), alignItems: "baseline", border: "1px solid", borderRadius: radius.md, padding: `${space(3)}px ${space(4)}px`, marginBottom: space(4), fontSize: 12 }, bodyText: { margin: `${space(3)}px 0 0`, color: color.body, fontSize: 12, lineHeight: 1.5 }, formula: { marginTop: space(3), padding: `${space(2)}px ${space(3)}px`, borderRadius: radius.sm, background: "#F8FAFC", color: "#4F46E5", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 11 }, qualityGrid: { display: "grid", gridTemplateColumns: "1fr auto", gap: `${space(2)}px ${space(4)}px`, margin: `${space(3)}px 0 0`, fontSize: 12 }, qualityTerm: { color: color.muted }, qualityValue: { margin: 0, color: color.ink, fontWeight: 700, textAlign: "right" }, unavailable: { margin: `${space(3)}px 0 0`, color: color.muted, fontSize: 11 },
};
