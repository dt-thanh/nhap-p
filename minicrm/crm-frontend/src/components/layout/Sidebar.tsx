import { NavLink } from "react-router-dom";
import {
  LayoutGrid, Building2, Blocks, CircleDollarSign, Users2,
  PieChart, LineChart, Sparkles, UserCog, ScrollText, ExternalLink,
} from "lucide-react";
import { absorbiqBaseUrl } from "../../lib/absorbiq";

const SECTIONS = [
  {
    heading: "Tổng quan",
    items: [{ to: "/", label: "Dashboard", icon: LayoutGrid, end: true }],
  },
  {
    heading: "Quản lý bán hàng",
    items: [
      { to: "/projects", label: "Dự án", icon: Building2 },
      { to: "/units", label: "Sản phẩm", icon: Blocks },
      { to: "/deals", label: "Giao dịch", icon: CircleDollarSign },
      { to: "/sales-team", label: "Đội ngũ Sales", icon: Users2 },
    ],
  },
  {
    heading: "Phân tích",
    items: [
      { to: "/absorption", label: "Tỷ lệ hấp thụ", icon: PieChart },
      { to: "/forecast", label: "Dự báo", icon: LineChart },
      { to: "/ai-suggestions", label: "Gợi ý AI", icon: Sparkles },
    ],
  },
  {
    heading: "Hệ thống",
    items: [
      { to: "/users", label: "Người dùng", icon: UserCog },
      { to: "/audit-logs", label: "Nhật ký", icon: ScrollText },
    ],
  },
];

export function Sidebar() {
  return (
    <aside className="hidden w-60 shrink-0 flex-col bg-navy-900 lg:flex">
      <div className="flex flex-col items-center gap-1 px-4 pb-6 pt-7">
        <div className="flex h-14 w-14 items-center justify-center rounded-lg border-2 border-gold font-display text-lg font-bold text-gold">
          AF
        </div>
        <p className="mt-2 font-display text-base font-semibold text-white">AbsorptionForecast</p>
        <p className="font-display text-sm font-semibold tracking-wide text-gold">CRM</p>
      </div>

      <nav className="flex-1 space-y-6 overflow-y-auto px-3 pb-4">
        {SECTIONS.map((sec) => (
          <div key={sec.heading}>
            <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-wider text-white/35">
              {sec.heading}
            </p>
            <div className="space-y-0.5">
              {sec.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={(item as { end?: boolean }).end}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-teal text-white"
                        : "text-white/60 hover:bg-white/5 hover:text-white"
                    }`
                  }
                >
                  <item.icon className="h-[18px] w-[18px]" />
                  {item.label}
                </NavLink>
              ))}

        {/* PHẦN 13 — lối sang hệ thống Product/AbsorbIQ.
            Đây là link NGOÀI (khác origin), nên KHÔNG dùng <NavLink>: react-router
            chỉ điều hướng trong chính SPA này, một `to` trỏ sang cổng 5173 sẽ
            thành đường dẫn nội bộ hỏng. Ẩn hẳn khi chưa cấu hình
            VITE_PRODUCT_FRONTEND_URL — một mục menu dẫn tới link chết tệ hơn là
            không có mục nào. */}
        {absorbiqBaseUrl() && (
          <div>
            <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-wider text-white/35">
              Hệ thống liên kết
            </p>
            <a
              href={absorbiqBaseUrl()}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-white/60 transition-colors hover:bg-white/5 hover:text-white"
              title="Mở AbsorbIQ — phân tích, xếp hạng và dự báo"
            >
              <ExternalLink className="h-[18px] w-[18px]" />
              AbsorbIQ
            </a>
          </div>
        )}
            </div>
          </div>
        ))}
      </nav>

      <div className="m-3 rounded-card border border-white/10 bg-navy-800 p-4">
        <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-md border border-gold/40 text-gold">
          <Sparkles className="h-4 w-4" />
        </div>
        <p className="font-display text-sm italic leading-snug text-white/85">
          Biến insight thành cuộc sống biểu tượng.
        </p>
      </div>
    </aside>
  );
}
