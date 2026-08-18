import { useState } from "react";
import { Search, Bell, ChevronDown, Menu, LogOut } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { Avatar } from "../ui/Avatar";

export function Topbar() {
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-4 border-b border-line bg-surface-card px-6">
      <button className="text-ink-muted lg:hidden" aria-label="Mở menu">
        <Menu className="h-5 w-5" />
      </button>

      <div className="relative hidden max-w-md flex-1 sm:block">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
        <input
          className="w-full rounded-lg border border-line bg-surface-page py-2 pl-9 pr-14 text-sm text-ink placeholder:text-ink-faint focus:border-teal focus:outline-none"
          placeholder="Tìm dự án, sản phẩm, giao dịch…"
        />
        <kbd className="absolute right-3 top-1/2 -translate-y-1/2 rounded border border-line-strong bg-white px-1.5 py-0.5 text-[10px] text-ink-faint">
          ⌘ K
        </kbd>
      </div>

      <div className="ml-auto flex items-center gap-4">
        <button className="relative text-ink-muted hover:text-ink" aria-label="Thông báo">
          <Bell className="h-5 w-5" />
          <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-teal text-[10px] font-semibold text-white">
            3
          </span>
        </button>

        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="flex items-center gap-2.5 rounded-lg py-1 pl-1 pr-2 hover:bg-surface-page"
          >
            <Avatar src={user?.avatarUrl} name={user?.name ?? "User"} size={36} />
            <div className="hidden text-left sm:block">
              <p className="text-sm font-semibold leading-tight text-ink">{user?.name}</p>
              <p className="text-xs text-ink-muted">{user?.role}</p>
            </div>
            <ChevronDown className="h-4 w-4 text-ink-muted" />
          </button>

          {menuOpen && (
            <div className="absolute right-0 top-12 w-44 rounded-lg border border-line bg-white py-1 shadow-panel">
              <button
                onClick={logout}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-ink hover:bg-surface-page"
              >
                <LogOut className="h-4 w-4" /> Đăng xuất
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
