import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../api/endpoints", () => ({ getUnitRankingReport: vi.fn() }));

import { getUnitRankingReport } from "../api/endpoints";
import UnitRankingReportPage from "./UnitRankingReportPage";

const base = {
  state: "ready", reason: null,
  project: { external_id: "P-LA-PURA", name: "La Pura" },
  area: { external_id: "A-ZENITH", name: "Zenith" },
  apartment: { apartment_id: "U-1", code: "A1.06.05", unit_type: "2PN", status: "available", floor: 6, orientation: "Đông Nam", area_sqm: "60.1", price_vnd: "3057395419" },
  ranking_run_id: "run-1", config_version: 7, total_score: "0.8400", rank: 1, ranked_apartments_in_area: 12,
  criteria: [{ name: "unit_available", grain: "unit", weight: "0.4", normalized_score: "1", contribution: "0.4" }],
};

function renderPage() {
  return render(<MemoryRouter initialEntries={["/ranking/P-LA-PURA/areas/A-ZENITH/units/U-1/report"]}><Routes><Route path="/ranking/:projectId/areas/:areaId/units/:unitId/report" element={<UnitRankingReportPage />} /></Routes></MemoryRouter>);
}

describe("UnitRankingReportPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("renders a high-rank apartment explanation generated from its breakdown", async () => {
    getUnitRankingReport.mockResolvedValue({ ...base, explanation: "Căn A1.06.05 thuộc nhóm xếp hạng cao (#1/12); unit_available đóng góp nhiều nhất (0.400)." });
    renderPage();
    expect(await screen.findByText(/thuộc nhóm xếp hạng cao/)).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("unit available")).toBeInTheDocument();
  });

  it("renders a low-rank apartment explanation without substituting a generic sentence", async () => {
    getUnitRankingReport.mockResolvedValue({ ...base, total_score: "0.2100", rank: 12, explanation: "Căn A1.06.05 thuộc nhóm xếp hạng thấp (#12/12); area_conversion_norm có điểm chuẩn hóa thấp nhất (0.100)." });
    renderPage();
    expect(await screen.findByText(/thuộc nhóm xếp hạng thấp/)).toBeInTheDocument();
    expect(screen.getByText("#12")).toBeInTheDocument();
  });
});
