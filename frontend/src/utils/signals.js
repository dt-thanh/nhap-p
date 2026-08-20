// frontend/src/utils/signals.js
// ---------------------------------------------------------------------------
// Tầng SUY RA "Tín hiệu cần chú ý" cho trang danh mục (/overview).
//
// Chỉ BA nhóm nghiệp vụ, không phải hộp thư chất lượng dữ liệu chung:
//   A. absorption  — vận tốc hấp thụ GỘP và tầm nhìn bán hết đáng chú ý
//   B. forecasting — dự báo không có / chưa triển khai
//   C. ranking     — căn thuộc mức xếp hạng thấp, hoặc kết quả xếp hạng không đủ tin
//
// Hàm THUẦN: không fetch, không đọc đồng hồ ngoài `options.now`.
//
// NGUYÊN TẮC VỀ NGƯỠNG. Không có ngưỡng nào được bịa ra. Mỗi tín hiệu mang
// `evidence.thresholdStatus`:
//   VERIFIED    — luật/enum/hằng số CÓ THẬT trong repo (data_status,
//                 deriveVelocityDirection, band cutoffs của bands.py, …)
//   PROVISIONAL — dùng lại một ngưỡng repo tự nhận là tạm (STALE_AFTER_MS,
//                 RECALC_SKEW_AFTER_MS), hoặc một hằng số do tầng này chọn tay
//   UNDEFINED   — sản phẩm cần một ngưỡng nhưng repo CHƯA có; tín hiệu chỉ nêu
//                 sự kiện quan sát được, không tự xưng là luật nghiệp vụ
//
// PHÂN BIỆT PHẠM VI. `band_counts` của `GET /ranking` là số CĂN theo mức, trong
// phạm vi MỘT dự án. Repo KHÔNG định nghĩa cách gộp điểm căn thành điểm dự án
// (xem docs/database_impact_analysis.md §3.1), nên ở đây tuyệt đối không nói
// "dự án bị xếp hạng thấp" — chỉ nói "dự án X có N căn ở mức thấp".
//
// GỘP vs RÒNG. Mọi con số vận tốc ở đây là GỘP. Bộ tính miền chỉ đếm giao dịch
// `sold` còn sống; `lost` (đã gộp cả `cancelled` qua alias) KHÔNG bị trừ ra —
// xem khối "Quy tắc đếm" đầu `src/services/domain_absorption.py`. Hệ thống hiện
// KHÔNG có bất kỳ số hấp thụ RÒNG nào, nên không nhãn nào ở đây được phép nói
// "ròng"/"net".
// ---------------------------------------------------------------------------
import { RECALC_SKEW_AFTER_MS, STALE_AFTER_MS, classifyFreshness } from "./freshness";
import { deriveVelocityDirection } from "./velocity";

/**
 * @typedef {Object} SignalEvidence
 * @property {string|number} [currentValue]
 * @property {string|number} [baselineValue]
 * @property {string|number} [delta]
 * @property {string|number} [threshold]
 * @property {"VERIFIED"|"PROVISIONAL"|"UNDEFINED"} [thresholdStatus]
 * @property {string} [band]
 * @property {string} [detectedAt]
 * @property {string} [source]
 * @property {string} [sourcePath]
 * @property {string} [projectId]
 * @property {string} [externalId]
 * @property {string} [areaId]
 * @property {string} [unitId]
 * @property {string} [dataFreshness]
 * @property {number} [attentionScore]
 * @property {string} [attentionFormula]
 * @property {string[]} [details]
 */

/**
 * @typedef {Object} Signal
 * @property {string} id
 * @property {string} ruleId                 Khoá GỘP danh mục — id không kèm dự án
 * @property {"absorption"|"forecasting"|"ranking"} category
 * @property {1|2|null} layer                1 = tin cậy dữ liệu, 2 = rủi ro thương mại, null = vận hành nội bộ
 * @property {"critical"|"warning"|"info"} severity
 * @property {"open"|"acknowledged"|"investigating"|"resolved"|"dismissed"} status
 * @property {"portfolio"|"project"|"area"|"unit"} scope
 * @property {string} title
 * @property {string} whatHappened
 * @property {string} whyItMatters
 * @property {SignalEvidence} evidence
 * @property {string} recommendedAction
 * @property {{label: string, href: string}[]} [links]
 * @property {"high"|"medium"|"low"} confidence
 * @property {number} attentionScore         0..100, công thức TẠM — xem SCORING
 * @property {number|null} affectedUnits     Số CĂN mà chính luật này đo được
 * @property {Signal[]} [children]           Chỉ có ở tín hiệu GỘP cấp danh mục
 * @property {string} createdAt
 * @property {string} [updatedAt]
 */

export const CATEGORIES = ["absorption", "forecasting", "ranking"];
export const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 };
export const STATUS_ORDER = { open: 0, investigating: 1, acknowledged: 2, resolved: 3, dismissed: 4 };
export const CONFIDENCE_ORDER = { high: 0, medium: 1, low: 2 };

export const CATEGORY_LABEL = { absorption: "Hấp thụ", forecasting: "Dự báo", ranking: "Xếp hạng" };
export const SEVERITY_LABEL = { critical: "Nghiêm trọng", warning: "Cảnh báo", info: "Thông tin" };
export const CONFIDENCE_LABEL = { high: "Cao", medium: "Trung bình", low: "Thấp" };
export const STATUS_LABEL = {
  open: "Đang mở",
  acknowledged: "Đã ghi nhận",
  investigating: "Đang điều tra",
  resolved: "Đã xử lý",
  dismissed: "Đã bỏ qua",
};
export const THRESHOLD_STATUS_LABEL = {
  VERIFIED: "Ngưỡng đã xác lập",
  PROVISIONAL: "Ngưỡng TẠM",
  UNDEFINED: "Chưa có ngưỡng",
};

/** Tầng ưu tiên. KHÔNG có tầng 3 (rủi ro nhu cầu dẫn dắt): repo không có mô hình
 *  lead/khách hàng, không có lịch sử chuyển giai đoạn deal, không có giá — xem
 *  docs/signal_prerequisites.md. Khai báo tầng 3 ở đây sẽ là tuyên bố năng lực
 *  không có thật. */
export const LAYER_LABEL = {
  1: "Tầng 1 · Tin cậy dữ liệu",
  2: "Tầng 2 · Rủi ro thương mại",
  3: "Tầng 3 · Rủi ro nhu cầu dẫn dắt",
};
export const LAYER_SHORT_LABEL = { 1: "T1 Dữ liệu", 2: "T2 Thương mại", 3: "T3 Nhu cầu" };
export const HOUSEKEEPING_LAYER_LABEL = "Vận hành nội bộ";

/** Chưa có bảng/endpoint lưu trạng thái tín hiệu ⇒ giao diện CHỈ ĐỌC. */
export const SIGNAL_PERSISTENCE_SUPPORTED = false;

/** TRẦN số dự án được nạp HẤP THỤ. Chỉ còn chi phối nhóm A: `GET
 *  /absorption/summary` là 1 request/dự án và KHÔNG được dùng cho việc gì khác
 *  trên trang này. Xếp hạng thì trang đã nạp sẵn tới `RANKING_PROJECT_LIMIT`
 *  cho bảng xếp hạng toàn cục, nên nhóm C chạy trên TOÀN BỘ phần đã nạp — không
 *  tốn thêm request nào. Hai trần được nêu tường minh bằng hai tín hiệu riêng. */
export const SIGNAL_PROJECT_LIMIT = 6;

/** Số tuần trong một tháng dương lịch trung bình (365.25 / 12 / 7). Dùng DUY
 *  NHẤT để đổi đơn vị `estimated_weeks_to_sell_out` sang "tháng tồn kho" cho dễ
 *  đọc — KHÔNG phải một phép tính mới, và luôn nêu số chia kèm theo. */
export const WEEKS_PER_MONTH = 365.25 / 12 / 7;

