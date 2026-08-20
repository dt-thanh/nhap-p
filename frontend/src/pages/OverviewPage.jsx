// Top-level Overview entry point. The dashboard body remains the existing
// AbsorptionDashboard so scope, API calls, and unavailable-data behavior stay
// on one implementation path.
import React from "react";
import AbsorptionDashboard from "../components/dashboard/AbsorptionDashboard";

export default function OverviewPage() {
  return <AbsorptionDashboard standalone />;
}
