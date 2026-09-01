import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Chặn ở tầng TRANSPORT (api/client) chứ không mock endpoints.js: nhờ vậy các
// khẳng định về ĐƯỜNG DẪN, PHƯƠNG THỨC và số lượng request là thật, và hợp đồng
// trong endpoints.js vẫn được thực thi thay vì bị thay bằng bản giả.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

const { api } = await import("../api/client");
const overviewModule = await import("./OverviewPage");
const OverviewPage = overviewModule.default;
const { deriveCircularAttention } = overviewModule;
const { SIGNAL_PROJECT_LIMIT } = await import("../utils/signals");
const { RANKING_PROJECT_LIMIT, UNITS_PER_PROJECT_LIMIT } = await import("../utils/globalUnitRanking");

const PROJECTS = [
  {
    project_id: "11111111-1111-5111-8111-111111111111",
    name: "Harbor Crest",
    launch_date: "2025-08-03",
    status: "active",
    headline: "",
    introduce: "",
    cover_image_url: null,
    external_id: "syn1-P-001",
    source_revision: 3,
  },
];

const ABSORPTION = {
  units_sold: 42,
  units_remaining: 158,
  velocity_7d: 0.2,
  velocity_30d: 0.8,
  avg_velocity_30d: 0.8,
  velocity_unit: "units_per_day",
  data_status: "ready",
  calculator: "domain_units_deals",
  updated_at: "2026-08-17T20:00:00Z",
  last_successful_sync: "2026-08-17T20:00:00Z",
  last_attempted_sync: "2026-08-17T20:00:00Z",
  last_sync_status: "completed",
};

const PORTFOLIO = {
  project_count: 1,
  area_count: 4,
  unit_count: 120,
  deal_count: 37,
  booking_count: 6,
  selling_project_count: 1,
  data_status: "ready",
};

