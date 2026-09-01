import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ExpertAnalysisPage from "./ExpertAnalysisPage";

const api = vi.hoisted(() => ({
  listProjects: vi.fn(), listAreasScoped: vi.fn(),
  registerExpert: vi.fn(), listEvidenceDocuments: vi.fn(), listGovernanceProposals: vi.fn(),
  listGovernanceReviews: vi.fn(),
  getExpertAnalysisOverview: vi.fn(), getRankingV3Coverage: vi.fn(), listFeatureDefinitions: vi.fn(), getCurrentFeatureRubric: vi.fn(),
  createGovernanceProposal: vi.fn(), upsertJustification: vi.fn(), linkEvidence: vi.fn(),
  submitGovernanceProposal: vi.fn(), uploadEvidenceDocument: vi.fn(), requestEvidenceExtraction: vi.fn(),
  createAhpProposal: vi.fn(), saveAhpProposalDraft: vi.fn(),
}));
vi.mock("../api/endpoints", () => api);

function renderPage() { return render(<MemoryRouter><ExpertAnalysisPage /></MemoryRouter>); }

beforeEach(() => {
  vi.clearAllMocks();
  api.listProjects.mockResolvedValue([{ external_id: "P-0001", project_id: "project-1", name: "La Pura" }]);
  api.listAreasScoped.mockResolvedValue([]);
  api.registerExpert.mockResolvedValue({ id: "expert-1" });
  api.listEvidenceDocuments.mockResolvedValue([]);
  api.listGovernanceProposals.mockResolvedValue([]);
  api.listGovernanceReviews.mockResolvedValue([]);
  api.getExpertAnalysisOverview.mockResolvedValue({ documents_ready: 0, documents_processing: 0, documents_failed: 0, next_action: "Tải báo cáo" });
  api.getRankingV3Coverage.mockResolvedValue({ project: {}, market: {}, areas: [], required_features: [], evidence_blockers: [] });
  api.listFeatureDefinitions.mockResolvedValue([]);
  api.createAhpProposal.mockResolvedValue({ id: "ahp-1" });
  api.saveAhpProposalDraft.mockResolvedValue({ id: "ahp-1" });
  api.submitGovernanceProposal.mockResolvedValue({ id: "ahp-1", status: "submitted" });
});

