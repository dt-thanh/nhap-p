// Phase: Dashboard Integration — "loading, empty, error states" +
// "velocity direction: increasing, decreasing, stable, missing" as rendered
// in the KPI row.
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import KpiCards from "./KpiCards";

const SUMMARY = {
  total_units: 20,
  units_sold: 8,
  remaining_units: 12,
  available_remaining_units: 9,
  reserved_units: 3,
  sell_through: 40,
  velocity_7d: 10.5,
  velocity_30d: 9.8,
  estimated_weeks_to_sell_out: 1.2,
};

describe("KpiCards", () => {
  it("renders explicit total, immediately available, and reserved inventory metrics", () => {
    render(<KpiCards summary={SUMMARY} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getByText("Đã bán")).toBeInTheDocument();
    expect(screen.getByText("Còn lại tổng cộng")).toBeInTheDocument();
    expect(screen.getByText("Có thể bán ngay")).toBeInTheDocument();
    expect(screen.getByText("Đang giữ chỗ")).toBeInTheDocument();
    expect(screen.getByText("9 căn")).toBeInTheDocument();
    expect(screen.getByText("3 căn")).toBeInTheDocument();
    expect(screen.getByText("Tốc độ bán 7 ngày")).toBeInTheDocument();
    expect(screen.getByText("Tốc độ bán 30 ngày")).toBeInTheDocument();
    expect(screen.getByText("Ước tính số tuần bán hết")).toBeInTheDocument();
    expect(screen.getByText("40,0%")).toBeInTheDocument();
  });

  it("missing values render Vietnamese unavailable text, not a fabricated 0", () => {
    render(<KpiCards summary={{}} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getAllByText("Chưa có dữ liệu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Chưa đủ dữ liệu").length).toBe(3);
  });

  it("loading shows skeletons, not stale/zero values", () => {
    const { container } = render(<KpiCards summary={null} loading error={null} onRetry={() => {}} />);
    expect(container.querySelectorAll("[style]").length).toBeGreaterThan(0);
    expect(screen.queryByText("Chưa có dữ liệu")).not.toBeInTheDocument();
  });

  it("error shows an error state with retry, not broken cards", () => {
    render(<KpiCards summary={null} loading={false} error={{ message: "boom" }} onRetry={() => {}} />);
    expect(screen.getByText(/Thử lại/)).toBeInTheDocument();
    expect(screen.queryByText("Đã bán")).not.toBeInTheDocument();
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
