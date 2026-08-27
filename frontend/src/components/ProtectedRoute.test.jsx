import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

// Không có token cũ thì ProtectedRoute hỏi `/auth/me` (qua useAuth) trước khi
// quyết định — mặc định coi như chưa có phiên SSO (401), từng test tự override
// khi cần mô phỏng phiên hợp lệ.
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
});

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("ProtectedRoute", () => {
  it("redirects to /login when there is no access token and no SSO session", async () => {
    renderAt("/overview");
    await waitFor(() => expect(screen.getByText("Login page")).toBeInTheDocument());
    expect(screen.queryByText("Overview page")).not.toBeInTheDocument();
  });

  it("renders the protected route when a token is present", () => {
    setAccessToken("held-token");
    renderAt("/overview");
    expect(screen.getByText("Overview page")).toBeInTheDocument();
  });

  it("renders the protected route when an SSO session cookie is valid, even without a legacy token", async () => {
    fetch.mockResolvedValue(new Response(JSON.stringify({ sub: "demo" }), { status: 200 }));
    renderAt("/overview");
    await waitFor(() => expect(screen.getByText("Overview page")).toBeInTheDocument());
  });

  it("skips the login redirect when VITE_DEV_AUTH_BYPASS is explicitly enabled, even without a token", () => {
    vi.stubEnv("VITE_DEV_AUTH_BYPASS", "true");
    renderAt("/overview");
    expect(screen.getByText("Overview page")).toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
  });

  it("still redirects to /login when VITE_DEV_AUTH_BYPASS is set to anything other than \"true\"", async () => {
    vi.stubEnv("VITE_DEV_AUTH_BYPASS", "false");
    renderAt("/overview");
    await waitFor(() => expect(screen.getByText("Login page")).toBeInTheDocument());
  });
});
