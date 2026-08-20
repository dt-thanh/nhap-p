// frontend/src/pages/AreaDetailPage.jsx
// S04 — Chi tiết phân khu: tồn kho + giao dịch THẬT (GET /inventory, GET
// /deals, scoped theo external_id của dự án/phân khu — cùng quy ước URL với
// phần còn lại của app). Trước đây trang này không có lối vào UI nào và tab
// "Xếp hạng" gọi một stub luôn trả rỗng — giờ trang được điều hướng tới từ
// AreaComparison/AreaDetailTable (dashboard), và tab xếp hạng nói rõ vì sao
// chưa có màn xem độc lập thay vì hiện một bảng trống trông như "không có
// dữ liệu".
import React, { useMemo, useState } from "react";
import { useOutletContext, useParams, useNavigate } from "react-router-dom";
import { getAreaByExternalId, getDashboardTrend, listInventoryScoped, listDealsScoped } from "../api/endpoints";
import { areaLabel } from "../utils/areaLabel";
import { useAsync } from "../hooks/useAsync";
import { color, size, radius, shadow, space, font } from "../styles/tokens";
import { SectionState, EmptyState, Skeleton, fmt } from "../components/ui/States";
import AbsorptionTrendChart from "../components/dashboard/AbsorptionTrendChart";
import { DASHBOARD_TEXT, formatDashboardDate } from "../components/dashboard/labels";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";

const TABS = [
  { key: "inventory", label: "Tồn kho" },
  { key: "deals", label: "Giao dịch" },
  { key: "ranking", label: "Phân loại căn theo trạng thái" },
];

const UNIT_STATUS_BADGE = {
  available: { label: "Còn trống", fg: color.ok, bg: color.okSoft },
  reserved: { label: "Đang giữ", fg: color.warn, bg: color.warnSoft },
  sold: { label: "Đã bán", fg: color.muted, bg: color.canvas },
  blocked: { label: "Tạm khoá", fg: color.danger, bg: color.dangerSoft },
};

const AREA_STATUS_LABEL = {
  active: "Đang hoạt động",
  pending: "Chờ duyệt",
  archived: "Đã lưu trữ",
  inactive: "Không hoạt động",
};

const DEAL_STATUS_LABEL = {
  reserved: "Đang giữ",
  sold: "Đã bán",
  lost: "Đã mất",
  cancelled: "Đã hủy",
  canceled: "Đã hủy",
};

const STATUS_PRIORITY = { sold: 0, reserved: 1, available: 2, blocked: 3 };
const MAX_UNIT_ROWS = 200;

function normalizeStatus(value) {
  const status = String(value || "").trim().toLowerCase();
  return UNIT_STATUS_BADGE[status] ? status : null;
}

function compareUnits(a, b) {
  const aStatus = normalizeStatus(a?.status);
  const bStatus = normalizeStatus(b?.status);
  const statusOrder = (status) => STATUS_PRIORITY[status] ?? 99;
  const statusDifference = statusOrder(aStatus) - statusOrder(bStatus);
  if (statusDifference !== 0) return statusDifference;

  const aDeal = String(a?.active_deal_status || "").trim().toLowerCase();
  const bDeal = String(b?.active_deal_status || "").trim().toLowerCase();
  if (Boolean(aDeal) !== Boolean(bDeal)) return aDeal ? -1 : 1;
  const dealDifference = aDeal.localeCompare(bDeal, "vi");
  if (dealDifference !== 0) return dealDifference;

  const codeDifference = String(a?.unit_code || "").localeCompare(String(b?.unit_code || ""), "vi", { numeric: true });
  if (codeDifference !== 0) return codeDifference;
  return String(a?.unit_id || "").localeCompare(String(b?.unit_id || ""), "vi");
}

