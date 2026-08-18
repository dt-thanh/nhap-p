// Phase F/G required test: "API client auth headers", "401 handling",
// "403 handling", "transport/project/area separation" (đọc backend vs ghi Mini
// CRM là HAI client, HAI token, không rò rỉ sang nhau).
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

describe("minicrm client (Mini CRM writes) — tách riêng khỏi backend reads", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("posts to the same-origin /minicrm-api gateway, not the backend /api base", async () => {
    const { minicrm, setMiniCrmToken } = await import("./client");
    setMiniCrmToken("minicrm-token-xyz");
    fetch.mockResolvedValue(new Response(JSON.stringify({ record: {} }), { status: 201 }));

    await minicrm.post("/areas", { area_name: "A1" });

    const [url, opts] = fetch.mock.calls[0];
    expect(url).toBe("/minicrm-api/areas");
    expect(opts.headers["Authorization"]).toBe("Bearer minicrm-token-xyz");
  });

  it("setAccessToken (backend) does NOT leak into Mini CRM requests", async () => {
    const { minicrm, setAccessToken, setMiniCrmToken } = await import("./client");
    setAccessToken("backend-token");
    setMiniCrmToken(null);
    fetch.mockResolvedValue(new Response(JSON.stringify({ record: {} }), { status: 201 }));

    await minicrm.post("/areas", { area_name: "A1" });

    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBeUndefined();
  });

  it("setMiniCrmToken does NOT leak into backend requests", async () => {
    const { api, setMiniCrmToken, setAccessToken } = await import("./client");
    setMiniCrmToken("minicrm-token-xyz");
    setAccessToken(null);
    fetch.mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));

    await api.get("/v1/projects");

    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBeUndefined();
  });

  it("403 from Mini CRM (scope denied) is recognized by isAuthError", async () => {
    const { minicrm, isAuthError } = await import("./client");
    fetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: { error_code: "PROJECT_OUT_OF_SCOPE" } }), { status: 403 }),
    );
    try {
      await minicrm.post("/areas", { external_project_id: "P-9999" });
      throw new Error("phải ném lỗi");
    } catch (e) {
      expect(isAuthError(e)).toBe(true);
    }
  });
});
