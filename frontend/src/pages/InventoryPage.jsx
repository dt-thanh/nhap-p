// frontend/src/pages/InventoryPage.jsx
// S05 — Tồn kho: dữ liệu THẬT theo từng căn, chiếu từ Mini CRM (units/deals)
// qua GET /inventory (bộ tính domain_units_deals — KHÔNG phải dashboard cũ,
// đọc units/deals trực tiếp, không qua sales_records/inventory_snapshots).
//
// Trước đây route này chạy hoàn toàn trên MarketPrototypePage (mô phỏng giá,
// KHÔNG nối API thật) — thay bằng dữ liệu thật, đúng phạm vi dự án đang chọn.
import React, { useEffect, useMemo, useState } from "react";
import { useProjectScope } from "../hooks/useProjectScope";
import { useAsync } from "../hooks/useAsync";
import { listInventoryScoped } from "../api/endpoints";
import ProjectSelector from "../components/ProjectSelector";
import { SectionState, fmt } from "../components/ui/States";
import GlobalKeyframes from "../components/ui/GlobalKeyframes";
import { color, size, radius, shadow, space, font } from "../styles/tokens";
import { areaLabel } from "../utils/areaLabel";

const STATUS_FILTERS = [
  { key: "all", label: "Tất cả" },
  { key: "available", label: "Còn trống" },
  { key: "reserved", label: "Đang giữ" },
  { key: "sold", label: "Đã bán" },
  { key: "blocked", label: "Tạm khoá" },
];

const STATUS_BADGE = {
  available: { label: "Còn trống", fg: color.ok, bg: color.okSoft },
  reserved: { label: "Đang giữ", fg: color.warn, bg: color.warnSoft },
  sold: { label: "Đã bán", fg: color.muted, bg: color.canvas },
  blocked: { label: "Tạm khoá", fg: color.danger, bg: color.dangerSoft },
};

const PAGE_SIZE = 100;

