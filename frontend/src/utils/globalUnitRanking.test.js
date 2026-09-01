import { describe, expect, it } from "vitest";
import {
  GLOBAL_PAGE_SIZE,
  RANK_DIRECTION,
  SORT_DIRECTIONS,
  UNKNOWN_AREA_LABEL,
  UNKNOWN_PROJECT_LABEL,
  buildGlobalRanking,
  normalizeUnit,
  pageOf,
  sortGlobalRankingRows,
  toScore,
} from "./globalUnitRanking";

/** Backend gửi `score` dạng CHUỖI Decimal — fixture giữ đúng kiểu đó. */
const unit = (over = {}) => ({
  unit_id: over.unit_id || `u-${over.unit_code || "A"}`,
  unit_code: "A-01",
  unit_type: "2PN",
  unit_status: "available",
  area_id: "aaaa-1111",
  area_name: "Sapphire 1",
  score: "0.5000",
  score_percent: 50,
  band: "medium",
  rank_in_project: 1,
  rank_in_area: 1,
  weight_coverage: "1.0000",
  contributions: [{ feature_key: "unit_available", source: "resolved" }],
  ...over,
});

const project = (over = {}) => ({
  project_id: "11111111-1111-5111-8111-111111111111",
  name: "Harbor Crest",
  external_id: "syn1-P-001",
  ...over,
});

const ranking = (items, over = {}) => ({
  project_id: "11111111-1111-5111-8111-111111111111",
  external_project_id: "syn1-P-001",
  computed_at: "2026-08-17T21:00:00Z",
  config_version: 2,
  units_ranked: items.length,
  units_skipped: 0,
  band_counts: { high: 0, medium: items.length, low: 0 },
  items,
  total: items.length,
  limit: 200,
  offset: 0,
  ...over,
});

const codes = (rows) => rows.map((r) => r.unitName);

const sortableRows = [
  { unitId: "u-low", unitName: "Low", score: "0.11", projectName: "P1", areaName: "A1" },
  { unitId: "u-zero", unitName: "Zero", score: 0, projectName: "P2", areaName: "A2" },
  { unitId: "u-high", unitName: "High", score: "0.9", projectName: "P3", areaName: "A3" },
  { unitId: "u-missing", unitName: "Missing", score: null, projectName: "P4", areaName: "A4" },
];

describe("globalUnitRanking — chiều sắp xếp", () => {
  it("đặt điểm cao nhất trước theo mặc định và không đổi mảng gốc", () => {
    const input = [...sortableRows];
    const result = sortGlobalRankingRows(input);
    expect(result.map((row) => row.unitId)).toEqual(["u-high", "u-low", "u-zero", "u-missing"]);
    expect(input).toEqual(sortableRows);
  });

  it("đặt điểm thấp nhất trước khi chọn ascending", () => {
    expect(sortGlobalRankingRows(sortableRows, SORT_DIRECTIONS.ASC).map((row) => row.unitId)).toEqual([
      "u-zero", "u-low", "u-high", "u-missing",
    ]);
  });

  it("so sánh chuỗi điểm theo số, giữ 0 hợp lệ, và để null/chuỗi rỗng/rác ở cuối", () => {
    const result = sortGlobalRankingRows([
      { unitId: "u-11", unitName: "11", score: "0.11" },
      { unitId: "u-9", unitName: "9", score: "0.9" },
      { unitId: "u-0", unitName: "0", score: "0" },
      { unitId: "u-null", unitName: "null", score: null },
      { unitId: "u-empty", unitName: "empty", score: "" },
      { unitId: "u-bad", unitName: "bad", score: "not-a-number" },
    ]);
    expect(result.map((row) => row.unitId)).toEqual(["u-9", "u-11", "u-0", "u-bad", "u-empty", "u-null"]);
  });

  it("phá hoà tất định và giữ nguyên project/area context", () => {
    const rows = [
      { unitId: "u-2", unitName: "A-01", score: 0.5, projectName: "P2", areaName: "A2" },
      { unitId: "u-1", unitName: "A-01", score: 0.5, projectName: "P1", areaName: "A1" },
    ];
    const result = sortGlobalRankingRows(rows);
    expect(result.map((row) => row.unitId)).toEqual(["u-1", "u-2"]);
    expect(result[0]).toMatchObject({ projectName: "P1", areaName: "A1" });
  });
});

