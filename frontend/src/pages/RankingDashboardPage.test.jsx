import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../api/endpoints", () => ({ getAbsorptionSummary: vi.fn() }));
vi.mock("../hooks/useProjectScope", () => ({ useProjectScope: vi.fn() }));
vi.mock("../components/ProjectSelector", () => ({ default: () => <div /> }));

import { getAbsorptionSummary } from "../api/endpoints";
import { useProjectScope } from "../hooks/useProjectScope";
import RankingDashboardPage from "./RankingDashboardPage";

function Location() { return <div data-testid="location">{useLocation().pathname}</div>; }

describe("RankingDashboardPage report navigation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useProjectScope.mockReturnValue({
      projects: [{ project_id: "uuid-1", external_id: "P-LA-PURA", name: "La Pura", status: "active", launch_date: "2026-01-01" }],
      loadingProjects: false,
      projectsStatus: "ok",
    });
    getAbsorptionSummary.mockResolvedValue({ total_units: 10, units_sold: 5, sell_through: 50 });
  });

  function renderDashboard() {
    return render(<MemoryRouter initialEntries={["/ranking"]}><Routes><Route path="/ranking" element={<RankingDashboardPage />} /><Route path="/ranking/:projectId/report" element={<Location />} /></Routes></MemoryRouter>);
  }

  it("uses the canonical external ID for mouse navigation", async () => {
    renderDashboard();
    const row = await screen.findByRole("button", { name: "Mở xếp hạng chi tiết cho La Pura" });
    fireEvent.click(row);
    expect(screen.getByTestId("location")).toHaveTextContent("/ranking/P-LA-PURA/report");
  });

  it.each(["Enter", " "])("supports %s keyboard navigation", async (key) => {
    renderDashboard();
    const row = await screen.findByRole("button", { name: "Mở xếp hạng chi tiết cho La Pura" });
    fireEvent.keyDown(row, { key });
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/ranking/P-LA-PURA/report"));
  });

  it("renders executive KPIs from the existing absorption response", async () => {
    renderDashboard();
    expect((await screen.findAllByText("50.0%")).length).toBeGreaterThan(0);
    expect(screen.getByText("Tổng số căn")).toBeInTheDocument();
    expect(screen.getAllByText("Đã hấp thụ").length).toBeGreaterThan(0);
    expect(screen.getByText("10", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("5", { selector: "strong" })).toBeInTheDocument();
  });

  it("keeps unavailable score coverage honest", async () => {
    renderDashboard();
    const quality = await screen.findByRole("heading", { name: "Chất lượng dữ liệu" });
    expect(quality.parentElement).toHaveTextContent("Độ phủ điểm—");
    expect(screen.getByText("Chi tiết sức khỏe ranking chưa có trong view này.")).toBeInTheDocument();
  });

  it("shows partial ranking health only when the response supplies coverage", async () => {
    getAbsorptionSummary.mockResolvedValueOnce({ total_units: 10, units_sold: 5, sell_through: 50, units_ranked: 3, score_coverage: { total: 10 } });
    renderDashboard();
    expect(await screen.findByRole("status")).toHaveTextContent("3/10");
  });
});
