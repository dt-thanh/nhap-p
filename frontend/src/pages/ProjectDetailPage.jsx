// frontend/src/pages/ProjectDetailPage.jsx
// S03 — Giới thiệu dự án và danh sách phân khu. Dashboard của phân khu là một
// route riêng tại /projects/:id/areas/:areaId; trang này chỉ giữ ngữ cảnh dự án
// và các phân khu đã được nạp.
import React, { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getProjectByExternalId, listAreasScoped } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";
import { color, size, radius, space, font } from "../styles/tokens";
import { Skeleton } from "../components/ui/States";
import Icon from "../components/ui/Icon";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { DASHBOARD_TEXT } from "../components/dashboard/labels";

const UNAVAILABLE = "Chưa có dữ liệu";

const STATUS = {
  pending: { label: "Chờ duyệt", fg: color.warn, bg: color.warnSoft },
  active: { label: "Đang bán", fg: color.ok, bg: color.okSoft },
  rejected: { label: "Bị từ chối", fg: color.danger, bg: color.dangerSoft },
  archived: { label: "Lưu trữ", fg: color.muted, bg: color.canvas },
};

function formatDate(value) {
  if (!value) return UNAVAILABLE;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? UNAVAILABLE : date.toLocaleDateString("vi-VN");
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return UNAVAILABLE;
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("vi-VN") : UNAVAILABLE;
}

function searchValue(value) {
  return String(value || "").toLocaleLowerCase("vi-VN");
}

function areaKey(area, index) {
  return area?.external_id || area?.area_id || `${area?.area_name || UNAVAILABLE}-${index}`;
}

function sumIfComplete(rows, field) {
  if (!rows.length) return null;
  const values = rows.map((row) => {
    const value = row?.[field];
    return value === null || value === undefined || value === "" ? NaN : Number(value);
  });
  if (values.some((value) => !Number.isFinite(value))) return null;
  return values.reduce((total, value) => total + value, 0);
}