/** Nguồn/luật CHƯA dựng được, hiển thị nguyên văn dưới danh sách. */
export const UNAVAILABLE_RULES = [
  {
    rule: "Tụt hạng so với lần chạy trước",
    reason:
      "GET /api/v1/ranking chỉ trả kết quả HIỆN TẠI; không có endpoint lịch sử điểm/thứ hạng theo lần chạy, nên không có mốc nền để so. Không suy ra mức tụt khi chưa có nền.",
  },
  {
    rule: "Ngưỡng 'vận tốc thấp' (khác 0)",
    reason:
      "Repo không định nghĩa ngưỡng vận tốc thấp. Chỉ phát tín hiệu khi vận tốc bằng ĐÚNG 0 (sự kiện quan sát được), không tự đặt mốc 'thấp'.",
  },
  {
    rule: "Hấp thụ RÒNG và tỷ lệ huỷ",
    reason:
      "Bộ tính miền chỉ đếm giao dịch `sold`; `lost`/`cancelled` không bị trừ ra và không endpoint nào của trang này trả số lượng của chúng. Mọi vận tốc ở đây là GỘP và được ghi rõ là gộp.",
  },
  {
    rule: "Ngưỡng tồn kho (tháng/tuần bán hết) là cao hay thấp",
    reason:
      "Repo tính được `estimated_weeks_to_sell_out` nhưng KHÔNG có mốc nghiệp vụ nào nói bao nhiêu là quá cao. Tín hiệu chỉ nêu con số quan sát được, không xếp loại tốt/xấu.",
  },
  {
    rule: "Sai số / độ bất ổn dự báo",
    reason:
      "Không có đầu ra dự báo nào để đo (src/jobs/forecast.py là stub). Không gọi dự báo là 'bất ổn' khi nó chưa từng chạy.",
  },
  {
    rule: "Xếp hạng cấp DỰ ÁN",
    reason:
      "Repo không định nghĩa cách gộp điểm căn thành điểm dự án, nên chỉ báo cáo số CĂN theo mức trong từng dự án.",
  },
];

// --- Chấm điểm chú ý --------------------------------------------------------
//
// MỘT chỗ duy nhất cho mọi hằng số. Công thức là TẠM (PROVISIONAL): nó xếp thứ
// tự trong danh sách chứ không phải một phán quyết nghiệp vụ, và không có mô
// hình học máy nào ở đây — mọi số hạng đều tra ngược được từ bằng chứng.
//
//   attentionScore = baseSeverity + layerBoost + confidenceBoost + impactBoost
//
// Trần 100 đạt được đúng khi: critical (60) + tầng 2 (20) + tin cậy cao (10) +
// tác động toàn phần (10).
export const SCORING = Object.freeze({
  STATUS: "PROVISIONAL",
  MAX: 100,
  BASE_SEVERITY: Object.freeze({ critical: 60, warning: 35, info: 10 }),
  LAYER_BOOST: Object.freeze({ 1: 10, 2: 20, 3: 15, null: 0 }),
  CONFIDENCE_BOOST: Object.freeze({ high: 10, medium: 5, low: 0 }),
  /** Hệ số quy đổi tác động đã chuẩn hoá [0,1] sang điểm. */
  IMPACT_MAX: 10,
  /** Cộng thêm cho mỗi dự án bị ảnh hưởng NGOÀI dự án đầu tiên, ở tín hiệu gộp. */
  AGGREGATE_SPREAD_BONUS: 2,
  EPSILON: 1e-9,
  /** Tác động CỐ ĐỊNH cho các luật không đo được tỷ lệ. Chọn tay ⇒ PROVISIONAL.
   *    1.00 — nguồn không đọc được / dữ liệu tự mâu thuẫn: toàn bộ phạm vi mù
   *    0.50 — đọc được nhưng không dùng để ra quyết định được
   *    0.00 — quan sát đúng và không hàm ý mất mát nào */
  FIXED_IMPACT: Object.freeze({
    UNAVAILABLE: 1,
    CONTRADICTORY: 1,
    DEGRADED: 0.5,
    OBSERVATION_ONLY: 0,
  }),
});

export const SCORING_FORMULA_NOTE =
  "attentionScore = mức độ (critical 60 / warning 35 / info 10) + tầng (T1 10 / T2 20) + " +
  "độ tin cậy (cao 10 / trung bình 5 / thấp 0) + tác động đã chuẩn hoá × 10, chặn ở 0–100. " +
  "Công thức TẠM (PROVISIONAL); hằng số nằm ở `SCORING` trong frontend/src/utils/signals.js.";

/** CHÍNH SÁCH XẾP TẦNG — vì sao Tầng 1 KHÔNG mặc nhiên trên Tầng 2.
 *
 *  Lập luận: một sự cố tin cậy dữ liệu ở mức `critical` (đồng bộ hỏng, hấp thụ
 *  tự mâu thuẫn, không đọc được danh mục) làm cho MỌI kết luận thương mại tính
 *  từ chính dữ liệu đó trở nên vô nghĩa — xử lý "vận tốc giảm" trên một lô đồng
 *  bộ hỏng là hành động sai trên số sai. Nên nhóm này lên đầu.
 *
 *  Nhưng một cảnh báo Tầng 1 KHÔNG nghiêm trọng (dữ liệu cũ 25 giờ, một số căn
 *  chưa chấm được điểm) thì con số vẫn dùng được, chỉ kém tươi. Để nó chặn trên
 *  một rủi ro thương mại thật sẽ biến bảng tín hiệu thành hàng đợi dọn dữ liệu —
 *  đúng cái khiếm khuyết mà đợt này phải sửa.
 *
 *  Do đó BỐN bậc, xét trước mọi thứ khác:
 *    0 — Tầng 1 severity critical   (dữ liệu không dùng được ⇒ sửa trước)
 *    1 — Tầng 2 (và Tầng 3 nếu có)  (rủi ro thương mại thật)
 *    2 — Tầng 1 còn lại             (suy giảm chất lượng, chưa chặn quyết định)
 *    3 — layer null                 (vận hành nội bộ: dự báo chưa có, trần quét)
 */
export const LAYER_POLICY_NOTE =
  "Thứ tự: (0) Tầng 1 nghiêm trọng — dữ liệu không dùng được; (1) Tầng 2 — rủi ro thương mại; " +
  "(2) Tầng 1 còn lại — suy giảm chất lượng; (3) vận hành nội bộ. Trong cùng bậc: điểm chú ý giảm dần, " +
  "rồi mức độ, độ tin cậy, số căn ảnh hưởng, cuối cùng mới tới id.";

export function priorityTier(signal) {
  if (signal.layer === 1) return signal.severity === "critical" ? 0 : 2;
  if (signal.layer === 2 || signal.layer === 3) return 1;
  return 3;
}

/** Chặn một tỷ lệ về [0, 1]. Trả 0 khi không tính được — KHÔNG đoán. */
export function normalizeImpact(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n > 1 ? 1 : n;
}

export function computeAttentionScore({ severity, layer, confidence, impact = 0 }) {
  const base = SCORING.BASE_SEVERITY[severity] ?? 0;
  const layerBoost = SCORING.LAYER_BOOST[layer === null || layer === undefined ? "null" : layer] ?? 0;
  const confidenceBoost = SCORING.CONFIDENCE_BOOST[confidence] ?? 0;
  const impactBoost = normalizeImpact(impact) * SCORING.IMPACT_MAX;
  const total = base + layerBoost + confidenceBoost + impactBoost;
  return Math.max(0, Math.min(SCORING.MAX, Math.round(total * 100) / 100));
}

export function isMissing(value) {
  return value === null || value === undefined;
}

