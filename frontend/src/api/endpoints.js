// frontend/src/api/endpoints.js
// ---------------------------------------------------------------------------
// Một nơi duy nhất khai báo mọi lời gọi API, khớp SRS mục 6.
// Backend đổi đường dẫn / tên field -> chỉ sửa ở đây.
//
// File này cũng là tầng chuyển đổi: backend trả {items: [...]} / {errors: [...]}
// và dùng khóa `file_id`, còn các trang đang dùng mảng phẳng và `id`.
// ---------------------------------------------------------------------------
import { api, minicrm } from "./client";
import { classifyFreshness } from "../utils/freshness";

// Backend gắn router dưới /api/v1; client.js đã gắn sẵn tiền tố /api.
const V1 = "/v1";

// ---------- Phase E: quyền của token hiện tại (chỉ để FE hiển thị) ----------
/** -> { role, project_scope: "ALL" | string[] } */
export const getMePermissions = () => api.get(`${V1}/me/permissions`);

// ---------- Phase F: đọc CÓ PHẠM VI, theo external_id (danh tính Mini CRM) --
/** Phân khu của MỘT dự án cụ thể, theo external_id — khác `listAreas(projectId)`
 *  (UUID nội bộ, dùng cho Dashboard cũ); đây là hàm ProjectSelector/AreaSelector
 *  scoped dùng, vì URL scope lưu external_id chứ không phải UUID nội bộ. */
export const listAreasScoped = (externalProjectId) =>
  api.get(`${V1}/areas?external_project_id=${encodeURIComponent(externalProjectId)}`);

export const getProjectByExternalId = (externalId) =>
  api.get(`${V1}/projects/${encodeURIComponent(externalId)}`);

export const getAreaByExternalId = (externalId) => api.get(`${V1}/areas/${encodeURIComponent(externalId)}`);

export const listInventoryScoped = (externalProjectId, params = {}) =>
  api.get(
    `${V1}/inventory?${new URLSearchParams({ external_project_id: externalProjectId, ...params })}`,
  );

export const listDealsScoped = (externalProjectId, params = {}) =>
  api.get(`${V1}/deals?${new URLSearchParams({ external_project_id: externalProjectId, ...params })}`);

// ---------- Dự án ----------

/** Danh sách dự án -> [{ project_id, name, launch_date }] */
export const listProjects = () => api.get(`${V1}/projects`);

// MVP 1 chỉ có một dự án pilot nên lấy dự án đầu tiên làm phạm vi mặc định.
let _projectPromise = null;

export function activeProjectId() {
  if (!_projectPromise) {
    _projectPromise = listProjects().then((rows) => {
      if (!rows?.length) throw new Error("Chưa có dự án nào trong hệ thống");
      return rows[0].project_id;
    });
  }
  return _projectPromise;
}

/** Xóa cache dự án. */
export function resetActiveProject() {
  _projectPromise = null;
}

// ---------- MVP 1: Data -> Dashboard ----------

export const health = () => api.get("/health", { forceReal: true });

/** Danh sách phân khu / loại căn kèm tồn kho hiện tại.
 *  -> [{ id, area_name, unit_type, bedrooms, area_sqm, total_units, units_remaining }] */
export async function listAreas(projectId) {
  // Không truyền thì dùng dự án mặc định (Dashboard đang gọi kiểu này).
  const target = projectId ?? (await activeProjectId());
  const rows = await api.get(`${V1}/areas?project_id=${target}`);
  return rows.map((row) => ({ ...row, id: row.area_id }));
}

/** Chuỗi tốc độ hấp thụ theo thời gian của một phân khu. */
export async function getAbsorption({
  areaId,
  from,
  to,
  granularity = "day",
}) {
  const q = new URLSearchParams({ area_id: areaId, granularity });
  if (from) q.set("from", from);
  if (to) q.set("to", to);

  const body = await api.get(`${V1}/absorption?${q}`);
  return body.points ?? [];
}

