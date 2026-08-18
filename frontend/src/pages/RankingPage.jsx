// frontend/src/pages/RankingPage.jsx
// Bảng xếp hạng căn — đường ĐỌC của `GET /api/v1/ranking`.
//
// Nguyên tắc trình bày của trang này:
//
// 1. **Điểm không bao giờ đứng một mình.** Mỗi dòng mở ra được phần "vì sao":
//    từng đặc trưng, trọng số, và phần điểm nó đóng góp — lấy thẳng từ
//    `ranking_scores.contributions`. Một con số 65.9% không nói cho đội bán hàng
//    biết phải làm gì; "nhu cầu 0.33 × 0.25" thì có.
//
// 2. **Không xếp hạng lại khi mở trang.** Nút "Tính lại" là hành động TƯỜNG MINH
//    của người dùng (và cần quyền cao hơn), vì tính lại thay thế toàn bộ điểm
//    của dự án — xem docstring `src/api/ranking.py`.
//
// 3. **Mức (`band`) hiển thị đúng như backend trả về.** Ngưỡng nằm ở
//    `src/ranking/bands.py`; tính lại ngưỡng ở đây sẽ tạo bản sao thứ hai của
//    một quy tắc nghiệp vụ và hai bên sẽ lệch nhau.
//
// 4. **Miễn trừ trách nhiệm luôn hiện.** Backend gửi kèm chuỗi cố định đó ở mọi
//    phản hồi; AGENTS.md coi xếp hạng là đầu vào cho người quyết định, không
//    phải cam kết kết quả bán hàng.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getRanking, runRanking } from "../api/endpoints";
import { isAuthError } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { useProjectScope } from "../hooks/useProjectScope";
import ProjectSelector from "../components/ProjectSelector";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { SectionState, fmt } from "../components/ui/States";
import { areaLabel } from "../utils/areaLabel";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

const PAGE_SIZE = 50;

const BAND_STYLE = {
  high: { label: "Cao", fg: color.ok, bg: color.okSoft },
  medium: { label: "Trung bình", fg: color.warn, bg: color.warnSoft },
  low: { label: "Thấp", fg: color.muted, bg: color.canvas },
};

const UNIT_STATUS_LABEL = {
  available: "Còn trống",
  reserved: "Đang giữ",
  sold: "Đã bán",
  blocked: "Tạm khoá",
};

// Tên đặc trưng bằng tiếng Việt cho phần giải thích. Khoá nào chưa có ở đây thì
// hiện nguyên khoá kỹ thuật — thà lộ `unit_demand_norm` còn hơn giấu mất một
// đặc trưng vừa được thêm vào config mà bảng này chưa kịp cập nhật.
const FEATURE_LABEL = {
  unit_available: "Căn còn trống",
  unit_demand_norm: "Nhu cầu trên căn (deal đang trong phễu)",
  has_active_deal: "Đang có giao dịch giữ căn",
  area_velocity_norm: "Tốc độ bán của phân khu (30 ngày)",
  area_conversion_norm: "Tỉ lệ chốt của phân khu",
};

const BAND_FILTERS = [
  { key: null, label: "Tất cả" },
  { key: "high", label: "Cao" },
  { key: "medium", label: "Trung bình" },
  { key: "low", label: "Thấp" },
];

/** Chuỗi Decimal của backend dài tới 28 chữ số (`0.3333333333333333333333333333`).
 *  Hiển thị nguyên văn là vô nghĩa với người đọc; cắt còn 2 chữ số thập phân. */
