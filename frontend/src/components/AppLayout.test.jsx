import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AppLayout from "./AppLayout";
import { getMePermissions } from "../api/endpoints";

vi.mock("../api/endpoints", () => ({ getMePermissions: vi.fn() }));

const NAV_ITEMS = [
  ["Tổng quan", "/overview"],
  ["AI Agent", "/ai-agent"],
  ["Xếp hạng", "/ranking"],
  ["Dự án", "/projects"],
];

function setViewport(width) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
}

function renderShell(path = "/projects") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="*" element={<div>Outlet content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => setViewport(1280));

describe("AppLayout integration shell", () => {
  it("renders navigation labels in the required order with unchanged destinations", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    renderShell();

    const links = within(screen.getByRole("navigation", { name: "Điều hướng chính" })).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual(NAV_ITEMS);
    expect(screen.queryByRole("link", { name: "Tồn kho" })).not.toBeInTheDocument();
  });

  it("shows Phân tích cố vấn only for the server-derived Advisor capability", async () => {
    getMePermissions.mockResolvedValue({
      role: "business_viewer",
      capabilities: { advisor_analysis_authoring: true },
    });
    renderShell();
    expect(await screen.findByRole("link", { name: "Phân tích cố vấn" })).toHaveAttribute("href", "/expert-analysis");
  });

  it.each([
    ["viewer", { role: "business_viewer", capabilities: { advisor_analysis_authoring: false } }],
    ["sales", { role: "pipeline_operator", capabilities: { advisor_analysis_authoring: false } }],
    ["CEO", { role: "admin", capabilities: { advisor_analysis_authoring: false, advisor_analysis_review: true } }],
  ])("does not expose Advisor authoring navigation to %s", async (_persona, permissions) => {
    getMePermissions.mockResolvedValue(permissions);
    renderShell();
    await waitFor(() => expect(getMePermissions).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "Phân tích cố vấn" })).not.toBeInTheDocument();
  });

  it("shows the CEO-only Advisor Analysis review navigation from the server capability", async () => {
    getMePermissions.mockResolvedValue({ role: "admin", capabilities: { advisor_analysis_review: true } });
    renderShell();
    expect(await screen.findByRole("link", { name: "Phê duyệt phân tích cố vấn" })).toHaveAttribute("href", "/advisor-analysis/review");
  });

  it("marks only the exact Overview route active", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    renderShell("/overview");

    expect(screen.getByRole("link", { name: "Tổng quan" })).toHaveAttribute("aria-current", "page");
  });

  it("marks the current route with aria-current and renders the outlet", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    renderShell("/projects");

    expect(screen.getByRole("link", { name: "Dự án" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "Nhật ký" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Danh mục" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AI Agent" })).toHaveAttribute("href", "/ai-agent");
    expect(screen.getByRole("button", { name: "Mở trò chuyện với AI" })).toBeInTheDocument();
    expect(screen.getByText("Outlet content")).toBeInTheDocument();
  });

  it("keeps the tablet viewport-bound and scrolls only its main content", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    renderShell();

    const main = screen.getByRole("main");
    const tablet = main.parentElement;
    const shell = tablet.parentElement;
    const header = main.previousElementSibling;

    expect(shell.style.height).toBe("100vh");
    expect(shell).toHaveStyle({ minHeight: 0, overflow: "hidden" });
    expect(tablet.style.height).toBe("calc(100vh - 32px)");
    expect(tablet).toHaveStyle({
      minHeight: 0,
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
    });
    expect(header).toHaveStyle({ flexShrink: 0 });
    expect(main).toHaveStyle({ flex: 1, minHeight: 0, overflowY: "auto" });
  });

  it("exposes Ranking at the staging route and marks it active", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    renderShell("/ranking");

    expect(screen.getByRole("link", { name: "Xếp hạng" })).toHaveAttribute("href", "/ranking");
    expect(screen.getByRole("link", { name: "Xếp hạng" })).toHaveAttribute("aria-current", "page");
  });

  it.each([
    "/projects/project-a",
    "/projects/project-a/dashboard",
    "/projects/project-a/areas/area-a",
  ])("keeps Projects active for nested route %s", (path) => {
    renderShell(path);
    expect(screen.getByRole("link", { name: "Dự án" })).toHaveAttribute("aria-current", "page");
  });

  it("does not expose Data Import in the navigation", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    renderShell("/import/upload");
    expect(screen.queryByRole("link", { name: "Nạp dữ liệu" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  it("opens and closes the mobile drawer with the overlay and Escape", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    setViewport(500);
    renderShell("/projects");

    const toggle = screen.getByRole("button", { name: "Mở menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Đóng menu" })).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByRole("link", { name: "Cài đặt" })).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Đóng thanh điều hướng" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("closes the mobile drawer after navigation", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    setViewport(500);
    renderShell("/projects");

    const toggle = screen.getByRole("button", { name: "Mở menu" });
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("link", { name: "Dự án" }));

    await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "false"));
  });

  it("renders the outlet without connection or deprecated feature controls", () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer" });
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/projects" element={<div>Projects page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.queryByRole("button", { name: "Kết nối" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mở trò chuyện với AI" })).toBeInTheDocument();
    expect(screen.getByText("Projects page")).toBeInTheDocument();
  });

  it("does not expose Settings to an admin principal", async () => {
    getMePermissions.mockResolvedValue({ role: "admin", project_scope: "ALL" });
    renderShell("/settings");
    await waitFor(() => expect(getMePermissions).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "Cài đặt" })).not.toBeInTheDocument();
    expect(screen.queryByText("Cài đặt")).not.toBeInTheDocument();
  });
});
