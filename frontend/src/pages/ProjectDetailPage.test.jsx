import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ProjectDetailPage from "./ProjectDetailPage";

vi.mock("../api/endpoints", () => ({
  getProjectByExternalId: vi.fn(),
  listAreasScoped: vi.fn(),
  getDashboardTrend: vi.fn(),
}));

import { getDashboardTrend, getProjectByExternalId, listAreasScoped } from "../api/endpoints";

const PROJECT = {
  project_id: "project-uuid",
  external_id: "project-a",
  name: "Ocean Park 1",
  launch_date: "2026-01-01",
  status: "active",
  headline: "Một dự án thật",
  introduce: "Giới thiệu dự án",
  cover_image_url: null,
  source_revision: 1,
};

const AREAS = [
  {
    area_id: "area-uuid-1",
    external_id: "area-a",
    area_name: "Sapphire 1",
    unit_type: "2PN",
    bedrooms: 2,
    area_sqm: "65",
    total_units: 100,
    units_remaining: 40,
    snapshot_date: "2026-08-15",
    headline: "Sống xanh",
    introduce: "",
    cover_image_url: "/area-a.jpg",
    source_revision: 1,
  },
  {
    area_id: "area-uuid-2",
    external_id: "area-b",
    area_name: "The ZenPark",
    unit_type: "1PN",
    bedrooms: 1,
    area_sqm: "46",
    total_units: 80,
    units_remaining: null,
    snapshot_date: null,
    headline: "",
    introduce: "Khu vực yên tĩnh",
    cover_image_url: null,
    source_revision: 1,
  },
];

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

