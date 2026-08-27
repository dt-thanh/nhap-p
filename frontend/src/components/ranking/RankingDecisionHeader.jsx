import React from "react";
import { color } from "../../styles/tokens";

const BAND_META = {
  high: { label: "Hot", tone: color.ok, soft: color.okSoft, threshold: "Điểm ≥ 66%" },
  medium: { label: "Normal", tone: color.warn, soft: color.warnSoft, threshold: "Điểm 33–65,9%" },
  low: { label: "Slow", tone: color.muted, soft: color.canvas, threshold: "Điểm < 33%" },
  at_risk: { label: "At risk", tone: color.danger, soft: color.dangerSoft, threshold: "Thiếu dữ liệu / chưa chấm" },
};

export function RankingDecisionHeader({ summary, trend, quality, ranking, loading }) {
  const warningCount = (quality?.warnings?.length || 0) + (ranking?.units_skipped || 0);
  const trendDelta = calculateTrendDelta(trend?.points);
  const mos = summary?.estimated_weeks_to_sell_out == null
    ? null
    : Number(summary.estimated_weeks_to_sell_out) / 4.345;

  return (
    <section className="ranking-decision-header" aria-label="Các chỉ số quyết định">
      <div className="ranking-kpi-strip">
        <Kpi label="Tốc độ hấp thụ" value={formatNumber(summary?.velocity_30d, 1)} suffix=" căn/tuần" hint="Vận tốc 30 ngày do backend trả về." loading={loading} />
        <Kpi label="MOS" value={formatNumber(mos, 1)} suffix=" tháng" hint="Months of supply = số tuần dự kiến bán hết / 4,345." loading={loading} />
        <Kpi label="Đã bán" value={formatNumber(summary?.sell_through, 1)} suffix="%" hint="Sell-through trong scope đang chọn." loading={loading} />
        <Kpi label="Xu hướng 3–6 tháng" value={trendDelta == null ? "—" : `${trendDelta > 0 ? "+" : ""}${trendDelta.toFixed(1)}%`} suffix={trendDelta == null ? "" : " vs kỳ trước"} tone={trendDelta == null ? color.muted : trendDelta >= 0 ? color.ok : color.danger} hint="So sánh vận tốc trung bình 3 kỳ gần nhất với 3 kỳ trước." loading={loading} />
        <Kpi label="Cảnh báo sớm" value={warningCount || "0"} suffix={warningCount === 1 ? " tín hiệu" : " tín hiệu"} tone={warningCount ? color.danger : color.ok} hint="Bất thường dữ liệu hoặc căn chưa đủ độ phủ trọng số; không tự động kết luận rủi ro bán." loading={loading} />
      </div>
      <RiskBandLegend ranking={ranking} />
    </section>
  );
}

export function RiskBandLegend({ ranking }) {
  const counts = ranking?.band_counts || {};
  return (
    <div className="ranking-risk-legend" aria-label="Giải thích các dải rủi ro">
      <div className="ranking-risk-legend__title">Risk bands <span title="Ngưỡng do backend ranking engine quyết định">ⓘ</span></div>
      {Object.entries(BAND_META).map(([key, meta]) => (
        <span className="ranking-risk-band" key={key} style={{ "--band-color": meta.tone, "--band-soft": meta.soft }} title={meta.threshold}>
          <i aria-hidden="true" />
          <b>{meta.label}</b>
          <small>{key === "at_risk" ? (ranking?.units_skipped || 0) : (counts[key] || 0)} căn</small>
        </span>
      ))}
      <span className="ranking-risk-legend__note">Hot/Normal/Slow là nhãn hiển thị của high/medium/low từ API.</span>
    </div>
  );
}

export function DataReliability({ summary, quality, ranking, trend }) {
  const qualityTone = quality?.status === "error" ? color.danger : quality?.status === "ok_with_warnings" ? color.warn : color.ok;
  return (
    <details className="ranking-reliability" open>
      <summary>
        <span><b>Độ tin cậy dữ liệu</b><small> nguồn, thời điểm cập nhật và phạm vi bất định</small></span>
        <strong style={{ color: qualityTone }}>{quality?.status === "error" ? "Cần xử lý" : quality?.status === "ok_with_warnings" ? "Có cảnh báo" : "Theo dõi được"}</strong>
      </summary>
      <div className="ranking-reliability__grid">
        <ReliabilityItem label="Nguồn dữ liệu" value={summary?.data_source || quality?.source || "—"} />
        <ReliabilityItem label="Refresh thành công" value={formatDate(summary?.last_successful_sync)} />
        <ReliabilityItem label="Tính điểm gần nhất" value={formatDate(ranking?.computed_at || summary?.updated_at)} />
        <ReliabilityItem label="Sell-out date (ước tính)" value={formatDate(estimatedSellOutDate(summary?.estimated_weeks_to_sell_out))} />
        <ReliabilityItem label="Độ phủ trọng số" value={ranking?.units_skipped ? `${ranking.units_skipped} căn chưa đủ dữ liệu` : "Không có căn bị bỏ qua"} />
        <ReliabilityItem label="Forecast uncertainty" value="Chưa có trường dữ liệu trong API" />
        <ReliabilityItem label="Recent accuracy" value={trend?.points?.length ? "Chưa có backtest trong API" : "Chưa có dữ liệu"} />
      </div>
      {quality?.warnings?.length ? <ul className="ranking-reliability__warnings">{quality.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
      <p className="ranking-reliability__disclaimer">{ranking?.disclaimer || "Xếp hạng là đầu vào quyết định, không phải cam kết kết quả bán hàng."}</p>
    </details>
  );
}

function Kpi({ label, value, suffix, hint, tone, loading }) {
  return (
    <article className="ranking-kpi" title={hint}>
      <span>{label} <i aria-hidden="true">ⓘ</i></span>
      {loading ? <strong className="ranking-kpi__loading">—</strong> : <strong style={{ color: tone || color.ink }}>{value}<small>{suffix}</small></strong>}
    </article>
  );
}

function ReliabilityItem({ label, value }) {
  return <div><span>{label}</span><b title={value}>{value}</b></div>;
}

function formatNumber(value, digits = 0) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("vi-VN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function formatDate(value) {
  if (!value) return "Không rõ";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Không rõ" : date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

function estimatedSellOutDate(weeks) {
  if (weeks == null || !Number.isFinite(Number(weeks))) return null;
  const date = new Date();
  date.setDate(date.getDate() + (Number(weeks) * 7));
  return date;
}

function calculateTrendDelta(points = []) {
  const velocities = points.map((point) => Number(point.moving_average_30d ?? point.units_sold)).filter(Number.isFinite);
  if (velocities.length < 6) return null;
  const recent = average(velocities.slice(-3));
  const previous = average(velocities.slice(-6, -3));
  if (!previous) return null;
  return ((recent - previous) / previous) * 100;
}

function average(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

export const rankingBandMeta = BAND_META;