export default function ProjectDetailPage() {
  const { externalId, id } = useParams();
  const projectExternalId = externalId || id;
  const navigate = useNavigate();
  const { bp } = useBreakpoint();
  const projectRequest = useAsync(() => getProjectByExternalId(projectExternalId), [projectExternalId]);
  const areasRequest = useAsync(
    () => (projectRequest.data ? listAreasScoped(projectExternalId) : Promise.resolve(null)),
    [projectExternalId, Boolean(projectRequest.data)],
  );
  const [query, setQuery] = useState("");

  const project = projectRequest.data;
  const areas = Array.isArray(areasRequest.data) ? areasRequest.data : [];
  const filteredAreas = useMemo(() => {
    const normalized = searchValue(query.trim());
    if (!normalized) return areas;
    return areas.filter((area) => (
      searchValue(area?.area_name).includes(normalized)
      || searchValue(area?.external_id).includes(normalized)
    ));
  }, [areas, query]);
  const totalUnits = useMemo(() => sumIfComplete(areas, "total_units"), [areas]);
  const remainingUnits = useMemo(() => sumIfComplete(areas, "units_remaining"), [areas]);
  const areaColumns = pick(bp, { mobile: 1, tablet: 2, laptop: 3, desktop: 4 });
  const summaryColumns = pick(bp, { mobile: "1fr", tablet: "repeat(3, minmax(0, 1fr))", laptop: "repeat(3, minmax(0, 1fr))", desktop: "repeat(3, minmax(0, 1fr))" });
  const contextColumns = pick(bp, { mobile: "1fr", tablet: "repeat(3, minmax(0, 1fr))", laptop: "repeat(3, minmax(0, 1fr))", desktop: "repeat(3, minmax(0, 1fr))" });
  const areasLoading = projectRequest.loading || areasRequest.loading;
  const areasError = !projectRequest.loading && areasRequest.error;

  if (projectRequest.error) {
    // Lỗi qua `useAsync` là object đã bọc `{message, status, network}` — so
    // `status` trực tiếp, không dùng `isAuthError()` (kiểm `instanceof
    // ApiError`, không còn khớp sau khi đã bị bọc lại).
    const notFound = projectRequest.error.status === 404;
    const forbidden = projectRequest.error.status === 401 || projectRequest.error.status === 403;
    return (
      <div style={S.stateWrap}>
        <h1 style={S.h1}>
          {notFound ? "Không tìm thấy dự án" : forbidden ? "Bạn không có quyền xem dự án này" : "Không tải được dự án"}
        </h1>
        <p style={S.sub}>
          {notFound
            ? `Không có dự án nào khớp "${projectExternalId}".`
            : forbidden
              ? "Token hiện tại không nằm trong phạm vi dự án này."
              : projectRequest.error.message}
        </p>
        <div style={S.stateActions}>
          <button style={S.secondaryBtn} onClick={() => projectRequest.reload()}>Thử lại</button>
          <button style={S.addBtn} onClick={() => navigate("/projects")}>Về danh sách dự án</button>
        </div>
      </div>
    );
  }

  const st = project ? STATUS[project.status] || { label: UNAVAILABLE, fg: color.muted, bg: color.canvas } : null;

  return (
    <>
      <GlobalKeyframes />

      <nav aria-label="Điều hướng dự án" style={S.crumb}>
        <Link to="/projects" style={S.crumbLink}>Dự án</Link>
        <span style={S.sep}>/</span>
        <span style={S.crumbCur}>{project?.name || "…"}</span>
      </nav>

      <header style={S.head}>
        <div>
          <div style={S.titleRow}>
            <h1 style={S.h1}>{projectRequest.loading ? <Skeleton width={220} height={26} /> : project?.name || UNAVAILABLE}</h1>
            {st && <span style={{ ...S.badge, color: st.fg, background: st.bg }}>{st.label}</span>}
          </div>
          {project && (
            <p style={S.sub}>
              {areasLoading ? "Đang tải phân khu…" : `${areas.length} phân khu`}
              {` · Mở bán ${formatDate(project.launch_date)}`}
            </p>
          )}
          {project?.headline && <p style={S.introduce}>{project.headline}</p>}
          {project?.introduce && <p style={S.introduce}>{project.introduce}</p>}
        </div>
        <div style={S.actions}>
          <button style={S.primaryBtn} onClick={() => navigate(`/projects/${projectExternalId}/dashboard`)}>
            {DASHBOARD_TEXT.openDashboard}
          </button>
        </div>
      </header>

      <section style={S.projectContext} aria-label="Thông tin dự án">
        <div style={project?.cover_image_url ? S.coverWrap : S.coverFallback}>
          {project?.cover_image_url ? (
            <img src={project.cover_image_url} alt="" style={S.cover} />
          ) : (
            <Icon name="folder" size={32} color={color.muted} />
          )}
        </div>
        <div style={{ ...S.contextDetails, gridTemplateColumns: contextColumns }}>
          <div><span style={S.contextLabel}>Mã dự án</span><strong style={S.contextValue}>{project?.external_id || UNAVAILABLE}</strong></div>
          <div><span style={S.contextLabel}>Trạng thái</span><strong style={S.contextValue}>{st?.label || UNAVAILABLE}</strong></div>
          <div><span style={S.contextLabel}>Ngày mở bán</span><strong style={S.contextValue}>{formatDate(project?.launch_date)}</strong></div>
        </div>
      </section>

      <section aria-label="Tổng quan phân khu" style={{ ...S.summaryGrid, gridTemplateColumns: summaryColumns }}>
        <SummaryCard label="Tổng phân khu" value={areasLoading ? null : formatNumber(areas.length)} icon="folder" loading={areasLoading} />
        <SummaryCard label="Tổng số căn" value={areasLoading ? null : formatNumber(totalUnits)} icon="units" loading={areasLoading} />
        <SummaryCard label="Còn lại tổng cộng" value={areasLoading ? null : formatNumber(remainingUnits)} icon="remaining" loading={areasLoading} />
      </section>

      <section aria-labelledby="areas-title" style={S.catalogSection}>
          <div style={S.catalogHead}>
            <div>
              <h2 id="areas-title" style={S.sectionTitle}>Danh mục phân khu</h2>
              <p style={S.sectionSub}>Thông tin phân khu được tải từ dữ liệu của dự án.</p>
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Tìm theo tên hoặc mã phân khu…"
              aria-label="Tìm kiếm phân khu"
              style={S.search}
            />
          </div>

          {query && !areasLoading && !areasError && (
            <div style={S.searchContext}>
              Đang tìm: <strong>{query}</strong>
              <button type="button" onClick={() => setQuery("")} style={S.clearButton}>Xóa tìm kiếm</button>
            </div>
          )}

          {areasLoading && (
            <div data-testid="areas-loading" style={{ ...S.areaGrid, gridTemplateColumns: `repeat(${areaColumns}, minmax(0, 1fr))` }}>
              {[1, 2, 3].map((item) => <div key={item} style={S.areaCard}><Skeleton height={128} /><Skeleton width="65%" height={20} /><Skeleton width="90%" height={16} /></div>)}
            </div>
          )}

          {areasError && (
            <div data-testid="areas-error" style={S.inlineState}>
              <Icon name="warning" size={24} color={color.danger} />
              <strong>Không thể tải danh sách phân khu</strong>
              <span>Dữ liệu phân khu chưa được cập nhật. Vui lòng thử lại.</span>
              <button type="button" onClick={() => areasRequest.reload()} style={S.addBtn}>Thử lại</button>
            </div>
          )}

          {!areasLoading && !areasError && areas.length === 0 && (
            <div data-testid="areas-empty" style={S.inlineState}>
              <Icon name="inbox" size={26} color={color.muted} />
              <strong>Chưa có phân khu nào</strong>
              <span>Chưa có phân khu nào được tạo trong dự án này.</span>
            </div>
          )}

          {!areasLoading && !areasError && areas.length > 0 && filteredAreas.length === 0 && (
            <div data-testid="areas-filtered-empty" style={S.inlineState}>
              <strong>Không tìm thấy phân khu phù hợp</strong>
              <button type="button" onClick={() => setQuery("")} style={S.addBtn}>Xóa tìm kiếm</button>
            </div>
          )}

          {!areasLoading && !areasError && filteredAreas.length > 0 && (
            <div style={{ ...S.areaGrid, gridTemplateColumns: `repeat(${areaColumns}, minmax(0, 1fr))` }}>
              {filteredAreas.map((area, index) => (
                <AreaCard
                  key={areaKey(area, index)}
                  area={area}
                  externalId={projectExternalId}
                />
              ))}
            </div>
          )}
      </section>
    </>
  );
}

