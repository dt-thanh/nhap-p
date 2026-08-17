import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ChevronLeft, Copy, XCircle, CheckCircle2, Pencil, Phone, Mail, MapPin,
  Eye, FileText, Calendar, PhoneIncoming, User, Building2, ChevronRight, ChevronDown,
} from "lucide-react";
import { fetchDealById } from "../services";
import { Badge } from "../components/ui/Badge";
import { Avatar } from "../components/ui/Avatar";
import type { DealDetail as DealDetailType, DealActivity } from "../types";

const STAGES = [
  { key: "viewing", label: "Xem / Đặt chỗ", sub: "Khách đang xem hoặc đặt chỗ", icon: Eye },
  { key: "negotiation", label: "Thương lượng", sub: "Trao đổi giá và điều khoản", icon: Pencil },
  { key: "legal", label: "Pháp lý", sub: "Hợp đồng & giấy tờ", icon: FileText },
  { key: "won", label: "Chốt (Thành công)", sub: "Giao dịch hoàn tất", icon: CheckCircle2 },
  { key: "lost", label: "Chốt (Thất bại)", sub: "Giao dịch không thành", icon: XCircle },
];

const ACT_ICON: Record<DealActivity["type"], React.ReactNode> = {
  call_out: <Phone className="h-4 w-4" />, call_in: <PhoneIncoming className="h-4 w-4" />,
  viewing: <Calendar className="h-4 w-4" />, booking: <FileText className="h-4 w-4" />,
  reminder: <Calendar className="h-4 w-4" />, note: <FileText className="h-4 w-4" />,
};

