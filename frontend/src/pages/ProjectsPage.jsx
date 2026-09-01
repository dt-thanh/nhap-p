// S02 — Danh sách dự án.
// Nguồn dữ liệu duy nhất là GET /api/v1/projects thông qua listProjects().
// ProjectSummary hiện không có các chỉ số hấp thụ/đơn vị, vì vậy các trường
// đó chỉ hiển thị "Chưa có dữ liệu" thay vì suy diễn từ dữ liệu khác.
import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listProjects } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { useBreakpoint, pick } from "../hooks/useBreakpoint";
import { color, size, radius, shadow, space, font } from "../styles/tokens";
import { Skeleton } from "../components/ui/States";
import Icon from "../components/ui/Icon";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";

const UNAVAILABLE = "Chưa có dữ liệu";

const STATUS = {
  pending: { label: "Chờ duyệt", fg: color.warn, bg: color.warnSoft },
  active: { label: "Đang bán", fg: color.ok, bg: color.okSoft },
  rejected: { label: "Bị từ chối", fg: color.danger, bg: color.dangerSoft },
  archived: { label: "Lưu trữ", fg: color.muted, bg: color.canvas },
};

const FILTERS = [
  { key: "all", label: "Tất cả" },
  { key: "active", label: "Đang bán" },
  { key: "pending", label: "Chờ duyệt" },
  { key: "rejected", label: "Bị từ chối" },
  { key: "archived", label: "Lưu trữ" },
];

function statusInfo(status) {
  return STATUS[status] || { label: UNAVAILABLE, fg: color.muted, bg: color.canvas };
}

