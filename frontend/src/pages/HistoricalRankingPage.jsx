// frontend/src/pages/HistoricalRankingPage.jsx
// Bảng so sánh các dự án theo `historical_ranking_score` — đường ĐỌC của
// `GET /api/v1/ranking/historical`.
//
// Khác hẳn `/ranking` (RankingPage.jsx): trang đó xếp hạng TỪNG CĂN, hiện tại,
// đọc `ranking_scores` đã lưu. Trang này xếp hạng TỪNG DỰ ÁN, tại một mốc quá
// khứ tuỳ chọn (`as_of_date`), gấp từ `unit_status_history`/`deal_status_history`
// — không có kết quả nào được LƯU để tham chiếu lại, nên không có nút "tính lại"
// (mỗi lần tải trang đã là một lần tính, luôn tất định trên cùng dữ liệu).
//
// Không có backend endpoint liệt kê "điểm lịch sử của mọi dự án" — endpoint chỉ
// nhận một `external_project_id`. Trang này lấy danh sách dự án từ
// `useProjectScope()` (đã dùng ở mọi trang scoped khác) rồi gọi endpoint đó
// MỘT LẦN CHO MỖI dự án, song song — chấp nhận N lời gọi thay vì thêm một
// endpoint tổng hợp mới chỉ để phục vụ đúng một bảng so sánh.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { getHistoricalRanking } from "../api/endpoints";
import { useProjectScope } from "../hooks/useProjectScope";
import { SectionState, fmt } from "../components/ui/States";
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

function toPercent(scoreString) {
  const n = Number(scoreString);
  return Number.isFinite(n) ? Math.round(n * 1000) / 10 : null;
}

/** Gọi `getHistoricalRanking` cho MỘT dự án; lỗi của một dự án không được kéo
 *  sập cả bảng — trang khác vẫn phải đọc được điểm của N-1 dự án còn lại. */
async function fetchOne(project, asOfDate) {
  const externalId = project.external_id;
  if (!externalId) return { project, row: null, error: "Dự án di sản, chưa có external_id" };
  try {
    const params = asOfDate ? { as_of_date: `${asOfDate}T00:00:00Z` } : {};
    const row = await getHistoricalRanking(externalId, params);
    return { project, row, error: null };
  } catch (e) {
    return { project, row: null, error: e?.message || "Không tải được" };
  }
}

function toCsv(rows) {
  const header = ["project", "external_id", "as_of_date", "score", "confidence", ...COMPONENT_COLUMNS.map((c) => c.key)];
  const lines = [header.join(",")];
  for (const r of rows) {
    const cells = [
      r.project.name, r.project.external_id, r.row?.as_of_date ?? "", r.row?.score ?? "",
      r.row?.confidence ?? "", ...COMPONENT_COLUMNS.map((c) => r.row?.components?.[c.key] ?? ""),
    ];
    lines.push(cells.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(","));
  }
  return lines.join("\n");
}