function SummaryCard({ label, value, icon, loading }) {
  return (
    <div style={S.summaryCard}>
      <div style={S.summaryIcon}><Icon name={icon} size={18} color={color.accent} /></div>
      <span style={S.summaryLabel}>{label}</span>
      <strong style={S.summaryValue}>{loading ? <Skeleton width={54} height={20} /> : value}</strong>
    </div>
  );
}

function AreaCard({ area, externalId }) {
  const title = area?.area_name || UNAVAILABLE;
  const description = area?.headline || area?.introduce;
  const target = area?.external_id
    ? `/projects/${encodeURIComponent(externalId)}/areas/${encodeURIComponent(area.external_id)}`
    : null;
  const content = (
    <article data-testid="area-card" style={S.areaCard}>
      <div style={area?.cover_image_url ? S.areaCover : S.areaCoverFallback}>
        {area?.cover_image_url ? (
          <img src={area.cover_image_url} alt="" style={S.areaCoverImage} />
        ) : (
          <div data-testid="area-cover-fallback"><Icon name="folder" size={28} color={color.muted} /></div>
        )}
      </div>
      <div style={S.areaBody}>
        <div style={S.areaTitleRow}>
          <h3 style={S.areaTitle}>{title}</h3>
          <span style={S.areaType}>{area?.unit_type || UNAVAILABLE}</span>
        </div>
        <div style={S.areaFacts}>
          <span>Phòng ngủ: {formatNumber(area?.bedrooms)}</span>
          <span>Diện tích: {formatNumber(area?.area_sqm) === UNAVAILABLE ? UNAVAILABLE : `${formatNumber(area?.area_sqm)} m²`}</span>
          <span>Tổng số căn: {formatNumber(area?.total_units)}</span>
          <span>Còn lại tổng cộng: {formatNumber(area?.units_remaining)}</span>
        </div>
        {description && <p style={S.areaDescription}>{description}</p>}
        {area?.external_id && <span style={S.areaCode}>Mã: {area.external_id}</span>}
        <div style={S.areaFooter}>
          <span style={S.snapshot}>Ngày chốt tồn kho: {formatDate(area?.snapshot_date)}</span>
          {target ? (
            <span style={S.areaLink}>{DASHBOARD_TEXT.openDashboard}</span>
          ) : (
            <button type="button" disabled style={S.disabledLink}>{DASHBOARD_TEXT.unavailableDashboard}: {UNAVAILABLE}</button>
          )}
        </div>
      </div>
    </article>
  );
  return target ? <Link to={target} style={S.areaCardLink} aria-label={`${DASHBOARD_TEXT.openAreaDashboard} ${title}`}>{content}</Link> : content;
}

