import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import LogoutButton from "./LogoutButton";
import { useAuth } from "../hooks/useAuth";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

const USER = { role: "business_viewer", project_scope: "ALL" };

beforeEach(() => vi.resetAllMocks());
afterEach(() => vi.restoreAllMocks());

describe("LogoutButton", () => {
  it("is hidden when there is no authenticated user", () => {
    useAuth.mockReturnValue({ user: null, logout: vi.fn() });

    render(<LogoutButton />);

    expect(screen.queryByRole("button", { name: "Đăng xuất" })).not.toBeInTheDocument();
  });

  it("renders for an authenticated user and prevents duplicate clicks", async () => {
    let resolveLogout;
    const logout = vi.fn(() => new Promise((resolve) => { resolveLogout = resolve; }));
    useAuth.mockReturnValue({ user: USER, logout });

    render(<LogoutButton />);
    const button = screen.getByRole("button", { name: "Đăng xuất" });

    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Đang đăng xuất…" })).toBe(button);

    fireEvent.click(button);
    expect(logout).toHaveBeenCalledOnce();

    await act(async () => resolveLogout());
  });

  it("shows an error and re-enables the control when logout fails", async () => {
    const logout = vi.fn().mockRejectedValue(new Error("network failure"));
    useAuth.mockReturnValue({ user: USER, logout });

    render(<LogoutButton />);
    fireEvent.click(screen.getByRole("button", { name: "Đăng xuất" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Đăng xuất thất bại");
    await waitFor(() => expect(screen.getByRole("button", { name: "Đăng xuất" })).not.toBeDisabled());
  });
});
