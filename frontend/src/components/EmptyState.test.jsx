import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import EmptyState from "./EmptyState";

describe("EmptyState", () => {
  it("renders the supplied message and accessible status", () => {
    render(<EmptyState />);
    expect(screen.getByRole("status")).toHaveTextContent("Chưa có dữ liệu xếp hạng");
  });
});
