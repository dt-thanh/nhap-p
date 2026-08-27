import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PreviewOverviewPage from "./PreviewOverviewPage";

vi.mock("../components/dashboard/AbsorptionDashboard", () => ({
  default: ({ standalone, preview }) => (
    <div data-testid="preview-dashboard" data-standalone={String(standalone)} data-preview={String(preview)}>
      <h1>Actual overview workspace</h1>
    </div>
  ),
}));

describe("PreviewOverviewPage", () => {
  it("renders a shell-free, same-origin preview target with standalone scope handling", () => {
    render(
      <MemoryRouter initialEntries={["/preview/overview?project=project-a&area=area-a"]}>
        <PreviewOverviewPage />
      </MemoryRouter>,
    );

    const root = screen.getByTestId("overview-preview-root");

    expect(root).toBeInTheDocument();
    expect(root.style.width).toBe("100%");
    expect(root.style.height).toBe("100vh");
    expect(root.style.overflowY).toBe("auto");
    expect(root.style.overflowX).toBe("auto");
    expect(root.style.boxSizing).toBe("border-box");
    expect(screen.getByRole("heading", { name: "Actual overview workspace" })).toBeInTheDocument();
    expect(screen.getByTestId("preview-dashboard")).toHaveAttribute("data-standalone", "true");
    expect(screen.getByTestId("preview-dashboard")).toHaveAttribute("data-preview", "true");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});
