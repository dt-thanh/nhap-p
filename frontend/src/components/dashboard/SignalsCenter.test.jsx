import React from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SignalsCenter from "./SignalsCenter";
import { deriveSignals } from "../../utils/signals";

const NOW = new Date("2026-08-18T00:00:00Z");

const project = (over = {}) => ({
  project_id: "11111111-1111-5111-8111-111111111111",
  name: "Harbor Crest",
  status: "active",
  external_id: "syn1-P-001",
  source_revision: 3,
  ...over,
});

const absorption = (over = {}) => ({
  units_sold: 42,
  units_remaining: 158,
  velocity_7d: 0.5,
  velocity_30d: 0.5,
  avg_velocity_30d: 0.5,
  velocity_unit: "units_per_day",
  data_status: "ready",
  calculator: "domain_units_deals",
  updated_at: "2026-08-17T20:00:00Z",
  last_successful_sync: "2026-08-17T20:00:00Z",
  last_attempted_sync: "2026-08-17T20:00:00Z",
  last_sync_status: "completed",
  ...over,
});

const ranking = (over = {}) => ({
  external_project_id: "syn1-P-001",
  computed_at: "2026-08-17T21:00:00Z",
  config_version: 2,
  units_ranked: 100,
  units_skipped: 0,
  band_counts: { high: 20, medium: 70, low: 10 },
  ...over,
});

/** Có đủ cả ba nhóm: hấp thụ (suy giảm), xếp hạng (mức thấp), dự báo. */
function allThree() {
  return deriveSignals(
    {
      entries: [
        {
          project: project(),
          absorption: absorption({ velocity_7d: 0.2, velocity_30d: 0.8, avg_velocity_30d: 0.8 }),
          ranking: ranking(),
        },
      ],
    },
    { now: NOW },
  );
}

function renderCenter(props = {}) {
  return render(
    <MemoryRouter>
      <SignalsCenter signals={[]} {...props} />
    </MemoryRouter>,
  );
}

const openDetail = (re) => fireEvent.click(screen.getByRole("button", { name: re }));