describe("globalUnitRanking — chuẩn hoá và trộn", () => {
  it("1. sắp theo điểm SỐ giảm dần, xuyên dự án", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", score: "0.4000" }),
          unit({ unit_id: "u2", unit_code: "A-02", score: "0.9000" }),
        ]),
      },
      {
        project: project({ project_id: "p2", name: "Willow Park", external_id: "syn1-P-002" }),
        ranking: ranking([unit({ unit_id: "u3", unit_code: "B-01", score: "0.7000" })], {
          external_project_id: "syn1-P-002",
        }),
      },
    ]);
    expect(codes(rows)).toEqual(["A-02", "B-01", "A-01"]);
    expect(rows.every((r) => r.rankDirection === RANK_DIRECTION)).toBe(true);
  });

  it("2. căn điểm CAO NHẤT đứng đầu", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", score: "0.1000" }),
          unit({ unit_id: "u2", unit_code: "A-02", score: "0.9900" }),
        ]),
      },
    ]);
    expect(rows[0].unitName).toBe("A-02");
    expect(rows[0].rank).toBe(1);
  });

  it("3. căn điểm THẤP NHẤT đứng cuối", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", score: "0.9000" }),
          unit({ unit_id: "u2", unit_code: "A-02", score: "0.0100" }),
        ]),
      },
    ]);
    expect(rows[rows.length - 1].unitName).toBe("A-02");
  });

  it("4. so sánh theo SỐ, không theo chuỗi: 0.9 xếp trên 0.11 dù '0.11' < '0.9' về mặt chuỗi", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", score: "0.1100" }),
          unit({ unit_id: "u2", unit_code: "A-02", score: "0.9000" }),
          unit({ unit_id: "u3", unit_code: "A-03", score: "0.2000" }),
        ]),
      },
    ]);
    expect(codes(rows)).toEqual(["A-02", "A-03", "A-01"]);
    expect(rows.map((r) => r.score)).toEqual([0.9, 0.2, 0.11]);
  });

  it("5. phá hoà TẤT ĐỊNH: cùng điểm thì theo mã căn rồi unit id, không phụ thuộc thứ tự đầu vào", () => {
    const items = [
      unit({ unit_id: "u-b", unit_code: "B-01", score: "0.5000" }),
      unit({ unit_id: "u-a", unit_code: "A-01", score: "0.5000" }),
      unit({ unit_id: "u-c", unit_code: "A-01", score: "0.5000" }),
    ];
    const first = buildGlobalRanking([{ project: project(), ranking: ranking(items) }]).rows;
    const second = buildGlobalRanking([
      { project: project(), ranking: ranking([...items].reverse()) },
    ]).rows;
    expect(first.map((r) => r.unitId)).toEqual(["u-a", "u-c", "u-b"]);
    expect(second.map((r) => r.unitId)).toEqual(first.map((r) => r.unitId));
  });

  it("6. thiếu điểm KHÔNG bị quy về 0: giữ null, xếp sau mọi căn có điểm, không có hạng", () => {
    const { rows, meta } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", score: null, band: null }),
          unit({ unit_id: "u2", unit_code: "A-02", score: "0.0000" }),
        ]),
      },
    ]);
    expect(codes(rows)).toEqual(["A-02", "A-01"]);
    expect(rows[0].score).toBe(0); // điểm 0 THẬT vẫn là điểm, vẫn có hạng
    expect(rows[0].rank).toBe(1);
    expect(rows[1].score).toBeNull();
    expect(rows[1].rank).toBeNull();
    expect(rows[1].confidence).toBe("unknown");
    expect(meta.unscoredCount).toBe(1);
  });

  it("7. căn trùng: giữ bản có computed_at MỚI hơn, chỉ còn một dòng", () => {
    const { rows, meta } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([unit({ unit_id: "u1", unit_code: "A-01", score: "0.4000" })], {
          computed_at: "2026-08-10T00:00:00Z",
        }),
      },
      {
        project: project(),
        ranking: ranking([unit({ unit_id: "u1", unit_code: "A-01", score: "0.8000" })], {
          computed_at: "2026-08-17T00:00:00Z",
        }),
      },
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].score).toBe(0.8);
    expect(rows[0].updatedAt).toBe("2026-08-17T00:00:00Z");
    expect(meta.duplicatesResolved).toBe(1);
  });

  it("8. ngữ cảnh dự án và phân khu được giữ cho MỌI căn", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", area_name: "Sapphire 1" }),
          unit({ unit_id: "u2", unit_code: "A-02", area_name: "Sapphire 2", area_id: "aaaa-2222" }),
        ]),
      },
    ]);
    for (const row of rows) {
      expect(row.projectName).toBe("Harbor Crest");
      expect(row.projectExternalId).toBe("syn1-P-001");
      expect(row.projectId).toBe("11111111-1111-5111-8111-111111111111");
      expect(row.areaName).toMatch(/^Sapphire /);
      expect(row.areaId).toBeTruthy();
    }
  });

  it("9. thiếu tên dự án được nêu TƯỜNG MINH, không gán sang dự án khác", () => {
    const { rows, meta } = buildGlobalRanking([
      { project: project({ name: null }), ranking: ranking([unit({ unit_id: "u1" })]) },
      {
        project: project({ project_id: "p2", name: "Willow Park", external_id: "syn1-P-002" }),
        ranking: ranking([unit({ unit_id: "u2", unit_code: "B-01" })]),
      },
    ]);
    const orphan = rows.find((r) => r.unitId === "u1");
    expect(orphan.projectName).toBe(UNKNOWN_PROJECT_LABEL);
    expect(orphan.projectContextAvailable).toBe(false);
    expect(orphan.projectExternalId).toBe("syn1-P-001"); // danh tính thật vẫn giữ
    expect(rows.find((r) => r.unitId === "u2").projectName).toBe("Willow Park");
    expect(meta.missingProjectContext).toBe(1);
  });

  it("10. thiếu tên phân khu được nêu TƯỜNG MINH", () => {
    const { rows, meta } = buildGlobalRanking([
      { project: project(), ranking: ranking([unit({ unit_id: "u1", area_name: null })]) },
    ]);
    expect(rows[0].areaName).toBe(UNKNOWN_AREA_LABEL);
    expect(rows[0].areaContextAvailable).toBe(false);
    expect(meta.missingAreaContext).toBe(1);
  });

  it("11. tên dự án và tên phân khu KHÔNG bị hoán chỗ", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project({ name: "Harbor Crest" }),
        ranking: ranking([unit({ unit_id: "u1", area_name: "Sapphire 1" })]),
      },
    ]);
    expect(rows[0].projectName).toBe("Harbor Crest");
    expect(rows[0].areaName).toBe("Sapphire 1");
    expect(rows[0].projectName).not.toBe(rows[0].areaName);
  });

  it("12. chỉ có dòng CĂN — không có dòng tổng hợp của dự án hay phân khu", () => {
    const { rows, meta } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01" }),
          unit({ unit_id: "u2", unit_code: "A-02" }),
        ]),
      },
    ]);
    expect(rows).toHaveLength(2);
    expect(meta.totalUnits).toBe(2);
    for (const row of rows) {
      expect(row.unitId).toBeTruthy();
      expect(row.unitName).toBeTruthy();
    }
    // band_counts/units_ranked của phản hồi KHÔNG được biến thành dòng nào.
    expect(rows.some((r) => r.unitName === "2" || r.unitId === "band_counts")).toBe(false);
  });

  it("13. hạng toàn cục liên tục 1..N, và hạng trong dự án được giữ riêng", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([unit({ unit_id: "u1", unit_code: "A-01", score: "0.4000", rank_in_project: 1 })]),
      },
      {
        project: project({ project_id: "p2", name: "Willow Park", external_id: "syn1-P-002" }),
        ranking: ranking([unit({ unit_id: "u2", unit_code: "B-01", score: "0.9000", rank_in_project: 1 })]),
      },
    ]);
    expect(rows.map((r) => r.rank)).toEqual([1, 2]);
    expect(rows.map((r) => r.rankInProject)).toEqual([1, 1]);
    expect(rows[0].unitName).toBe("B-01");
  });

  it("14. hai lần chạy khác nhau không bị trộn im lặng: mốc và phiên bản config được nêu", () => {
    const { rows, meta } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([unit({ unit_id: "u1" })], { computed_at: "2026-08-01T00:00:00Z", config_version: 1 }),
      },
      {
        project: project({ project_id: "p2", name: "Willow Park", external_id: "syn1-P-002" }),
        ranking: ranking([unit({ unit_id: "u2", unit_code: "B-01" })], {
          computed_at: "2026-08-17T00:00:00Z",
          config_version: 3,
        }),
      },
    ]);
    expect(meta.mixedConfigVersions).toBe(true);
    expect(meta.configVersions).toEqual([1, 3]);
    expect(meta.latestComputedAt).toBe("2026-08-17T00:00:00Z");
    expect(rows.find((r) => r.unitId === "u1").updatedAt).toBe("2026-08-01T00:00:00Z");
    expect(rows.find((r) => r.unitId === "u1").configVersion).toBe(1);
    expect(rows.find((r) => r.unitId === "u2").configVersion).toBe(3);
  });

  it("14b. một phiên bản config duy nhất thì KHÔNG báo trộn", () => {
    const { meta } = buildGlobalRanking([
      { project: project(), ranking: ranking([unit({ unit_id: "u1" })], { config_version: 2 }) },
      {
        project: project({ project_id: "p2", external_id: "syn1-P-002" }),
        ranking: ranking([unit({ unit_id: "u2" })], { config_version: 2 }),
      },
    ]);
    expect(meta.mixedConfigVersions).toBe(false);
  });

  it("15a. dữ liệu méo mó không làm sập và không sinh dòng giả", () => {
    const { rows, meta } = buildGlobalRanking([
      { project: project(), ranking: "not-an-object" },
      { project: project({ external_id: "syn1-P-002" }), ranking: { items: "nope" } },
      { project: project({ external_id: "syn1-P-003" }), ranking: ranking([null, 42, {}, unit({ unit_id: "ok" })]) },
      null,
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].unitId).toBe("ok");
    expect(meta.malformedProjects).toHaveLength(2);
  });

  it("15b. điểm không phải số (NaN/chuỗi rác) coi như KHÔNG có điểm", () => {
    expect(toScore("abc")).toBeNull();
    expect(toScore(Number.NaN)).toBeNull();
    expect(toScore(undefined)).toBeNull();
    expect(toScore("")).toBeNull();
    expect(toScore("0")).toBe(0);
    expect(toScore("0.6600")).toBe(0.66);
  });

  it("15c. bản ghi không có danh tính căn bị loại, không tạo dòng rỗng", () => {
    expect(normalizeUnit({ unit_id: null, unit_code: null, score: "0.9" })).toBeNull();
    expect(normalizeUnit(null)).toBeNull();
    expect(normalizeUnit("x")).toBeNull();
  });

  it("16. lỗi của một dự án không xoá sổ các dự án còn lại", () => {
    const { rows, meta } = buildGlobalRanking([
      { project: project(), rankingError: { status: 404, message: "not found" } },
      {
        project: project({ project_id: "p2", name: "Willow Park", external_id: "syn1-P-002" }),
        ranking: ranking([unit({ unit_id: "u2", unit_code: "B-01" })]),
      },
    ]);
    expect(rows).toHaveLength(1);
    expect(meta.failedProjects).toHaveLength(1);
    expect(meta.failedProjects[0].label).toBe("Harbor Crest");
    expect(meta.projectsIncluded).toBe(1);
  });

  it("17. dự án chưa từng xếp hạng được nêu riêng, không lẫn với 'không có căn nào'", () => {
    const { meta } = buildGlobalRanking([
      { project: project(), ranking: ranking([], { computed_at: null, config_version: null }) },
    ]);
    expect(meta.neverRankedProjects).toHaveLength(1);
    expect(meta.totalUnits).toBe(0);
  });

  it("18. dự án bị cắt bớt vì trần mỗi dự án được nêu kèm số thật", () => {
    const { meta } = buildGlobalRanking([
      { project: project(), ranking: ranking([unit({ unit_id: "u1" })], { total: 640 }) },
    ]);
    expect(meta.truncatedProjects).toEqual([{ label: "Harbor Crest", shown: 1, total: 640 }]);
  });

  it("18a. dự án đã áp dụng AHP (v3): ưu tiên effective_score/effective_score_percent, gắn rankingFormula", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking(
          [unit({ unit_id: "u1", score: "0.2000", score_percent: 20, effective_score: "0.9500", effective_score_percent: 95 })],
          { ranking_formula: "v3_hierarchical" },
        ),
      },
    ]);
    expect(rows[0].score).toBe(0.95);
    expect(rows[0].scorePercent).toBe(95);
    expect(rows[0].rankingFormula).toBe("v3_hierarchical");
  });

  it("18b. dự án v2 thuần (không có ranking_formula): score/scorePercent giữ nguyên legacy, rankingFormula là v2_legacy", () => {
    const { rows } = buildGlobalRanking([
      { project: project(), ranking: ranking([unit({ unit_id: "u1", score: "0.2000", score_percent: 20 })]) },
    ]);
    expect(rows[0].score).toBe(0.2);
    expect(rows[0].scorePercent).toBe(20);
    expect(rows[0].rankingFormula).toBe("v2_legacy");
  });

  it("18c. v3 nhưng backend không gửi kèm effective_score cho căn này: rơi về score v2, không coi là 0", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking(
          [unit({ unit_id: "u1", score: "0.2000", score_percent: 20 })],
          { ranking_formula: "v3_hierarchical" },
        ),
      },
    ]);
    expect(rows[0].score).toBe(0.2);
    expect(rows[0].scorePercent).toBe(20);
  });

  it("19. số dự án chưa nạp được truyền nguyên vẹn ra meta", () => {
    const { meta } = buildGlobalRanking([], { projectsNotScanned: 7 });
    expect(meta.projectsNotScanned).toBe(7);
    expect(buildGlobalRanking([]).meta.projectsNotScanned).toBe(0);
  });

  it("20. độ tin cậy phản ánh đặc trưng thiếu, không phải một ngưỡng tự đặt", () => {
    const { rows } = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", contributions: [{ feature_key: "x", source: "resolved" }] }),
          unit({ unit_id: "u2", unit_code: "A-02", contributions: [{ feature_key: "x", source: "missing" }] }),
          unit({ unit_id: "u3", unit_code: "A-03", contributions: undefined }),
        ]),
      },
    ]);
    const byId = Object.fromEntries(rows.map((r) => [r.unitId, r]));
    expect(byId.u1.confidence).toBe("high");
    expect(byId.u1.missingFeaturesCount).toBe(0);
    expect(byId.u2.confidence).toBe("medium");
    expect(byId.u2.missingFeaturesCount).toBe(1);
    expect(byId.u3.confidence).toBe("medium");
    expect(byId.u3.missingFeaturesCount).toBeNull();
  });
});

describe("globalUnitRanking — phân trang", () => {
  const many = Array.from({ length: 120 }, (_, i) => ({
    unitId: `u-${i}`,
    unitName: `U-${String(i).padStart(3, "0")}`,
    score: 1 - i / 1000,
  }));

  it("21. cắt trang SAU khi đã sắp xếp: trang 2 nối tiếp trang 1 theo thứ tự toàn cục", () => {
    const first = pageOf(many, 0);
    const second = pageOf(many, 1);
    expect(first.items).toHaveLength(GLOBAL_PAGE_SIZE);
    expect(first.items[0].unitId).toBe("u-0");
    expect(second.items[0].unitId).toBe(`u-${GLOBAL_PAGE_SIZE}`);
    expect(second.pages).toBe(3);
    expect(second.start).toBe(GLOBAL_PAGE_SIZE);
  });

  it("22. trang ngoài biên được kẹp về trang hợp lệ, không trả trang rỗng", () => {
    expect(pageOf(many, 99).page).toBe(2);
    expect(pageOf(many, -3).page).toBe(0);
    expect(pageOf([], 0)).toEqual({ items: [], page: 0, pages: 1, start: 0 });
  });
});
