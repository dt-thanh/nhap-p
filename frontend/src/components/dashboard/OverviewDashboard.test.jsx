import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import OverviewDashboard from "./OverviewDashboard";

vi.mock("recharts", () => ({
  Area: () => <div data-testid="overview-area" />,
  AreaChart: () => <div data-testid="overview-chart" />,
  CartesianGrid: () => null,
  Line: () => <div data-testid="overview-line" />,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const BASE_PROPS = {
  project: { name: "Riverside Gardens" },
  area: null,
  summary: {
    total_units: 100,
    units_sold: 25,
    remaining_units: 75,
    velocity_7d: 2,
    velocity_30d: 1.5,
    sell_through: 25,
    last_successful_sync: "2026-08-17T08:00:00Z",
    last_attempted_sync: "2026-08-17T08:00:00Z",
    last_sync_status: "completed",
  },
  summaryLoading: false,
  summaryError: null,
  onSummaryRetry: vi.fn(),
  trend: [{ date: "2026-08-17", units_sold: 3, moving_average_30d: 1.5 }],
  trendLoading: false,
  trendError: null,
  onTrendRetry: vi.fn(),
  trendStatus: "ready",
  trendMessage: null,
  areas: [{ id: "area-1", name: "Tháp A", total_units: 60, sold: 12, absorption_rate: 20 }],
  areasLoading: false,
  areasError: null,
  onAreasRetry: vi.fn(),
  dataQuality: null,
  dataQualityLoading: false,
  dataQualityError: null,
  onDataQualityRetry: vi.fn(),
  refreshing: false,
  onRefresh: vi.fn(),
  toolbarProjects: [{ id: "P-1", name: "Riverside Gardens" }],
  toolbarAreas: [{ id: "A-1", name: "Tháp A" }],
  projectExternalId: "P-1",
  areaExternalId: null,
  range: "90d",
  onProject: vi.fn(),
  onArea: vi.fn(),
  onRange: vi.fn(),
  availableYears: [],
  selectedYear: "",
  onYear: vi.fn(),
  customFrom: "",
  customTo: "",
  onCustomFrom: vi.fn(),
  onCustomTo: vi.fn(),
  onSelectArea: vi.fn(),
  isWide: true,
};

describe("OverviewDashboard", () => {
  it("renders real KPI values, area detail, and truthful unavailable metrics", () => {
    render(<OverviewDashboard {...BASE_PROPS} />);

    expect(screen.getByText("Tỷ lệ hấp thụ")).toBeInTheDocument();
    expect(screen.getByText("Hấp thụ ròng")).toBeInTheDocument();
    expect(screen.getByText("Vận tốc đặt chỗ")).toBeInTheDocument();
    expect(screen.getAllByText("Tỷ lệ huỷ").length).toBeGreaterThan(0);
    expect(screen.getByText("25,0%")).toBeInTheDocument();
    expect(screen.getAllByText("Chưa có dữ liệu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tháp A").length).toBeGreaterThan(0);
  });

  it("switches to the forecast tab without fabricating a forecast series", () => {
    render(<OverviewDashboard {...BASE_PROPS} />);

    fireEvent.click(screen.getByRole("tab", { name: "Dự báo" }));

    expect(screen.getByText("Chưa có dữ liệu dự báo")).toBeInTheDocument();
    expect(screen.getByText(/Giai đoạn 10/)).toBeInTheDocument();
  });

  it("forwards the visual time range selection to the existing range state", () => {
    render(<OverviewDashboard {...BASE_PROPS} />);

    fireEvent.click(screen.getByRole("button", { name: "7D" }));

    expect(BASE_PROPS.onRange).toHaveBeenCalledWith("7d");
  });
});
