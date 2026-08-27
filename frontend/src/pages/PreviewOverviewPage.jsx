import React from "react";
import AbsorptionDashboard from "../components/dashboard/AbsorptionDashboard";
import { color } from "../styles/tokens";

/**
 * Same-origin document loaded by the tablet device mockup. It intentionally
 * has no AppLayout or device gallery, so it cannot recursively render the
 * parent dashboard preview.
 */
export default function PreviewOverviewPage() {
  return (
    <main data-testid="overview-preview-root" style={S.root}>
      <AbsorptionDashboard standalone preview />
    </main>
  );
}

const S = {
  root: {
    width: "100%",
    height: "100vh",
    minWidth: 0,
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    overflowX: "auto",
    overflowY: "auto",
    overscrollBehavior: "contain",
    boxSizing: "border-box",
    padding: 16,
    background: color.canvas,
    color: color.body,
  },
};
