import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import AreaDetailPage from "./AreaDetailPage";

vi.mock("../api/endpoints", () => ({
  getAreaByExternalId: vi.fn(),
  getDashboardTrend: vi.fn(),
  listInventoryScoped: vi.fn(),
  listDealsScoped: vi.fn(),
}));

import { getAreaByExternalId, getDashboardTrend, listInventoryScoped, listDealsScoped } from "../api/endpoints";

const AREA = {
  area_id: "area-internal-1",
  project_id: "project-internal-1",
  external_id: "area-external-1",
  area_name: "Sapphire 1",
  unit_type: "2PN",
  bedrooms: 2,
  area_sqm: "65",
  total_units: 5,
  status: "active",
};

const TREND = {
  points: [
    { date: "2026-08-01", units_sold: 2, moving_average_7d: 0.3, moving_average_30d: 0.2, cumulative_sold: 2, sell_through: 40, is_observed: true, data_quality_status: "ok" },
  ],
  latestVelocity7d: 0.3,
  latestVelocity30d: 0.2,
};

const INVENTORY = {
  project_id: "project-internal-1",
  calculator: "domain_units_deals",
  totals: { total_units: 5, units_sold: 1, units_reserved: 1, units_remaining: 3, units_blocked: 0 },
  areas: [],
  units: [
    { unit_id: "unit-sold", external_unit_id: "ext-sold", area_id: AREA.area_id, unit_code: "S-01", unit_type: "2PN", status: "sold", active_deal_status: "sold", deleted_at: null },
    { unit_id: "unit-reserved", external_unit_id: "ext-reserved", area_id: AREA.area_id, unit_code: "R-01", unit_type: "2PN", status: "reserved", active_deal_status: "reserved", deleted_at: null },
    { unit_id: "unit-available", external_unit_id: "ext-available", area_id: AREA.area_id, unit_code: "A-01", unit_type: "2PN", status: "available", active_deal_status: null, deleted_at: null },
  ],
  anomalies: [],
};

