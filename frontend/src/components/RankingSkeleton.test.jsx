import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RankingSkeleton from "./RankingSkeleton";

describe("RankingSkeleton", () => {
  it("renders five accessible placeholder rows", () => {
    render(<RankingSkeleton />);
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
    expect(document.querySelectorAll(".ranking-skeleton-row")).toHaveLength(5);
  });
});