describe("ExpertAnalysisPage", () => {
  it("renders one vertical Advisor workspace while preserving separate governed workflows", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    expect(await screen.findByRole("heading", { name: "Báo cáo tư vấn chi tiết" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Rubrics — Đánh giá định tính" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Đề xuất trọng số AHP" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tổng hợp và Gửi" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Các mục Phân tích cố vấn" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Hàng đợi phê duyệt|Lịch sử công bố|Xem trước tác động|Trọng số & AHP/)).not.toBeInTheDocument();
  });

  it("does not request global config or audit history", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await waitFor(() => expect(api.listEvidenceDocuments).toHaveBeenCalled());
    expect(api).not.toHaveProperty("listRankingConfigs");
    expect(api).not.toHaveProperty("listAuditEvents");
  });

  it("labels succeeded documents without chunks as not ready", async () => {
    api.listEvidenceDocuments.mockResolvedValue([{ id: "doc-1", original_filename: "report.pdf", lifecycle_status: "active", extraction_status: "succeeded", chunk_count: 0 }]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    expect(await screen.findByText(/chưa sẵn sàng/)).toBeInTheDocument();
  });

  it("shows a pending state and ignores duplicate bootstrap clicks", async () => {
    let resolve;
    api.registerExpert.mockImplementation(() => new Promise((res) => { resolve = res; }));
    renderPage();
    const button = await screen.findByRole("button", { name: "Mở workspace" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(api.registerExpert).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Đang mở workspace…" })).toBeDisabled();
    resolve({ id: "expert-1" });
    expect(await screen.findByRole("heading", { name: "Báo cáo tư vấn chi tiết" })).toBeInTheDocument();
  });

  it("shows an actionable bootstrap error and retries without false success", async () => {
    api.registerExpert.mockRejectedValueOnce({ status: 404, message: "not found" }).mockResolvedValueOnce({ id: "expert-1" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/chưa sẵn sàng trên máy chủ/);
    expect(screen.queryByRole("heading", { name: "Báo cáo tư vấn chi tiết" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    expect(await screen.findByRole("heading", { name: "Báo cáo tư vấn chi tiết" })).toBeInTheDocument();
    expect(api.registerExpert).toHaveBeenCalledTimes(2);
  });

  it("starts grain allocation at an exact backend-compatible 100%, not 400%", async () => {
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft", proposed_hierarchy_snapshot: null,
    }]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));

    expect(await screen.findByText("Tổng trọng số: 100%")).toBeInTheDocument();
    expect(screen.getByLabelText("Trọng số Thị trường (%)")).toHaveValue(25);
    expect(screen.getByLabelText("Trọng số Dự án (%)")).toHaveValue(25);
    expect(screen.getByLabelText("Trọng số Phân khu (%)")).toHaveValue(25);
    expect(screen.getByLabelText("Trọng số Căn hộ (%)")).toHaveValue(25);
  });

  it("rebalances unlocked grains proportionally while preserving locked grains", async () => {
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft", proposed_hierarchy_snapshot: null,
    }]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await screen.findByLabelText("Trọng số Thị trường (%)");
    fireEvent.click(await screen.findByRole("button", { name: "Khóa trọng số Dự án" }));
    fireEvent.change(screen.getByLabelText("Trọng số Thị trường (%)"), { target: { value: "40" } });

    expect(screen.getByLabelText("Trọng số Thị trường (%)")).toHaveValue(40);
    expect(screen.getByLabelText("Trọng số Dự án (%)")).toHaveValue(25);
    expect(screen.getByLabelText("Trọng số Phân khu (%)")).toHaveValue(17.5);
    expect(screen.getByLabelText("Trọng số Căn hộ (%)")).toHaveValue(17.5);
    expect(screen.getByText("Tổng trọng số: 100%")).toBeInTheDocument();
  });

  it("rejects an edit when every other grain is locked without making the allocation invalid", async () => {
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft", proposed_hierarchy_snapshot: null,
    }]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await screen.findByLabelText("Trọng số Thị trường (%)");
    for (const grain of ["Dự án", "Phân khu", "Căn hộ"]) {
      fireEvent.click(screen.getByRole("button", { name: `Khóa trọng số ${grain}` }));
    }
    fireEvent.change(screen.getByLabelText("Trọng số Thị trường (%)"), { target: { value: "40" } });

    expect(screen.getByText(/Không còn khối nào chưa khóa/)).toBeInTheDocument();
    expect(screen.getByLabelText("Trọng số Thị trường (%)")).toHaveValue(25);
    expect(screen.getByText("Tổng trọng số: 100%")).toBeInTheDocument();
  });

  it("allows an empty Project criteria block when its grain weight is zero", async () => {
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft", proposed_hierarchy_snapshot: null,
    }]);
    api.listFeatureDefinitions.mockResolvedValue([
      { id: "market-1", feature_key: "market_interest_rate", grain: "market", name: "Lãi suất thị trường", direction: "negative", missing_policy: "neutral" },
      { id: "area-1", feature_key: "area_accessibility", grain: "area", name: "Khả năng tiếp cận", direction: "positive", missing_policy: "neutral" },
    ]);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Lãi suất thị trường/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Khả năng tiếp cận/ }));
    fireEvent.change(screen.getByLabelText("Giải thích cho Lãi suất thị trường"), { target: { value: "Lãi suất cao làm giảm sức hút." } });
    fireEvent.change(screen.getByLabelText("Trọng số Dự án (%)"), { target: { value: "0" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Lưu bản nháp" }).at(-1));

    await waitFor(() => expect(api.saveAhpProposalDraft).toHaveBeenCalledWith(
      "ahp-1",
      expect.objectContaining({
        mode: "direct",
        direct_hierarchical_weights: expect.objectContaining({
          project: {},
          market: expect.objectContaining({
            market_interest_rate: expect.objectContaining({ rationale: "Lãi suất cao làm giảm sức hút." }),
          }),
        }),
      }),
    ));
    const payload = api.saveAhpProposalDraft.mock.calls.at(-1)[1].direct_hierarchical_weights;
    expect(Object.values(payload.grain_weights).reduce((sum, item) => sum + item.weight, 0)).toBeCloseTo(1, 10);
    expect(payload.grain_weights.project.weight).toBe(0);
    expect(screen.queryByText(/Cần chọn ít nhất một tiêu chí có trọng số dương cho khối: Dự án/)).not.toBeInTheDocument();
  });

  it("requires a valid Project criterion when the Project grain is above zero", async () => {
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft", proposed_hierarchy_snapshot: null,
    }]);
    api.listFeatureDefinitions.mockResolvedValue([
      { id: "market-1", feature_key: "market_interest_rate", grain: "market", name: "Lãi suất thị trường", direction: "negative", missing_policy: "neutral" },
      { id: "project-1", feature_key: "project_design_score", grain: "project", name: "Điểm chất lượng thiết kế dự án", direction: "positive", missing_policy: "neutral" },
      { id: "area-1", feature_key: "area_accessibility", grain: "area", name: "Khả năng tiếp cận", direction: "positive", missing_policy: "neutral" },
      { id: "legal-1", feature_key: "project_legal_status", grain: "project", name: "Tình trạng pháp lý", direction: "neutral", missing_policy: "neutral" },
    ]);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Lãi suất thị trường/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Khả năng tiếp cận/ }));
    fireEvent.click(screen.getAllByRole("button", { name: "Lưu bản nháp" }).at(-1));

    expect(await screen.findByText(/Cần chọn ít nhất một tiêu chí có trọng số dương cho khối: Dự án/)).toBeInTheDocument();
    expect(screen.queryByText("Tình trạng pháp lý")).not.toBeInTheDocument();
    expect(api.saveAhpProposalDraft).not.toHaveBeenCalled();
  });

  it("accepts the governed Project design criterion when Project has positive weight", async () => {
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft", proposed_hierarchy_snapshot: null,
    }]);
    api.listFeatureDefinitions.mockResolvedValue([
      { id: "market-1", feature_key: "market_interest_rate", grain: "market", name: "Lãi suất thị trường", direction: "negative", missing_policy: "neutral" },
      { id: "project-1", feature_key: "project_design_score", grain: "project", name: "Điểm chất lượng thiết kế dự án", direction: "positive", missing_policy: "neutral" },
      { id: "area-1", feature_key: "area_accessibility", grain: "area", name: "Khả năng tiếp cận", direction: "positive", missing_policy: "neutral" },
    ]);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Lãi suất thị trường/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Điểm chất lượng thiết kế dự án/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Khả năng tiếp cận/ }));
    fireEvent.click(screen.getAllByRole("button", { name: "Lưu bản nháp" }).at(-1));

    await waitFor(() => expect(api.saveAhpProposalDraft).toHaveBeenCalledWith(
      "ahp-1",
      expect.objectContaining({
        direct_hierarchical_weights: expect.objectContaining({
          project: expect.objectContaining({
            project_design_score: expect.objectContaining({ weight: 1, direction: "positive" }),
          }),
        }),
      }),
    ));
  });

  const AHP_FEATURES = [
    { id: "market-1", feature_key: "market_interest_rate", grain: "market", name: "Lãi suất thị trường", direction: "negative", missing_policy: "neutral" },
    { id: "project-1", feature_key: "project_design_score", grain: "project", name: "Điểm chất lượng thiết kế dự án", direction: "positive", missing_policy: "neutral" },
    { id: "area-1", feature_key: "area_accessibility", grain: "area", name: "Khả năng tiếp cận", direction: "positive", missing_policy: "neutral" },
  ];

  function mockSubmittableAhpProposal() {
    api.listFeatureDefinitions.mockResolvedValue(AHP_FEATURES);
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft",
      // No `proposed_hierarchy_snapshot` key — matches the real ProposalOut contract.
    }]);
    api.saveAhpProposalDraft.mockResolvedValue({
      id: "ahp-1",
      proposed_hierarchy_snapshot: {
        hierarchical_weights: {
          grain_weights: { market: { weight: 0.25 }, project: { weight: 0.25 }, area: { weight: 0.25 }, unit: { weight: 0.25 } },
          market: { market_interest_rate: { weight: 1, direction: "negative" } },
          project: { project_design_score: { weight: 1, direction: "positive" } },
          area: { area_accessibility: { weight: 1, direction: "positive" } },
        },
      },
    });
    api.listEvidenceDocuments.mockResolvedValue([{
      id: "doc-1", original_filename: "report.pdf", lifecycle_status: "active", extraction_status: "succeeded", chunk_count: 2, embedded_chunk_count: 2,
    }]);
  }

  async function saveValidAhpDraft() {
    fireEvent.click(await screen.findByRole("checkbox", { name: /Lãi suất thị trường/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Điểm chất lượng thiết kế dự án/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Khả năng tiếp cận/ }));
    fireEvent.click(screen.getAllByRole("button", { name: "Lưu bản nháp" }).at(-1));
    await waitFor(() => expect(api.saveAhpProposalDraft).toHaveBeenCalled());
    // Real component state update from the PATCH response, not from a reload —
    // this is exactly the mechanism the fix depends on.
    expect(await screen.findByText("Đã lưu bản nháp hierarchy — chưa thay đổi ranking.")).toBeInTheDocument();
  }

  it("opens the confirmation dialog after a real save, showing the frozen summary — grain weights, criteria, rationale, evidence readiness", async () => {
    mockSubmittableAhpProposal();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("heading", { name: "Xác nhận gửi CEO duyệt" })).toBeInTheDocument();
    expect(within(dialog).getByText(/Thị trường: 25/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Lãi suất thị trường/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Điểm chất lượng thiết kế dự án/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Khả năng tiếp cận/)).toBeInTheDocument();
    expect(within(dialog).getByText(/1 tài liệu sẵn sàng/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Xác nhận gửi CEO duyệt" })).toBeInTheDocument();
    expect(api.submitGovernanceProposal).not.toHaveBeenCalled();
  });

  it("shows the current application status inside the dialog when the proposal already has one", async () => {
    mockSubmittableAhpProposal();
    api.listGovernanceProposals.mockResolvedValue([{
      id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft", ahp_application_status: "awaiting_prior_run",
    }]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    fireEvent.click(screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/đang chờ phiên ranking trước hoàn tất/)).toBeInTheDocument();
  });

  it("moves focus into the dialog on open and never clips it (fixed, full-viewport backdrop)", async () => {
    mockSubmittableAhpProposal();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    fireEvent.click(screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" }));

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog).toHaveFocus());
    const backdrop = screen.getByTestId("ahp-confirm-backdrop");
    expect(backdrop.style.position).toBe("fixed");
    expect(backdrop.style.zIndex).not.toBe("");
  });

  it("emits exactly one submit call when the final confirmation is clicked, and blocks a second click while it is loading", async () => {
    mockSubmittableAhpProposal();
    let resolveSubmit;
    api.submitGovernanceProposal.mockImplementation(() => new Promise((resolve) => { resolveSubmit = resolve; }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    fireEvent.click(screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" }));
    const dialog = await screen.findByRole("dialog");
    const confirmBtn = within(dialog).getByRole("button", { name: "Xác nhận gửi CEO duyệt" });

    fireEvent.click(confirmBtn);
    expect(confirmBtn).toBeDisabled();
    expect(confirmBtn).toHaveAttribute("aria-busy", "true");
    fireEvent.click(confirmBtn); // second click while in flight — must not fire twice
    resolveSubmit({ id: "ahp-1", status: "submitted" });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.submitGovernanceProposal).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Đã gửi CEO duyệt đề xuất ranking.")).toBeInTheDocument();
  });

  it("returns focus to the primary CTA when the dialog is closed via Escape", async () => {
    mockSubmittableAhpProposal();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    const cta = screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" });
    cta.focus(); // jsdom does not auto-focus on a synthetic click, unlike a real browser
    fireEvent.click(cta);
    await screen.findByRole("dialog");

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(cta).toHaveFocus();
    expect(api.submitGovernanceProposal).not.toHaveBeenCalled();
  });

  it("returns focus to the primary CTA when the dialog is closed via its close button", async () => {
    mockSubmittableAhpProposal();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    const cta = screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" });
    cta.focus(); // jsdom does not auto-focus on a synthetic click, unlike a real browser
    fireEvent.click(cta);
    const dialog = await screen.findByRole("dialog");

    fireEvent.click(within(dialog).getByRole("button", { name: "Đóng hộp thoại xác nhận" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(cta).toHaveFocus();
  });

  it("returns focus to the primary CTA when the dialog is closed via 'Quay lại'", async () => {
    mockSubmittableAhpProposal();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    const cta = screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" });
    cta.focus();
    fireEvent.click(cta);
    const dialog = await screen.findByRole("dialog");

    fireEvent.click(within(dialog).getByRole("button", { name: "Quay lại" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(cta).toHaveFocus();
    expect(api.submitGovernanceProposal).not.toHaveBeenCalled();
  });

  it("shows an invalid-draft blocker inline instead of a misleading dialog when no hierarchy has been saved yet", async () => {
    mockSubmittableAhpProposal();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await screen.findByRole("heading", { name: "Đề xuất trọng số AHP" });

    fireEvent.click(screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText(/Cần lưu bản nháp hierarchy hợp lệ/)).toBeInTheDocument();
    expect(api.saveAhpProposalDraft).not.toHaveBeenCalled();
    expect(api.submitGovernanceProposal).not.toHaveBeenCalled();
  });

  it("shows an inline blocker (not a disabled silent button) when the project has no ready document", async () => {
    api.listFeatureDefinitions.mockResolvedValue(AHP_FEATURES);
    api.listGovernanceProposals.mockResolvedValue([{ id: "ahp-1", proposal_type: "ahp_ranking_proposal", status: "draft" }]);
    api.saveAhpProposalDraft.mockResolvedValue({
      id: "ahp-1",
      proposed_hierarchy_snapshot: { hierarchical_weights: { grain_weights: {}, market: {}, project: {}, area: {} } },
    });
    // No ready evidence document — listEvidenceDocuments defaults to [] (beforeEach).

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Vui lòng upload bằng chứng trong tab Báo cáo tư vấn chi tiết.",
    );
    const cta = screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" });
    expect(cta).not.toBeDisabled(); // clickable — never a silently inert control

    fireEvent.click(cta);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText(/Cần ít nhất một tài liệu sẵn sàng trong dự án/)).toBeInTheDocument();
    expect(api.submitGovernanceProposal).not.toHaveBeenCalled();
  });

  it("shows the backend error code and message inside the still-open dialog when AHP submission fails, and re-enables the confirm button", async () => {
    mockSubmittableAhpProposal();
    api.submitGovernanceProposal.mockRejectedValueOnce(Object.assign(new Error("Không có evidence hợp lệ"), {
      body: { detail: { error_code: "EVIDENCE_REQUIRED", message: "Không có evidence hợp lệ" } },
    }));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Mở workspace" }));
    await saveValidAhpDraft();
    fireEvent.click(screen.getByRole("button", { name: "Xem lại và gửi CEO duyệt" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Xác nhận gửi CEO duyệt" }));

    expect(await within(dialog).findByText("EVIDENCE_REQUIRED: Không có evidence hợp lệ")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument(); // stays open — the error belongs with the action that caused it
    expect(within(dialog).getByRole("button", { name: "Xác nhận gửi CEO duyệt" })).not.toBeDisabled();
  });
});