describe("SignalsCenter — ba nhóm nghiệp vụ", () => {
  it("1. render mục tín hiệu", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByRole("heading", { name: "Tín hiệu cần chú ý" })).toBeInTheDocument();
    expect(screen.getByTestId("signals-list")).toBeInTheDocument();
  });

  it("2. đếm và lọc theo NHÓM", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByTestId("count-category-absorption")).toHaveTextContent("1");
    expect(screen.getByTestId("count-category-ranking")).toHaveTextContent("1");
    expect(screen.getByTestId("count-category-forecasting")).toHaveTextContent("1");

    fireEvent.change(screen.getByLabelText("Nhóm tín hiệu"), { target: { value: "absorption" } });
    const rows = within(screen.getByTestId("signals-list")).getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute("data-category", "absorption");
  });

  it("3. đếm theo mức độ và sắp xếp critical → warning → info", () => {
    const signals = deriveSignals(
      { entries: [{ project: project(), absorption: absorption({ data_status: "no_data" }), ranking: ranking() }] },
      { now: NOW },
    );
    renderCenter({ signals });
    expect(screen.getByTestId("count-critical")).toHaveTextContent("1");
    const order = within(screen.getByTestId("signals-list"))
      .getAllByRole("listitem")
      .map((li) => li.getAttribute("data-severity"));
    const rank = { critical: 0, warning: 1, info: 2 };
    expect(order.map((s) => rank[s])).toEqual([...order.map((s) => rank[s])].sort((a, b) => a - b));
  });

  it("4. tín hiệu hấp thụ hiện đủ BỐN câu hỏi", () => {
    renderCenter({ signals: allThree() });
    openDetail(/Vận tốc bán GỘP 7 ngày thấp hơn 30 ngày/);
    expect(screen.getByText("Chuyện gì đã xảy ra")).toBeInTheDocument();
    expect(screen.getByText("Vì sao quan trọng")).toBeInTheDocument();
    expect(screen.getByText("Bằng chứng")).toBeInTheDocument();
    expect(screen.getByText("Nên làm gì")).toBeInTheDocument();
    expect(screen.getByText(/0\.8 căn\/ngày \(30 ngày, gộp\)/)).toBeInTheDocument();
  });

  it("5. tín hiệu dự báo trung thực, không có con số bịa", () => {
    renderCenter({ signals: allThree() });
    openDetail(/Forecasting unavailable/);
    const row = screen.getByTestId("signal-forecasting:not-implemented");
    expect(within(row).getByText("NOT_IMPLEMENTED")).toBeInTheDocument();
    expect(within(row).getAllByText(/forecast\.py/).length).toBeGreaterThan(0);
    // Không có giá trị/khoảng/tỷ lệ dự báo nào được hiển thị.
    expect(within(row).queryByText(/sellout|dự kiến bán hết|khoảng tin cậy \d/i)).not.toBeInTheDocument();
    expect(within(row).queryByText("Chênh lệch")).not.toBeInTheDocument();
    expect(within(row).queryByText("Giá trị nền")).not.toBeInTheDocument();
  });

  it("6. tín hiệu xếp hạng hiện số căn, mức và phiên bản cấu hình", () => {
    renderCenter({ signals: allThree() });
    const row = screen.getByTestId("signal-ranking:low-band-units:syn1-P-001");
    expect(within(row).getByText(/10 căn ở mức xếp hạng thấp/)).toBeInTheDocument();
    openDetail(/10 căn ở mức xếp hạng thấp/);
    expect(within(row).getByText("low")).toBeInTheDocument();
    expect(within(row).getByText("score < 0.33")).toBeInTheDocument();
    expect(within(row).getByText(/high=20, medium=70, low=10/)).toBeInTheDocument();
  });

  it("7. trạng thái ngưỡng hiển thị rõ VERIFIED / PROVISIONAL", () => {
    const signals = deriveSignals(
      {
        entries: [{
          project: project(),
          absorption: absorption({ last_successful_sync: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" }),
          ranking: ranking(),
        }],
      },
      { now: NOW },
    );
    renderCenter({ signals });
    openDetail(/10 căn ở mức xếp hạng thấp/);
    expect(screen.getByTestId("signal-threshold-ranking:low-band-units:syn1-P-001")).toHaveTextContent("VERIFIED");
    openDetail(/Dữ liệu hấp thụ .* stale/);
    expect(screen.getByTestId("signal-threshold-absorption:freshness:stale:syn1-P-001")).toHaveTextContent("PROVISIONAL");
  });

  it("8. liên kết dự án mang đúng danh tính và tuyến thật", () => {
    renderCenter({ signals: allThree() });
    openDetail(/Vận tốc bán GỘP 7 ngày thấp hơn 30 ngày/);
    expect(screen.getByRole("link", { name: /Phân tích hấp thụ syn1-P-001/ }))
      .toHaveAttribute("href", "/projects/syn1-P-001/dashboard");
    expect(screen.getByRole("link", { name: /Mở dự án syn1-P-001/ }))
      .toHaveAttribute("href", "/projects/syn1-P-001");
  });

  it("9. trạng thái đang tải", () => {
    renderCenter({ loading: true });
    expect(screen.getByTestId("signals-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("signals-list")).not.toBeInTheDocument();
  });

  it("10. trạng thái rỗng", () => {
    renderCenter({ signals: [] });
    expect(screen.getByText("Không có tín hiệu nào")).toBeInTheDocument();
  });

  it("11. trạng thái lỗi toàn phần", () => {
    renderCenter({ error: { message: "Sập" } });
    expect(screen.queryByTestId("signals-list")).not.toBeInTheDocument();
  });

  it("12. hỏng một phần: hấp thụ lỗi vẫn còn tín hiệu xếp hạng và dự báo", () => {
    const signals = deriveSignals(
      {
        entries: [{
          project: project(),
          absorption: null,
          absorptionError: { status: 500, message: "boom" },
          ranking: ranking(),
        }],
      },
      { now: NOW },
    );
    renderCenter({ signals });
    expect(screen.getByText(/Không đọc được hấp thụ/)).toBeInTheDocument();
    expect(screen.getByText(/10 căn ở mức xếp hạng thấp/)).toBeInTheDocument();
    expect(screen.getByText(/Forecasting unavailable/)).toBeInTheDocument();
  });

  it("13. mở / đóng chi tiết", () => {
    renderCenter({ signals: allThree() });
    const toggle = screen.getByRole("button", { name: /Forecasting unavailable/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("14. mức độ và nhóm có nhãn chữ + aria-label, không chỉ dựa vào màu", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getAllByLabelText("Mức độ: Cảnh báo").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("Trạng thái: Đang mở").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("Nhóm: Hấp thụ").length).toBeGreaterThan(0);
  });

  it("15. không trình bày số của một dự án như số toàn danh mục", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByText("Phạm vi danh mục")).toBeInTheDocument();
    // Mọi tín hiệu cấp dự án đều gắn tên dự án trong tiêu đề.
    const rows = within(screen.getByTestId("signals-list")).getAllByRole("listitem");
    for (const row of rows) {
      if (row.getAttribute("data-testid").includes("syn1-P-001")) {
        expect(within(row).getByText(/Harbor Crest/)).toBeInTheDocument();
      }
    }
  });

  it("16. chỉ đọc: không có nút đổi trạng thái", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByTestId("signals-readonly-notice")).toBeInTheDocument();
    for (const name of [/ghi nhận/i, /bắt đầu điều tra/i, /đánh dấu đã xử lý/i, /^bỏ qua$/i]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("nêu rõ các luật chưa dựng được", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByText(/Luật chưa dựng được/)).toBeInTheDocument();
    expect(screen.getByText(/Tụt hạng so với lần chạy trước/)).toBeInTheDocument();
  });
});

// ===========================================================================
// Đợt nâng cấp: huy hiệu tầng/độ tin cậy ở HÀNG ĐẦU, tín hiệu gộp cấp danh mục,
// và nhãn cho trình đọc màn hình.
// ===========================================================================

/** Ba dự án cùng dính một luật ⇒ chắc chắn sinh ra tín hiệu GỘP. */
function threeProjectsSameRule() {
  return deriveSignals(
    {
      entries: [1, 2, 3].map((i) => ({
        project: project({ external_id: `syn1-P-00${i}`, project_id: `p-${i}`, name: `Dự án ${i}` }),
        absorptionRequested: false,
        absorption: null,
        ranking: ranking({ external_project_id: `syn1-P-00${i}`, band_counts: { high: 1, medium: 1, low: 4 }, units_ranked: 6 }),
      })),
    },
    { now: NOW },
  );
}

describe("SignalsCenter — tầng, độ tin cậy và ngưỡng ở hàng đầu", () => {
  it("độ tin cậy hiện Ở HÀNG THU GỌN, đã Việt hoá", () => {
    renderCenter({ signals: allThree() });
    const chip = screen.getByTestId("signal-confidence-absorption:velocity-decreasing:syn1-P-001");
    expect(chip).toHaveTextContent("Tin cậy: Cao");
    // Không cần bấm mở mới thấy.
    expect(chip).toBeVisible();
  });

  it("huy hiệu tầng hiện ở hàng thu gọn và phân biệt T1 với T2", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByTestId("signal-layer-ranking:low-band-units:syn1-P-001")).toHaveTextContent("T2 Thương mại");
    expect(screen.getByTestId("signal-layer-forecasting:not-implemented")).toHaveTextContent("Vận hành nội bộ");
  });

  it("trạng thái ngưỡng hiện ngay hàng thu gọn, không phải chỉ trong chi tiết", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByTestId("signal-threshold-chip-absorption:velocity-decreasing:syn1-P-001"))
      .toHaveTextContent("Ngưỡng đã xác lập");
  });

  it("nhãn trình đọc màn hình gói đủ mức độ, độ tin cậy, tầng, phạm vi, ngưỡng", () => {
    renderCenter({ signals: allThree() });
    const sr = screen.getByTestId("signal-a11y-ranking:low-band-units:syn1-P-001");
    expect(sr).toHaveTextContent("Mức độ Cảnh báo");
    expect(sr).toHaveTextContent("Độ tin cậy Cao");
    expect(sr).toHaveTextContent("Tầng 2 · Rủi ro thương mại");
    expect(sr).toHaveTextContent("Phạm vi Dự án");
    expect(sr).toHaveTextContent("Ngưỡng đã xác lập");
  });

  it("bộ đếm theo tầng hiện ở đầu mục", () => {
    renderCenter({ signals: allThree() });
    expect(screen.getByTestId("count-layer-1")).toBeInTheDocument();
    expect(screen.getByTestId("count-layer-2")).toBeInTheDocument();
  });

  it("chính sách ưu tiên được nêu công khai, không phải luật ngầm", () => {
    renderCenter({ signals: allThree() });
    const policy = screen.getByTestId("signals-priority-policy");
    expect(policy).toHaveTextContent(/Tầng 1 nghiêm trọng/);
    expect(policy).toHaveTextContent(/Tầng 2 — rủi ro thương mại/);
  });

  it("điểm chú ý hiện trong bằng chứng kèm công thức TẠM", () => {
    renderCenter({ signals: allThree() });
    openDetail(/căn ở mức xếp hạng thấp/);
    const row = screen.getByTestId("signal-ranking:low-band-units:syn1-P-001");
    expect(within(row).getByText(/\/ 100/)).toBeInTheDocument();
    expect(within(row).getByText(/Công thức TẠM \(PROVISIONAL\)/)).toBeInTheDocument();
  });
});

