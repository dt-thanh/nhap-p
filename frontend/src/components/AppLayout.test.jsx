import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AppLayout from "./AppLayout";

const NAV_ITEMS = [
  ["Giỏ hàng", "/inventory"],
  ["AI tư vấn", "/ai-agent"],
  ["Xếp hạng", "/ranking"],
  ["Nhật ký", "/audit"],
  ["Dự án", "/projects"],
  ["Danh mục", "/catalog"],
  ["Nạp dữ liệu", "/import"],
];

function setViewport(width) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
}

function renderShell(path = "/catalog") {
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
    renderShell();

    const links = within(screen.getByRole("navigation", { name: "Điều hướng chính" })).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual(NAV_ITEMS);
  });

  it("marks the current route with aria-current and renders the outlet", () => {
    renderShell("/catalog");

    expect(screen.getByRole("link", { name: "Danh mục" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dự án" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: "Kết nối" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AI tư vấn" })).toHaveAttribute("href", "/ai-agent");
    expect(screen.getByRole("button", { name: "Mở trò chuyện với AI" })).toBeInTheDocument();
    expect(screen.getByText("Outlet content")).toBeInTheDocument();
  });

  it("exposes Ranking at the staging route and marks it active", () => {
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

  it("keeps Data Import active for the upload route", () => {
    renderShell("/import/upload");
    expect(screen.getByRole("link", { name: "Nạp dữ liệu" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  it("opens and closes the mobile drawer with the overlay and Escape", () => {
    setViewport(500);
    renderShell("/catalog");

    const toggle = screen.getByRole("button", { name: "Mở menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Đóng menu" })).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Đóng thanh điều hướng" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("closes the mobile drawer after navigation", async () => {
    setViewport(500);
    renderShell("/catalog");

    const toggle = screen.getByRole("button", { name: "Mở menu" });
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("link", { name: "Dự án" }));

    await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "false"));
  });

  it("mounts connection controls and renders the Catalog outlet", () => {
    render(
      <MemoryRouter initialEntries={["/catalog"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/catalog" element={<div>Catalog page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "Kết nối" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mở trò chuyện với AI" })).toBeInTheDocument();
    expect(screen.getByText("Catalog page")).toBeInTheDocument();
  });
});
