// Project-level historical absorption ranking shown inside /ranking.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getHistoricalRanking } from "../api/endpoints";
import { useProjectScope } from "../hooks/useProjectScope";
import { RankingSkeleton } from "./RankingSkeleton";
import { SectionState, fmt } from "./ui/States";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

const CONFIDENCE_FILTERS = [
  { key: null, label: "Tất cả" },
  { key: "high", label: "Cao" },
  { key: "medium", label: "Trung bình" },
  { key: "insufficient_history", label: "Không đủ lịch sử" },
];

const CONFIDENCE_LABEL = {
  high: "Cao",
  medium: "Trung bình",
  insufficient_history: "Không đủ lịch sử",
};

const COMPONENT_COLUMNS = [
  { key: "absorption_30d_score", label: "Hấp thụ 30 ngày" },
  { key: "absorption_90d_score", label: "Hấp thụ 90 ngày" },
  { key: "velocity_30d_score", label: "Tốc độ bán" },
  { key: "momentum_score", label: "Đà bán" },
  { key: "stability_score", label: "Ổn định" },
];

function todayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

function toPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 1000) / 10 : null;
}

function fetchOne(project, asOfDate) {
  const externalId = project.external_id;
  if (!externalId) return Promise.resolve({ project, row: null, error: "Chưa có external_id" });
  const params = asOfDate ? { as_of_date: `${asOfDate}T00:00:00Z` } : {};
  return getHistoricalRanking(externalId, params)
    .then((row) => ({ project, row, error: null }))
    .catch((error) => ({ project, row: null, error: error?.message || "Không tải được" }));
}

function toCsv(rows) {
  const header = ["project", "external_id", "as_of_date", "score", "confidence", ...COMPONENT_COLUMNS.map((c) => c.key)];
  const lines = [header.join(",")];
  for (const { project, row } of rows) {
    const cells = [
      project.name,
      project.external_id,
      row?.as_of_date ?? "",
      row?.score ?? "",
      row?.confidence ?? "",
      ...COMPONENT_COLUMNS.map((c) => row?.components?.[c.key] ?? ""),
    ];
    lines.push(cells.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(","));
  }
  return lines.join("\n");
}