const unit = (over = {}) => ({
  unit_id: "u1",
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

const RANKING = {
  external_project_id: "syn1-P-001",
  computed_at: "2026-08-17T21:00:00Z",
  config_version: 2,
  units_ranked: 100,
  units_skipped: 0,
  band_counts: { high: 20, medium: 70, low: 10 },
  items: [
    unit({ unit_id: "u1", unit_code: "A-01", score: "0.2000", score_percent: 20, band: "low" }),
    unit({ unit_id: "u2", unit_code: "A-02", score: "0.9000", score_percent: 90, band: "high" }),
  ],
  total: 2,
  limit: 200,
  offset: 0,
};

/** Định tuyến theo đường dẫn để mỗi test chỉ khai điều nó quan tâm. */
function routeGet(overrides = {}) {
  api.get.mockImplementation((path) => {
    for (const [fragment, value] of Object.entries(overrides)) {
      if (path.includes(fragment)) {
        return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
      }
    }
    if (path.includes("/portfolio/summary")) return Promise.resolve(PORTFOLIO);
    if (path.includes("/absorption/summary")) return Promise.resolve(ABSORPTION);
    if (path.includes("/ranking?")) return Promise.resolve(RANKING);
    if (path.includes("/projects")) return Promise.resolve(PROJECTS);
    return Promise.resolve([]);
  });
}

const renderPage = () => render(<MemoryRouter><OverviewPage /></MemoryRouter>);
const paths = () => api.get.mock.calls.map(([p]) => p);

// Fixture mang mốc thời gian 2026-08-17/18. Với ĐỒNG HỒ THẬT, các mốc đó trôi
// quá STALE_AFTER_MS (24 giờ) và sinh thêm một tín hiệu freshness, làm các phép
// đếm trong file này hỏng theo NGÀY CHẠY chứ không theo mã. Ghim đồng hồ để bài
// test đo đúng thứ nó định đo.
const FIXED_NOW = new Date("2026-08-18T00:00:00Z");

beforeEach(() => {
  api.get.mockReset();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(FIXED_NOW);
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

describe("OverviewPage — hợp đồng API của Signals Center", () => {
  it("1/2. gọi đúng endpoint danh mục + hấp thụ/xếp hạng theo dự án, toàn bộ bằng GET", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("signals-list");

    const all = paths();
    expect(all.some((p) => p.endsWith("/v1/projects"))).toBe(true);
    expect(all.some((p) => p.includes("/v1/absorption/summary?project_id="))).toBe(true);
    expect(all.some((p) => p.includes("/v1/ranking?external_project_id="))).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("3/4. tham số đúng phạm vi: absorption dùng project_id nội bộ, ranking dùng external_id", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("signals-list");

    const abs = paths().find((p) => p.includes("/absorption/summary"));
    const rank = paths().find((p) => p.includes("/ranking?"));
    expect(abs).toContain(`project_id=${PROJECTS[0].project_id}`);
    expect(rank).toContain(`external_project_id=${PROJECTS[0].external_id}`);
    // Không rò danh tính dự án vào tuyến danh mục toàn cục.
    expect(paths().find((p) => p.endsWith("/v1/projects"))).not.toMatch(/project_id|external_project_id/);
  });

  it("5. fan-out CÓ TRẦN theo DỰ ÁN (không phải theo căn), không tăng theo số dự án", async () => {
    const count = RANKING_PROJECT_LIMIT + 6;
    const many = Array.from({ length: count }, (_, i) => ({
      ...PROJECTS[0],
      project_id: `p-${i}`,
      external_id: `syn1-P-${String(i).padStart(3, "0")}`,
      name: `Dự án ${i}`,
    }));
    routeGet({ "/v1/projects": many });
    renderPage();
    await screen.findByTestId("signals-list");

    // Report details are now opened on demand instead of rendered beside the
    // chart; inspect the same real portfolio signal through its category.
    fireEvent.click(screen.getByTestId("category-legend-watch"));

    // 1 lượt danh mục + 1 lượt portfolio + 1 lượt xếp hạng/dự án (tới trần) + 1 lượt hấp thụ/dự án
    // (tới trần tín hiệu). KHÔNG có lượt gọi nào theo từng CĂN.
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledTimes(2 + RANKING_PROJECT_LIMIT + SIGNAL_PROJECT_LIMIT),
    );
    // HAI trần được nêu RIÊNG, không im lặng bỏ qua và không trộn làm một:
    //   · ngoài trần xếp hạng ⇒ không có tín hiệu nào cả
    //   · trong trần xếp hạng nhưng ngoài trần hấp thụ ⇒ có nhóm C, thiếu nhóm A
    expect(screen.getByText(new RegExp(`${count - RANKING_PROJECT_LIMIT} dự án chưa được quét tín hiệu`))).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(`${RANKING_PROJECT_LIMIT - SIGNAL_PROJECT_LIMIT} dự án chỉ được quét xếp hạng`)),
    ).toBeInTheDocument();
    expect(screen.getByTestId("global-ranking-notes"))
      .toHaveTextContent(new RegExp(`${count - RANKING_PROJECT_LIMIT} dự án chưa được nạp`));
  });

  it("6. không có va chạm cache toàn cục/dự án: mỗi dự án gọi bằng danh tính riêng", async () => {
    const two = [
      PROJECTS[0],
      { ...PROJECTS[0], project_id: "22222222-2222-5222-8222-222222222222", external_id: "syn1-P-002", name: "Willow Park" },
    ];
    routeGet({ "/v1/projects": two });
    renderPage();
    await screen.findByTestId("signals-list");

    const absPaths = paths().filter((p) => p.includes("/absorption/summary"));
    expect(new Set(absPaths).size).toBe(2);
    expect(absPaths.some((p) => p.includes(two[1].project_id))).toBe(true);
  });

  it("7a. 5xx trên hấp thụ ⇒ tín hiệu riêng và xếp hạng vẫn còn", async () => {
    routeGet({ "/absorption/summary": Object.assign(new Error("boom"), { status: 500 }) });
    renderPage();

    await screen.findByTestId("signals-list");
    fireEvent.click(screen.getByTestId("category-legend-watch"));
    expect(screen.getAllByText(/Dữ liệu hấp thụ chưa sẵn sàng/).length).toBeGreaterThan(0);
    expect(screen.getByText(/10 căn ở mức xếp hạng thấp/)).toBeInTheDocument();
    expect(screen.queryByText(/Dự báo/)).not.toBeInTheDocument();
  });

  it("7b. 404 trên xếp hạng ⇒ tín hiệu riêng, hấp thụ vẫn còn", async () => {
    routeGet({ "/ranking?": Object.assign(new Error("not found"), { status: 404 }) });
    renderPage();

    await screen.findByTestId("signals-list");
    fireEvent.click(screen.getByTestId("category-legend-missing_score"));
    expect(screen.getByRole("heading", { name: "Báo cáo: Chưa có điểm AHP" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("category-legend-watch"));
    expect(screen.getByRole("heading", { name: "Báo cáo: Cần theo dõi" })).toBeInTheDocument();
  });

  it("7c. 4xx trên /projects ⇒ critical toàn danh mục", async () => {
    routeGet({ "/v1/projects": Object.assign(new Error("Forbidden"), { status: 403 }) });
    renderPage();

    expect(await screen.findByText(/Không thể tải tín hiệu cần chú ý/)).toBeInTheDocument();
    expect(screen.queryByTestId("attention-donut")).not.toBeInTheDocument();
  });

  it("7d. phản hồi méo mó ⇒ không sinh tín hiệu giả, không ném lỗi", async () => {
    routeGet({ "/absorption/summary": "not-an-object", "/ranking?": 42 });
    renderPage();
    await screen.findByTestId("signals-list");

    expect(screen.queryByText(/Vận tốc bán 7 ngày/)).not.toBeInTheDocument();
    expect(screen.queryByText(/căn ở mức xếp hạng thấp/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("category-legend-watch"));
    expect(screen.queryByText(/Dự báo/)).not.toBeInTheDocument();
  });

  it("7e. mọi nguồn timeout ⇒ vẫn nêu đúng hiện trạng, không sập", async () => {
    api.get.mockRejectedValue(Object.assign(new TypeError("Network request failed"), { status: undefined }));
    renderPage();
    expect(await screen.findByText(/Không thể tải tín hiệu cần chú ý/)).toBeInTheDocument();
  });

  it("8. phần còn lại của Overview không đổi; ô 'Sức khỏe danh mục' cũ đã thành xếp hạng căn", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("signals-list");

    expect(screen.getByRole("heading", { name: "Tổng quan danh mục" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Xếp hạng căn toàn hệ thống" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sức khỏe danh mục" })).not.toBeInTheDocument();
    // Dự án vẫn hiện — nhưng là NGỮ CẢNH của từng căn, không phải một danh sách dự án.
    expect(screen.getAllByText("Harbor Crest").length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Xu hướng hấp thụ toàn danh mục" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tín hiệu cần chú ý" })).toBeInTheDocument();
    // KPI danh mục giữ nguyên và mở rộng thành đúng sáu thẻ theo thứ tự.
    expect(screen.getByText("Tổng dự án")).toBeInTheDocument();
    expect(screen.getByText("Tổng phân khu")).toBeInTheDocument();
    expect(screen.getByText("Tổng unit")).toBeInTheDocument();
    expect(screen.getByText("Tổng deals")).toBeInTheDocument();
    expect(screen.getByText("Đang booking")).toBeInTheDocument();
    expect(screen.getByText("Đang bán")).toBeInTheDocument();
  });

  it("9. đọc đủ sáu KPI từ đúng một aggregate endpoint, không đếm từ ranking", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("signals-list");

    expect(paths().filter((p) => p.includes("/v1/portfolio/summary"))).toHaveLength(1);
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("37")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
  });
});