/** Tổng hợp toàn dự án cho các thẻ số liệu. */
export async function getAbsorptionSummary() {
  const projectId = await activeProjectId();
  return api.get(`${V1}/absorption/summary?project_id=${projectId}`);
}

// ---------- Dashboard nghiệp vụ theo dự án (/projects/:externalId/dashboard) -
//
// Bốn hàm dưới đây là TẦNG CHUYỂN ĐỔI cho `AbsorptionDashboard.jsx` và các
// widget con của nó — mỗi hàm gọi đúng route ĐÃ CÓ ở backend (không route nào
// mới), rồi định hình lại kết quả đúng theo những gì widget cần. Component
// KHÔNG tự suy diễn field nào từ payload thô.
//
// `Decimal` ở backend (`velocity_7d`, `velocity_30d`, `avg_velocity_30d`) được
// serialize thành CHUỖI trong JSON (xác nhận bằng
// `AbsorptionPointOut(...).model_dump_json()` — vd `"velocity_7d":"1.428571"`),
// không phải số — mọi nơi dưới đây ép kiểu `Number(...)` tường minh trước khi
// so sánh hay vẽ biểu đồ, không dựa vào ép kiểu ngầm của JS.

function toFiniteNumberOrNull(value) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** KPI toàn dự án cho `KpiCards`.
 *
 * `total_units` KHÔNG đọc được an toàn từ `/absorption/summary` — dưới bộ
 * tính `legacy_aggregate`, `units_sold` đọc từ `sales_records` còn
 * `units_remaining` đọc từ một bảng snapshot RIÊNG (`inventory_snapshots`,
 * xem `src/services/absorption.py:334-346`); cộng hai số đó lại không phải
 * tổng số căn thật. Tổng số căn THẬT lấy từ `Σ AreaOut.total_units` — nơi gọi
 * truyền `areas` (đã có sẵn, từ `useProjectScope`) để không gọi `/areas` lần
 * hai chỉ để cộng một con số.
 *
 * -> { total_units, units_sold, remaining_units, absorption_rate, avg_velocity,
 *      units_reserved, updated_at, calculator,
 *      last_successful_sync, last_attempted_sync, last_sync_status }
 */
export async function getDashboardSummary({ projectId, areas = [], calculator = "legacy_aggregate" }) {
  const summary = await api.get(
    `${V1}/absorption/summary?project_id=${encodeURIComponent(projectId)}&calculator=${encodeURIComponent(calculator)}`,
  );
  const total_units = areas.reduce((sum, a) => sum + (a.total_units || 0), 0);
  const absorption_rate = total_units > 0 ? (summary.units_sold / total_units) * 100 : null;
  return {
    total_units,
    units_sold: summary.units_sold,
    remaining_units: summary.units_remaining,
    absorption_rate,
    avg_velocity: toFiniteNumberOrNull(summary.avg_velocity_30d),
    units_reserved: summary.units_reserved,
    updated_at: summary.updated_at,
    calculator: summary.calculator,
    last_successful_sync: summary.last_successful_sync,
    last_attempted_sync: summary.last_attempted_sync,
    last_sync_status: summary.last_sync_status,
  };
}

/** Chuỗi hấp thụ THEO NGÀY của một phân khu, cho `AbsorptionTrendChart` +
 *  chỉ báo hướng vận tốc (Q2).
 *
 * Backend chỉ trả `units_sold` mỗi điểm (`GET /absorption`), không trả
 * `cumulative_sold`/`absorption_rate` — cả hai được TÍNH LẠI ở đây, không
 * phải backend bịa: `cumulative_sold` là tổng dồn `units_sold` theo thứ tự
 * ngày; `absorption_rate` là `cumulative_sold / areaTotalUnits`, và là `null`
 * (không phải 0) khi không có `areaTotalUnits` — không có mẫu số thì không có
 * tỷ lệ, hiện "N/A" thay vì một con số bịa.
 *
 * -> { points: [{date, units_sold, cumulative_sold, absorption_rate,
 *                velocity_7d, velocity_30d, is_observed, data_quality_status}],
 *      latestVelocity7d, latestVelocity30d }
 */
