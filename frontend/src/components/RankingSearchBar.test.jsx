import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RankingSearchBar, { filterRankingUnits } from "./RankingSearchBar";

describe("RankingSearchBar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("renders the search input and result count without duplicate filters", () => {
    render(<RankingSearchBar onFilter={vi.fn()} totalUnits={100} />);

    expect(screen.getByPlaceholderText("Tìm căn (mã, tên)...")).toBeInTheDocument();
    expect(screen.getByRole("search", { name: "Tìm kiếm xếp hạng" })).toBeInTheDocument();
    expect(screen.getByRole("search")).not.toHaveClass("ranking-card");
    expect(screen.queryByLabelText("Giá tối thiểu (triệu)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Số PN")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Mức độ quan tâm")).not.toBeInTheDocument();
    expect(screen.getByText("100", { selector: "strong" })).toBeInTheDocument();
  });

  it("debounces search changes by 300ms", () => {
    const onFilter = vi.fn(() => 2);
    render(<RankingSearchBar onFilter={onFilter} totalUnits={5} />);

    fireEvent.change(screen.getByPlaceholderText("Tìm căn (mã, tên)..."), { target: { value: "A1" } });
    expect(onFilter).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(299));
    expect(onFilter).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(onFilter).toHaveBeenCalledWith("A1");
  });

  it("filters units by code and name", () => {
    const units = [
      { unit_code: "A-01", unit_name: "Căn góc" },
      { unit_code: "B-02", unit_name: "Căn thường" },
    ];

    expect(filterRankingUnits(units, "A-01"))
      .toEqual([units[0]]);
    expect(filterRankingUnits(units, "thường"))
      .toEqual([units[1]]);
  });

  it("clears the search text and updates the result count", () => {
    const onFilter = vi.fn((term) => (term ? 1 : 5));
    render(<RankingSearchBar onFilter={onFilter} totalUnits={5} />);

    fireEvent.change(screen.getByPlaceholderText("Tìm căn (mã, tên)..."), { target: { value: "A" } });
    expect(screen.getByRole("button", { name: "Xóa tìm kiếm" })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByText("1", { selector: "strong" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Xóa tìm kiếm" }));
    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByPlaceholderText("Tìm căn (mã, tên)...")).toHaveValue("");
    expect(screen.getByText("5", { selector: "strong" })).toBeInTheDocument();
    expect(onFilter).toHaveBeenLastCalledWith("");
  });
});
