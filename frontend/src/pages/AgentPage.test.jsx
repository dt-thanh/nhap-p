// AgentPage — luồng đề xuất tư vấn THẬT: chọn dự án -> tạo đề xuất -> duyệt/từ
// chối, đúng gate vai trò (`pipeline_operator`+ mới duyệt được), và các trạng
// thái lỗi 401/403/404/409/503 không bị hiểu nhầm thành nhau.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AgentPage from "./AgentPage";

vi.mock("../api/endpoints", () => ({
  listProjects: vi.fn(),
  listAreasScoped: vi.fn(),
  getMePermissions: vi.fn(),
  chatWithAgent: vi.fn(),
  createRecommendation: vi.fn(),
  approveRecommendation: vi.fn(),
  rejectRecommendation: vi.fn(),
  executeRecommendation: vi.fn(),
}));

import {
  listProjects,
  listAreasScoped,
  getMePermissions,
  chatWithAgent,
  createRecommendation,
  approveRecommendation,
  rejectRecommendation,
} from "../api/endpoints";

const PROJECTS = [{ project_id: "u-1", external_id: "prj_op1", name: "Ocean Park 1", status: "active" }];

const PENDING = {
  recommendation_id: "rec-1",
  project_id: "prj_op1",
  area_id: null,
  status: "pending_approval",
  ranking_run_id: "run-1",
  summary: "Top căn ... mức=high (90.0%) ...\n\nXếp hạng là một phép tính tất định...",
  recommended_actions: [{ unit_id: "u-9", action: "Gọi tư vấn", reason: "Điểm cao" }],
  generated_at: "2026-08-15T00:00:00Z",
  decided_by: null,
  decided_at: null,
  decision_reason: null,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentPage />
    </MemoryRouter>,
  );
}

async function chooseProjectAndGenerate() {
  await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "prj_op1" } });
  const btn = await screen.findByRole("button", { name: "Tạo đề xuất" });
  await waitFor(() => expect(btn).toBeEnabled());
  fireEvent.click(btn);
  await waitFor(() => expect(createRecommendation).toHaveBeenCalled());
}