export async function getDashboardTrend({ areaId, areaTotalUnits = null, from, to, granularity = "day" }) {
  const empty = { points: [], latestVelocity7d: null, latestVelocity30d: null };
  if (!areaId) return empty;

  const rawPoints = await getAbsorption({ areaId, from, to, granularity });
  const sorted = [...rawPoints].sort((a, b) => (a.stat_date < b.stat_date ? -1 : a.stat_date > b.stat_date ? 1 : 0));

  let cumulative = 0;
  const points = sorted.map((point) => {
    cumulative += point.units_sold || 0;
    return {
      date: point.stat_date,
      units_sold: point.units_sold,
      cumulative_sold: cumulative,
      absorption_rate: areaTotalUnits ? (cumulative / areaTotalUnits) * 100 : null,
      velocity_7d: toFiniteNumberOrNull(point.velocity_7d),
      velocity_30d: toFiniteNumberOrNull(point.velocity_30d),
      is_observed: point.is_observed,
      data_quality_status: point.data_quality_status,
    };
  });

  const latest = points[points.length - 1] || null;
  return {
    points,
    latestVelocity7d: latest?.velocity_7d ?? null,
    latestVelocity30d: latest?.velocity_30d ?? null,
  };
}

/** Bảng xếp hạng/chi tiết phân khu cho `AreaComparison` + `AreaDetailTable`.
 *
 * Đọc `GET /inventory` (bộ tính `domain_units_deals`, suy từ bản sao CRM) —
 * MỘT lời gọi cho cả dự án, không phải N lời gọi từng phân khu. `velocity` và
 * `latest_data` không có trong `InventoryAreaOut`/`InventoryOut` — để `null`
 * thay vì bịa; hai widget này đã tự hiện "N/A" khi gặp `null`. `status`
 * (hot/normal/slow) cũng KHÔNG được gán ở đây: chưa có ngưỡng nghiệp vụ nào
 * được chốt cho việc đó (xem `docs/roadmap.md`, "NGƯỠNG ĐỘ TƯƠI" — cùng loại
 * quyết định còn treo) — gán bừa một ngưỡng tự chọn sẽ là đúng thứ việc "đừng
 * bịa quy tắc nghiệp vụ" mà việc này cấm.
 *
 * -> [{ id, name, total_units, sold, remaining, absorption_rate, velocity,
 *       latest_data, status }]
 */
export async function getDashboardAreas({ externalProjectId }) {
  if (!externalProjectId) return [];
  const inventory = await listInventoryScoped(externalProjectId);
  return (inventory.areas || []).map((row) => ({
    id: row.area_id,
    name: `${row.area_name} · ${row.unit_type}`,
    total_units: row.total_units,
    sold: row.units_sold,
    remaining: row.units_remaining,
    absorption_rate: row.total_units > 0 ? (row.units_sold / row.total_units) * 100 : null,
    velocity: null,
    latest_data: null,
    status: undefined,
  }));
}

const DASHBOARD_ANOMALY_LABEL = {
  HELD_EXCEEDS_STOCK: "Số giao dịch đang giữ vượt quá tồn kho khả bán",
  DEAL_ON_DELETED_UNIT: "Có giao dịch đang giữ trỏ tới một căn đã xoá",
};

const DASHBOARD_CALCULATOR_LABEL = {
  legacy_aggregate: "Dữ liệu tổng hợp (đường sản xuất hiện tại)",
  domain_units_deals: "Chiếu từ bản sao CRM (đối chiếu)",
};

/** Độ tươi + chất lượng dữ liệu cho `DataQualityPanel`.
 *
 * Mốc đồng bộ đọc từ `/absorption/summary` (đúng ba mốc backend có:
 * `last_successful_sync`/`last_attempted_sync`/`last_sync_status`); bất
 * thường đọc từ `InventoryOut.anomalies` (`GET /inventory`) — danh sách dict
 * có `code` (`HELD_EXCEEDS_STOCK`, `DEAL_ON_DELETED_UNIT`, xem
 * `src/services/domain_absorption.py:214,265`), ánh xạ sang câu tiếng Việt dễ
 * đọc, KHÔNG bỏ mã gốc nếu gặp `code` lạ (không nuốt câm lặng một loại bất
 * thường mới).
 *
 * -> { status: "ok"|"ok_with_warnings"|"error", latest_data, source,
 *      date_range: {from, to} | null, error_records, warnings: string[] }
 */
