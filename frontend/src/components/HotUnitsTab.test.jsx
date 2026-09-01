import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/endpoints", () => ({
  runRanking: vi.fn(),
  getRanking: vi.fn(),
}));
vi.mock("../hooks/useProjectScope", () => ({ useProjectScope: vi.fn() }));
vi.mock("../hooks/useBreakpoint", () => ({ useBreakpoint: () => ({ isMobile: false }) }));

import { runRanking, getRanking } from "../api/endpoints";
import { useProjectScope } from "../hooks/useProjectScope";
import HotUnitsTab from "./HotUnitsTab";

const ranking = {
  computed_at: "2026-08-22T00:00:00Z",
  config_version: 2,
  units_ranked: 1,
  units_skipped: 0,
  band_counts: { high: 1, medium: 0, low: 0 },
  total: 1,
  disclaimer: "Đây không phải cam kết.",
  items: [{
    unit_id: "u-1", unit_code: "A-101", unit_type: "2PN", unit_status: "available", area_name: "A1",
    score: "0.8400", score_percent: 84, band: "high", rank_in_project: 1, rank_in_area: 1,
    weight_coverage: "1", contributions: [],
  }],
};

const manyRanking = {
  ...ranking,
  units_ranked: 5,
  total: 5,
  band_counts: { high: 1, medium: 3, low: 1 },
  items: [
    ranking.items[0],
    { ...ranking.items[0], unit_id: "u-2", unit_code: "A-102", score: "0.7400", score_percent: 74, band: "medium", rank_in_project: 2 },
    { ...ranking.items[0], unit_id: "u-3", unit_code: "A-103", score: "0.6400", score_percent: 64, band: "medium", rank_in_project: 3 },
    { ...ranking.items[0], unit_id: "u-4", unit_code: "A-104", score: "0.5400", score_percent: 54, band: "medium", rank_in_project: 4 },
    { ...ranking.items[0], unit_id: "u-5", unit_code: "A-105", score: "0.4400", score_percent: 44, band: "low", rank_in_project: 5 },
  ],
};

const scopedUnits = Array.from({ length: 101 }, (_, index) => ({
  ...ranking.items[0],
  unit_id: `scope-${index}`,
  unit_code: `S-${String(index).padStart(3, "0")}`,
  band: index < 20 ? "high" : index < 80 ? "medium" : "low",
  unit_status: index === 100 ? "sold" : "available",
  rank_in_project: index + 1,
}));

function scopedResponse(params = {}) {
  const scopeUnits = params.unit_status === "available"
    ? scopedUnits.filter((unit) => unit.unit_status === "available")
    : scopedUnits;
  const matched = params.band
    ? scopeUnits.filter((unit) => unit.band === params.band)
    : scopeUnits;
  return {
    ...manyRanking,
    units_ranked: scopeUnits.length,
    total: matched.length,
    band_counts: {
      high: scopeUnits.filter((unit) => unit.band === "high").length,
      medium: scopeUnits.filter((unit) => unit.band === "medium").length,
      low: scopeUnits.filter((unit) => unit.band === "low").length,
    },
    items: matched.slice(params.offset || 0, (params.offset || 0) + 50),
  };
}

function renderTab() {
  return render(<MemoryRouter><HotUnitsTab /></MemoryRouter>);
}