export default function InventoryPage() {
  const scope = useProjectScope();
  const [unitStatus, setUnitStatus] = useState("all");
  const [offset, setOffset] = useState(0);

  // Đổi dự án -> reset bộ lọc/trang cục bộ. `useProjectScope` đã tự reset
  // `?area=` trong URL khi đổi dự án; unitStatus/offset là state CỤC BỘ của
  // trang này nên phải tự dọn ở đây (không có ai khác dọn hộ).
  useEffect(() => {
    setUnitStatus("all");
    setOffset(0);
  }, [scope.projectExternalId]);

  useEffect(() => {
    setOffset(0);
  }, [scope.areaExternalId, unitStatus]);

  const params = useMemo(() => {
    const p = { include_units: true, limit: PAGE_SIZE, offset };
    if (scope.areaExternalId) p.external_area_id = scope.areaExternalId;
    if (unitStatus !== "all") p.unit_status = unitStatus;
    return p;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.areaExternalId, unitStatus, offset]);

  const inv = useAsync(
    () =>
      scope.projectExternalId
        ? listInventoryScoped(scope.projectExternalId, params)
        : Promise.resolve(null),
    [scope.projectExternalId, params],
  );

  const units = inv.data?.units ?? [];
  const totals = inv.data?.totals;
  const hasNextPage = units.length === PAGE_SIZE;

  return (
    <>
      <GlobalKeyframes />
      <div style={S.head}>
        <h1 style={S.h1}>Tồn kho</h1>
        <p style={S.sub}>Tồn kho từng căn theo dự án, chiếu trực tiếp từ dữ liệu vận hành (units/deals).</p>
      </div>

      <div style={S.filters}>
        <ProjectSelector
          projects={scope.projects}
          value={scope.projectExternalId}
          onChange={scope.setProjectExternalId}
          loading={scope.loadingProjects}
          status={scope.projectsStatus === "unauthorized" ? "unauthorized" : scope.projectsStatus === "error" ? "error" : undefined}
        />

        {scope.projectExternalId && (
          <label style={S.label}>
            Phân khu
            <select
              style={S.select}
              value={scope.areaExternalId ?? "all"}
              onChange={(e) => scope.setAreaExternalId(e.target.value === "all" ? null : e.target.value)}
            >
              <option value="all">Tất cả phân khu</option>
              {(scope.areas || [])
                .filter((a) => a.external_id)
                .map((a) => (
                  <option key={a.external_id} value={a.external_id}>
                    {areaLabel(a)}
                  </option>
                ))}
            </select>
          </label>
        )}

        {scope.projectExternalId && (
          <div style={S.chips}>
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setUnitStatus(f.key)}
                style={{ ...S.chip, ...(unitStatus === f.key ? S.chipOn : null) }}
              >
                {f.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {!scope.projectExternalId ? (
        <div style={S.hint}>Chọn một dự án để xem tồn kho.</div>
      ) : (
        <>
          {totals && (
            <div style={S.totals}>
              <TotalCard label="Tổng số căn" value={totals.total_units} />
              <TotalCard label="Đã bán" value={totals.units_sold} />
              <TotalCard label="Đang giữ chỗ" value={totals.units_reserved} />
              <TotalCard label="Có thể bán ngay" value={totals.units_remaining} />
              <TotalCard label="Tạm khoá" value={totals.units_blocked} />
            </div>
          )}

          <SectionState
            loading={inv.loading}
            error={inv.error}
            empty={!inv.loading && !inv.error && units.length === 0}
            onRetry={inv.reload}
          >
            <div style={S.tableCard}>
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
                      const st = STATUS_BADGE[u.status] || STATUS_BADGE.available;
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
            </div>
          </SectionState>

          {(offset > 0 || hasNextPage) && (
            <div style={S.pager}>
              <button style={S.pagerBtn} disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>
                ‹ Trang trước
              </button>
              <button style={S.pagerBtn} disabled={!hasNextPage} onClick={() => setOffset((o) => o + PAGE_SIZE)}>
                Trang sau ›
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}

function TotalCard({ label, value }) {
  return (
    <div style={S.totalCard}>
      <div style={S.totalLabel}>{label}</div>
      <div style={S.totalValue}>{fmt(value)}</div>
    </div>
  );
}

const S = {
  head: { marginBottom: space(5) },
  h1: { fontFamily: font.display, fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.02em" },
  sub: { fontSize: size.small, color: color.muted, margin: "4px 0 0" },

  filters: {
    display: "flex", alignItems: "flex-end", gap: space(4), flexWrap: "wrap",
    background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md,
    padding: `${space(4)}px ${space(5)}px`, boxShadow: shadow, marginBottom: space(5),
  },
  label: { display: "flex", flexDirection: "column", gap: 4, fontSize: size.small, fontWeight: 600, color: color.ink },
  select: {
    padding: `${space(2)}px ${space(3)}px`, borderRadius: radius.sm, border: `1px solid ${color.borderStrong}`,
    fontSize: size.small, fontFamily: "inherit", background: color.surface, minWidth: 200,
  },
  chips: { display: "flex", gap: space(2), flexWrap: "wrap" },
  chip: {
    background: color.surface, border: `1px solid ${color.borderStrong}`, color: color.body,
    fontSize: size.tiny, fontWeight: 600, padding: "6px 13px", borderRadius: radius.pill,
    cursor: "pointer", fontFamily: "inherit",
  },
  chipOn: { background: color.ink, color: "#fff", borderColor: color.ink },

  hint: { fontSize: size.small, color: color.muted, padding: `${space(10)}px 0`, textAlign: "center" },

  totals: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: space(3), marginBottom: space(5) },
  totalCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), boxShadow: shadow },
  totalLabel: { fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em" },
  totalValue: { fontSize: 24, fontWeight: 700, color: color.ink, marginTop: space(1), fontVariantNumeric: "tabular-nums" },

  tableCard: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, boxShadow: shadow, overflow: "hidden" },
  scroll: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", minWidth: 560 },
  th: {
    fontSize: size.tiny, color: color.muted, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em",
    padding: `${space(2)}px ${space(4)}px`, borderTop: `1px solid ${color.border}`, borderBottom: `1px solid ${color.border}`,
    background: color.canvas, textAlign: "left", whiteSpace: "nowrap",
  },
  td: { padding: `${space(3)}px ${space(4)}px`, fontSize: size.small, color: color.body, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
  badge: { fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: radius.pill },

  pager: { display: "flex", justifyContent: "center", gap: space(3), marginTop: space(4) },
  pagerBtn: {
    background: color.surface, border: `1px solid ${color.borderStrong}`, color: color.body,
    fontSize: size.small, fontWeight: 600, padding: `${space(2)}px ${space(4)}px`, borderRadius: radius.sm,
    cursor: "pointer", fontFamily: "inherit",
  },
};
