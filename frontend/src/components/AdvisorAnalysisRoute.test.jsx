import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AdvisorAnalysisRoute from "./AdvisorAnalysisRoute";

const { getMePermissions } = vi.hoisted(() => ({ getMePermissions: vi.fn() }));
vi.mock("../api/endpoints", () => ({ getMePermissions }));

function renderRoute(permissions) {
  getMePermissions.mockResolvedValue(permissions);
  return render(<MemoryRouter initialEntries={["/expert-analysis"]}><Routes>
    <Route path="/overview" element={<div>Overview</div>} />
    <Route element={<AdvisorAnalysisRoute capability="advisor_analysis_authoring" />}>
      <Route path="/expert-analysis" element={<div>Advisor workspace</div>} />
    </Route>
  </Routes></MemoryRouter>);
}

describe("AdvisorAnalysisRoute", () => {
  it("mounts the workspace only for an authorized Advisor", async () => {
    renderRoute({ capabilities: { advisor_analysis_authoring: true } });
    expect(await screen.findByText("Advisor workspace")).toBeInTheDocument();
  });

  it.each(["viewer", "sales", "ceo", "admin"])("does not mount the workspace for %s", async (_persona) => {
    renderRoute({ capabilities: { advisor_analysis_authoring: false } });
    await waitFor(() => expect(screen.getByText("Overview")).toBeInTheDocument());
    expect(screen.queryByText("Advisor workspace")).not.toBeInTheDocument();
  });
});
