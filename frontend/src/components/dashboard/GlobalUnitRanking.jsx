// frontend/src/components/dashboard/GlobalUnitRanking.jsx
// ---------------------------------------------------------------------------
// Bảng xếp hạng CĂN toàn hệ thống — một danh sách duy nhất, xuyên dự án và
// xuyên phân khu, với chiều mặc định là điểm cao nhất ở trên cùng.
//
// Dự án và phân khu là CỘT NGỮ CẢNH. Không có tab, không có nhóm, không có
// tiêu đề chia theo dự án/phân khu — nếu có, người đọc sẽ đọc ra một bảng xếp
// hạng dự án, đúng thứ trang này KHÔNG phải.
//
// Thành phần THUẦN TRÌNH BÀY: không fetch. Thứ tự hiển thị được tính cục bộ từ
// `utils/globalUnitRanking.js`; dữ liệu và xếp hạng gốc không bị thay đổi.
// ---------------------------------------------------------------------------
import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, ErrorState, Skeleton } from "../ui/States";
import Icon from "../ui/Icon";
import {
  GLOBAL_PAGE_SIZE,
  RANKING_PROJECT_LIMIT,
  UNITS_PER_PROJECT_LIMIT,
  pageOf,
  RANK_DIRECTION,
  SORT_DIRECTIONS,
  sortGlobalRankingRows,
} from "../../utils/globalUnitRanking";
import { color, font, radius, shadow, size, space } from "../../styles/tokens";

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

const CONFIDENCE_LABEL = {
  high: "Đủ đặc trưng",
  medium: "Thiếu một phần đặc trưng",
  unknown: "Chưa chấm được điểm",
};

function formatUpdatedAt(iso) {
  if (!iso) return "Chưa có mốc";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "Chưa có mốc";
  return at.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export default function GlobalUnitRanking({
  rows = [],
  meta = {},
  loading = false,
  error = null,
  onRetry,
  page = 0,
  onPageChange,
}) {
  const [sortDirection, setSortDirection] = useState(RANK_DIRECTION);
  const sortedRows = useMemo(
    () => sortGlobalRankingRows(rows, sortDirection),
    [rows, sortDirection],
  );
  const view = pageOf(sortedRows, page);
  const directionLabel = sortDirection === SORT_DIRECTIONS.ASC
    ? "Top thấp nhất → cao nhất"
    : "Top cao nhất → thấp nhất";
  const directionDescription = sortDirection === SORT_DIRECTIONS.ASC
    ? "điểm tăng dần"
    : "điểm giảm dần";
  const directionSentence = sortDirection === SORT_DIRECTIONS.ASC
    ? "điểm xếp hạng tăng dần"
    : "điểm xếp hạng giảm dần";

  return (
    <section style={S.card} aria-labelledby="global-unit-ranking-title">
      <div style={S.cardHeader}>
        <div>
          <div style={S.eyebrow}>GLOBAL UNIT RANKING</div>
          <h2 id="global-unit-ranking-title" style={S.sectionTitle}>Xếp hạng căn toàn hệ thống</h2>
          <p style={S.subtitle} data-testid="global-ranking-scope-note">
            Một danh sách duy nhất cho MỌI căn thuộc mọi dự án và mọi phân khu trong phạm vi được
            cấp quyền, sắp theo {directionSentence} ({directionLabel.toLowerCase()}). Dự án và phân khu chỉ là ngữ cảnh của từng
            căn, không phải cách chia nhóm.
          </p>
        </div>
        <div style={S.headerTools}>
          <label htmlFor="global-ranking-sort" style={S.sortLabel}>Thứ tự xếp hạng</label>
          <select
            id="global-ranking-sort"
            value={sortDirection}
            onChange={(event) => setSortDirection(event.target.value)}
            style={S.sortSelect}
            data-testid="global-ranking-sort"
          >
            <option value={SORT_DIRECTIONS.DESC}>Top cao nhất → thấp nhất</option>
            <option value={SORT_DIRECTIONS.ASC}>Top thấp nhất → cao nhất</option>
          </select>
          <span style={S.activeDirection} aria-live="polite">Đang xem: {directionLabel}</span>
        </div>
      </div>

      {loading ? (
        <div style={S.loading} data-testid="global-ranking-loading">
          <Skeleton width="90%" height={18} />
          <Skeleton width="80%" height={18} />
          <Skeleton width="86%" height={18} />
          <Skeleton width="72%" height={18} />
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} compact />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Chưa có căn nào được xếp hạng"
          hint="Bảng này chỉ đọc kết quả đang lưu. Mở một dự án và bấm “Tính lại” ở trang Xếp hạng để sinh điểm."
          compact
        />
      ) : (
        <>
          <MetaBar meta={meta} shown={view.items.length} total={sortedRows.length} directionLabel={directionLabel} directionDescription={directionDescription} />
          <div style={S.scroll} role="region" aria-label="Global unit ranking">
            <table style={S.table} data-testid="global-ranking-table">
              <caption style={S.caption}>
                Xếp hạng căn toàn hệ thống, {directionDescription} ({directionLabel.toLowerCase()}) — {sortedRows.length} căn từ{" "}
                {meta.projectsIncluded ?? 0} dự án
              </caption>
              <thead>
                <tr>
                  <th scope="col" style={{ ...S.th, width: 64 }}>Hạng</th>
                  <th scope="col" style={{ ...S.th, width: 200 }}>Căn</th>
                  <th scope="col" style={{ ...S.th, width: 210 }}>Dự án</th>
                  <th scope="col" style={{ ...S.th, width: 180 }}>Phân khu</th>
                  <th scope="col" style={{ ...S.th, width: 180, textAlign: "right" }}>Điểm</th>
                  <th scope="col" style={{ ...S.th, width: 120 }}>Mức</th>
                  <th scope="col" style={{ ...S.th, width: 130 }}>Cập nhật</th>
                </tr>
              </thead>
              <tbody>
                {view.items.map((row) => (
                  <UnitRow key={row.unitId} row={row} />
                ))}
              </tbody>
            </table>
          </div>
          <Pager view={view} total={rows.length} onPageChange={onPageChange} />
        </>
      )}
    </section>
  );
}

