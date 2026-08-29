import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Search, ChevronDown, Menu, LogOut, Building2, Blocks, CircleDollarSign, Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { Avatar } from "../ui/Avatar";
import { fetchProjects, fetchUnits, fetchDeals } from "../../services";
import { dealStage } from "../../lib/status";
import type { Project, Unit, Deal } from "../../types";

const SEARCH_DEBOUNCE_MS = 250;
const MAX_PER_GROUP = 4;

type SearchResults = { projects: Project[]; units: Unit[]; deals: Deal[] };
const EMPTY_RESULTS: SearchResults = { projects: [], units: [], deals: [] };

export function Topbar({ onOpenNav }: { onOpenNav: () => void }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  // Tìm kiếm toàn cục: gộp kết quả từ 3 API đã có sẵn (không thêm endpoint
  // mới). Deal chưa có tham số search ở service layer nên lọc theo id/mã căn
  // ngay tại đây.
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SearchResults>(EMPTY_RESULTS);
  const boxRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults(EMPTY_RESULTS);
      setLoading(false);
      return;
    }
    setLoading(true);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const [projects, units, deals] = await Promise.all([
          fetchProjects(q),
          fetchUnits({ search: q }),
          fetchDeals(),
        ]);
        const ql = q.toLowerCase();
        setResults({
          projects: projects.slice(0, MAX_PER_GROUP),
          units: units.slice(0, MAX_PER_GROUP),
          deals: deals
            .filter((d) => d.id.toLowerCase().includes(ql) || d.unitCode.toLowerCase().includes(ql))
            .slice(0, MAX_PER_GROUP),
        });
      } catch {
        setResults(EMPTY_RESULTS);
      } finally {
        setLoading(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query]);

  // Đóng dropdown khi click ra ngoài hoặc khi chuyển trang.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);
  useEffect(() => { setOpen(false); }, [location.pathname]);

  function resetSearch() {
    setQuery("");
    setOpen(false);
  }
  function goToProject(p: Project) { navigate(`/projects/${p.id}`); resetSearch(); }
  function goToUnit(u: Unit) { navigate(`/units?q=${encodeURIComponent(u.code)}`); resetSearch(); }
  function goToDeal(d: Deal) { navigate(`/deals/${d.id}`); resetSearch(); }

  function handleSearchKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") { setOpen(false); return; }
    if (e.key !== "Enter") return;
    if (results.projects[0]) return goToProject(results.projects[0]);
    if (results.units[0]) return goToUnit(results.units[0]);
    if (results.deals[0]) return goToDeal(results.deals[0]);
  }

  const totalResults = results.projects.length + results.units.length + results.deals.length;
  const showDropdown = open && query.trim().length > 0;

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-4 border-b border-line bg-surface-card px-6">
      <button onClick={onOpenNav} className="text-ink-muted lg:hidden" aria-label="Mở menu">
        <Menu className="h-5 w-5" />
      </button>

      <div ref={boxRef} className="relative hidden max-w-md flex-1 sm:block">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
        <input
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => query.trim() && setOpen(true)}
          onKeyDown={handleSearchKeyDown}
          className="w-full rounded-lg border border-line bg-surface-page py-2 pl-9 pr-3 text-sm text-ink placeholder:text-ink-faint focus:border-teal focus:outline-none"
          placeholder="Tìm dự án, sản phẩm, giao dịch…"
        />

        {showDropdown && (
          <div className="absolute left-0 right-0 top-11 z-20 max-h-[70vh] overflow-y-auto rounded-lg border border-line bg-white p-1.5 shadow-panel">
            {loading && (
              <div className="flex items-center gap-2 px-3 py-3 text-sm text-ink-muted">
                <Loader2 className="h-4 w-4 animate-spin" /> Đang tìm…
              </div>
            )}

            {!loading && totalResults === 0 && (
              <p className="px-3 py-4 text-center text-sm text-ink-muted">Không tìm thấy kết quả phù hợp.</p>
            )}

            {!loading && results.projects.length > 0 && (
              <div className="mb-1">
                <p className="px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-faint">Dự án</p>
                {results.projects.map((p) => (
                  <button key={p.id} onClick={() => goToProject(p)} className="flex w-full items-center gap-2.5 rounded px-3 py-2 text-left text-sm hover:bg-surface-page">
                    <Building2 className="h-4 w-4 shrink-0 text-teal" />
                    <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  </button>
                ))}
              </div>
            )}

            {!loading && results.units.length > 0 && (
              <div className="mb-1">
                <p className="px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-faint">Sản phẩm</p>
                {results.units.map((u) => (
                  <button key={u.id} onClick={() => goToUnit(u)} className="flex w-full items-center gap-2.5 rounded px-3 py-2 text-left text-sm hover:bg-surface-page">
                    <Blocks className="h-4 w-4 shrink-0 text-teal" />
                    <span className="min-w-0 flex-1 truncate font-mono">{u.code}</span>
                    <span className="shrink-0 text-xs text-ink-faint">{u.tower}</span>
                  </button>
                ))}
              </div>
            )}

            {!loading && results.deals.length > 0 && (
              <div>
                <p className="px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.12em] text-ink-faint">Giao dịch</p>
                {results.deals.map((d) => {
                  const st = dealStage[d.stage] ?? { label: d.stage, color: "#64748B" };
                  return (
                    <button key={d.id} onClick={() => goToDeal(d)} className="flex w-full items-center gap-2.5 rounded px-3 py-2 text-left text-sm hover:bg-surface-page">
                      <CircleDollarSign className="h-4 w-4 shrink-0 text-teal" />
                      <span className="min-w-0 flex-1 truncate font-mono">{d.id}</span>
                      <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium" style={{ background: st.color + "1f", color: st.color }}>
                        {st.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="ml-auto flex items-center gap-4">
        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="flex items-center gap-2.5 rounded-lg py-1 pl-1 pr-2 hover:bg-surface-page"
          >
            <Avatar name={user?.name ?? "User"} size={36} />
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
