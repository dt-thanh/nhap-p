// Phase: Dashboard Integration — "API response normalization" for
// getDashboardSummary / getDashboardTrend / getDashboardAreas / getDataQuality.
// Mocks `fetch` directly (same convention as client.test.js) so the real
// composition/normalization logic in endpoints.js runs end-to-end at the HTTP
// boundary, not a re-mocked internal call.
import { describe, it, expect, beforeEach, vi } from "vitest";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("getDashboardSummary", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("derives total_units from Σ areas[].total_units, NOT from summary.units_sold + units_remaining", async () => {
    const { getDashboardSummary } = await import("./endpoints");
    fetch.mockResolvedValue(
      jsonResponse({
        units_remaining: 4,
        units_sold: 6,
        avg_velocity_30d: "0.75",
        updated_at: "2026-01-10T00:00:00Z",
        units_reserved: null,
        calculator: "legacy_aggregate",
        last_successful_sync: "2026-01-09T00:00:00Z",
        last_attempted_sync: "2026-01-09T00:00:00Z",
        last_sync_status: "completed",
      }),
    );
    const areas = [{ total_units: 8 }, { total_units: 12 }];

    const result = await getDashboardSummary({ projectId: "proj-uuid", areas, calculator: "legacy_aggregate" });

    expect(result.total_units).toBe(20); // 8 + 12, not 6 + 4
    expect(result.units_sold).toBe(6);
    expect(result.remaining_units).toBe(4);
    expect(result.absorption_rate).toBeCloseTo(30); // 6 / 20 * 100
  });

  it("converts the Decimal-as-string avg_velocity_30d to a real number", async () => {
    const { getDashboardSummary } = await import("./endpoints");
    fetch.mockResolvedValue(
      jsonResponse({ units_remaining: 0, units_sold: 0, avg_velocity_30d: "1.428571", calculator: "legacy_aggregate" }),
    );

    const result = await getDashboardSummary({ projectId: "proj-uuid", areas: [] });

    expect(result.avg_velocity).toBeCloseTo(1.428571);
    expect(typeof result.avg_velocity).toBe("number");
  });

  it("absorption_rate is null (not 0 or NaN) when there are no areas to sum", async () => {
    const { getDashboardSummary } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({ units_remaining: 0, units_sold: 5, avg_velocity_30d: "0", calculator: "legacy_aggregate" }));

    const result = await getDashboardSummary({ projectId: "proj-uuid", areas: [] });

    expect(result.total_units).toBe(0);
    expect(result.absorption_rate).toBeNull();
  });

  it("passes project_id and calculator as query params", async () => {
    const { getDashboardSummary } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({ units_remaining: 0, units_sold: 0, avg_velocity_30d: "0", calculator: "domain_units_deals" }));

    await getDashboardSummary({ projectId: "abc-123", areas: [], calculator: "domain_units_deals" });

    const [url] = fetch.mock.calls[0];
    expect(url).toContain("/absorption/summary");
    expect(url).toContain("project_id=abc-123");
    expect(url).toContain("calculator=domain_units_deals");
  });

  it("normalizes scoped summary metrics and converts daily velocity to weekly KPI units", async () => {
    const { getDashboardSummary } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({
      total_units: 100,
      units_remaining: 20,
      available_remaining_units: 12,
      units_reserved: 8,
      units_sold: 80,
      sell_through: "80.0",
      velocity_7d: "1.5",
      velocity_30d: "2.0",
      estimated_weeks_to_sell_out: "1.4286",
      avg_velocity_30d: "2.0",
      calculator: "legacy_aggregate",
    }));

    const result = await getDashboardSummary({ projectId: "project-1", areaId: "area-1", areas: [{ area_id: "area-1", total_units: 100 }] });

    expect(result).toMatchObject({
      total_units: 100,
      units_sold: 80,
      remaining_units: 20,
      sell_through: 80,
      velocity_7d: 10.5,
      velocity_30d: 14,
      estimated_weeks_to_sell_out: 1.4286,
    });
    expect(result.available_remaining_units).toBe(12);
    expect(result.reserved_units).toBe(8);
    expect(fetch.mock.calls[0][0]).toContain("area_id=area-1");
  });

  it("keeps sell-out forecast unavailable when velocity is zero or missing", async () => {
    const { getDashboardSummary } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({
      total_units: 100,
      units_remaining: 20,
      units_sold: 80,
      velocity_30d: "0",
      estimated_weeks_to_sell_out: null,
      calculator: "legacy_aggregate",
    }));

    const result = await getDashboardSummary({ projectId: "project-1", areaId: "area-1" });
    expect(result.estimated_weeks_to_sell_out).toBeNull();
    expect(Number.isFinite(result.estimated_weeks_to_sell_out)).toBe(false);
  });

  it("preserves domain weekly velocity and historical metadata without legacy conversion", async () => {
    const { getDashboardSummary } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({
      total_units: 10,
      units_remaining: 4,
      units_sold: 6,
      velocity_7d: "2",
      velocity_30d: "1.4",
      estimated_weeks_to_sell_out: "2.8571",
      calculator: "domain_units_deals",
      data_source: "domain_units_deals",
      velocity_unit: "units_per_week",
      available_years: [2024, 2025],
    }));

    const result = await getDashboardSummary({ projectId: "project-1" });

    expect(result.velocity_7d).toBe(2);
    expect(result.velocity_30d).toBe(1.4);
    expect(result.available_years).toEqual([2024, 2025]);
    expect(result.data_source).toBe("domain_units_deals");
  });
});

