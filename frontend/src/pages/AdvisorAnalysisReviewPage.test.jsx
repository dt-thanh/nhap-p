import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import AdvisorAnalysisReviewPage from "./AdvisorAnalysisReviewPage";

const api = vi.hoisted(() => ({ getAdvisorAnalysisReviewQueue: vi.fn(), getAdvisorAnalysisReviewDetail: vi.fn(), submitGovernanceReview: vi.fn(), publishGovernanceProposal: vi.fn() }));
vi.mock("../api/endpoints", () => api);

const detail = {
  proposal_id: "proposal-1", assertion_kind: "value", submitted_at: "2026-08-30T00:00:00Z", submitter_label: "Cố vấn", evidence_ready: true,
  validation: "Bằng chứng và validation hiện đủ điều kiện tại thời điểm đọc.", justifications: [],
  evidence_documents: [{ original_filename: "report.pdf", mime_type: "application/pdf", file_size_bytes: 100, extraction_status: "succeeded", lifecycle_status: "active", ready: true, file_url: "/api/v1/governance/advisor-analysis/review-queue/proposal-1/evidence/doc-1/file", citation_position_note: "Vị trí trang/chunk/trích dẫn không được lưu trong liên kết bằng chứng hiện tại." }],
};

afterEach(() => vi.restoreAllMocks());

describe("AdvisorAnalysisReviewPage", () => {
  it("uses the minimal submitted queue, shows a PDF action, and requires acknowledgement before approval", async () => {
    api.getAdvisorAnalysisReviewQueue.mockResolvedValue({ items: [{ proposal_id: "proposal-1", assertion_kind: "value", submitted_at: "2026-08-30T00:00:00Z", submitter_label: "Cố vấn", evidence_document_count: 1, evidence_ready: true, requires_attention: false }], limit: 25, offset: 0, total: 1 });
    api.getAdvisorAnalysisReviewDetail.mockResolvedValue(detail);
    render(<AdvisorAnalysisReviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Xem chi tiết" }));
    expect(await screen.findByRole("link", { name: "Mở PDF bằng chứng" })).toHaveAttribute("href", detail.evidence_documents[0].file_url);
    const button = screen.getByRole("button", { name: "Ghi nhận quyết định" });
    expect(button).toBeDisabled();
    fireEvent.click(await screen.findByRole("checkbox"));
    expect(button).toBeEnabled();
  });

  it("confirms a rejection and sends only approved/rejected with a reason", async () => {
    api.getAdvisorAnalysisReviewQueue.mockResolvedValue({ items: [{ proposal_id: "proposal-1", assertion_kind: "value", submitted_at: "2026-08-30T00:00:00Z", evidence_document_count: 1, evidence_ready: true }], limit: 25, offset: 0, total: 1 });
    api.getAdvisorAnalysisReviewDetail.mockResolvedValue(detail);
    api.submitGovernanceReview.mockResolvedValue({});
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AdvisorAnalysisReviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Xem chi tiết" }));
    fireEvent.change(await screen.findByLabelText("Quyết định"), { target: { value: "rejected" } });
    fireEvent.change(screen.getByLabelText("Lý do từ chối (bắt buộc)"), { target: { value: "Lý do cần chỉnh sửa rõ ràng" } });
    fireEvent.click(screen.getByRole("button", { name: "Ghi nhận quyết định" }));
    expect(api.submitGovernanceReview).toHaveBeenCalledWith("proposal-1", { decision: "rejected", comment: "Lý do cần chỉnh sửa rõ ràng", evidence_review_acknowledged: false });
  });

  it("shows frozen AHP criterion rationales as read-only CEO review content", async () => {
    api.getAdvisorAnalysisReviewQueue.mockResolvedValue({ items: [{ proposal_id: "proposal-1", proposal_type: "ahp_ranking_proposal", assertion_kind: "weight", submitted_at: "2026-08-30T00:00:00Z", evidence_document_count: 1, evidence_ready: true }], limit: 25, offset: 0, total: 1 });
    api.getAdvisorAnalysisReviewDetail.mockResolvedValue({
      ...detail,
      proposal_type: "ahp_ranking_proposal",
      ahp_package: {
        mode: "direct", frozen_at: "2026-08-30T00:00:00Z", current_active_config_version: 3, current_active_config_note: "current",
        hierarchical_weights: {
          grain_weights: { market: { weight: 0.35 }, project: { weight: 0 }, area: { weight: 0.25 }, unit: { weight: 0.4 } },
          market: { market_interest_rate: { weight: 1, rationale: "Lãi suất cao làm giảm sức hút." } }, project: {}, area: {},
        }, selected_criteria: ["market_interest_rate"], levels: null,
      },
    });
    render(<AdvisorAnalysisReviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Xem chi tiết" }));
    expect(await screen.findByText("Giải thích từ Expert:")).toBeInTheDocument();
    expect(screen.getByText("Lãi suất cao làm giảm sức hút.")).toBeInTheDocument();
  });

  it("lets the CEO publish an approved value proposal through the dedicated endpoint", async () => {
    api.getAdvisorAnalysisReviewQueue.mockResolvedValue({ items: [{ proposal_id: "proposal-1", assertion_kind: "value", submitted_at: "2026-08-30T00:00:00Z", evidence_document_count: 1, evidence_ready: true }], limit: 25, offset: 0, total: 1 });
    api.getAdvisorAnalysisReviewDetail.mockResolvedValue(detail);
    api.submitGovernanceReview.mockResolvedValue({ status: "approved" });
    api.publishGovernanceProposal.mockResolvedValue({ status: "published" });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AdvisorAnalysisReviewPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Xem chi tiết" }));
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.change(screen.getByLabelText("Nhận xét"), { target: { value: "Đã kiểm tra bằng chứng đầy đủ" } });
    fireEvent.click(screen.getByRole("button", { name: "Ghi nhận quyết định" }));
    expect(await screen.findByRole("button", { name: "Công bố đánh giá đã duyệt" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Công bố đánh giá đã duyệt" }));
    await waitFor(() => expect(api.publishGovernanceProposal).toHaveBeenCalledWith("proposal-1"));
  });
});