const S = {
  crumb: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.small, color: color.muted, marginBottom: space(3), flexWrap: "wrap" },
  crumbLink: { color: color.accent, fontWeight: 600, textDecoration: "none" },
  crumbCur: { color: color.body, fontWeight: 600 },
  sep: { color: color.borderStrong },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  titleRow: { display: "flex", alignItems: "center", gap: space(3), flexWrap: "wrap" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  badge: { fontSize: size.tiny, fontWeight: 700, padding: "4px 11px", borderRadius: radius.pill },
  sub: { fontSize: size.small, color: color.muted, margin: "6px 0 0" },
  introduce: { fontSize: size.small, color: color.body, margin: `${space(3)}px 0 0`, maxWidth: "70ch", lineHeight: 1.6 },
  actions: { display: "flex", gap: space(2), flex: "none", flexWrap: "wrap" },
  secondaryBtn: {
    background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm,
    padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit",
  },
  primaryBtn: {
    background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm,
    padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer",
    fontFamily: "inherit", boxShadow: "0 4px 12px rgba(199,167,58,.24)",
  },
  addBtn: {
    background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm,
    padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600,
    cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(199,167,58,.24)",
  },
  stateActions: { display: "flex", justifyContent: "center", gap: space(2), marginTop: space(4) },
  projectContext: { display: "flex", gap: space(4), alignItems: "stretch", flexWrap: "wrap", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(3), marginBottom: space(5), boxShadow: "0 1px 3px rgba(26,24,46,.04)" },
  coverWrap: { width: 180, minWidth: 180, height: 112, borderRadius: radius.sm, overflow: "hidden" },
  coverFallback: { width: 180, minWidth: 180, height: 112, borderRadius: radius.sm, background: `linear-gradient(135deg, ${color.canvas}, ${color.accentSoft})`, display: "grid", placeItems: "center" },
  cover: { width: "100%", height: "100%", objectFit: "cover", display: "block" },
  contextDetails: { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", alignItems: "center", gap: space(4), minWidth: 0, flex: 1 },
  contextLabel: { display: "block", color: color.muted, fontSize: size.tiny, marginBottom: space(1) },
  contextValue: { display: "block", color: color.ink, fontSize: size.small, overflowWrap: "anywhere" },
  summaryGrid: { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: space(3), marginBottom: space(6) },
  summaryCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), display: "grid", gridTemplateColumns: "auto 1fr", columnGap: space(2), alignItems: "center" },
  summaryIcon: { width: 32, height: 32, borderRadius: radius.sm, background: color.accentSoft, display: "grid", placeItems: "center", gridRow: "span 2" },
  summaryLabel: { color: color.muted, fontSize: size.tiny },
  summaryValue: { color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  catalogSection: { minWidth: 0, marginBottom: space(8) },
  catalogHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(3), flexWrap: "wrap" },
  sectionTitle: { color: color.ink, fontFamily: font.display, fontSize: size.h2, margin: 0 },
  sectionSub: { color: color.muted, fontSize: size.small, margin: `${space(1)}px 0 0` },
  search: { width: 300, maxWidth: "100%", boxSizing: "border-box", background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(3)}px`, fontSize: size.small, fontFamily: "inherit", outlineColor: color.accent },
  searchContext: { display: "flex", alignItems: "center", gap: space(2), color: color.muted, fontSize: size.small, marginBottom: space(3) },
  clearButton: { border: 0, background: "transparent", color: color.accent, cursor: "pointer", fontWeight: 600, fontFamily: "inherit" },
  areaGrid: { display: "grid", gap: space(4), width: "100%" },
  areaCardLink: { display: "block", minWidth: 0, color: "inherit", textDecoration: "none", borderRadius: radius.md },
  areaCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, overflow: "hidden", minWidth: 0, boxShadow: "0 1px 3px rgba(26,24,46,.04)" },
  areaCover: { height: 128, overflow: "hidden", background: color.canvas },
  areaCoverFallback: { height: 128, display: "grid", placeItems: "center", background: `linear-gradient(135deg, ${color.canvas}, ${color.accentSoft})` },
  areaCoverImage: { width: "100%", height: "100%", objectFit: "cover", display: "block" },
  areaBody: { padding: space(4) },
  areaTitleRow: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: space(2) },
  areaTitle: { color: color.ink, fontFamily: font.display, fontSize: size.h2, margin: 0, overflowWrap: "anywhere" },
  areaType: { color: color.accent, background: color.accentSoft, borderRadius: radius.pill, padding: "4px 8px", fontSize: size.tiny, whiteSpace: "nowrap" },
  areaFacts: { display: "grid", gap: space(1), color: color.body, fontSize: size.tiny, marginTop: space(3) },
  areaDescription: { color: color.body, fontSize: size.small, lineHeight: 1.5, margin: `${space(3)}px 0 0` },
  areaCode: { display: "block", color: color.muted, fontFamily: font.mono, fontSize: size.tiny, marginTop: space(3), overflowWrap: "anywhere" },
  areaFooter: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: space(3), borderTop: `1px solid ${color.border}`, marginTop: space(4), paddingTop: space(3), flexWrap: "wrap" },
  snapshot: { color: color.muted, fontSize: size.tiny },
  areaLink: { color: color.accent, fontSize: size.tiny, fontWeight: 700, textDecoration: "none", whiteSpace: "nowrap" },
  disabledLink: { color: color.muted, background: color.canvas, border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: "6px 8px", fontSize: size.tiny, fontFamily: "inherit" },
  inlineState: { display: "flex", alignItems: "center", flexDirection: "column", gap: space(2), border: `1px dashed ${color.borderStrong}`, borderRadius: radius.md, padding: space(10), textAlign: "center", color: color.muted },
  stateWrap: { maxWidth: 560, margin: "80px auto", textAlign: "center" },
};
