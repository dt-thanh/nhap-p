import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ProjectsPage from "./ProjectsPage";
import ImportSelectPage from "./ImportSelectPage";
import UploadPage from "./UploadPage";

vi.mock("../api/endpoints", () => ({
  listProjects: vi.fn(),
  listProjectsForImport: vi.fn(),
  listProjectZones: vi.fn(),
  listFiles: vi.fn(),
  uploadFile: vi.fn(),
  fileStatus: vi.fn(),
  fileErrors: vi.fn(),
}));

import {
  listProjects,
  listProjectsForImport,
  listProjectZones,
  listFiles,
} from "../api/endpoints";

const PROJECTS = [
  { project_id: "uuid-a", external_id: "project-a", name: "Ocean Park 1", launch_date: "2026-01-01", status: "active" },
  { project_id: "uuid-b", external_id: "project-b", name: "Smart City", launch_date: "2026-02-01", status: "archived" },
];

function Destination() {
  const location = useLocation();
  return <div>UPLOAD_ROUTE {location.state?.project?.id} {location.state?.zone?.id}</div>;
}

describe("integration pages", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("filters ProjectsPage by the backend ProjectSummary.status", async () => {
    listProjects.mockResolvedValue(PROJECTS);
    render(
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: /Ocean Park 1/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Lưu trữ" }));

    expect(screen.queryByRole("button", { name: /Ocean Park 1/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Smart City/ })).toBeInTheDocument();
  });

  it("hands the selected project and area to the upload route", async () => {
    listProjectsForImport.mockResolvedValue([{ id: "uuid-b", name: "Smart City", project_id: "uuid-b" }]);
    listProjectZones.mockResolvedValue([{ id: "area-b", name: "Sapphire", total_units: 10, units_remaining: 4 }]);

    render(
      <MemoryRouter initialEntries={["/import"]}>
        <Routes>
          <Route path="/import" element={<ImportSelectPage />} />
          <Route path="/import/upload" element={<Destination />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /Smart City/ }));
    await waitFor(() => expect(listProjectZones).toHaveBeenCalledWith("uuid-b"));
    fireEvent.click(await screen.findByRole("button", { name: /Sapphire/ }));

    expect(await screen.findByText("UPLOAD_ROUTE uuid-b area-b")).toBeInTheDocument();
  });

  it("keeps UploadPage history scoped while switching file/CRM transport tabs", async () => {
    listFiles.mockResolvedValue([]);
    render(
      <MemoryRouter initialEntries={[{ pathname: "/import/upload", state: { project: { id: "uuid-b", name: "Smart City" }, zone: { id: "area-b", name: "Sapphire" } } }]}>
        <Routes>
          <Route path="/import/upload" element={<UploadPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(listFiles).toHaveBeenCalledWith("uuid-b", "file_upload"));
    fireEvent.click(screen.getByRole("tab", { name: "Đồng bộ CRM" }));
    await waitFor(() => expect(listFiles).toHaveBeenLastCalledWith("uuid-b", "api_push"));
  });
});