describe("getMarketDashboard", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("requests the live project snapshot by external project ID", async () => {
    const { getMarketDashboard } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({ project: { live_units: 1 }, metrics: { active_total: 1 } }));

    const result = await getMarketDashboard("P-0001");

    expect(result.project.live_units).toBe(1);
    expect(result.metrics.active_total).toBe(1);
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/market/dashboard?project_id=P-0001",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});

describe("getDashboardTrend", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("returns an empty, well-shaped result when no areaId is given (no fetch made)", async () => {
    const { getDashboardTrend } = await import("./endpoints");

    const result = await getDashboardTrend({ areaId: null, from: "2026-01-01", to: "2026-01-31" });

    expect(result).toEqual({ points: [], latestVelocity7d: null, latestVelocity30d: null });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("computes cumulative_sold as a running sum ordered by date, and absorption_rate from areaTotalUnits", async () => {
    const { getDashboardTrend } = await import("./endpoints");
    fetch.mockResolvedValue(
      jsonResponse({
        area_id: "area-1",
        granularity: "day",
        points: [
          { stat_date: "2026-01-02", units_sold: 3, velocity_7d: "1.0", velocity_30d: "0.5", is_observed: true, data_quality_status: "ok" },
          { stat_date: "2026-01-01", units_sold: 2, velocity_7d: "0.8", velocity_30d: "0.4", is_observed: true, data_quality_status: "ok" },
        ],
      }),
    );

    const result = await getDashboardTrend({ areaId: "area-1", areaTotalUnits: 10, from: "2026-01-01", to: "2026-01-31" });

    // sorted by date ascending regardless of API order
    expect(result.points.map((p) => p.date)).toEqual(["2026-01-01", "2026-01-02"]);
    expect(result.points[0].cumulative_sold).toBe(2);
    expect(result.points[1].cumulative_sold).toBe(5); // 2 + 3
    expect(result.points[1].absorption_rate).toBeCloseTo(50); // 5 / 10 * 100
    expect(result.latestVelocity7d).toBeCloseTo(1.0);
    expect(result.latestVelocity30d).toBeCloseTo(0.5);
  });

  it("uses the scoped all-time sold total as the baseline for filtered trend ranges", async () => {
    const { getDashboardTrend } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({
      area_id: "area-1",
      granularity: "day",
      points: [
        { stat_date: "2026-01-01", units_sold: 2, velocity_7d: "1", velocity_30d: "1", is_observed: true, data_quality_status: "ok" },
        { stat_date: "2026-01-02", units_sold: 3, velocity_7d: "1", velocity_30d: "1", is_observed: true, data_quality_status: "ok" },
      ],
    }));

    const result = await getDashboardTrend({ areaId: "area-1", areaTotalUnits: 20, totalSold: 10 });
    expect(result.points.map((point) => point.cumulative_sold)).toEqual([7, 10]);
    expect(result.points.at(-1).sell_through).toBeCloseTo(50);
  });

  it("absorption_rate is null per point (not 0) when areaTotalUnits is not known", async () => {
    const { getDashboardTrend } = await import("./endpoints");
    fetch.mockResolvedValue(
      jsonResponse({
        area_id: "area-1",
        granularity: "day",
        points: [{ stat_date: "2026-01-01", units_sold: 1, velocity_7d: "0.5", velocity_30d: "0.5", is_observed: true, data_quality_status: "ok" }],
      }),
    );

    const result = await getDashboardTrend({ areaId: "area-1", areaTotalUnits: null, from: "2026-01-01", to: "2026-01-31" });

    expect(result.points[0].absorption_rate).toBeNull();
  });

  it("keeps zero-unit days numeric and returns null absorption for zero or invalid totals", async () => {
    const { getDashboardTrend } = await import("./endpoints");
    const responseBody = {
        area_id: "area-1",
        granularity: "day",
        points: [
          { stat_date: "2026-01-01", units_sold: 0, velocity_7d: "0", velocity_30d: "0", is_observed: false, data_quality_status: "warning" },
          { stat_date: "2026-01-02", units_sold: "2", velocity_7d: "1", velocity_30d: "1", is_observed: true, data_quality_status: "warning" },
        ],
      };
    fetch.mockImplementation(() => Promise.resolve(jsonResponse(responseBody)));

    const zeroTotal = await getDashboardTrend({ areaId: "area-1", areaTotalUnits: 0 });
    expect(zeroTotal.points.map((point) => point.units_sold)).toEqual([0, 2]);
    expect(zeroTotal.points.map((point) => point.cumulative_sold)).toEqual([0, 2]);
    expect(zeroTotal.points.every((point) => point.absorption_rate === null)).toBe(true);

    const missingTotal = await getDashboardTrend({ areaId: "area-1" });
    expect(missingTotal.points.every((point) => point.absorption_rate === null)).toBe(true);
    expect(missingTotal.points.some((point) => Number.isNaN(point.absorption_rate))).toBe(false);
  });

  it("empty points array -> latestVelocity7d/30d are null, not an error", async () => {
    const { getDashboardTrend } = await import("./endpoints");
    fetch.mockResolvedValue(jsonResponse({ area_id: "area-1", granularity: "day", points: [] }));

    const result = await getDashboardTrend({ areaId: "area-1", areaTotalUnits: 10, from: "2026-01-01", to: "2026-01-31" });

    expect(result.points).toEqual([]);
    expect(result.latestVelocity7d).toBeNull();
    expect(result.latestVelocity30d).toBeNull();
  });
});

describe("getDashboardAreas", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("maps GET /inventory's per-area breakdown into the leaderboard/table shape", async () => {
    const { getDashboardAreas } = await import("./endpoints");
    fetch.mockResolvedValue(
      jsonResponse({
        project_id: "proj-1",
        calculator: "domain_units_deals",
        totals: null,
        areas: [
          { area_id: "a1", area_name: "North Tower", unit_type: "2PN", total_units: 10, units_sold: 4, units_reserved: 1, units_remaining: 5, units_blocked: 0 },
        ],
        units: [],
        anomalies: [],
      }),
    );

    const result = await getDashboardAreas({ externalProjectId: "P-0001" });

    expect(result).toEqual([
      {
        id: "a1",
        name: "North Tower · 2PN",
        total_units: 10,
        sold: 4,
        remaining: 5,
        available_remaining_units: 5,
        reserved_units: 1,
        absorption_rate: 40,
        velocity: null,
        latest_data: null,
        status: undefined,
      },
    ]);
  });

  it("no externalProjectId -> empty array, no fetch", async () => {
    const { getDashboardAreas } = await import("./endpoints");

    const result = await getDashboardAreas({ externalProjectId: null });

    expect(result).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("getDataQuality", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  function mockSummaryAndInventory(summary, inventory) {
    fetch.mockImplementation((url) => {
      if (url.includes("/absorption/summary")) return Promise.resolve(jsonResponse(summary));
      if (url.includes("/inventory")) return Promise.resolve(jsonResponse(inventory));
      throw new Error(`unexpected fetch: ${url}`);
    });
  }

  it("status ok when sync succeeded recently and there are no anomalies", async () => {
    const { getDataQuality } = await import("./endpoints");
    const now = new Date().toISOString();
    mockSummaryAndInventory(
      { units_remaining: 0, units_sold: 0, avg_velocity_30d: "0", calculator: "legacy_aggregate", last_successful_sync: now, last_attempted_sync: now, last_sync_status: "completed" },
      { project_id: "p1", calculator: "domain_units_deals", areas: [], units: [], anomalies: [] },
    );

    const result = await getDataQuality({ projectId: "p1", externalProjectId: "P-0001" });

    expect(result.status).toBe("ok");
    expect(result.error_records).toBe(0);
    expect(result.warnings).toEqual([]);
  });

  it("status error when the last sync failed", async () => {
    const { getDataQuality } = await import("./endpoints");
    const now = new Date().toISOString();
    mockSummaryAndInventory(
      { units_remaining: 0, units_sold: 0, avg_velocity_30d: "0", calculator: "legacy_aggregate", last_successful_sync: null, last_attempted_sync: now, last_sync_status: "failed" },
      { project_id: "p1", calculator: "domain_units_deals", areas: [], units: [], anomalies: [] },
    );

    const result = await getDataQuality({ projectId: "p1", externalProjectId: "P-0001" });

    expect(result.status).toBe("error");
  });

  it("status ok_with_warnings when there are reconciliation anomalies, with readable labels", async () => {
    const { getDataQuality } = await import("./endpoints");
    const now = new Date().toISOString();
    mockSummaryAndInventory(
      { units_remaining: 0, units_sold: 0, avg_velocity_30d: "0", calculator: "legacy_aggregate", last_successful_sync: now, last_attempted_sync: now, last_sync_status: "completed" },
      {
        project_id: "p1",
        calculator: "domain_units_deals",
        areas: [],
        units: [],
        anomalies: [{ code: "HELD_EXCEEDS_STOCK", area_id: "a1", sellable: 5, sold: 4, reserved: 3 }],
      },
    );

    const result = await getDataQuality({ projectId: "p1", externalProjectId: "P-0001" });

    expect(result.status).toBe("ok_with_warnings");
    expect(result.error_records).toBe(1);
    expect(result.warnings[0]).toContain("tồn kho khả bán");
    expect(result.warnings[0]).toContain("a1");
  });

  it("an unrecognized anomaly code is still surfaced (not silently dropped)", async () => {
    const { getDataQuality } = await import("./endpoints");
    const now = new Date().toISOString();
    mockSummaryAndInventory(
      { units_remaining: 0, units_sold: 0, avg_velocity_30d: "0", calculator: "legacy_aggregate", last_successful_sync: now, last_attempted_sync: now, last_sync_status: "completed" },
      { project_id: "p1", calculator: "domain_units_deals", areas: [], units: [], anomalies: [{ code: "SOME_FUTURE_CODE" }] },
    );

    const result = await getDataQuality({ projectId: "p1", externalProjectId: "P-0001" });

    expect(result.warnings[0]).toContain("SOME_FUTURE_CODE");
  });

  it("date_range echoes the requested from/to, and is null when not given", async () => {
    const { getDataQuality } = await import("./endpoints");
    mockSummaryAndInventory(
      { units_remaining: 0, units_sold: 0, avg_velocity_30d: "0", calculator: "legacy_aggregate" },
      { project_id: "p1", calculator: "domain_units_deals", areas: [], units: [], anomalies: [] },
    );

    const withRange = await getDataQuality({ projectId: "p1", externalProjectId: "P-0001", from: "2026-01-01", to: "2026-01-31" });
    expect(withRange.date_range).toEqual({ from: "2026-01-01", to: "2026-01-31" });

    const withoutRange = await getDataQuality({ projectId: "p1", externalProjectId: "P-0001" });
    expect(withoutRange.date_range).toBeNull();
  });
});