/** Những gì bảng này KHÔNG bao trùm, nói thẳng thay vì để người đọc tưởng đã đủ. */
function MetaBar({ meta, shown, total, directionLabel, directionDescription }) {
  const notes = [];
  if (meta.projectsNotScanned > 0) {
    notes.push(
      `${meta.projectsNotScanned} dự án chưa được nạp (trần ${RANKING_PROJECT_LIMIT} dự án mỗi lần mở trang) — bảng chưa phủ hết danh mục.`,
    );
  }
  if (meta.truncatedProjects?.length) {
    const worst = meta.truncatedProjects
      .map((p) => `${p.label} (${p.shown}/${p.total})`)
      .join(", ");
    notes.push(
      `Chỉ lấy tối đa ${UNITS_PER_PROJECT_LIMIT} căn điểm cao nhất mỗi dự án: ${worst}. Thứ tự vẫn đúng ở phần đầu bảng; các căn điểm thấp hơn của những dự án này chưa có mặt.`,
    );
  }
  if (meta.failedProjects?.length) {
    notes.push(
      `Không đọc được xếp hạng của ${meta.failedProjects.length} dự án: ${meta.failedProjects.map((p) => p.label).join(", ")}.`,
    );
  }
  if (meta.malformedProjects?.length) {
    notes.push(
      `Phản hồi xếp hạng không đúng định dạng ở ${meta.malformedProjects.length} dự án: ${meta.malformedProjects.map((p) => p.label).join(", ")}.`,
    );
  }
  if (meta.neverRankedProjects?.length) {
    notes.push(
      `Chưa từng xếp hạng lần nào: ${meta.neverRankedProjects.map((p) => p.label).join(", ")}.`,
    );
  }
  if (meta.mixedConfigVersions) {
    notes.push(
      `Các dự án đang giữ điểm của nhiều phiên bản cấu hình khác nhau (v${meta.configVersions.join(", v")}) — điểm giữa chúng đến từ hai lần chạy khác nhau, hãy cân nhắc chạy lại trước khi so trực tiếp.`,
    );
  }
  if (meta.unscoredCount > 0) {
    notes.push(
      `${meta.unscoredCount} căn chưa chấm được điểm (độ phủ trọng số dưới ngưỡng) — xếp cuối bảng, KHÔNG quy về 0 điểm.`,
    );
  }
  if (meta.missingProjectContext > 0) {
    notes.push(`${meta.missingProjectContext} căn thiếu tên dự án.`);
  }
  if (meta.missingAreaContext > 0) {
    notes.push(`${meta.missingAreaContext} căn thiếu tên phân khu.`);
  }

  return (
    <div style={S.metaBar} data-testid="global-ranking-meta">
      <span style={S.scopeBadge}>Phạm vi toàn hệ thống · {directionDescription}</span>
      <span style={S.metaCount}>
        Hiện {shown} / {total} căn · {meta.projectsIncluded ?? 0} dự án
      </span>
      {notes.length > 0 && (
        <ul style={S.notes} data-testid="global-ranking-notes">
          {notes.map((note) => (
            <li key={note} style={S.note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UnitRow({ row }) {
  const bandStyle = row.band ? BAND_STYLE[row.band] : null;
  const percent = row.scorePercent;

  return (
    <tr
      style={S.row}
      data-testid="global-ranking-row"
      data-unit-id={row.unitId}
      data-rank={row.rank ?? ""}
      data-score={row.score ?? ""}
      data-project={row.projectExternalId ?? ""}
    >
      <td style={{ ...S.td, ...S.rank }}>
        {row.rank === null ? <span style={S.muted}>—</span> : `#${row.rank}`}
      </td>
      <td style={S.td}>
        <strong style={S.unitCode} title={row.unitName}>{row.unitName}</strong>
        <span style={S.sub}>
          {row.unitType || "Chưa rõ loại căn"}
          {row.unitStatus ? ` · ${UNIT_STATUS_LABEL[row.unitStatus] || row.unitStatus}` : ""}
        </span>
      </td>
      <td style={S.td}>
        {row.projectContextAvailable ? (
          row.projectExternalId ? (
            <Link
              to={`/projects/${encodeURIComponent(row.projectExternalId)}`}
              style={S.contextLink}
              aria-label={`Mở dự án ${row.projectExternalId}`}
            >
              <span style={S.truncated} title={row.projectName}>{row.projectName}</span>
            </Link>
          ) : (
            <span style={{ ...S.contextText, ...S.truncated }} title={row.projectName}>{row.projectName}</span>
          )
        ) : (
          <span style={S.unavailable} data-testid="missing-project-context">{row.projectName}</span>
        )}
        {row.projectExternalId && <span style={S.sub}>{row.projectExternalId}</span>}
      </td>
      <td style={S.td}>
        {/* Không tạo liên kết phân khu: tuyến /projects/:id/areas/:areaId cần
            external_id của phân khu, còn `RankedUnitOut.area_id` là UUID nội
            bộ — link dựng từ UUID sẽ dẫn tới một trang không tìm thấy. */}
        {row.areaContextAvailable ? (
          <span style={{ ...S.contextText, ...S.truncated }} title={row.areaName}>{row.areaName}</span>
        ) : (
          <span style={S.unavailable} data-testid="missing-area-context">{row.areaName}</span>
        )}
      </td>
      <td style={{ ...S.td, ...S.scoreColumn }}>
        {row.score === null ? (
          <span style={S.unavailable} data-testid="unscored-unit">Chưa chấm được điểm</span>
        ) : (
          <div style={S.scoreCell}>
            <span style={S.track}>
              <span
                style={{ ...S.fill, width: `${Math.max(Math.min(percent ?? row.score * 100, 100), 0)}%`, background: bandStyle ? bandStyle.fg : color.borderStrong }}
              />
            </span>
            <span style={S.percent}>{(percent ?? row.score * 100).toFixed(1)}%</span>
          </div>
        )}
        <span style={S.sub} title={CONFIDENCE_LABEL[row.confidence]}>{CONFIDENCE_LABEL[row.confidence]}</span>
      </td>
      <td style={S.td}>
        {bandStyle ? (
          <span style={{ ...S.badge, color: bandStyle.fg, background: bandStyle.bg }}>
            {bandStyle.label}
          </span>
        ) : (
          <span style={S.unavailable}>Chưa phân mức</span>
        )}
      </td>
      <td style={{ ...S.td, ...S.updated }}>{formatUpdatedAt(row.updatedAt)}</td>
    </tr>
  );
}

/** Phân trang chạy trên danh sách ĐÃ sắp xếp toàn cục, nên trang 2 luôn là các
 *  căn xếp ngay sau trang 1 trên toàn hệ thống — không phải "sắp xếp lại trong
 *  từng trang". */
function Pager({ view, total, onPageChange }) {
  if (view.pages <= 1) return null;
  return (
    <div style={S.pager} data-testid="global-ranking-pager">
      <button
        type="button"
        style={S.pageButton}
        disabled={view.page === 0}
        onClick={() => onPageChange?.(view.page - 1)}
      >
        Trang trước
      </button>
      <span style={S.pageInfo}>
        {view.start + 1}–{Math.min(view.start + GLOBAL_PAGE_SIZE, total)} / {total}
      </span>
      <button
        type="button"
        style={S.pageButton}
        disabled={view.page >= view.pages - 1}
        onClick={() => onPageChange?.(view.page + 1)}
      >
        Trang sau
      </button>
    </div>
  );
}

const S = {
  card: { minWidth: 0, background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(5), marginBottom: space(5) },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(3), marginBottom: space(4) },
  eyebrow: { color: color.muted, fontSize: 11, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: space(1) },
  sectionTitle: { margin: 0, color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  subtitle: { margin: `${space(2)}px 0 0`, maxWidth: 720, color: color.muted, fontSize: size.small, lineHeight: 1.55 },
  loading: { display: "grid", gap: space(3) },

  metaBar: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: space(3), marginBottom: space(3) },
  scopeBadge: { color: color.accent, background: color.accentSoft, borderRadius: radius.pill, padding: "4px 10px", fontSize: size.tiny, fontWeight: 700 },
  metaCount: { color: color.muted, fontSize: size.tiny, fontVariantNumeric: "tabular-nums" },
  notes: { flexBasis: "100%", margin: 0, padding: `0 0 0 ${space(4)}px`, display: "grid", gap: 4 },
  note: { color: color.muted, fontSize: size.tiny, lineHeight: 1.5 },

  headerTools: { display: "flex", flexDirection: "column", alignItems: "stretch", gap: space(2), minWidth: 220 },
  sortLabel: { color: color.muted, fontSize: size.tiny, fontWeight: 700 },
  sortSelect: { width: "100%", border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(3)}px`, background: color.surface, color: color.ink, fontFamily: "inherit", fontSize: size.tiny, cursor: "pointer" },
  activeDirection: { color: color.body, fontSize: size.tiny, fontWeight: 600 },
  scroll: { overflowX: "auto", overflowY: "auto", maxHeight: "min(62vh, 640px)", overscrollBehavior: "contain", border: `1px solid ${color.border}`, borderRadius: radius.sm },
  table: { width: "100%", minWidth: 1084, tableLayout: "fixed", borderCollapse: "separate", borderSpacing: 0, fontSize: size.small },
  caption: { captionSide: "top", textAlign: "left", color: color.muted, fontSize: size.tiny, paddingBottom: space(2) },
  th: { position: "sticky", top: 0, zIndex: 2, textAlign: "left", padding: `${space(3)}px ${space(3)}px`, color: color.muted, background: color.surface, fontSize: size.tiny, fontWeight: 700, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap", boxShadow: `0 1px 0 ${color.border}` },
  row: { borderBottom: `1px solid ${color.border}` },
  td: { padding: `${space(3)}px ${space(3)}px`, verticalAlign: "top" },
  rank: { fontFamily: font.mono, color: color.ink, fontVariantNumeric: "tabular-nums", fontWeight: 700 },
  unitCode: { display: "block", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: color.ink },
  sub: { display: "block", color: color.muted, fontSize: size.tiny, marginTop: 2 },
  contextLink: { color: color.accent, fontWeight: 600, textDecoration: "none" },
  contextText: { color: color.body },
  truncated: { display: "block", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  unavailable: { color: color.warn, fontStyle: "italic" },
  muted: { color: color.muted },

  scoreCell: { display: "flex", alignItems: "center", gap: space(2) },
  scoreColumn: { textAlign: "right" },
  track: { flex: 1, minWidth: 70, height: 6, borderRadius: radius.pill, background: color.canvas, overflow: "hidden" },
  fill: { display: "block", height: "100%", borderRadius: radius.pill },
  percent: { fontVariantNumeric: "tabular-nums", fontWeight: 700, color: color.ink, minWidth: 52, textAlign: "right" },
  badge: { display: "inline-block", borderRadius: radius.pill, padding: "4px 10px", fontSize: size.tiny, fontWeight: 700, whiteSpace: "nowrap" },
  updated: { color: color.muted, fontSize: size.tiny, whiteSpace: "nowrap" },

  pager: { display: "flex", alignItems: "center", justifyContent: "center", gap: space(4), marginTop: space(4) },
  pageButton: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: "8px 14px", fontFamily: "inherit", fontSize: size.tiny, cursor: "pointer" },
  pageInfo: { color: color.muted, fontSize: size.tiny, fontVariantNumeric: "tabular-nums" },
};
