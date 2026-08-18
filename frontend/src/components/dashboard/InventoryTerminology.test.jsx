import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AreaComparison from "./AreaComparison";
import AreaDetailTable from "./AreaDetailTable";

const AREAS = [{
  id: "area-1",
  name: "North Tower",
  total_units: 112,
  sold: 60,
  remaining: 52,
  available_remaining_units: 40,
  reserved_units: 12,
  absorption_rate: 53.6,
  velocity: null,
  latest_data: null,
  status: undefined,
}];

describe("dashboard inventory terminology", () => {
  it("shows total remaining, immediately available, and reserved values in area comparison", () => {
    render(<AreaComparison areas={AREAS} loading={false} error={null} />);

    expect(screen.getByText(/Có thể bán ngay: 40 căn/)).toBeInTheDocument();
    expect(screen.getByText(/Đang giữ chỗ: 12 căn/)).toBeInTheDocument();
  });

  it("shows insufficient data instead of zero for missing area breakdown values", () => {
    render(<AreaDetailTable areas={[{ ...AREAS[0], available_remaining_units: null, reserved_units: null }]} loading={false} error={null} />);

    expect(screen.getByRole("columnheader", { name: "Còn lại tổng cộng" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Có thể bán ngay" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Đang giữ chỗ" })).toBeInTheDocument();
    expect(screen.getAllByText("Chưa đủ dữ liệu")).toHaveLength(2);
  });
});
