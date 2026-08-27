import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import LoginPage from "./LoginPage";
import { setAccessToken } from "../api/client";

vi.mock("../hooks/useBreakpoint", () => ({
  useBreakpoint: () => ({ isNarrow: false }),
}));

vi.mock("../api/auth", () => ({
  fetchMe: vi.fn(),
  startLogin: vi.fn(),
}));

import { fetchMe, startLogin } from "../api/auth";

function renderAt(path = "/login", { from } = {}) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state: from ? { from: { pathname: from } } : undefined }]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/overview" element={<div>Overview page</div>} />
        <Route path="/ranking" element={<div>Ranking page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  fetchMe.mockResolvedValue(null);
});

afterEach(() => setAccessToken(null));

describe("LoginPage", () => {
  it("restores an existing SSO session and enters the app", async () => {
    fetchMe.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    renderAt();

    expect(await screen.findByText("Overview page")).toBeInTheDocument();
  });

  it("starts Keycloak SSO for the originally requested route", async () => {
    renderAt("/login", { from: "/ranking" });

    fireEvent.click(screen.getByRole("button", { name: "Đăng nhập SSO" }));

    expect(startLogin).toHaveBeenCalledWith("/ranking");
  });

  it("does not render the legacy token form by default", () => {
    renderAt();

    expect(screen.queryByPlaceholderText("Dán token vai trò được cấp")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Đăng nhập SSO" })).toBeInTheDocument();
  });

  it("stays on login when no SSO session exists", async () => {
    renderAt();

    expect(await screen.findByRole("button", { name: "Đăng nhập SSO" })).toBeInTheDocument();
    expect(fetchMe).toHaveBeenCalledOnce();
  });
});
