// frontend/src/pages/AreaDetailPage.jsx
// S04 — Chi tiết phân khu. KPI + tabs.
// Tab "Xếp hạng khả năng bán" là BÀI TOÁN LÕI: liệt kê từng căn kèm điểm khả năng bán.
// Các tab Dự báo/Cảnh báo để mờ (giai đoạn sau).
import React, { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getUnitRanking } from "../api/endpoints";
import { useAsync } from "../hooks/useAsync";
import { color, size, radius, shadow, space, font } from "../styles/tokens";
import { SectionState, fmt, Skeleton } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";

const BAND = {
  high:   { label: "Cao", fg: color.ok, bg: color.okSoft },
  medium: { label: "Trung bình", fg: color.warn, bg: color.warnSoft },
  low:    { label: "Thấp", fg: color.danger, bg: color.dangerSoft },
};

const TABS = [
  { key: "ranking", label: "Xếp hạng khả năng bán" },
  { key: "inventory", label: "Tồn kho" },
  { key: "forecast", label: "Dự báo", soon: true },
  { key: "alerts", label: "Cảnh báo", soon: true },
];

export default function AreaDetailPage() {
  const { id: projectId, areaId } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("ranking");

  return (
    <>
      <GlobalKeyframes />
      <div style={S.crumb}>
        <span style={S.link} onClick={() => navigate("/projects")}>Dự án</span><span style={S.sep}>/</span>
        <span style={S.link} onClick={() => navigate(`/projects/${projectId}`)}>Chi tiết</span><span style={S.sep}>/</span>
        <span style={S.cur}>Phân khu</span>
      </div>

      <header style={S.head}>
        <div>
          <h1 style={S.h1}>Phân khu {areaId}</h1>
          <p style={S.sub}>Xếp hạng khả năng bán từng căn &amp; theo dõi tồn kho</p>
        </div>
        <button style={S.addBtn} onClick={() => navigate("/import")}>Nạp dữ liệu</button>
      </header>

      <div style={S.tabs}>
        {TABS.map((t) => (
          <button key={t.key}
            onClick={() => !t.soon && setTab(t.key)}
            style={{ ...S.tab, ...(tab === t.key ? S.tabOn : null), ...(t.soon ? S.tabSoon : null) }}
            disabled={t.soon}>
            {t.label}{t.soon ? " · Giai đoạn 2" : ""}
          </button>
        ))}
      </div>

      {tab === "ranking" && <RankingTab areaId={areaId} />}
      {tab === "inventory" && <InventoryTab />}
    </>
  );
}

function RankingTab({ areaId }) {
  const { data, loading, error, reload } = useAsync(() => getUnitRanking(areaId), [areaId]);
  const empty = !loading && !error && (!data || data.length === 0);

  return (
    <section style={S.card}>
      <div style={S.cardHead}>
        <div>
          <h2 style={S.h2}>Xếp hạng khả năng bán</h2>
          <p style={S.csub}>Điểm càng cao, căn càng dễ bán. Ưu tiên tư vấn từ trên xuống.</p>
        </div>
        {data && <span style={S.count}>{data.length} căn</span>}
      </div>

      <SectionState loading={loading} error={error} empty={empty} onRetry={reload} compact>
        <div style={S.scroll}>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>#</th>
                <th style={S.th}>Mã căn</th>
                <th style={S.th}>Loại</th>
                <th style={{ ...S.th, textAlign: "right" }}>Diện tích</th>
                <th style={{ ...S.th, width: 200 }}>Khả năng bán</th>
                <th style={S.th}>Mức</th>
              </tr>
            </thead>
            <tbody>
              {(data || []).map((u, i) => {
                const b = BAND[u.band] || BAND.medium;
                return (
                  <tr key={u.unit_code}>
                    <td style={{ ...S.td, color: color.muted, fontFamily: font.mono }}>{i + 1}</td>
                    <td style={{ ...S.td, fontWeight: 600, color: color.ink }}>{u.unit_code}</td>
                    <td style={S.td}>{u.unit_type}</td>
                    <td style={{ ...S.td, textAlign: "right", fontFamily: font.mono }}>{fmt(u.area_sqm, { suffix: " m²" })}</td>
                    <td style={S.td}>
                      <div style={S.scoreRow}>
                        <div style={S.track}><span style={{ ...S.fill, width: `${u.score}%`, background: b.fg }} /></div>
                        <span style={{ ...S.scoreVal, color: b.fg }}>{fmt(u.score, { suffix: "%" })}</span>
                      </div>
                    </td>
                    <td style={S.td}><span style={{ ...S.badge, color: b.fg, background: b.bg }}>{b.label}</span></td>
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

function InventoryTab() {
  return (
    <section style={S.card}>
      <h2 style={S.h2}>Tồn kho</h2>
      <p style={S.csub}>Danh sách căn còn/đã bán theo thời gian (đang hoàn thiện).</p>
      <div style={{ padding: space(10), textAlign: "center", color: color.muted, fontSize: size.small }}>
        Bảng tồn kho chi tiết sẽ hiển thị ở đây.
      </div>
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
  tabSoon: { color: color.borderStrong, cursor: "not-allowed" },
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflow: "hidden" },
  cardHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: `${space(4)}px ${space(5)}px ${space(3)}px` },
  h2: { fontFamily: font.display, fontSize: size.h2, fontWeight: 700, color: color.ink, margin: 0 },
  csub: { fontSize: size.tiny, color: color.muted, margin: "3px 0 0" },
  count: { fontSize: size.tiny, color: color.muted },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", minWidth: 640 },
  th: { fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em", padding: `${space(2)}px ${space(4)}px`, borderTop: `1px solid ${color.border}`, borderBottom: `1px solid ${color.border}`, background: color.canvas, textAlign: "left", whiteSpace: "nowrap" },
  td: { padding: `${space(3)}px ${space(4)}px`, fontSize: size.small, color: color.body, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  scoreRow: { display: "flex", alignItems: "center", gap: space(3) },
  track: { flex: 1, height: 8, background: color.canvas, borderRadius: radius.pill, overflow: "hidden", minWidth: 90 },
  fill: { display: "block", height: "100%", borderRadius: radius.pill },
  scoreVal: { fontSize: size.small, fontWeight: 700, minWidth: 44, textAlign: "right", fontVariantNumeric: "tabular-nums" },
  badge: { fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: radius.pill },
};