function decimal(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function freshness(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const minutes = Math.round((Date.now() - then.getTime()) / 60000);
  if (minutes < 1) return "vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  return `${Math.round(hours / 24)} ngày trước`;
}

export default function RankingPage() {
  const navigate = useNavigate();
  const scope = useProjectScope();
  const { isMobile } = useBreakpoint();
  const [band, setBand] = useState(null);
  const [availableOnly, setAvailableOnly] = useState(true);
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState("");

  const projectId = scope.projectExternalId;
  const areaId = scope.areaExternalId;

  const params = useMemo(() => {
    const query = { limit: PAGE_SIZE, offset };
    if (band) query.band = band;
    // Mặc định CHỈ hiện căn còn bán được: xếp hạng dùng để quyết định đẩy căn
    // nào, mà một căn đã bán thì không còn quyết định gì để ra. Vẫn bỏ lọc được
    // — thứ hạng của căn đã bán là bằng chứng cho thấy công thức đang hạ chúng
    // xuống đúng như mong đợi.
    if (availableOnly) query.unit_status = "available";
    if (areaId) query.external_area_id = areaId;
    return query;
  }, [band, availableOnly, offset, areaId]);

  const ranking = useAsync(
    () => (projectId ? getRanking(projectId, params) : Promise.resolve(null)),
    [projectId, params],
  );

  // Đổi bộ lọc thì quay về trang đầu — giữ nguyên `offset` cũ sẽ cho ra một
  // trang trống khi tập kết quả mới ngắn hơn vị trí đang đứng.
  useEffect(() => { setOffset(0); setExpanded(null); }, [projectId, areaId, band, availableOnly]);

  const recompute = useCallback(async () => {
    if (!projectId || running) return;
    setRunning(true);
    setRunError("");
    try {
      await runRanking(projectId, areaId ? { external_area_id: areaId } : {});
      setOffset(0);
      ranking.reload();
    } catch (error) {
      setRunError(
        isAuthError(error)
          ? error.message
          : error?.status === 403
            ? "Vai trò hiện tại không đủ để tính lại xếp hạng. Cần pipeline_operator trở lên."
            : error?.status === 503
              ? "Chưa có cấu hình xếp hạng nào đang phát hành."
              : error?.message || "Không tính lại được xếp hạng.",
      );
    } finally {
      setRunning(false);
    }
  }, [projectId, areaId, running, ranking]);

  const data = ranking.data;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const neverRanked = Boolean(data) && data.computed_at === null;
  const hasNextPage = offset + PAGE_SIZE < total;

  return (
    <>
      <GlobalKeyframes />
      <header style={S.pageHead}>
        <div>
          <h1 style={S.h1}>Xếp hạng căn nên ưu tiên</h1>
          <p style={S.sub}>Điểm tất định từ dữ liệu vận hành · Mỗi dòng mở ra được lý do đằng sau điểm số</p>
        </div>
        {data?.config_version != null && (
          <button
            style={S.configBadge}
            onClick={() => navigate("/ranking/configs")}
            title="Xem và đổi bộ trọng số xếp hạng"
          >
            config v{data.config_version} ›
          </button>
        )}
      </header>

      <section style={S.scopeBar} aria-label="Phạm vi xếp hạng">
        <ProjectSelector
          projects={scope.projects}
          value={projectId}
          onChange={scope.setProjectExternalId}
          loading={scope.loadingProjects}
          status={
            scope.projectsStatus === "unauthorized" ? "unauthorized"
              : scope.projectsStatus === "error" ? "error" : undefined
          }
        />
        {projectId && (
          <label style={S.label}>
            Phân khu
            <select
              style={S.select}
              value={areaId ?? "all"}
              onChange={(e) => scope.setAreaExternalId(e.target.value === "all" ? null : e.target.value)}
            >
              <option value="all">Toàn dự án</option>
              {(scope.areas || []).filter((a) => a.external_id).map((a) => (
                <option key={a.external_id} value={a.external_id}>{areaLabel(a)}</option>
              ))}
            </select>
          </label>
        )}
        {projectId && (
          <div style={S.runBox}>
            <span style={S.freshness}>
              {data?.computed_at ? `Tính lúc ${freshness(data.computed_at)}` : "Chưa từng tính"}
            </span>
            <button style={{ ...S.runButton, ...(running ? S.runButtonBusy : null) }} onClick={recompute} disabled={running}>
              {running ? "Đang tính…" : "Tính lại"}
            </button>
          </div>
        )}
      </section>

      {runError && <div style={S.error}>{runError}</div>}

      {!projectId ? (
        <div style={S.hint}>Chọn một dự án để xem bảng xếp hạng.</div>
      ) : (
        <>
          {data && !neverRanked && (
            <div style={S.summary}>
              <SummaryCard label="Đã xếp hạng" value={fmt(data.units_ranked)} />
              <SummaryCard label="Khả năng bán cao" value={fmt(data.band_counts?.high ?? 0)} tone={color.ok} />
              <SummaryCard label="Trung bình" value={fmt(data.band_counts?.medium ?? 0)} tone={color.warn} />
              <SummaryCard label="Thấp" value={fmt(data.band_counts?.low ?? 0)} tone={color.muted} />
              {/* Bỏ qua vì thiếu dữ liệu — KHÁC với "điểm thấp". Trộn hai thứ này
                  vào một ô sẽ khiến một lỗi dữ liệu trông như một kết luận. */}
              <SummaryCard label="Bỏ qua (thiếu dữ liệu)" value={fmt(data.units_skipped)} tone={color.muted} />
            </div>
          )}

          <div style={S.filters}>
            {BAND_FILTERS.map((f) => (
              <button
                key={f.label}
                onClick={() => setBand(f.key)}
                style={{ ...S.chip, ...(band === f.key ? S.chipOn : null) }}
              >
                {f.label}
                {f.key && data?.band_counts ? ` (${data.band_counts[f.key] ?? 0})` : ""}
              </button>
            ))}
            <label style={S.toggle}>
              <input type="checkbox" checked={availableOnly} onChange={(e) => setAvailableOnly(e.target.checked)} />
              Chỉ căn còn trống
            </label>
          </div>

          {neverRanked ? (
            <div style={S.emptyCard}>
              <div style={S.emptyIcon}>▦</div>
              <b style={{ color: color.ink }}>Dự án này chưa được xếp hạng lần nào</b>
              <p style={{ margin: `${space(2)}px 0 ${space(4)}px` }}>
                Bấm “Tính lại” để chạy bộ xếp hạng trên dữ liệu tồn kho và giao dịch hiện tại.
              </p>
              <button style={S.runButton} onClick={recompute} disabled={running}>
                {running ? "Đang tính…" : "Tính lại"}
              </button>
            </div>
          ) : (
            <SectionState
              loading={ranking.loading}
              error={ranking.error}
              empty={!ranking.loading && !ranking.error && items.length === 0}
              onRetry={ranking.reload}
            >
              <div style={S.tableCard}>
                <div style={S.scroll}>
                  <table style={S.table}>
                    <thead>
                      <tr>
                        <th style={{ ...S.th, width: 56 }}>#</th>
                        <th style={S.th}>Mã căn</th>
                        {!isMobile && <th style={S.th}>Phân khu</th>}
                        <th style={S.th}>Trạng thái</th>
                        <th style={{ ...S.th, minWidth: 180 }}>Điểm</th>
                        <th style={S.th}>Mức</th>
                        {!isMobile && <th style={S.th}>Hạng trong phân khu</th>}
                        <th style={{ ...S.th, width: 40 }} aria-label="Mở giải thích" />
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((u) => {
                        const bandStyle = BAND_STYLE[u.band] || BAND_STYLE.low;
                        const open = expanded === u.unit_id;
                        const percent = u.score_percent ?? 0;
                        return (
                          <React.Fragment key={u.unit_id}>
                            <tr
                              onClick={() => setExpanded(open ? null : u.unit_id)}
                              style={{ ...S.row, background: open ? color.accentSoft : undefined }}
                            >
                              <td style={{ ...S.td, ...S.rank }}>{u.rank_in_project}</td>
                              <td style={{ ...S.td, fontWeight: 600, color: color.ink }}>
                                {u.unit_code}
                                <span style={S.unitType}>{u.unit_type}</span>
                              </td>
                              {!isMobile && <td style={S.td}>{u.area_name}</td>}
                              <td style={S.td}>{UNIT_STATUS_LABEL[u.unit_status] ?? u.unit_status}</td>
                              <td style={S.td}>
                                <div style={S.scoreCell}>
                                  <span style={S.track}>
                                    <span style={{ ...S.fill, width: `${percent}%`, background: bandStyle.fg }} />
                                  </span>
                                  <span style={S.percent}>{percent.toFixed(1)}%</span>
                                </div>
                              </td>
                              <td style={S.td}>
                                <span style={{ ...S.badge, color: bandStyle.fg, background: bandStyle.bg }}>
                                  {bandStyle.label}
                                </span>
                              </td>
                              {!isMobile && <td style={S.td}>#{u.rank_in_area}</td>}
                              <td style={{ ...S.td, ...S.caret }}>{open ? "▾" : "▸"}</td>
                            </tr>
                            {open && (
                              <tr>
                                <td colSpan={isMobile ? 6 : 8} style={S.explainCell}>
                                  <Explanation unit={u} />
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </SectionState>
          )}

          {(offset > 0 || hasNextPage) && (
            <div style={S.pager}>
              <button style={S.pageButton} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Trang trước
              </button>
              <span style={S.pageInfo}>
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} / {fmt(total)}
              </span>
              <button style={S.pageButton} disabled={!hasNextPage} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Trang sau
              </button>
            </div>
          )}

          {data?.disclaimer && <p style={S.disclaimer}>{data.disclaimer}</p>}
        </>
      )}
    </>
  );
}

/** Phần "vì sao căn này đứng ở đây" — trải từng đặc trưng ra thành một dòng.
 *  Backend đã sắp theo đóng góp giảm dần, nên thứ tự ở đây không được sắp lại. */
function Explanation({ unit }) {
  const contributions = unit.contributions || [];
  return (
    <div style={S.explain}>
      <div style={S.explainHead}>
        <b style={{ color: color.ink }}>Điểm {Number(unit.score).toFixed(4)} được tạo từ</b>
        <span style={S.coverage}>độ phủ trọng số {decimal(unit.weight_coverage)}</span>
      </div>
      <table style={S.explainTable}>
        <thead>
          <tr>
            <th style={S.explainTh}>Đặc trưng</th>
            <th style={S.explainTh}>Giá trị</th>
            <th style={S.explainTh}>Trọng số</th>
            <th style={S.explainTh}>Đóng góp</th>
          </tr>
        </thead>
        <tbody>
          {contributions.map((c) => (
            <tr key={c.feature_key}>
              <td style={S.explainTd}>
                {FEATURE_LABEL[c.feature_key] || c.feature_key}
                {c.direction === "negative" && <span style={S.negative}>ngược chiều</span>}
                {c.source !== "resolved" && <span style={S.missing}>thiếu dữ liệu</span>}
              </td>
              <td style={S.explainTd}>{c.value === null ? "—" : decimal(c.value)}</td>
              <td style={S.explainTd}>{decimal(c.weight)}</td>
              <td style={{ ...S.explainTd, fontWeight: 700, color: color.ink }}>{decimal(c.contribution, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {contributions.length === 0 && <p style={{ margin: 0, color: color.muted }}>Không có chi tiết đóng góp.</p>}
    </div>
  );
}

function SummaryCard({ label, value, tone }) {
  return (
    <div style={S.summaryCard}>
      <span style={S.summaryLabel}>{label}</span>
      <b style={{ ...S.summaryValue, color: tone || color.ink }}>{value}</b>
    </div>
  );
}

const S = {
  pageHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(5) },
  h1: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h1, letterSpacing: "-.03em" },
  sub: { margin: "5px 0 0", color: color.muted, fontSize: size.small },
  configBadge: { flex: "none", color: color.accent, background: color.accentSoft, border: 0, borderRadius: radius.pill, padding: "7px 12px", fontSize: size.tiny, fontWeight: 700, fontFamily: font.mono, cursor: "pointer" },

  scopeBar: { display: "flex", alignItems: "flex-end", gap: space(4), flexWrap: "wrap", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), marginBottom: space(4), boxShadow: shadow },
  label: { display: "flex", flexDirection: "column", gap: 5, color: color.ink, fontSize: size.tiny, fontWeight: 700 },
  select: { minWidth: 190, padding: "9px 11px", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, background: color.surface, fontFamily: "inherit" },
  runBox: { marginLeft: "auto", display: "flex", alignItems: "center", gap: space(3) },
  freshness: { color: color.muted, fontSize: size.tiny },
  runButton: { background: color.accent, color: "#fff", border: 0, borderRadius: radius.sm, padding: "10px 16px", fontWeight: 700, cursor: "pointer", fontFamily: "inherit", fontSize: size.small },
  runButtonBusy: { background: color.borderStrong, cursor: "wait" },

  summary: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: space(3), marginBottom: space(4) },
  summaryCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), display: "flex", flexDirection: "column", gap: 6, boxShadow: shadow },
  summaryLabel: { color: color.muted, fontSize: size.tiny },
  summaryValue: { fontFamily: font.display, fontSize: 24, fontVariantNumeric: "tabular-nums" },

  filters: { display: "flex", gap: space(2), flexWrap: "wrap", alignItems: "center", marginBottom: space(4) },
  chip: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.pill, padding: "7px 14px", fontFamily: "inherit", fontSize: size.tiny, cursor: "pointer" },
  chipOn: { background: color.accent, color: "#fff", borderColor: color.accent, fontWeight: 700 },
  toggle: { display: "flex", alignItems: "center", gap: 6, color: color.body, fontSize: size.tiny, cursor: "pointer", marginLeft: space(2) },

  tableCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflow: "hidden" },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: size.small },
  th: { textAlign: "left", padding: `${space(3)}px ${space(4)}px`, color: color.muted, fontSize: size.tiny, fontWeight: 700, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  row: { cursor: "pointer", borderBottom: `1px solid ${color.border}` },
  td: { padding: `${space(3)}px ${space(4)}px`, verticalAlign: "middle" },
  rank: { fontFamily: font.mono, color: color.muted, fontVariantNumeric: "tabular-nums" },
  unitType: { display: "block", color: color.muted, fontWeight: 400, fontSize: size.tiny, marginTop: 2 },
  caret: { color: color.muted, textAlign: "center" },

  scoreCell: { display: "flex", alignItems: "center", gap: space(2) },
  track: { flex: 1, minWidth: 80, height: 6, borderRadius: radius.pill, background: color.canvas, overflow: "hidden" },
  fill: { display: "block", height: "100%", borderRadius: radius.pill },
  percent: { fontVariantNumeric: "tabular-nums", fontWeight: 700, color: color.ink, minWidth: 52, textAlign: "right" },
  badge: { borderRadius: radius.pill, padding: "4px 10px", fontSize: size.tiny, fontWeight: 700, whiteSpace: "nowrap" },

  explainCell: { padding: 0, background: color.accentSoft, borderBottom: `1px solid ${color.border}` },
  explain: { padding: space(4) },
  explainHead: { display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: space(3), marginBottom: space(3), flexWrap: "wrap" },
  coverage: { color: color.muted, fontSize: size.tiny, fontFamily: font.mono },
  explainTable: { width: "100%", borderCollapse: "collapse", fontSize: size.tiny, background: color.surface, borderRadius: radius.sm, overflow: "hidden" },
  explainTh: { textAlign: "left", padding: `${space(2)}px ${space(3)}px`, color: color.muted, fontWeight: 700, borderBottom: `1px solid ${color.border}` },
  explainTd: { padding: `${space(2)}px ${space(3)}px`, borderBottom: `1px solid ${color.border}`, fontVariantNumeric: "tabular-nums" },
  negative: { marginLeft: 6, color: color.danger, fontSize: 10, fontWeight: 700 },
  missing: { marginLeft: 6, color: color.warn, fontSize: 10, fontWeight: 700 },

  emptyCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(10), textAlign: "center", color: color.muted },
  emptyIcon: { color: color.accent, background: color.accentSoft, borderRadius: "50%", width: 54, height: 54, display: "grid", placeItems: "center", fontSize: 22, margin: `0 auto ${space(3)}px` },
  hint: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(8), textAlign: "center", color: color.muted, boxShadow: shadow },
  error: { background: color.dangerSoft, color: color.danger, borderRadius: radius.sm, padding: space(3), marginBottom: space(4), fontSize: size.small },

  pager: { display: "flex", alignItems: "center", justifyContent: "center", gap: space(4), marginTop: space(4) },
  pageButton: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "8px 14px", fontFamily: "inherit", fontSize: size.tiny, cursor: "pointer" },
  pageInfo: { color: color.muted, fontSize: size.tiny, fontVariantNumeric: "tabular-nums" },

  disclaimer: { marginTop: space(5), color: color.muted, fontSize: size.tiny, textAlign: "center", lineHeight: 1.6 },
};