function externalProjectId(project) {
  const value = project?.external_id;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function projectKey(project, index) {
  return externalProjectId(project) || project?.project_id || `${project?.name || UNAVAILABLE}-${index}`;
}

function uniqueProjects(rows) {
  const seen = new Set();
  return (Array.isArray(rows) ? rows : []).filter((project, index) => {
    const key = projectKey(project, index);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function searchable(value) {
  return String(value ?? "").toLocaleLowerCase("vi-VN");
}

function formatLaunchDate(value) {
  if (!value) return UNAVAILABLE;
  const rawValue = String(value);
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(rawValue);
  const date = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
  return Number.isNaN(date.getTime()) ? UNAVAILABLE : date.toLocaleDateString("vi-VN");
}

export default function ProjectsPage() {
  const navigate = useNavigate();
  const { bp } = useBreakpoint();
  const { data, loading, error, reload } = useAsync(() => listProjects(), []);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");

  const projects = useMemo(() => uniqueProjects(data), [data]);
  const statusCounts = useMemo(
    () => projects.reduce((counts, project) => {
      if (STATUS[project?.status]) counts[project.status] += 1;
      return counts;
    }, { active: 0, pending: 0, rejected: 0, archived: 0 }),
    [projects],
  );
  const attentionProjects = useMemo(
    () => projects.filter((project) => project?.status === "pending" || project?.status === "rejected"),
    [projects],
  );
  const filteredProjects = useMemo(() => {
    const normalizedQuery = searchable(query.trim());
    return projects.filter((project) => {
      const matchesStatus = filter === "all" || project?.status === filter;
      const matchesQuery = !normalizedQuery
        || searchable(project?.name).includes(normalizedQuery)
        || searchable(externalProjectId(project)).includes(normalizedQuery);
      return matchesStatus && matchesQuery;
    });
  }, [filter, projects, query]);

  const columns = pick(bp, { mobile: 1, tablet: 2, laptop: 3, desktop: 3 });

  function clearFilters() {
    setQuery("");
    setFilter("all");
  }

  return (
    <>
      <GlobalKeyframes />

      <div style={S.crumb}>
        <span style={S.crumbCurrent}>Dự án</span>
        <span style={S.crumbSep}>/</span>
        <span style={S.crumbCurrent}>Tổng quan</span>
      </div>

      <div style={S.head}>
        <div>
          <h1 style={S.h1}>Dự án</h1>
          <p style={S.sub}>Quản lý và theo dõi hiệu suất toàn bộ danh mục dự án</p>
        </div>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Tìm theo tên hoặc mã dự án…"
          aria-label="Tìm kiếm dự án"
          style={S.search}
        />
      </div>

      <ProjectKpis loading={loading} ready={Array.isArray(data)} projects={projects} statusCounts={statusCounts} />
      <AttentionPanel projects={attentionProjects} loading={loading} navigate={navigate} />

      <div style={S.filterHead}>
        <h2 style={S.sectionTitle}>Danh mục dự án</h2>
        <span style={S.resultCount}>{loading ? "Đang tải…" : `${filteredProjects.length} dự án`}</span>
      </div>

      <div style={S.filters} aria-label="Lọc trạng thái dự án">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setFilter(item.key)}
            aria-pressed={filter === item.key}
            style={{ ...S.chip, ...(filter === item.key ? S.chipOn : null) }}
          >
            {item.label}
            <span data-testid={`filter-count-${item.key}`} aria-hidden="true">
              {` (${loading ? "—" : item.key === "all" ? projects.length : statusCounts[item.key]})`}
            </span>
          </button>
        ))}
        {(query || filter !== "all") && (
          <button type="button" onClick={clearFilters} style={S.clearFilters}>Xóa bộ lọc</button>
        )}
      </div>

      {loading ? (
        <ProjectGridSkeleton columns={columns} />
      ) : error ? (
        <ProjectsErrorState error={error} onRetry={reload} />
      ) : projects.length === 0 ? (
        <ProjectEmptyState />
      ) : filteredProjects.length === 0 ? (
        <FilteredEmptyState onClear={clearFilters} />
      ) : (
        <div style={{ ...S.grid, gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
          {filteredProjects.map((project, index) => (
            <ProjectCard key={projectKey(project, index)} project={project} navigate={navigate} />
          ))}
        </div>
      )}
    </>
  );
}

function ProjectKpis({ loading, ready, projects, statusCounts }) {
  const attentionCount = statusCounts.pending + statusCounts.rejected;
  const cards = [
    { key: "total", label: "Tổng dự án", value: projects.length, hint: ready ? "Dự án đang được quản lý" : UNAVAILABLE, tone: "accent" },
    { key: "active", label: "Đang bán", value: statusCounts.active, hint: ready ? "Theo trạng thái dự án" : UNAVAILABLE, tone: "ok" },
    { key: "attention", label: "Cần xử lý", value: attentionCount, hint: ready ? `${statusCounts.pending} chờ duyệt · ${statusCounts.rejected} bị từ chối` : UNAVAILABLE, tone: attentionCount > 0 ? "danger" : "muted" },
  ];

  return (
    <section style={S.kpiGrid} aria-label="Tóm tắt dự án">
      {cards.map((card) => (
        <article key={card.key} style={S.kpiCard} data-testid={`kpi-${card.key}`}>
          <div style={S.kpiLabel}>{card.label}</div>
          <div style={{ ...S.kpiValue, ...(S[`kpi${card.tone}`] || null) }}>
            {loading ? <Skeleton width={46} height={30} /> : ready ? card.value : UNAVAILABLE}
          </div>
          <div style={S.kpiHint}>{loading ? <Skeleton width="75%" height={14} /> : card.hint}</div>
        </article>
      ))}
    </section>
  );
}

function AttentionPanel({ projects, loading, navigate }) {
  return (
    <section style={S.attention} aria-labelledby="attention-title">
      <div style={S.attentionHead}>
        <div>
          <div style={S.eyebrow}>Ưu tiên theo dõi</div>
          <h2 id="attention-title" style={S.sectionTitle}>Cần chú ý</h2>
        </div>
        <Icon name="warning" size={19} color={loading || projects.length ? color.warn : color.ok} />
      </div>

      {loading ? (
        <div style={S.attentionLoading} data-testid="projects-attention-loading">
          <Skeleton width="30%" height={16} />
          <Skeleton width="78%" height={16} />
        </div>
      ) : projects.length === 0 ? (
        <div style={S.noAttention}>
          <Icon name="sold" size={18} color={color.ok} />
          <span>Không có vấn đề cần xử lý</span>
        </div>
      ) : (
        <div style={S.attentionList}>
          {projects.map((project, index) => {
            const status = statusInfo(project?.status);
            const externalId = externalProjectId(project);
            return (
              <div key={projectKey(project, index)} style={S.attentionItem}>
                <span style={{ ...S.badge, color: status.fg, background: status.bg }}>{status.label}</span>
                <div style={S.attentionCopy}>
                  <strong style={S.attentionName}>{project?.name || UNAVAILABLE}</strong>
                  <span style={S.attentionMessage}>
                    {project?.status === "pending" ? "Dự án đang chờ được phê duyệt." : "Dự án đã bị từ chối."}
                  </span>
                </div>
                {externalId && (
                  <button type="button" style={S.attentionAction} onClick={() => navigate(`/projects/${externalId}`)}>
                    Xem dự án
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ProjectCard({ project, navigate }) {
  const status = statusInfo(project?.status);
  const name = project?.name || UNAVAILABLE;
  const externalId = externalProjectId(project);
  const hasIdentity = Boolean(externalId);

  return (
    <article style={S.card}>
      <button
        type="button"
        style={{ ...S.cardLink, ...(hasIdentity ? null : S.cardDisabled) }}
        disabled={!hasIdentity}
        title={hasIdentity ? undefined : "Dự án chưa có external ID — chưa thể mở khu vực làm việc"}
        onClick={() => hasIdentity && navigate(`/projects/${externalId}`)}
      >
        <div style={S.cardTop}>
          {project?.cover_image_url && (
            <img
              src={project.cover_image_url}
              alt=""
              style={S.cover}
              onError={(event) => { event.currentTarget.style.display = "none"; }}
            />
          )}
          <span style={{ ...S.badge, ...S.badgeOverlay, color: status.fg, background: status.bg }}>{status.label}</span>
        </div>
        <div style={S.cardBody}>
          <div style={S.name}>{name}</div>
          <div style={S.projectCode}>Mã dự án: {externalId || UNAVAILABLE}</div>
          <div style={S.launchDate}>Mở bán: {formatLaunchDate(project?.launch_date)}</div>
          {project?.headline && <div style={S.headline}>{project.headline}</div>}
          <div style={S.metaGrid}>
            <div><span style={S.metaLabel}>Phân khu</span><strong style={S.metaValue}>{UNAVAILABLE}</strong></div>
            <div><span style={S.metaLabel}>Tổng số căn</span><strong style={S.metaValue}>{UNAVAILABLE}</strong></div>
          </div>
        </div>
      </button>
    </article>
  );
}

function ProjectGridSkeleton({ columns }) {
  return (
    <div style={{ ...S.grid, gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }} data-testid="projects-loading">
      {[1, 2, 3].map((item) => (
        <div key={item} style={S.skeletonCard}>
          <Skeleton height={116} />
          <div style={S.skeletonBody}>
            <Skeleton width="72%" height={20} />
            <Skeleton width="45%" height={14} />
            <Skeleton width="60%" height={14} />
          </div>
        </div>
      ))}
    </div>
  );
}

function ProjectsErrorState({ error, onRetry }) {
  return (
    <div style={S.stateWrap} data-testid="projects-error">
      <div style={{ ...S.stateIcon, background: color.dangerSoft }}><Icon name="warning" size={23} color={color.danger} /></div>
      <h2 style={S.stateTitle}>Không thể tải danh sách dự án</h2>
      <p style={S.stateText}>Dữ liệu chưa được cập nhật. Vui lòng thử lại hoặc kiểm tra trạng thái dữ liệu.</p>
      {error?.message && <span style={S.errorDetail}>{error.message}</span>}
      <button type="button" style={S.primaryButton} onClick={onRetry}>Thử lại</button>
    </div>
  );
}

function ProjectEmptyState() {
  return (
    <div style={S.stateWrap} data-testid="projects-empty">
      <div style={S.stateIcon}><Icon name="folder" size={23} color={color.muted} /></div>
      <h2 style={S.stateTitle}>Chưa có dự án nào</h2>
      <p style={S.stateText}>Chưa có dữ liệu dự án trong phạm vi được cấp.</p>
    </div>
  );
}

function FilteredEmptyState({ onClear }) {
  return (
    <div style={S.stateWrap} data-testid="projects-filtered-empty">
      <div style={S.stateIcon}><Icon name="filter" size={23} color={color.muted} /></div>
      <h2 style={S.stateTitle}>Không tìm thấy dự án phù hợp</h2>
      <button type="button" style={S.secondaryButton} onClick={onClear}>Xóa bộ lọc</button>
    </div>
  );
}

const S = {
  crumb: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.tiny, color: color.muted, marginBottom: space(3) },
  crumbCurrent: { fontWeight: 600 },
  crumbSep: { color: color.borderStrong },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "5px 0 0", lineHeight: 1.5 },
  search: { border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontFamily: "inherit", minWidth: 260, color: color.ink, outline: "none" },
  kpiGrid: { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: space(4), marginBottom: space(5) },
  kpiCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(5), minWidth: 0 },
  kpiLabel: { color: color.muted, fontSize: size.tiny, fontWeight: 700, letterSpacing: ".04em" },
  kpiValue: { color: color.ink, fontFamily: font.display, fontSize: 30, lineHeight: 1.15, fontWeight: 700, margin: `${space(2)}px 0` },
  kpiaccent: { color: color.accent }, kpiok: { color: color.ok }, kpidanger: { color: color.danger }, kpimuted: { color: color.muted },
  kpiHint: { color: color.muted, fontSize: size.tiny, minHeight: 18 },
  attention: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, padding: space(5), marginBottom: space(6) },
  attentionHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: space(4) },
  eyebrow: { color: color.muted, fontSize: 11, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: space(1) },
  sectionTitle: { color: color.ink, fontFamily: font.display, fontSize: size.h2, fontWeight: 700, margin: 0 },
  noAttention: { display: "flex", alignItems: "center", gap: space(2), color: color.ok, fontSize: size.small, padding: `${space(2)}px 0` },
  attentionLoading: { display: "flex", flexDirection: "column", gap: space(3) },
  attentionList: { display: "flex", flexDirection: "column", gap: space(3) },
  attentionItem: { display: "flex", alignItems: "center", gap: space(3), padding: `${space(3)}px 0`, borderTop: `1px solid ${color.border}`, minWidth: 0 },
  attentionCopy: { display: "flex", flexDirection: "column", gap: 3, minWidth: 0, flex: 1 },
  attentionName: { color: color.ink, fontSize: size.small, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  attentionMessage: { color: color.body, fontSize: size.tiny },
  attentionAction: { flex: "none", background: "transparent", color: color.accent, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(3)}px`, fontSize: size.tiny, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  filterHead: { display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: space(3), marginBottom: space(3) },
  resultCount: { color: color.muted, fontSize: size.tiny },
  filters: { display: "flex", alignItems: "center", gap: space(2), marginBottom: space(5), flexWrap: "wrap" },
  chip: { background: color.surface, border: `1px solid ${color.borderStrong}`, color: color.body, fontSize: size.small, fontWeight: 600, padding: "7px 13px", borderRadius: radius.pill, cursor: "pointer", fontFamily: "inherit" },
  chipOn: { background: color.ink, color: "#fff", border: `1px solid ${color.ink}` },
  clearFilters: { background: "transparent", border: "none", color: color.accent, fontSize: size.tiny, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", padding: "7px 4px" },
  grid: { display: "grid", gap: space(4) },
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, overflow: "hidden", boxShadow: shadow, minWidth: 0 },
  cardLink: { display: "block", width: "100%", textAlign: "left", background: color.surface, border: "none", cursor: "pointer", fontFamily: "inherit", padding: 0 },
  cardDisabled: { cursor: "not-allowed", opacity: 0.6 },
  cardTop: { height: 116, background: `linear-gradient(135deg, ${color.accent}22, ${color.ok}18)`, position: "relative", display: "flex", justifyContent: "flex-end", alignItems: "flex-start", padding: space(3), boxSizing: "border-box", overflow: "hidden" },
  cover: { position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", display: "block" },
  badge: { fontSize: size.tiny, fontWeight: 700, padding: "4px 11px", borderRadius: radius.pill, whiteSpace: "nowrap" },
  badgeOverlay: { position: "relative", zIndex: 1 },
  cardBody: { padding: space(4) },
  name: { fontFamily: font.display, fontSize: size.body + 1, fontWeight: 700, color: color.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  projectCode: { color: color.body, fontSize: size.tiny, marginTop: space(2), overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  launchDate: { color: color.muted, fontSize: size.tiny, marginTop: space(1) },
  headline: { color: color.body, fontSize: size.tiny, lineHeight: 1.45, marginTop: space(3), display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" },
  metaGrid: { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: space(3), borderTop: `1px solid ${color.border}`, marginTop: space(4), paddingTop: space(3) },
  metaLabel: { display: "block", color: color.muted, fontSize: 11, marginBottom: 2 },
  metaValue: { display: "block", color: color.body, fontSize: size.tiny, fontWeight: 600 },
  skeletonCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, overflow: "hidden" },
  skeletonBody: { display: "flex", flexDirection: "column", gap: space(2), padding: space(4) },
  stateWrap: { display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", padding: `${space(12)}px ${space(4)}px`, gap: space(3) },
  stateIcon: { width: 48, height: 48, borderRadius: "50%", background: color.canvas, display: "grid", placeItems: "center" },
  stateTitle: { color: color.ink, fontFamily: font.display, fontSize: size.h2, margin: 0 },
  stateText: { color: color.muted, fontSize: size.small, maxWidth: "44ch", margin: 0, lineHeight: 1.5 },
  errorDetail: { color: color.muted, fontSize: size.tiny },
  primaryButton: { background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
  secondaryButton: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" },
};
