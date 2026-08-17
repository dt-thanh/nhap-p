// frontend/src/api/endpoints.js
// ---------------------------------------------------------------------------
// Má»˜T nÆ¡i duy nháº¥t khai bÃ¡o má»i lá»i gá»i API, khá»›p SRS má»¥c 6.
// Backend Ä‘á»•i Ä‘Æ°á»ng dáº«n / tÃªn field -> chá»‰ sá»­a á»ž ÄÃ‚Y, khÃ´ng lá»¥c tá»«ng component.
//
// Cháº·ng nÃ y chá»‰ khai MVP 1. MVP 2 (forecast/alerts) vÃ  MVP 3 (auth/HITL)
// sáº½ bá»• sung sau, khÃ´ng pháº£i viáº¿t láº¡i.
// ---------------------------------------------------------------------------
import { api } from "./client";

// ---------- MVP 1: Data â†’ Dashboard ----------

export const health = () => api.get("/health");

/** Danh sÃ¡ch phÃ¢n khu / loáº¡i cÄƒn.
 *  -> [{ id, area_name, unit_type, bedrooms, area_sqm, total_units, units_remaining }] */
export const listAreas = () => api.get("/areas");

/** Chuá»—i tá»‘c Ä‘á»™ háº¥p thá»¥ theo thá»i gian cá»§a 1 phÃ¢n khu.
 *  -> [{ stat_date, units_sold, velocity_7d, velocity_30d }] */
export function getAbsorption({ areaId, from, to, granularity = "day" }) {
  const q = new URLSearchParams({ area_id: areaId, from, to, granularity });
  return api.get(`/absorption?${q}`);
}

/** Tá»•ng há»£p toÃ n dá»± Ã¡n cho cÃ¡c tháº» sá»‘ liá»‡u.
 *  -> { units_remaining, units_sold, avg_velocity_30d, updated_at } */
export const getAbsorptionSummary = () => api.get("/absorption/summary");

/** Lá»‹ch sá»­ upload file.
 *  -> [{ id, filename, status, rows_ok, rows_failed, uploaded_at }] */
export const listFiles = () => api.get("/files");

/** Upload Excel/CSV (multipart). file: File tá»« <input type="file">. */
export function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  return api.post("/files/upload", fd);
}

/** Tráº¡ng thÃ¡i parse cá»§a 1 file -> { status, rows_ok, rows_failed } */
export const fileStatus = (id) => api.get(`/files/${id}/status`);

/** Lá»—i validate theo dÃ²ng -> [{ row_number, column_name, message }] */
export const fileErrors = (id) => api.get(`/files/${id}/errors`);

// ---------- AI Agent chat (backend ÄÃƒ cÃ³ sáºµn) ----------
/** Gá»­i cÃ¢u há»i tá»›i AI agent.
 *  POST /api/v1/chat  body { message }  ->  { response, analysis }
 *  forceReal: true vÃ¬ endpoint nÃ y backend Ä‘Ã£ cháº¡y tháº­t, khÃ´ng cáº§n mock. */
export const chatWithAgent = (message) =>
  api.post("/v1/chat", { message }, { forceReal: true });

export const getMarketDashboard = () => api.get("/v1/market/dashboard", { forceReal: true });
export const getMarketUnits = () => api.get("/v1/market/units", { forceReal: true });
export const changeMarketPhase = (direction, confirmed) => api.post("/v1/market/phase", { direction, confirmed, actor: "Admin" }, { forceReal: true });
export const getMarketScenarios = () => api.get("/v1/market/scenarios", { forceReal: true });
export const runMarketScenario = (scenario_id, intensity, confirmed) => api.post("/v1/market/scenarios/run", { scenario_id, intensity, confirmed, actor: "Admin" }, { forceReal: true });
export const getMarketProposals = () => api.get("/v1/market/proposals", { forceReal: true });
export const decideMarketProposal = (id, decision, reason, confirmed, unit_ids = []) => api.post(`/v1/market/proposals/${id}/decision`, { decision, reason, confirmed, actor: "Admin", unit_ids }, { forceReal: true });
export const getMarketAudit = () => api.get("/v1/market/audit", { forceReal: true });

