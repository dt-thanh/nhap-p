// frontend/src/utils/globalUnitRanking.js
// ---------------------------------------------------------------------------
// Gộp kết quả xếp hạng của NHIỀU dự án thành MỘT bảng xếp hạng CĂN toàn hệ
// thống: điểm cao nhất trước, thấp nhất sau, không chia nhóm theo dự án hay
// phân khu.
//
// Thực thể chính LUÔN là CĂN. Dự án và phân khu chỉ là NGỮ CẢNH hiển thị —
// không bao giờ là khoá nhóm, tab, hay tiêu chí sắp xếp chính.
//
// VÌ SAO PHẢI GỘP Ở PHÍA CLIENT. `GET /v1/ranking` bắt buộc có
// `external_project_id` (`src/api/ranking.py`, `Query(...)`), backend KHÔNG có
// endpoint xếp hạng căn toàn cục. Ở đây gọi một lần cho mỗi dự án rồi trộn —
// KHÔNG gọi theo từng căn (không N+1), và tên dự án lấy từ `GET /v1/projects`
// đã tải sẵn nên không phát sinh request để lấy ngữ cảnh.
//
// VÌ SAO ĐIỂM SO SÁNH ĐƯỢC GIỮA CÁC DỰ ÁN (bằng chứng, không phải giả định):
//   1. `ranking_configs` là TOÀN CỤC — một bộ trọng số cho mọi dự án
//      (`src/api/ranking.py`: không endpoint config nào nhận project id).
//   2. Ngưỡng mức là giá trị TUYỆT ĐỐI, không theo phân vị
//      (`src/ranking/bands.py:26-27`, và docstring nói rõ lý do).
//   3. Hằng số chuẩn hoá là hằng số toàn cục, không tính lại theo dự án
//      (`VELOCITY_SATURATION`, `DEMAND_SATURATION` — `src/ranking/service.py:71,76`).
// Điều KHÔNG bảo đảm: hai dự án có thể đang giữ điểm của hai `config_version`
// khác nhau nếu một dự án chưa được chạy lại. Trường hợp đó được nêu TƯỜNG
// MINH qua `meta.mixedConfigVersions` chứ không im lặng trộn.
// ---------------------------------------------------------------------------

/** Chiều sắp xếp mặc định và các lựa chọn được phép của bảng toàn cục. */
export const SORT_DIRECTIONS = Object.freeze({ DESC: "desc", ASC: "asc" });
export const RANK_DIRECTION = SORT_DIRECTIONS.DESC;

/** Số dự án tối đa được nạp xếp hạng trong một lần mở trang.
 *  Fan-out là 1 request/dự án. Phần vượt trần KHÔNG bị bỏ im lặng — xem
 *  `meta.projectsNotScanned`. */
export const RANKING_PROJECT_LIMIT = 24;

/** Số căn tối đa lấy được cho mỗi dự án trong một lời gọi. Bằng đúng
 *  `MAX_UNITS_PER_PAGE` của `src/api/ranking.py`; xin lớn hơn sẽ bị 422.
 *  Backend trả theo `rank_in_project` tăng dần, tức là TOP-N điểm cao nhất của
 *  dự án đó, nên bảng gộp là thứ tự toàn cục ĐÚNG cho tới hạng N. */
export const UNITS_PER_PROJECT_LIMIT = 200;

/** Số dòng mỗi trang. Phân trang chạy SAU khi đã sắp xếp toàn cục. */
export const GLOBAL_PAGE_SIZE = 50;

export const UNKNOWN_PROJECT_LABEL = "Chưa xác định dự án";
export const UNKNOWN_AREA_LABEL = "Chưa xác định phân khu";
export const UNKNOWN_UNIT_LABEL = "Chưa xác định căn";

