// API client auth headers and stable 401/403 handling.
import { describe, it, expect, beforeEach, vi } from "vitest";

describe("api client (backend reads)", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("attaches Authorization: Bearer <token> once setAccessToken is called", async () => {
    const { api, setAccessToken } = await import("./client");
    setAccessToken("test-token-abc");
    fetch.mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));

    await api.get("/v1/projects");

    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBe("Bearer test-token-abc");
  });

  it("does not send an Authorization header when no token is set", async () => {
    const { api } = await import("./client");
    fetch.mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));

    await api.get("/v1/projects");

    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBeUndefined();
  });

  it("401 throws an ApiError that isAuthError recognizes", async () => {
    const { api, isAuthError } = await import("./client");
    fetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: { error_code: "MISSING_CREDENTIALS" } }), { status: 401 }),
    );

    try {
      await api.get("/v1/projects");
      throw new Error("phải ném lỗi");
    } catch (e) {
      expect(e.status).toBe(401);
      expect(isAuthError(e)).toBe(true);
    }
  });

  it("403 throws an ApiError that isAuthError recognizes, with the backend message", async () => {
    const { api, isAuthError } = await import("./client");
    fetch.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { message: "Thao tác nằm ngoài phạm vi", error_code: "PROJECT_OUT_OF_SCOPE" } }),
        { status: 403 },
      ),
    );

    try {
      await api.get("/v1/projects/P-9999");
      throw new Error("phải ném lỗi");
    } catch (e) {
      expect(isAuthError(e)).toBe(true);
      expect(e.status).toBe(403);
      expect(e.message).toBe("Thao tác nằm ngoài phạm vi");
    }
  });

  it("a plain 500 is NOT an auth error", async () => {
    const { api, isAuthError } = await import("./client");
    fetch.mockResolvedValue(new Response(JSON.stringify({ message: "boom" }), { status: 500 }));

    try {
      await api.get("/v1/projects");
      throw new Error("phải ném lỗi");
    } catch (e) {
      expect(isAuthError(e)).toBe(false);
    }
  });
});
