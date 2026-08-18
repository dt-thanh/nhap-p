// Phase F/G required tests: "ProjectSelector permission filtering",
// "AreaSelector reset on Project change", "URL scope persistence",
// "scoped table queries".
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useProjectScope } from "./useProjectScope";

vi.mock("../api/endpoints", () => ({
  listProjects: vi.fn(),
  listAreasScoped: vi.fn(),
}));

import { listProjects, listAreasScoped } from "../api/endpoints";

function wrapper(initialEntries) {
  // eslint-disable-next-line react/display-name
  return ({ children }) => <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>;
}

describe("useProjectScope", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("only shows what /v1/projects returns (backend already scoped it — no FE re-filtering)", async () => {
    listProjects.mockResolvedValue([
      { external_id: "P-0001", name: "A", project_id: "u1" },
      { external_id: "P-0002", name: "B", project_id: "u2" },
    ]);
    listAreasScoped.mockResolvedValue([]);

    const { result } = renderHook(() => useProjectScope(), { wrapper: wrapper(["/catalog"]) });

    await waitFor(() => expect(result.current.loadingProjects).toBe(false));
    expect(result.current.projects.map((p) => p.external_id)).toEqual(["P-0001", "P-0002"]);
    expect(result.current.projectsStatus).toBe("ok");
  });

  it("restores project+area scope from the URL on load (deep link)", async () => {
    listProjects.mockResolvedValue([{ external_id: "P-0001", name: "A", project_id: "u1" }]);
    listAreasScoped.mockResolvedValue([{ external_id: "A-0001", area_name: "Toa 1", area_id: "au1" }]);

    const { result } = renderHook(() => useProjectScope(), {
      wrapper: wrapper(["/catalog?project=P-0001&area=A-0001"]),
    });

    await waitFor(() => expect(result.current.loadingProjects).toBe(false));
    expect(result.current.projectExternalId).toBe("P-0001");
    expect(result.current.areaExternalId).toBe("A-0001");
    await waitFor(() => expect(listAreasScoped).toHaveBeenCalledWith("P-0001"));
  });

  it("changing the project resets the area (both in state and the URL)", async () => {
    listProjects.mockResolvedValue([
      { external_id: "P-0001", name: "A", project_id: "u1" },
      { external_id: "P-0002", name: "B", project_id: "u2" },
    ]);
    listAreasScoped.mockResolvedValue([]);

    const { result } = renderHook(() => useProjectScope(), {
      wrapper: wrapper(["/catalog?project=P-0001&area=A-0001"]),
    });
    await waitFor(() => expect(result.current.loadingProjects).toBe(false));
    expect(result.current.areaExternalId).toBe("A-0001");

    act(() => result.current.setProjectExternalId("P-0002"));

    await waitFor(() => expect(result.current.projectExternalId).toBe("P-0002"));
    expect(result.current.areaExternalId).toBeNull();
  });

  it("queries areas scoped to the selected project, not a global default", async () => {
    listProjects.mockResolvedValue([{ external_id: "P-0007", name: "X", project_id: "u7" }]);
    listAreasScoped.mockResolvedValue([]);

    renderHook(() => useProjectScope(), { wrapper: wrapper(["/catalog?project=P-0007"]) });

    await waitFor(() => expect(listAreasScoped).toHaveBeenCalledWith("P-0007"));
    expect(listAreasScoped).not.toHaveBeenCalledWith(undefined);
  });

  it("no project selected -> no area query at all, areas stay empty", async () => {
    listProjects.mockResolvedValue([{ external_id: "P-0001", name: "A", project_id: "u1" }]);

    const { result } = renderHook(() => useProjectScope(), { wrapper: wrapper(["/catalog"]) });

    await waitFor(() => expect(result.current.loadingProjects).toBe(false));
    expect(result.current.areas).toEqual([]);
    expect(listAreasScoped).not.toHaveBeenCalled();
  });

  it("marks projectsStatus as unauthorized on a 401/403 (ProjectSelector reads this to show a safe message)", async () => {
    const { ApiError } = await import("../api/client");
    listProjects.mockRejectedValue(new ApiError(403, "forbidden", null));

    const { result } = renderHook(() => useProjectScope(), { wrapper: wrapper(["/catalog"]) });

    await waitFor(() => expect(result.current.projectsStatus).toBe("unauthorized"));
  });
});

