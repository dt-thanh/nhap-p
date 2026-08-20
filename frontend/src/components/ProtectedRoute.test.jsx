import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProtectedRoute from "./ProtectedRoute";
import { setAccessToken } from "../api/client";

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>Login page</div>} />
        <Route element={<ProtectedRoute />}>
          <Route path="/overview" element={<div>Overview page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllEnvs();
});

describe("ProtectedRoute", () => {
  it("redirects to /login when there is no access token", () => {
    renderAt("/overview");
    expect(screen.getByText("Login page")).toBeInTheDocument();
    expect(screen.queryByText("Overview page")).not.toBeInTheDocument();
  });

  it("renders the protected route when a token is present", () => {
    setAccessToken("held-token");
    renderAt("/overview");
    expect(screen.getByText("Overview page")).toBeInTheDocument();
  });

  it("skips the login redirect when VITE_DEV_AUTH_BYPASS is explicitly enabled, even without a token", () => {
    vi.stubEnv("VITE_DEV_AUTH_BYPASS", "true");
    renderAt("/overview");
    expect(screen.getByText("Overview page")).toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
  });

  it("still redirects to /login when VITE_DEV_AUTH_BYPASS is set to anything other than \"true\"", () => {
    vi.stubEnv("VITE_DEV_AUTH_BYPASS", "false");
    renderAt("/overview");
    expect(screen.getByText("Login page")).toBeInTheDocument();
  });
});