function num(value) {
  if (isMissing(value)) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Dựng một tín hiệu và GẮN điểm chú ý ngay tại chỗ, để không tồn tại đường nào
 *  sinh ra tín hiệu thiếu điểm. `impact` là tỷ lệ [0,1] đã có bằng chứng. */
function signal(base) {
  const { impact = 0, impactNote, ...rest } = base;
  const layer = rest.layer === undefined ? null : rest.layer;
  const attentionScore = computeAttentionScore({
    severity: rest.severity,
    layer,
    confidence: rest.confidence,
    impact,
  });
  const evidence = { ...(rest.evidence || {}) };
  evidence.attentionScore = attentionScore;
  evidence.attentionFormula = SCORING_FORMULA_NOTE;
  const details = Array.isArray(evidence.details) ? [...evidence.details] : [];
  details.push(
    `Điểm chú ý ${attentionScore}/100 — ${SCORING_FORMULA_NOTE}` +
      (impactNote ? ` Tác động lấy từ: ${impactNote}` : " Luật này không đo được tỷ lệ tác động nên phần tác động = 0."),
  );
  evidence.details = details;
  return {
    status: "open",
    links: [],
    affectedUnits: null,
    ...rest,
    // SAU phần spread: `rest` cũng mang `layer`, nên chuẩn hoá trước rồi spread
    // sẽ đặt lại `undefined` cho bất kỳ luật nào sau này quên khai trường đó.
    layer,
    attentionScore,
    evidence,
  };
}

function projectRef(project) {
  return {
    id: project?.external_id || project?.project_id || "unknown",
    label: project?.name || project?.external_id || project?.project_id || "Dự án chưa có tên",
    projectId: project?.project_id,
    externalId: project?.external_id,
  };
}

function projectLinks(ref) {
  if (!ref.externalId) return [];
  const e = encodeURIComponent(ref.externalId);
  return [
    { label: `Phân tích hấp thụ ${ref.externalId}`, href: `/projects/${e}/dashboard` },
    { label: `Mở dự án ${ref.externalId}`, href: `/projects/${e}` },
  ];
}

/** Câu cảnh báo GỘP-vs-RÒNG, dán vào MỌI tín hiệu nói về vận tốc hay tồn kho. */
const GROSS_CAVEAT =
  "Số GỘP: bộ tính miền chỉ đếm giao dịch `sold` còn sống, KHÔNG trừ `lost`/`cancelled` " +
  "(khối 'Quy tắc đếm' đầu src/services/domain_absorption.py). Hệ thống chưa có bất kỳ số hấp thụ RÒNG nào.";

// --- A. Hấp thụ -------------------------------------------------------------

const DATA_STATUS_MEANING = {
  no_data: "Không có dữ liệu hấp thụ nào được tính cho phạm vi này.",
  no_units: "Phạm vi này không có căn nào để tính hấp thụ.",
  insufficient_data: "Chuỗi dữ liệu quá ngắn để kết luận.",
};

function absorptionSignals({ entry, nowIso, now }) {
  const { project, absorption, absorptionError } = entry;
  const ref = projectRef(project);
  const out = [];

  if (absorptionError) {
    out.push(
      signal({
        id: `absorption:unavailable:${ref.id}`,
        ruleId: "absorption:unavailable",
        category: "absorption",
        layer: 1,
        severity: "warning",
        scope: "project",
        title: `Không đọc được hấp thụ của "${ref.label}"`,
        whatHappened: `GET /api/v1/absorption/summary thất bại${absorptionError.status ? ` (HTTP ${absorptionError.status})` : ""}.`,
        whyItMatters:
          "Không có số hấp thụ thì không đánh giá được tốc độ bán của dự án này ở lần tải hiện tại; ô trống dễ bị đọc nhầm thành 'bán chậm'.",
        evidence: {
          currentValue: absorptionError.status ? `HTTP ${absorptionError.status}` : "Lỗi mạng",
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/absorption/summary",
          sourcePath: "src/api/dashboard.py — absorption_summary",
          detectedAt: nowIso,
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [absorptionError.message || "Không có thông điệp lỗi"],
        },
        recommendedAction: "Tải lại trang. Nếu lặp lại, kiểm tra log backend cho tuyến /absorption/summary của dự án này.",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: null,
        impact: SCORING.FIXED_IMPACT.UNAVAILABLE,
        impactNote: "tác động CỐ ĐỊNH 1.0 — nguồn không đọc được thì toàn bộ phạm vi của dự án này đang mù",
        createdAt: nowIso,
      }),
    );
    return out;
  }

  if (!absorption || typeof absorption !== "object") return out;

  const status = absorption.data_status;
  const sold = num(absorption.units_sold);
  const remaining = num(absorption.units_remaining);
  const total = num(absorption.total_units);
  const v7 = num(absorption.velocity_7d);
  const v30 = num(absorption.velocity_30d ?? absorption.avg_velocity_30d);
  const unit = absorption.velocity_unit === "units_per_week" ? "căn/tuần" : "căn/ngày";

  // A1. data_status — enum CÓ THẬT của backend, không phải suy đoán.
  if (!isMissing(status) && status !== "ready") {
    // Phân biệt "0/0 hợp lệ" (không có căn nào) với "0/0 đáng ngờ" (có hoạt
    // động miền nhưng vẫn không tính ra hấp thụ).
    const hasActivity = (sold !== null && sold > 0) || (remaining !== null && remaining > 0);
    const suspicious = status === "no_data" && hasActivity;
    out.push(
      signal({
        id: `absorption:data-status:${status}:${ref.id}`,
        ruleId: `absorption:data-status:${status}`,
        category: "absorption",
        layer: 1,
        severity: suspicious ? "critical" : status === "no_units" ? "info" : "warning",
        scope: "project",
        title: suspicious
          ? `Hấp thụ trống dù "${ref.label}" có hoạt động`
          : `Hấp thụ chưa sẵn sàng cho "${ref.label}" (${status})`,
        whatHappened: suspicious
          ? `data_status = "no_data" nhưng dự án ghi nhận units_sold = ${sold ?? "Không có"}, units_remaining = ${remaining ?? "Không có"}.`
          : `data_status = "${status}". ${DATA_STATUS_MEANING[status] || ""}`,
        whyItMatters: suspicious
          ? "Có hoạt động miền nhưng không có đầu ra hấp thụ: lần tính lại nhiều khả năng chưa chạy hoặc đã hỏng, nên mọi chỉ số tốc độ bán của dự án này đang sai lệch."
          : status === "no_units"
            ? "Không có căn nào trong phạm vi, nên số hấp thụ bằng 0 là ĐÚNG chứ không phải bán chậm — cần đọc đúng để không kết luận nhầm."
            : "Chưa đủ dữ liệu để kết luận về tốc độ bán; con số hiện tại không nên dùng để ra quyết định.",
        evidence: {
          currentValue: status,
          baselineValue: "ready",
          threshold: 'data_status === "ready"',
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/absorption/summary",
          sourcePath: "src/models/schemas.py — AbsorptionSummaryOut.data_status ∈ ready|no_data|no_units|insufficient_data",
          detectedAt: nowIso,
          dataFreshness: absorption.updated_at || "Không rõ",
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [
            `units_sold: ${sold === null ? "Không có" : sold}`,
            `units_remaining: ${remaining === null ? "Không có" : remaining}`,
            `calculator: ${absorption.calculator || "Không rõ"}`,
          ],
        },
        recommendedAction: suspicious
          ? "Chạy lại tính hấp thụ cho dự án này rồi đối chiếu lineage: kiểm tra projects.absorption_calculator có khớp nguồn dữ liệu đang có hay không."
          : status === "no_units"
            ? "Không cần hành động về tốc độ bán; xác nhận dự án thật sự chưa mở bán căn nào."
            : "Chờ thêm dữ liệu hoặc kiểm tra lô đồng bộ gần nhất trước khi dùng số này.",
        links: projectLinks(ref),
        confidence: suspicious ? "medium" : "high",
        affectedUnits: total,
        impact: suspicious
          ? SCORING.FIXED_IMPACT.CONTRADICTORY
          : status === "no_units"
            ? SCORING.FIXED_IMPACT.OBSERVATION_ONLY
            : SCORING.FIXED_IMPACT.DEGRADED,
        impactNote: suspicious
          ? "tác động CỐ ĐỊNH 1.0 — dữ liệu tự mâu thuẫn"
          : status === "no_units"
            ? "tác động CỐ ĐỊNH 0 — quan sát đúng, không hàm ý mất mát"
            : "tác động CỐ ĐỊNH 0.5 — đọc được nhưng chưa dùng để ra quyết định được",
        createdAt: nowIso,
      }),
    );
  }

  // A2. Vận tốc giảm — luật CÓ THẬT: utils/velocity.js so v7 với v30.
  const direction = deriveVelocityDirection(absorption.velocity_7d, absorption.velocity_30d ?? absorption.avg_velocity_30d);
  if (direction === "decreasing") {
    // Độ lớn mức giảm, chuẩn hoá theo chính nền 30 ngày của dự án. Đây là TỶ LỆ
    // đọc được từ hai con số có thật, không phải một ngưỡng.
    const declineRatio = (v30 - v7) / Math.max(Math.abs(v30), SCORING.EPSILON);
    out.push(
      signal({
        id: `absorption:velocity-decreasing:${ref.id}`,
        ruleId: "absorption:velocity-decreasing",
        category: "absorption",
        layer: 2,
        severity: "warning",
        scope: "project",
        title: `Vận tốc bán GỘP 7 ngày thấp hơn 30 ngày ở "${ref.label}"`,
        whatHappened: `velocity_7d = ${v7} ${unit} so với velocity_30d = ${v30} ${unit} (chênh ${(v7 - v30).toFixed(4)}), tức giảm ${(declineRatio * 100).toFixed(1)}% so với nền 30 ngày.`,
        whyItMatters:
          "Nhịp bán GỘP ngắn hạn đang chậm hơn mức trung bình 30 ngày của chính dự án — dấu hiệu sớm cho thấy đà bán yếu đi, cần xem lại trước khi tồn kho dồn lại.",
        evidence: {
          currentValue: `${v7} ${unit} (7 ngày, gộp)`,
          baselineValue: `${v30} ${unit} (30 ngày, gộp)`,
          delta: Number((v7 - v30).toFixed(4)),
          threshold: "velocity_7d < velocity_30d",
          // Hướng so sánh là luật CÓ THẬT; ĐỘ LỚN "đáng kể" thì repo chưa định nghĩa.
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/absorption/summary",
          sourcePath: "frontend/src/utils/velocity.js — deriveVelocityDirection",
          detectedAt: nowIso,
          dataFreshness: absorption.updated_at || "Không rõ",
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [
            "Ngưỡng ĐỘ LỚN của mức giảm: CHƯA CÓ trong repo — tín hiệu này chỉ nêu chiều giảm, không xếp loại nặng nhẹ.",
            GROSS_CAVEAT,
            `calculator: ${absorption.calculator || "Không rõ"}`,
          ],
        },
        recommendedAction:
          "Mở phân tích hấp thụ của dự án, so chuỗi 7 ngày với 30 ngày theo từng phân khu để xác định phân khu nào kéo nhịp bán xuống.",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: remaining,
        impact: declineRatio,
        impactNote: `(velocity_30d − velocity_7d) / max(|velocity_30d|, ε) = ${declineRatio.toFixed(4)}`,
        createdAt: nowIso,
      }),
    );
  }

  // A3. Vận tốc bằng ĐÚNG 0 — sự kiện quan sát được, không phải ngưỡng tự đặt.
  if (v30 === 0 && status === "ready") {
    out.push(
      signal({
        id: `absorption:velocity-zero:${ref.id}`,
        ruleId: "absorption:velocity-zero",
        category: "absorption",
        layer: 2,
        severity: "warning",
        scope: "project",
        title: `Không ghi nhận căn nào bán ra trong 30 ngày ở "${ref.label}"`,
        whatHappened: `velocity_30d = 0 ${unit} (GỘP) trong khi data_status = "ready" (dữ liệu được coi là đầy đủ).`,
        whyItMatters:
          "Dữ liệu đã sẵn sàng nhưng không có giao dịch `sold` nào trong cửa sổ 30 ngày — đây là quan sát về đà bán gộp, không phải lỗi dữ liệu.",
        evidence: {
          currentValue: `0 ${unit} (gộp)`,
          threshold: "velocity_30d === 0",
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/absorption/summary",
          sourcePath: "src/models/schemas.py — AbsorptionSummaryOut.velocity_30d",
          detectedAt: nowIso,
          dataFreshness: absorption.updated_at || "Không rõ",
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [
            "Chỉ phát khi vận tốc bằng ĐÚNG 0. Repo chưa có ngưỡng 'vận tốc thấp' khác 0 nên không xếp loại thêm.",
            GROSS_CAVEAT,
            `units_remaining: ${remaining === null ? "Không có" : remaining}`,
          ],
        },
        recommendedAction:
          "Xác nhận với đội bán hàng đây là giai đoạn tạm dừng bán hay đà bán thật sự dừng; nếu là dừng bán, ghi chú để không đọc nhầm thành suy giảm.",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: remaining,
        // Vận tốc bằng 0 là mức giảm TOÀN PHẦN so với chính nó: không còn nhịp bán nào.
        impact: 1,
        impactNote: "vận tốc gộp 30 ngày bằng 0 ⇒ mức giảm toàn phần (1.0)",
        createdAt: nowIso,
      }),
    );
  }

  // A5. Tầm nhìn bán hết (tồn kho) — ĐỌC LẠI `estimated_weeks_to_sell_out` mà
  // backend đã tính (`_weeks_to_sell_out` trong src/services/domain_absorption.py),
  // KHÔNG tính lại ở client. Chỉ phát khi có một con số hữu hạn, DƯƠNG:
  //   · null  ⇒ vận tốc bằng 0 hoặc không có (A3 đã nói chuyện đó rồi)
  //   · 0     ⇒ hết hàng, không phải một "tầm nhìn" nào cả
  // KHÔNG có ngưỡng cao/thấp trong repo ⇒ UNDEFINED, và không xếp loại tốt/xấu.
  const weeksToSellOut = num(absorption.estimated_weeks_to_sell_out);
  if (status === "ready" && weeksToSellOut !== null && weeksToSellOut > 0) {
    const months = weeksToSellOut / WEEKS_PER_MONTH;
    out.push(
      signal({
        id: `absorption:sellout-horizon:${ref.id}`,
        ruleId: "absorption:sellout-horizon",
        category: "absorption",
        layer: 2,
        severity: "info",
        scope: "project",
        title: `Tầm nhìn bán hết của "${ref.label}": ${weeksToSellOut.toFixed(1)} tuần tồn kho`,
        whatHappened:
          `estimated_weeks_to_sell_out = ${weeksToSellOut.toFixed(4)} tuần ` +
          `(≈ ${months.toFixed(1)} tháng, chia ${WEEKS_PER_MONTH.toFixed(3)} tuần/tháng), ` +
          `tính từ units_remaining = ${remaining === null ? "Không có" : remaining} và velocity_30d = ${v30 === null ? "Không có" : v30} ${unit}.`,
        whyItMatters:
          "Đây là số tuần cần để bán hết tồn kho hiện tại NẾU giữ nguyên nhịp bán gộp 30 ngày. Nó cho biết áp lực tồn kho của dự án, " +
          "nhưng KHÔNG tự nói là cao hay thấp: hệ thống chưa được cấu hình mốc tồn kho mục tiêu nào.",
        evidence: {
          currentValue: `${weeksToSellOut.toFixed(1)} tuần (≈ ${months.toFixed(1)} tháng)`,
          baselineValue: "Không có — chưa cấu hình mốc tồn kho mục tiêu",
          threshold: "CHƯA CÓ ngưỡng nghiệp vụ cho số tuần/tháng bán hết",
          thresholdStatus: "UNDEFINED",
          source: "GET /api/v1/absorption/summary",
          sourcePath:
            "src/services/domain_absorption.py — _weeks_to_sell_out(remaining, velocity_30d); src/models/schemas.py — AbsorptionSummaryOut.estimated_weeks_to_sell_out",
          detectedAt: nowIso,
          dataFreshness: absorption.updated_at || "Không rõ",
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [
            "CHƯA CÓ NGƯỠNG: repo không định nghĩa bao nhiêu tuần/tháng tồn kho là quá cao. Tín hiệu chỉ nêu con số quan sát được, không xếp loại tốt/xấu và không gợi ý hành động bán hàng cụ thể.",
            GROSS_CAVEAT,
            "Không phải dự báo: đây là phép chia tồn kho cho vận tốc đã xảy ra, không có mô hình chuỗi thời gian nào tham gia (src/jobs/forecast.py là stub).",
            `Phạm vi: toàn dự án "${ref.label}"${absorption.calculator ? ` · calculator: ${absorption.calculator}` : ""}`,
            `Tồn kho dùng để tính: ${remaining === null ? "Không có" : `${remaining} căn`}`,
          ],
        },
        recommendedAction:
          "Đối chiếu con số này giữa các dự án trong danh mục để biết nơi nào tồn kho dồn nhất, rồi chốt một mốc tồn kho mục tiêu để lần sau tín hiệu có thể xếp loại thay vì chỉ quan sát.",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: remaining,
        // KHÔNG có tác động chuẩn hoá: thiếu mốc mục tiêu thì không có gì để so.
        impact: 0,
        impactNote: "không có — chưa có mốc tồn kho mục tiêu nên không chuẩn hoá được tác động",
        createdAt: nowIso,
      }),
    );
  }

  // A4. Độ mới — dùng lại classifyFreshness của repo.
  const freshness = classifyFreshness(absorption, now);
  if (["stale", "sync_failed", "never_synced", "calculation_outdated"].includes(freshness)) {
    // Hai trạng thái dựa trên MỐC THỜI GIAN do tầng này chọn tay ⇒ TẠM.
    // Hai trạng thái còn lại đọc thẳng cờ backend ⇒ đã xác lập.
    const provisional = freshness === "stale" || freshness === "calculation_outdated";
    out.push(
      signal({
        id: `absorption:freshness:${freshness}:${ref.id}`,
        ruleId: `absorption:freshness:${freshness}`,
        category: "absorption",
        layer: 1,
        severity: freshness === "sync_failed" ? "critical" : "warning",
        scope: "project",
        title: `Dữ liệu hấp thụ của "${ref.label}": ${freshness}`,
        whatHappened:
          freshness === "sync_failed"
            ? `last_sync_status = "${absorption.last_sync_status}" — lần đồng bộ gần nhất thất bại.`
            : freshness === "never_synced"
              ? "Chưa có lần đồng bộ thành công nào được ghi nhận."
              : freshness === "calculation_outdated"
                ? `Đã đồng bộ lúc ${absorption.last_successful_sync} nhưng lần tính lại (${absorption.updated_at}) cũ hơn quá ${RECALC_SKEW_AFTER_MS / 60000} phút.`
                : `Lần đồng bộ thành công gần nhất (${absorption.last_successful_sync}) đã quá ngưỡng 24 giờ.`,
        whyItMatters:
          "Mọi con số hấp thụ hiển thị đều được tính từ lần đồng bộ này. Dữ liệu cũ hoặc chưa tính lại khiến tốc độ bán trông chậm hơn hoặc nhanh hơn thực tế.",
        evidence: {
          currentValue: freshness,
          baselineValue: "fresh",
          threshold:
            freshness === "stale"
              ? `${STALE_AFTER_MS} ms (24 giờ)`
              : freshness === "calculation_outdated"
                ? `${RECALC_SKEW_AFTER_MS} ms (5 phút lệch giữa đồng bộ và tính lại)`
                : "trạng thái đồng bộ do backend trả về",
          thresholdStatus: provisional ? "PROVISIONAL" : "VERIFIED",
          source: "GET /api/v1/absorption/summary",
          sourcePath: "frontend/src/utils/freshness.js — classifyFreshness",
          detectedAt: nowIso,
          dataFreshness: absorption.last_successful_sync || absorption.updated_at || "Không rõ",
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: provisional
            ? [
                freshness === "stale"
                  ? "STALE_AFTER_MS là NGƯỠNG TẠM, repo tự ghi rõ trong frontend/src/utils/freshness.js."
                  : "RECALC_SKEW_AFTER_MS (5 phút) cũng là NGƯỠNG TẠM chọn tay, cùng hạng với STALE_AFTER_MS — không phải quyết định nghiệp vụ đã chốt.",
              ]
            : [`last_sync_status: ${absorption.last_sync_status || "Không có"}`],
        },
        recommendedAction:
          freshness === "calculation_outdated"
            ? "Chạy lại tính hấp thụ cho dự án này để thẻ số liệu bắt kịp lô đồng bộ đã nhận."
            : "Kiểm tra lô đồng bộ gần nhất của dự án ở GET /api/v1/sync-runs và chạy lại nếu cần.",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: total,
        impact: freshness === "sync_failed" ? SCORING.FIXED_IMPACT.UNAVAILABLE : SCORING.FIXED_IMPACT.DEGRADED,
        impactNote:
          freshness === "sync_failed"
            ? "tác động CỐ ĐỊNH 1.0 — lô đồng bộ hỏng thì mọi số của phạm vi này đáng ngờ"
            : "tác động CỐ ĐỊNH 0.5 — số vẫn đọc được nhưng đã cũ",
        createdAt: nowIso,
      }),
    );
  }

  return out;
}

// --- B. Dự báo --------------------------------------------------------------

function forecastingSignals({ nowIso }) {
  return [
    signal({
      id: "forecasting:not-implemented",
      ruleId: "forecasting:not-implemented",
      category: "forecasting",
      // Vận hành nội bộ: đây là phát biểu về NĂNG LỰC hệ thống, không phải một
      // quan sát về dữ liệu hay về thương mại của bất kỳ dự án nào.
      layer: null,
      severity: "info",
      scope: "portfolio",
      title: "Forecasting unavailable — chưa triển khai dự báo",
      whatHappened: "Hệ thống hiện không sinh ra bất kỳ đầu ra dự báo nào.",
      whyItMatters:
        "Không thể dùng giá trị hay độ tin cậy dự báo để ra quyết định. Mọi số hấp thụ đang hiển thị là HỒI CỐ (đã xảy ra), không phải dự đoán.",
      evidence: {
        currentValue: "NOT_IMPLEMENTED",
        thresholdStatus: "VERIFIED",
        source: "Mã nguồn backend",
        sourcePath:
          "src/jobs/forecast.py — run_daily_forecast: TODO (MVP 2), trả {areas_total: 0, areas_failed: 0} và không đọc/ghi dữ liệu",
        detectedAt: nowIso,
        details: [
          "Bốn bảng forecasts/forecast_points/forecast_jobs/alerts không có tham chiếu nào từ mã ứng dụng.",
          "Trạng thái là NOT_IMPLEMENTED, KHÔNG phải 'bất ổn' — dự báo chưa từng chạy nên không có sai số để đo.",
          "Tầm nhìn bán hết ở nhóm hấp thụ KHÔNG phải dự báo: đó là tồn kho chia cho vận tốc đã xảy ra.",
        ],
      },
      recommendedAction:
        "Coi dự báo là không khả dụng; dùng bằng chứng hấp thụ hồi cố và xếp hạng hiện có cho tới khi pipeline dự báo được triển khai.",
      // `high`: đường mã chứng minh TRỰC TIẾP rằng năng lực này vắng mặt.
      confidence: "high",
      affectedUnits: null,
      impact: SCORING.FIXED_IMPACT.OBSERVATION_ONLY,
      impactNote: "tác động CỐ ĐỊNH 0 — phát biểu về năng lực, không phải một mất mát đo được",
      createdAt: nowIso,
    }),
  ];
}

// --- C. Xếp hạng ------------------------------------------------------------

function rankingSignals({ entry, nowIso }) {
  const { project, ranking, rankingError } = entry;
  const ref = projectRef(project);
  const out = [];

  if (rankingError) {
    out.push(
      signal({
        id: `ranking:unavailable:${ref.id}`,
        ruleId: "ranking:unavailable",
        category: "ranking",
        layer: 1,
        severity: "warning",
        scope: "project",
        title: `Không đọc được xếp hạng của "${ref.label}"`,
        whatHappened: `GET /api/v1/ranking thất bại${rankingError.status ? ` (HTTP ${rankingError.status})` : ""}.`,
        whyItMatters:
          "Không có bảng xếp hạng thì không biết dự án này có căn nào cần ưu tiên xem lại hay không ở lần tải hiện tại.",
        evidence: {
          currentValue: rankingError.status ? `HTTP ${rankingError.status}` : "Lỗi mạng",
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/ranking",
          sourcePath: "src/api/ranking.py — get_ranking",
          detectedAt: nowIso,
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [rankingError.message || "Không có thông điệp lỗi"],
        },
        recommendedAction: "Tải lại trang; nếu là 404, dự án chưa có external_id hợp lệ để tra xếp hạng.",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: null,
        impact: SCORING.FIXED_IMPACT.UNAVAILABLE,
        impactNote: "tác động CỐ ĐỊNH 1.0 — không đọc được xếp hạng thì toàn bộ dự án nằm ngoài tầm nhìn ưu tiên",
        createdAt: nowIso,
      }),
    );
    return out;
  }

  if (!ranking || typeof ranking !== "object") return out;

  const bands = ranking.band_counts && typeof ranking.band_counts === "object" ? ranking.band_counts : {};
  const low = num(bands.low) ?? 0;
  const medium = num(bands.medium) ?? 0;
  const high = num(bands.high) ?? 0;
  const ranked = num(ranking.units_ranked) ?? 0;
  const skipped = num(ranking.units_skipped) ?? 0;

  // C0. Chưa từng chạy xếp hạng.
  if (isMissing(ranking.computed_at)) {
    out.push(
      signal({
        id: `ranking:never-computed:${ref.id}`,
        ruleId: "ranking:never-computed",
        category: "ranking",
        layer: 1,
        severity: "warning",
        scope: "project",
        title: `"${ref.label}" chưa từng được xếp hạng`,
        whatHappened: "computed_at = null — không có lần chạy xếp hạng nào cho dự án này.",
        whyItMatters:
          "Không có điểm nào để ưu tiên căn bán, nên dự án này đang nằm ngoài mọi quyết định dựa trên xếp hạng.",
        evidence: {
          currentValue: "Chưa có (null)",
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/ranking",
          sourcePath: "src/models/schemas.py — RankingOut.computed_at (NULL nếu chưa từng chạy)",
          detectedAt: nowIso,
          projectId: ref.projectId,
          externalId: ref.externalId,
        },
        recommendedAction: "Chạy POST /api/v1/ranking/run cho dự án này (cần vai trò pipeline_operator).",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: null,
        impact: SCORING.FIXED_IMPACT.UNAVAILABLE,
        impactNote: "tác động CỐ ĐỊNH 1.0 — chưa chạy lần nào thì không căn nào của dự án có thứ tự ưu tiên",
        createdAt: nowIso,
      }),
    );
    return out;
  }

  // C1. Căn ở mức THẤP — dùng band cutoff CÓ THẬT của bands.py (<0.33).
  if (low > 0) {
    const total = low + medium + high;
    const share = total > 0 ? (low / total) * 100 : null;
    // Tác động = tỷ trọng căn mức thấp trên số căn ĐÃ CHẤM ĐIỂM. Dùng
    // `units_ranked` khi có (đúng mẫu số của lần chạy), lùi về tổng band khi
    // backend không gửi kèm số liệu lần chạy.
    const impactDenominator = ranked > 0 ? ranked : total;
    out.push(
      signal({
        id: `ranking:low-band-units:${ref.id}`,
        ruleId: "ranking:low-band-units",
        category: "ranking",
        // Tồn kho khó bán là rủi ro THƯƠNG MẠI, không phải lỗi dữ liệu.
        layer: 2,
        severity: "warning",
        scope: "project",
        title: `${low} căn ở mức xếp hạng thấp tại "${ref.label}"`,
        whatHappened: `band_counts.low = ${low} trên ${total} căn được chấm điểm${share === null ? "" : ` (${share.toFixed(1)}%)`}.`,
        whyItMatters:
          "Mức 'low' nghĩa là điểm ưu tiên dưới 0.33 — nhóm căn này khó được đội bán hàng chú ý tới nếu chỉ đi từ trên xuống, nên cần rà soát riêng.",
        evidence: {
          currentValue: low,
          baselineValue: `${total} căn được chấm điểm`,
          band: "low",
          threshold: "score < 0.33",
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/ranking",
          sourcePath: "src/ranking/bands.py — BAND_HIGH_MIN 0.66 / BAND_MEDIUM_MIN 0.33",
          detectedAt: nowIso,
          dataFreshness: ranking.computed_at,
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [
            `Phân bố mức: high=${high}, medium=${medium}, low=${low}`,
            `Phiên bản cấu hình: v${ranking.config_version ?? "Không rõ"}`,
            "Đây là số CĂN theo mức trong một dự án — KHÔNG phải xếp hạng của bản thân dự án (repo chưa định nghĩa cách gộp).",
          ],
        },
        recommendedAction:
          "Mở bảng xếp hạng của dự án, lọc mức 'low' và xem cột đóng góp đặc trưng để biết căn nào thấp vì hết hàng và căn nào thấp vì thiếu nhu cầu.",
        links: [
          ...(ref.externalId ? [{ label: `Xem xếp hạng ${ref.externalId}`, href: `/ranking?project=${encodeURIComponent(ref.externalId)}` }] : []),
          ...projectLinks(ref),
        ],
        confidence: "high",
        affectedUnits: low,
        impact: impactDenominator > 0 ? low / impactDenominator : 0,
        impactNote: `band_counts.low / ${ranked > 0 ? "units_ranked" : "tổng band"} = ${low}/${impactDenominator}`,
        createdAt: nowIso,
      }),
    );
  }

  // C2. Căn bị BỎ QUA vì thiếu đặc trưng — độ tin của kết quả xếp hạng.
  if (skipped > 0) {
    const processed = ranked + skipped;
    // Tỷ lệ PHỦ của lần chạy. Có mẫu số thật (`units_ranked + units_skipped`)
    // nên tính được; nhưng KHÔNG có mốc nào nói bao nhiêu là đủ.
    const coverageRatio = processed > 0 ? ranked / processed : null;
    out.push(
      signal({
        id: `ranking:skipped-units:${ref.id}`,
        ruleId: "ranking:skipped-units",
        category: "ranking",
        layer: 1,
        severity: "warning",
        scope: "project",
        title: `${skipped} căn không chấm được điểm tại "${ref.label}"${coverageRatio === null ? "" : ` — phủ ${(coverageRatio * 100).toFixed(1)}%`}`,
        whatHappened:
          `units_skipped = ${skipped} trên ${processed} căn xử lý; units_ranked = ${ranked}` +
          (coverageRatio === null
            ? "; không tính được tỷ lệ phủ vì mẫu số bằng 0."
            : `; tỷ lệ phủ = units_ranked / (units_ranked + units_skipped) = ${ranked}/${processed} = ${(coverageRatio * 100).toFixed(1)}%.`),
        whyItMatters:
          "Căn bị bỏ qua không có điểm và không có thứ hạng, nên chúng biến mất khỏi mọi bảng ưu tiên — thiếu dữ liệu đang bị đọc thành 'không đáng chú ý'.",
        evidence: {
          // Số ĐẾM giữ nguyên kiểu số, cùng quy ước với band_counts.low ở C1.
          currentValue: skipped,
          baselineValue: `${processed} căn được xử lý`,
          delta: coverageRatio === null ? "Không tính được" : `Tỷ lệ phủ ${(coverageRatio * 100).toFixed(1)}%`,
          threshold: "CHƯA CÓ ngưỡng tỷ lệ phủ tối thiểu ở cấp lần chạy",
          // Điều kiện phát (`units_skipped > 0`) là quan sát CHÍNH XÁC; nhưng câu
          // hỏi sản phẩm "phủ bao nhiêu là đủ" thì repo chưa trả lời được ⇒ tỷ lệ
          // được nêu mà KHÔNG xếp loại.
          thresholdStatus: "UNDEFINED",
          source: "GET /api/v1/ranking",
          sourcePath: "src/ranking/engine.py — coverage < min_weight_coverage ⇒ skipped=True",
          detectedAt: nowIso,
          dataFreshness: ranking.computed_at,
          projectId: ref.projectId,
          externalId: ref.externalId,
          details: [
            `units_ranked = ${ranked}; units_skipped = ${skipped}; tỷ lệ phủ = ${coverageRatio === null ? "Không tính được" : coverageRatio.toFixed(4)}`,
            "Điều kiện phát tín hiệu (units_skipped > 0) là quan sát chính xác. Cái CHƯA CÓ là mốc 'phủ bao nhiêu phần trăm thì chấp nhận được' — nên tỷ lệ chỉ được nêu, không xếp loại.",
            "`min_weight_coverage` của ranking_configs là cổng chấm điểm TỪNG CĂN, không phải ngưỡng tỷ lệ phủ của cả lần chạy, và GET /ranking cũng không trả kèm giá trị đó.",
            `Phiên bản cấu hình: v${ranking.config_version ?? "Không rõ"}`,
          ],
        },
        recommendedAction:
          "Bổ sung dữ liệu đặc trưng còn thiếu cho các căn này (trạng thái căn, giao dịch của phân khu) rồi chạy lại xếp hạng.",
        links: projectLinks(ref),
        confidence: "high",
        affectedUnits: skipped,
        impact: processed > 0 ? skipped / processed : 0,
        impactNote: `units_skipped / (units_ranked + units_skipped) = ${skipped}/${processed}`,
        createdAt: nowIso,
      }),
    );
  }

  return out;
}

// --- Gộp cấp danh mục -------------------------------------------------------

/** Chỉ những trường có ĐƠN VỊ tương thích mới được cộng/trung bình. Số CĂN thì
 *  cộng được. Vận tốc thì KHÔNG: hai dự án có thể trả `velocity_unit` khác nhau
 *  (`units_per_day` ở bộ tính cũ, `units_per_week` ở bộ tính miền — xem
 *  src/api/dashboard.py), nên không có "vận tốc trung bình danh mục" nào ở đây. */
const AGGREGATE_MIN_PROJECTS = 2;

function aggregateConfidence(children) {
  // Chính sách BẢO THỦ: tín hiệu gộp không thể chắc hơn đứa con kém chắc nhất.
  return children.reduce(
    (worst, c) => ((CONFIDENCE_ORDER[c.confidence] ?? 9) > (CONFIDENCE_ORDER[worst] ?? 9) ? c.confidence : worst),
    "high",
  );
}

function aggregateSeverity(children) {
  return children.reduce(
    (worst, c) => ((SEVERITY_ORDER[c.severity] ?? 9) < (SEVERITY_ORDER[worst] ?? 9) ? c.severity : worst),
    "info",
  );
}

/**
 * Gộp các tín hiệu CẤP DỰ ÁN cùng `ruleId` thành một tín hiệu cấp danh mục.
 * Chỉ gộp khi có TỪ 2 DỰ ÁN trở lên — một dự án thì tín hiệu gộp không nói thêm
 * được gì mà chỉ thêm một lớp phải bấm mở.
 *
 * Tín hiệu con KHÔNG bị xoá: chúng đi kèm trong `children` và giao diện phải
 * hiện được toàn bộ bằng chứng của từng dự án.
 */
export function aggregatePortfolio(signals, { nowIso }) {
  const groups = new Map();
  const passthrough = [];

  for (const s of signals) {
    if (s.scope !== "project") {
      passthrough.push(s);
      continue;
    }
    const list = groups.get(s.ruleId) || [];
    list.push(s);
    groups.set(s.ruleId, list);
  }

  const out = [...passthrough];

  for (const [ruleId, children] of groups) {
    const projectIds = [...new Set(children.map((c) => c.evidence.externalId || c.evidence.projectId).filter(Boolean))];
    if (children.length < AGGREGATE_MIN_PROJECTS || projectIds.length < AGGREGATE_MIN_PROJECTS) {
      out.push(...children);
      continue;
    }

    const sample = children[0];
    const severity = aggregateSeverity(children);
    const confidence = aggregateConfidence(children);
    const knownUnits = children.map((c) => c.affectedUnits).filter((u) => typeof u === "number" && Number.isFinite(u));
    // Chỉ cộng khi MỌI con đều biết số căn — cộng một phần rồi gọi là tổng là nói dối.
    const affectedUnits = knownUnits.length === children.length ? knownUnits.reduce((a, b) => a + b, 0) : null;
    const maxChildScore = Math.max(...children.map((c) => c.attentionScore));
    const attentionScore = Math.min(
      SCORING.MAX,
      Math.round((maxChildScore + SCORING.AGGREGATE_SPREAD_BONUS * (projectIds.length - 1)) * 100) / 100,
    );
    const projectList = children
      .map((c) => c.evidence.externalId || c.evidence.projectId || "Không rõ")
      .filter((v, i, a) => a.indexOf(v) === i);

    out.push({
      id: `portfolio:${ruleId}`,
      ruleId: `portfolio:${ruleId}`,
      category: sample.category,
      layer: sample.layer,
      severity,
      status: "open",
      scope: "portfolio",
      title: `${projectIds.length} dự án cùng gặp: ${stripProjectSuffix(sample.title)}`,
      whatHappened:
        `Cùng một luật (${ruleId}) phát tín hiệu ở ${projectIds.length} dự án trong lần tải này: ${projectList.join(", ")}.` +
        (affectedUnits === null
          ? " Không cộng được tổng số căn vì có dự án không cho biết số căn của luật này."
          : ` Tổng số căn mà luật này đo được trên các dự án đó: ${affectedUnits}.`),
      whyItMatters:
        `Vấn đề này không cục bộ ở một dự án mà lặp ở ${projectIds.length} dự án, nên nhiều khả năng nguyên nhân nằm ở khâu dùng chung ` +
        "(lô đồng bộ, cấu hình xếp hạng, quy trình vận hành) chứ không ở riêng dự án nào. Xử lý từng dự án một sẽ tốn công hơn xử lý nguyên nhân chung.",
      evidence: {
        currentValue: `${projectIds.length} dự án bị ảnh hưởng`,
        baselineValue: affectedUnits === null ? "Không có — không cộng được số căn" : `${affectedUnits} căn (tổng cộng)`,
        threshold: `Gộp khi có từ ${AGGREGATE_MIN_PROJECTS} dự án trở lên cùng luật`,
        thresholdStatus: "PROVISIONAL",
        source: "Gộp ở client từ các tín hiệu cấp dự án của cùng lần tải",
        sourcePath: "frontend/src/utils/signals.js — aggregatePortfolio",
        detectedAt: nowIso,
        details: [
          `Dự án bị ảnh hưởng: ${projectList.join(", ")}`,
          `Mức độ = mức nặng nhất trong nhóm (${SEVERITY_LABEL[severity] || severity}); độ tin cậy = mức THẤP nhất trong nhóm (${CONFIDENCE_LABEL[confidence] || confidence}) theo chính sách bảo thủ.`,
          `Điểm chú ý = điểm cao nhất của nhóm (${maxChildScore}) + ${SCORING.AGGREGATE_SPREAD_BONUS} điểm cho mỗi dự án ngoài dự án đầu tiên, chặn ở ${SCORING.MAX}. Hằng số lan rộng là TẠM.`,
          affectedUnits === null
            ? "KHÔNG cộng tổng số căn: ít nhất một dự án trong nhóm không cho biết số căn của luật này."
            : "Tổng số căn chỉ cộng khi MỌI dự án trong nhóm đều cho biết số căn của luật này.",
          "KHÔNG có trung bình vận tốc toàn danh mục: `velocity_unit` có thể khác nhau giữa các dự án (units_per_day ở bộ tính cũ, units_per_week ở bộ tính miền), nên các giá trị đó không cộng/chia được với nhau.",
          "Bằng chứng của từng dự án được giữ nguyên trong các tín hiệu con bên dưới, không bị nén mất.",
        ],
        attentionScore,
        attentionFormula: SCORING_FORMULA_NOTE,
      },
      recommendedAction:
        `Xử lý theo nguyên nhân chung trước: kiểm tra xem ${projectIds.length} dự án này có chung lô đồng bộ, chung phiên bản cấu hình xếp hạng hay chung quy trình vận hành hay không. ` +
        `Bằng chứng riêng của từng dự án nằm ở ${children.length} tín hiệu con.`,
      links: [],
      confidence,
      attentionScore,
      affectedUnits,
      affectedProjectCount: projectIds.length,
      affectedProjects: projectList,
      childIds: children.map((c) => c.id),
      children: sortSignals(children),
      createdAt: nowIso,
    });
  }

  return out;
}

/** Bỏ phần `tại "Tên dự án"` / `của "Tên dự án"` khỏi tiêu đề con khi dựng tiêu
 *  đề gộp — tiêu đề gộp nói về NHIỀU dự án nên không được mang tên một dự án. */
function stripProjectSuffix(title) {
  return title
    .replace(/\s+(tại|của|ở)\s+"[^"]*"/g, "")
    .replace(/^"[^"]*"\s+/, "")
    .trim();
}

// --- Điểm vào ---------------------------------------------------------------

/**
 * @param {Object} sources
 * @param {Array|null}  [sources.entries]  [{ project, absorptionRequested, absorption, absorptionError, ranking, rankingError }]
 * @param {Object|null} [sources.projectsError]
 * @param {number}      [sources.skippedProjectCount]    dự án KHÔNG được quét gì cả (ngoài trần xếp hạng)
 * @param {number}      [sources.absorptionSkippedCount] dự án CÓ tín hiệu xếp hạng nhưng KHÔNG nạp hấp thụ
 * @param {Object}      [options]
 * @param {Date}        [options.now]
 * @returns {Signal[]}
 */
export function deriveSignals(sources = {}, options = {}) {
  const now = options.now instanceof Date ? options.now : new Date();
  const nowIso = now.toISOString();
  const out = [];

  if (sources.projectsError) {
    out.push(
      signal({
        id: "portfolio:projects-unavailable",
        ruleId: "portfolio:projects-unavailable",
        category: "absorption",
        layer: 1,
        severity: "critical",
        scope: "portfolio",
        title: "Không đọc được danh mục dự án",
        whatHappened: `GET /api/v1/projects thất bại${sources.projectsError.status ? ` (HTTP ${sources.projectsError.status})` : ""}.`,
        whyItMatters:
          "Không có danh sách dự án thì không tính được tín hiệu hấp thụ hay xếp hạng nào — mục này đang KHÔNG theo dõi gì cả, chứ không phải 'không có vấn đề'.",
        evidence: {
          currentValue: sources.projectsError.status ? `HTTP ${sources.projectsError.status}` : "Lỗi mạng",
          thresholdStatus: "VERIFIED",
          source: "GET /api/v1/projects",
          sourcePath: "src/api/dashboard.py — list_projects",
          detectedAt: nowIso,
          details: [sources.projectsError.message || "Không có thông điệp lỗi"],
        },
        recommendedAction: "Tải lại trang. Nếu 401/403, kiểm tra token vai trò và phạm vi dự án được cấp.",
        confidence: "high",
        affectedUnits: null,
        impact: SCORING.FIXED_IMPACT.UNAVAILABLE,
        impactNote: "tác động CỐ ĐỊNH 1.0 — mất danh mục thì toàn bộ trang không theo dõi được gì",
        createdAt: nowIso,
      }),
    );
  }

  for (const entry of Array.isArray(sources.entries) ? sources.entries : []) {
    // Hấp thụ CHỈ được suy ra cho dự án thật sự có gọi `/absorption/summary`.
    // Cờ tường minh, không suy từ `absorption == null` — "không gọi" và "gọi
    // xong không có dữ liệu" là hai chuyện khác nhau.
    if (entry?.absorptionRequested !== false) {
      out.push(...absorptionSignals({ entry, nowIso, now }));
    }
    out.push(...rankingSignals({ entry, nowIso }));
  }

  out.push(...forecastingSignals({ nowIso }));

  // Trần 1: dự án KHÔNG được quét gì cả.
  if (num(sources.skippedProjectCount) > 0) {
    const scanned = Array.isArray(sources.entries) ? sources.entries.length : 0;
    out.push(
      signal({
        id: "portfolio:project-limit",
        ruleId: "portfolio:project-limit",
        category: "absorption",
        layer: null,
        severity: "info",
        scope: "portfolio",
        title: `${sources.skippedProjectCount} dự án chưa được quét tín hiệu`,
        whatHappened: `Chỉ ${scanned} dự án đầu tiên được nạp ở lần tải này; phần còn lại không có tín hiệu nào, kể cả xếp hạng.`,
        whyItMatters:
          "Hấp thụ và xếp hạng chỉ có endpoint theo TỪNG dự án. Đặt trần để số request không tăng vô hạn theo số dự án; hệ quả là danh sách tín hiệu chưa phủ hết danh mục.",
        evidence: {
          currentValue: `${scanned} dự án đã quét`,
          baselineValue: `${scanned + Number(sources.skippedProjectCount)} dự án trong phạm vi`,
          threshold: "RANKING_PROJECT_LIMIT — trần nạp xếp hạng của trang",
          thresholdStatus: "PROVISIONAL",
          source: "frontend/src/utils/globalUnitRanking.js",
          sourcePath: "RANKING_PROJECT_LIMIT — trần do frontend đặt, chưa có endpoint tổng hợp toàn danh mục",
          detectedAt: nowIso,
          details: ["Cần một endpoint tổng hợp hấp thụ/xếp hạng ở phạm vi danh mục để bỏ trần này."],
        },
        recommendedAction:
          "Mở từng dự án còn lại để xem hấp thụ/xếp hạng, hoặc đề xuất bổ sung endpoint tổng hợp toàn danh mục ở backend.",
        confidence: "high",
        affectedUnits: null,
        impact: SCORING.FIXED_IMPACT.OBSERVATION_ONLY,
        impactNote: "tác động CỐ ĐỊNH 0 — giới hạn phạm vi quét, không phải một mất mát đo được",
        createdAt: nowIso,
      }),
    );
  }

  // Trần 2: dự án CÓ tín hiệu xếp hạng nhưng KHÔNG nạp hấp thụ. Phải nói riêng,
  // nếu không người đọc sẽ tưởng những dự án đó không có vấn đề hấp thụ nào.
  if (num(sources.absorptionSkippedCount) > 0) {
    out.push(
      signal({
        id: "portfolio:absorption-limit",
        ruleId: "portfolio:absorption-limit",
        category: "absorption",
        layer: null,
        severity: "info",
        scope: "portfolio",
        title: `${sources.absorptionSkippedCount} dự án chỉ được quét xếp hạng, chưa quét hấp thụ`,
        whatHappened: `Hấp thụ chỉ được nạp cho ${SIGNAL_PROJECT_LIMIT} dự án đầu tiên (1 request/dự án). Các dự án còn lại trong phạm vi xếp hạng vẫn có đủ tín hiệu nhóm C, nhưng KHÔNG có tín hiệu nhóm A.`,
        whyItMatters:
          "Vắng tín hiệu hấp thụ ở những dự án đó KHÔNG có nghĩa là hấp thụ của chúng ổn — chỉ là chưa nạp. Không nói rõ thì im lặng sẽ bị đọc thành 'không có vấn đề'.",
        evidence: {
          currentValue: `${SIGNAL_PROJECT_LIMIT} dự án được quét hấp thụ`,
          baselineValue: `${SIGNAL_PROJECT_LIMIT + Number(sources.absorptionSkippedCount)} dự án được quét xếp hạng`,
          threshold: `SIGNAL_PROJECT_LIMIT = ${SIGNAL_PROJECT_LIMIT}`,
          thresholdStatus: "PROVISIONAL",
          source: "frontend/src/utils/signals.js",
          sourcePath: "SIGNAL_PROJECT_LIMIT — trần request hấp thụ do frontend đặt",
          detectedAt: nowIso,
          details: [
            "Xếp hạng KHÔNG bị trần này: trang đã nạp sẵn xếp hạng cho bảng xếp hạng toàn cục nên nhóm C chạy trên toàn bộ phần đã nạp, không tốn thêm request.",
            "Bỏ được trần này khi backend có endpoint hấp thụ ở phạm vi danh mục.",
          ],
        },
        recommendedAction:
          "Mở dashboard của từng dự án còn lại để xem hấp thụ, hoặc đề xuất endpoint hấp thụ tổng hợp toàn danh mục ở backend.",
        confidence: "high",
        affectedUnits: null,
        impact: SCORING.FIXED_IMPACT.OBSERVATION_ONLY,
        impactNote: "tác động CỐ ĐỊNH 0 — giới hạn phạm vi quét, không phải một mất mát đo được",
        createdAt: nowIso,
      }),
    );
  }

  const seen = new Set();
  const unique = out.filter((s) => (seen.has(s.id) ? false : (seen.add(s.id), true)));
  return sortSignals(aggregatePortfolio(unique, { nowIso }));
}

/** Bậc tầng → điểm chú ý giảm dần → mức độ → độ tin cậy → số căn ảnh hưởng → id.
 *  `id` chỉ còn phá hoà khi mọi dữ kiện có nghĩa đều bằng nhau. */
export function sortSignals(signals) {
  return [...signals].sort((a, b) => {
    const tier = priorityTier(a) - priorityTier(b);
    if (tier !== 0) return tier;

    const score = (b.attentionScore ?? 0) - (a.attentionScore ?? 0);
    if (score !== 0) return score;

    const sev = (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99);
    if (sev !== 0) return sev;

    const conf = (CONFIDENCE_ORDER[a.confidence] ?? 99) - (CONFIDENCE_ORDER[b.confidence] ?? 99);
    if (conf !== 0) return conf;

    const units = (b.affectedUnits ?? -1) - (a.affectedUnits ?? -1);
    if (units !== 0) return units;

    const st = (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99);
    if (st !== 0) return st;

    return a.id.localeCompare(b.id);
  });
}

/** Trải phẳng cha + con. Mọi phép ĐẾM phải chạy trên bản trải phẳng, nếu không
 *  việc gộp sẽ làm các con số tự nhiên tụt xuống. */
export function flattenSignals(signals = []) {
  const out = [];
  for (const s of signals) {
    out.push(s);
    if (Array.isArray(s.children)) out.push(...s.children);
  }
  return out;
}

export function summarizeSignals(signals = []) {
  const flat = flattenSignals(signals);
  return flat.reduce(
    (acc, s) => {
      if (s.severity === "critical") acc.critical += 1;
      else if (s.severity === "warning") acc.warning += 1;
      else if (s.severity === "info") acc.info += 1;
      if (s.status === "open") acc.open += 1;
      acc.byCategory[s.category] = (acc.byCategory[s.category] || 0) + 1;
      if (s.layer === 1) acc.layer1 += 1;
      else if (s.layer === 2) acc.layer2 += 1;
      return acc;
    },
    { critical: 0, warning: 0, info: 0, open: 0, total: flat.length, layer1: 0, layer2: 0, byCategory: {} },
  );
}

function matches(s, { severity, status, category }) {
  return (
    (severity === "all" || s.severity === severity) &&
    (status === "all" || s.status === status) &&
    (category === "all" || s.category === category)
  );
}

/** Cha được giữ khi CHÍNH nó khớp hoặc có con khớp. Khi cha không khớp, chỉ các
 *  con khớp được hiện — bộ lọc không bao giờ làm mất một tín hiệu con đang khớp. */
export function filterSignals(signals = [], { severity = "all", status = "all", category = "all" } = {}) {
  const criteria = { severity, status, category };
  const out = [];
  for (const s of signals) {
    const selfMatch = matches(s, criteria);
    if (!Array.isArray(s.children)) {
      if (selfMatch) out.push(s);
      continue;
    }
    const kept = s.children.filter((c) => matches(c, criteria));
    if (selfMatch) out.push(s);
    else if (kept.length > 0) out.push({ ...s, children: kept });
  }
  return out;
}
