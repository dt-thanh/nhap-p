import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { fetchDealById } from "../services";
import { dealStage } from "../lib/status";
import type { DealDetail as DealDetailType } from "../types";

export function DealDetail() {
  const { dealId } = useParams();
  const [deal, setDeal] = useState<DealDetailType | null>(null);

  useEffect(() => {
    if (dealId) fetchDealById(dealId).then(setDeal).catch(() => setDeal(null));
  }, [dealId]);

  if (!deal) return <div className="p-8 text-ink-muted">Đang tải…</div>;

  const st = dealStage[deal.stage] ?? { label: deal.stage, color: "#6B7688" };

  return (
    <div className="px-6 py-6">
      <nav className="mb-2 flex items-center gap-1.5 text-sm text-ink-muted">
        <Link to="/" className="hover:text-ink">Dashboard</Link><ChevronRight className="h-3.5 w-3.5" />
        <Link to="/deals" className="hover:text-ink">Giao dịch</Link><ChevronRight className="h-3.5 w-3.5" />
        <span className="font-medium text-ink">{deal.id}</span>
      </nav>

      <h1 className="font-display text-3xl font-bold text-ink mb-6">{deal.id}</h1>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="stat-card">
          <h3 className="text-sm font-medium text-ink-muted mb-2">Trạng thái</h3>
          <span className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium" style={{ background: st.color + "18", color: st.color }}>
            <span className="h-2 w-2 rounded-full" style={{ background: st.color }} />
            {st.label}
          </span>
        </div>
        <div className="stat-card">
          <h3 className="text-sm font-medium text-ink-muted mb-2">Căn hộ</h3>
          <p className="font-mono text-ink">{deal.unitCode}</p>
        </div>
        <div className="stat-card">
          <h3 className="text-sm font-medium text-ink-muted mb-2">Ngày tạo</h3>
          <p className="text-ink">{deal.createdAt ? new Date(deal.createdAt).toLocaleDateString("vi-VN") : "—"}</p>
        </div>
      </div>
    </div>
  );
}