export async function getDataQuality({ projectId, externalProjectId, from, to }) {
  const [summary, inventory] = await Promise.all([
    api.get(`${V1}/absorption/summary?project_id=${encodeURIComponent(projectId)}&calculator=legacy_aggregate`),
    externalProjectId ? listInventoryScoped(externalProjectId) : Promise.resolve(null),
  ]);

  const anomalies = inventory?.anomalies || [];
  const freshness = classifyFreshness(summary);
  const FAILURE_STATES = new Set(["sync_failed"]);
  const WARNING_STATES = new Set(["stale", "calculation_outdated", "never_synced", "timestamp_unknown"]);

  let status = "ok";
  if (FAILURE_STATES.has(freshness)) status = "error";
  else if (WARNING_STATES.has(freshness) || anomalies.length > 0) status = "ok_with_warnings";

  return {
    status,
    latest_data: summary.last_successful_sync,
    source:
      DASHBOARD_CALCULATOR_LABEL[inventory?.calculator] ||
      inventory?.calculator ||
      DASHBOARD_CALCULATOR_LABEL.legacy_aggregate,
    date_range: from && to ? { from, to } : null,
    error_records: anomalies.length,
    warnings: anomalies.map((a) => {
      const label = DASHBOARD_ANOMALY_LABEL[a.code] || a.code || "Bất thường dữ liệu chưa phân loại";
      const ref = a.area_id ? ` (khu vực ${a.area_id})` : a.external_deal_id ? ` (giao dịch ${a.external_deal_id})` : "";
      return `${label}${ref}`;
    }),
  };
}

/** Lịch sử upload. `transportMode` ("file_upload" | "api_push") lọc đúng tab
 *  đang xem — `GET /files` đã hỗ trợ sẵn query `transport_mode`, trước đây bị
 *  bỏ quên nên cả hai tab "Tải lên file"/"Đồng bộ CRM" hiện cùng một danh sách
 *  chưa lọc. */
export async function listFiles(projectIdOrTransportMode, maybeTransportMode) {
  // Giữ tương thích với lời gọi cũ `listFiles(transportMode)` trong khi cho
  // UploadPage truyền đúng project UUID của ngữ cảnh đã chọn.
  const legacyMode = projectIdOrTransportMode === "file_upload" || projectIdOrTransportMode === "api_push";
  const projectId = legacyMode ? await activeProjectId() : (projectIdOrTransportMode ?? await activeProjectId());
  const transportMode = legacyMode ? projectIdOrTransportMode : maybeTransportMode;
  const q = new URLSearchParams({ project_id: projectId });
  if (transportMode) q.set("transport_mode", transportMode);
  const body = await api.get(`${V1}/files?${q}`);
  return (body.items ?? []).map((item) => ({
    ...item,
    id: item.file_id,
  }));
}

/** Upload Excel/CSV. */
export async function uploadFile(file, template = "sales", projectId) {
  const targetProjectId = projectId ?? await activeProjectId();
  const fd = new FormData();
  fd.append("file", file);
  fd.append("template", template);
  fd.append("project_id", targetProjectId);
  return api.post(`${V1}/files/upload`, fd);
}

/** Trạng thái parse của một file. */
export const fileStatus = (id) =>
  api.get(`${V1}/files/${id}/status`);

/** Lỗi validate theo dòng. */
export async function fileErrors(id) {
  const body = await api.get(`${V1}/files/${id}/errors`);
  return body.errors ?? [];
}

/** Đường tải CSV toàn bộ lỗi. */
export const fileErrorsCsvUrl = (id) =>
  `/api${V1}/files/${id}/errors.csv`;

// ---------- AI Agent chat ----------