/**
 * @typedef {Object} GlobalRankedUnit
 * @property {string} unitId
 * @property {string} unitName            Mã căn (`units.unit_code`)
 * @property {string|null} unitType
 * @property {string|null} unitStatus     available | reserved | sold | blocked
 * @property {string|null} projectId      UUID nội bộ
 * @property {string|null} projectExternalId  Danh tính Mini CRM — dùng cho URL
 * @property {string} projectName
 * @property {boolean} projectContextAvailable
 * @property {string|null} areaId         UUID phân khu (KHÔNG dùng được cho URL)
 * @property {string} areaName
 * @property {boolean} areaContextAvailable
 * @property {number|null} score          [0,1]; null = backend không chấm được
 * @property {number|null} scorePercent
 * @property {string|null} band           high | medium | low | null
 * @property {number|null} rank           Hạng TOÀN CỤC, chỉ gán cho căn có điểm
 * @property {number|null} rankInProject  Hạng trong dự án, do backend cấp
 * @property {"desc"} rankDirection
 * @property {number|null} weightCoverage
 * @property {number|null} missingFeaturesCount
 * @property {"high"|"medium"|"unknown"} confidence
 * @property {string} source
 * @property {string|null} updatedAt      `ranking_scores.computed_at` của dự án
 * @property {number|null} configVersion
 */

/** Nguồn của mọi dòng trong bảng này. */
const SOURCE = "ranking_scores";

/** Điểm về từ backend là CHUỖI Decimal (`RankedUnitOut.score`) để không mất độ
 *  chính xác. Ép kiểu TƯỜNG MINH ở đây; thiếu/hỏng thì trả `null`, KHÔNG quy về
 *  0 — "chưa chấm được" và "chấm được 0 điểm" là hai chuyện khác nhau, và quy
 *  về 0 sẽ đẩy một căn thiếu dữ liệu xuống cuối bảng như thể nó khó bán. */
