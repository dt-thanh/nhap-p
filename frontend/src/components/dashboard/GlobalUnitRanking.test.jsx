import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import GlobalUnitRanking from "./GlobalUnitRanking";
import { GLOBAL_PAGE_SIZE, buildGlobalRanking } from "../../utils/globalUnitRanking";

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

const ranking = (items, over = {}) => ({
  external_project_id: "syn1-P-001",
  computed_at: "2026-08-17T21:00:00Z",
  config_version: 2,
  units_ranked: items.length,
  units_skipped: 0,
  band_counts: { high: 0, medium: items.length, low: 0 },
  items,
  total: items.length,
  ...over,
});

const project = (over = {}) => ({
  project_id: "11111111-1111-5111-8111-111111111111",
  name: "Harbor Crest",
  external_id: "syn1-P-001",
  ...over,
});

/** Hai dự án, ba căn — đủ để chứng minh danh sách không chia nhóm. */
function twoProjects() {
  return buildGlobalRanking([
    {
      project: project(),
      ranking: ranking([
        unit({ unit_id: "u1", unit_code: "A-01", score: "0.2000", score_percent: 20, band: "low" }),
        unit({ unit_id: "u2", unit_code: "A-02", score: "0.9000", score_percent: 90, band: "high" }),
      ]),
    },
    {
      project: project({ project_id: "p2", name: "Willow Park", external_id: "syn1-P-002" }),
      ranking: ranking(
        [unit({ unit_id: "u3", unit_code: "B-01", score: "0.5000", score_percent: 50, area_name: "Emerald 2" })],
        { external_project_id: "syn1-P-002" },
      ),
    },
  ]);
}

function renderTable(props = {}) {
  const built = props.built || twoProjects();
  return render(
    <MemoryRouter>
      <GlobalUnitRanking rows={built.rows} meta={built.meta} {...props.overrides} />
    </MemoryRouter>,
  );
}

const rowsOf = () => screen.getAllByTestId("global-ranking-row");

