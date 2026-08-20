// frontend/src/components/dashboard/DashboardHeader.jsx
import React from "react";
import { color, size, radius, space, font } from "../../styles/tokens";
import Icon from "../ui/Icon";
import { DASHBOARD_TEXT, formatDashboardDateTime } from "./labels";

export default function DashboardHeader({ title = DASHBOARD_TEXT.dashboardTitle, updatedAt, onRefresh, refreshing }) {
  return (
    <header style={S.wrap}>
      <div>
        <h1 style={S.title}>{title}</h1>
        <p style={S.sub}>{DASHBOARD_TEXT.dashboardSubtitle}</p>
      </div>
      <div style={S.right}>
        <div style={S.updated}>
          <span style={S.updatedLabel}>{DASHBOARD_TEXT.updatedAt}</span>
          <span style={S.updatedVal}>{formatDashboardDateTime(updatedAt)}</span>
        </div>
        <button style={{ ...S.refresh, opacity: refreshing ? 0.6 : 1 }} onClick={onRefresh} disabled={refreshing}>
          <Icon name="refresh" size={16} style={refreshing ? S.spin : undefined} />
          <span>{refreshing ? DASHBOARD_TEXT.loading : DASHBOARD_TEXT.refresh}</span>
        </button>
      </div>
    </header>
  );
}

const S = {
  wrap: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space(4), marginBottom: space(5), flexWrap: "wrap" },
  title: { fontSize: size.h1, fontWeight: 700, color: color.ink, margin: 0, letterSpacing: "-.015em", fontFamily: font.sans },
  sub: { fontSize: size.small, color: color.muted, margin: "5px 0 0", maxWidth: "60ch" },
  right: { display: "flex", alignItems: "center", gap: space(4) },
  updated: { display: "flex", flexDirection: "column", alignItems: "flex-end", lineHeight: 1.3 },
  updatedLabel: { fontSize: size.tiny, color: color.muted },
  updatedVal: { fontSize: size.small, fontWeight: 600, color: color.body, fontVariantNumeric: "tabular-nums" },
  refresh: {
    display: "inline-flex", alignItems: "center", gap: space(2),
    background: color.surface, border: `1px solid ${color.borderStrong}`,
    color: color.body, borderRadius: radius.sm, padding: `${space(2)}px ${space(4)}px`,
    fontSize: size.small, fontWeight: 600, cursor: "pointer", fontFamily: "inherit",
  },
  spin: { animation: "aiq-spin 0.9s linear infinite" },
};
