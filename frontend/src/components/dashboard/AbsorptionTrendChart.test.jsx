import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("recharts", () => ({
  ComposedChart: ({ children, data }) => <div data-testid="composed-chart" data-point-count={data?.length}>{children}</div>,
  Bar: ({ dataKey, name, yAxisId }) => <div data-testid="bar-series" data-key={dataKey} data-name={name} data-y-axis={yAxisId} />,
  Line: ({ dataKey, name, yAxisId }) => <div data-testid="line-series" data-key={dataKey} data-name={name} data-y-axis={yAxisId} />,
  XAxis: ({ dataKey }) => <div data-testid="x-axis" data-key={dataKey} />,
  YAxis: ({ tickFormatter, yAxisId }) => (
    <div data-testid={`y-axis-${yAxisId}`}>{tickFormatter ? tickFormatter(42) : ""}</div>
  ),
  CartesianGrid: () => <div data-testid="grid" />,
  Tooltip: () => <div data-testid="tooltip-slot" />,
  Legend: () => <div data-testid="legend" />,
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
}));

import AbsorptionTrendChart, { CustomTip } from "./AbsorptionTrendChart";

const POINT = {
  date: "2026-08-03",
  units_sold: 8,
  moving_average_7d: 7.4,
  moving_average_30d: 6.1,
  cumulative_sold: 43,
  sell_through: 0.21,
};

describe("AbsorptionTrendChart", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders daily units and rolling velocity lines on the units axis", () => {
    render(<AbsorptionTrendChart series={[POINT]} loading={false} error={null} />);

    expect(screen.getByTestId("bar-series")).toHaveAttribute("data-key", "units_sold");
    expect(screen.getByTestId("bar-series")).toHaveAttribute("data-name", "Số căn bán mỗi ngày");
    expect(screen.getByTestId("bar-series")).toHaveAttribute("data-y-axis", "left");
    const lines = screen.getAllByTestId("line-series");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toHaveAttribute("data-key", "moving_average_7d");
    expect(lines[0]).toHaveAttribute("data-name", "Trung bình trượt 7 ngày");
    expect(lines[1]).toHaveAttribute("data-key", "moving_average_30d");
    expect(lines[1]).toHaveAttribute("data-name", "Trung bình trượt 30 ngày");
    expect(lines[0]).toHaveAttribute("data-y-axis", "left");
    expect(lines[1]).toHaveAttribute("data-y-axis", "left");
    expect(screen.getByTestId("y-axis-left")).toHaveTextContent("42");
    expect(screen.queryByTestId("y-axis-right")).not.toBeInTheDocument();
  });

  it("shows all tooltip values with safe locale formatting", () => {
    render(
      <CustomTip
        active
        label={POINT.date}
        payload={[{ payload: POINT }]}
      />,
    );

    expect(screen.getByText("Ngày")).toBeInTheDocument();
    expect(screen.getByText("03/08/2026")).toBeInTheDocument();
    expect(screen.getByText("Đã bán")).toBeInTheDocument();
    expect(screen.getByText("8 căn")).toBeInTheDocument();
    expect(screen.getByText("Đã bán cộng dồn")).toBeInTheDocument();
    expect(screen.getByText("43 căn")).toBeInTheDocument();
    expect(screen.getByText("7,4 căn/ngày")).toBeInTheDocument();
    expect(screen.getByText("6,1 căn/ngày")).toBeInTheDocument();
    expect(screen.getByText(/0[,.]21%/)).toBeInTheDocument();
  });

  it("preserves loading, empty, and API error states", () => {
    const onRetry = vi.fn();
    const { rerender } = render(<AbsorptionTrendChart series={undefined} loading error={null} onRetry={onRetry} />);
    expect(screen.getByText("Xu hướng tiêu thụ")).toBeInTheDocument();
    expect(screen.queryByTestId("composed-chart")).not.toBeInTheDocument();

    rerender(<AbsorptionTrendChart series={[]} loading={false} error={null} onRetry={onRetry} />);
    expect(screen.getByText("Chưa có dữ liệu")).toBeInTheDocument();

    const error = new Error("trend unavailable");
    rerender(<AbsorptionTrendChart series={undefined} loading={false} error={error} onRetry={onRetry} />);
    expect(screen.getByText("Có lỗi xảy ra")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
