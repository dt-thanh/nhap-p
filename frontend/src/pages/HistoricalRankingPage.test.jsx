import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/endpoints", () => ({
  listProjects: vi.fn(),
  listAreasScoped: vi.fn(),
  getHistoricalRanking: vi.fn(),
}));

import { getHistoricalRanking, listAreasScoped, listProjects } from "../api/endpoints";
import HistoricalRankingPage from "./HistoricalRankingPage";

const PROJECT_A = { project_id: "p-a", external_id: "ext-a", name: "Ocean Park", launch_date: "2026-01-01", status: "active" };
const PROJECT_B = { project_id: "p-b", external_id: "ext-b", name: "Sky Garden", launch_date: "2026-01-01", status: "active" };

function scoreRow(overrides = {}) {
  return {
    project_id: "uuid", external_project_id: "ext-a", as_of_date: "2026-06-01T00:00:00Z",
    score: "0.6500", confidence: "high",
    components: {
      absorption_30d_score: "0.7000", absorption_90d_score: "0.6000",
      velocity_30d_score: "0.5000", momentum_score: "0.5500", stability_score: "0.9000",
    },
    excluded_factors: [], computed_at: "2026-06-01T00:00:00Z",
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <HistoricalRankingPage />
    </MemoryRouter>,
  );
}

describe("HistoricalRankingPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listAreasScoped.mockResolvedValue([]);
  });

  it("fetches one historical score per project and renders a comparison table", async () => {
    listProjects.mockResolvedValue([PROJECT_A, PROJECT_B]);
    getHistoricalRanking.mockImplementation((externalId) =>
      Promise.resolve(
        externalId === "ext-a"
          ? scoreRow({ external_project_id: "ext-a" })
          : scoreRow({ external_project_id: "ext-b", score: "0.3000", confidence: "medium" }),
      ),
    );

    renderPage();

    await waitFor(() => expect(getHistoricalRanking).toHaveBeenCalledTimes(2));
    expect(getHistoricalRanking).toHaveBeenCalledWith("ext-a", {});
    expect(getHistoricalRanking).toHaveBeenCalledWith("ext-b", {});

    expect(await screen.findByText("Ocean Park")).toBeInTheDocument();
    expect(screen.getByText("Sky Garden")).toBeInTheDocument();
    expect(screen.getByText("65%")).toBeInTheDocument();
    expect(screen.getByText("30%")).toBeInTheDocument();
  });

  it("shows a per-row error without losing the other projects' scores", async () => {
    listProjects.mockResolvedValue([PROJECT_A, PROJECT_B]);
    getHistoricalRanking.mockImplementation((externalId) =>
      externalId === "ext-a"
        ? Promise.resolve(scoreRow())
        : Promise.reject(new Error("boom")),
    );

    renderPage();

    expect(await screen.findByText("Ocean Park")).toBeInTheDocument();
    expect(screen.getByText("Sky Garden")).toBeInTheDocument();
    expect(screen.getByText("65%")).toBeInTheDocument();
    expect(screen.getByTitle("boom")).toBeInTheDocument();
  });

  it("filters the table by confidence level", async () => {
    listProjects.mockResolvedValue([PROJECT_A, PROJECT_B]);
    getHistoricalRanking.mockImplementation((externalId) =>
      Promise.resolve(
        externalId === "ext-a"
          ? scoreRow({ confidence: "high" })
          : scoreRow({ external_project_id: "ext-b", confidence: "insufficient_history", score: null, components: {} }),
      ),
    );

    renderPage();
    await screen.findByText("Ocean Park");
    expect(screen.getByText("Sky Garden")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Độ tin cậy"), { target: { value: "high" } });

    expect(screen.getByText("Ocean Park")).toBeInTheDocument();
    expect(screen.queryByText("Sky Garden")).not.toBeInTheDocument();
  });

  it("re-fetches all projects when the as-of date changes", async () => {
    listProjects.mockResolvedValue([PROJECT_A]);
    getHistoricalRanking.mockResolvedValue(scoreRow());

    renderPage();
    await waitFor(() => expect(getHistoricalRanking).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Tính tại ngày"), { target: { value: "2026-05-01" } });

    await waitFor(() => expect(getHistoricalRanking).toHaveBeenCalledTimes(2));
    expect(getHistoricalRanking).toHaveBeenLastCalledWith("ext-a", { as_of_date: "2026-05-01T00:00:00Z" });
  });
});