export default function AreaDetailPage() {
  const { id: projectId, areaId } = useParams();
  const navigate = useNavigate();
  const workspace = useOutletContext() || null;
  const inWorkspace = Boolean(workspace);
  const [tab, setTab] = useState("inventory");

  const { data: area, loading, error } = useAsync(() => getAreaByExternalId(areaId), [areaId]);
  const trend = useAsync(
    () => (area
      ? getDashboardTrend({ areaId: area.area_id, areaTotalUnits: area.total_units, granularity: "day" })
      : Promise.resolve(null)),
    [area?.area_id, area?.total_units],
  );
  const inventory = useAsync(
    () => (area
      ? listInventoryScoped(projectId, { external_area_id: areaId, include_units: true, limit: 200 })
      : Promise.resolve(null)),
    [area?.area_id, projectId, areaId],
  );

  if (error) {
    const notFound = error.status === 404;
    const forbidden = error.status === 401 || error.status === 403;
    return (
      <div style={S.stateWrap}>
        <h1 style={S.h1}>
          {notFound ? "Không tìm thấy phân khu" : forbidden ? "Bạn không có quyền xem phân khu này" : "Không tải được phân khu"}
        </h1>
        <p style={S.sub}>
          {notFound
            ? `Không có phân khu nào khớp "${areaId}".`
            : forbidden
              ? "Token hiện tại không nằm trong phạm vi dự án này."
              : "Đã xảy ra lỗi khi tải phân khu. Vui lòng thử lại."}
        </p>
        <button style={S.addBtn} onClick={() => navigate(`/projects/${projectId}`)}>Về trang dự án</button>
      </div>
    );
  }

  return (
    <>
      <GlobalKeyframes />
      {!inWorkspace && (
        <div style={S.crumb}>
          <span style={S.link} onClick={() => navigate("/projects")}>Dự án</span><span style={S.sep}>/</span>
          <span style={S.link} onClick={() => navigate(`/projects/${projectId}`)}>Chi tiết</span><span style={S.sep}>/</span>
          <span style={S.cur}>{loading ? "…" : area ? areaLabel(area) : areaId}</span>
        </div>
      )}

      <header data-testid={inWorkspace ? "selected-area-dashboard" : undefined} style={{ ...S.head, ...(inWorkspace ? S.workspaceHead : null) }}>
        <div>
          <h1 style={S.h1}>{loading ? <Skeleton width={220} height={26} /> : area?.area_name || "Chưa có dữ liệu"}</h1>
          <p style={S.sub}>
            {loading ? "…" : `${area?.unit_type || "Chưa có dữ liệu"} · ${fmt(area?.total_units)} căn`}
          </p>
          {inWorkspace && area?.status && <span style={S.areaStatus}>Trạng thái: {AREA_STATUS_LABEL[area.status] || "Chưa đủ dữ liệu"}</span>}
        </div>
        <div style={S.actions}>
          {inWorkspace && workspace.openCatalog && (
            <button type="button" style={S.secondaryBtn} onClick={workspace.openCatalog}>Chọn phân khu</button>
          )}
        </div>
      </header>

      <TrendSection areaLoading={loading} trend={trend} />

      <div style={S.tabs}>
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{ ...S.tab, ...(tab === t.key ? S.tabOn : null) }}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "inventory" && <InventoryTab inventory={inventory} />}
      {tab === "deals" && <DealsTab projectId={projectId} areaId={areaId} />}
      {tab === "ranking" && (
        <StatusUnitsSection
          units={inventory.data?.units ?? []}
          loading={inventory.loading}
          error={inventory.error}
          totalUnits={inventory.data?.totals?.total_units}
        />
      )}
    </>
  );
}

