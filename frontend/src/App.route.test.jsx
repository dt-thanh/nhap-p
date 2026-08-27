import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet } from "react-router-dom";
import { AppRoutes } from "./App";
import { setAccessToken } from "./api/client";

vi.mock("./hooks/useBreakpoint", () => ({
  useBreakpoint: vi.fn(),
  pick: (bp, values) => values[bp] ?? values.desktop ?? Object.values(values)[0],
}));

vi.mock("./components/AppLayout", () => ({
  default: function TestLayout() {
    return <div data-testid="app-layout"><Outlet /></div>;
  },
}));

vi.mock("./components/dashboard/AbsorptionTrendChart", () => ({
  default: function TestTrendChart() {
    return <div data-testid="trend-chart">Absorption Trend</div>;
  },
}));

// `AbsorptionDashboard` giờ chỉ còn là thân của dashboard MỘT dự án. Nếu
// /overview lại render nó, marker dưới đây sẽ lộ ra ở đúng route đó — đây là
// bài kiểm chống lại chính lỗi "hai trang trông y hệt nhau".
vi.mock("./components/dashboard/AbsorptionDashboard", () => ({
  default: function TestProjectDashboard() {
    return <div data-testid="project-dashboard">Project dashboard</div>;
  },
}));

vi.mock("./pages/PreviewOverviewPage", () => ({
  default: () => <div data-testid="overview-preview-route">Overview preview route</div>,
}));

vi.mock("./api/endpoints", () => ({
  getProjectByExternalId: vi.fn(),
  listAreasScoped: vi.fn(),
  getAreaByExternalId: vi.fn(),
  getDashboardTrend: vi.fn(),
  listInventoryScoped: vi.fn(),
  listDealsScoped: vi.fn(),
  listProjects: vi.fn(),
  getPortfolioSummary: vi.fn(),
  getRanking: vi.fn(),
  getAbsorptionSummary: vi.fn(),
}));

import {
  getAreaByExternalId,
  getDashboardTrend,
  getPortfolioSummary,
  getProjectByExternalId,
  listAreasScoped,
  listDealsScoped,
  listInventoryScoped,
  listProjects,
} from "./api/endpoints";
import { useBreakpoint } from "./hooks/useBreakpoint";

const PROJECT = {
  project_id: "project-uuid",
  external_id: "project-a",
  name: "Ocean Park 1",
  launch_date: "2026-01-01",
  status: "active",
};

const AREAS = [
  { area_id: "area-uuid-a", external_id: "area-a", area_name: "Sapphire 1", unit_type: "2PN", total_units: 100, units_remaining: 40 },
  { area_id: "area-uuid-b", external_id: "area-b", area_name: "The ZenPark", unit_type: "1PN", total_units: 80, units_remaining: 30 },
];

const AREA = {
  ...AREAS[0],
  project_id: "project-uuid",
  bedrooms: 2,
  area_sqm: 65,
  status: "active",
};

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

function resolveProject() {
  getProjectByExternalId.mockResolvedValue(PROJECT);
  listAreasScoped.mockResolvedValue(AREAS);
}

describe("project and area route composition", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // ProtectedRoute (bọc AppLayout trong App.jsx) chặn khi không có phiên.
    // Các test này kiểm COMPOSITION route, không kiểm auth — cấp token tĩnh để
    // qua cửa guard (cùng cách ProtectedRoute.test.jsx dùng setAccessToken).
    setAccessToken("route-composition-test-token");
    useBreakpoint.mockReturnValue({ bp: "laptop", isNarrow: false, isMobile: false, isTablet: false, isDesktop: false });
    resolveProject();
    getAreaByExternalId.mockImplementation((externalId) => Promise.resolve(
      externalId === "area-b" ? { ...AREAS[1], area_id: "area-uuid-b", project_id: "project-uuid", status: "active" } : AREA,
    ));
    getDashboardTrend.mockResolvedValue({ points: [] });
    listInventoryScoped.mockResolvedValue({ totals: { total_units: 2 }, units: [] });
    listDealsScoped.mockResolvedValue({ items: [] });
    listProjects.mockResolvedValue([]);
    getPortfolioSummary.mockResolvedValue({
      project_count: 0, area_count: 0, unit_count: 0,
      deal_count: 0, booking_count: 0, selling_project_count: 0,
    });
  });

  it("renders the project detail area list without an inline dashboard", async () => {
    renderAt("/projects/project-a");

    expect(await screen.findByRole("heading", { name: "Ocean Park 1" })).toBeInTheDocument();
    expect(await screen.findAllByTestId("area-card")).toHaveLength(2);
    expect(screen.queryByTestId("no-area-selected")).not.toBeInTheDocument();
    expect(screen.queryByText("Chọn một phân khu để xem dashboard")).not.toBeInTheDocument();
    expect(getProjectByExternalId).toHaveBeenCalledTimes(1);
    expect(listAreasScoped).toHaveBeenCalledTimes(1);
    expect(getAreaByExternalId).not.toHaveBeenCalled();
    expect(getDashboardTrend).not.toHaveBeenCalled();
  });

  it("registers the shell-free overview preview route", async () => {
    renderAt("/preview/overview?project=project-a&area=area-a");

    expect(await screen.findByTestId("overview-preview-route")).toBeInTheDocument();
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
  });

  it("renders the area dashboard as a separate direct route", async () => {
    renderAt("/projects/project-a/areas/area-a");

    expect(await screen.findByRole("heading", { name: "Sapphire 1" })).toBeInTheDocument();
    expect(screen.queryByTestId("area-card")).not.toBeInTheDocument();
    await waitFor(() => expect(getDashboardTrend).toHaveBeenCalledTimes(1));
    expect(getAreaByExternalId).toHaveBeenCalledWith("area-a");
    expect(getDashboardTrend).toHaveBeenCalledWith({ areaId: "area-uuid-a", areaTotalUnits: 100, granularity: "day" });
    expect(getProjectByExternalId).not.toHaveBeenCalled();
    expect(listAreasScoped).not.toHaveBeenCalled();
    expect(listDealsScoped).not.toHaveBeenCalled();
  });

  it("keeps the project dashboard route separate from the area workspace", async () => {
    renderAt("/projects/project-a/dashboard");

    expect(await screen.findByTestId("project-dashboard")).toBeInTheDocument();
    expect(screen.queryByTestId("area-card")).not.toBeInTheDocument();
    expect(listAreasScoped).not.toHaveBeenCalled();
    // Dashboard dự án KHÔNG được mang nội dung cấp danh mục.
    expect(screen.queryByRole("heading", { name: "Tổng quan danh mục" })).not.toBeInTheDocument();
    expect(listProjects).not.toHaveBeenCalled();
  });

  it("renders its own portfolio entry component at /overview, not the project dashboard", async () => {
    renderAt("/overview");

    expect(await screen.findByRole("heading", { name: "Tổng quan danh mục" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Xếp hạng căn toàn hệ thống" })).toBeInTheDocument();
    // Đúng điểm gãy cũ: /overview từng render CHÍNH thân dashboard của một dự án.
    expect(screen.queryByTestId("project-dashboard")).not.toBeInTheDocument();
    expect(getProjectByExternalId).not.toHaveBeenCalled();
    expect(listAreasScoped).not.toHaveBeenCalled();
  });
});
