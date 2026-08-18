import { describe, it, expect, beforeEach, vi } from "vitest";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("import transport endpoints", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("lists upload history for the selected project and transport mode", async () => {
    const { listFiles } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({ items: [] }));

    await listFiles("project-2", "api_push");

    const [url] = fetch.mock.calls[0];
    expect(url).toContain("/v1/files");
    expect(url).toContain("project_id=project-2");
    expect(url).toContain("transport_mode=api_push");
  });

  it("uploads to the selected project instead of silently falling back to the first project", async () => {
    const { uploadFile } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({ file_id: "file-1" }, 202));

    await uploadFile(new File(["unit_code\nA-01"], "inventory.csv", { type: "text/csv" }), "inventory", "project-2");

    const [, options] = fetch.mock.calls[0];
    expect(options.method).toBe("POST");
    expect(options.body.get("template")).toBe("inventory");
    expect(options.body.get("project_id")).toBe("project-2");
  });
});
