// ============================================================
// SERVICE LAYER — điểm nối duy nhất với backend.
// Khi API thật sẵn sàng: đổi USE_MOCK = false (hoặc đọc từ .env).
// UI/components/hooks KHÔNG cần sửa gì.
// ============================================================
import type {
  LoginResponse, Project, Area, Unit, Deal, DealDetail, SalesStaff,
} from "../types";
import * as M from "../mocks";

const USE_MOCK = true;

function delay<T>(v: T, ms = 300): Promise<T> {
  return new Promise((r) => setTimeout(() => r(v), ms));
}

// ---------- Auth ----------
export async function login(email: string, password: string): Promise<LoginResponse> {
  if (!USE_MOCK) {
    const res = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) throw new Error("Email hoặc mật khẩu không đúng");
    return res.json();
  }
  // Mock: KHÔNG ghi cứng mật khẩu trong source (tránh lộ secret trong bundle).
  // Ở chế độ demo, chấp nhận mọi email hợp lệ + mật khẩu tối thiểu 6 ký tự.
  // Khi USE_MOCK = false, xác thực thật do backend đảm nhiệm ở nhánh trên.
  await delay(null, 600);
  const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  if (emailOk && password.length >= 6) {
    return {
      token: "mock-jwt-token-" + Date.now(),
      user: { ...M.currentUser, email },
    };
  }
  throw new Error("Email không hợp lệ hoặc mật khẩu quá ngắn (tối thiểu 6 ký tự).");
}

// ---------- Dashboard ----------
export async function fetchDashboard() {
  if (!USE_MOCK) {
    const res = await fetch("/api/dashboard");
    return res.json();
  }
  return delay({
    kpis: M.dashboardKpis,
    salesTrend: M.salesTrend,
    unitStatus: M.unitStatusBreakdown,
    recentDeals: M.dealsMock.slice(0, 5),
    featuredProject: M.projectsMock[3],
  });
}

// ---------- Projects ----------
export async function fetchProjects(search = ""): Promise<Project[]> {
  if (!USE_MOCK) {
    const res = await fetch(`/api/projects?search=${encodeURIComponent(search)}`);
    return res.json();
  }
  const q = search.trim().toLowerCase();
  const list = q
    ? M.projectsMock.filter((p) => p.name.toLowerCase().includes(q) || p.location.toLowerCase().includes(q))
    : M.projectsMock;
  return delay(list);
}
export async function fetchProjectById(id: string): Promise<Project | undefined> {
  if (!USE_MOCK) return (await fetch(`/api/projects/${id}`)).json();
  return delay(M.projectsMock.find((p) => p.id === id));
}
export async function fetchProjectKpis() {
  return delay(M.projectKpis);
}

// ---------- Areas ----------
export async function fetchAreas(projectId: string): Promise<Area[]> {
  if (!USE_MOCK) return (await fetch(`/api/projects/${projectId}/areas`)).json();
  return delay(M.areasMock.filter((a) => a.projectId === projectId));
}

// ---------- Units ----------
export interface UnitQuery { status?: string; type?: string; tower?: string; search?: string; }
export async function fetchUnits(q: UnitQuery = {}): Promise<Unit[]> {
  if (!USE_MOCK) {
    const p = new URLSearchParams(q as Record<string, string>);
    return (await fetch(`/api/units?${p}`)).json();
  }
  let list = M.unitsMock;
  if (q.status && q.status !== "all") list = list.filter((u) => u.status === q.status);
  if (q.tower && q.tower !== "all") list = list.filter((u) => u.tower === q.tower);
  if (q.search) {
    const s = q.search.toLowerCase();
    list = list.filter((u) => u.code.toLowerCase().includes(s) || u.type.toLowerCase().includes(s));
  }
  return delay(list);
}
export async function createUnit(u: Partial<Unit>): Promise<Unit> {
  if (!USE_MOCK) return (await fetch(`/api/units`, { method: "POST", body: JSON.stringify(u) })).json();
  return delay({ ...(u as Unit), id: "unit_new_" + Date.now() });
}
export async function updateUnit(id: string, u: Partial<Unit>): Promise<Unit> {
  if (!USE_MOCK) return (await fetch(`/api/units/${id}`, { method: "PUT", body: JSON.stringify(u) })).json();
  return delay({ ...(M.unitsMock.find((x) => x.id === id) as Unit), ...u });
}
export async function deleteUnit(id: string): Promise<void> {
  if (!USE_MOCK) { await fetch(`/api/units/${id}`, { method: "DELETE" }); return; }
  return delay(undefined);
}

// ---------- Deals ----------
export async function fetchDeals(): Promise<Deal[]> {
  if (!USE_MOCK) return (await fetch(`/api/deals`)).json();
  return delay(M.dealsMock);
}
export async function fetchDealById(id: string): Promise<DealDetail> {
  if (!USE_MOCK) return (await fetch(`/api/deals/${id}`)).json();
  return delay({ ...M.dealDetailMock, id });
}
export async function fetchDealKpis() { return delay(M.dealPipelineKpis); }

// ---------- Sales Team ----------
export interface StaffQuery { role?: string; project?: string; status?: string; search?: string; }
export async function fetchSalesTeam(q: StaffQuery = {}): Promise<SalesStaff[]> {
  if (!USE_MOCK) {
    const p = new URLSearchParams(q as Record<string, string>);
    return (await fetch(`/api/sales-team?${p}`)).json();
  }
  let list = M.salesTeamMock;
  if (q.status && q.status !== "all") list = list.filter((s) => s.status === q.status);
  if (q.search) {
    const s = q.search.toLowerCase();
    list = list.filter((x) => x.name.toLowerCase().includes(s) || x.role.toLowerCase().includes(s) || x.assignedProject.toLowerCase().includes(s));
  }
  return delay(list);
}
export async function fetchSalesTeamKpis() { return delay(M.salesTeamKpis); }
