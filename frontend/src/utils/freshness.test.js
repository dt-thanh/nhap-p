// Phase F/G required test: "timestamp semantic labels",
// "fresh/stale/sync_failed rendering". `classifyFreshness`/`FRESHNESS_LABEL`
// existed trước Phase F/G nhưng chưa từng có test — không có framework nào
// trước đó (xem pipeline_status.md).
import { describe, it, expect } from "vitest";
import { classifyFreshness, FRESHNESS_LABEL, formatBackendTimestamp } from "./freshness";

const NOW = new Date("2026-08-13T12:00:00Z");
const hoursAgo = (h) => new Date(NOW.getTime() - h * 3600 * 1000).toISOString();

describe("classifyFreshness", () => {
  it("null summary -> loading", () => {
    expect(classifyFreshness(null, NOW)).toBe("loading");
  });

  it("no timestamps at all -> timestamp_unknown", () => {
    expect(classifyFreshness({}, NOW)).toBe("timestamp_unknown");
  });

  it("never attempted but has updated_at -> never_synced", () => {
    expect(classifyFreshness({ updated_at: hoursAgo(1) }, NOW)).toBe("never_synced");
  });

  it("last_sync_status=failed -> sync_failed, regardless of age", () => {
    const summary = {
      last_attempted_sync: hoursAgo(1),
      last_successful_sync: hoursAgo(1),
      last_sync_status: "failed",
      updated_at: hoursAgo(1),
    };
    expect(classifyFreshness(summary, NOW)).toBe("sync_failed");
  });

  it("last_sync_status=partially_completed -> sync_failed", () => {
    const summary = { last_attempted_sync: hoursAgo(1), last_sync_status: "partially_completed" };
    expect(classifyFreshness(summary, NOW)).toBe("sync_failed");
  });

  it("attempted but never a successful sync -> never_synced", () => {
    const summary = { last_attempted_sync: hoursAgo(1), last_sync_status: "completed", last_successful_sync: null };
    expect(classifyFreshness(summary, NOW)).toBe("never_synced");
  });

  it("synced recently, calculation caught up -> fresh", () => {
    const summary = {
      last_attempted_sync: hoursAgo(1),
      last_successful_sync: hoursAgo(1),
      last_sync_status: "completed",
      updated_at: hoursAgo(1),
    };
    expect(classifyFreshness(summary, NOW)).toBe("fresh");
  });

  it("synced long ago -> stale", () => {
    const summary = {
      last_attempted_sync: hoursAgo(30),
      last_successful_sync: hoursAgo(30),
      last_sync_status: "completed",
      updated_at: hoursAgo(30),
    };
    expect(classifyFreshness(summary, NOW)).toBe("stale");
  });

  it("synced recently but calculation lags by more than 5 minutes -> calculation_outdated", () => {
    const summary = {
      last_attempted_sync: hoursAgo(1),
      last_successful_sync: hoursAgo(1),
      last_sync_status: "completed",
      updated_at: hoursAgo(2), // tính lại CŨ hơn lần đồng bộ gần nhất
    };
    expect(classifyFreshness(summary, NOW)).toBe("calculation_outdated");
  });
});

describe("FRESHNESS_LABEL", () => {
  it("every classifyFreshness outcome has a label", () => {
    const outcomes = [
      "loading",
      "fresh",
      "stale",
      "sync_failed",
      "never_synced",
      "calculation_outdated",
      "timestamp_unknown",
    ];
    for (const outcome of outcomes) {
      expect(FRESHNESS_LABEL[outcome]).toBeDefined();
      expect(FRESHNESS_LABEL[outcome].text).toBeTruthy();
      expect(FRESHNESS_LABEL[outcome].tone).toBeTruthy();
    }
  });
});

describe("formatBackendTimestamp", () => {
  it("null -> 'Không rõ', never throws or shows a browser-local guess", () => {
    expect(formatBackendTimestamp(null)).toBe("Không rõ");
    expect(formatBackendTimestamp(undefined)).toBe("Không rõ");
  });

  it("a real ISO timestamp formats to a non-empty, different string", () => {
    const out = formatBackendTimestamp("2026-08-09T00:00:00Z");
    expect(out).not.toBe("Không rõ");
    expect(out.length).toBeGreaterThan(0);
  });
});