const DEALS = {
  items: [{ deal_id: "deal-1", unit_id: "unit-sold", status: "sold", reserved_at: "2026-08-01", sold_at: "2026-08-02" }],
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function pageTree(path = "/projects/project-external-1/areas/area-external-1") {
  return (
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/projects/:id/areas/:areaId"
          element={<><AreaDetailPage /><LocationProbe /></>}
        />
      </Routes>
    </MemoryRouter>
  );
}

function renderPage() {
  return render(pageTree());
}

function useDefaultMocks() {
  getAreaByExternalId.mockResolvedValue(AREA);
  getDashboardTrend.mockResolvedValue(TREND);
  listInventoryScoped.mockResolvedValue(INVENTORY);
  listDealsScoped.mockResolvedValue(DEALS);
}

describe("AreaDetailPage trend and status catalog", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useDefaultMocks();
  });

  it("loads the existing trend once for the area and preserves external route identifiers", async () => {
    renderPage();

    await waitFor(() => expect(getDashboardTrend).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Xu hướng tiêu thụ" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Tồn kho" })).not.toBeInTheDocument();
    expect(getDashboardTrend).toHaveBeenCalledWith({ areaId: "area-internal-1", areaTotalUnits: 5, granularity: "day" });
    expect(getAreaByExternalId).toHaveBeenCalledWith("area-external-1");
    expect(listInventoryScoped).toHaveBeenCalledWith("project-external-1", {
      external_area_id: "area-external-1",
      include_units: true,
      limit: 200,
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/projects/project-external-1/areas/area-external-1");
  });

  it("does not request trend data per unit or duplicate it on ordinary re-render", async () => {
    const view = renderPage();
    expect(await screen.findAllByText("S-01")).not.toHaveLength(0);
    await waitFor(() => expect(getDashboardTrend).toHaveBeenCalledTimes(1));

    view.rerender(pageTree());
    expect(getDashboardTrend).toHaveBeenCalledTimes(1);
    expect(getDashboardTrend.mock.calls[0][0]).not.toHaveProperty("unit_id");
    expect(getDashboardTrend.mock.calls[0][0]).not.toHaveProperty("unit_code");
  });

  it("renders the trend loading state", async () => {
    getDashboardTrend.mockImplementation(() => new Promise(() => {}));
    renderPage();

    expect(screen.getByTestId("area-trend-loading")).toBeInTheDocument();
    expect(screen.getByText("Xu hướng tiêu thụ")).toBeInTheDocument();
  });

  it("renders real trend data through the existing chart", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByRole("heading", { name: "Xu hướng tiêu thụ" })).toBeInTheDocument());
    await waitFor(() => expect(getDashboardTrend).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Số căn bán mỗi ngày · tốc độ bán theo ngày")).toBeInTheDocument();
    expect(getDashboardTrend).toHaveBeenCalledWith({ areaId: "area-internal-1", areaTotalUnits: 5, granularity: "day" });
  });

  it("renders an honest empty trend state", async () => {
    getDashboardTrend.mockResolvedValue({ points: [], latestVelocity7d: null, latestVelocity30d: null });
    renderPage();

    await waitFor(() => expect(screen.getByText("Chưa có dữ liệu xu hướng hấp thụ")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText("Xu hướng tiêu thụ")).not.toBeInTheDocument());
  });

  it("renders trend errors and retries the same request", async () => {
    const error = new Error("trend failed");
    error.status = 500;
    getDashboardTrend.mockRejectedValueOnce(error).mockResolvedValueOnce(TREND);
    renderPage();

    expect(await screen.findByText("Có lỗi xảy ra")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    await waitFor(() => expect(getDashboardTrend).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Xu hướng tiêu thụ")).toBeInTheDocument();
  });

  it("preserves status data and lazy deals behavior", async () => {
    renderPage();

    expect(await screen.findAllByText("S-01")).not.toHaveLength(0);
    expect(screen.getAllByText("A-01").length).toBeGreaterThan(0);
    expect(listDealsScoped).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Giao dịch" }));
    expect(await screen.findByText("unit-sold")).toBeInTheDocument();
    expect(listDealsScoped).toHaveBeenCalledWith("project-external-1", { external_area_id: "area-external-1" });
  });

  it("sorts the status table deterministically using only status, deal status, and unit code", async () => {
    listInventoryScoped.mockResolvedValue({
      ...INVENTORY,
      units: [
        { ...INVENTORY.units[2], unit_id: "available-10", unit_code: "A-10", active_deal_status: null },
        { ...INVENTORY.units[1], unit_id: "reserved-no-deal", unit_code: "R-01", active_deal_status: null },
        { ...INVENTORY.units[1], unit_id: "reserved-deal", unit_code: "R-02", active_deal_status: "reserved" },
        { ...INVENTORY.units[0], unit_id: "sold-1", unit_code: "S-01" },
        { ...INVENTORY.units[2], unit_id: "available-2", unit_code: "A-02", active_deal_status: null },
      ],
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Phân loại căn theo trạng thái" }));

    const table = await screen.findByTestId("status-units-table");
    const rows = within(table).getAllByRole("row").slice(1).map((row) => row.textContent);
    const expectedCodes = ["S-01", "R-02", "R-01", "A-02", "A-10"];
    expect(rows.map((row) => expectedCodes.find((code) => row.includes(code)))).toEqual(expectedCodes);
  });

  it("shows the loaded-unit scope and partial-list warning without performance claims", async () => {
    listInventoryScoped.mockResolvedValue({
      ...INVENTORY,
      totals: { ...INVENTORY.totals, total_units: 250 },
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Phân loại căn theo trạng thái" }));

    const section = (await screen.findByRole("heading", { name: "Phân loại căn theo trạng thái" })).closest("section");
    expect(within(section).getByText("Dựa trên các căn đã tải · Hiển thị tối đa 200 căn")).toBeInTheDocument();
    expect(within(section).getByText(/Đang hiển thị một phần danh sách căn/)).toBeInTheDocument();
    expect(within(section).queryByText(/hiệu suất|tốc độ bán|velocity|absorption|doanh thu|giá/i)).not.toBeInTheDocument();
  });

  it("handles null and unknown statuses without inventing a status", async () => {
    listInventoryScoped.mockResolvedValue({
      ...INVENTORY,
      units: [
        { ...INVENTORY.units[0], status: null, unit_code: "UNKNOWN-1" },
        { ...INVENTORY.units[1], status: "new-status", unit_code: "UNKNOWN-2" },
      ],
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Phân loại căn theo trạng thái" }));

    expect(await screen.findByText("Chưa có dữ liệu trạng thái căn")).toBeInTheDocument();
    expect(within(screen.getByTestId("status-units-empty")).getByText("Chưa có dữ liệu trạng thái căn")).toBeInTheDocument();
  });

  it("renders unknown rows safely when at least one real status is available", async () => {
    listInventoryScoped.mockResolvedValue({
      ...INVENTORY,
      units: [
        { ...INVENTORY.units[0], status: "sold", unit_code: "S-01" },
        { ...INVENTORY.units[1], status: null, unit_code: "UNKNOWN-1" },
      ],
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Phân loại căn theo trạng thái" }));

    const table = await screen.findByTestId("status-units-table");
    expect(within(table).getByText("S-01")).toBeInTheDocument();
    expect(within(table).getByText("UNKNOWN-1")).toBeInTheDocument();
    expect(within(table).getAllByText("Chưa có dữ liệu").length).toBeGreaterThan(0);
  });
});