function downloadCsv(rows) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `historical-ranking-${todayIsoDate()}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export default function HistoricalTab() {
  const scope = useProjectScope();
  const [asOfDate, setAsOfDate] = useState("");
  const [confidenceFilter, setConfidenceFilter] = useState(null);
  const [sortKey, setSortKey] = useState("score");
  const [sortDir, setSortDir] = useState("desc");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [exported, setExported] = useState(false);
  const loadVersion = useRef(0);
  const projects = scope.projects || [];

  const load = useCallback(async () => {
    const currentLoad = ++loadVersion.current;
    setLoading(true);
    setError(null);
    if (projects.length === 0) {
      if (currentLoad === loadVersion.current) {
        setRows([]);
        setLoading(false);
      }
      return;
    }
    try {
      const nextRows = await Promise.all(projects.map((project) => fetchOne(project, asOfDate)));
      if (currentLoad === loadVersion.current) setRows(nextRows);
    } catch (loadError) {
      if (currentLoad === loadVersion.current) setError({ message: loadError?.message || "Không tải được bảng so sánh" });
    } finally {
      if (currentLoad === loadVersion.current) setLoading(false);
    }
  }, [projects, asOfDate]);

  useEffect(() => {
    setLoading(true);
    const timer = window.setTimeout(load, 300);
    return () => window.clearTimeout(timer);
  }, [load]);

  const scopeError = scope.projectsStatus === "error"
    ? { message: "Không tải được danh sách dự án trong phạm vi hiện tại." }
    : null;
  const viewError = error || scopeError;
  const viewLoading = loading || Boolean(scope.loadingProjects);

  const filtered = useMemo(() => {
    const list = confidenceFilter
      ? rows.filter((row) => (row.row?.confidence ?? null) === confidenceFilter)
      : rows;
    const direction = sortDir === "desc" ? -1 : 1;
    return [...list].sort((a, b) => {
      const av = Number(sortKey === "score" ? a.row?.score ?? -1 : a.row?.components?.[sortKey] ?? -1);
      const bv = Number(sortKey === "score" ? b.row?.score ?? -1 : b.row?.components?.[sortKey] ?? -1);
      return (av - bv) * direction;
    });
  }, [rows, confidenceFilter, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((direction) => (direction === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  const exportVisibleRows = () => {
    downloadCsv(filtered);
    setExported(true);
    window.setTimeout(() => setExported(false), 2400);
  };

  return (
    <>
      <header style={S.pageHead}>
        <div>
          <h1 style={S.h1}>Xếp hạng lịch sử theo dự án</h1>
          <p style={S.sub}>So sánh hiệu quả hấp thụ quá khứ tại một mốc thời gian đã chọn.</p>
        </div>
      </header>

      <section style={S.filterBar} aria-label="Bộ lọc lịch sử">
        <label style={S.filterLabel}>
          Tính tại ngày
          <input
            type="date"
            value={asOfDate}
            max={todayIsoDate()}
            onChange={(event) => setAsOfDate(event.target.value)}
            style={S.input}
          />
        </label>
        <label style={S.filterLabel}>
          Độ tin cậy
          <select
            aria-label="Độ tin cậy"
            value={confidenceFilter ?? ""}
            onChange={(event) => setConfidenceFilter(event.target.value || null)}
            style={S.input}
          >
            {CONFIDENCE_FILTERS.map((filter) => <option key={filter.key ?? "all"} value={filter.key ?? ""}>{filter.label}</option>)}
          </select>
        </label>
        <button style={S.exportButton} onClick={exportVisibleRows} disabled={filtered.length === 0} aria-label="Xuất CSV">
          Xuất CSV
        </button>
      </section>

      <SectionState
        loading={viewLoading}
        error={viewError}
        empty={!viewLoading && !viewError && filtered.length === 0}
        emptyTitle="Không có dự án nào khớp bộ lọc"
        onRetry={load}
        skeleton={<RankingSkeleton />}
      >
        <div className="ranking-card" style={S.tableWrap}>
          <table className="ranking-table" style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Dự án</th>
                <th style={S.thSortable} onClick={() => toggleSort("score")}>Điểm{sortKey === "score" ? (sortDir === "desc" ? " ▼" : " ▲") : ""}</th>
                <th style={S.th}>Độ tin cậy</th>
                {COMPONENT_COLUMNS.map((column) => (
                  <th key={column.key} style={S.thSortable} onClick={() => toggleSort(column.key)}>
                    {column.label}{sortKey === column.key ? (sortDir === "desc" ? " ▼" : " ▲") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(({ project, row, error: rowError }) => (
                <tr key={project.project_id || project.external_id}>
                  <td style={S.td}>{project.name}</td>
                  <td style={S.td}>{rowError ? "—" : row?.score != null ? `${toPercent(row.score)}%` : "Chưa có dữ liệu"}</td>
                  <td style={S.td}>{rowError ? <span style={S.rowError} title={rowError}>Lỗi tải</span> : CONFIDENCE_LABEL[row?.confidence] || row?.confidence || "—"}</td>
                  {COMPONENT_COLUMNS.map((column) => <td key={column.key} style={S.td}>{rowError ? "—" : fmt(toPercent(row?.components?.[column.key]), { suffix: "%" })}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionState>
      {exported && <div className="ranking-toast" role="status" aria-live="polite">Đã xuất CSV thành công.</div>}
    </>
  );
}

const S = {
  pageHead: { marginBottom: space(4) },
  h1: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h1 },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.small },
  filterBar: { display: "flex", gap: space(3), alignItems: "flex-end", marginBottom: space(4), flexWrap: "wrap" },
  filterLabel: { display: "flex", flexDirection: "column", gap: space(1), fontSize: size.tiny, color: color.body, fontWeight: 600 },
  input: { border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: "8px 10px", fontSize: size.small, fontFamily: "inherit" },
  exportButton: { background: color.surface, border: `1px solid ${color.borderStrong}`, color: color.body, borderRadius: radius.sm, padding: "8px 14px", fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit" },
  tableWrap: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflowX: "auto" },
  table: { width: "100%", fontSize: size.small, minWidth: 760 },
  th: { textAlign: "left", padding: space(3), color: color.muted, fontWeight: 600, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  thSortable: { textAlign: "left", padding: space(3), color: color.muted, fontWeight: 600, borderBottom: `1px solid ${color.border}`, cursor: "pointer", whiteSpace: "nowrap" },
  td: { padding: space(3), color: color.ink, borderBottom: `1px solid ${color.border}` },
  rowError: { color: color.danger },
};
