// frontend/src/api/mock.js
// ---------------------------------------------------------------------------
// BACKEND GIẢ (mock) — trả về dữ liệu ĐÚNG SCHEMA của SRS.
// Khi backend thật xong: mở client.js, đổi USE_MOCK = false.
//
// CHẶNG 3 bổ sung: upload file + giả lập quá trình parse chuyển trạng thái
// pending -> parsing -> done/failed theo thời gian thật, để bạn kiểm thử được
// luồng polling 3 giây của UploadPage.
// ---------------------------------------------------------------------------

const delay = (ms = 350) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// DỰ ÁN và PHÂN KHU (cho luồng chọn ngữ cảnh trước khi nạp dữ liệu)
// Một chủ đầu tư có nhiều dự án; mỗi dự án có nhiều phân khu.
// ---------------------------------------------------------------------------
const PROJECTS = [
  { id: "p1", name: "Vinhomes Ocean Park", location: "Gia Lâm, Hà Nội", zone_count: 6, total_units: 1200, sold_pct: 58, status: "active" },
  { id: "p2", name: "Masteri Waterfront",  location: "Gia Lâm, Hà Nội", zone_count: 4, total_units: 800,  sold_pct: 72, status: "active" },
  { id: "p3", name: "Vinhomes Smart City", location: "Nam Từ Liêm, Hà Nội", zone_count: 8, total_units: 2000, sold_pct: 41, status: "active" },
];

const ZONES = {
  p1: [
    { id: "p1z1", name: "Phân khu S1", total_units: 320, units_remaining: 96,  status: "hot" },
    { id: "p1z2", name: "Phân khu S2", total_units: 280, units_remaining: 150, status: "normal" },
    { id: "p1z3", name: "Phân khu The Zurich", total_units: 240, units_remaining: 38, status: "hot" },
    { id: "p1z4", name: "Phân khu San Hô", total_units: 360, units_remaining: 220, status: "slow" },
  ],
  p2: [
    { id: "p2z1", name: "Toà M1", total_units: 200, units_remaining: 40, status: "hot" },
    { id: "p2z2", name: "Toà M2", total_units: 200, units_remaining: 132, status: "slow" },
    { id: "p2z3", name: "Toà M3", total_units: 200, units_remaining: 60, status: "normal" },
  ],
  p3: [
    { id: "p3z1", name: "Phân khu Sapphire", total_units: 500, units_remaining: 210, status: "normal" },
    { id: "p3z2", name: "Phân khu Ruby", total_units: 480, units_remaining: 300, status: "slow" },
    { id: "p3z3", name: "Phân khu The Miami", total_units: 520, units_remaining: 120, status: "hot" },
  ],
};

// ---- Dữ liệu phân khu mẫu: 1 bán chạy / 1 bán chậm / 1 nhiễu
const AREAS = [
  { id: "a1", area_name: "Phân khu A", unit_type: "2PN", bedrooms: 2, area_sqm: 61, total_units: 120, units_remaining: 34 },
  { id: "a2", area_name: "Phân khu A", unit_type: "3PN", bedrooms: 3, area_sqm: 86, total_units: 80,  units_remaining: 61 },
  { id: "a3", area_name: "Phân khu C", unit_type: "Studio", bedrooms: 1, area_sqm: 31, total_units: 50, units_remaining: 44 },
];

