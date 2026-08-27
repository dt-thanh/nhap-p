import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/endpoints", () => ({
  getHistoricalRanking: vi.fn(),
}));
vi.mock("../hooks/useProjectScope", () => ({ useProjectScope: vi.fn() }));

import { getHistoricalRanking } from "../api/endpoints";
import { useProjectScope } from "../hooks/useProjectScope";
import HistoricalTab from "./HistoricalTab";

const PROJECTS = [
  { project_id: "p-a", external_id: "ext-a", name: "Ocean Park" },
  { project_id: "p-b", external_id: "ext-b", name: "Sky Garden" },
];

const score = (external_project_id, value, confidence = "high") => ({
  external_project_id,
  score: value,
  confidence,
  components: {
    absorption_30d_score: value,
    absorption_90d_score: "0.5000",
    velocity_30d_score: "0.4000",
    momentum_score: "0.3000",
    stability_score: "0.8000",
  },
});

function renderTab() {
  return render(<MemoryRouter><HistoricalTab /></MemoryRouter>);
}

describe("HistoricalTab", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useProjectScope.mockReturnValue({ projects: PROJECTS });
    getHistoricalRanking.mockImplementation((id) => Promise.resolve(
      id === "ext-a" ? score(id, "0.6500") : score(id, "0.3000", "medium"),
    ));
  });

  it("renders scores, filters by confidence, and sorts columns", async () => {
    renderTab();
    await screen.findByText("Ocean Park");
    expect(screen.getAllByText("65%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("30%").length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText("Độ tin cậy"), { target: { value: "high" } });
    expect(screen.getByText("Ocean Park")).toBeInTheDocument();
    expect(screen.queryByText("Sky Garden")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Độ tin cậy"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("columnheader", { name: /Điểm/ }));
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Sky Garden");
  });

  it("exports the currently visible rows as CSV", async () => {
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    renderTab();
    await screen.findByText("Ocean Park");

    fireEvent.click(screen.getByRole("button", { name: "Xuất CSV" }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:test");
    createObjectURL.mockRestore();
    revokeObjectURL.mockRestore();
  });

  it("re-fetches when the historical date changes", async () => {
    renderTab();
    await waitFor(() => expect(getHistoricalRanking).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText("Tính tại ngày"), { target: { value: "2026-05-01" } });
    await waitFor(() => expect(getHistoricalRanking).toHaveBeenCalledTimes(4));
    expect(getHistoricalRanking).toHaveBeenLastCalledWith("ext-b", { as_of_date: "2026-05-01T00:00:00Z" });
  });
});
