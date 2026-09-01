import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../api/endpoints", () => ({ getProjectRankingReport: vi.fn() }));

import { getProjectRankingReport } from "../api/endpoints";
import ProjectRankingReportPage from "./ProjectRankingReportPage";

const readyReport = {
  state: "ready", reason: null,
  project: { external_id: "P-LA-PURA", project_id: "uuid-1", name: "La Pura", status: "active" },
  ranking_run_id: "run-1", config_version: 7, computed_at: "2026-08-28T10:00:00Z",
  persisted_hierarchical_scores: 12,
  areas: [{ area_id: "area-1", external_id: "A-LAPURA-1", name: "Zenith", apartment_count: 20, scored_apartment_count: 12, average_ahp_score: "0.7345" }],
};

function Location() { return <output data-testid="location">{useLocation().pathname}</output>; }
function renderPage() {
  return render(<MemoryRouter initialEntries={["/ranking/P-LA-PURA/report"]}><Routes><Route path="/ranking/:projectId/report" element={<ProjectRankingReportPage />} /><Route path="*" element={<Location />} /></Routes></MemoryRouter>);
}

describe("ProjectRankingReportPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("renders one backend-aggregated zone and navigates to its unit ranking", async () => {
    getProjectRankingReport.mockResolvedValue(readyReport);
    renderPage();
    await waitFor(() => expect(getProjectRankingReport).toHaveBeenCalledWith("P-LA-PURA"));
    expect(await screen.findByText("Zenith")).toBeInTheDocument();
    expect(screen.getByText("0.7345")).toBeInTheDocument();
    expect(screen.getByText("12/20 căn có điểm")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mở xếp hạng căn hộ tại Zenith" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/ranking/P-LA-PURA/areas/A-LAPURA-1");
  });

  it("keeps zones visible while hierarchical read is disabled", async () => {
    getProjectRankingReport.mockResolvedValue({ ...readyReport, state: "feature_disabled", reason: "HIERARCHICAL_READ_DISABLED", persisted_hierarchical_scores: 0, areas: [{ ...readyReport.areas[0], scored_apartment_count: 0, average_ahp_score: null }] });
    renderPage();
    expect(await screen.findByText("Chế độ xem AHP đang tắt")).toBeInTheDocument();
    expect(screen.getByText("Zenith")).toBeInTheDocument();
  });

  it("no published hierarchy: shows the flat-v2 message and a CTA to the flat CRM ranking page, never claims AHP failure", async () => {
    getProjectRankingReport.mockResolvedValue({
      ...readyReport, state: "no_scored_units", reason: "NO_PERSISTED_HIERARCHICAL_SCORES",
      persisted_hierarchical_scores: 0, hierarchy_status: "not_published", expert_criteria_applied: [],
      areas: [{ ...readyReport.areas[0], scored_apartment_count: 0, average_ahp_score: null }],
    });
    renderPage();
    expect(await screen.findByText("Ranking hiện hành đang dùng dữ liệu CRM theo cấu hình v2.")).toBeInTheDocument();
    expect(screen.getByText("AHP phân cấp chưa được công bố cho dự án này.")).toBeInTheDocument();
    const cta = screen.getByRole("button", { name: "Xem điểm CRM v2 tại đây →" });
    fireEvent.click(cta);
    expect(screen.getByTestId("location")).toHaveTextContent("/ranking/P-LA-PURA");
  });

  it("published v3, CRM-only: shows the CRM-only message and never claims an Expert contribution", async () => {
    getProjectRankingReport.mockResolvedValue({
      ...readyReport, hierarchy_status: "crm_only", expert_criteria_applied: [],
      score_mode_counts: { partial_hierarchical: 10, unit_only: 2 },
    });
    renderPage();
    expect(await screen.findByText(/Ranking hiện hành dùng cấu hình v7\./)).toBeInTheDocument();
    expect(screen.getByText("Điểm hiện tại được tính từ dữ liệu CRM.")).toBeInTheDocument();
    expect(screen.getByText("Chưa có đánh giá cố vấn hợp lệ được áp dụng.")).toBeInTheDocument();
  });

  it("published v3, Expert-enriched: shows the combined message and lists the applied Expert criteria", async () => {
    getProjectRankingReport.mockResolvedValue({
      ...readyReport, hierarchy_status: "expert_enriched", expert_criteria_applied: ["area_accessibility"],
      score_mode_counts: { partial_hierarchical: 12 },
    });
    renderPage();
    expect(await screen.findByText("Điểm hiện tại kết hợp dữ liệu CRM và đánh giá cố vấn đã được CEO duyệt.")).toBeInTheDocument();
    expect(screen.getByText("Tiêu chí cố vấn đang áp dụng: area_accessibility.")).toBeInTheDocument();
  });

  it("always offers a labeled v2-comparison link, never labeled as AHP", async () => {
    getProjectRankingReport.mockResolvedValue(readyReport);
    renderPage();
    const link = await screen.findByText("Điểm CRM v2 — dùng để đối chiếu →");
    fireEvent.click(link);
    expect(screen.getByTestId("location")).toHaveTextContent("/ranking/P-LA-PURA");
  });
});
