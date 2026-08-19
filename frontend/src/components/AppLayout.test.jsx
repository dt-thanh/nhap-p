import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AppLayout from "./AppLayout";

describe("AppLayout integration shell", () => {
  it("mounts connection controls and exposes Catalog plus the integrated AI advisory route", () => {
    render(
      <MemoryRouter initialEntries={["/catalog"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/catalog" element={<div>Catalog page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Danh mục" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kết nối" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AI tư vấn" })).toHaveAttribute("href", "/ai-agent");
    expect(screen.getByText("Catalog page")).toBeInTheDocument();
  });
});
