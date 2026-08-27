import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { fetchMe, logout as logoutFn } from "../api/auth";
import { useAuth } from "./useAuth";

vi.mock("../api/auth", () => ({
  fetchMe: vi.fn(),
  logout: vi.fn(),
  startLogin: vi.fn(),
}));

function Harness() {
  const { user, logout } = useAuth();
  return (
    <>
      <output data-testid="auth-state">{user ? "authenticated" : "anonymous"}</output>
      <button type="button" onClick={() => { void logout().catch(() => {}); }}>logout</button>
    </>
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  fetchMe.mockResolvedValue({ role: "admin", project_scope: "ALL" });
  logoutFn.mockResolvedValue(undefined);
});

afterEach(() => vi.restoreAllMocks());

describe("useAuth logout lifecycle", () => {
  it("clears client auth state after the existing logout API resolves", async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated"));

    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("anonymous"));
    expect(logoutFn).toHaveBeenCalledOnce();
  });

  it("keeps auth state when the logout API rejects", async () => {
    logoutFn.mockRejectedValue(new Error("logout failed"));
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated"));

    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(logoutFn).toHaveBeenCalledOnce());
    expect(screen.getByTestId("auth-state")).toHaveTextContent("authenticated");
  });
});
