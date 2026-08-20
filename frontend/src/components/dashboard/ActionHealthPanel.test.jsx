import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import ActionHealthPanel, { deriveInventoryStatus } from "./ActionHealthPanel";

const SUMMARY = {
  total_units: 100,
  remaining_units: 80,
  velocity_7d: 7,
  velocity_30d: 5,
  estimated_weeks_to_sell_out: 16,
};

describe("ActionHealthPanel", () => {
  it("derives increasing velocity, forecast, and high remaining inventory", () => {
    render(<ActionHealthPanel summary={SUMMARY} loading={false} error={null} />);

    expect(screen.getByText("Đang tăng")).toBeInTheDocument();
    expect(screen.getByText("16,0 tuần")).toBeInTheDocument();
    expect(screen.getByText("Tồn kho còn cao")).toBeInTheDocument();
  });

  it("shows insufficient data instead of a fabricated forecast", () => {
    render(<ActionHealthPanel summary={{ total_units: 0, remaining_units: null, velocity_7d: null, velocity_30d: 0 }} loading={false} error={null} />);

    expect(screen.getAllByText("Chưa đủ dữ liệu").length).toBeGreaterThan(0);
    expect(screen.getByText("Chưa có dữ liệu")).toBeInTheDocument();
    expect(screen.getByText(/Chưa đủ dữ liệu để đưa ra khuyến nghị tự động/)).toBeInTheDocument();
  });

  it("never reports negative sell-out time and classifies sold-out inventory", () => {
    expect(deriveInventoryStatus({ total_units: 10, remaining_units: 0 })).toBe("sold_out");
    render(<ActionHealthPanel summary={{ total_units: 10, remaining_units: 0, velocity_7d: 1, velocity_30d: 2, estimated_weeks_to_sell_out: 0 }} loading={false} error={null} />);
    expect(screen.getByText("Đã bán hết")).toBeInTheDocument();
    expect(screen.getByText("0,0 tuần")).toBeInTheDocument();
  });
});
