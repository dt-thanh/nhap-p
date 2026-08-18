// Phase 6: generate() -> approve()/reject() và các trạng thái loading/error.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useAgentRecommendation } from "./useAgentRecommendation";

vi.mock("../api/endpoints", () => ({
  createRecommendation: vi.fn(),
  approveRecommendation: vi.fn(),
  rejectRecommendation: vi.fn(),
}));

import { approveRecommendation, createRecommendation, rejectRecommendation } from "../api/endpoints";

const PENDING = {
  recommendation_id: "rec-1",
  project_id: "P-0001",
  area_id: null,
  status: "pending_approval",
  ranking_run_id: "run-1",
  summary: "Ưu tiên căn A",
  recommended_actions: [],
  generated_at: "2026-08-13T00:00:00Z",
};

describe("useAgentRecommendation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("starts idle: no data, not loading, no error", () => {
    const { result } = renderHook(() => useAgentRecommendation());
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("generate() sets loading then resolves with the pending recommendation", async () => {
    createRecommendation.mockResolvedValue(PENDING);
    const { result } = renderHook(() => useAgentRecommendation());

    let promise;
    act(() => {
      promise = result.current.generate("P-0001", null);
    });
    expect(result.current.loading).toBe(true);
    await act(async () => {
      await promise;
    });

    expect(createRecommendation).toHaveBeenCalledWith("P-0001", null);
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual(PENDING);
    expect(result.current.error).toBeNull();
  });

  it("generate() failure surfaces the error and clears data", async () => {
    const err = { message: "boom", status: 500 };
    createRecommendation.mockRejectedValue(err);
    const { result } = renderHook(() => useAgentRecommendation());

    await act(async () => {
      await expect(result.current.generate("P-0001")).rejects.toEqual(err);
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeNull();
    expect(result.current.error).toEqual(err);
  });

  it("generate() then approve() transitions status to approved and records the actor", async () => {
    createRecommendation.mockResolvedValue(PENDING);
    approveRecommendation.mockResolvedValue({
      ...PENDING,
      status: "approved",
      decided_by: "sales-lead",
      decided_at: "2026-08-13T01:00:00Z",
      decision_reason: "Khớp dữ liệu thực tế",
    });

    const { result } = renderHook(() => useAgentRecommendation());
    await act(async () => {
      await result.current.generate("P-0001");
    });
    expect(result.current.data.status).toBe("pending_approval");

    await act(async () => {
      await result.current.approve("rec-1", "Khớp dữ liệu thực tế", "sales-lead");
    });

    expect(approveRecommendation).toHaveBeenCalledWith("rec-1", "Khớp dữ liệu thực tế", "sales-lead");
    expect(result.current.data.status).toBe("approved");
    expect(result.current.data.decided_by).toBe("sales-lead");
    expect(result.current.loading).toBe(false);
  });

  it("generate() then reject() transitions status to rejected", async () => {
    createRecommendation.mockResolvedValue(PENDING);
    rejectRecommendation.mockResolvedValue({ ...PENDING, status: "rejected", decided_by: "sales-lead" });

    const { result } = renderHook(() => useAgentRecommendation());
    await act(async () => {
      await result.current.generate("P-0001");
    });

    await act(async () => {
      await result.current.reject("rec-1", "Không phù hợp", "sales-lead");
    });

    expect(rejectRecommendation).toHaveBeenCalledWith("rec-1", "Không phù hợp", "sales-lead");
    expect(result.current.data.status).toBe("rejected");
  });

  it("approve() failure keeps the previous data but surfaces the error", async () => {
    createRecommendation.mockResolvedValue(PENDING);
    const err = { message: "forbidden", status: 403 };
    approveRecommendation.mockRejectedValue(err);

    const { result } = renderHook(() => useAgentRecommendation());
    await act(async () => {
      await result.current.generate("P-0001");
    });

    await act(async () => {
      await expect(result.current.approve("rec-1", "r", "viewer")).rejects.toEqual(err);
    });

    expect(result.current.error).toEqual(err);
    expect(result.current.data.status).toBe("pending_approval"); // không bị ghi đè thành "approved" giả
    await waitFor(() => expect(result.current.loading).toBe(false));
  });
});