describe("OverviewPage — xếp hạng căn toàn cục (tích hợp)", () => {
  const rankRows = () => screen.getAllByTestId("global-ranking-row");

  it("G1. gọi đúng endpoint xếp hạng, bằng GET, ở mức CĂN và KHÔNG kèm bộ lọc nào", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("global-ranking-table");

    const rank = paths().filter((p) => p.includes("/v1/ranking?"));
    expect(rank).toHaveLength(1);
    expect(rank[0]).toContain(`external_project_id=${PROJECTS[0].external_id}`);
    // Không lọc phân khu, không lọc mức, không lọc trạng thái căn — đây là bảng
    // TOÀN CỤC; mọi bộ lọc lén sẽ làm nó không còn là toàn cục nữa.
    expect(rank[0]).not.toMatch(/external_area_id|area_id=|band=|unit_status/);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("G2. xin đúng trần mỗi dự án của backend, từ offset 0", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("global-ranking-table");
    expect(paths().find((p) => p.includes("/v1/ranking?"))).toContain(`limit=${UNITS_PER_PROJECT_LIMIT}`);
    expect(UNITS_PER_PROJECT_LIMIT).toBe(200); // = MAX_UNITS_PER_PAGE của src/api/ranking.py
  });

  it("G3. KHÔNG có request nào theo từng căn: số request không đổi khi số căn tăng", async () => {
    const manyUnits = {
      ...RANKING,
      items: Array.from({ length: 60 }, (_, i) =>
        unit({ unit_id: `u-${i}`, unit_code: `U-${i}`, score: (1 - i / 100).toFixed(4), score_percent: (1 - i / 100) * 100 }),
      ),
      total: 60,
    };
    routeGet({ "/ranking?": manyUnits });
    renderPage();
    await screen.findByTestId("global-ranking-table");

    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(4)); // projects + portfolio + ranking + absorption
    expect(paths().some((p) => /\/units?\//.test(p))).toBe(false);
  });

  it("G4. trộn hai dự án thành MỘT danh sách, sắp theo điểm giảm dần, không chia nhóm", async () => {
    const two = [
      PROJECTS[0],
      { ...PROJECTS[0], project_id: "22222222-2222-5222-8222-222222222222", external_id: "syn1-P-002", name: "Willow Park" },
    ];
    api.get.mockImplementation((path) => {
      if (path.includes("/absorption/summary")) return Promise.resolve(ABSORPTION);
      if (path.includes("external_project_id=syn1-P-002")) {
        return Promise.resolve({
          ...RANKING,
          external_project_id: "syn1-P-002",
          items: [unit({ unit_id: "u3", unit_code: "B-01", score: "0.5000", score_percent: 50, area_name: "Emerald 2" })],
          total: 1,
        });
      }
      if (path.includes("/ranking?")) return Promise.resolve(RANKING);
      if (path.includes("/projects")) return Promise.resolve(two);
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByTestId("global-ranking-table");

    await waitFor(() => expect(rankRows()).toHaveLength(3));
    expect(rankRows().map((r) => r.getAttribute("data-score"))).toEqual(["0.9", "0.5", "0.2"]);
    expect(rankRows().map((r) => r.getAttribute("data-project"))).toEqual([
      "syn1-P-001",
      "syn1-P-002",
      "syn1-P-001",
    ]);
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });

  it("G5. mỗi căn hiện đủ ngữ cảnh dự án và phân khu", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("global-ranking-table");

    for (const row of rankRows()) {
      expect(within(row).getByText("Harbor Crest")).toBeInTheDocument();
      expect(within(row).getByText("Sapphire 1")).toBeInTheDocument();
      expect(within(row).getByText("syn1-P-001")).toBeInTheDocument();
    }
  });

  it("G6. chuẩn hoá đúng: điểm chuỗi Decimal thành số, hạng toàn cục 1..N", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("global-ranking-table");

    expect(rankRows().map((r) => r.getAttribute("data-rank"))).toEqual(["1", "2"]);
    expect(within(rankRows()[0]).getByText("90.0%")).toBeInTheDocument();
  });

  it("G7. 5xx trên xếp hạng ⇒ nêu rõ dự án lỗi, trang không sập", async () => {
    routeGet({ "/ranking?": Object.assign(new Error("boom"), { status: 500 }) });
    renderPage();
    expect(await screen.findByText("Chưa có căn nào được xếp hạng")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Xếp hạng căn toàn hệ thống" })).toBeInTheDocument();
  });

  it("G8. 4xx trên /projects ⇒ bảng vào trạng thái lỗi có nút thử lại", async () => {
    routeGet({ "/v1/projects": Object.assign(new Error("Forbidden"), { status: 403 }) });
    renderPage();
    await screen.findByText(/Không thể tải tín hiệu cần chú ý/);
    expect(screen.queryByTestId("global-ranking-table")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Thử lại" }).length).toBeGreaterThan(0);
  });

  it("G9. phản hồi méo mó ⇒ không sinh dòng giả", async () => {
    routeGet({ "/ranking?": { items: "nope" } });
    renderPage();
    expect(await screen.findByText("Chưa có căn nào được xếp hạng")).toBeInTheDocument();
    expect(screen.queryAllByTestId("global-ranking-row")).toHaveLength(0);
  });

  it("G10. timeout mọi nguồn ⇒ không sập, không hiện bảng rỗng như thể đã xong", async () => {
    api.get.mockRejectedValue(Object.assign(new TypeError("Network request failed"), { status: undefined }));
    renderPage();
    await screen.findByText(/Không thể tải tín hiệu cần chú ý/);
    expect(screen.queryByTestId("global-ranking-table")).not.toBeInTheDocument();
  });

  it("G11. trạng thái tải hiện trước khi dữ liệu về, không chớp bảng thiếu", async () => {
    api.get.mockImplementation(() => new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId("global-ranking-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("global-ranking-table")).not.toBeInTheDocument();
  });

  it("G12. không va chạm cache với đường theo dự án: mỗi dự án một danh tính riêng", async () => {
    const two = [
      PROJECTS[0],
      { ...PROJECTS[0], project_id: "22222222-2222-5222-8222-222222222222", external_id: "syn1-P-002", name: "Willow Park" },
    ];
    routeGet({ "/v1/projects": two });
    renderPage();
    await screen.findByTestId("global-ranking-table");

    const rank = paths().filter((p) => p.includes("/v1/ranking?"));
    expect(new Set(rank).size).toBe(2);
    expect(rank.some((p) => p.includes("syn1-P-002"))).toBe(true);
  });
});

describe("OverviewPage — hiển thị ba nhóm tín hiệu", () => {
  it("hiện trạng thái tải trước khi dữ liệu về", async () => {
    api.get.mockImplementation(() => new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId("signals-loading")).toBeInTheDocument();
  });

  it("hiện đủ tín hiệu hấp thụ và xếp hạng trong popup", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("signals-list");

    expect(screen.getByTestId("signals-list")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("category-legend-watch"));
    expect(screen.getByRole("heading", { name: "Báo cáo: Cần theo dõi" })).toBeInTheDocument();
    expect(screen.getAllByText(/Vận tốc bán|mức xếp hạng thấp/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Dự báo/)).not.toBeInTheDocument();
  });

  it("tín hiệu cấp dự án nêu danh tính và thông tin hữu ích, không render liên kết chi tiết", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("signals-list");
    fireEvent.click(screen.getByTestId("category-legend-watch"));
    const row = await screen.findByTestId("signal-ranking:low-band-units:syn1-P-001");
    expect(within(row).getByText(/Harbor Crest/)).toBeInTheDocument();
    expect(within(row).getByText(/Số căn ảnh hưởng/)).toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  });

  it("chỉ đọc: không có nút đổi trạng thái tín hiệu", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("signals-list");
    expect(screen.getByTestId("signals-readonly-notice")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /ghi nhận|đã xử lý/i })).not.toBeInTheDocument();
  });
});