/** Gửi câu hỏi tới AI agent. */
export const chatWithAgent = (message, projectId) => {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return api.post(`/v1/chat${query}`, { message }, { forceReal: true });
};

// ---------- Phase 6: đề xuất tư vấn dựa trên xếp hạng, CHỜ DUYỆT -------------
// `AGENTS.md` — bước duyệt người là bắt buộc, không tuỳ chọn. Không hàm nào ở
// đây coi một đề xuất là "cuối cùng" trước khi `approveRecommendation` chạy.

/** project_id/area_id là external_id (Mini CRM), area_id không bắt buộc.
 * -> { recommendation_id, project_id, area_id, status: "pending_approval",
 *      ranking_run_id, summary, recommended_actions, generated_at, ... } */
export const createRecommendation = (projectId, areaId) =>
  api.post(`${V1}/agent/recommendations`, { project_id: projectId, area_id: areaId ?? null });

/** Đọc lại một đề xuất — dùng để theo dõi status sau khi duyệt/từ chối. */
export const getRecommendation = (recommendationId) =>
  api.get(`${V1}/agent/recommendations/${recommendationId}`);

/** -> đề xuất với status: "approved", decided_by/decided_at/decision_reason đã điền. */
export const approveRecommendation = (recommendationId, reason, actor) =>
  api.post(`${V1}/agent/recommendations/${recommendationId}/approve`, { actor, reason });

/** -> đề xuất với status: "rejected". */
export const rejectRecommendation = (recommendationId, reason, actor) =>
  api.post(`${V1}/agent/recommendations/${recommendationId}/reject`, { actor, reason });

export const listRecommendations = (projectId, limit = 20) =>
  api.get(`${V1}/agent/recommendations?project_id=${encodeURIComponent(projectId)}&limit=${limit}`);

export const executeRecommendation = (recommendationId, actor) =>
  api.post(`${V1}/agent/recommendations/${recommendationId}/execute`, { actor, confirmed: true });

// ---------- Chọn ngữ cảnh nạp dữ liệu: Dự án → Phân khu ----------
// Giải quyết xung đột merge: `listProjects` đã khai ở trên với hợp đồng thật của
// backend, nên bản trùng ở nhánh kia bị bỏ. `listProjectZones` giữ lại nhưng trỏ
// vào endpoint CÓ THẬT — "zone" ở ImportSelectPage chính là `areas`.
//
// `ProjectSummary` không có `location`/`zone_count`/`total_units`/`sold_pct` —
// không có nguồn thật cho bốn trường này ở mức MỘT lời gọi cho cả danh sách dự
// án (sẽ phải N+1 sang /areas hoặc /inventory từng dự án). KHÔNG bịa giá trị
// trung tính nữa — trang gọi hàm này tự hiện "—" khi thiếu, đúng nguyên tắc
// "không có dữ liệu thì không hiện số 0 giả".
export async function listProjectsForImport() {
  const rows = await listProjects();
  return rows.map((row) => ({ ...row, id: row.project_id }));
}

/** Các phân khu trong 1 dự án -> [{ id, name, total_units, units_remaining, status }] */
export async function listProjectZones(projectId) {
  const rows = await api.get(`${V1}/areas?project_id=${projectId}`);
  return rows.map((row) => ({
    ...row,
    id: row.area_id,
    name: row.area_name,
    units_remaining: row.units_remaining,
    status: row.status ?? null,
  }));
}

// ---------- Tạo / sửa dự án / phân khu — QUA MINI CRM (Phase D/F) -----------
// Backend KHÔNG còn POST /projects, POST /areas từ Phase D (§D7) — Mini CRM là
// nguồn sự thật cho MỌI trường CANONICAL (name/launch_date/area_name/unit_type/
// bedrooms/area_sqm/total_units). Backend chỉ còn PATCH headline/introduce —
// nội dung hiển thị backend TỰ SỞ HỮU (§A2.3), xem khối "Sửa nội dung hiển thị"
// bên dưới. Viết SAI cổng (gọi thẳng backend cho trường canonical) sẽ ăn 422
// NO_CHANGES im lặng — xem lịch sử phát hiện ở test_catalog.py Phase D.
//
// Ghi xong CHƯA chiếu ngay: Mini CRM chỉ LƯU + relay tự động (Phase C.5) mới
// gửi sang backend sau đó — nơi gọi phải tự hỏi lại backend để biết đã chiếu
// hay chưa (`getProjectByExternalId`/`getAreaByExternalId`), không giả định
// "ghi xong là đọc lại thấy ngay".

