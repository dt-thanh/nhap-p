import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet } from "react-router-dom";
import { AppRoutes } from "./App";

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

vi.mock("./components/dashboard/AbsorptionDashboard", () => ({
  default: function TestProjectDashboard({ standalone }) {
    return <div data-testid={standalone ? "overview-dashboard" : "project-dashboard"}>Project dashboard</div>;
  },
}));

vi.mock("./api/endpoints", () => ({
  getProjectByExternalId: vi.fn(),
  listAreasScoped: vi.fn(),
  getAreaByExternalId: vi.fn(),
  getDashboardTrend: vi.fn(),
  listInventoryScoped: vi.fn(),
  listDealsScoped: vi.fn(),
}));

import {
  getAreaByExternalId,
  getDashboardTrend,
  getProjectByExternalId,
  listAreasScoped,
  listDealsScoped,
  listInventoryScoped,
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
    useBreakpoint.mockReturnValue({ bp: "laptop", isNarrow: false, isMobile: false, isTablet: false, isDesktop: false });
    resolveProject();
    getAreaByExternalId.mockImplementation((externalId) => Promise.resolve(
      externalId === "area-b" ? { ...AREAS[1], area_id: "area-uuid-b", project_id: "project-uuid", status: "active" } : AREA,
    ));
    getDashboardTrend.mockResolvedValue({ points: [] });
    listInventoryScoped.mockResolvedValue({ totals: { total_units: 2 }, units: [] });
    listDealsScoped.mockResolvedValue({ items: [] });
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
  });

  it("exposes the top-level Overview route through the existing app router", () => {
    renderAt("/overview");

    expect(screen.getByTestId("overview-dashboard")).toBeInTheDocument();
    expect(screen.queryByTestId("project-dashboard")).not.toBeInTheDocument();
  });
});
