import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import AhpWeightPanel, { tokenToValue } from "./AhpWeightPanel";
import { computeAhpWeights } from "../../api/endpoints";

vi.mock("../../api/endpoints", () => ({ computeAhpWeights: vi.fn() }));
vi.mock("../../api/client", () => ({ isAuthError: () => false }));

const FEATURES = [
  { key: "unit_available", label: "Căn còn trống", direction: "positive", missing_value_policy: "zero", min_confidence: 0 },
  { key: "unit_demand_norm", label: "Nhu cầu trên căn (deal đang trong phễu)", direction: "positive", missing_value_policy: "zero", min_confidence: 0 },
  { key: "area_velocity_norm", label: "Tốc độ bán của phân khu (30 ngày)", direction: "positive", missing_value_policy: "neutral", min_confidence: 0 },
];

const PUBLISHED = {
  unit_available: { weight: 0.35 },
  unit_demand_norm: { weight: 0.25 },
  area_velocity_norm: { weight: 0.4 },
};

const OK_RESPONSE = {
  formula_version: "V2-AHP",
  consistency_ratio: "0.003836",
  threshold: "0.05",
  consistent: true,
  override_applied: false,
  note: "Ranking V2-AHP — CR=0.0038",
  weights: {
    unit_available: { weight: 0.5571, direction: "positive", missing_value_policy: "zero", min_confidence: 0 },
    unit_demand_norm: { weight: 0.3202, direction: "positive", missing_value_policy: "zero", min_confidence: 0 },
    area_velocity_norm: { weight: 0.1227, direction: "positive", missing_value_policy: "neutral", min_confidence: 0 },
  },
  hotspots: [],
};

function apiError(status, detail) {
  return Object.assign(new Error(detail.message), { status, data: { detail } });
}

function renderPanel(props = {}) {
  const onApply = vi.fn();
  const utils = render(
    <AhpWeightPanel features={FEATURES} publishedWeights={PUBLISHED} onApply={onApply} {...props} />,
  );
  return { ...utils, onApply };
}

function open() {
  fireEvent.click(screen.getByRole("button", { name: "Mở" }));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("thang Saaty", () => {
  it("đổi token thành giá trị, nghịch đảo về đúng 1/n", () => {
    expect(tokenToValue("eq")).toBe(1);
    expect(tokenToValue("a9")).toBe(9);
    expect(tokenToValue("b3")).toBeCloseTo(1 / 3, 12);
    // Biên dưới của thang phải nằm TRONG khoảng backend chấp nhận [1/9, 9].
    expect(tokenToValue("b9")).toBeCloseTo(1 / 9, 12);
  });
});

describe("bảng câu hỏi", () => {
  it("hỏi đúng n(n-1)/2 cặp — chỉ tam giác trên", () => {
    renderPanel();
    open();
    // 3 tiêu chí -> 3 câu. Hỏi cả n² sẽ cho phép người dùng tự mâu thuẫn.
    expect(screen.getAllByRole("combobox")).toHaveLength(3);
  });

  it("yêu cầu tối thiểu 2 tiêu chí", () => {
    renderPanel({ features: [FEATURES[0]] });
    expect(screen.getByText(/Bật ít nhất/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mở" })).not.toBeInTheDocument();
  });

  it("gửi phán đoán đúng cặp và đúng giá trị nghịch đảo", async () => {
    computeAhpWeights.mockResolvedValue(OK_RESPONSE);
    renderPanel();
    open();

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "a2" } }); // A hơn B gấp 2
    fireEvent.change(selects[1], { target: { value: "b3" } }); // B hơn A gấp 3
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));

    await waitFor(() => expect(computeAhpWeights).toHaveBeenCalledTimes(1));
    const body = computeAhpWeights.mock.calls[0][0];

    expect(body.criteria).toEqual(["unit_available", "unit_demand_norm", "area_velocity_norm"]);
    expect(body.judgments).toHaveLength(3);
    expect(body.judgments[0]).toEqual({ a: "unit_available", b: "unit_demand_norm", value: 2 });
    expect(body.judgments[1].value).toBeCloseTo(1 / 3, 12);
    expect(body.judgments[2]).toEqual({ a: "unit_demand_norm", b: "area_velocity_norm", value: 1 });
  });

  it("gửi direction/missing_value_policy lấy từ chính bản nháp", async () => {
    computeAhpWeights.mockResolvedValue(OK_RESPONSE);
    renderPanel();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));

    await waitFor(() => expect(computeAhpWeights).toHaveBeenCalled());
    // AHP chỉ cho ĐỘ LỚN — suy đoán chiều ở frontend là âm thầm đảo một tiêu chí.
    expect(computeAhpWeights.mock.calls[0][0].feature_specs.area_velocity_norm).toEqual({
      direction: "positive",
      missing_value_policy: "neutral",
      min_confidence: 0,
    });
  });
});