describe("SignalsCenter — tín hiệu gộp cấp danh mục", () => {
  it("hàng cha nói rõ có bao nhiêu dự án bị ảnh hưởng", () => {
    renderCenter({ signals: threeProjectsSameRule() });
    expect(screen.getByTestId("signal-aggregate-portfolio:ranking:low-band-units"))
      .toHaveTextContent("3 dự án bị ảnh hưởng");
  });

  it("mở cha ra thì thấy đủ bằng chứng của TỪNG dự án", () => {
    renderCenter({ signals: threeProjectsSameRule() });
    openDetail(/3 dự án cùng gặp/);
    const kids = screen.getByTestId("signal-children-portfolio:ranking:low-band-units");
    expect(within(kids).getByTestId("signal-ranking:low-band-units:syn1-P-001")).toBeInTheDocument();
    expect(within(kids).getByTestId("signal-ranking:low-band-units:syn1-P-002")).toBeInTheDocument();
    expect(within(kids).getByTestId("signal-ranking:low-band-units:syn1-P-003")).toBeInTheDocument();
  });

  it("con mở/đóng ĐỘC LẬP, không bung hết cùng lúc", () => {
    renderCenter({ signals: threeProjectsSameRule() });
    openDetail(/3 dự án cùng gặp/);
    // Trạng thái mở/đóng đọc qua chính hợp đồng trợ năng (aria-expanded).
    const child = (i) => screen.getByRole("button", { name: new RegExp(`4 căn ở mức xếp hạng thấp tại "Dự án ${i}"`) });
    expect(child(2)).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(child(2));
    expect(child(2)).toHaveAttribute("aria-expanded", "true");
    // Mở con 2 không kéo theo con 3.
    expect(child(3)).toHaveAttribute("aria-expanded", "false");
  });

  it("cha nêu tổng số căn và danh sách dự án trong bằng chứng", () => {
    renderCenter({ signals: threeProjectsSameRule() });
    openDetail(/3 dự án cùng gặp/);
    const row = screen.getByTestId("signal-portfolio:ranking:low-band-units");
    expect(within(row).getByText("syn1-P-001, syn1-P-002, syn1-P-003")).toBeInTheDocument();
    expect(within(row).getByText("12")).toBeInTheDocument();
  });

  it("MỘT dự án ⇒ không có hàng gộp nào", () => {
    renderCenter({ signals: allThree() });
    expect(screen.queryByTestId("signal-portfolio:ranking:low-band-units")).not.toBeInTheDocument();
    expect(screen.getByTestId("signal-ranking:low-band-units:syn1-P-001")).toBeInTheDocument();
  });

  it("gộp KHÔNG làm tụt các con số ở đầu mục", () => {
    renderCenter({ signals: threeProjectsSameRule() });
    // 3 con + 1 cha = 4 tín hiệu nhóm xếp hạng, dù danh sách chỉ hiện 1 hàng cha.
    expect(screen.getByTestId("count-category-ranking")).toHaveTextContent("4");
  });

  it("lọc theo mức độ vẫn giữ được con đang khớp", () => {
    renderCenter({ signals: threeProjectsSameRule() });
    fireEvent.change(screen.getByLabelText("Mức độ"), { target: { value: "warning" } });
    expect(screen.getByTestId("signal-portfolio:ranking:low-band-units")).toBeInTheDocument();
  });
});
