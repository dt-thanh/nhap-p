import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AbsorptionDashboard from "./AbsorptionDashboard";

vi.mock("../../api/endpoints", () => ({
  getDashboardSummary: vi.fn(),
  getDashboardTrend: vi.fn(),
  getDashboardAreas: vi.fn(),
  getDataQuality: vi.fn(),
  listProjects: vi.fn(),
  listAreasScoped: vi.fn(),
}));

vi.mock("./AbsorptionTrendChart", () => ({
  default: () => <div data-testid="absorption-trend-chart" />,
}));

import {
  getDashboardAreas,
  getDashboardSummary,
  getDashboardTrend,
  getDataQuality,
  listAreasScoped,
  listProjects,
} from "../../api/endpoints";

const PROJECT = { project_id: "project-internal", external_id: "project-external", name: "Pilot" };
const AREA = { area_id: "area-internal", external_id: "area-external", area_name: "A1", unit_type: "2PN", total_units: 100 };

function daysBetween(from, to) {
  return Math.round((new Date(`${to}T00:00:00Z`) - new Date(`${from}T00:00:00Z`)) / 86400000);
}

describe("AbsorptionDashboard trend scope and date filters", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listProjects.mockResolvedValue([PROJECT]);
    listAreasScoped.mockResolvedValue([AREA]);
    getDashboardSummary.mockResolvedValue({ total_units: 100, units_sold: 8, remaining_units: 92, absorption_rate: 8 });
    getDashboardTrend.mockResolvedValue({ points: [], latestVelocity7d: null, latestVelocity30d: null });
    getDashboardAreas.mockResolvedValue([]);
    getDataQuality.mockResolvedValue(null);
  });

  it.each([
    ["30 ngày", 30],
    ["90 ngày", 90],
    ["12 tháng", 365],
  ])("keeps project/area scope and requests the %s range", async (label, expectedDays) => {
    render(
      <MemoryRouter initialEntries={["/projects/project-external/dashboard?area=area-external"]}>
        <AbsorptionDashboard projectExternalId="project-external" />
      </MemoryRouter>,
    );

    await waitFor(() => expect(getDashboardTrend).toHaveBeenCalled());
    const initialCall = getDashboardTrend.mock.calls.at(-1)[0];
    expect(initialCall).toMatchObject({ areaId: "area-internal", areaTotalUnits: 100 });

    fireEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() => {
      const latest = getDashboardTrend.mock.calls.at(-1)?.[0];
      expect(latest?.from).toBeTruthy();
      expect(latest?.to).toBeTruthy();
      expect(daysBetween(latest.from, latest.to)).toBe(expectedDays);
    });

    const latestCall = getDashboardTrend.mock.calls.at(-1)[0];
    expect(latestCall).toMatchObject({ areaId: "area-internal", areaTotalUnits: 100, granularity: expectedDays > 90 ? "month" : "day" });
  });

  it("uses the domain source and requests an available historical year", async () => {
    getDashboardSummary.mockResolvedValue({
      total_units: 100, units_sold: 8, remaining_units: 92,
      data_source: "domain_units_deals", available_years: [2024, 2025],
    });
    getDashboardTrend.mockResolvedValue({
      points: [], latestVelocity7d: null, latestVelocity30d: null,
      dataSource: "domain_units_deals", availableYears: [2024, 2025], dataStatus: "no_data",
    });

    render(
      <MemoryRouter initialEntries={["/projects/project-external/dashboard?area=area-external"]}>
        <AbsorptionDashboard projectExternalId="project-external" />
      </MemoryRouter>,
    );

    await waitFor(() => expect(getDashboardSummary).toHaveBeenCalled());
    expect(getDashboardSummary.mock.calls[0][0]).toMatchObject({ calculator: "domain_units_deals" });
    const yearSelect = await screen.findByRole("combobox", { name: "Chọn năm" });
    fireEvent.change(yearSelect, { target: { value: "2025" } });
    await waitFor(() => expect(getDashboardTrend.mock.calls.at(-1)[0]).toMatchObject({
      year: "2025", granularity: "month", calculator: "domain_units_deals",
    }));
  });
});
