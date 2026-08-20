import { describe, expect, it } from "vitest";
import {
  SIGNAL_PERSISTENCE_SUPPORTED,
  SIGNAL_PROJECT_LIMIT,
  deriveSignals,
  filterSignals,
  isMissing,
  sortSignals,
  summarizeSignals,
} from "./signals";

const NOW = new Date("2026-08-18T00:00:00Z");
const opts = { now: NOW };

const project = (over = {}) => ({
  project_id: "11111111-1111-5111-8111-111111111111",
  name: "Harbor Crest",
  launch_date: "2025-08-03",
  status: "active",
  external_id: "syn1-P-001",
  source_revision: 3,
  ...over,
});

/** Hình dạng THẬT của AbsorptionSummaryOut (src/models/schemas.py). */
const absorption = (over = {}) => ({
  units_sold: 42,
  units_remaining: 158,
  units_reserved: 10,
  total_units: 200,
  velocity_7d: 0.5,
  velocity_30d: 0.5,
  avg_velocity_30d: 0.5,
  velocity_unit: "units_per_day",
  data_status: "ready",
  data_source: "domain_units_deals",
  calculator: "domain_units_deals",
  updated_at: "2026-08-17T20:00:00Z",
  last_successful_sync: "2026-08-17T20:00:00Z",
  last_attempted_sync: "2026-08-17T20:00:00Z",
  last_sync_status: "completed",
  ...over,
});

/** Hình dạng THẬT của RankingOut (src/models/schemas.py). */
const ranking = (over = {}) => ({
  project_id: "11111111-1111-5111-8111-111111111111",
  external_project_id: "syn1-P-001",
  computed_at: "2026-08-17T21:00:00Z",
  config_version: 2,
  units_ranked: 100,
  units_skipped: 0,
  band_counts: { high: 20, medium: 70, low: 10 },
  items: [],
  total: 100,
  ...over,
});

const entry = (over = {}) => ({
  project: project(),
  absorption: absorption(),
  absorptionError: null,
  ranking: ranking(),
  rankingError: null,
  ...over,
});

const run = (entries, extra = {}) => deriveSignals({ entries, ...extra }, opts);
const byId = (signals, id) => signals.find((s) => s.id === id);
const ids = (signals) => signals.map((s) => s.id);