function downloadCsv(rows) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `historical-ranking-${todayIsoDate()}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function HistoricalRankingPage() {
  const scope = useProjectScope();
  const [asOfDate, setAsOfDate] = useState("");
  const [confidenceFilter, setConfidenceFilter] = useState(null);
  const [sortKey, setSortKey] = useState("score");
  const [sortDir, setSortDir] = useState("desc");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const projects = scope.projects || [];

  const load = useCallback(async () => {
    if (projects.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const results = await Promise.all(projects.map((p) => fetchOne(p, asOfDate)));
      setRows(results);
    } catch (e) {
      setError({ message: e?.message || "Không tải được bảng so sánh" });
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects.length, asOfDate]);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    let list = rows;
    if (confidenceFilter) {
      list = list.filter((r) => (r.row?.confidence ?? null) === confidenceFilter);
    }
    const dir = sortDir === "desc" ? -1 : 1;
    return [...list].sort((a, b) => {
      const av = sortKey === "score" ? Number(a.row?.score ?? -1) : Number(a.row?.components?.[sortKey] ?? -1);
      const bv = sortKey === "score" ? Number(b.row?.score ?? -1) : Number(b.row?.components?.[sortKey] ?? -1);
      return (av - bv) * dir;
    });
  }, [rows, confidenceFilter, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  return (
    <>
      <header style={S.pageHead}>
        <div>
          <h1 style={S.h1}>So sánh dự án theo hấp thụ lịch sử</h1>
          <p style={S.sub}>
            Điểm gấp từ nhật ký chuyển trạng thái đơn vị/giao dịch, tính tại một mốc bất kỳ — không phải bảng
            xếp hạng từng căn (xem <code>/ranking</code> cho việc đó).
          </p>
        </div>
      </header>

      <section style={S.filterBar} aria-label="Bộ lọc">
        <label style={S.filterLabel}>
          Tính tại ngày
          <input
            type="date"
            value={asOfDate}
            max={todayIsoDate()}
            onChange={(e) => setAsOfDate(e.target.value)}
            style={S.dateInput}
          />
        </label>
        <label style={S.filterLabel}>
          Độ tin cậy
          <select
            value={confidenceFilter ?? ""}
            onChange={(e) => setConfidenceFilter(e.target.value || null)}
            style={S.select}
          >
            {CONFIDENCE_FILTERS.map((f) => (
              <option key={f.key ?? "all"} value={f.key ?? ""}>{f.label}</option>
            ))}
          </select>
        </label>
        <button style={S.exportButton} onClick={() => downloadCsv(filtered)} disabled={filtered.length === 0}>
          Xuất CSV
        </button>
      </section>

      <SectionState
        loading={loading}
        error={error}
        empty={!loading && !error && filtered.length === 0}
        emptyTitle="Không có dự án nào khớp bộ lọc"
        onRetry={load}
      >
        <div style={S.tableWrap}>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Dự án</th>
                <th style={S.thSortable} onClick={() => toggleSort("score")}>
                  Điểm hợp thành{sortKey === "score" ? (sortDir === "desc" ? " ▼" : " ▲") : ""}
                </th>
                <th style={S.th}>Độ tin cậy</th>
                {COMPONENT_COLUMNS.map((c) => (
                  <th key={c.key} style={S.thSortable} onClick={() => toggleSort(c.key)}>
                    {c.label}{sortKey === c.key ? (sortDir === "desc" ? " ▼" : " ▲") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(({ project, row, error: rowError }) => (
                <tr key={project.project_id}>
                  <td style={S.td}>{project.name}</td>
                  <td style={S.td}>
                    {rowError ? "—" : row?.score != null ? `${toPercent(row.score)}%` : "Chưa có dữ liệu"}
                  </td>
                  <td style={S.td}>
                    {rowError ? (
                      <span style={S.rowError} title={rowError}>Lỗi tải</span>
                    ) : (
                      CONFIDENCE_LABEL[row?.confidence] || row?.confidence || "—"
                    )}
                  </td>
                  {COMPONENT_COLUMNS.map((c) => (
                    <td key={c.key} style={S.td}>
                      {rowError ? "—" : fmt(toPercent(row?.components?.[c.key]), { suffix: "%" })}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionState>
    </>
  );
}

const S = {
  pageHead: { marginBottom: space(4) },
  h1: { fontFamily: font.display, fontSize: size.h1, color: color.ink, margin: 0 },
  sub: { fontSize: size.small, color: color.muted, marginTop: space(1) },
  filterBar: { display: "flex", gap: space(3), alignItems: "flex-end", marginBottom: space(4), flexWrap: "wrap" },
  filterLabel: { display: "flex", flexDirection: "column", gap: space(1), fontSize: size.tiny, color: color.body, fontWeight: 600 },
  dateInput: {
    border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: "8px 10px", fontSize: size.small, fontFamily: "inherit",
  },
  select: {
    border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: "8px 10px", fontSize: size.small, fontFamily: "inherit",
  },
  exportButton: {
    background: color.surface, border: `1px solid ${color.borderStrong}`, color: color.body, borderRadius: radius.sm,
    padding: "8px 14px", fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", alignSelf: "flex-end",
  },
  tableWrap: {
    background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflowX: "auto",
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: size.small },
  th: { textAlign: "left", padding: space(3), color: color.muted, fontWeight: 600, borderBottom: `1px solid ${color.border}` },
  thSortable: {
    textAlign: "left", padding: space(3), color: color.muted, fontWeight: 600, borderBottom: `1px solid ${color.border}`, cursor: "pointer",
  },
  td: { padding: space(3), color: color.ink, borderBottom: `1px solid ${color.border}` },
  rowError: { color: color.danger },
};