describe("HotUnitsTab", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useProjectScope.mockReturnValue({
      projects: [{ project_id: "p-1", external_id: "ext-1", name: "Ocean Park" }],
      projectExternalId: "ext-1",
      areaExternalId: null,
      setProjectExternalId: vi.fn(),
      setAreaExternalId: vi.fn(),
      areas: [],
      loadingProjects: false,
      projectsStatus: "ok",
    });
    runRanking.mockResolvedValue(ranking);
    getRanking.mockResolvedValue(ranking);
  });

  it("renders the hot-unit grid and applies the available filter", async () => {
    const view = renderTab();
    expect((await screen.findAllByText("A-101")).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("Phân bố mức độ quan tâm")).toBeInTheDocument();
    expect(screen.getAllByText("Được quan tâm nhiều nhất").length).toBeGreaterThanOrEqual(1);
    const controlsRow = view.container.querySelector(".ranking-controls-row");
    expect(controlsRow).toBeInTheDocument();
    expect(controlsRow).toContainElement(screen.getByLabelText("Chọn dự án"));
    expect(controlsRow).toContainElement(screen.getByLabelText("Chọn phân khu"));
    expect(controlsRow).not.toContainElement(screen.getByPlaceholderText("Tìm căn (mã, tên)..."));
    const listSearch = view.container.querySelector(".ranking-list-search");
    expect(listSearch).toBeInTheDocument();
    expect(listSearch).toContainElement(screen.getByPlaceholderText("Tìm căn (mã, tên)..."));
    const rankedTable = view.container.querySelector(".ranking-table");
    expect(listSearch.compareDocumentPosition(rankedTable) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Chỉ căn còn trống" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Tất cả" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cao (1)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Trung bình (0)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Thấp (0)" })).not.toBeInTheDocument();
    await waitFor(() => expect(runRanking).toHaveBeenCalledWith("ext-1", {}));

  });

  it("recalculates when the selected area changes", async () => {
    const first = renderTab();
    await screen.findAllByText("A-101");
    useProjectScope.mockReturnValue({
      ...useProjectScope.mock.results[useProjectScope.mock.results.length - 1].value,
      areaExternalId: "area-1",
    });
    first.rerender(<MemoryRouter><HotUnitsTab /></MemoryRouter>);
    await waitFor(() => expect(runRanking).toHaveBeenCalledWith("ext-1", { external_area_id: "area-1" }));
  });

  it("recalculates when the selected project changes", async () => {
    const first = renderTab();
    await waitFor(() => expect(runRanking).toHaveBeenCalledWith("ext-1", {}));
    const currentScope = useProjectScope.mock.results[useProjectScope.mock.results.length - 1].value;
    useProjectScope.mockReturnValue({ ...currentScope, projectExternalId: "ext-2", areaExternalId: null });
    first.rerender(<MemoryRouter><HotUnitsTab /></MemoryRouter>);
    await waitFor(() => expect(runRanking).toHaveBeenCalledWith("ext-2", {}));
  });

  it("shows the recalculation error without hiding the controls", async () => {
    runRanking.mockRejectedValueOnce(new Error("ranking failed"));
    renderTab();
    expect(await screen.findByText("ranking failed")).toBeInTheDocument();
    expect(screen.getByLabelText("Chọn dự án")).toBeInTheDocument();
  });

  it("shows the API-provided insufficient-input state instead of calling it unranked", async () => {
    const insufficient = {
      computed_at: "2026-08-22T00:00:00Z",
      state: "insufficient_data",
      reason: "NO_LIVE_UNITS",
      items: [],
      total: 0,
      units_ranked: 0,
      units_skipped: 0,
      band_counts: {},
      disclaimer: "Đây không phải cam kết.",
    };
    runRanking.mockResolvedValue(insufficient);
    getRanking.mockResolvedValue(insufficient);

    renderTab();
    expect(await screen.findByText("Chưa có căn đủ điều kiện để xếp hạng")).toBeInTheDocument();
    expect(screen.getByText("Dữ liệu đồng bộ hiện không có căn còn hiệu lực cho dự án này.")).toBeInTheDocument();
    expect(screen.queryByText("Dự án này chưa được xếp hạng lần nào")).not.toBeInTheDocument();
  });

  it("does not expose a manual calculate action", async () => {
    renderTab();
    await screen.findAllByText("A-101");
    expect(screen.queryByRole("button", { name: "Tính lại" })).not.toBeInTheDocument();
  });

  it("filters the ranking rows by unit search", async () => {
    renderTab();
    await screen.findAllByText("A-101");

    fireEvent.change(screen.getByPlaceholderText("Tìm căn (mã, tên)..."), { target: { value: "Z-999" } });
    const emptyState = await screen.findByText("Không tìm thấy căn nào phù hợp với bộ lọc");
    const listSearch = document.querySelector(".ranking-list-search");
    expect(emptyState).toBeInTheDocument();
    expect(listSearch.compareDocumentPosition(emptyState) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("filters the table with demand rows and toggles the active row off", async () => {
    runRanking.mockResolvedValue(manyRanking);
    getRanking.mockImplementation((_projectId, params) => {
      const items = params.band
        ? manyRanking.items.filter((unit) => unit.band === params.band)
        : manyRanking.items;
      return Promise.resolve({ ...manyRanking, items, total: items.length });
    });

    renderTab();
    await screen.findByText("A-105");
    const categoryList = screen.getByRole("list", { name: "Các nhóm mức độ quan tâm" });
    const categoryButton = (label) => within(categoryList).getByRole("button", { name: label });

    fireEvent.click(categoryButton("Lọc các căn được quan tâm cao"));
    await waitFor(() => expect(getRanking).toHaveBeenLastCalledWith(
      "ext-1",
      expect.objectContaining({ band: "high", unit_status: "available", offset: 0 }),
    ));
    expect(await screen.findByText("A-101")).toBeInTheDocument();
    expect(screen.queryByText("A-102")).not.toBeInTheDocument();
    expect(categoryButton("Lọc các căn được quan tâm cao")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(categoryButton("Lọc các căn được quan tâm cao"));
    await waitFor(() => expect(getRanking).toHaveBeenLastCalledWith(
      "ext-1",
      expect.not.objectContaining({ band: expect.anything() }),
    ));
    expect(await screen.findByText("A-102")).toBeInTheDocument();
    expect(categoryButton("Lọc các căn được quan tâm cao")).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(categoryButton("Lọc các căn được quan tâm trung bình"));
    await waitFor(() => expect(getRanking).toHaveBeenLastCalledWith(
      "ext-1",
      expect.objectContaining({ band: "medium", unit_status: "available" }),
    ));
    expect(screen.queryByText("A-101")).not.toBeInTheDocument();
    expect(await screen.findByText("A-104")).toBeInTheDocument();

    fireEvent.click(categoryButton("Lọc các căn được quan tâm thấp"));
    await waitFor(() => expect(getRanking).toHaveBeenLastCalledWith(
      "ext-1",
      expect.objectContaining({ band: "low", unit_status: "available" }),
    ));
    expect(await screen.findByText("A-105")).toBeInTheDocument();
  });

  it("resets pagination when a demand row is selected", async () => {
    const pagedRanking = { ...manyRanking, total: 60 };
    runRanking.mockResolvedValue(pagedRanking);
    getRanking.mockResolvedValue(pagedRanking);

    renderTab();
    await screen.findByText("A-105");
    fireEvent.click(screen.getByRole("button", { name: "Trang sau" }));
    await waitFor(() => expect(getRanking).toHaveBeenLastCalledWith(
      "ext-1",
      expect.objectContaining({ offset: 50 }),
    ));

    fireEvent.click(screen.getByRole("button", { name: "Lọc các căn được quan tâm cao" }));
    await waitFor(() => expect(getRanking).toHaveBeenLastCalledWith(
      "ext-1",
      expect.objectContaining({ band: "high", offset: 0 }),
    ));
  });

  it("keeps full-scope demand counts stable across availability and pagination", async () => {
    runRanking.mockResolvedValue({ ...scopedResponse(), items: scopedUnits.slice(0, 50), total: 101 });
    getRanking.mockImplementation((_projectId, params) => Promise.resolve(scopedResponse(params)));

    renderTab();
    await screen.findByText("S-000");
    expect(screen.queryByText("S-100")).not.toBeInTheDocument();
    await waitFor(() => expect(getRanking).toHaveBeenCalledWith(
      "ext-1",
      expect.objectContaining({ unit_status: "available", offset: 0, limit: 50 }),
    ));

    const categoryList = screen.getByRole("list", { name: "Các nhóm mức độ quan tâm" });
    const categoryCounts = () => within(categoryList).getAllByRole("listitem").map((row) => row.textContent);
    expect(categoryCounts()).toEqual([
      expect.stringContaining("20 căn"),
      expect.stringContaining("60 căn"),
      expect.stringContaining("20 căn"),
    ]);
    expect(screen.getByText(/1–50 \/ 100/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Trang sau" }));
    await waitFor(() => expect(getRanking).toHaveBeenLastCalledWith(
      "ext-1",
      expect.objectContaining({ unit_status: "available", offset: 50, limit: 50 }),
    ));
    expect(categoryCounts()).toEqual([
      expect.stringContaining("20 căn"),
      expect.stringContaining("60 căn"),
      expect.stringContaining("20 căn"),
    ]);
  });

  it("toggling 'Chỉ căn còn trống' off drops unit_status from the GET /ranking call", async () => {
    renderTab();
    await screen.findAllByText("A-101");

    fireEvent.click(screen.getByRole("checkbox", { name: "Chỉ căn còn trống" }));

    await waitFor(() => {
      const call = getRanking.mock.calls.find(([, params]) => !("unit_status" in params));
      expect(call).toBeTruthy();
    });
    expect(getRanking.mock.calls.every(([, params]) => !("band" in params))).toBe(true);
  });

  it("paging to the next page reads via GET /ranking with the new offset", async () => {
    const manyUnits = { ...ranking, total: 60 };
    runRanking.mockResolvedValue(manyUnits);
    getRanking.mockResolvedValue(manyUnits);
    renderTab();
    await screen.findAllByText("A-101");

    fireEvent.click(screen.getByRole("button", { name: "Trang sau" }));

    await waitFor(() => expect(getRanking).toHaveBeenCalledWith(
      "ext-1",
      expect.objectContaining({ offset: 50, limit: 50 }),
    ));
  });

  it("shows no v3 badge and no pending hint for a legacy-only ranking (no new fields present)", async () => {
    renderTab();
    await screen.findAllByText("A-101");
    expect(screen.queryByText("Đã áp dụng AHP (v3)")).not.toBeInTheDocument();
    expect(screen.queryByText("Đang chờ áp dụng AHP")).not.toBeInTheDocument();
    expect(await screen.findByText("84.0%")).toBeInTheDocument();
  });

  it("shows the v3 badge and prefers effective_score_percent when ranking_formula is v3_hierarchical", async () => {
    const v3Ranking = {
      ...ranking,
      ranking_formula: "v3_hierarchical",
      items: [{ ...ranking.items[0], effective_score: "0.9500", effective_score_percent: 95 }],
    };
    runRanking.mockResolvedValue(v3Ranking);
    getRanking.mockResolvedValue(v3Ranking);

    renderTab();

    expect(await screen.findByText("Đã áp dụng AHP (v3)")).toBeInTheDocument();
    expect(await screen.findByText("95.0%")).toBeInTheDocument();
    expect(screen.queryByText("84.0%")).not.toBeInTheDocument();
  });

  it("shows the pending-AHP hint when ahp_pending_status is set", async () => {
    const pendingRanking = { ...ranking, ahp_pending_status: "queued" };
    runRanking.mockResolvedValue(pendingRanking);
    getRanking.mockResolvedValue(pendingRanking);

    renderTab();

    expect(await screen.findByText("Đang chờ áp dụng AHP")).toBeInTheDocument();
    expect(screen.queryByText("Đã áp dụng AHP (v3)")).not.toBeInTheDocument();
  });
});
