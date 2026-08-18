import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="current-path">{location.pathname}</div>;
}

function Destination() {
  const location = useLocation();
  return <div>UPLOAD_ROUTE {location.state?.project?.id} {location.state?.zone?.id}</div>;
}

function renderProjects() {
  return render(
    <MemoryRouter>
      <ProjectsPage />
    </MemoryRouter>,
  );
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

  it("renders unique total, active, and attention KPIs from real project statuses", async () => {
    listProjects.mockResolvedValue([
      { ...PROJECTS[0] },
      { ...PROJECTS[0] },
      { project_id: "uuid-p", external_id: "project-p", name: "Pending Project", status: "pending" },
      { project_id: "uuid-r", external_id: "project-r", name: "Rejected Project", status: "rejected" },
      { project_id: "uuid-u", external_id: "project-u", name: "Unknown Project", status: "unknown" },
    ]);

    renderProjects();

    expect(await screen.findByTestId("kpi-total")).toHaveTextContent("4");
    expect(screen.getByTestId("kpi-active")).toHaveTextContent("1");
    expect(screen.getByTestId("kpi-attention")).toHaveTextContent("2");
    expect(screen.getByTestId("filter-count-all")).toHaveTextContent("(4)");
    expect(screen.getByTestId("filter-count-active")).toHaveTextContent("(1)");
    expect(screen.getByTestId("filter-count-pending")).toHaveTextContent("(1)");
    expect(screen.getByTestId("filter-count-rejected")).toHaveTextContent("(1)");
    expect(screen.getByText("Dự án đang chờ được phê duyệt.")).toBeInTheDocument();
    expect(screen.getByText("Dự án đã bị từ chối.")).toBeInTheDocument();
  });

  it("searches by project name and external_id without fetching again", async () => {
    listProjects.mockResolvedValue([
      { project_id: "uuid-a", external_id: "OP-001", name: "Ocean Park 1", status: "active" },
      { project_id: "uuid-b", external_id: "SC-002", name: "Smart City", status: "active" },
    ]);

    renderProjects();
    expect(await screen.findByRole("button", { name: /Ocean Park 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Smart City/ })).toBeInTheDocument();

    const search = screen.getByRole("textbox", { name: "Tìm kiếm dự án" });
    fireEvent.change(search, { target: { value: "Ocean" } });
    expect(screen.getByRole("button", { name: /Ocean Park 1/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Smart City/ })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "SC-002" } });
    expect(screen.getByRole("button", { name: /Smart City/ })).toBeInTheDocument();
    expect(listProjects).toHaveBeenCalledTimes(1);
  });

  it("renders safe fallbacks for missing identity, date, status, and unavailable metrics", async () => {
    listProjects.mockResolvedValue([
      { project_id: "legacy", external_id: null, name: "Legacy Project", launch_date: null, status: null },
    ]);

    renderProjects();

    const card = await screen.findByRole("button", { name: /Legacy Project/ });
    expect(card).toBeDisabled();
    expect(screen.getByText("Mã dự án: Chưa có dữ liệu")).toBeInTheDocument();
    expect(screen.getByText("Mở bán: Chưa có dữ liệu")).toBeInTheDocument();
    expect(screen.getByText("Phân khu").nextSibling).toHaveTextContent("Chưa có dữ liệu");
    expect(screen.getAllByText("Chưa có dữ liệu").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Xem dashboard" })).not.toBeInTheDocument();
  });

  it("uses a real cover image when supplied and keeps the fallback when it is absent", async () => {
    listProjects.mockResolvedValue([
      { project_id: "uuid-a", external_id: "project-a", name: "With Cover", cover_image_url: "/cover-a.jpg", status: "active" },
      { project_id: "uuid-b", external_id: "project-b", name: "Without Cover", cover_image_url: null, status: "active" },
    ]);
    const { container } = render(
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: /With Cover/ })).toBeInTheDocument();
    expect(container.querySelector('img[src="/cover-a.jpg"]')).toBeInTheDocument();
    expect(container.querySelector('img[src="/cover-b.jpg"]')).not.toBeInTheDocument();
  });

  it("renders the real launch date and navigates the dashboard with external_id", async () => {
    listProjects.mockResolvedValue([
      { project_id: "uuid-a", external_id: "project-a", name: "Ocean Park 1", launch_date: "2026-01-01", status: "active" },
    ]);

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <Routes>
          <Route path="*" element={<><ProjectsPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText((text) => text.startsWith("Mở bán:")).then((element) => {
      expect(element).toHaveTextContent(/2026/);
      return element;
    })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Xem bảng điều khiển" }));
    expect(screen.getByTestId("current-path")).toHaveTextContent("/projects/project-a/dashboard");
    expect(listProjects).toHaveBeenCalledTimes(1);
  });

  it("shows project-card loading skeletons", () => {
    listProjects.mockReturnValue(new Promise(() => {}));
    const loadingView = renderProjects();
    expect(screen.getByTestId("projects-loading")).toBeInTheDocument();
    loadingView.unmount();
  });

  it("shows the project empty state", async () => {
    listProjects.mockResolvedValue([]);
    renderProjects();
    expect(await screen.findByText("Chưa có dự án nào")).toBeInTheDocument();
  });

  it("shows a filtered-empty state and clears filters", async () => {
    listProjects.mockResolvedValue(PROJECTS);
    renderProjects();
    expect(await screen.findByRole("button", { name: /Ocean Park 1/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Chờ duyệt" }));
    await waitFor(() => expect(screen.getByTestId("projects-filtered-empty")).toBeInTheDocument());
    fireEvent.click(within(screen.getByTestId("projects-filtered-empty")).getByRole("button", { name: "Xóa bộ lọc" }));
    expect(screen.getByRole("button", { name: /Ocean Park 1/ })).toBeInTheDocument();
  });

  it("shows an API error and retries through the existing listProjects flow", async () => {
    listProjects.mockRejectedValueOnce(new Error("temporary failure"));
    listProjects.mockResolvedValueOnce(PROJECTS);
    renderProjects();
    const retry = await screen.findByRole("button", { name: "Thử lại" });
    fireEvent.click(retry);
    expect(await screen.findByRole("button", { name: /Ocean Park 1/ })).toBeInTheDocument();
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
