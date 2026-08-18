// Phase: Dashboard Integration — "velocity direction: increasing, decreasing,
// stable, missing".
import { describe, it, expect } from "vitest";
import { deriveVelocityDirection, VELOCITY_DIRECTION_LABEL } from "./velocity";

describe("deriveVelocityDirection", () => {
  it("velocity_7d > velocity_30d -> increasing", () => {
    expect(deriveVelocityDirection(3, 1.5)).toBe("increasing");
  });

  it("velocity_7d < velocity_30d -> decreasing", () => {
    expect(deriveVelocityDirection(1, 2)).toBe("decreasing");
  });

  it("velocity_7d === velocity_30d -> stable", () => {
    expect(deriveVelocityDirection(2, 2)).toBe("stable");
  });

  it("Decimal-as-string inputs (backend serializes Decimal as JSON strings) still compare numerically", () => {
    expect(deriveVelocityDirection("1.428571", "0.5")).toBe("increasing");
    expect(deriveVelocityDirection("0.5", "1.428571")).toBe("decreasing");
    expect(deriveVelocityDirection("0.75", "0.75")).toBe("stable");
  });

  it("missing velocity_7d -> unknown, no directional claim", () => {
    expect(deriveVelocityDirection(null, 2)).toBe("unknown");
    expect(deriveVelocityDirection(undefined, 2)).toBe("unknown");
  });

  it("missing velocity_30d -> unknown, no directional claim", () => {
    expect(deriveVelocityDirection(2, null)).toBe("unknown");
    expect(deriveVelocityDirection(2, undefined)).toBe("unknown");
  });

  it("both missing -> unknown", () => {
    expect(deriveVelocityDirection(null, null)).toBe("unknown");
  });

  it("non-numeric input -> unknown, not NaN comparisons", () => {
    expect(deriveVelocityDirection("not-a-number", 2)).toBe("unknown");
  });
});

describe("VELOCITY_DIRECTION_LABEL", () => {
  it("unknown has no arrow/text to render (caller must skip rendering, not show an empty claim)", () => {
    expect(VELOCITY_DIRECTION_LABEL.unknown.arrow).toBeNull();
    expect(VELOCITY_DIRECTION_LABEL.unknown.text).toBeNull();
  });

  it("increasing/decreasing/stable each have a visible label and a distinct tone", () => {
    expect(VELOCITY_DIRECTION_LABEL.increasing.arrow).toBe("↑");
    expect(VELOCITY_DIRECTION_LABEL.decreasing.arrow).toBe("↓");
    expect(VELOCITY_DIRECTION_LABEL.stable.arrow).toBe("→");
    const tones = new Set([
      VELOCITY_DIRECTION_LABEL.increasing.tone,
      VELOCITY_DIRECTION_LABEL.decreasing.tone,
      VELOCITY_DIRECTION_LABEL.stable.tone,
    ]);
    expect(tones.size).toBeGreaterThan(1);
  });
});