function makeSeries(areaId, days = 90) {
  const base = { a1: 4.2, a2: 0.6, a3: 2.1 }[areaId] ?? 2;
  const out = [];
  for (let i = days; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86400000);
    const wobble = Math.sin(i / 7) * base * 0.25 + (Math.random() - 0.5) * base * 0.2;
    const v7 = Math.max(0, +(base + wobble).toFixed(2));
    out.push({
      stat_date: d.toISOString().slice(0, 10),
      units_sold: Math.max(0, Math.round(v7 / 7 + (Math.random() < 0.3 ? 0 : 0.5))),
      velocity_7d: v7,
      velocity_30d: +(base + wobble * 0.4).toFixed(2),
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// KHO FILE UPLOAD (giữ trong bộ nhớ trình duyệt, mất khi F5 — đủ để thử luồng)
// ---------------------------------------------------------------------------
let fileSeq = 100;
const FILES = [
  {
    id: "f1", filename: "ban_hang_thang_6.xlsx", status: "done",
    rows_ok: 1240, rows_failed: 0,
    uploaded_at: new Date(Date.now() - 86400000).toISOString(),
    _startedAt: 0,
  },
];

// Lỗi validate mẫu — khớp ValidationService trong SRS §5.2
const SAMPLE_ERRORS = [
  { row_number: 14,  column_name: "sold_date",   error_code: "INVALID_DATE",   message: "Sai định dạng ngày, cần YYYY-MM-DD" },
  { row_number: 27,  column_name: "units_sold",  error_code: "NEGATIVE_VALUE", message: "Số căn bán không được âm" },
  { row_number: 58,  column_name: "area_name",   error_code: "UNKNOWN_AREA",   message: "Phân khu không tồn tại trong hệ thống" },
  { row_number: 102, column_name: "units_sold",  error_code: "MISSING_FIELD",  message: "Thiếu giá trị bắt buộc" },
  { row_number: 133, column_name: "sold_date",   error_code: "DUPLICATE_ROW",  message: "Trùng khoá (phân khu, ngày) với dòng 131" },
];

/** Giả lập tiến trình parse: 0–2s pending, 2–6s parsing, sau 6s done. */
function computeFileState(f) {
  if (!f._startedAt) return f;                    // file cũ, đã xong sẵn
  const elapsed = Date.now() - f._startedAt;
  if (elapsed < 2000) return { ...f, status: "pending",  rows_ok: 0, rows_failed: 0 };
  if (elapsed < 6000) {
    const pct = (elapsed - 2000) / 4000;
    return { ...f, status: "parsing", rows_ok: Math.round(1315 * pct), rows_failed: Math.round(5 * pct) };
  }
  return { ...f, status: "done", rows_ok: 1310, rows_failed: 5 };
}

// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// DỮ LIỆU DASHBOARD (KPI, trend 3 series, so sánh area, data quality)
// Có CHỦ Ý để vài trường = null để minh hoạ trạng thái "N/A" (missing values).
// ---------------------------------------------------------------------------
const DASH_AREAS = [
  { id: "a1", name: "Sapphire", total_units: 500, sold: 374, remaining: 126, absorption_rate: 74.8, velocity: 4.2, latest_data: "2026-08-05", status: "hot" },
  { id: "a2", name: "Ruby",     total_units: 480, sold: 293, remaining: 187, absorption_rate: 61.0, velocity: 2.1, latest_data: "2026-08-05", status: "normal" },
  { id: "a3", name: "Diamond",  total_units: 520, sold: 203, remaining: 317, absorption_rate: 39.0, velocity: 0.6, latest_data: "2026-08-04", status: "slow" },
  { id: "a4", name: "The Miami (mới mở)", total_units: 300, sold: 12, remaining: 288, absorption_rate: 4.0, velocity: null, latest_data: "2026-08-02", status: "new" },
];

function dashSummary() {
  const total = DASH_AREAS.reduce((s, a) => s + a.total_units, 0);
  const sold = DASH_AREAS.reduce((s, a) => s + a.sold, 0);
  const vels = DASH_AREAS.map(a => a.velocity).filter(v => v != null);
  const avgVel = vels.length ? +(vels.reduce((s, v) => s + v, 0) / vels.length).toFixed(1) : null;
  return {
    total_units: total,
    units_sold: sold,
    remaining_units: total - sold,
    absorption_rate: +((sold / total) * 100).toFixed(1),
    avg_velocity: avgVel,
    updated_at: new Date().toISOString(),
  };
}

function dashTrend(days = 90) {
  const TOTAL = 1800;
  const out = [];
  let cum = 900;
  for (let i = days; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86400000);
    const sold = Math.max(0, Math.round(6 + Math.sin(i / 8) * 4 + (Math.random() - 0.5) * 3));
    cum = Math.min(TOTAL, cum + sold);
    out.push({
      date: d.toISOString().slice(0, 10),
      units_sold: sold,
      cumulative_sold: cum,
      absorption_rate: +Math.min(100, (cum / TOTAL) * 100).toFixed(1),
    });
  }
  return out;
}

// Xếp hạng khả năng bán từng căn (MINH HOẠ). AI thay bằng model thật.
function unitRanking(areaId) {
  const seed = { a1: 4, a2: 2, a3: 1, all: 3 }[areaId] ?? 3;
  const types = ["2PN", "3PN", "Studio", "1PN"];
  const out = [];
  for (let i = 0; i < 12; i++) {
    const score = Math.max(8, Math.min(98, Math.round(95 - i * 6 + Math.sin(i + seed) * 6 + (Math.random() - 0.5) * 6)));
    const floor = 18 - Math.floor(i / 3);
    out.push({
      unit_code: `A-${floor}.${String((i % 12) + 1).padStart(2, "0")}`,
      unit_type: types[i % types.length],
      area_sqm: [31, 58, 61, 84, 86][i % 5],
      score,
      band: score >= 75 ? "high" : score >= 50 ? "medium" : "low",
    });
  }
  return out.sort((a, b) => b.score - a.score);
}

function dataQuality() {
  return {
    latest_data: "2026-08-05",
    source: "Dữ liệu chuẩn hoá (canonical)",
    date_range: { from: "2025-05-01", to: "2026-08-05" },
    error_records: 5,
    status: "ok_with_warnings",
    warnings: [
      "Phân khu 'The Miami' mới mở, chưa đủ dữ liệu để tính vận tốc.",
      "5 dòng bị loại do sai định dạng ngày.",
    ],
  };
}

// Scoped dashboard mock contract. Keep this shape aligned with the real
// `/v1/projects`, `/v1/areas`, `/v1/inventory`, and `/v1/absorption` routes so
// switching USE_MOCK does not hide scope or metric-contract regressions.
const MOCK_PROJECTS = [
  { project_id: "p1", external_id: "P-0001", name: "Vinhomes Ocean Park", launch_date: "2025-06-01", status: "active" },
  { project_id: "p2", external_id: "P-0002", name: "Masteri Waterfront", launch_date: "2025-07-01", status: "active" },
];

const MOCK_SCOPED_AREAS = [
  { area_id: "a1", external_id: "A-0001", project_external_id: "P-0001", area_name: "Phân khu S1", unit_type: "2PN", bedrooms: 2, area_sqm: 61, total_units: 120, units_remaining: 34 },
  { area_id: "a2", external_id: "A-0002", project_external_id: "P-0001", area_name: "Phân khu S2", unit_type: "3PN", bedrooms: 3, area_sqm: 86, total_units: 80, units_remaining: 61 },
  { area_id: "a3", external_id: "A-0003", project_external_id: "P-0002", area_name: "Toà M1", unit_type: "2PN", bedrooms: 2, area_sqm: 61, total_units: 200, units_remaining: 40 },
];

function mockScopedAreas(projectExternalId) {
  return MOCK_SCOPED_AREAS.filter((area) => area.project_external_id === projectExternalId);
}

function mockSummary(path) {
  const params = new URLSearchParams(path.split("?")[1] || "");
  const selected = MOCK_SCOPED_AREAS.find((area) => area.area_id === params.get("area_id"));
  const areas = selected ? [selected] : mockScopedAreas(MOCK_PROJECTS.find((project) => project.project_id === params.get("project_id"))?.external_id);
  const total = areas.reduce((sum, area) => sum + area.total_units, 0);
  const remaining = areas.reduce((sum, area) => sum + area.units_remaining, 0);
  const sold = total - remaining;
  const points = selected ? makeSeries(selected.area_id) : [];
  const latest = points.at(-1);
  const velocity7d = latest?.velocity_7d ?? null;
  const velocity30d = latest?.velocity_30d ?? null;
  return {
    total_units: total,
    units_remaining: remaining,
    units_sold: sold,
    sell_through: total > 0 ? (sold / total) * 100 : null,
    velocity_7d: velocity7d,
    velocity_30d: velocity30d,
    avg_velocity_30d: velocity30d,
    estimated_weeks_to_sell_out: remaining > 0 && velocity30d > 0 ? remaining / (velocity30d * 7) : null,
    updated_at: new Date().toISOString(),
    calculator: "legacy_aggregate",
  };
}

function mockTrend(path) {
  const params = new URLSearchParams(path.split("?")[1] || "");
  const areaId = params.get("area_id") || "a1";
  return {
    area_id: areaId,
    granularity: params.get("granularity") || "day",
    points: makeSeries(areaId).map((point) => ({
      ...point,
      is_observed: true,
      data_quality_status: "ok",
    })),
  };
}

const routes = [
  { match: (p) => p === "/v1/projects", handler: () => MOCK_PROJECTS },
  {
    match: (p) => p.startsWith("/v1/areas?") && p.includes("external_project_id="),
    handler: (path) => mockScopedAreas(new URLSearchParams(path.split("?")[1] || "").get("external_project_id")),
  },
  {
    match: (p) => p.startsWith("/v1/inventory?"),
    handler: (path) => {
      const projectId = new URLSearchParams(path.split("?")[1] || "").get("external_project_id");
      const areas = mockScopedAreas(projectId);
      return {
        project_id: MOCK_PROJECTS.find((project) => project.external_id === projectId)?.project_id,
        calculator: "domain_units_deals",
        areas: areas.map((area) => ({ ...area, id: area.area_id, name: area.area_name, units_sold: area.total_units - area.units_remaining, absorption_rate: (area.total_units - area.units_remaining) / area.total_units * 100 })),
        anomalies: [],
      };
    },
  },
  { match: (p) => p.startsWith("/v1/absorption/summary"), handler: mockSummary },
  { match: (p) => p.startsWith("/v1/absorption?"), handler: mockTrend },
  { match: (p) => p === "/dashboard/summary", handler: () => dashSummary() },
  { match: (p) => p.startsWith("/dashboard/trend"), handler: () => dashTrend() },
  { match: (p) => p === "/dashboard/areas", handler: () => DASH_AREAS },
  { match: (p) => p === "/dashboard/data-quality", handler: () => dataQuality() },

  { match: (p) => p === "/projects", handler: () => PROJECTS },
  {
    match: (p) => /^\/projects\/[^/]+$/.test(p),
    handler: (path) => {
      const id = path.split("/")[2];
      const p = PROJECTS.find((x) => x.id === id);
      if (!p) { const e = new Error("Không tìm thấy dự án"); e.status = 404; throw e; }
      return { ...p, launch_date: "2025-06-01" };
    },
  },
  {
    // Xếp hạng khả năng bán từng căn trong 1 phân khu (bài toán lõi).
    // AI sẽ thay handler này bằng model thật -> [{ unit_code, unit_type, area_sqm, score, band }]
    match: (p) => /^\/areas\/[^/]+\/ranking$/.test(p),
    handler: (path) => {
      const areaId = path.split("/")[2];
      return unitRanking(areaId);
    },
  },
  {
    match: (p) => /^\/projects\/[^/]+\/zones$/.test(p),
    handler: (path) => {
      const pid = path.split("/")[2];
      return ZONES[pid] || [];
    },
  },
  { match: (p) => p === "/areas", handler: () => AREAS },

  {
    match: (p) => p.startsWith("/absorption/summary"),
    handler: () => ({
      units_remaining: AREAS.reduce((s, a) => s + a.units_remaining, 0),
      units_sold: AREAS.reduce((s, a) => s + (a.total_units - a.units_remaining), 0),
      avg_velocity_30d: 2.3,
      updated_at: new Date().toISOString(),
    }),
  },

  {
    match: (p) => p.startsWith("/absorption"),
    handler: (path) => {
      const areaId = new URLSearchParams(path.split("?")[1] || "").get("area_id") || "a1";
      return makeSeries(areaId);
    },
  },

  // --- Upload ---
  {
    match: (p) => p === "/files/upload",
    method: "POST",
    handler: (_path, { body }) => {
      const file = body instanceof FormData ? body.get("file") : null;
      const name = file?.name || "khong_ro.xlsx";

      // Chặn trùng bằng tên (backend thật dùng checksum SHA-256)
      if (FILES.some((f) => f.filename === name)) {
        const err = new Error("File này đã được nạp trước đó (trùng checksum)");
        err.status = 409;
        throw err;
      }
      const rec = {
        id: `f${++fileSeq}`,
        filename: name,
        status: "pending",
        rows_ok: 0,
        rows_failed: 0,
        uploaded_at: new Date().toISOString(),
        _startedAt: Date.now(),
      };
      FILES.unshift(rec);
      return { file_id: rec.id, status: rec.status };
    },
  },

  {
    match: (p) => /^\/files\/[^/]+\/status$/.test(p),
    handler: (path) => {
      const id = path.split("/")[2];
      const f = FILES.find((x) => x.id === id);
      if (!f) { const e = new Error("Không tìm thấy file"); e.status = 404; throw e; }
      const s = computeFileState(f);
      return { status: s.status, rows_ok: s.rows_ok, rows_failed: s.rows_failed };
    },
  },

  {
    match: (p) => /^\/files\/[^/]+\/errors$/.test(p),
    handler: (path) => {
      const id = path.split("/")[2];
      const f = FILES.find((x) => x.id === id);
      if (!f) return [];
      const s = computeFileState(f);
      return s.rows_failed > 0 ? SAMPLE_ERRORS : [];
    },
  },

  {
    match: (p) => p === "/files",
    handler: () => FILES.map(computeFileState).map(({ _startedAt, ...rest }) => rest),
  },
];

export async function mockFetch(path, { method = "GET", body } = {}) {
  await delay();
  const route = routes.find(
    (r) => r.match(path) && (!r.method || r.method === method)
  );
  if (!route) {
    const err = new Error(`[mock] Chưa khai báo route: ${method} ${path}`);
    err.status = 404;
    throw err;
  }
  return route.handler(path, { method, body });
}
