import { ArrowUpRight, Info } from "lucide-react";
import type { Kpi } from "../../types";

export function StatCard({ kpi, icon }: { kpi: Kpi; icon?: React.ReactNode }) {
  return (
    <div className="stat-card">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          {icon && (
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-teal-soft text-teal">
              {icon}
            </div>
          )}
          <div>
            <p className="text-sm text-ink-muted">{kpi.label}</p>
            <p className="mt-0.5 font-display text-2xl font-semibold text-ink">{kpi.value}</p>
          </div>
        </div>
        <Info className="h-4 w-4 text-ink-faint" />
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs">
        {kpi.delta != null ? (
          <span className="inline-flex items-center gap-0.5 font-semibold text-status-green">
            <ArrowUpRight className="h-3.5 w-3.5" />
            {kpi.delta}%
          </span>
        ) : (
          <span className="text-ink-faint">—</span>
        )}
        <span className="text-ink-faint">{kpi.deltaNote}</span>
      </div>
    </div>
  );
}
