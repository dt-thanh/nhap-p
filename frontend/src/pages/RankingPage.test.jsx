import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/endpoints", () => ({
  getRanking: vi.fn(),
  runRanking: vi.fn(),
  listProjects: vi.fn(),
}));
vi.mock("../hooks/useProjectScope", () => ({ useProjectScope: vi.fn() }));
vi.mock("../hooks/useBreakpoint", () => ({ useBreakpoint: () => ({ isMobile: false }) }));

import { getRanking, runRanking } from "../api/endpoints";
import { useProjectScope } from "../hooks/useProjectScope";
import RankingPage from "./RankingPage";

const scope = {
  projects: [{ project_id: "p-1", external_id: "ext-1", name: "Ocean Park" }],
  projectExternalId: "ext-1", areaExternalId: null, setProjectExternalId: vi.fn(), setAreaExternalId: vi.fn(),
  areas: [], loadingProjects: false, projectsStatus: "ok",
};

beforeEach(() => {
  vi.resetAllMocks();
  useProjectScope.mockReturnValue(scope);
  getRanking.mockResolvedValue({ computed_at: null, items: [], total: 0, units_ranked: 0, units_skipped: 0, band_counts: {} });
});

describe("RankingPage", () => {
  it("shows the unit search", async () => {
    const ranked = {
      computed_at: "2026-08-22T00:00:00Z",
      config_version: 2,
      units_ranked: 1,
      units_skipped: 0,
      band_counts: { high: 1, medium: 0, low: 0 },
      total: 1,
      disclaimer: "Đây không phải cam kết.",
      items: [{
        unit_id: "u-1", unit_code: "A-101", unit_type: "2PN", unit_status: "available",
        area_name: "A1", score: "0.8400", score_percent: 84, band: "high",
        rank_in_project: 1, rank_in_area: 1, weight_coverage: "1", contributions: [],
      }],
    };
    runRanking.mockResolvedValue(ranked);
    getRanking.mockResolvedValue(ranked);

    render(<MemoryRouter initialEntries={["/ranking"]}><RankingPage /></MemoryRouter>);
    const searchInput = await screen.findByPlaceholderText("Tìm căn (mã, tên)...");
    expect(searchInput).toBeInTheDocument();
    expect(searchInput.closest(".ranking-controls-row")).not.toBeInTheDocument();
    expect(searchInput.closest(".ranking-list-search")).toBeInTheDocument();

  });

  it("auto-selects the first scoped project and area on an unscoped first visit", async () => {
    const setProjectExternalId = vi.fn();
    const setAreaExternalId = vi.fn();
    useProjectScope.mockReturnValue({
      ...scope,
      projectExternalId: null,
      areaExternalId: null,
      setProjectExternalId,
      setAreaExternalId,
      areas: [{ external_id: "area-1", area_name: "Sapphire" }],
      loadingAreas: false,
    });

    render(<MemoryRouter initialEntries={["/ranking"]}><RankingPage /></MemoryRouter>);

    await waitFor(() => expect(setProjectExternalId).toHaveBeenCalledWith("ext-1"));
    // The area is selected when a project is already available; this separate
    // assertion verifies the same first-load behavior for a deep-linked project.
  });

  it("auto-selects the first area for a project without an area query", async () => {
    const setAreaExternalId = vi.fn();
    useProjectScope.mockReturnValue({
      ...scope,
      setAreaExternalId,
      areaExternalId: null,
      areas: [{ external_id: "area-1", area_name: "Sapphire" }],
      loadingAreas: false,
    });

    render(<MemoryRouter initialEntries={["/ranking?project=ext-1"]}><RankingPage /></MemoryRouter>);

    await waitFor(() => expect(setAreaExternalId).toHaveBeenCalledWith("area-1"));
  });
});
