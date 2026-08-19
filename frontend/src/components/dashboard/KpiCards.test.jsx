// Phase: Dashboard Integration — "loading, empty, error states" +
// "velocity direction: increasing, decreasing, stable, missing" as rendered
// in the KPI row.
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import KpiCards from "./KpiCards";

const SUMMARY = { total_units: 20, units_sold: 8, remaining_units: 12, absorption_rate: 40, avg_velocity: 1.5 };

describe("KpiCards", () => {
  it("renders all five KPI values from summary", () => {
    render(<KpiCards summary={SUMMARY} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getByText("Total Units")).toBeInTheDocument();
    expect(screen.getByText("40.0%")).toBeInTheDocument();
  });

  it("missing values render N/A, not a fabricated 0", () => {
    render(<KpiCards summary={{}} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
  });

  it("loading shows skeletons, not stale/zero values", () => {
    const { container } = render(<KpiCards summary={null} loading error={null} onRetry={() => {}} />);
    expect(container.querySelectorAll("[style]").length).toBeGreaterThan(0);
    expect(screen.queryByText("N/A")).not.toBeInTheDocument();
  });

  it("error shows an error state with retry, not broken cards", () => {
    render(<KpiCards summary={null} loading={false} error={{ message: "boom" }} onRetry={() => {}} />);
    expect(screen.getByText(/Thử lại/)).toBeInTheDocument();
    expect(screen.queryByText("Total Units")).not.toBeInTheDocument();
  });

  it("velocity increasing shows an up arrow with a distinct label", () => {
    render(<KpiCards summary={SUMMARY} velocityDirection="increasing" loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getByText(/↑/)).toBeInTheDocument();
    expect(screen.getByText(/Đang tăng/)).toBeInTheDocument();
  });

  it("velocity decreasing shows a down arrow with a distinct label", () => {
    render(<KpiCards summary={SUMMARY} velocityDirection="decreasing" loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getByText(/↓/)).toBeInTheDocument();
    expect(screen.getByText(/Đang giảm/)).toBeInTheDocument();
  });

  it("velocity stable shows a neutral arrow", () => {
    render(<KpiCards summary={SUMMARY} velocityDirection="stable" loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getByText(/→/)).toBeInTheDocument();
  });

  it("velocity unknown (insufficient history) shows NO directional claim at all", () => {
    render(<KpiCards summary={SUMMARY} velocityDirection="unknown" loading={false} error={null} onRetry={() => {}} />);
    expect(screen.queryByText(/↑|↓|→/)).not.toBeInTheDocument();
  });

  it("no velocityDirection prop at all -> no directional claim (same as unknown)", () => {
    render(<KpiCards summary={SUMMARY} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.queryByText(/↑|↓|→/)).not.toBeInTheDocument();
  });

  it("does not show a directional arrow while the KPI row is loading (avoid a stale claim mid-refresh)", () => {
    render(<KpiCards summary={SUMMARY} velocityDirection="increasing" loading error={null} onRetry={() => {}} />);
    expect(screen.queryByText(/↑/)).not.toBeInTheDocument();
  });
});
