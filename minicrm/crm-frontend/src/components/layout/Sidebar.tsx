import { NavLink } from "react-router-dom";
import {
  LayoutGrid, Building2, Blocks, CircleDollarSign, ExternalLink, X,
  Sparkles, ArrowUpRight, ChevronRight,
} from "lucide-react";
import { absorbiqBaseUrl } from "../../lib/absorbiq";

const SECTIONS = [
  {
    heading: "Tổng quan",
    items: [{ to: "/", label: "Dashboard", description: "Toàn cảnh kinh doanh", icon: LayoutGrid, end: true }],
  },
  {
    heading: "Quản lý nghiệp vụ",
    items: [
      { to: "/projects", label: "Dự án", description: "Danh mục và khu vực", icon: Building2 },
      { to: "/units", label: "Căn hộ", description: "Quản lý giỏ hàng", icon: Blocks },
      { to: "/deals", label: "Giao dịch", description: "Theo dõi pipeline", icon: CircleDollarSign },
    ],
  },
];

export function Sidebar({ mobileOpen = false, onClose = () => undefined }: { mobileOpen?: boolean; onClose?: () => void }) {
  return (
    <>
      {mobileOpen && <button aria-label="Đóng menu" onClick={onClose} className="fixed inset-0 z-40 bg-navy-900/55 backdrop-blur-sm lg:hidden" />}
      <aside className={`fixed inset-y-0 left-0 z-50 flex w-[278px] shrink-0 flex-col overflow-hidden bg-[#07152f] shadow-2xl transition-transform duration-300 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 lg:shadow-none ${mobileOpen ? "translate-x-0" : "-translate-x-full"}`}>
        {/* Ánh sáng và lớp kiến trúc mờ tạo chiều sâu, không phụ thuộc ảnh ngoài. */}
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_10%_-5%,rgba(59,130,246,.38),transparent_31%),radial-gradient(circle_at_115%_67%,rgba(14,165,233,.16),transparent_34%),linear-gradient(180deg,rgba(255,255,255,.025),transparent_35%)]" />
        <div className="pointer-events-none absolute inset-0 opacity-[0.035] [background-image:linear-gradient(rgba(255,255,255,.8)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.8)_1px,transparent_1px)] [background-size:28px_28px]" />
        <div className="pointer-events-none absolute bottom-20 right-0 flex h-56 w-full items-end justify-end gap-1.5 overflow-hidden px-3 opacity-[0.045]">
          {[34, 52, 42, 76, 58, 88, 64, 46].map((height, index) => (
            <span
              key={height + index}
              className="w-7 rounded-t-sm border border-white bg-white/30"
              style={{ height: `${height}%` }}
            />
          ))}
        </div>

        {/* Logo */}
        <div className="relative flex h-[88px] shrink-0 items-center gap-3 border-b border-white/[0.08] px-5">
          <div className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-gradient-to-br from-blue-500 to-sky-400 text-base font-extrabold text-white shadow-[0_10px_30px_rgba(37,99,235,.45)] ring-1 ring-white/20">
            AF
            <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-navy-900 bg-emerald-400" />
          </div>
          <div className="min-w-0 leading-tight">
            <p className="truncate font-display text-[14px] font-bold tracking-[-0.02em] text-white">AbsorptionForecast</p>
            <div className="mt-1.5 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,.8)]" />
              <p className="text-[9px] font-bold uppercase tracking-[0.18em] text-blue-200/70">CRM Workspace</p>
            </div>
          </div>
          <button onClick={onClose} className="ml-auto flex h-8 w-8 items-center justify-center rounded-lg text-white/55 hover:bg-white/10 hover:text-white lg:hidden" aria-label="Đóng menu">
            <X className="h-4 w-4" />
          </button>
        </div>

      <nav className="relative flex min-h-0 flex-1 flex-col overflow-y-auto px-3 py-5">
        <div className="relative z-10 space-y-6">
          {SECTIONS.map((sec) => (
            <div key={sec.heading}>
              <p className="mb-2.5 px-3 text-[9px] font-bold uppercase tracking-[0.18em] text-white/35">
                {sec.heading}
              </p>
              <div className="space-y-1.5">
                {sec.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={(item as { end?: boolean }).end}
                    onClick={onClose}
                    className={({ isActive }) =>
                      `group relative flex min-h-[58px] items-center gap-3 rounded-[13px] border px-3 text-[13px] transition-all duration-200 ${
                        isActive
                          ? "border-blue-300/20 bg-gradient-to-r from-blue-500/20 to-sky-400/[0.07] text-white shadow-[inset_0_1px_0_rgba(255,255,255,.08),0_8px_24px_rgba(0,0,0,.12)]"
                          : "border-transparent text-white/65 hover:border-white/[0.08] hover:bg-white/[0.055] hover:text-white"
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive && (
                          <span className="absolute inset-y-3 left-0 w-[3px] rounded-full bg-sky-300 shadow-[0_0_14px_rgba(125,211,252,.9)]" />
                        )}
                        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] transition-all duration-200 ${isActive ? "bg-blue-500 text-white shadow-[0_6px_16px_rgba(37,99,235,.35)]" : "bg-white/[0.055] text-white/55 ring-1 ring-white/[0.04] group-hover:bg-white/10 group-hover:text-white"}`}>
                          <item.icon className="h-[18px] w-[18px]" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block font-semibold leading-5">{item.label}</span>
                          <span className={`block truncate text-[10px] leading-4 ${isActive ? "text-blue-100/65" : "text-white/35 group-hover:text-white/50"}`}>
                            {item.description}
                          </span>
                        </span>
                        <ChevronRight className={`h-3.5 w-3.5 shrink-0 transition-all ${isActive ? "translate-x-0 text-blue-200/70" : "-translate-x-1 text-white/0 group-hover:translate-x-0 group-hover:text-white/40"}`} />
                      </>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Ảnh kiến trúc hòa trực tiếp vào nền sidebar, không tạo card riêng. */}
        <div aria-hidden="true" className="relative z-0 -mx-3 -mt-7 min-h-[222px] flex-1 overflow-hidden">
          {/* Veil cùng màu sidebar phủ lên ảnh, tạo chuyển tiếp liền mạch từ menu. */}
          <div className="pointer-events-none absolute inset-x-0 top-0 z-[3] h-24 bg-gradient-to-b from-[#07152f] via-[#07152f]/75 to-transparent" />
          <div className="pointer-events-none absolute left-[30px] top-4 z-[4] h-16 w-px bg-gradient-to-b from-blue-300/25 via-blue-300/10 to-transparent" />
          <div className="sidebar-hologram-glow absolute left-1/2 top-[50%] h-40 w-44 -translate-x-1/2 -translate-y-1/2 rounded-full bg-blue-500/25 blur-3xl" />
          <img
            src="/sidebar-buildings.png"
            alt=""
            className="sidebar-building-photo absolute inset-x-0 -top-2 h-[calc(100%+24px)] w-full object-cover object-[center_66%] opacity-75 mix-blend-screen"
          />

          <div className="sidebar-orbit absolute left-1/2 top-[50%] h-[112px] w-[198px] -translate-x-1/2 -translate-y-1/2 rounded-[50%] border border-blue-200/15">
            <span className="absolute -top-1 left-1/2 h-2 w-2 rounded-full bg-sky-300/70 shadow-[0_0_12px_rgba(125,211,252,.75)]" />
          </div>
          <div className="sidebar-orbit-reverse absolute left-1/2 top-[50%] h-[78px] w-[148px] -translate-x-1/2 -translate-y-1/2 rounded-[50%] border border-dashed border-indigo-200/15" />
          <div className="sidebar-building-scan absolute left-[14%] right-[14%] top-[22%] h-px bg-gradient-to-r from-transparent via-sky-200/70 to-transparent shadow-[0_0_10px_rgba(125,211,252,.65)]" />
          <div className="sidebar-data-line absolute bottom-2 left-0 h-px w-1/2 bg-gradient-to-r from-transparent via-sky-300/60 to-transparent" />
        </div>
      </nav>

      {/* PHẦN 13 — lối sang hệ thống Product/AbsorbIQ. Link NGOÀI (khác
          origin), nên KHÔNG dùng <NavLink>. Đặt cuối sidebar, tách khỏi nhóm
          nghiệp vụ — không chen giữa "Quản lý". Ẩn hẳn khi chưa cấu hình
          VITE_PRODUCT_FRONTEND_URL. */}
      {absorbiqBaseUrl() && (
        <div className="relative border-t border-white/[0.08] p-3">
          <a
            href={absorbiqBaseUrl()}
            target="_blank"
            rel="noopener noreferrer"
            className="group relative block overflow-hidden rounded-[16px] border border-blue-300/30 bg-gradient-to-br from-blue-600 via-blue-600 to-indigo-700 p-4 text-white shadow-[0_14px_32px_rgba(37,99,235,.3)] transition duration-200 hover:-translate-y-0.5 hover:border-blue-200/50 hover:shadow-[0_18px_38px_rgba(37,99,235,.4)]"
            title="Mở AbsorbIQ — phân tích, xếp hạng và dự báo"
          >
            <span className="pointer-events-none absolute -right-8 -top-10 h-28 w-28 rounded-full bg-sky-300/25 blur-2xl" />
            <span className="pointer-events-none absolute -bottom-12 -left-6 h-24 w-24 rounded-full bg-indigo-300/20 blur-2xl" />
            <span className="relative flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/15 shadow-inner ring-1 ring-white/20 backdrop-blur-sm">
                <Sparkles className="h-[19px] w-[19px] text-sky-100" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-[9px] font-bold uppercase tracking-[0.16em] text-blue-100/70">
                  AI Decision Support
                  <ExternalLink className="h-3 w-3" />
                </span>
                <span className="mt-1 block font-display text-[15px] font-bold tracking-[-0.02em]">Mở AbsorbIQ</span>
                <span className="mt-1 block text-[10px] leading-4 text-blue-100/70">Phân tích và xếp hạng ưu tiên</span>
              </span>
              <ArrowUpRight className="mt-1 h-4 w-4 shrink-0 text-white/70 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-white" />
            </span>
          </a>
          <p className="mt-2 text-center text-[9px] font-medium text-white/30">Mở trong một tab mới</p>
        </div>
      )}
      </aside>
    </>
  );
}