export function toScore(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function toFiniteOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** Số đặc trưng KHÔNG giải được của một căn — `contributions[].source !== "resolved"`
 *  (xem `_contributions` trong `src/api/ranking.py`). `null` khi backend không
 *  gửi kèm `contributions` (không suy diễn "đủ dữ liệu" từ sự vắng mặt). */
function missingFeatures(contributions) {
  if (!Array.isArray(contributions)) return null;
  return contributions.filter((c) => c && c.source !== "resolved").length;
}

/** Nhãn độ tin cậy — chỉ là cách phát biểu lại DỮ KIỆN CÓ THẬT, không phải một
 *  ngưỡng tự đặt:
 *    unknown — backend không chấm điểm (coverage dưới `min_weight_coverage`)
 *    high    — có điểm và MỌI đặc trưng đều giải được
 *    medium  — có điểm nhưng ít nhất một đặc trưng thiếu dữ liệu
 *  Không có mức "low" vì không có dữ kiện nào trong phản hồi phân biệt được nó. */
function confidenceOf(score, missing) {
  if (score === null) return "unknown";
  if (missing === null || missing > 0) return "medium";
  return "high";
}

/**
 * Một dòng chuẩn hoá cho một căn.
 * @returns {GlobalRankedUnit|null} `null` nếu bản ghi không có danh tính căn.
 */
export function normalizeUnit(item, context = {}) {
  if (!item || typeof item !== "object") return null;
  const unitId = nonEmptyString(item.unit_id);
  const unitName = nonEmptyString(item.unit_code);
  if (!unitId && !unitName) return null;

  const project = context.project || {};
  const projectName = nonEmptyString(project.name);
  const areaName = nonEmptyString(item.area_name);
  const score = toScore(item.score);
  const missing = missingFeatures(item.contributions);

  return {
    unitId: unitId || `unit-code:${unitName}`,
    unitName: unitName || UNKNOWN_UNIT_LABEL,
    unitType: nonEmptyString(item.unit_type),
    unitStatus: nonEmptyString(item.unit_status),
    projectId: nonEmptyString(project.project_id),
    projectExternalId: nonEmptyString(project.external_id),
    projectName: projectName || UNKNOWN_PROJECT_LABEL,
    projectContextAvailable: Boolean(projectName),
    areaId: nonEmptyString(item.area_id),
    areaName: areaName || UNKNOWN_AREA_LABEL,
    areaContextAvailable: Boolean(areaName),
    score,
    scorePercent: toFiniteOrNull(item.score_percent),
    band: nonEmptyString(item.band),
    rank: null, // gán sau khi sắp xếp toàn cục
    rankInProject: toFiniteOrNull(item.rank_in_project),
    rankDirection: RANK_DIRECTION,
    weightCoverage: toFiniteOrNull(item.weight_coverage),
    missingFeaturesCount: missing,
    confidence: confidenceOf(score, missing),
    source: SOURCE,
    updatedAt: nonEmptyString(context.computedAt),
    configVersion: toFiniteOrNull(context.configVersion),
  };
}

/** So sánh HAI căn ĐÃ CÓ điểm.
 *
 *  Điểm giảm dần là tiêu chí chính. Phá hoà bằng mã căn rồi unit id — cả hai
 *  đều tất định và độc lập với thứ tự request trả về.
 *
 *  KHÔNG dùng `rank_in_project` để phá hoà: nó chỉ có nghĩa TRONG một dự án,
 *  nên dùng xuyên dự án sẽ không phải một quan hệ thứ tự toàn phần (hai căn
 *  cùng hạng 1 ở hai dự án khác nhau). Backend phá hoà bằng
 *  `tie_break_created_at` (`src/ranking/engine.py:147`) nhưng trường đó KHÔNG
 *  có trong `RankedUnitOut`, nên không tái lập được ở client. */
function compareScored(a, b, direction) {
  if (a.score !== b.score) {
    return direction === SORT_DIRECTIONS.ASC ? a.score - b.score : b.score - a.score;
  }
  const byName = String(a.unitName).localeCompare(String(b.unitName), "vi");
  if (byName !== 0) return byName;
  return String(a.unitId).localeCompare(String(b.unitId));
}

function compareUnscored(a, b) {
  const byName = String(a.unitName).localeCompare(String(b.unitName), "vi");
  return byName !== 0 ? byName : String(a.unitId).localeCompare(String(b.unitId));
}

function sortableScore(row) {
  if (!row || row.score === null || row.score === undefined || row.score === "") return null;
  const score = Number(row.score);
  return Number.isFinite(score) ? score : null;
}

/**
 * Sort normalized global-ranking rows without mutating the input array.
 * Scored rows always precede missing scores; ties use unit name then ID.
 */
export function sortGlobalRankingRows(rows = [], direction = RANK_DIRECTION) {
  const normalizedDirection = direction === SORT_DIRECTIONS.ASC
    ? SORT_DIRECTIONS.ASC
    : SORT_DIRECTIONS.DESC;

  return (Array.isArray(rows) ? rows : []).slice().sort((a, b) => {
    const aScore = sortableScore(a);
    const bScore = sortableScore(b);
    const aMissing = aScore === null;
    const bMissing = bScore === null;

    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    if (!aMissing) return compareScored({ ...a, score: aScore }, { ...b, score: bScore }, normalizedDirection);
    return compareUnscored(a, b);
  });
}

/** Giữ bản ghi MỚI hơn khi cùng một căn xuất hiện nhiều lần.
 *  `_persist_scores` xoá-rồi-chèn nên mỗi dự án chỉ có một lần chạy đang lưu;
 *  trùng ở đây chỉ xảy ra khi hai phản hồi chồng lấn nhau, và khi đó bản có
 *  `computed_at` mới hơn là bản đúng. */
function dedupeByUnit(rows) {
  const byId = new Map();
  let duplicates = 0;
  for (const row of rows) {
    const kept = byId.get(row.unitId);
    if (!kept) {
      byId.set(row.unitId, row);
      continue;
    }
    duplicates += 1;
    const keptAt = Date.parse(kept.updatedAt || "");
    const nextAt = Date.parse(row.updatedAt || "");
    if (Number.isFinite(nextAt) && (!Number.isFinite(keptAt) || nextAt > keptAt)) {
      byId.set(row.unitId, row);
    }
  }
  return { rows: [...byId.values()], duplicates };
}

/**
 * Gộp kết quả nhiều dự án thành MỘT bảng xếp hạng căn toàn cục.
 *
 * @param {{project: Object, ranking: Object|null, rankingError: Object|null}[]} entries
 * @param {{projectsNotScanned?: number}} [options]
 * @returns {{rows: GlobalRankedUnit[], meta: Object}}
 */
export function buildGlobalRanking(entries = [], options = {}) {
  const list = Array.isArray(entries) ? entries : [];
  const collected = [];
  const failedProjects = [];
  const malformedProjects = [];
  const truncatedProjects = [];
  const neverRankedProjects = [];
  const configVersions = new Set();
  let latestComputedAt = null;

  for (const entry of list) {
    const project = entry?.project || {};
    const label = nonEmptyString(project.name) || nonEmptyString(project.external_id) || UNKNOWN_PROJECT_LABEL;

    if (entry?.rankingError) {
      failedProjects.push({ label, error: entry.rankingError });
      continue;
    }
    const ranking = entry?.ranking;
    if (!ranking || typeof ranking !== "object" || Array.isArray(ranking)) {
      if (ranking !== null && ranking !== undefined) malformedProjects.push({ label });
      continue;
    }
    if (!Array.isArray(ranking.items)) {
      malformedProjects.push({ label });
      continue;
    }
    if (ranking.computed_at === null || ranking.computed_at === undefined) {
      neverRankedProjects.push({ label });
    }

    const total = toFiniteOrNull(ranking.total);
    if (total !== null && total > ranking.items.length) {
      truncatedProjects.push({ label, shown: ranking.items.length, total });
    }
    const configVersion = toFiniteOrNull(ranking.config_version);
    if (configVersion !== null) configVersions.add(configVersion);
    const computedAt = nonEmptyString(ranking.computed_at);
    if (computedAt) {
      const ts = Date.parse(computedAt);
      if (Number.isFinite(ts) && (latestComputedAt === null || ts > Date.parse(latestComputedAt))) {
        latestComputedAt = computedAt;
      }
    }

    for (const item of ranking.items) {
      const row = normalizeUnit(item, { project, computedAt, configVersion });
      if (row) collected.push(row);
    }
  }

  const { rows: unique, duplicates } = dedupeByUnit(collected);
  const sorted = sortGlobalRankingRows(unique, RANK_DIRECTION);
  const scored = sorted.filter((row) => row.score !== null);
  const unscored = sorted.filter((row) => row.score === null);
  scored.forEach((row, index) => {
    row.rank = index + 1;
  });

  return {
    rows: [...scored, ...unscored],
    meta: {
      rankDirection: RANK_DIRECTION,
      totalUnits: unique.length,
      scoredCount: scored.length,
      unscoredCount: unscored.length,
      duplicatesResolved: duplicates,
      projectsIncluded: list.length - failedProjects.length - malformedProjects.length,
      projectsNotScanned: Math.max(Number(options.projectsNotScanned) || 0, 0),
      failedProjects,
      malformedProjects,
      truncatedProjects,
      neverRankedProjects,
      missingProjectContext: unique.filter((row) => !row.projectContextAvailable).length,
      missingAreaContext: unique.filter((row) => !row.areaContextAvailable).length,
      configVersions: [...configVersions].sort((a, b) => a - b),
      mixedConfigVersions: configVersions.size > 1,
      latestComputedAt,
    },
  };
}

/** Cắt trang SAU khi đã sắp xếp toàn cục — thứ tự toàn cục vì thế không đổi. */
export function pageOf(rows, page, pageSize = GLOBAL_PAGE_SIZE) {
  const list = Array.isArray(rows) ? rows : [];
  const pages = Math.max(Math.ceil(list.length / pageSize), 1);
  const current = Math.min(Math.max(page, 0), pages - 1);
  const start = current * pageSize;
  return { items: list.slice(start, start + pageSize), page: current, pages, start };
}