/** Mini CRM POST /projects — đòi vai trò admin (tạo dự án MỚI mở rộng phạm vi).
 *  -> { record: { external_id, name, launch_date, source_revision, ... } } */
export const createProjectInMiniCrm = (payload) => minicrm.post("/projects", payload);

/** Mini CRM POST /areas — đòi vai trò pipeline_operator trở lên, VÀ phạm vi
 *  chứa `external_project_id`. -> { record: { external_id, ... } } */
export const createAreaInMiniCrm = (payload) => minicrm.post("/areas", payload);

/** Mini CRM PATCH /projects/{external_id} — sửa trường CANONICAL (name/launch_date). */
export const updateProjectInMiniCrm = (externalId, changes) =>
  minicrm.patch(`/projects/${encodeURIComponent(externalId)}`, changes);

/** Mini CRM PATCH /areas/{external_id} — sửa trường CANONICAL. */
export const updateAreaInMiniCrm = (externalId, changes) =>
  minicrm.patch(`/areas/${encodeURIComponent(externalId)}`, changes);

// ---------- Ảnh bìa dự án / phân khu ----------
// Mỗi dự án / phân khu có TỐI ĐA một ảnh. POST tạo mới (409 nếu đã có), PUT thay
// thế, DELETE xoá. Backend nhận multipart nên gửi FormData, không gửi JSON.

const imageBase = (kind, id) => `${V1}/${kind === "project" ? "projects" : "areas"}/${id}/image`;

const imageForm = (file) => {
  const fd = new FormData();
  fd.append("file", file);
  return fd;
};

/** Xem ảnh hiện tại -> { owner_id, url, public_id }; 404 nếu chưa có ảnh. */
export const getImage = (kind, id) => api.get(imageBase(kind, id));

/** Tải ảnh lần đầu. Đã có ảnh -> 409 IMAGE_ALREADY_EXISTS. */
export const createImage = (kind, id, file) => api.post(imageBase(kind, id), imageForm(file));

/** Thay ảnh (đặt thành) — chấp nhận cả khi chưa có ảnh nào. */
export const replaceImage = (kind, id, file) => api.put(imageBase(kind, id), imageForm(file));

/** Xoá ảnh khỏi Cloudinary và bỏ tham chiếu trong DB. */
export const deleteImage = (kind, id) => api.del(imageBase(kind, id));

// ---------- Sửa NỘI DUNG HIỂN THỊ dự án / phân khu — backend TỰ SỞ HỮU ------
// Từ Phase D (§D7/§A2.3): backend chỉ còn nhận `headline`/`introduce` — hai
// trường này KHÔNG phải dữ liệu nghiệp vụ Mini CRM sở hữu, chúng là nội dung
// hiển thị RIÊNG của backend. Gửi bất kỳ trường nào khác (name, launch_date,
// area_name, ...) sẽ bị lặng lẽ bỏ qua (pydantic `extra="ignore"`) rồi backend
// trả 422 NO_CHANGES nếu đó là TOÀN BỘ nội dung gửi lên — trường canonical đi
// qua `updateProjectInMiniCrm`/`updateAreaInMiniCrm` ở trên.

/** PATCH dự án — CHỈ headline/introduce. UUID nội bộ (không phải external_id). */
export const updateProject = (projectId, changes) =>
  api.patch(`${V1}/projects/${projectId}`, changes);

/** PATCH phân khu — CHỈ headline/introduce. */
export const updateArea = (areaId, changes) => api.patch(`${V1}/areas/${areaId}`, changes);
