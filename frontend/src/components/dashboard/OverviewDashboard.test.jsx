import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import OverviewDashboard, { buildPreviewHref } from "./OverviewDashboard";

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
  market: { project: { live_units: 1 }, metrics: { active_total: 1 } },
  marketLoading: false,
  marketError: null,
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
    render(<OverviewDashboard {...BASE_PROPS} preview />);

    expect(screen.getByText("Tỷ lệ hấp thụ")).toBeInTheDocument();
    expect(screen.getByText("Hấp thụ ròng")).toBeInTheDocument();
    expect(screen.getByText("Vận tốc đặt chỗ")).toBeInTheDocument();
    expect(screen.getByText("Căn đang sống")).toBeInTheDocument();
    expect(screen.getByText("Tổng căn hoạt động")).toBeInTheDocument();
    expect(screen.getAllByText("1 căn").length).toBe(2);
    expect(screen.getAllByText("Tỷ lệ huỷ").length).toBeGreaterThan(0);
    expect(screen.getByText("25,0%")).toBeInTheDocument();
    expect(screen.getAllByText("Chưa có dữ liệu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tháp A").length).toBeGreaterThan(0);
  });

  it("switches to the forecast tab without fabricating a forecast series", () => {
    render(<OverviewDashboard {...BASE_PROPS} preview />);

    fireEvent.click(screen.getByRole("tab", { name: "Dự báo" }));

    expect(screen.getByText("Chưa có dữ liệu dự báo")).toBeInTheDocument();
    expect(screen.getByText(/Giai đoạn 10/)).toBeInTheDocument();
  });

  it("forwards the visual time range selection to the existing range state", () => {
    render(<OverviewDashboard {...BASE_PROPS} preview />);

    fireEvent.click(screen.getByRole("button", { name: "7D" }));

    expect(BASE_PROPS.onRange).toHaveBeenCalledWith("7d");
  });

  it("renders a rigid tablet iframe mockup with the current scope", () => {
    const { unmount } = render(<OverviewDashboard {...BASE_PROPS} isWide={false} />);

    const phone = screen.getByRole("complementary", { name: "Nhịp độ bán hàng" });
    const tabletCell = screen.getByRole("region", { name: "Không gian phân tích tổng quan" });
    const tablet = screen.getByTestId("overview-tablet-mockup");

    expect(phone.style.height).toBe("420px");
    expect(phone.style.overflow).toBe("hidden");
    expect(tabletCell.style.minWidth).toBe("0px");
    expect(tabletCell.style.alignItems).toBe("flex-end");
    expect(tablet).toHaveAttribute("type", "tablet");
    expect(tablet).toHaveAttribute("mode", "iframe");
    expect(tablet).toHaveAttribute("href", "/preview/overview?project=P-1");
    expect(tablet).toHaveAttribute("alt", "AbsorptionIQ tablet dashboard preview");
    expect(tablet).toHaveAttribute("screen-background", "white");
    expect(tablet).toHaveAttribute("width");
    expect(tablet).not.toHaveAttribute("height");
    expect(tablet.style.width).toMatch(/px$/);
    expect(tablet.style.height).toMatch(/px$/);
    expect(tablet.style.overflow).toBe("hidden");
    expect(tablet.style.flexShrink).toBe("0");
    expect(tablet.style.position).toBe("relative");
    expect(tablet.style.boxSizing).toBe("border-box");
    expect(phone.style.flexShrink).toBe("0");

    unmount();
    render(<OverviewDashboard {...BASE_PROPS} isWide />);

    const widePhone = screen.getByRole("complementary", { name: "Nhịp độ bán hàng" });
    const wideTablet = screen.getByTestId("overview-tablet-mockup");
    const previewRow = widePhone.parentElement;

    expect(previewRow.style.gridTemplateRows).toBe("minmax(0, 1fr)");
    expect(previewRow.style.minHeight).toBe("0px");
    expect(previewRow.style.alignItems).toBe("end");
    expect(widePhone.style.alignSelf).toBe("end");
    expect(wideTablet).toHaveAttribute("type", "tablet");
    expect(wideTablet).toHaveAttribute("mode", "iframe");
    expect(wideTablet).toHaveAttribute("width");
    expect(wideTablet).not.toHaveAttribute("height");
    expect(wideTablet.style.height).toMatch(/px$/);
    expect(wideTablet.style.overflow).toBe("hidden");
  });

  it("updates the same-origin preview URL when project or area scope changes", () => {
    const { rerender } = render(<OverviewDashboard {...BASE_PROPS} areaExternalId={null} />);
    const tablet = screen.getByTestId("overview-tablet-mockup");

    expect(tablet).toHaveAttribute("href", "/preview/overview?project=P-1");
    rerender(<OverviewDashboard {...BASE_PROPS} areaExternalId="A/1" />);
    expect(tablet).toHaveAttribute("href", "/preview/overview?project=P-1&area=A%2F1");
    expect(buildPreviewHref("P 1", "A&1")).toBe("/preview/overview?project=P+1&area=A%261");
  });

  it("renders the real workspace directly in preview mode without another device frame", () => {
    render(<OverviewDashboard {...BASE_PROPS} preview />);

    expect(screen.getByRole("heading", { name: "Riverside Gardens · Tổng quan" })).toBeInTheDocument();
    expect(screen.queryByTestId("overview-tablet-mockup")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Nhịp độ bán hàng" })).not.toBeInTheDocument();
  });
});
