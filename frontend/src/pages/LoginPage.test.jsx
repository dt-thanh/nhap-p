import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import LoginPage from "./LoginPage";
import { ApiError, getAccessToken, setAccessToken } from "../api/client";

vi.mock("../hooks/useBreakpoint", () => ({
  useBreakpoint: () => ({ isNarrow: false }),
}));

vi.mock("../api/endpoints", () => ({ getMePermissions: vi.fn() }));

import { getMePermissions } from "../api/endpoints";

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
});

afterEach(() => setAccessToken(null));

describe("LoginPage", () => {
  it("stores the token and enters the app on a valid token", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    renderAt();

    fireEvent.change(screen.getByPlaceholderText("Dán token vai trò được cấp"), { target: { value: "my-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Đăng nhập" }));

    expect(await screen.findByText("Overview page")).toBeInTheDocument();
    expect(getAccessToken()).toBe("my-token");
  });

  it("returns to the originally requested route after login", async () => {
    getMePermissions.mockResolvedValue({ role: "pipeline_operator", project_scope: "ALL" });
    renderAt("/login", { from: "/ranking" });

    fireEvent.change(screen.getByPlaceholderText("Dán token vai trò được cấp"), { target: { value: "my-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Đăng nhập" }));

    expect(await screen.findByText("Ranking page")).toBeInTheDocument();
  });

  it("clears the token and shows an error on an invalid token", async () => {
    getMePermissions.mockRejectedValue(new ApiError(401, "Token không hợp lệ", null));
    renderAt();

    fireEvent.change(screen.getByPlaceholderText("Dán token vai trò được cấp"), { target: { value: "wrong-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Đăng nhập" }));

    expect(await screen.findByText("Token không hợp lệ.")).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
    expect(screen.queryByText("Overview page")).not.toBeInTheDocument();
  });

  it("shows a network error distinct from an invalid token", async () => {
    getMePermissions.mockRejectedValue(new TypeError("Failed to fetch"));
    renderAt();

    fireEvent.change(screen.getByPlaceholderText("Dán token vai trò được cấp"), { target: { value: "any-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Đăng nhập" }));

    expect(await screen.findByText("Không kết nối được máy chủ, thử lại sau.")).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });
});
