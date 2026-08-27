import React from "react";
import { color, font, radius, shadow, size, space } from "../styles/tokens";

export default function ProjectCard({ project, summary, loading, onOpen }) {
  const sellThrough = summary?.sell_through == null ? null : Number(summary.sell_through);
  const total = summary?.total_units == null ? null : Number(summary.total_units);
  const sold = summary?.units_sold == null ? null : Number(summary.units_sold);
  const percent = Number.isFinite(sellThrough)
    ? Math.max(0, Math.min(100, sellThrough))
    : total > 0 && Number.isFinite(sold) ? Math.max(0, Math.min(100, (sold / total) * 100)) : null;

  return (
    <article style={S.card}>
      <div style={S.topline}>
        <div>
          <p style={S.eyebrow}>{project.status || "—"}</p>
          <h2 style={S.title}>{project.name}</h2>
        </div>
        <span style={S.date}>{project.launch_date || "—"}</span>
      </div>
      {loading ? (
        <div style={S.loading}>Đang tải dữ liệu hấp thụ…</div>
      ) : (
        <>
          <div style={S.metrics}>
            <Metric label="Sell-through" value={percent == null ? "—" : `${percent.toFixed(1)}%`} />
            <Metric label="Đã bán" value={sold == null ? "—" : sold.toLocaleString("vi-VN")} />
            <Metric label="Còn lại" value={summary?.units_remaining == null ? "—" : Number(summary.units_remaining).toLocaleString("vi-VN")} />
          </div>
          <div aria-label="Tiến độ sell-through" style={S.progressTrack}>
            <span style={{ ...S.progress, width: `${percent ?? 0}%` }} />
          </div>
          <p style={S.caption}>
            Vận tốc 30 ngày: {summary?.velocity_30d == null ? "—" : `${Number(summary.velocity_30d).toLocaleString("vi-VN")} căn/ngày`}
          </p>
        </>
      )}
      <button type="button" style={S.button} onClick={onOpen} disabled={!project.external_id}>
        Xem xếp hạng căn →
      </button>
    </article>
  );
}

function Metric({ label, value }) {
  return <div><span style={S.metricLabel}>{label}</span><strong style={S.metricValue}>{value}</strong></div>;
}

const S = {
  card: { background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(4), boxShadow: shadow, display: "grid", gap: space(3) },
  topline: { display: "flex", justifyContent: "space-between", gap: space(3), alignItems: "flex-start" },
  eyebrow: { margin: 0, color: color.muted, fontSize: size.tiny, textTransform: "uppercase", letterSpacing: ".08em" },
  title: { margin: "4px 0 0", color: color.ink, fontFamily: font.display, fontSize: size.h2 },
  date: { color: color.muted, fontSize: size.tiny, whiteSpace: "nowrap" },
  metrics: { display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: space(2) },
  metricLabel: { display: "block", color: color.muted, fontSize: size.tiny },
  metricValue: { display: "block", marginTop: 3, color: color.ink, fontFamily: font.display, fontSize: 19 },
  progressTrack: { height: 8, overflow: "hidden", background: color.canvas, borderRadius: radius.pill },
  progress: { display: "block", height: "100%", background: color.accent, borderRadius: radius.pill, transition: "width 200ms ease" },
  caption: { margin: 0, color: color.muted, fontSize: size.tiny },
  loading: { minHeight: 66, display: "grid", placeItems: "center", color: color.muted, fontSize: size.small },
  button: { justifySelf: "start", border: 0, background: "transparent", color: color.accentHover, fontWeight: 700, cursor: "pointer", padding: 0, fontFamily: "inherit" },
};