describe("OverviewPage — circular attention chart", () => {
  it("does not render the report panel until a category is activated", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("attention-donut");
    expect(screen.queryByTestId("attention-report-dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("BÁO CÁO TÍN HIỆU")).not.toBeInTheDocument();
  });

  it("groups real signals by primary category without inventing scores", () => {
    const result = deriveCircularAttention([
      { id: "missing", ruleId: "ranking:never-computed", affectedProjectCount: 1 },
      { id: "partial", ruleId: "ranking:skipped-units", affectedProjectCount: 2 },
      { id: "inventory", ruleId: "absorption:sellout-horizon", affectedProjectCount: 1 },
    ]);
    expect(result.total).toBe(4);
    expect(result.categories.map((category) => [category.key, category.count])).toEqual([
      ["missing_score", 1], ["partial_score", 2], ["inventory_risk", 1],
    ]);
  });

  it("renders the donut center count and selects a category from its legend", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("attention-donut");
    expect(screen.getByTestId("attention-donut").textContent).toContain("4");
    const followUp = screen.getByTestId("category-legend-watch");
    fireEvent.click(followUp);
    expect(screen.getByRole("heading", { name: "Báo cáo: Cần theo dõi" })).toBeInTheDocument();
    expect(followUp).toHaveAttribute("aria-pressed", "true");
  });

  it("supports keyboard selection on an accessible donut slice", async () => {
    routeGet({ "/ranking?": Object.assign(new Error("not found"), { status: 404 }) });
    renderPage();
    await screen.findByTestId("attention-donut");
    const missingSlice = screen.getByTestId("category-slice-missing_score");
    fireEvent.keyDown(missingSlice, { key: "Enter" });
    expect(screen.getByRole("heading", { name: "Báo cáo: Chưa có điểm AHP" })).toBeInTheDocument();
    expect(missingSlice).toHaveAttribute("aria-pressed", "true");
  });

  it("opens category-specific reports and updates the open dialog when switching", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("attention-donut");

    fireEvent.click(screen.getByTestId("category-legend-inventory_risk"));
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-labelledby", "attention-report-title");
    expect(screen.getByRole("dialog")).toHaveFocus();
    expect(screen.getByRole("heading", { name: "Báo cáo: Nguy cơ tồn kho" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Giải thích theo AHP" })).not.toBeInTheDocument();
    expect(screen.queryByText("Điểm AHP")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Đề xuất bước tiếp theo" })).not.toBeInTheDocument();
    const affectedItems = screen.getByRole("heading", { name: "Mục bị ảnh hưởng" }).closest("section");
    expect(within(affectedItems).queryByRole("link")).not.toBeInTheDocument();
    expect(within(affectedItems).queryByText("Xem báo cáo chi tiết →")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("category-legend-watch"));
    expect(screen.getByRole("heading", { name: "Báo cáo: Cần theo dõi" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Báo cáo: Nguy cơ tồn kho" })).not.toBeInTheDocument();
  });

  it("closes with the close button, Escape, and the backdrop", async () => {
    routeGet();
    renderPage();
    await screen.findByTestId("attention-donut");
    const trigger = screen.getByTestId("category-legend-watch");

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Đóng báo cáo tín hiệu" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(trigger);
    fireEvent.click(screen.getByTestId("attention-report-backdrop"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

// ===========================================================================
// Hồi quy cho P0.1: trần HẤP THỤ không được phép nuốt tín hiệu XẾP HẠNG.
// ===========================================================================
describe("OverviewPage — phủ tín hiệu xếp hạng ngoài trần hấp thụ", () => {
  /** Nhiều dự án hơn trần hấp thụ nhưng vẫn trong trần xếp hạng. */
  const manyProjects = (n) =>
    Array.from({ length: n }, (_, i) => ({
      ...PROJECTS[0],
      project_id: `p-${i}`,
      external_id: `syn1-P-${String(i).padStart(3, "0")}`,
      name: `Dự án ${i}`,
    }));

  it("dự án NGOÀI trần hấp thụ vẫn phát low-band và skipped-units", async () => {
    const n = SIGNAL_PROJECT_LIMIT + 3;
    expect(n).toBeLessThanOrEqual(RANKING_PROJECT_LIMIT);
    const beyond = `syn1-P-${String(n - 1).padStart(3, "0")}`;

    routeGet({
      "/v1/projects": manyProjects(n),
      // Dự án cuối (ngoài trần hấp thụ) có cả căn mức thấp lẫn căn bị bỏ qua.
      [`/ranking?external_project_id=${beyond}`]: {
        ...RANKING,
        external_project_id: beyond,
        units_ranked: 80,
        units_skipped: 20,
        band_counts: { high: 5, medium: 15, low: 60 },
      },
    });
    renderPage();
    await screen.findByTestId("signals-list");

    // `skipped-units` chỉ dự án này có ⇒ không gộp, nằm thẳng ở cấp cao nhất.
    // Trước đợt sửa, dự án này bị cắt TRƯỚC khi suy nên không có tín hiệu nào.
    fireEvent.click(screen.getByTestId("category-legend-partial_score"));
    expect(screen.getByText(/20 căn không chấm được điểm/)).toBeInTheDocument();
    expect(screen.getByText(/Phủ điểm AHP: 80\/100 căn/)).toBeInTheDocument();

    // `low-band` thì mọi dự án đều có ⇒ đã gộp; bằng chứng riêng nằm trong con.
    // Có nhiều hàng gộp (vận tốc, mức thấp, …) nên phải chỉ đích danh hàng cần mở.
    fireEvent.click(screen.getByTestId("category-legend-watch"));
    const parent = screen.getByTestId("signal-portfolio:ranking:low-band-units");
    const kids = screen.getByTestId("signal-children-portfolio:ranking:low-band-units");
    expect(within(kids).getByTestId(`signal-ranking:low-band-units:${beyond}`)).toBeInTheDocument();
  });

  it("không gọi thêm request nào cho phần phủ mở rộng", async () => {
    const n = SIGNAL_PROJECT_LIMIT + 3;
    routeGet({ "/v1/projects": manyProjects(n) });
    renderPage();
    await screen.findByTestId("signals-list");

    // Vẫn đúng 1 danh mục + 1 portfolio + n xếp hạng + trần hấp thụ lượt hấp thụ.
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2 + n + SIGNAL_PROJECT_LIMIT));
    expect(paths().filter((p) => p.includes("/absorption/summary"))).toHaveLength(SIGNAL_PROJECT_LIMIT);
  });

  it("dự án ngoài trần hấp thụ KHÔNG bị gán tín hiệu hấp thụ giả", async () => {
    const n = SIGNAL_PROJECT_LIMIT + 3;
    const beyond = `syn1-P-${String(n - 1).padStart(3, "0")}`;
    routeGet({ "/v1/projects": manyProjects(n) });
    renderPage();
    await screen.findByTestId("signals-list");

    expect(screen.queryByTestId(`signal-absorption:velocity-decreasing:${beyond}`)).not.toBeInTheDocument();
    // …và sự vắng mặt đó được NÓI RA, không để im lặng bị đọc thành "không sao".
    fireEvent.click(screen.getByTestId("category-legend-watch"));
    expect(screen.getByText(/3 dự án chỉ được quét xếp hạng, chưa quét hấp thụ/)).toBeInTheDocument();
  });

  it("nhiều dự án cùng một luật ⇒ hiện hàng gộp cấp danh mục", async () => {
    routeGet({ "/v1/projects": manyProjects(3) });
    renderPage();
    await screen.findByTestId("signals-list");

    fireEvent.click(screen.getByTestId("category-legend-watch"));
    expect(screen.getByTestId("signal-aggregate-portfolio:ranking:low-band-units"))
      .toHaveTextContent("3 dự án bị ảnh hưởng");
  });
});
