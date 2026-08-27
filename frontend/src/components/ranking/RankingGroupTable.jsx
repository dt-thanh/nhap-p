import React, { useMemo } from "react";
import { color } from "../../styles/tokens";

const BAND_LABEL = { high: "Hot", medium: "Normal", low: "Slow" };
const ACTION = {
  high: { label: "Marketing + gọi bán", tone: color.ok, detail: "Tập trung lead và lịch xem" },
  medium: { label: "Marketing", tone: color.warn, detail: "Giữ nhịp và theo dõi chuyển đổi" },
  low: { label: "Giá / hold", tone: color.danger, detail: "Rà soát giá, ưu đãi hoặc giữ nhịp" },
};

export default function RankingGroupTable({ items = [], selectedKey, onSelect }) {
  const groups = useMemo(() => buildGroups(items), [items]);
  return (
    <section className="ranking-group-panel" aria-labelledby="ranking-group-title">
      <div className="ranking-group-panel__head">
        <div><span className="ranking-eyebrow">ECONOMIC PRIORITY</span><h2 id="ranking-group-title">Nhóm cần quyết định</h2><p>Gộp theo phân khu và loại căn từ dữ liệu ranking hiện tại. Mở nhóm để xem từng căn.</p></div>
        <span className="ranking-group-panel__meta">{groups.length} nhóm · {items.length} căn trong trang</span>
      </div>
      <div className="ranking-group-table-wrap">
        <table className="ranking-group-table">
          <thead><tr><th>Nhóm</th><th>Quy mô</th><th>Risk band</th><th>Drivers</th><th>Gợi ý hành động</th><th>Tác động đo được</th></tr></thead>
          <tbody>
            {groups.length ? groups.map((group) => {
              const action = ACTION[group.dominantBand] || ACTION.medium;
              const selected = selectedKey === group.key;
              return (
                <tr key={group.key} className={selected ? "is-selected" : undefined}>
                  <td><button type="button" className="ranking-group-link" onClick={() => onSelect?.(selected ? null : group.key)}><b>{group.areaName}</b><small>{group.unitType || "Loại căn chưa rõ"}</small></button></td>
                  <td><b>{group.count}</b><small>{group.averageScore == null ? "—" : `Điểm TB ${group.averageScore.toFixed(1)}%`}</small></td>
                  <td><span className="ranking-group-band" style={{ "--band-color": group.bandTone, "--band-soft": group.bandSoft }}>{BAND_LABEL[group.dominantBand] || "At risk"}</span><small>{group.bandCount} căn</small></td>
                  <td><div className="ranking-driver-list">{group.drivers.slice(0, 3).map((driver) => <span key={driver.key} title={`${driver.label}: ${driver.value}`}><b>{driver.label}</b> {driver.value}</span>)}</div></td>
                  <td><b style={{ color: action.tone }}>{action.label}</b><small>{action.detail}</small></td>
                  <td><span className="ranking-impact">{group.count} căn trong phạm vi</span><small>Chưa có forecast impact trong API</small></td>
                </tr>
              );
            }) : <tr><td colSpan="6" className="ranking-group-table__empty">Chưa có nhóm để quyết định.</td></tr>}
          </tbody>
        </table>
      </div>
      {selectedKey ? <button type="button" className="ranking-clear-group" onClick={() => onSelect?.(null)}>Xoá drill-down nhóm</button> : <p className="ranking-group-panel__note">Drivers lấy từ 3 đóng góp lớn nhất của ranking engine; action là gợi ý vận hành, không phải quyết định giá tự động.</p>}
    </section>
  );
}

function buildGroups(items) {
  const map = new Map();
  items.forEach((item) => {
    const key = `${item.area_name || "Phân khu chưa rõ"}::${item.unit_type || ""}`;
    const group = map.get(key) || { key, areaName: item.area_name || "Phân khu chưa rõ", unitType: item.unit_type, items: [] };
    group.items.push(item);
    map.set(key, group);
  });
  return [...map.values()].map((group) => {
    const bands = { high: 0, medium: 0, low: 0 };
    group.items.forEach((item) => { if (bands[item.band] !== undefined) bands[item.band] += 1; });
    const dominantBand = Object.entries(bands).sort((a, b) => b[1] - a[1])[0]?.[0] || "low";
    const averageScore = average(group.items.map((item) => Number(item.score_percent)).filter(Number.isFinite));
    const drivers = aggregateDrivers(group.items);
    const bandStyle = bandColors(dominantBand);
    return { ...group, count: group.items.length, bands, dominantBand, bandCount: bands[dominantBand], averageScore, drivers, ...bandStyle };
  });
}

function aggregateDrivers(items) {
  const byKey = new Map();
  items.forEach((item) => (item.contributions || []).forEach((contribution) => {
    const current = byKey.get(contribution.feature_key) || { key: contribution.feature_key, total: 0, count: 0 };
    current.total += Number(contribution.contribution) || 0;
    current.count += 1;
    byKey.set(contribution.feature_key, current);
  }));
  return [...byKey.values()].sort((a, b) => Math.abs(b.total) - Math.abs(a.total)).map((driver) => ({
    ...driver,
    label: labelFeature(driver.key),
    value: formatContribution(driver.total / driver.count),
  }));
}

function labelFeature(key) {
  return {
    unit_available: "Còn trống",
    unit_demand_norm: "Nhu cầu",
    has_active_deal: "Deal giữ",
    area_velocity_norm: "Tốc độ",
    area_conversion_norm: "Chốt",
  }[key] || key;
}

function formatContribution(value) {
  if (!Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function average(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function bandColors(band) {
  if (band === "high") return { bandTone: color.ok, bandSoft: color.okSoft };
  if (band === "medium") return { bandTone: color.warn, bandSoft: color.warnSoft };
  return { bandTone: color.muted, bandSoft: color.canvas };
}
