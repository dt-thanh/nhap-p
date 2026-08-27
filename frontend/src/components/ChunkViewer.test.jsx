import React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChunkViewer from "./ChunkViewer";
import { listEvidenceChunks } from "../api/endpoints";

vi.mock("../api/endpoints", () => ({
  listEvidenceChunks: vi.fn(),
}));

describe("ChunkViewer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    listEvidenceChunks.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("renders nothing to fetch when no document is selected", () => {
    render(<ChunkViewer documentId={null} />);
    expect(screen.getByText("Chưa chọn tài liệu")).toBeInTheDocument();
    expect(listEvidenceChunks).not.toHaveBeenCalled();
  });

  it("renders chunks immediately when they already exist", async () => {
    listEvidenceChunks.mockResolvedValue([
      { id: "c1", chunk_index: 0, page_number: 3, embedding_model: "text-embedding-3-small", content: "sold 12 units" },
    ]);

    render(<ChunkViewer documentId="doc-1" />);
    await act(async () => {});

    expect(screen.getByText("sold 12 units")).toBeInTheDocument();
    expect(listEvidenceChunks).toHaveBeenCalledTimes(1);
  });

  it("polls every 2s while extraction is still pending, and stops once chunks arrive", async () => {
    listEvidenceChunks
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        { id: "c1", chunk_index: 0, page_number: 1, embedding_model: "text-embedding-3-small", content: "arrived" },
      ]);

    render(<ChunkViewer documentId="doc-2" />);
    await act(async () => {});
    expect(screen.getByText("Đang chờ trích xuất… (tự làm mới mỗi 2 giây)")).toBeInTheDocument();
    expect(listEvidenceChunks).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(listEvidenceChunks).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Đang chờ trích xuất… (tự làm mới mỗi 2 giây)")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(listEvidenceChunks).toHaveBeenCalledTimes(3);
    expect(screen.getByText("arrived")).toBeInTheDocument();
    expect(screen.queryByText("Đang chờ trích xuất… (tự làm mới mỗi 2 giây)")).not.toBeInTheDocument();

    // No further polling once chunks exist.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(listEvidenceChunks).toHaveBeenCalledTimes(3);
  });

  it("stops polling and shows a timeout message after 60s with no chunks", async () => {
    listEvidenceChunks.mockResolvedValue([]);

    render(<ChunkViewer documentId="doc-3" />);
    await act(async () => {});

    // Advance in 2s steps (matching the poll interval) rather than one 60s
    // jump — lets each round's `reload()` promise and effect re-run settle
    // before the next timer fires, same pattern as the "polls every 2s" test.
    for (let i = 0; i < 30; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
    }

    expect(screen.getByText(/Chưa có chunk sau 60 giây/)).toBeInTheDocument();
    const callsAtTimeout = listEvidenceChunks.mock.calls.length;

    // No further polling after the timeout fires.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(listEvidenceChunks).toHaveBeenCalledTimes(callsAtTimeout);
  });

  it("resets the wait state when documentId changes", async () => {
    listEvidenceChunks.mockResolvedValue([]);
    const { rerender } = render(<ChunkViewer documentId="doc-4" />);
    await act(async () => {});
    for (let i = 0; i < 30; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
    }
    expect(screen.getByText(/Chưa có chunk sau 60 giây/)).toBeInTheDocument();

    listEvidenceChunks.mockReset();
    listEvidenceChunks.mockResolvedValue([]);
    rerender(<ChunkViewer documentId="doc-5" />);
    await act(async () => {});

    expect(screen.queryByText(/Chưa có chunk sau 60 giây/)).not.toBeInTheDocument();
    expect(screen.getByText("Đang chờ trích xuất… (tự làm mới mỗi 2 giây)")).toBeInTheDocument();
  });
});
