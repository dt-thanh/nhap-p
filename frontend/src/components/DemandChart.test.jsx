import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("recharts", () => ({
  Cell: ({ fill }) => <span data-testid="chart-cell" data-fill={fill} />,
  Legend: () => <div data-testid="chart-legend">Legend</div>,
  Pie: ({ children }) => <div data-testid="pie">{children}</div>,
  PieChart: ({ children }) => <div data-testid="pie-chart">{children}</div>,
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
  Tooltip: () => <div data-testid="tooltip" />,
}));

import DemandChart from "./DemandChart";
import { countDemandLevels } from "../utils/rankingDemand";

const units = [
  { unit_id: "u1", unit_code: "A-01", band: "high" },
  { unit_id: "u2", unit_code: "A-02", band: "medium" },
  { unit_id: "u3", unit_code: "A-03", band: "medium" },
  { unit_id: "u4", unit_code: "A-04", band: "medium" },
  { unit_id: "u5", unit_code: "A-05", band: "low" },
];

describe("DemandChart", () => {
  it("uses the authoritative unit bands for category counts", () => {
    expect(countDemandLevels(units)).toEqual({ high: 1, medium: 3, low: 1 });
  });

  it("renders the pie and vertical category summaries in high-to-low order", () => {
    render(<DemandChart units={units} categoryCounts={{ high: 20, medium: 60, low: 20 }} />);

    expect(screen.getByRole("region", { name: /Phân bố mức độ quan tâm/i })).toBeInTheDocument();
    expect(screen.getByTestId("pie-chart")).toBeInTheDocument();
    expect(screen.getAllByTestId("chart-cell")).toHaveLength(3);

    const categoryList = screen.getByRole("list", { name: "Các nhóm mức độ quan tâm" });
    const donut = document.querySelector(".demand-donut");
    expect(categoryList).toHaveClass("demand-summary-list");
    expect(donut).toBeInTheDocument();
    expect(categoryList.compareDocumentPosition(donut) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId("chart-legend")).not.toBeInTheDocument();
    const rows = within(categoryList).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Quan tâm cao"),
      expect.stringContaining("Quan tâm trung bình"),
      expect.stringContaining("Quan tâm thấp"),
    ]);
    expect(within(rows[0]).getByText("20 căn")).toBeInTheDocument();
    expect(within(rows[1]).getByText("60 căn")).toBeInTheDocument();
    expect(within(rows[2]).getByText("20 căn")).toBeInTheDocument();
    expect(within(rows[2]).getByText("💤")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Quan tâm thấp")).toBeInTheDocument();
  });

  it("filters through clickable category summaries while supporting chart visibility", () => {
    const onCategorySelect = vi.fn();
    const { rerender } = render(
      <DemandChart units={units} activeCategory={null} onCategorySelect={onCategorySelect} />,
    );

    const categoryList = screen.getByRole("list", { name: "Các nhóm mức độ quan tâm" });
    const categoryButtons = within(categoryList).getAllByRole("button");
    expect(categoryButtons).toHaveLength(3);
    expect(categoryButtons[0]).toHaveClass("demand-row", "demand-row--high");
    expect(categoryButtons[0]).toHaveAttribute("aria-pressed", "false");
    expect(categoryButtons[1]).toHaveAttribute("aria-pressed", "false");
    categoryButtons[0].focus();
    expect(categoryButtons[0]).toHaveFocus();
    fireEvent.click(categoryButtons[0]);
    expect(onCategorySelect).toHaveBeenCalledWith("high");

    rerender(<DemandChart units={units} activeCategory="high" onCategorySelect={onCategorySelect} />);
    const activeButton = within(categoryList).getAllByRole("button")[0];
    expect(activeButton).toHaveClass("is-active");
    expect(activeButton).toHaveAttribute("aria-pressed", "true");
    expect(within(categoryList).getAllByRole("button")[1]).toHaveAttribute("aria-pressed", "false");
    expect(within(activeButton).getByText("✓")).toBeInTheDocument();
    fireEvent.click(activeButton);
    expect(onCategorySelect).toHaveBeenLastCalledWith(null);

    fireEvent.click(screen.getByRole("button", { name: "Ẩn biểu đồ" }));
    expect(screen.queryByTestId("pie-chart")).not.toBeInTheDocument();

    rerender(<DemandChart units={[]} />);
    expect(screen.getByText("Chưa có căn để phân loại nhu cầu.")).toBeInTheDocument();
    rerender(<DemandChart units={[units[0]]} />);
    expect(screen.getAllByText(/Quan tâm cao/i).length).toBeGreaterThan(0);
    expect(screen.getByText("1 căn")).toBeInTheDocument();
  });
});
