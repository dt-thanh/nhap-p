// Phase F/G required test: "ProjectSelector permission filtering",
// "401/403 UX" (phần hiển thị), "role-aware action visibility" (disable dự án
// di sản không có external_id — không route ghi nào chấp nhận nó).
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ProjectSelector from "./ProjectSelector";

describe("ProjectSelector", () => {
  it("renders only the projects it was given — filtering happened server-side, not here", () => {
    render(
      <ProjectSelector
        projects={[
          { external_id: "P-0001", name: "Du an A" },
          { external_id: "P-0002", name: "Du an B" },
        ]}
        value="P-0001"
        onChange={() => {}}
        loading={false}
        status="ok"
      />,
    );
    expect(screen.getByText("Du an A")).toBeInTheDocument();
    expect(screen.getByText("Du an B")).toBeInTheDocument();
  });

  it("shows a safe unauthorized message instead of an empty/broken selector", () => {
    render(<ProjectSelector projects={[]} value={null} onChange={() => {}} loading={false} status="unauthorized" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/không thấy được dự án nào/i);
  });

  it("shows a loading state, not an empty-looking selector", () => {
    render(<ProjectSelector projects={[]} value={null} onChange={() => {}} loading status="idle" />);
    expect(screen.getByText(/Đang tải dự án/)).toBeInTheDocument();
  });

  it("a legacy project (no external_id) is listed but disabled — no scoped route accepts it", () => {
    render(
      <ProjectSelector
        projects={[{ external_id: null, name: "Legacy", project_id: "u1" }]}
        value={null}
        onChange={() => {}}
        loading={false}
        status="ok"
      />,
    );
    const option = screen.getByText(/Legacy/);
    expect(option).toBeDisabled();
  });

  it("calls onChange with the external_id of the picked project", () => {
    const onChange = vi.fn();
    render(
      <ProjectSelector
        projects={[{ external_id: "P-0001", name: "A" }]}
        value={null}
        onChange={onChange}
        loading={false}
        status="ok"
      />,
    );
    const select = screen.getByLabelText("Chọn dự án");
    select.value = "P-0001";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    expect(onChange).toHaveBeenCalledWith("P-0001");
  });
});
