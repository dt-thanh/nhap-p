// frontend/src/pages/AreaDetailPage.jsx
// S04 — Chi tiết phân khu: tồn kho + giao dịch THẬT (GET /inventory, GET
// /deals, scoped theo external_id của dự án/phân khu — cùng quy ước URL với
// phần còn lại của app). Trước đây trang này không có lối vào UI nào và tab
// "Xếp hạng" gọi một stub luôn trả rỗng — giờ trang được điều hướng tới từ
// AreaComparison/AreaDetailTable (dashboard), và tab xếp hạng nói rõ vì sao
// chưa có màn xem độc lập thay vì hiện một bảng trống trông như "không có
// dữ liệu".
import React, { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getAreaByExternalId, listInventoryScoped, listDealsScoped } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { color, size, radius, shadow, space, font } from "../styles/tokens";
import { SectionState, EmptyState, Skeleton, fmt } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";

const TABS = [
  { key: "inventory", label: "Tồn kho" },
  { key: "deals", label: "Giao dịch" },
  { key: "ranking", label: "Xếp hạng khả năng bán" },
];

const UNIT_STATUS_BADGE = {
  available: { label: "Còn trống", fg: color.ok, bg: color.okSoft },
  reserved: { label: "Đang giữ", fg: color.warn, bg: color.warnSoft },
  sold: { label: "Đã bán", fg: color.muted, bg: color.canvas },
  blocked: { label: "Tạm khoá", fg: color.danger, bg: color.dangerSoft },
};

export default function AreaDetailPage() {
  const { id: projectId, areaId } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("inventory");

  const { data: area, loading, error } = useAsync(() => getAreaByExternalId(areaId), [areaId]);

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
              : error.message}
        </p>
        <button style={S.addBtn} onClick={() => navigate(`/projects/${projectId}`)}>Về trang dự án</button>
      </div>
    );
  }

  return (
    <>
      <GlobalKeyframes />
      <div style={S.crumb}>
        <span style={S.link} onClick={() => navigate("/projects")}>Dự án</span><span style={S.sep}>/</span>
        <span style={S.link} onClick={() => navigate(`/projects/${projectId}`)}>Chi tiết</span><span style={S.sep}>/</span>
        <span style={S.cur}>{loading ? "…" : area ? `${area.area_name} · ${area.unit_type}` : areaId}</span>
      </div>

      <header style={S.head}>
        <div>
          <h1 style={S.h1}>{loading ? <Skeleton width={220} height={26} /> : area?.area_name}</h1>
          <p style={S.sub}>
            {loading ? "…" : `${area?.unit_type} · ${fmt(area?.total_units)} căn`}
          </p>
        </div>
        <button style={S.addBtn} onClick={() => navigate("/import")}>Nạp dữ liệu</button>
      </header>

      <div style={S.tabs}>
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{ ...S.tab, ...(tab === t.key ? S.tabOn : null) }}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "inventory" && <InventoryTab projectId={projectId} areaId={areaId} />}
      {tab === "deals" && <DealsTab projectId={projectId} areaId={areaId} />}
      {tab === "ranking" && <RankingUnavailable />}
    </>
  );
}

function InventoryTab({ projectId, areaId }) {
  const inv = useAsync(
    () => listInventoryScoped(projectId, { external_area_id: areaId, include_units: true, limit: 200 }),
    [projectId, areaId],
  );
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
                    <td style={S.td}>{u.active_deal_status ?? "—"}</td>
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
                <th style={S.th}>Căn (unit_id)</th>
                <th style={S.th}>Trạng thái</th>
                <th style={S.th}>Đặt giữ lúc</th>
                <th style={S.th}>Bán lúc</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.deal_id}>
                  <td style={{ ...S.td, fontWeight: 600, color: color.ink, fontFamily: font.mono, fontSize: size.tiny }}>{d.unit_id}</td>
                  <td style={S.td}>{d.status}</td>
                  <td style={{ ...S.td, color: color.muted }}>{d.reserved_at ? new Date(d.reserved_at).toLocaleDateString("vi-VN") : "—"}</td>
                  <td style={{ ...S.td, color: color.muted }}>{d.sold_at ? new Date(d.sold_at).toLocaleDateString("vi-VN") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionState>
    </section>
  );
}

function RankingUnavailable() {
  return (
    <section style={S.card}>
      <div style={S.cardHead}>
        <h2 style={S.h2}>Xếp hạng khả năng bán</h2>
      </div>
      <EmptyState
        icon="rate"
        title="Chưa có màn xem xếp hạng độc lập"
        hint='Xếp hạng khả năng bán hiện được tính BÊN TRONG luồng đề xuất tư vấn (mục "AI Agent") — chưa có endpoint đọc xếp hạng độc lập cho một phân khu. Tạo một đề xuất ở AI Agent để xem điểm/mức của từng căn.'
      />
    </section>
  );
}

const S = {
  crumb: { display: "flex", alignItems: "center", gap: space(2), fontSize: size.small, color: color.muted, marginBottom: space(3) },
  link: { color: color.accent, fontWeight: 600, cursor: "pointer" },
  cur: { color: color.body, fontWeight: 600 },
  sep: { color: color.borderStrong },
  head: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "5px 0 0" },
  addBtn: { background: color.accent, color: "#fff", border: "none", borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`, fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", boxShadow: "0 4px 12px rgba(91,82,230,.28)" },
  tabs: { display: "flex", gap: 2, borderBottom: `1px solid ${color.border}`, marginBottom: space(5), overflowX: "auto" },
  tab: { background: "transparent", border: "none", borderBottom: "2px solid transparent", color: color.muted, fontSize: size.small, fontWeight: 600, padding: `${space(3)}px ${space(4)}px`, cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap" },
  tabOn: { color: color.accent, borderBottomColor: color.accent },
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflow: "hidden" },
  cardHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: `${space(4)}px ${space(5)}px ${space(3)}px` },
  h2: { fontFamily: font.display, fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0 },
  csub: { fontSize: size.tiny, color: color.muted, margin: "3px 0 0" },
  count: { fontSize: size.tiny, color: color.muted },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", minWidth: 640 },
  th: { fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em", padding: `${space(2)}px ${space(4)}px`, borderTop: `1px solid ${color.border}`, borderBottom: `1px solid ${color.border}`, background: color.canvas, textAlign: "left", whiteSpace: "nowrap" },
  td: { padding: `${space(3)}px ${space(4)}px`, fontSize: size.small, color: color.body, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  badge: { fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: radius.pill },
  stateWrap: { maxWidth: 560, margin: "80px auto", textAlign: "center" },
};
