// InventoryPage — trình duyệt tồn kho THẬT (GET /inventory), thay cho
// MarketPrototypePage cũ (mô phỏng, không nối API). Kiểm: tham số gọi đúng,
// đổi dự án reset bộ lọc, không gọi khi chưa chọn dự án, loading/empty/error.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import InventoryPage from "./InventoryPage";

vi.mock("../api/endpoints", () => ({
  listProjects: vi.fn(),
  listAreasScoped: vi.fn(),
  listInventoryScoped: vi.fn(),
  bootstrapInventoryDefault: vi.fn(),
}));

import { bootstrapInventoryDefault, listProjects, listAreasScoped, listInventoryScoped } from "../api/endpoints";

const PROJECTS = [
  { project_id: "u-1", external_id: "prj_op1", name: "Ocean Park 1", status: "active" },
  { project_id: "u-2", external_id: "prj_smc", name: "Smart City", status: "active" },
];
const AREAS = [{ area_id: "a-1", external_id: "ar_0001", area_name: "Sapphire 1", unit_type: "Studio" }];
const INVENTORY = {
  project_id: "u-1",
  calculator: "domain_units_deals",
  totals: { total_units: 10, units_sold: 4, units_reserved: 1, units_remaining: 5, units_blocked: 0 },
  areas: [],
  units: [
    { unit_id: "un-1", external_unit_id: "e-1", area_id: "a-1", unit_code: "ST-01", unit_type: "Studio", status: "available", active_deal_status: null },
    { unit_id: "un-2", external_unit_id: "e-2", area_id: "a-1", unit_code: "ST-02", unit_type: "Studio", status: "reserved", active_deal_status: "reserved" },
  ],
  anomalies: [],
};

function renderPage() {
  return render(
    <MemoryRouter>
      <InventoryPage />
    </MemoryRouter>,
  );
}

describe("InventoryPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listProjects.mockResolvedValue(PROJECTS);
    listAreasScoped.mockResolvedValue(AREAS);
    listInventoryScoped.mockResolvedValue(INVENTORY);
    bootstrapInventoryDefault.mockResolvedValue({ project: PROJECTS[0], area: AREAS[0], inventory: INVENTORY, created: false });
  });

  it("selects the first valid project and area, then loads inventory immediately", async () => {
    renderPage();
    await waitFor(() => expect(listInventoryScoped).toHaveBeenCalledWith("prj_op1", expect.objectContaining({ external_area_id: "ar_0001" })));
    expect(await screen.findByText("ST-01")).toBeInTheDocument();
  });

  it("selecting a project calls GET /inventory with include_units=true and renders real units", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "prj_op1" } });

    await waitFor(() =>
      expect(listInventoryScoped).toHaveBeenCalledWith(
        "prj_op1",
        expect.objectContaining({ include_units: true, limit: 100, offset: 0 }),
      ),
    );
    expect(await screen.findByText("ST-01")).toBeInTheDocument();
    expect(screen.getByText("ST-02")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Còn trống" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Đang giữ" })).toBeInTheDocument();
    const table = within(screen.getByRole("table"));
    expect(table.getByText("Còn trống")).toBeInTheDocument();
    expect(table.getByText("Đang giữ")).toBeInTheDocument();
  });

  it("renders real totals from the API, not derived/fabricated numbers", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "prj_op1" } });

    expect(await screen.findByText("10")).toBeInTheDocument(); // total_units
    expect(screen.getByText("4")).toBeInTheDocument(); // units_sold
  });

  it("filtering by unit status passes unit_status through to the API and resets to page 0", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "prj_op1" } });
    await screen.findByText("ST-01");

    fireEvent.click(screen.getByRole("button", { name: "Còn trống" }));

    await waitFor(() =>
      expect(listInventoryScoped).toHaveBeenLastCalledWith(
        "prj_op1",
        expect.objectContaining({ unit_status: "available", offset: 0 }),
      ),
    );
  });

  it("changing project resets the unit-status filter (dependent data does not leak across scope)", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    const projectSelect = screen.getByRole("combobox");
    fireEvent.change(projectSelect, { target: { value: "prj_op1" } });
    await screen.findByText("ST-01");
    fireEvent.click(screen.getByRole("button", { name: "Đã bán" }));
    await waitFor(() => expect(listInventoryScoped).toHaveBeenLastCalledWith("prj_op1", expect.objectContaining({ unit_status: "sold" })));

    fireEvent.change(projectSelect, { target: { value: "prj_smc" } });

    await waitFor(() =>
      expect(listInventoryScoped).toHaveBeenLastCalledWith(
        "prj_smc",
        expect.not.objectContaining({ unit_status: expect.anything() }),
      ),
    );
  });

  it("shows the real error state on failure, with a retry affordance, not a blank table", async () => {
    const err = { message: "Đã xảy ra lỗi từ máy chủ.", status: 500 };
    listInventoryScoped.mockRejectedValue(err);
    renderPage();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "prj_op1" } });

    expect(await screen.findByText("Thử lại")).toBeInTheDocument();
  });

  it("shows a scope error instead of an endless initial skeleton when areas cannot load", async () => {
    listAreasScoped.mockRejectedValue({ message: "area service unavailable", status: 500 });
    renderPage();

    expect(await screen.findByText("Có lỗi xảy ra")).toBeInTheDocument();
    expect(listInventoryScoped).not.toHaveBeenCalled();
  });

  it("shows an explicit empty state, not a fabricated row, when a project genuinely has no units", async () => {
    listInventoryScoped.mockResolvedValue({ ...INVENTORY, units: [], totals: { ...INVENTORY.totals, total_units: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "prj_op1" } });

    expect(await screen.findByText("Chưa có dữ liệu")).toBeInTheDocument();
  });
});