describe("GlobalUnitRanking — bảng xếp hạng căn toàn cục", () => {
  it("1. hiện bảng xếp hạng căn toàn cục", () => {
    renderTable();
    expect(screen.getByTestId("global-ranking-table")).toBeInTheDocument();
    expect(rowsOf()).toHaveLength(3);
  });

  it("2. tiêu đề nói rõ đây là xếp hạng CĂN", () => {
    renderTable();
    expect(screen.getByRole("heading", { name: "Xếp hạng căn toàn hệ thống" })).toBeInTheDocument();
  });

  it("3. phần mô tả nói rõ phạm vi trải khắp mọi dự án và phân khu", () => {
    renderTable();
    const note = screen.getByTestId("global-ranking-scope-note");
    expect(note).toHaveTextContent(/MỌI căn thuộc mọi dự án và mọi phân khu/);
    expect(note).toHaveTextContent(/điểm xếp hạng giảm dần/);
  });

  it("4. mỗi dòng hiện căn, dự án, phân khu, điểm và hạng", () => {
    renderTable();
    const top = rowsOf()[0];
    expect(within(top).getByText("A-02")).toBeInTheDocument();
    expect(within(top).getByText("Harbor Crest")).toBeInTheDocument();
    expect(within(top).getByText("Sapphire 1")).toBeInTheDocument();
    expect(within(top).getByText("90.0%")).toBeInTheDocument();
    expect(within(top).getByText("#1")).toBeInTheDocument();
    expect(within(top).getByText("Cao")).toBeInTheDocument();
  });

  it("5. căn điểm cao nhất đứng TRƯỚC các căn điểm thấp hơn", () => {
    renderTable();
    expect(rowsOf().map((r) => r.getAttribute("data-score"))).toEqual(["0.9", "0.5", "0.2"]);
  });

  it("5b. control đổi sang chiều thấp nhất trước ngay lập tức", () => {
    renderTable();
    const sort = screen.getByRole("combobox", { name: "Thứ tự xếp hạng" });
    fireEvent.change(sort, { target: { value: "asc" } });
    expect(sort).toHaveValue("asc");
    expect(screen.getByText("Đang xem: Top thấp nhất → cao nhất")).toBeInTheDocument();
    expect(rowsOf().map((row) => row.getAttribute("data-score"))).toEqual(["0.2", "0.5", "0.9"]);
  });

  it("6. căn điểm thấp nhất đứng CUỐI", () => {
    renderTable();
    const last = rowsOf().at(-1);
    expect(within(last).getByText("A-01")).toBeInTheDocument();
    expect(last).toHaveAttribute("data-score", "0.2");
  });

  it("7. KHÔNG chia nhóm theo dự án: hai căn cùng dự án bị cách nhau bởi căn của dự án khác", () => {
    renderTable();
    expect(rowsOf().map((r) => r.getAttribute("data-project"))).toEqual([
      "syn1-P-001",
      "syn1-P-002",
      "syn1-P-001",
    ]);
    // Không có tiêu đề phân nhóm nào mang tên dự án.
    expect(screen.queryByRole("heading", { name: /Harbor Crest|Willow Park/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });

  it("8. KHÔNG chia nhóm theo phân khu và không có tiêu đề phân khu", () => {
    renderTable();
    expect(screen.queryByRole("heading", { name: /Sapphire|Emerald/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("rowgroup")).toHaveLength(2); // thead + tbody, không có nhóm nào khác
  });

  it("9. trạng thái đang tải", () => {
    renderTable({ overrides: { loading: true } });
    expect(screen.getByTestId("global-ranking-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("global-ranking-table")).not.toBeInTheDocument();
  });

  it("10. trạng thái rỗng", () => {
    render(
      <MemoryRouter>
        <GlobalUnitRanking rows={[]} meta={{}} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Chưa có căn nào được xếp hạng")).toBeInTheDocument();
    expect(screen.queryByTestId("global-ranking-table")).not.toBeInTheDocument();
  });

  it("11. trạng thái lỗi, có nút thử lại", () => {
    const onRetry = vi.fn();
    renderTable({ overrides: { error: { message: "Sập", status: 500 }, onRetry } });
    expect(screen.queryByTestId("global-ranking-table")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("12. thiếu ngữ cảnh dự án/phân khu được đánh dấu, không im lặng", () => {
    const built = buildGlobalRanking([
      {
        project: project({ name: null }),
        ranking: ranking([unit({ unit_id: "u1", area_name: null })]),
      },
    ]);
    renderTable({ built });
    expect(screen.getByTestId("missing-project-context")).toHaveTextContent("Chưa xác định dự án");
    expect(screen.getByTestId("missing-area-context")).toHaveTextContent("Chưa xác định phân khu");
    expect(screen.getByTestId("global-ranking-notes")).toHaveTextContent(/1 căn thiếu tên dự án/);
    expect(screen.getByTestId("global-ranking-notes")).toHaveTextContent(/1 căn thiếu tên phân khu/);
  });

  it("13. căn chưa chấm được điểm: không hiện 0%, không có hạng, được giải thích", () => {
    const built = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking([
          unit({ unit_id: "u1", unit_code: "A-01", score: "0.4000", score_percent: 40 }),
          unit({ unit_id: "u2", unit_code: "A-02", score: null, score_percent: null, band: null }),
        ]),
      },
    ]);
    renderTable({ built });
    const last = rowsOf().at(-1);
    expect(within(last).getByTestId("unscored-unit")).toHaveTextContent("Chưa chấm được điểm");
    expect(within(last).queryByText("0.0%")).not.toBeInTheDocument();
    expect(within(last).getByText("Chưa phân mức")).toBeInTheDocument();
    expect(last).toHaveAttribute("data-rank", "");
    expect(screen.getByTestId("global-ranking-notes")).toHaveTextContent(/KHÔNG quy về 0 điểm/);
  });

  it("14. liên kết dự án dùng external_id thật, tuyến có thật; KHÔNG tạo liên kết phân khu", () => {
    renderTable();
    expect(screen.getByRole("link", { name: "Mở dự án syn1-P-002" })).toHaveAttribute(
      "href",
      "/projects/syn1-P-002",
    );
    // Tuyến phân khu cần external_id của phân khu, phản hồi chỉ có UUID -> không link.
    expect(screen.queryByRole("link", { name: /Sapphire|Emerald/ })).not.toBeInTheDocument();
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("href")).toMatch(/^\/projects\/[^/]+$/);
    }
  });

  it("15. bảng có tiêu đề cột đọc được và caption mô tả phạm vi", () => {
    renderTable();
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toEqual(["Hạng", "Căn", "Dự án", "Phân khu", "Điểm", "Mức", "Cập nhật"]);
    expect(screen.getByTestId("global-ranking-table").querySelector("caption").textContent).toMatch(
      /điểm giảm dần/,
    );
  });

  it("15b. bảng có vùng cuộn được đặt tên và header sticky", () => {
    renderTable();
    const region = screen.getByRole("region", { name: "Global unit ranking" });
    expect(region).toHaveStyle({ overflowX: "auto", overflowY: "auto" });
    expect(screen.getAllByRole("columnheader")[0]).toHaveStyle({ position: "sticky" });
  });

  it("16. mức và thứ tự KHÔNG chỉ dựa vào màu: đều có nhãn chữ", () => {
    renderTable();
    expect(screen.getByText("Cao")).toBeInTheDocument();
    expect(screen.getByText("Trung bình")).toBeInTheDocument();
    expect(screen.getByText("Thấp")).toBeInTheDocument();
    expect(rowsOf().map((r) => within(r).getByText(/^#\d+$/).textContent)).toEqual(["#1", "#2", "#3"]);
  });

  it("17. giới hạn phạm vi được nêu tường minh: dự án chưa nạp, dự án lỗi, dự án bị cắt", () => {
    const built = buildGlobalRanking(
      [
        { project: project(), ranking: ranking([unit({ unit_id: "u1" })], { total: 640 }) },
        {
          project: project({ name: "Willow Park", external_id: "syn1-P-002" }),
          rankingError: { status: 500, message: "boom" },
        },
      ],
      { projectsNotScanned: 4 },
    );
    renderTable({ built });
    const notes = screen.getByTestId("global-ranking-notes");
    expect(notes).toHaveTextContent(/4 dự án chưa được nạp/);
    expect(notes).toHaveTextContent(/Harbor Crest \(1\/640\)/);
    expect(notes).toHaveTextContent(/Không đọc được xếp hạng của 1 dự án: Willow Park/);
  });

  it("18. trộn nhiều phiên bản cấu hình được cảnh báo", () => {
    const built = buildGlobalRanking([
      { project: project(), ranking: ranking([unit({ unit_id: "u1" })], { config_version: 1 }) },
      {
        project: project({ name: "Willow Park", external_id: "syn1-P-002" }),
        ranking: ranking([unit({ unit_id: "u2" })], { config_version: 3 }),
      },
    ]);
    renderTable({ built });
    expect(screen.getByTestId("global-ranking-notes")).toHaveTextContent(/v1, v3/);
  });

  it("19. phân trang giữ nguyên thứ tự toàn cục và chỉ hiện khi cần", () => {
    const items = Array.from({ length: GLOBAL_PAGE_SIZE + 5 }, (_, i) =>
      unit({
        unit_id: `u-${i}`,
        unit_code: `U-${String(i).padStart(3, "0")}`,
        score: (1 - i / 1000).toFixed(4),
        score_percent: (1 - i / 1000) * 100,
      }),
    );
    const built = buildGlobalRanking([{ project: project(), ranking: ranking(items) }]);
    const onPageChange = vi.fn();
    const { rerender } = render(
      <MemoryRouter>
        <GlobalUnitRanking rows={built.rows} meta={built.meta} page={0} onPageChange={onPageChange} />
      </MemoryRouter>,
    );
    expect(rowsOf()).toHaveLength(GLOBAL_PAGE_SIZE);
    expect(rowsOf()[0]).toHaveAttribute("data-rank", "1");

    fireEvent.click(screen.getByRole("button", { name: "Trang sau" }));
    expect(onPageChange).toHaveBeenCalledWith(1);

    rerender(
      <MemoryRouter>
        <GlobalUnitRanking rows={built.rows} meta={built.meta} page={1} onPageChange={onPageChange} />
      </MemoryRouter>,
    );
    expect(rowsOf()[0]).toHaveAttribute("data-rank", String(GLOBAL_PAGE_SIZE + 1));
    expect(rowsOf().at(-1)).toHaveAttribute("data-rank", String(GLOBAL_PAGE_SIZE + 5));
  });

  it("20. không có phân trang khi chỉ có một trang", () => {
    renderTable();
    expect(screen.queryByTestId("global-ranking-pager")).not.toBeInTheDocument();
  });

  it("21. số dòng đang hiện và số dự án được nêu chính xác", () => {
    renderTable();
    expect(screen.getByTestId("global-ranking-meta")).toHaveTextContent("Hiện 3 / 3 căn · 2 dự án");
  });

  it("22. dự án đã áp dụng AHP (v3): hiện nhãn AHP (v3) và ưu tiên effective_score_percent", () => {
    const built = buildGlobalRanking([
      {
        project: project(),
        ranking: ranking(
          [unit({ unit_id: "u1", unit_code: "A-01", score: "0.2000", score_percent: 20, effective_score: "0.9500", effective_score_percent: 95, band: "high" })],
          { ranking_formula: "v3_hierarchical" },
        ),
      },
    ]);
    renderTable({ built });

    expect(screen.getByText("AHP (v3)")).toBeInTheDocument();
    expect(screen.getByText("95.0%")).toBeInTheDocument();
    expect(screen.queryByText("20.0%")).not.toBeInTheDocument();
  });

  it("23. dự án v2 thuần: không hiện nhãn AHP (v3)", () => {
    renderTable();
    expect(screen.queryByText("AHP (v3)")).not.toBeInTheDocument();
  });
});