function renderPage(initialEntries = ["/projects/project-a"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/projects/:externalId" element={<ProjectDetailPage />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function resolvePage(areas = AREAS) {
  getProjectByExternalId.mockResolvedValue(PROJECT);
  listAreasScoped.mockResolvedValue(areas);
  renderPage();
}

describe("ProjectDetailPage area list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders project context, launch date, KPI totals, and real area fields", async () => {
    resolvePage();

    expect(await screen.findByRole("heading", { name: "Ocean Park 1" })).toBeInTheDocument();
    expect(screen.getByText(/Mở bán 1\/1\/2026|Mở bán 01\/01\/2026/)).toBeInTheDocument();
    expect(screen.getByText("Mã dự án").nextSibling).toHaveTextContent("project-a");
    expect(screen.queryByRole("button", { name: "Nạp dữ liệu" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xem bảng điều khiển" })).toBeInTheDocument();

    expect(await screen.findAllByTestId("area-card")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Sapphire 1" })).toBeInTheDocument();
    expect(screen.getByText("2PN")).toBeInTheDocument();
    expect(screen.getByText(/Phòng ngủ: 2/)).toBeInTheDocument();
    expect(screen.getByText(/Diện tích: 65 m²/)).toBeInTheDocument();
    expect(screen.getByText(/Tổng số căn: 100/)).toBeInTheDocument();
    expect(screen.getByText(/Còn lại tổng cộng: 40/)).toBeInTheDocument();
    expect(screen.getByText("Sống xanh")).toBeInTheDocument();
    expect(screen.getByText(/Ngày chốt tồn kho: 15\/8\/2026|Ngày chốt tồn kho: 15\/08\/2026/)).toBeInTheDocument();

    expect(screen.getByText("Tổng phân khu").nextSibling).toHaveTextContent("2");
    expect(screen.getByText("Tổng số căn").nextSibling).toHaveTextContent("180");
    expect(screen.getByText("Còn lại tổng cộng").nextSibling).toHaveTextContent("Chưa có dữ liệu");
  });

  it("uses the supplied cover and a safe fallback for missing cover and values", async () => {
    resolvePage([
      AREAS[0],
      {
        area_id: "legacy-area-uuid",
        external_id: null,
        area_name: null,
        unit_type: null,
        bedrooms: null,
        area_sqm: null,
        total_units: null,
        units_remaining: null,
        snapshot_date: null,
        cover_image_url: null,
      },
    ]);

    expect(await screen.findByTestId("area-cover-fallback")).toBeInTheDocument();
    expect(document.querySelector('img[src="/area-a.jpg"]')).toBeInTheDocument();
    expect(screen.getAllByText("Chưa có dữ liệu").length).toBeGreaterThan(1);
    expect(screen.queryByText("legacy-area-uuid")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bảng điều khiển: Chưa có dữ liệu/ })).toBeDisabled();
  });

  it("searches areas by name and external_id without requesting again", async () => {
    resolvePage();
    expect(await screen.findAllByTestId("area-card")).toHaveLength(2);

    const search = screen.getByRole("textbox", { name: "Tìm kiếm phân khu" });
    fireEvent.change(search, { target: { value: "Sapphire" } });
    expect(screen.getAllByTestId("area-card")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Sapphire 1" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "The ZenPark" })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "area-b" } });
    expect(screen.getByRole("heading", { name: "The ZenPark" })).toBeInTheDocument();
    expect(listAreasScoped).toHaveBeenCalledTimes(1);
  });

  it("shows and clears the filtered-empty state", async () => {
    resolvePage();
    await screen.findAllByTestId("area-card");

    fireEvent.change(screen.getByRole("textbox", { name: "Tìm kiếm phân khu" }), { target: { value: "missing" } });
    const empty = await screen.findByTestId("areas-filtered-empty");
    expect(within(empty).getByText("Không tìm thấy phân khu phù hợp")).toBeInTheDocument();
    fireEvent.click(within(empty).getByRole("button", { name: "Xóa tìm kiếm" }));
    expect(screen.getAllByTestId("area-card")).toHaveLength(2);
  });

  it("uses real external IDs for area and project dashboard navigation", async () => {
    resolvePage();
    await screen.findAllByTestId("area-card");

    expect(screen.getByRole("link", { name: "Mở bảng điều khiển phân khu Sapphire 1" })).toHaveAttribute("href", "/projects/project-a/areas/area-a");
    expect(screen.queryByRole("link", { name: /area-uuid-1/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Xem bảng điều khiển" }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/projects/project-a/dashboard");
  });

  it("keeps the detail page list-only and does not request dashboard data", async () => {
    resolvePage();

    expect(await screen.findAllByTestId("area-card")).toHaveLength(2);
    expect(screen.queryByText("Chọn một phân khu để xem dashboard")).not.toBeInTheDocument();
    expect(screen.queryByText("Absorption Trend")).not.toBeInTheDocument();
    expect(screen.queryByTestId("no-area-selected")).not.toBeInTheDocument();
    expect(getDashboardTrend).not.toHaveBeenCalled();
    expect(listAreasScoped).toHaveBeenCalledTimes(1);
  });

  it("shows loading while the project and area list are being fetched", async () => {
    getProjectByExternalId.mockImplementation(() => new Promise(() => {}));
    renderPage();

    expect(screen.getByTestId("areas-loading")).toBeInTheDocument();
    expect(listAreasScoped).not.toHaveBeenCalled();
  });

  it("shows the empty state without exposing a removed creation page", async () => {
    resolvePage([]);
    const empty = await screen.findByTestId("areas-empty");
    expect(within(empty).getByText("Chưa có phân khu nào được tạo trong dự án này.")).toBeInTheDocument();

    expect(within(empty).queryByRole("button", { name: "Thêm phân khu" })).not.toBeInTheDocument();
  });

  it("shows an area error and retries the same scoped request", async () => {
    getProjectByExternalId.mockResolvedValue(PROJECT);
    listAreasScoped.mockRejectedValueOnce(new Error("area request failed")).mockResolvedValueOnce(AREAS);
    renderPage();

    const error = await screen.findByTestId("areas-error");
    expect(within(error).getByText("Không thể tải danh sách phân khu")).toBeInTheDocument();
    expect(within(error).getByText("Dữ liệu phân khu chưa được cập nhật. Vui lòng thử lại.")).toBeInTheDocument();
    fireEvent.click(within(error).getByRole("button", { name: "Thử lại" }));
    await waitFor(() => expect(screen.getAllByTestId("area-card")).toHaveLength(2));
    expect(listAreasScoped).toHaveBeenCalledTimes(2);
  });

  it("makes one area-list request and never fetches one request per area", async () => {
    resolvePage();
    await screen.findAllByTestId("area-card");

    expect(listAreasScoped).toHaveBeenCalledTimes(1);
    expect(listAreasScoped).toHaveBeenCalledWith("project-a");
  });
});
