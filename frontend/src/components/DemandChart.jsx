import React, { memo, useMemo, useState } from "react";
import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { color } from "../styles/tokens";
import { countDemandLevels } from "../utils/rankingDemand";
import "./DemandChart.css";

export const DEMAND_LEVELS = [
  { key: "high", label: "Quan tâm cao", color: color.ok, icon: "🔥" },
  { key: "medium", label: "Quan tâm trung bình", color: color.warn, icon: "⚡" },
  { key: "low", label: "Quan tâm thấp", color: color.danger, icon: "💤" },
];

function DemandTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  return (
    <div className="demand-tooltip">
      <b>{entry.payload.name}</b>
      <span>{entry.value} căn</span>
    </div>
  );
}

export const DemandChart = memo(function DemandChart({
  units = [],
  activeCategory = null,
  onCategorySelect,
  categoryCounts,
}) {
  const [chartVisible, setChartVisible] = useState(true);
  const counts = useMemo(
    () => categoryCounts || countDemandLevels(units),
    [categoryCounts, units],
  );
  const total = DEMAND_LEVELS.reduce((sum, level) => sum + Number(counts[level.key] || 0), 0);
  const data = DEMAND_LEVELS
    .map((level) => ({
      key: level.key,
      name: level.label,
      value: Number(counts[level.key] || 0),
      color: level.color,
    }));
  const description = DEMAND_LEVELS
    .map((level) => `${counts[level.key] || 0} ${level.label.toLowerCase()}`)
    .join(", ");

  return (
    <section className="demand-chart-container" aria-label={`Phân bố mức độ quan tâm: ${description}`}>
      <div className="demand-chart-header">
        <div>
          <h3 className="chart-title">Phân bố mức độ quan tâm</h3>
          <p className="chart-subtitle">Top 20% · 60% ở giữa · 20% cuối theo điểm xếp hạng</p>
        </div>
        <button
          type="button"
          className="chart-toggle-button"
          onClick={() => setChartVisible((visible) => !visible)}
          aria-expanded={chartVisible}
        >
          {chartVisible ? "Ẩn biểu đồ" : "Hiện biểu đồ"}
        </button>
      </div>

      {total === 0 ? (
        <div className="demand-chart-empty" role="status">Chưa có căn để phân loại nhu cầu.</div>
      ) : (
        <>
          <div className="demand-chart-body">
            <div className="demand-summary-list" role="list" aria-label="Các nhóm mức độ quan tâm">
              {DEMAND_LEVELS.map((level) => (
                <div className="demand-category" key={level.key} role="listitem">
                  <button
                    type="button"
                    className={`demand-row demand-row--${level.key} ${activeCategory === level.key ? "is-active" : ""}`}
                    aria-pressed={activeCategory === level.key}
                    aria-label={`Lọc các căn được ${level.label.toLowerCase()}`}
                    onClick={() => onCategorySelect?.(activeCategory === level.key ? null : level.key)}
                    style={{
                      "--demand-color": level.color,
                      "--demand-tint": level.key === "high" ? color.okSoft : level.key === "medium" ? color.warnSoft : color.dangerSoft,
                    }}
                  >
                    <span className="demand-row__label">
                      <span className="demand-row__icon" aria-hidden="true">{level.icon}</span>
                      <span>{level.label}</span>
                    </span>
                    <span className="demand-row__meta">
                      <strong className="demand-row__count">{counts[level.key] || 0} căn</strong>
                      <span className="demand-row__active-indicator" aria-hidden="true">✓</span>
                    </span>
                  </button>
                </div>
              ))}
            </div>

            <div className="demand-donut">
              {chartVisible && (
                <div className="demand-pie" role="img" aria-label={`Biểu đồ phân bố mức độ quan tâm: ${description}`}>
                  <ResponsiveContainer width="100%" height={280}>
                    <PieChart>
                      <Pie
                        data={data}
                        cx="50%"
                        cy="50%"
                        innerRadius={58}
                        outerRadius={96}
                        paddingAngle={3}
                        dataKey="value"
                        nameKey="name"
                      >
                        {data.map((entry) => <Cell key={entry.key} fill={entry.color} />)}
                      </Pie>
                      <Tooltip content={<DemandTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
});

export default DemandChart;