// ---------- Chá»n ngá»¯ cáº£nh náº¡p dá»¯ liá»‡u: Dá»± Ã¡n â†’ PhÃ¢n khu ----------
/** Danh sÃ¡ch dá»± Ã¡n cá»§a chá»§ Ä‘áº§u tÆ°.
 *  -> [{ id, name, location, zone_count, total_units, sold_pct, status }] */
export const listProjects = () => api.get("/projects");

/** CÃ¡c phÃ¢n khu trong 1 dá»± Ã¡n.
 *  -> [{ id, name, total_units, units_remaining, status }] */
export const listProjectZones = (projectId) => api.get(`/projects/${projectId}/zones`);

// ---------- Absorption Dashboard (MVP1) ----------
/** KPI tá»•ng há»£p -> { total_units, units_sold, remaining_units, absorption_rate, avg_velocity, updated_at } */
export const getDashboardSummary = ({ projectId, areaId, from, to } = {}) => {
  const q = new URLSearchParams();
  if (projectId) q.set("project_id", projectId);
  if (areaId) q.set("area_id", areaId);
  if (from) q.set("from", from);
  if (to) q.set("to", to);
  return api.get(`/dashboard/summary?${q}`);
};

/** Chuá»—i trend 3 series -> [{ date, units_sold, cumulative_sold, absorption_rate }] */
export const getDashboardTrend = ({ projectId, areaId, from, to } = {}) => {
  const q = new URLSearchParams();
  if (projectId) q.set("project_id", projectId);
  if (areaId) q.set("area_id", areaId);
  if (from) q.set("from", from);
  if (to) q.set("to", to);
  return api.get(`/dashboard/trend?${q}`);
};

/** So sÃ¡nh + báº£ng chi tiáº¿t area -> [{ id, name, total_units, sold, remaining, absorption_rate, velocity, latest_data, status }] */
export const getDashboardAreas = ({ projectId } = {}) =>
  api.get(`/dashboard/areas${projectId ? `?project_id=${projectId}` : ""}`);

/** Cháº¥t lÆ°á»£ng dá»¯ liá»‡u -> { latest_data, source, date_range, error_records, status, warnings } */
export const getDataQuality = ({ projectId } = {}) =>
  api.get(`/dashboard/data-quality${projectId ? `?project_id=${projectId}` : ""}`);

// ---------- Chi tiáº¿t dá»± Ã¡n + Xáº¿p háº¡ng kháº£ nÄƒng bÃ¡n ----------
/** ThÃ´ng tin 1 dá»± Ã¡n -> { id, name, location, zone_count, total_units, sold_pct, status, launch_date } */
export const getProject = (projectId) => api.get(`/projects/${projectId}`);

/** Xáº¿p háº¡ng kháº£ nÄƒng bÃ¡n tá»«ng cÄƒn trong 1 phÃ¢n khu (bÃ i toÃ¡n lÃµi).
 *  -> [{ unit_code, unit_type, area_sqm, score, band }]  band âˆˆ high|medium|low
 *  AI: thay báº±ng model tháº­t; frontend chá»‰ hiá»ƒn thá»‹ score + band. */
export const getUnitRanking = (areaId) => api.get(`/areas/${areaId}/ranking`);
export const getMarketPolicies = () => api.get("/v1/market/policies", { forceReal: true });
export const addMarketPhase = (kind, confirmed) => api.post("/v1/market/phases", { kind, confirmed, actor: "Admin" }, { forceReal: true });
export const generateMarketProposal = (prompt) => api.post("/v1/market/proposals/generate", { prompt, actor: "Admin" }, { forceReal: true });