export function DealDetail() {
  const { dealId } = useParams();
  const [deal, setDeal] = useState<DealDetailType | null>(null);
  useEffect(() => { if (dealId) fetchDealById(dealId).then(setDeal); }, [dealId]);
  if (!deal) return <div className="p-8 text-ink-muted">Đang tải…</div>;
  const activeStage = 0;

  return (
    <div className="px-6 py-6">
      <Link to="/deals" className="mb-2 flex items-center gap-1 text-sm font-semibold text-teal hover:underline"><ChevronLeft className="h-4 w-4" /> Quay lại Giao dịch</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink">Chi tiết giao dịch</h1>
          <p className="mt-1 text-sm text-ink-muted">Theo dõi và quản lý tiến độ, hoạt động của giao dịch.</p>
        </div>
        <div className="flex items-center gap-6">
          <div><p className="text-xs text-ink-muted">Mã giao dịch</p><p className="flex items-center gap-1 font-semibold text-ink">{deal.id} <Copy className="h-3.5 w-3.5 text-ink-faint" /></p></div>
          <div><p className="text-xs text-ink-muted">Ngày tạo</p><p className="font-semibold text-ink">28/5/2024</p></div>
        </div>
      </div>

      {/* Stage progress */}
      <div className="stat-card mb-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold text-ink">Giai đoạn hiện tại</h2>
          <div className="flex gap-2">
            <button className="btn-ghost"><XCircle className="h-4 w-4" /> Đánh dấu Thất bại</button>
            <button className="btn-teal"><CheckCircle2 className="h-4 w-4" /> Đánh dấu Thành công</button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {STAGES.map((s, i) => {
            const active = i === activeStage;
            return (
              <div key={s.key} className={`flex flex-1 items-center gap-3 rounded-lg border px-4 py-3 ${active ? "border-teal bg-teal-soft/40" : "border-line bg-surface-page"}`}>
                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${active ? "bg-teal text-white" : "bg-white text-ink-faint"}`}><s.icon className="h-4 w-4" /></div>
                <div className="min-w-0"><p className={`text-sm font-semibold ${active ? "text-teal" : "text-ink"}`}>{s.label}</p><p className="truncate text-xs text-ink-muted">{s.sub}</p></div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Customer */}
        <Card title="Thông tin khách hàng" icon={<User className="h-4 w-4" />}>
          <div className="flex items-center gap-3">
            <Avatar name={deal.customer.name} size={48} />
            <div><p className="flex items-center gap-2 font-semibold text-ink">{deal.customer.name} <Badge tone="green">Liên hệ chính</Badge></p></div>
          </div>
          <div className="mt-3 space-y-2 text-sm text-ink-muted">
            <p className="flex items-center gap-2"><Phone className="h-4 w-4" />{deal.customer.phone}</p>
            <p className="flex items-center gap-2"><Mail className="h-4 w-4" />{deal.customer.email}</p>
            <p className="flex items-center gap-2"><MapPin className="h-4 w-4" />{deal.customer.address}</p>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 border-t border-line pt-3 text-xs">
            <div><p className="text-ink-faint">Loại KH</p><p className="font-medium text-ink">{deal.customer.type}</p></div>
            <div><p className="text-ink-faint">Nguồn</p><p className="font-medium text-ink">{deal.customer.source}</p></div>
            <div><p className="text-ink-faint">Liên hệ đầu</p><p className="font-medium text-ink">28/5/2024</p></div>
          </div>
        </Card>

        {/* Property */}
        <Card title="Thông tin bất động sản" icon={<Building2 className="h-4 w-4" />}>
          <dl className="space-y-2.5 text-sm">
            <Row k="Dự án" v={<span className="flex items-center gap-1">{deal.property.project} <ChevronRight className="h-3.5 w-3.5 text-ink-faint" /></span>} />
            <Row k="Phân khu" v={deal.property.area} />
            <Row k="Sản phẩm" v={<span className="flex items-center gap-2">{deal.property.unit} <Badge tone="green" dot>Còn trống</Badge></span>} />
            <Row k="Loại" v={deal.property.type} />
            <Row k="Giá (VND)" v={<b className="text-ink">{deal.property.price.toLocaleString("vi-VN")}</b>} />
          </dl>
        </Card>

        {/* Activity timeline */}
        <Card title="Dòng thời gian hoạt động" icon={<Calendar className="h-4 w-4" />} action={<button className="btn-ghost !px-3 !py-1.5 text-xs"><span className="font-normal">Tất cả</span><ChevronDown className="h-3.5 w-3.5" /></button>}>
          <div className="space-y-4">
            {deal.activities.map((a) => (
              <div key={a.id} className="flex gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-teal-soft text-teal">{ACT_ICON[a.type]}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <p className="flex items-center gap-2 text-sm font-semibold text-ink">{a.title} {a.tag && <Badge tone="teal">{a.tag}</Badge>}</p>
                    <span className="shrink-0 text-xs text-ink-faint">{a.at}</span>
                  </div>
                  <p className="mt-0.5 text-sm text-ink-muted">{a.description}</p>
                  <p className="mt-0.5 text-xs text-ink-faint">Bởi {a.by}</p>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Assigned sales */}
        <Card title="Sales phụ trách" icon={<User className="h-4 w-4" />}>
          <div className="flex items-center gap-3">
            <Avatar src={deal.assignedTo.avatarUrl} name={deal.assignedTo.name} size={48} />
            <div><p className="font-semibold text-ink">{deal.assignedTo.name}</p><p className="text-sm text-ink-muted">Senior Sales Manager</p></div>
          </div>
          <div className="mt-3 space-y-2 text-sm text-ink-muted">
            <p className="flex items-center gap-2"><Phone className="h-4 w-4" />+84 901 234 567</p>
            <p className="flex items-center gap-2"><Mail className="h-4 w-4" />james.tan@marinavista.com</p>
          </div>
        </Card>

        {/* Financial summary */}
        <Card title="Tóm tắt tài chính" icon={<FileText className="h-4 w-4" />} className="lg:col-span-2">
          <dl className="space-y-2.5 text-sm">
            <Row k="Giá niêm yết" v={deal.financial.unitPrice.toLocaleString("vi-VN")} />
            <Row k="Chiết khấu" v={<span className="text-status-red">{deal.financial.discount.toLocaleString("vi-VN")}</span>} />
            <Row k="Giá thực" v={<b className="text-ink">{deal.financial.netPrice.toLocaleString("vi-VN")}</b>} />
            <div className="my-1 h-px bg-line" />
            <Row k="Phí đặt chỗ" v={deal.financial.bookingFee.toLocaleString("vi-VN")} />
            <Row k="Phương thức thanh toán" v={deal.financial.paymentPlan} />
          </dl>
        </Card>
      </div>
    </div>
  );
}

function Card({ title, icon, action, children, className = "" }: { title: string; icon: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={`stat-card ${className}`}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 font-display text-base font-semibold text-ink"><span className="text-ink-muted">{icon}</span>{title}</h3>
        {action ?? <button className="flex items-center gap-1 text-sm text-teal hover:underline"><Pencil className="h-3.5 w-3.5" /> Sửa</button>}
      </div>
      {children}
    </div>
  );
}
function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex items-center justify-between"><dt className="text-ink-muted">{k}</dt><dd className="text-ink">{v}</dd></div>;
}
