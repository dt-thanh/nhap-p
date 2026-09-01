import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import EvidenceUploader from "./EvidenceUploader";

vi.mock("../api/endpoints", () => ({
  uploadEvidenceDocument: vi.fn(),
  requestEvidenceExtraction: vi.fn(),
  linkEvidence: vi.fn(),
}));

import { linkEvidence, requestEvidenceExtraction, uploadEvidenceDocument } from "../api/endpoints";

function pdfFile(name = "report.pdf") {
  return new File(["%PDF-1.4 mock"], name, { type: "application/pdf" });
}

describe("EvidenceUploader", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("uploads the real file bytes (no manual storage-key field) and requests extraction", async () => {
    uploadEvidenceDocument.mockResolvedValue({ id: "doc-1", reused: false, extraction_status: "not_requested" });
    requestEvidenceExtraction.mockResolvedValue({ document_id: "doc-1", extraction_status: "pending" });

    render(<EvidenceUploader expertId="expert-1" onRegistered={vi.fn()} />);
    expect(screen.queryByPlaceholderText(/storage key/i)).not.toBeInTheDocument();

    const input = screen.getByLabelText(/File báo cáo/i);
    fireEvent.change(input, { target: { files: [pdfFile()] } });
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/i }));

    await waitFor(() => expect(uploadEvidenceDocument).toHaveBeenCalledWith(
      expect.any(File),
      { proposalId: null },
    ));
    await waitFor(() => expect(requestEvidenceExtraction).toHaveBeenCalledWith("doc-1"));
    await screen.findByText(/trạng thái trích xuất: pending/i);
  });

  it("links to the justification when one is selected", async () => {
    uploadEvidenceDocument.mockResolvedValue({ id: "doc-2", reused: false, extraction_status: "not_requested" });
    requestEvidenceExtraction.mockResolvedValue({ document_id: "doc-2", extraction_status: "pending" });
    linkEvidence.mockResolvedValue(undefined);

    render(<EvidenceUploader expertId="expert-1" justificationId="just-1" onRegistered={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/File báo cáo/i), { target: { files: [pdfFile()] } });
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/i }));

    await waitFor(() =>
      expect(linkEvidence).toHaveBeenCalledWith({ document_id: "doc-2", feature_justification_id: "just-1" }),
    );
  });

  it("shows a distinct message and skips re-extraction when the upload was reused (duplicate content)", async () => {
    uploadEvidenceDocument.mockResolvedValue({ id: "doc-3", reused: true, extraction_status: "succeeded" });

    render(<EvidenceUploader expertId="expert-1" onRegistered={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/File báo cáo/i), { target: { files: [pdfFile()] } });
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/i }));

    await screen.findByText(/trùng nội dung/i);
    expect(requestEvidenceExtraction).not.toHaveBeenCalled();
  });

  it("rejects an unsupported file type client-side before calling the API", async () => {
    render(<EvidenceUploader expertId="expert-1" onRegistered={vi.fn()} />);
    const file = new File(["data"], "sheet.xlsx", { type: "application/vnd.ms-excel" });
    fireEvent.change(screen.getByLabelText(/File báo cáo/i), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/i }));

    await screen.findByText(/Chỉ nhận PDF, TXT hoặc Markdown/i);
    expect(uploadEvidenceDocument).not.toHaveBeenCalled();
  });

  it("disables the submit button until a file and expertId are both present", () => {
    render(<EvidenceUploader expertId={null} onRegistered={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Tải lên/i })).toBeDisabled();
  });
});
