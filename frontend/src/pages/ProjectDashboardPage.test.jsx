// Phase: Dashboard Integration — required tests: "unknown project behavior",
// "project ID propagation" (route -> AbsorptionDashboard), direct-navigation
// route resolution.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ProjectDashboardPage from "./ProjectDashboardPage";

vi.mock("../api/endpoints", () => ({
  getProjectByExternalId: vi.fn(),
}));
import { getProjectByExternalId } from "../api/endpoints";

// AbsorptionDashboard's own data-fetching is covered elsewhere (endpoints
// tests, useProjectScope tests) — here we only need proof that this PAGE
// resolves the project first and passes the right identifier through.
vi.mock("../components/dashboard/AbsorptionDashboard", () => ({
  default: ({ projectExternalId }) => <div data-testid="dashboard-body">dashboard for {projectExternalId}</div>,
}));

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:externalId/dashboard" element={<ProjectDashboardPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProjectDashboardPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("resolves the project from the URL and renders the dashboard body scoped to it (direct navigation works cold)", async () => {
    getProjectByExternalId.mockResolvedValue({ project_id: "u-1", external_id: "P-0002", name: "Riverside Gardens" });

    renderAt("/projects/P-0002/dashboard");

    await waitFor(() => expect(getProjectByExternalId).toHaveBeenCalledWith("P-0002"));
    expect(await screen.findByTestId("dashboard-body")).toHaveTextContent("dashboard for P-0002");
  });

  it("a project that does not exist (404) shows a distinct not-found state, not a blank dashboard", async () => {
    const err = new Error("not found");
    err.status = 404;
    getProjectByExternalId.mockRejectedValue(err);

    renderAt("/projects/P-9999/dashboard");

    expect(await screen.findByText("Không tìm thấy dự án")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-body")).not.toBeInTheDocument();
  });

  it("a project outside the token's scope (403) shows a distinct not-permitted state, not a blank dashboard", async () => {
    const err = new Error("forbidden");
    err.status = 403;
    getProjectByExternalId.mockRejectedValue(err);

    renderAt("/projects/P-0003/dashboard");

    expect(await screen.findByText("Bạn không có quyền xem dự án này")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-body")).not.toBeInTheDocument();
  });

  it("a generic backend error shows a generic-but-distinct error state (not confused with not-found/not-permitted)", async () => {
    const err = new Error("Đã xảy ra lỗi từ máy chủ");
    err.status = 500;
    getProjectByExternalId.mockRejectedValue(err);

    renderAt("/projects/P-0001/dashboard");

    expect(await screen.findByText("Không tải được dự án")).toBeInTheDocument();
  });

  it("shows a breadcrumb with the resolved project name once loaded", async () => {
    getProjectByExternalId.mockResolvedValue({ project_id: "u-1", external_id: "P-0001", name: "Skyline Residences" });

    renderAt("/projects/P-0001/dashboard");

    expect(await screen.findByText("Skyline Residences")).toBeInTheDocument();
  });
});