describe("A. Tín hiệu hấp thụ", () => {
  it("1. vận tốc 30 ngày bằng 0 với data_status='ready' ⇒ cảnh báo, ngưỡng VERIFIED", () => {
    const s = byId(run([entry({ absorption: absorption({ velocity_7d: 0, velocity_30d: 0, avg_velocity_30d: 0 }) })]),
      "absorption:velocity-zero:syn1-P-001");
    expect(s.category).toBe("absorption");
    expect(s.severity).toBe("warning");
    expect(s.evidence.threshold).toBe("velocity_30d === 0");
    expect(s.evidence.thresholdStatus).toBe("VERIFIED");
  });

  it("2. vận tốc 7 ngày < 30 ngày ⇒ cảnh báo suy giảm, kèm nền và chênh lệch", () => {
    const s = byId(run([entry({ absorption: absorption({ velocity_7d: 0.2, velocity_30d: 0.8, avg_velocity_30d: 0.8 }) })]),
      "absorption:velocity-decreasing:syn1-P-001");
    expect(s.severity).toBe("warning");
    expect(s.evidence.currentValue).toMatch(/0\.2/);
    expect(s.evidence.baselineValue).toMatch(/0\.8/);
    expect(s.evidence.delta).toBeCloseTo(-0.6, 4);
    expect(s.evidence.sourcePath).toMatch(/deriveVelocityDirection/);
    // Độ lớn "đáng kể" chưa có ngưỡng trong repo -> phải nói rõ.
    expect(s.evidence.details.join(" ")).toMatch(/CHƯA CÓ/);
  });

  it("2b. vận tốc 7 ngày > 30 ngày ⇒ KHÔNG phát tín hiệu suy giảm", () => {
    const s = run([entry({ absorption: absorption({ velocity_7d: 0.9, velocity_30d: 0.4, avg_velocity_30d: 0.4 }) })]);
    expect(byId(s, "absorption:velocity-decreasing:syn1-P-001")).toBeUndefined();
  });

  it("3. mức hấp thụ thấp: repo KHÔNG định nghĩa band hấp thụ nên không bịa ra", () => {
    const s = run([entry()]);
    expect(s.filter((x) => x.category === "absorption").every((x) => x.evidence.band === undefined)).toBe(true);
  });

  it("4. 0/0 ĐÁNG NGỜ: data_status='no_data' nhưng có hoạt động miền ⇒ critical", () => {
    const s = byId(
      run([entry({ absorption: absorption({ data_status: "no_data", units_sold: 42, units_remaining: 158 }) })]),
      "absorption:data-status:no_data:syn1-P-001",
    );
    expect(s.severity).toBe("critical");
    expect(s.whatHappened).toMatch(/units_sold = 42/);
    expect(s.confidence).toBe("medium");
  });

  it("5. 0/0 HỢP LỆ: data_status='no_units' ⇒ chỉ thông tin, nêu rõ không phải bán chậm", () => {
    const s = byId(
      run([entry({ absorption: absorption({ data_status: "no_units", units_sold: 0, units_remaining: 0 }) })]),
      "absorption:data-status:no_units:syn1-P-001",
    );
    expect(s.severity).toBe("info");
    expect(s.whyItMatters).toMatch(/không phải bán chậm/);
  });

  it("6. thiếu giá trị vận tốc ⇒ không kết luận hướng, không hiện 0 giả", () => {
    const s = run([entry({ absorption: absorption({ velocity_7d: null, velocity_30d: null, avg_velocity_30d: null }) })]);
    expect(byId(s, "absorption:velocity-decreasing:syn1-P-001")).toBeUndefined();
    expect(byId(s, "absorption:velocity-zero:syn1-P-001")).toBeUndefined();
  });

  it("7. mốc đồng bộ cũ ⇒ tín hiệu freshness, ngưỡng PROVISIONAL", () => {
    const s = byId(
      run([entry({ absorption: absorption({ last_successful_sync: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" }) })]),
      "absorption:freshness:stale:syn1-P-001",
    );
    expect(s.severity).toBe("warning");
    expect(s.evidence.thresholdStatus).toBe("PROVISIONAL");
    expect(s.evidence.details.join(" ")).toMatch(/NGƯỠNG TẠM/);
  });

  it("7b. đồng bộ thất bại ⇒ critical, ngưỡng VERIFIED (trạng thái backend, không phải mốc thời gian)", () => {
    const s = byId(
      run([entry({ absorption: absorption({ last_sync_status: "failed" }) })]),
      "absorption:freshness:sync_failed:syn1-P-001",
    );
    expect(s.severity).toBe("critical");
    expect(s.evidence.thresholdStatus).toBe("VERIFIED");
  });
});

describe("B. Tín hiệu dự báo", () => {
  it("8. dự báo chưa triển khai ⇒ đúng một tín hiệu info, NOT_IMPLEMENTED", () => {
    const s = byId(run([entry()]), "forecasting:not-implemented");
    expect(s.category).toBe("forecasting");
    expect(s.severity).toBe("info");
    expect(s.scope).toBe("portfolio");
    expect(s.evidence.currentValue).toBe("NOT_IMPLEMENTED");
    expect(s.evidence.sourcePath).toMatch(/forecast\.py/);
    expect(s.confidence).toBe("high");
  });

  it("9/10/11. không bịa giá trị, khoảng tin cậy, sai số hay 'bất ổn'", () => {
    const s = byId(run([entry()]), "forecasting:not-implemented");
    expect(s.evidence.baselineValue).toBeUndefined();
    expect(s.evidence.delta).toBeUndefined();
    expect(s.evidence.band).toBeUndefined();
    // "chưa triển khai" KHÁC "bất ổn". Kiểm trên các trường KHẲNG ĐỊNH điều gì
    // đó về dự báo; câu PHỦ ĐỊNH trong `details` là đúng đắn và phải được phép.
    for (const claim of [s.title, s.whatHappened, s.whyItMatters, String(s.evidence.currentValue)]) {
      expect(claim).not.toMatch(/unstable|bất ổn|sai số|độ lệch/i);
    }
    expect(s.evidence.details.join(" ")).toMatch(/KHÔNG phải 'bất ổn'/);
  });

  it("dự báo là tín hiệu DUY NHẤT của nhóm forecasting", () => {
    const f = run([entry(), entry({ project: project({ external_id: "syn1-P-002" }) })])
      .filter((s) => s.category === "forecasting");
    expect(f).toHaveLength(1);
  });
});

describe("C. Tín hiệu xếp hạng", () => {
  it("12. có căn ở mức thấp ⇒ cảnh báo, ngưỡng band 0.33 VERIFIED", () => {
    const s = byId(run([entry()]), "ranking:low-band-units:syn1-P-001");
    expect(s.category).toBe("ranking");
    expect(s.severity).toBe("warning");
    expect(s.evidence.currentValue).toBe(10);
    expect(s.evidence.band).toBe("low");
    expect(s.evidence.threshold).toBe("score < 0.33");
    expect(s.evidence.thresholdStatus).toBe("VERIFIED");
    expect(s.evidence.sourcePath).toMatch(/bands\.py/);
  });

  it("12b. không có căn mức thấp ⇒ không phát tín hiệu", () => {
    const s = run([entry({ ranking: ranking({ band_counts: { high: 50, medium: 50, low: 0 } }) })]);
    expect(byId(s, "ranking:low-band-units:syn1-P-001")).toBeUndefined();
  });

  it("13. căn bị bỏ qua vì coverage dưới ngưỡng cấu hình ⇒ cảnh báo", () => {
    const s = byId(run([entry({ ranking: ranking({ units_skipped: 7, units_ranked: 93 }) })]),
      "ranking:skipped-units:syn1-P-001");
    expect(s.evidence.currentValue).toBe(7);
    // `threshold` nay nói về NGƯỠNG TỶ LỆ PHỦ (chưa có). `min_weight_coverage`
    // là cổng chấm điểm TỪNG CĂN, nên nó chuyển xuống details cùng lời giải
    // thích vì sao hai thứ đó không phải một.
    expect(s.evidence.thresholdStatus).toBe("UNDEFINED");
    expect(s.evidence.details.join(" ")).toMatch(/min_weight_coverage/);
    expect(s.evidence.sourcePath).toMatch(/engine\.py/);
  });

  it("14/15. thiếu mốc nền ⇒ KHÔNG suy ra tín hiệu tụt hạng", () => {
    const s = run([entry()]);
    expect(s.some((x) => /rank-drop|tụt hạng/i.test(x.id + x.title))).toBe(false);
  });

  it("chưa từng xếp hạng (computed_at null) ⇒ cảnh báo riêng, không suy ra mức thấp", () => {
    const s = run([entry({ ranking: ranking({ computed_at: null, band_counts: {}, units_ranked: 0 }) })]);
    expect(byId(s, "ranking:never-computed:syn1-P-001").severity).toBe("warning");
    expect(byId(s, "ranking:low-band-units:syn1-P-001")).toBeUndefined();
  });

  it("16/17. không suy ra xếp hạng CẤP DỰ ÁN từ điểm căn", () => {
    const s = byId(run([entry()]), "ranking:low-band-units:syn1-P-001");
    expect(s.scope).toBe("project");
    // Tiêu đề nói về số CĂN, không nói dự án bị xếp hạng thấp.
    expect(s.title).toMatch(/căn ở mức xếp hạng thấp/);
    expect(s.evidence.details.join(" ")).toMatch(/KHÔNG phải xếp hạng của bản thân dự án/);
    expect(JSON.stringify(s)).not.toMatch(/dự án bị xếp hạng thấp|điểm của dự án/);
  });
});

describe("Hợp đồng chung", () => {
  it("18. nhãn ngưỡng VERIFIED vs PROVISIONAL được phân biệt", () => {
    const s = run([entry({ absorption: absorption({ last_successful_sync: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" }) })]);
    const statuses = new Set(s.map((x) => x.evidence.thresholdStatus).filter(Boolean));
    expect(statuses.has("VERIFIED")).toBe(true);
    expect(statuses.has("PROVISIONAL")).toBe(true);
    for (const x of s) {
      if (x.evidence.thresholdStatus) {
        expect(["VERIFIED", "PROVISIONAL", "UNDEFINED"]).toContain(x.evidence.thresholdStatus);
      }
    }
  });

  it("19. ID tất định và không trùng", () => {
    const a = ids(run([entry()]));
    const b = ids(run([entry()]));
    expect(a).toEqual(b);
    expect(new Set(a).size).toBe(a.length);
  });

  it("20. thứ tự: critical → warning → info", () => {
    const s = run([entry({ absorption: absorption({ data_status: "no_data" }) })]);
    const rank = { critical: 0, warning: 1, info: 2 };
    const seq = s.map((x) => rank[x.severity]);
    expect(seq).toEqual([...seq].sort((x, y) => x - y));
    expect(s[0].severity).toBe("critical");
  });

  it("20b. sortSignals phá hoà bằng id", () => {
    const base = { severity: "info", status: "open", createdAt: "2026-08-18T00:00:00Z" };
    expect(sortSignals([{ ...base, id: "b" }, { ...base, id: "a" }]).map((x) => x.id)).toEqual(["a", "b"]);
  });

  it("21. null khác 0: units_remaining null không thành 0", () => {
    const s = byId(
      run([entry({ absorption: absorption({ data_status: "insufficient_data", units_sold: null, units_remaining: null }) })]),
      "absorption:data-status:insufficient_data:syn1-P-001",
    );
    expect(s.evidence.details.join(" ")).toMatch(/units_remaining: Không có/);
    expect(s.severity).toBe("warning"); // không phải critical: không có hoạt động nào xác nhận
  });

  it("21b. 0 là giá trị thật: units_sold=0 không bị coi là thiếu", () => {
    const s = byId(
      run([entry({ absorption: absorption({ data_status: "no_data", units_sold: 0, units_remaining: 0 }) })]),
      "absorption:data-status:no_data:syn1-P-001",
    );
    // 0/0 thật sự -> KHÔNG đáng ngờ -> không phải critical
    expect(s.severity).toBe("warning");
    expect(s.evidence.details.join(" ")).toMatch(/units_sold: 0/);
  });

  it("22. bản ghi trùng ⇒ chỉ một tín hiệu mỗi id", () => {
    const s = run([entry(), entry()]);
    expect(new Set(ids(s)).size).toBe(ids(s).length);
  });

  it("23. phản hồi méo mó ⇒ bỏ qua nhóm đó, không ném lỗi", () => {
    const s = run([entry({ absorption: "nope", ranking: 42 })]);
    expect(s.some((x) => x.category === "absorption" && x.scope === "project")).toBe(false);
    expect(byId(s, "forecasting:not-implemented")).toBeDefined();
  });

  it("23b. band_counts thiếu ⇒ không sinh tín hiệu mức thấp giả", () => {
    const s = run([entry({ ranking: ranking({ band_counts: undefined }) })]);
    expect(byId(s, "ranking:low-band-units:syn1-P-001")).toBeUndefined();
  });

  it("24. hỏng một phần: hấp thụ lỗi, xếp hạng vẫn cho tín hiệu", () => {
    const s = run([entry({ absorption: null, absorptionError: { status: 500, message: "boom" } })]);
    expect(byId(s, "absorption:unavailable:syn1-P-001").severity).toBe("warning");
    expect(byId(s, "ranking:low-band-units:syn1-P-001")).toBeDefined();
  });

  it("24b. xếp hạng lỗi, hấp thụ vẫn cho tín hiệu", () => {
    const s = run([
      entry({
        ranking: null,
        rankingError: { status: 404, message: "not found" },
        absorption: absorption({ velocity_7d: 0.1, velocity_30d: 0.9, avg_velocity_30d: 0.9 }),
      }),
    ]);
    expect(byId(s, "ranking:unavailable:syn1-P-001")).toBeDefined();
    expect(byId(s, "absorption:velocity-decreasing:syn1-P-001")).toBeDefined();
  });

  it("24c. /projects lỗi ⇒ critical toàn danh mục, nói rõ 'không theo dõi gì'", () => {
    const s = deriveSignals({ entries: [], projectsError: { status: 500, message: "x" } }, opts);
    const p = byId(s, "portfolio:projects-unavailable");
    expect(p.severity).toBe("critical");
    expect(p.whyItMatters).toMatch(/KHÔNG theo dõi/);
  });

  it("trần dự án được nêu tường minh khi có dự án chưa quét", () => {
    const s = byId(run([entry()], { skippedProjectCount: 4 }), "portfolio:project-limit");
    expect(s.severity).toBe("info");
    expect(s.evidence.thresholdStatus).toBe("PROVISIONAL");
    // Trần này là trần XẾP HẠNG (dự án không được quét gì cả), khác với trần
    // hấp thụ ở `portfolio:absorption-limit`.
    expect(s.evidence.threshold).toMatch(/RANKING_PROJECT_LIMIT/);
  });

  it("trần HẤP THỤ được nêu RIÊNG: dự án chỉ quét xếp hạng không bị coi là 'không có vấn đề'", () => {
    const s = byId(run([entry()], { absorptionSkippedCount: 3 }), "portfolio:absorption-limit");
    expect(s.severity).toBe("info");
    expect(s.evidence.thresholdStatus).toBe("PROVISIONAL");
    expect(s.evidence.threshold).toBe(`SIGNAL_PROJECT_LIMIT = ${SIGNAL_PROJECT_LIMIT}`);
    expect(s.whyItMatters).toMatch(/KHÔNG có nghĩa là hấp thụ của chúng ổn/);
  });

  it("mọi tín hiệu trả lời đủ BỐN câu hỏi + metadata hợp đồng", () => {
    const s = run(
      [
        entry({ absorption: absorption({ data_status: "no_data" }), ranking: ranking({ units_skipped: 3 }) }),
        entry({ project: project({ external_id: "syn1-P-002", project_id: "p2" }), absorptionError: { status: 500 }, absorption: null }),
      ],
      { skippedProjectCount: 2 },
    );
    expect(s.length).toBeGreaterThanOrEqual(6);
    for (const x of s) {
      expect(x.id).toBeTruthy();
      expect(["absorption", "forecasting", "ranking"]).toContain(x.category);
      expect(["critical", "warning", "info"]).toContain(x.severity);
      expect(["open", "acknowledged", "investigating", "resolved", "dismissed"]).toContain(x.status);
      expect(["portfolio", "project", "area", "unit"]).toContain(x.scope);
      expect(x.title.length).toBeGreaterThan(0);
      expect(x.whatHappened.length).toBeGreaterThan(0);
      expect(x.whyItMatters.length).toBeGreaterThan(0);
      expect(x.recommendedAction.length).toBeGreaterThan(0);
      expect(x.evidence.source || x.evidence.sourcePath).toBeTruthy();
      expect(["high", "medium", "low"]).toContain(x.confidence);
      expect(x.createdAt).toBeTruthy();
      expect(x.recommendedAction).not.toMatch(/^kiểm tra lại dữ liệu\.?$/i);
      // Tín hiệu cấp dự án PHẢI nêu danh tính dự án.
      if (x.scope === "project") {
        expect(x.evidence.externalId || x.evidence.projectId).toBeTruthy();
      }
    }
  });

  it("chỉ đọc: cờ persistence là false", () => {
    expect(SIGNAL_PERSISTENCE_SUPPORTED).toBe(false);
  });

  it("isMissing: chỉ null/undefined", () => {
    expect(isMissing(null)).toBe(true);
    expect(isMissing(undefined)).toBe(true);
    expect(isMissing(0)).toBe(false);
    expect(isMissing("")).toBe(false);
  });
});

describe("summarizeSignals / filterSignals", () => {
  const set = [
    { id: "a", category: "absorption", severity: "critical", status: "open" },
    { id: "b", category: "ranking", severity: "warning", status: "open" },
    { id: "c", category: "forecasting", severity: "info", status: "resolved" },
  ];

  it("đếm theo mức độ và theo nhóm", () => {
    const s = summarizeSignals(set);
    expect(s).toMatchObject({ critical: 1, warning: 1, info: 1, open: 2, total: 3 });
    expect(s.byCategory).toEqual({ absorption: 1, ranking: 1, forecasting: 1 });
  });

  it("lọc theo nhóm / mức độ / trạng thái", () => {
    expect(filterSignals(set, { category: "ranking" }).map((x) => x.id)).toEqual(["b"]);
    expect(filterSignals(set, { severity: "critical" }).map((x) => x.id)).toEqual(["a"]);
    expect(filterSignals(set, { status: "resolved" }).map((x) => x.id)).toEqual(["c"]);
    expect(filterSignals(set, {})).toHaveLength(3);
  });
});

// ===========================================================================
// Đợt nâng cấp: phủ xếp hạng, tầm nhìn bán hết, tỷ lệ phủ, chấm điểm chú ý,
// chính sách tầng, và gộp cấp danh mục.
// ===========================================================================

import {
  CONFIDENCE_LABEL,
  LAYER_LABEL,
  SCORING,
  UNAVAILABLE_RULES,
  aggregatePortfolio,
  computeAttentionScore,
  flattenSignals,
  normalizeImpact,
  priorityTier,
} from "./signals";

/** Sinh N bản ghi dự án khác danh tính, dùng cho các bài về trần và về gộp. */
const entries = (n, make = () => ({})) =>
  Array.from({ length: n }, (_, i) =>
    entry({
      project: project({ external_id: `syn1-P-${String(i + 1).padStart(3, "0")}`, project_id: `p-${i + 1}`, name: `Dự án ${i + 1}` }),
      ...make(i),
    }),
  );

describe("P0.1 — tín hiệu xếp hạng KHÔNG bị trần hấp thụ bóp nghẹt", () => {
  it("dự án ngoài trần hấp thụ vẫn phát low-band và skipped-units", () => {
    // Mô phỏng đúng cái trang làm: xếp hạng đã nạp cho MỌI dự án, hấp thụ chỉ
    // nạp cho phần đầu. Dự án thứ hai nằm NGOÀI trần hấp thụ.
    const s = run([
      entry(),
      entry({
        project: project({ external_id: "syn1-P-009", project_id: "p-9", name: "Xa Trần" }),
        absorptionRequested: false,
        absorption: null,
        ranking: ranking({ external_project_id: "syn1-P-009", band_counts: { high: 1, medium: 1, low: 5 }, units_ranked: 20, units_skipped: 4 }),
      }),
    ]);
    const flat = flattenSignals(s);
    expect(flat.some((x) => x.id === "ranking:low-band-units:syn1-P-009")).toBe(true);
    expect(flat.some((x) => x.id === "ranking:skipped-units:syn1-P-009")).toBe(true);
  });

  it("dự án ngoài trần hấp thụ KHÔNG sinh tín hiệu hấp thụ giả", () => {
    const s = flattenSignals(
      run([
        entry({
          project: project({ external_id: "syn1-P-009", project_id: "p-9" }),
          absorptionRequested: false,
          absorption: null,
        }),
      ]),
    );
    expect(s.some((x) => x.category === "absorption" && x.scope === "project")).toBe(false);
  });

  it("cờ absorptionRequested mặc định là BẬT ⇒ không âm thầm tắt nhóm A", () => {
    const s = flattenSignals(run([entry({ absorption: absorption({ velocity_7d: 0.1, velocity_30d: 0.9 }) })]));
    expect(s.some((x) => x.id === "absorption:velocity-decreasing:syn1-P-001")).toBe(true);
  });
});

describe("P0.2 — tầm nhìn bán hết (tồn kho)", () => {
  const withWeeks = (v) => run([entry({ absorption: absorption({ estimated_weeks_to_sell_out: v }) })]);
  const sellout = (v) => byId(flattenSignals(withWeeks(v)), "absorption:sellout-horizon:syn1-P-001");

  it("giá trị hữu hạn dương ⇒ phát tín hiệu info, ngưỡng UNDEFINED", () => {
    const s = sellout(31.6);
    expect(s.severity).toBe("info");
    expect(s.layer).toBe(2);
    expect(s.evidence.thresholdStatus).toBe("UNDEFINED");
    expect(s.evidence.threshold).toMatch(/CHƯA CÓ ngưỡng/);
  });

  it("nêu rõ phạm vi, tồn kho, vận tốc và tầm nhìn tính ra", () => {
    const s = sellout(31.6);
    expect(s.whatHappened).toMatch(/units_remaining = 158/);
    expect(s.whatHappened).toMatch(/velocity_30d = 0\.5/);
    expect(s.whatHappened).toMatch(/31\.6/);
    expect(s.evidence.details.join(" ")).toMatch(/Phạm vi: toàn dự án/);
  });

  it("KHÔNG tự xếp loại tốt/xấu và nói rõ chưa có ngưỡng", () => {
    const s = sellout(31.6);
    const claims = [s.title, s.whatHappened, s.whyItMatters, s.recommendedAction].join(" ");
    expect(claims).not.toMatch(/quá cao|quá chậm|nguy hiểm|báo động|rủi ro cao/i);
    expect(s.evidence.details.join(" ")).toMatch(/CHƯA CÓ NGƯỠNG/);
  });

  it("null (vận tốc bằng 0 hoặc không có) ⇒ KHÔNG phát", () => {
    expect(sellout(null)).toBeUndefined();
    expect(sellout(undefined)).toBeUndefined();
  });

  it("0 (đã bán hết) ⇒ KHÔNG phát: không có tầm nhìn nào để nói", () => {
    expect(sellout(0)).toBeUndefined();
  });

  it("giá trị không hữu hạn ⇒ KHÔNG phát, không hiện Infinity/NaN", () => {
    expect(sellout(Infinity)).toBeUndefined();
    expect(sellout("không phải số")).toBeUndefined();
  });

  it("data_status khác ready ⇒ KHÔNG phát: không dựng tầm nhìn trên số chưa sẵn sàng", () => {
    const s = flattenSignals(
      run([entry({ absorption: absorption({ data_status: "insufficient_data", estimated_weeks_to_sell_out: 12 }) })]),
    );
    expect(byId(s, "absorption:sellout-horizon:syn1-P-001")).toBeUndefined();
  });

  it("KHÔNG tự xưng là dự báo", () => {
    const s = sellout(31.6);
    expect(s.evidence.details.join(" ")).toMatch(/Không phải dự báo/);
    expect(s.category).toBe("absorption");
  });
});

describe("P0.3 — tỷ lệ phủ xếp hạng", () => {
  const cov = (ranked, skipped) =>
    byId(flattenSignals(run([entry({ ranking: ranking({ units_ranked: ranked, units_skipped: skipped }) })])), "ranking:skipped-units:syn1-P-001");

  it("giữ CẢ số đếm lẫn tỷ lệ", () => {
    const s = cov(93, 7);
    expect(s.evidence.currentValue).toBe(7);
    expect(s.affectedUnits).toBe(7);
    expect(s.whatHappened).toMatch(/93\/100 = 93\.0%/);
    expect(s.evidence.delta).toMatch(/93\.0%/);
  });

  it("tỷ lệ vào cả tiêu đề để đọc lướt cũng thấy", () => {
    expect(cov(93, 7).title).toMatch(/phủ 93\.0%/);
  });

  it("không có ngưỡng phủ ⇒ UNDEFINED, và nói rõ min_weight_coverage là chuyện KHÁC", () => {
    const s = cov(93, 7);
    expect(s.evidence.thresholdStatus).toBe("UNDEFINED");
    const d = s.evidence.details.join(" ");
    expect(d).toMatch(/cổng chấm điểm TỪNG CĂN/);
    expect(d).toMatch(/không phải ngưỡng tỷ lệ phủ/);
  });

  it("biên: ranked = 0 ⇒ tỷ lệ 0%, không chia cho 0", () => {
    const s = cov(0, 5);
    expect(s.whatHappened).toMatch(/0\/5 = 0\.0%/);
    expect(Number.isFinite(s.attentionScore)).toBe(true);
  });

  it("biên: skipped = 0 ⇒ KHÔNG phát tín hiệu", () => {
    expect(cov(100, 0)).toBeUndefined();
  });
});

describe("P0.4 — chỉ nói GỘP, không bao giờ nói RÒNG", () => {
  const all = () =>
    flattenSignals(
      run([
        entry({ absorption: absorption({ velocity_7d: 0.2, velocity_30d: 0.8, estimated_weeks_to_sell_out: 20 }) }),
        entry({ project: project({ external_id: "syn1-P-002", project_id: "p-2" }), absorption: absorption({ velocity_30d: 0, velocity_7d: 0 }) }),
      ]),
    );

  it("không tín hiệu nào TUYÊN BỐ một con số ròng", () => {
    for (const s of all()) {
      // Bốn câu trả lời cho người đọc: tuyệt đối không được nói "ròng"/"net".
      const claims = [s.title, s.whatHappened, s.whyItMatters, s.recommendedAction].join(" ");
      expect(claims).not.toMatch(/\bròng\b/i);
      expect(claims).not.toMatch(/\bnet absorption\b|\bnet velocity\b/i);
      // Chỗ DUY NHẤT được phép nhắc tới "ròng" là câu cảnh báo nói rằng nó
      // KHÔNG tồn tại — không được có lần xuất hiện nào ngoài câu đó.
      for (const d of s.evidence.details || []) {
        if (/ròng/i.test(d)) expect(d).toMatch(/chưa có bất kỳ số hấp thụ RÒNG nào/i);
      }
    }
  });

  it("tín hiệu vận tốc và tồn kho đều mang cảnh báo GỘP kèm đường dẫn mã", () => {
    const grossRules = ["absorption:velocity-decreasing", "absorption:velocity-zero", "absorption:sellout-horizon"];
    const seen = all().filter((s) => grossRules.includes(s.ruleId));
    expect(seen.length).toBeGreaterThan(0);
    for (const s of seen) {
      const d = s.evidence.details.join(" ");
      expect(d).toMatch(/Số GỘP/);
      expect(d).toMatch(/domain_absorption\.py/);
      expect(d).toMatch(/chưa có bất kỳ số hấp thụ RÒNG nào/i);
    }
  });

  it("tiêu đề vận tốc giảm nói rõ GỘP", () => {
    expect(byId(all(), "absorption:velocity-decreasing:syn1-P-001").title).toMatch(/GỘP/);
  });

  it("tỷ lệ huỷ nằm ở danh sách luật CHƯA dựng được, không bịa ra tín hiệu", () => {
    expect(all().some((s) => /huỷ|cancel/i.test(s.title))).toBe(false);
    expect(UNAVAILABLE_RULES.some((r) => /RÒNG và tỷ lệ huỷ/.test(r.rule))).toBe(true);
  });
});

describe("P0.5 — trung thực về ngưỡng và nhãn", () => {
  it("lệch tính lại 5 phút là PROVISIONAL, ngang hàng với 24 giờ", () => {
    const s = byId(
      flattenSignals(
        run([
          entry({
            absorption: absorption({
              last_successful_sync: "2026-08-17T23:00:00Z",
              updated_at: "2026-08-17T20:00:00Z",
            }),
          }),
        ]),
      ),
      "absorption:freshness:calculation_outdated:syn1-P-001",
    );
    expect(s.evidence.thresholdStatus).toBe("PROVISIONAL");
    expect(s.evidence.threshold).toMatch(/5 phút/);
    expect(s.evidence.details.join(" ")).toMatch(/NGƯỠNG TẠM/);
  });

  it("nhãn độ tin cậy được Việt hoá", () => {
    expect(CONFIDENCE_LABEL).toEqual({ high: "Cao", medium: "Trung bình", low: "Thấp" });
  });

  it("đường dẫn mã không còn số dòng cứng dễ trôi", () => {
    for (const s of flattenSignals(run([entry()]))) {
      if (s.evidence.sourcePath) expect(s.evidence.sourcePath).not.toMatch(/\.py:\d+/);
    }
  });
});

describe("P0.6 — chấm điểm chú ý và chính sách tầng", () => {
  it("công thức tất định, khớp đúng các hằng số đã công bố", () => {
    const expected =
      SCORING.BASE_SEVERITY.critical + SCORING.LAYER_BOOST[2] + SCORING.CONFIDENCE_BOOST.high + SCORING.IMPACT_MAX;
    expect(computeAttentionScore({ severity: "critical", layer: 2, confidence: "high", impact: 1 })).toBe(expected);
    expect(expected).toBe(100);
  });

  it("điểm bị chặn ở 0..100 kể cả khi tác động vượt biên", () => {
    expect(computeAttentionScore({ severity: "critical", layer: 2, confidence: "high", impact: 99 })).toBe(100);
    expect(computeAttentionScore({ severity: "info", layer: null, confidence: "low", impact: -5 })).toBe(10);
    expect(normalizeImpact(2)).toBe(1);
    expect(normalizeImpact(-1)).toBe(0);
    expect(normalizeImpact("x")).toBe(0);
  });

  it("cùng đầu vào ⇒ cùng điểm (không có yếu tố ngẫu nhiên nào)", () => {
    const a = run([entry()]);
    const b = run([entry()]);
    expect(flattenSignals(a).map((s) => s.attentionScore)).toEqual(flattenSignals(b).map((s) => s.attentionScore));
  });

  it("mọi tín hiệu đều có layer hợp lệ và điểm trong [0,100]", () => {
    for (const s of flattenSignals(run([entry({ ranking: ranking({ units_skipped: 5 }) })], { skippedProjectCount: 2 }))) {
      expect([1, 2, 3, null]).toContain(s.layer);
      expect(s.attentionScore).toBeGreaterThanOrEqual(0);
      expect(s.attentionScore).toBeLessThanOrEqual(100);
      expect(s.evidence.attentionFormula).toMatch(/PROVISIONAL/);
    }
  });

  it("tầng được gán ĐÚNG theo bản chất từng luật", () => {
    const s = flattenSignals(
      run([
        entry({
          absorption: absorption({ velocity_7d: 0.2, velocity_30d: 0.8, estimated_weeks_to_sell_out: 20, last_sync_status: "failed" }),
          ranking: ranking({ units_skipped: 4, band_counts: { high: 1, medium: 1, low: 3 } }),
        }),
      ]),
    );
    const layerOf = (id) => byId(s, id).layer;
    // Tầng 1 — tin cậy dữ liệu
    expect(layerOf("absorption:freshness:sync_failed:syn1-P-001")).toBe(1);
    expect(layerOf("ranking:skipped-units:syn1-P-001")).toBe(1);
    // Tầng 2 — rủi ro thương mại
    expect(layerOf("absorption:velocity-decreasing:syn1-P-001")).toBe(2);
    expect(layerOf("absorption:sellout-horizon:syn1-P-001")).toBe(2);
    expect(layerOf("ranking:low-band-units:syn1-P-001")).toBe(2);
    // Vận hành nội bộ
    expect(layerOf("forecasting:not-implemented")).toBe(null);
  });

  it("KHÔNG tuyên bố năng lực Tầng 3", () => {
    const s = flattenSignals(run([entry()], { skippedProjectCount: 3 }));
    expect(s.some((x) => x.layer === 3)).toBe(false);
  });

  it("chính sách: Tầng 1 NGHIÊM TRỌNG trên Tầng 2; Tầng 1 còn lại thì DƯỚI", () => {
    expect(priorityTier({ layer: 1, severity: "critical" })).toBe(0);
    expect(priorityTier({ layer: 2, severity: "info" })).toBe(1);
    expect(priorityTier({ layer: 1, severity: "warning" })).toBe(2);
    expect(priorityTier({ layer: null, severity: "info" })).toBe(3);
  });

  it("thực tế: hấp thụ tự mâu thuẫn (T1 critical) đứng TRÊN rủi ro thương mại", () => {
    const s = run([
      entry({
        absorption: absorption({ data_status: "no_data", units_sold: 42, velocity_7d: 0.1, velocity_30d: 0.9 }),
        ranking: ranking({ band_counts: { high: 0, medium: 0, low: 90 }, units_ranked: 90 }),
      }),
    ]);
    const order = ids(s);
    expect(order.indexOf("absorption:data-status:no_data:syn1-P-001")).toBeLessThan(
      order.indexOf("ranking:low-band-units:syn1-P-001"),
    );
  });

  it("thực tế: cảnh báo T1 KHÔNG nghiêm trọng đứng DƯỚI rủi ro thương mại T2", () => {
    const s = run([
      entry({
        absorption: absorption({ last_successful_sync: "2026-08-01T00:00:00Z" }), // stale ⇒ T1 warning
        ranking: ranking({ band_counts: { high: 0, medium: 0, low: 90 }, units_ranked: 90 }), // T2 warning
      }),
    ]);
    const order = ids(s);
    expect(order.indexOf("ranking:low-band-units:syn1-P-001")).toBeLessThan(
      order.indexOf("absorption:freshness:stale:syn1-P-001"),
    );
  });

  it("ĐỘ LỚN quyết định thứ tự: nhiều căn mức thấp xếp trên ít căn mức thấp", () => {
    const s = run([
      entry({
        project: project({ external_id: "aaa-nho", project_id: "p-a" }),
        absorptionRequested: false,
        absorption: null,
        ranking: ranking({ band_counts: { high: 90, medium: 8, low: 2 }, units_ranked: 100 }),
      }),
      entry({
        project: project({ external_id: "zzz-lon", project_id: "p-z" }),
        absorptionRequested: false,
        absorption: null,
        ranking: ranking({ band_counts: { high: 5, medium: 5, low: 90 }, units_ranked: 100 }),
      }),
    ]);
    // Không gộp (chỉ 2 dự án nhưng cùng luật ⇒ CÓ gộp) — nên xét trong danh sách con.
    const parent = byId(s, "portfolio:ranking:low-band-units");
    const order = parent.children.map((c) => c.id);
    // `zzz-lon` đứng trước dù id xếp sau theo bảng chữ cái: độ lớn thắng chữ cái.
    expect(order[0]).toBe("ranking:low-band-units:zzz-lon");
    expect(order[1]).toBe("ranking:low-band-units:aaa-nho");
  });

  it("id chỉ phá hoà khi mọi dữ kiện có nghĩa đã bằng nhau", () => {
    const same = { severity: "warning", layer: 2, confidence: "high", attentionScore: 50, affectedUnits: 3, status: "open" };
    const sorted = sortSignals([{ ...same, id: "b" }, { ...same, id: "a" }]);
    expect(sorted.map((x) => x.id)).toEqual(["a", "b"]);
  });
});

describe("P0.7 — gộp cấp danh mục", () => {
  const threeLowBand = () =>
    run(
      entries(3, () => ({
        absorptionRequested: false,
        absorption: null,
        ranking: ranking({ band_counts: { high: 1, medium: 1, low: 4 }, units_ranked: 6 }),
      })),
    );

  it("2+ dự án cùng luật ⇒ một tín hiệu cha cấp danh mục", () => {
    const parent = byId(threeLowBand(), "portfolio:ranking:low-band-units");
    expect(parent.scope).toBe("portfolio");
    expect(parent.affectedProjectCount).toBe(3);
    expect(parent.affectedProjects).toEqual(["syn1-P-001", "syn1-P-002", "syn1-P-003"]);
    expect(parent.childIds).toHaveLength(3);
  });

  it("cha GIỮ nguyên bằng chứng của từng con, không nén mất", () => {
    const parent = byId(threeLowBand(), "portfolio:ranking:low-band-units");
    expect(parent.children).toHaveLength(3);
    for (const c of parent.children) {
      expect(c.scope).toBe("project");
      expect(c.evidence.externalId).toMatch(/^syn1-P-00\d$/);
      expect(c.whatHappened.length).toBeGreaterThan(0);
      expect(c.recommendedAction.length).toBeGreaterThan(0);
    }
  });

  it("cha KHÔNG xuất hiện thêm ở cấp cao nhất (không đếm trùng)", () => {
    const top = ids(threeLowBand());
    expect(top).toContain("portfolio:ranking:low-band-units");
    expect(top).not.toContain("ranking:low-band-units:syn1-P-001");
    // nhưng bản trải phẳng vẫn thấy đủ
    expect(ids(flattenSignals(threeLowBand()))).toContain("ranking:low-band-units:syn1-P-001");
  });

  it("MỘT dự án ⇒ KHÔNG gộp: không tạo lớp bọc rỗng nghĩa", () => {
    const s = run([entry({ absorptionRequested: false, absorption: null })]);
    expect(ids(s)).toContain("ranking:low-band-units:syn1-P-001");
    expect(ids(s).some((id) => id.startsWith("portfolio:ranking"))).toBe(false);
  });

  it("cha lấy mức NẶNG nhất và độ tin cậy THẤP nhất của nhóm", () => {
    const s = run([
      entry({ absorption: absorption({ data_status: "no_data", units_sold: 5 }) }), // critical, confidence medium
      entry({ project: project({ external_id: "syn1-P-002", project_id: "p-2" }), absorption: absorption({ data_status: "no_data", units_sold: 0, units_remaining: 0 }) }),
    ]);
    const parent = byId(s, "portfolio:absorption:data-status:no_data");
    expect(parent.severity).toBe("critical");
    expect(parent.confidence).toBe("medium");
  });

  it("chỉ cộng tổng số căn khi MỌI con đều biết số căn", () => {
    const parent = byId(threeLowBand(), "portfolio:ranking:low-band-units");
    expect(parent.affectedUnits).toBe(12); // 4 + 4 + 4

    const mixed = aggregatePortfolio(
      [
        { id: "x1", ruleId: "r", title: 'Luật r tại "A"', status: "open", scope: "project", category: "ranking", layer: 2, severity: "warning", confidence: "high", attentionScore: 50, affectedUnits: 4, evidence: { externalId: "A" } },
        { id: "x2", ruleId: "r", title: 'Luật r tại "B"', status: "open", scope: "project", category: "ranking", layer: 2, severity: "warning", confidence: "high", attentionScore: 50, affectedUnits: null, evidence: { externalId: "B" } },
      ],
      { nowIso: NOW.toISOString() },
    );
    const p = mixed.find((x) => x.id === "portfolio:r");
    expect(p.affectedUnits).toBe(null);
    expect(p.whatHappened).toMatch(/Không cộng được tổng số căn/);
  });

  it("KHÔNG bịa trung bình vận tốc toàn danh mục", () => {
    const s = run(
      entries(3, () => ({ absorption: absorption({ velocity_7d: 0.2, velocity_30d: 0.8 }) })),
    );
    const parent = byId(s, "portfolio:absorption:velocity-decreasing");
    const text = [parent.whatHappened, parent.evidence.baselineValue, ...(parent.evidence.details || [])].join(" ");
    expect(text).toMatch(/KHÔNG có trung bình vận tốc/);
    expect(parent.evidence.currentValue).toBe("3 dự án bị ảnh hưởng");
  });

  it("tiêu đề cha không mang tên một dự án cụ thể", () => {
    const parent = byId(threeLowBand(), "portfolio:ranking:low-band-units");
    expect(parent.title).toMatch(/^3 dự án cùng gặp:/);
    expect(parent.title).not.toMatch(/Dự án 1|Dự án 2|Dự án 3/);
  });

  it("điểm của cha = điểm con cao nhất + thưởng lan rộng, chặn 100", () => {
    const parent = byId(threeLowBand(), "portfolio:ranking:low-band-units");
    const maxChild = Math.max(...parent.children.map((c) => c.attentionScore));
    expect(parent.attentionScore).toBe(Math.min(100, maxChild + SCORING.AGGREGATE_SPREAD_BONUS * 2));
    expect(parent.evidence.thresholdStatus).toBe("PROVISIONAL");
  });

  it("thông báo trần và tín hiệu 'không đọc được' KHÔNG bị gộp mất", () => {
    const s = run(
      entries(2, () => ({ absorptionError: { status: 500, message: "boom" }, absorption: null })),
      { skippedProjectCount: 4, absorptionSkippedCount: 2 },
    );
    expect(ids(s)).toContain("portfolio:project-limit");
    expect(ids(s)).toContain("portfolio:absorption-limit");
    // Lỗi cấp dự án thì VẪN gộp được, nhưng bằng chứng từng dự án còn nguyên.
    const parent = byId(s, "portfolio:absorption:unavailable");
    expect(parent.children.map((c) => c.evidence.details[0])).toEqual(["boom", "boom"]);
  });

  it("đếm và lọc chạy trên bản TRẢI PHẲNG nên gộp không làm tụt con số", () => {
    const s = threeLowBand();
    const summary = summarizeSignals(s);
    // 3 con + 1 cha + 1 dự báo = 5
    expect(summary.total).toBe(5);
    expect(summary.byCategory.ranking).toBe(4);
    expect(summary.layer2).toBe(4);
  });

  it("lọc giữ được con khớp ngay cả khi cha không khớp", () => {
    const parentOnly = [
      {
        id: "p", ruleId: "p", scope: "portfolio", category: "ranking", layer: 2, severity: "warning", status: "open", confidence: "high", attentionScore: 40, affectedUnits: null, evidence: {},
        children: [
          { id: "c1", category: "ranking", severity: "critical", status: "open" },
          { id: "c2", category: "ranking", severity: "info", status: "open" },
        ],
      },
    ];
    const kept = filterSignals(parentOnly, { severity: "critical" });
    expect(kept).toHaveLength(1);
    expect(kept[0].children.map((c) => c.id)).toEqual(["c1"]);
  });

  it("không có tín hiệu nào ⇒ danh sách rỗng ngoài dự báo, không ném lỗi", () => {
    const s = run([]);
    expect(ids(s)).toEqual(["forecasting:not-implemented"]);
  });

  it("hỏng một phần: một dự án lỗi, dự án còn lại vẫn cho tín hiệu đầy đủ", () => {
    const s = flattenSignals(
      run([
        entry({ absorptionError: { status: 503 }, absorption: null, rankingError: { status: 500 }, ranking: null }),
        entry({ project: project({ external_id: "syn1-P-002", project_id: "p-2" }), ranking: ranking({ external_project_id: "syn1-P-002" }) }),
      ]),
    );
    expect(s.some((x) => x.id === "absorption:unavailable:syn1-P-001")).toBe(true);
    expect(s.some((x) => x.id === "ranking:low-band-units:syn1-P-002")).toBe(true);
  });

  it("không có id trùng ở bất kỳ cấp nào", () => {
    const all = ids(flattenSignals(threeLowBand()));
    expect(new Set(all).size).toBe(all.length);
  });

  it("nhãn tầng có sẵn cho giao diện", () => {
    expect(LAYER_LABEL[1]).toMatch(/Tin cậy dữ liệu/);
    expect(LAYER_LABEL[2]).toMatch(/Rủi ro thương mại/);
  });
});