describe("AgentPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listProjects.mockResolvedValue(PROJECTS);
    listAreasScoped.mockResolvedValue([]);
  });

  it("explains why proposal generation needs a project instead of silently doing nothing", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    renderPage();

    const btn = await screen.findByRole("button", { name: "Tạo đề xuất" });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);

    await waitFor(() => expect(screen.getAllByText(/hỏi rõ tên dự án trong chat/).length).toBeGreaterThan(1));
    expect(createRecommendation).not.toHaveBeenCalled();
  });

  it("uses the project resolved by chat when creating a proposal", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    chatWithAgent.mockResolvedValue({
      response: "Đã phân tích Vinhomes Times City",
      tool_calls: ["project_overview"],
      sources: [],
      resolved_project_id: "prj_tmc",
    });
    createRecommendation.mockResolvedValue({ ...PENDING, project_id: "prj_tmc" });
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText(/Hỏi về dự án/), {
      target: { value: "Ở dự án Vinhomes Times City, tạo đề xuất giúp tôi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Gửi câu hỏi" }));

    expect(await screen.findByText(/Đã phân tích Vinhomes Times City/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Tạo đề xuất" }));

    await waitFor(() => expect(createRecommendation).toHaveBeenCalledWith("prj_tmc", undefined));
  });

  it("business_viewer can generate (create+read) but sees an explicit insufficient-role state instead of approve/reject buttons", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    createRecommendation.mockResolvedValue(PENDING);
    renderPage();

    await chooseProjectAndGenerate();

    expect(createRecommendation).toHaveBeenCalledWith("prj_op1", undefined);
    expect(await screen.findByText(/Chờ duyệt/)).toBeInTheDocument();
    expect(screen.getByText(/không đủ để duyệt\/từ chối/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Duyệt" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Từ chối" })).not.toBeInTheDocument();
  });

  it("pipeline_operator sees enabled approve/reject, requires an actor name, and approving calls the real endpoint", async () => {
    getMePermissions.mockResolvedValue({ role: "pipeline_operator", project_scope: "ALL" });
    createRecommendation.mockResolvedValue(PENDING);
    approveRecommendation.mockResolvedValue({
      ...PENDING,
      status: "approved",
      decided_by: "Nguyen Van A",
      decided_at: "2026-08-15T01:00:00Z",
      decision_reason: "ok",
    });
    renderPage();

    await chooseProjectAndGenerate();
    await screen.findByText(/Chờ duyệt/);

    const approveBtn = screen.getByRole("button", { name: "Duyệt" });
    expect(approveBtn).toBeDisabled(); // chưa nhập actor

    fireEvent.change(screen.getByPlaceholderText("Tên người duyệt"), { target: { value: "Nguyen Van A" } });
    expect(approveBtn).toBeEnabled();
    fireEvent.click(approveBtn);

    await waitFor(() => expect(approveRecommendation).toHaveBeenCalledWith("rec-1", "", "Nguyen Van A"));
    expect(await screen.findByText(/Đã duyệt bởi/)).toBeInTheDocument();
  });

  it("reject calls the real endpoint and reflects the rejected status", async () => {
    getMePermissions.mockResolvedValue({ role: "admin", project_scope: "ALL" });
    createRecommendation.mockResolvedValue(PENDING);
    rejectRecommendation.mockResolvedValue({ ...PENDING, status: "rejected", decided_by: "Admin" });
    renderPage();

    await chooseProjectAndGenerate();
    await screen.findByText(/Chờ duyệt/);

    fireEvent.change(screen.getByPlaceholderText("Tên người duyệt"), { target: { value: "Admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Từ chối" }));

    await waitFor(() => expect(rejectRecommendation).toHaveBeenCalledWith("rec-1", "", "Admin"));
    expect(await screen.findByText(/Đã từ chối bởi/)).toBeInTheDocument();
  });

  it("a 403 from an insufficient-role approve attempt shows a distinct message, not a generic error", async () => {
    getMePermissions.mockResolvedValue({ role: "pipeline_operator", project_scope: "ALL" });
    createRecommendation.mockResolvedValue(PENDING);
    const err = { message: "Bạn không có quyền thực hiện thao tác này.", status: 403 };
    approveRecommendation.mockRejectedValue(err);
    renderPage();

    await chooseProjectAndGenerate();
    fireEvent.change(await screen.findByPlaceholderText("Tên người duyệt"), { target: { value: "A" } });
    fireEvent.click(screen.getByRole("button", { name: "Duyệt" }));

    expect(await screen.findByText(err.message)).toBeInTheDocument();
  });

  it("a 409 (already decided) generate error shows a distinct conflict message", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    const err = { message: "ALREADY_DECIDED", status: 409 };
    createRecommendation.mockRejectedValue(err);
    renderPage();

    await chooseProjectAndGenerate();

    expect(await screen.findByText(/Đề xuất đã được quyết định trước đó/)).toBeInTheDocument();
  });

  it("a 503 (no active ranking config) generate error is shown as its own explicit state, not fabricated data", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    const err = { message: "NO_ACTIVE_CONFIG", status: 503 };
    createRecommendation.mockRejectedValue(err);
    renderPage();

    await chooseProjectAndGenerate();

    expect(await screen.findByText(/Chưa có cấu hình xếp hạng đang hoạt động/)).toBeInTheDocument();
    expect(screen.queryByText(/Chờ duyệt/)).not.toBeInTheDocument();
  });

  it("renders the recommended actions list from real data, not a placeholder", async () => {
    getMePermissions.mockResolvedValue({ role: "business_viewer", project_scope: "ALL" });
    createRecommendation.mockResolvedValue(PENDING);
    renderPage();

    await chooseProjectAndGenerate();

    expect(await screen.findByText(/Gọi tư vấn/)).toBeInTheDocument();
    expect(screen.getByText(/Điểm cao/)).toBeInTheDocument();
    expect(screen.getByText("Rủi ro sử dụng đề xuất")).toBeInTheDocument();
    expect(screen.getByText("Độ phủ tín hiệu đầu vào")).toBeInTheDocument();
    expect(screen.queryByText("Độ tin cậy")).not.toBeInTheDocument();
  });
});