// Phase: Dashboard Integration — route-driven project id (used by
// /projects/:externalId/dashboard via AbsorptionDashboard). Must stay fully
// backward compatible with the query-param mode exercised above.
describe("useProjectScope({ projectExternalId }) — route-scoped mode", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("uses the given projectExternalId as the source of truth, ignoring any ?project= in the URL", async () => {
    listProjects.mockResolvedValue([
      { external_id: "P-0001", name: "A", project_id: "u1" },
      { external_id: "P-0002", name: "B", project_id: "u2" },
    ]);
    listAreasScoped.mockResolvedValue([]);

    const { result } = renderHook(() => useProjectScope({ projectExternalId: "P-0002" }), {
      // query string deliberately points at a DIFFERENT project — the route
      // value must win, otherwise the dashboard could load one project's
      // shell around another project's data.
      wrapper: wrapper(["/projects/P-0002/dashboard?project=P-0001"]),
    });

    await waitFor(() => expect(result.current.loadingProjects).toBe(false));
    expect(result.current.projectExternalId).toBe("P-0002");
    expect(result.current.currentProject?.external_id).toBe("P-0002");
    await waitFor(() => expect(listAreasScoped).toHaveBeenCalledWith("P-0002"));
  });

  it("setProjectExternalId is a no-op in route-scoped mode (the page must navigate() instead)", async () => {
    listProjects.mockResolvedValue([{ external_id: "P-0001", name: "A", project_id: "u1" }]);
    listAreasScoped.mockResolvedValue([]);

    const { result } = renderHook(() => useProjectScope({ projectExternalId: "P-0001" }), {
      wrapper: wrapper(["/projects/P-0001/dashboard"]),
    });
    await waitFor(() => expect(result.current.loadingProjects).toBe(false));

    act(() => result.current.setProjectExternalId("P-0002"));

    // Still P-0001 — the hook refused to mutate the query param when the
    // route value is authoritative.
    expect(result.current.projectExternalId).toBe("P-0001");
  });

  it("area selection (?area=) still works normally alongside a route-scoped project", async () => {
    listProjects.mockResolvedValue([{ external_id: "P-0001", name: "A", project_id: "u1" }]);
    listAreasScoped.mockResolvedValue([{ external_id: "A-0001", area_name: "Toa 1", area_id: "au1", total_units: 10 }]);

    const { result } = renderHook(() => useProjectScope({ projectExternalId: "P-0001" }), {
      wrapper: wrapper(["/projects/P-0001/dashboard?area=A-0001"]),
    });

    await waitFor(() => expect(result.current.loadingProjects).toBe(false));
    expect(result.current.areaExternalId).toBe("A-0001");
    await waitFor(() => expect(result.current.currentArea?.external_id).toBe("A-0001"));
  });

  it("calling the hook with no options at all (CatalogPage's existing usage) is unaffected by the new option", async () => {
    listProjects.mockResolvedValue([{ external_id: "P-0001", name: "A", project_id: "u1" }]);
    listAreasScoped.mockResolvedValue([]);

    const { result } = renderHook(() => useProjectScope(), { wrapper: wrapper(["/catalog?project=P-0001"]) });

    await waitFor(() => expect(result.current.loadingProjects).toBe(false));
    expect(result.current.projectExternalId).toBe("P-0001");

    act(() => result.current.setProjectExternalId("P-0002")); // must NOT be a no-op here
    await waitFor(() => expect(result.current.projectExternalId).toBe("P-0002"));
  });
});