describe("kết quả", () => {
  it("hiện CR và trọng số kèm chênh lệch so với bản đang dùng", async () => {
    computeAhpWeights.mockResolvedValue(OK_RESPONSE);
    renderPanel();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));

    expect(await screen.findByText(/CR 0\.0038/)).toBeInTheDocument();
    expect(screen.getByText(/nhất quán/)).toBeInTheDocument();
    expect(screen.getByText("0.5571")).toBeInTheDocument();
    expect(screen.getByText("0.3500")).toBeInTheDocument(); // trọng số đang dùng
    expect(screen.getByText(/▲0\.2071/)).toBeInTheDocument();
    expect(screen.getByText(/▼0\.2773/)).toBeInTheDocument();
  });

  it("chỉ ĐIỀN vào bản nháp, không tự lưu", async () => {
    computeAhpWeights.mockResolvedValue(OK_RESPONSE);
    const { onApply } = renderPanel();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));

    fireEvent.click(await screen.findByRole("button", { name: "Điền vào bản nháp" }));
    expect(onApply).toHaveBeenCalledWith(OK_RESPONSE.weights, OK_RESPONSE.note);
    // Vòng duyệt của người là yêu cầu cứng — panel không được rút ngắn nó.
    expect(screen.getByText(/vẫn phải bấm/)).toBeInTheDocument();
  });
});

describe("cổng CR", () => {
  const HOTSPOTS = [
    { a: "unit_available", b: "unit_demand_norm", judged: "2", implied: "1.7321", deviation: "0.1438" },
  ];

  it("vượt ngưỡng: chỉ ra cặp lệch nhất và cho phép ghi lý do", async () => {
    computeAhpWeights.mockRejectedValue(
      apiError(422, {
        error_code: "CR_ABOVE_THRESHOLD",
        message: "CR=0.1240 vượt ngưỡng 0.05.",
        consistency_ratio: "0.1240",
        threshold: "0.05",
        hotspots: HOTSPOTS,
      }),
    );
    renderPanel();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));

    // Từ chối phải DÙNG ĐƯỢC: "CR = 0.12, nhập lại" thì người dùng không biết sửa gì.
    // Khớp CHÍNH thông báo lỗi: nhãn của ô override cũng chứa chữ "vượt ngưỡng",
    // nên một regex lỏng sẽ trúng hai phần tử và test hỏng vì lý do sai.
    expect(await screen.findByText(/CR=0\.1240 vượt ngưỡng/)).toBeInTheDocument();
    expect(screen.getByText(/Các so sánh lệch nhiều nhất/)).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1.7321")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeInTheDocument();
  });

  it("ô nhập lý do KHÔNG biến mất khi đang gửi lại", async () => {
    computeAhpWeights.mockRejectedValue(
      apiError(422, {
        error_code: "CR_ABOVE_THRESHOLD",
        message: "CR=0.1240 vượt ngưỡng 0.05.",
        hotspots: HOTSPOTS,
      }),
    );
    renderPanel();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));
    fireEvent.click(await screen.findByRole("checkbox"));

    const reason = screen.getByPlaceholderText(/Lý do/);
    fireEvent.change(reason, { target: { value: "Đợt đẩy hàng Q3" } });
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));

    // Hồi quy: `compute()` xoá `failure` ngay khi gửi — nếu ô chỉ phụ thuộc
    // `failure` thì nó unmount đúng lúc người dùng vừa bấm.
    await waitFor(() => expect(computeAhpWeights).toHaveBeenCalledTimes(2));
    expect(screen.getByPlaceholderText(/Lý do/)).toBeInTheDocument();
    expect(computeAhpWeights.mock.calls[1][0]).toMatchObject({
      override: true,
      override_reason: "Đợt đẩy hàng Q3",
    });
  });

  it("quá trần cứng: KHÔNG có đường vòng", async () => {
    computeAhpWeights.mockRejectedValue(
      apiError(422, {
        error_code: "CR_HARD_LIMIT_EXCEEDED",
        message: "CR=2.7586 vượt giới hạn cứng 0.20.",
        hotspots: HOTSPOTS,
      }),
    );
    renderPanel();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));

    expect(await screen.findByText(/giới hạn cứng/)).toBeInTheDocument();
    expect(screen.getByText(/Các so sánh lệch nhiều nhất/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

describe("đổi tập tiêu chí", () => {
  it("xoá kết quả cũ khi bật/tắt đặc trưng", async () => {
    computeAhpWeights.mockResolvedValue(OK_RESPONSE);
    const { rerender, onApply } = renderPanel();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Tính trọng số" }));
    expect(await screen.findByText(/CR 0\.0038/)).toBeInTheDocument();

    // Trọng số vừa tính không còn ứng với tập tiêu chí mới — giữ lại là để người
    // dùng điền một bộ trọng số cho những tiêu chí họ vừa đổi.
    rerender(
      <AhpWeightPanel
        features={FEATURES.slice(0, 2)}
        publishedWeights={PUBLISHED}
        onApply={onApply}
      />,
    );
    expect(screen.queryByText(/CR 0\.0038/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });
});
