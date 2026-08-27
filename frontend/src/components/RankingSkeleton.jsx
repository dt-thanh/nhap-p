import React from "react";
import { color, radius, space } from "../styles/tokens";

/** Lightweight table placeholder used while a ranking scope is loading. */
export function RankingSkeleton() {
  return (
    <div className="ranking-skeleton" role="status" aria-label="Đang tải xếp hạng" aria-busy="true">
      {[0, 1, 2, 3, 4].map((row) => (
        <div className="ranking-skeleton-row" key={row}>
          {[0, 1, 2, 3].map((cell) => <span className="ranking-skeleton-cell" key={cell} />)}
        </div>
      ))}
    </div>
  );
}

export const rankingSkeletonStyle = {
  background: color.surface,
  border: `1px solid ${color.border}`,
  borderRadius: radius.md,
  padding: space(4),
};

export default RankingSkeleton;