function InventoryTab({ inventory: inv }) {
  const units = inv.data?.units ?? [];

  return (
    <section style={S.card}>
      <div style={S.cardHead}>
        <div>
          <h2 style={S.h2}>Tồn kho</h2>
          <p style={S.csub}>Danh sách căn trong phân khu này.</p>
        </div>
        {units.length > 0 && <span style={S.count}>{units.length} căn</span>}
      </div>

      <SectionState loading={inv.loading} error={inv.error} empty={!inv.loading && !inv.error && units.length === 0} onRetry={inv.reload} compact>
        <div style={S.scroll}>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Mã căn</th>
                <th style={S.th}>Loại</th>
                <th style={S.th}>Trạng thái</th>
                <th style={S.th}>Giao dịch đang giữ</th>
              </tr>
            </thead>
            <tbody>
              {units.map((u) => {
                const st = UNIT_STATUS_BADGE[u.status] || UNIT_STATUS_BADGE.available;
                return (
                  <tr key={u.unit_id}>
                    <td style={{ ...S.td, fontWeight: 600, color: color.ink }}>{u.unit_code}</td>
                    <td style={S.td}>{u.unit_type}</td>
                    <td style={S.td}>
                      <span style={{ ...S.badge, color: st.fg, background: st.bg }}>{st.label}</span>
                    </td>
                    <td style={S.td}>{displayDealStatus(u.active_deal_status)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </SectionState>
    </section>
  );
}

function TrendSection({ areaLoading, trend }) {
  const points = trend.data?.points || [];
  const waitingForTrend = !areaLoading && !trend.data && !trend.error;
  const loading = areaLoading || trend.loading || waitingForTrend;

  if (!loading && !trend.error && points.length === 0) {
    return (
      <section style={S.trendEmptyCard} aria-labelledby="area-trend-title">
        <div style={S.trendHead}>
          <div>
            <h2 id="area-trend-title" style={S.h2}>Xu hướng hấp thụ</h2>
            <p style={S.csub}>{DASHBOARD_TEXT.unitsSoldPerDay} · {DASHBOARD_TEXT.cumulativeSold} · {DASHBOARD_TEXT.sellThrough}</p>
          </div>
        </div>
        <EmptyState icon="rate" title="Chưa có dữ liệu xu hướng hấp thụ" />
      </section>
    );
  }

  const chart = <AbsorptionTrendChart series={points} loading={loading} error={trend.error} onRetry={trend.reload} />;
  return <div data-testid={loading ? "area-trend-loading" : undefined}>{chart}</div>;
}

function StatusUnitsSection({ units, loading, error, totalUnits }) {
  const sortedUnits = useMemo(() => [...units].sort(compareUnits), [units]);
  const hasKnownStatus = sortedUnits.some((unit) => normalizeStatus(unit?.status));
  const numericTotal = Number(totalUnits);
  const partial = Number.isFinite(numericTotal)
    ? numericTotal > MAX_UNIT_ROWS || sortedUnits.length >= MAX_UNIT_ROWS
    : sortedUnits.length >= MAX_UNIT_ROWS;

  return (
    <section style={S.card} aria-labelledby="status-units-title">
      <div style={S.cardHead}>
        <div>
          <h2 id="status-units-title" style={S.h2}>Phân loại căn theo trạng thái</h2>
          <p style={S.csub}>Dựa trên các căn đã tải · Hiển thị tối đa 200 căn</p>
        </div>
        {!loading && !error && sortedUnits.length > 0 && <span style={S.count}>{sortedUnits.length} căn đã tải</span>}
      </div>

      {loading && <div data-testid="status-units-loading" style={S.statusState}><Skeleton height={18} width="45%" /><Skeleton height={120} /></div>}

      {!loading && (error || sortedUnits.length === 0 || !hasKnownStatus) && (
        <div data-testid="status-units-empty" style={S.statusState}>
          <EmptyState icon="inbox" title="Chưa có dữ liệu trạng thái căn" compact />
        </div>
      )}

      {!loading && !error && sortedUnits.length > 0 && hasKnownStatus && (
        <>
          {partial && <div style={S.scopeNote}>Đang hiển thị một phần danh sách căn</div>}
          <div style={S.scroll}>
            <table data-testid="status-units-table" style={S.table}>
              <thead>
                <tr>
                  <th style={S.th}>STT</th>
                  <th style={S.th}>Mã căn</th>
                  <th style={S.th}>Loại</th>
                  <th style={S.th}>Trạng thái</th>
                  <th style={S.th}>Giao dịch đang giữ</th>
                </tr>
              </thead>
              <tbody>
                {sortedUnits.map((unit, index) => {
                  const status = normalizeStatus(unit?.status);
                  const badge = status ? UNIT_STATUS_BADGE[status] : null;
                  return (
                    <tr key={unit?.unit_id || unit?.external_unit_id || `${unit?.unit_code || "unit"}-${index}`}>
                      <td style={S.td}>{index + 1}</td>
                      <td style={{ ...S.td, fontWeight: 600, color: color.ink }}>{unit?.unit_code || "Chưa có dữ liệu"}</td>
                      <td style={S.td}>{unit?.unit_type || "Chưa có dữ liệu"}</td>
                      <td style={S.td}>
                        {badge ? <span style={{ ...S.badge, color: badge.fg, background: badge.bg }}>{badge.label}</span> : "Chưa có dữ liệu"}
                      </td>
                      <td style={S.td}>{unit?.active_deal_status ? displayDealStatus(unit.active_deal_status) : "Chưa có dữ liệu"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function DealsTab({ projectId, areaId }) {
  const deals = useAsync(() => listDealsScoped(projectId, { external_area_id: areaId }), [projectId, areaId]);
  const items = deals.data?.items ?? [];

  return (
    <section style={S.card}>
      <div style={S.cardHead}>
        <div>
          <h2 style={S.h2}>Giao dịch</h2>
          <p style={S.csub}>Lịch sử đặt giữ/bán của các căn trong phân khu.</p>
        </div>
        {items.length > 0 && <span style={S.count}>{items.length} giao dịch</span>}
      </div>

      <SectionState loading={deals.loading} error={deals.error} empty={!deals.loading && !deals.error && items.length === 0} onRetry={deals.reload} compact>
        <div style={S.scroll}>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Mã căn hộ</th>
                <th style={S.th}>Trạng thái</th>
                <th style={S.th}>Đặt giữ lúc</th>
                <th style={S.th}>Bán lúc</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.deal_id}>
                  <td style={{ ...S.td, fontWeight: 600, color: color.ink, fontFamily: font.mono, fontSize: size.tiny }}>{d.unit_id}</td>
                  <td style={S.td}>{displayDealStatus(d.status)}</td>
                  <td style={{ ...S.td, color: color.muted }}>{d.reserved_at ? formatDashboardDate(d.reserved_at) : "—"}</td>
                  <td style={{ ...S.td, color: color.muted }}>{d.sold_at ? formatDashboardDate(d.sold_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionState>
    </section>
  );
}

function displayDealStatus(value) {
  if (!value) return "—";
  const key = String(value).trim().toLowerCase();
  return DEAL_STATUS_LABEL[key] || "Chưa đủ dữ liệu";
}

const S = {
  crumb: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.small, color: color.muted, marginBottom: space(3) },
  link: { color: color.accent, fontWeight: 600, cursor: "pointer" },
  cur: { color: color.body, fontWeight: 600 },
  sep: { color: color.borderStrong },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "5px 0 0" },
  workspaceHead: { padding: `${space(4)}px ${space(5)}px 0` },
  areaStatus: { display: "inline-block", color: color.muted, fontSize: size.tiny, marginTop: space(2) },
  actions: { display: "flex", gap: space(2), flexWrap: "wrap" },
  secondaryBtn: { background: color.surface, color: color.body, border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit" },
  addBtn: { background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(199,167,58,.24)" },
  tabs: { display: "flex", gap: 2, borderBottom: `1px solid ${color.border}`, marginBottom: space(5), overflowX: "auto" },
  tab: { background: "transparent", border: "none", borderBottom: "2px solid transparent", color: color.muted, fontSize: size.small, fontWeight: 600, padding: `${space(3)}px ${space(4)}px`, cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap" },
  tabOn: { color: color.accent, borderBottomColor: color.accent },
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflow: "hidden" },
  trendEmptyCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, marginBottom: space(5), overflow: "hidden" },
  trendHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: `${space(5)}px ${space(5)}px 0` },
  cardHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: `${space(4)}px ${space(5)}px ${space(3)}px` },
  h2: { fontFamily: font.display, fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0 },
  csub: { fontSize: size.tiny, color: color.muted, margin: "3px 0 0" },
  count: { fontSize: size.tiny, color: color.muted },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", minWidth: 640 },
  th: { fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em", padding: `${space(2)}px ${space(4)}px`, borderTop: `1px solid ${color.border}`, borderBottom: `1px solid ${color.border}`, background: color.canvas, textAlign: "left", whiteSpace: "nowrap" },
  td: { padding: `${space(3)}px ${space(4)}px`, fontSize: size.small, color: color.body, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  badge: { fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: radius.pill },
  statusState: { padding: `${space(4)}px ${space(5)}px` },
  scopeNote: { padding: `0 ${space(5)}px ${space(3)}px`, color: color.muted, fontSize: size.tiny },
  stateWrap: { maxWidth: 560, margin: "80px auto", textAlign: "center" },
};
