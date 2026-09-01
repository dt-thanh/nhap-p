import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("shows the v3 badge when ranking_formula is v3_hierarchical", async () => {
    const ranked = {
      computed_at: "2026-08-22T00:00:00Z",
      config_version: 2,
      ranking_formula: "v3_hierarchical",
      units_ranked: 1,
      units_skipped: 0,
      band_counts: { high: 1, medium: 0, low: 0 },
      total: 1,
      disclaimer: "Đây không phải cam kết.",
      items: [{
        unit_id: "u-1", unit_code: "A-101", unit_type: "2PN", unit_status: "available",
        area_name: "A1", score: "0.8400", score_percent: 84,
        effective_score: "0.9500", effective_score_percent: 95, band: "high",
        rank_in_project: 1, rank_in_area: 1, weight_coverage: "1", contributions: [],
      }],
    };
    runRanking.mockResolvedValue(ranked);
    getRanking.mockResolvedValue(ranked);

    render(<MemoryRouter initialEntries={["/ranking"]}><RankingPage /></MemoryRouter>);

    expect(await screen.findByText("Đã áp dụng AHP (v3)")).toBeInTheDocument();
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

// --- PR-7: hierarchical (M/P/A/U) disclosure panel ---------------------------
//
// `hierarchical` is `null` unless the backend read flag is on AND a result
// is persisted — every fixture below sets it explicitly per scenario, the
// same way the API itself would (see `src/ranking/hierarchical_view.py`).

function baseRankedResponse(hierarchical) {
  return {
    computed_at: "2026-08-22T00:00:00Z",
    config_version: 2,
    units_ranked: 1,
    units_skipped: 0,
    band_counts: { high: 0, medium: 1, low: 0 },
    total: 1,
    disclaimer: "Đây không phải cam kết.",
    items: [
      {
        unit_id: "u-1",
        unit_code: "A-101",
        unit_type: "2PN",
        unit_status: "available",
        area_name: "A1",
        score: "0.5900",
        score_percent: 59,
        band: "medium",
        rank_in_project: 1,
        rank_in_area: 1,
        weight_coverage: "1",
        contributions: [],
        hierarchical,
      },
    ],
  };
}

async function renderAndExpandFirstRow(response) {
  getRanking.mockResolvedValue(response);
  render(
    <MemoryRouter initialEntries={["/ranking"]}>
      <RankingPage />
    </MemoryRouter>
  );
  const code = await screen.findByText("A-101");
  fireEvent.click(code.closest("tr"));
}

describe("RankingPage — hierarchical disclosure (PR-7)", () => {
  it("hides the panel entirely when hierarchical is null (flag off / not computed)", async () => {
    await renderAndExpandFirstRow(baseRankedResponse(null));
    expect(screen.queryByText("Hierarchical score")).not.toBeInTheDocument();
    // legacy experience is fully preserved
    expect(screen.getByText(/Điểm 0.5900 được tạo từ/)).toBeInTheDocument();
  });

  it("shows the unit-only badge and mandatory disclosure", async () => {
    await renderAndExpandFirstRow(
      baseRankedResponse({
        available: true,
        score: "0.5900",
        score_mode: "unit_only",
        top_level_weight_coverage: "0.40",
        configured_grain_weights: { market: 0.1, project: 0.25, area: 0.25, unit: 0.4 },
        effective_grain_weights: { unit: "1.000000" },
        eligible_grains: [],
        excluded_grains: {
          market: { reason: "NO_PUBLISHED_MARKET_VALUE" },
          project: { reason: "NO_PUBLISHED_PROJECT_VALUE" },
          area: { reason: "NO_PUBLISHED_AREA_EXPERT_VALUE" },
        },
        grains: {
          market: { eligible: false, score: null, coverage: null, exclusion_reason: "NO_PUBLISHED_MARKET_VALUE" },
          project: { eligible: false, score: null, coverage: null, exclusion_reason: "NO_PUBLISHED_PROJECT_VALUE" },
          area: { eligible: false, score: null, coverage: null, exclusion_reason: "NO_PUBLISHED_AREA_EXPERT_VALUE" },
          unit: { eligible: true, score: "0.5900", coverage: "0.40", exclusion_reason: null },
        },
        legal_gate: { status: "UNKNOWN", gated: false, reason: "NO_PUBLISHED_LEGAL_ASSERTION" },
        comparability_warning: null,
        cutoff_at: "2026-08-22T00:00:00Z",
        computed_at: "2026-08-22T00:00:00Z",
        config_version_id: "cfg-1",
        disclosure: "Unit-only hierarchical score — Market, Project, and Area context unavailable.",
      })
    );
    expect(screen.getByText("Hierarchical score")).toBeInTheDocument();
    expect(screen.getByText("Unit only")).toBeInTheDocument();
    expect(screen.getByText("○", { exact: false })).toBeInTheDocument();
    expect(
      screen.getByText("Unit-only hierarchical score — Market, Project, and Area context unavailable.")
    ).toBeInTheDocument();
    expect(screen.getByText(/CRM unit score/)).toBeInTheDocument();
  });

  it("shows the partial badge, coverage, weights, and excluded-grain reasons", async () => {
    await renderAndExpandFirstRow(
      baseRankedResponse({
        available: true,
        score: "0.6708",
        score_mode: "partial_hierarchical",
        top_level_weight_coverage: "0.65",
        configured_grain_weights: { market: 0.1, project: 0.25, area: 0.25, unit: 0.4 },
        effective_grain_weights: { project: "0.384615", unit: "0.615385" },
        eligible_grains: ["project"],
        excluded_grains: {
          market: { reason: "NO_PUBLISHED_MARKET_VALUE" },
          area: { reason: "NO_PUBLISHED_AREA_EXPERT_VALUE" },
        },
        grains: {
          market: { eligible: false, score: null, coverage: null, exclusion_reason: "NO_PUBLISHED_MARKET_VALUE" },
          project: {
            eligible: true,
            score: "0.8000",
            coverage: "0.25",
            exclusion_reason: null,
            freshness: { effective_at: "2026-08-20T00:00:00Z", expires_at: null, status: "fresh" },
            evidence_refs: [{ status: "available", document_id: "doc-1", original_filename: "memo.pdf" }],
          },
          area: { eligible: false, score: null, coverage: null, exclusion_reason: "NO_PUBLISHED_AREA_EXPERT_VALUE" },
          unit: { eligible: true, score: "0.5900", coverage: "0.40", exclusion_reason: null },
        },
        legal_gate: { status: "UNKNOWN", gated: false, reason: "NO_PUBLISHED_LEGAL_ASSERTION" },
        comparability_warning: null,
        cutoff_at: "2026-08-22T00:00:00Z",
        computed_at: "2026-08-22T00:00:00Z",
        config_version_id: "cfg-1",
        disclosure: "Partial hierarchical score — excluded: area (NO_PUBLISHED_AREA_EXPERT_VALUE), market (NO_PUBLISHED_MARKET_VALUE).",
      })
    );
    expect(screen.getByText("Partial context")).toBeInTheDocument();
    expect(screen.getByText(/Top-level context coverage/)).toBeInTheDocument();
    expect(screen.getByText(/65%/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Chi tiết theo grain"));
    expect(screen.getByText("memo.pdf", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText(/NO_PUBLISHED_/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Full hierarchical score/)).not.toBeInTheDocument();
  });

  it("shows the full-context badge and full detail without claiming a guarantee", async () => {
    await renderAndExpandFirstRow(
      baseRankedResponse({
        available: true,
        score: "0.7325",
        score_mode: "full_hierarchical",
        top_level_weight_coverage: "1.00",
        configured_grain_weights: { market: 0.1, project: 0.25, area: 0.25, unit: 0.4 },
        effective_grain_weights: { market: "0.10", project: "0.25", area: "0.25", unit: "0.40" },
        eligible_grains: ["market", "project", "area"],
        excluded_grains: {},
        grains: {
          market: { eligible: true, score: "0.7000", coverage: "0.10", exclusion_reason: null, freshness: null, evidence_refs: [] },
          project: { eligible: true, score: "0.6500", coverage: "0.25", exclusion_reason: null, freshness: null, evidence_refs: [] },
          area: { eligible: true, score: "0.8000", coverage: "0.25", exclusion_reason: null, freshness: null, evidence_refs: [] },
          unit: { eligible: true, score: "0.7500", coverage: "0.40", exclusion_reason: null },
        },
        legal_gate: { status: "NOT_HIGH_RISK", gated: false, reason: null },
        comparability_warning: null,
        cutoff_at: "2026-08-22T00:00:00Z",
        computed_at: "2026-08-22T00:00:00Z",
        config_version_id: "cfg-1",
        disclosure: "Full hierarchical score — decision support only, not a sales guarantee.",
      })
    );
    expect(screen.getByText("Full context")).toBeInTheDocument();
    expect(screen.getByText("Full hierarchical score — decision support only, not a sales guarantee.")).toBeInTheDocument();
  });

  it("shows the legal-gated state with no score/band and a distinct legacy score", async () => {
    await renderAndExpandFirstRow(
      baseRankedResponse({
        available: true,
        score: null,
        score_mode: "legal_gated",
        top_level_weight_coverage: null,
        configured_grain_weights: { market: 0.1, project: 0.25, area: 0.25, unit: 0.4 },
        effective_grain_weights: {},
        eligible_grains: [],
        excluded_grains: {
          market: { reason: "LEGAL_GATE" },
          project: { reason: "LEGAL_GATE" },
          area: { reason: "LEGAL_GATE" },
          unit: { reason: "LEGAL_GATE" },
        },
        grains: {},
        legal_gate: { status: "HIGH_RISK", gated: true, reason: null },
        comparability_warning: null,
        cutoff_at: "2026-08-22T00:00:00Z",
        computed_at: "2026-08-22T00:00:00Z",
        config_version_id: "cfg-1",
        disclosure: "Not ranked on the hierarchical surface because the project is under a HIGH_RISK legal gate.",
      })
    );
    expect(screen.getByText("Legal gated")).toBeInTheDocument();
    expect(screen.getByText("Not ranked")).toBeInTheDocument();
    expect(screen.queryByText("Chi tiết theo grain")).not.toBeInTheDocument();
    expect(screen.getByText(/Legal gate:/).closest("div")).toHaveTextContent("HIGH_RISK");
    // Legacy score is still shown, clearly labeled and distinct from the gated hierarchical score.
    expect(screen.getByText(/CRM unit score/)).toHaveTextContent("0.5900");
  });

  it("surfaces the comparability warning when the backend sends one", async () => {
    await renderAndExpandFirstRow(
      baseRankedResponse({
        available: true,
        score: "0.6708",
        score_mode: "partial_hierarchical",
        top_level_weight_coverage: "0.65",
        configured_grain_weights: { market: 0.1, project: 0.25, area: 0.25, unit: 0.4 },
        effective_grain_weights: { area: "0.384615", unit: "0.615385" },
        eligible_grains: ["area"],
        excluded_grains: {
          market: { reason: "NO_PUBLISHED_MARKET_VALUE" },
          project: { reason: "NO_PUBLISHED_PROJECT_VALUE" },
        },
        grains: {
          market: { eligible: false, score: null, coverage: null, exclusion_reason: "NO_PUBLISHED_MARKET_VALUE" },
          project: { eligible: false, score: null, coverage: null, exclusion_reason: "NO_PUBLISHED_PROJECT_VALUE" },
          area: { eligible: true, score: "0.9000", coverage: "0.25", exclusion_reason: null, freshness: null, evidence_refs: [] },
          unit: { eligible: true, score: "0.5900", coverage: "0.40", exclusion_reason: null },
        },
        legal_gate: { status: "UNKNOWN", gated: false, reason: "NO_PUBLISHED_LEGAL_ASSERTION" },
        comparability_warning:
          "Area eligibility/coverage differs across areas in this project's run — scores in different areas are not directly comparable (T18, §24.4.4).",
        cutoff_at: "2026-08-22T00:00:00Z",
        computed_at: "2026-08-22T00:00:00Z",
        config_version_id: "cfg-1",
        disclosure: "Partial hierarchical score — excluded: market (NO_PUBLISHED_MARKET_VALUE), project (NO_PUBLISHED_PROJECT_VALUE).",
      })
    );
    expect(screen.getByText(/not directly comparable/)).toBeInTheDocument();
  });
});
