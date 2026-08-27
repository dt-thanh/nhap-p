// ============================================================
// SERVICE LAYER — connects CRM frontend to MiniCRM backend.
// USE_MOCK = false: all data flows through real API.
// ============================================================
import type {
  Project, Area, Unit, Deal, DealDetail, SalesStaff, Kpi,
} from "../types";
import { apiGet, apiPost, apiPatch, apiDelete } from "./api";
import {
  beProjectToFe, beAreaToFe, beUnitToFe, beDealToFe, beDealToDetail,
  type BEProject, type BEProjectWriteOut,
  type BEArea, type BEAreaWriteOut,
  type BEUnit, type BEUnitWriteOut,
  type BEDeal, type BEDealWriteOut,
} from "./adapters";

// ---------- Auth ----------
// CP4: `login()` ĐÃ BỊ GỠ khỏi tầng service.
//
// Bản cũ gọi `POST /auth/login` với email+password, và khi endpoint đó lỗi thì
// RƠI VỀ một nhánh "demo": gọi `/health`, thấy `status === "ok"` là trả về
// `token: "demo-token-" + Date.now()` với `role: "admin"`. Nghĩa là bất kỳ ai
// mở được trang, gõ một email bất kỳ, đều trở thành admin ngay khi backend
// chưa cấu hình xác thực — đúng tình huống mà một môi trường mới dựng luôn rơi
// vào. Nhánh đó không được "sửa cho an toàn hơn"; nó bị XOÁ.
//
// Đăng nhập bây giờ là một lần điều hướng trình duyệt sang Keycloak:
//   `services/api.ts::startLogin()` → `GET /auth/login` → 302 tới Keycloak.
// Xem `context/AuthContext.tsx`.

// ---------- Dashboard ----------
export async function fetchDashboard() {
  // Aggregate from real data
  try {
    const [projects, units, deals] = await Promise.all([
      fetchProjects(),
      fetchUnits(),
      fetchDeals(),
    ]);
    const totalUnits = units.length;
    const soldUnits = units.filter((u) => u.status === "sold").length;
    const openDeals = deals.filter((d) => d.stage !== "won" && d.stage !== "lost").length;

    const kpis: Kpi[] = [
      { label: "Tổng sản phẩm", value: String(totalUnits) },
      { label: "Đã bán", value: String(soldUnits) },
      { label: "Giao dịch đang mở", value: String(openDeals) },
      { label: "Dự án", value: String(projects.length) },
    ];
    return {
      kpis,
      salesTrend: [],
      unitStatus: [
        { name: "Còn trống", value: units.filter((u) => u.status === "available").length, pct: 0, color: "#17976E" },
        { name: "Đã đặt chỗ", value: units.filter((u) => u.status === "reserved").length, pct: 0, color: "#C6982F" },
        { name: "Đã bán", value: soldUnits, pct: 0, color: "#D8DCE3" },
      ],
      recentDeals: deals.slice(0, 5),
      featuredProject: projects[0] ?? null,
    };
  } catch {
    return {
      kpis: [] as Kpi[],
      salesTrend: [],
      unitStatus: [],
      recentDeals: [],
      featuredProject: null,
    };
  }
}

// ---------- Projects ----------
export async function fetchProjects(search = "", includeArchived = false): Promise<Project[]> {
  const qs = includeArchived ? "?include_archived=true" : "";
  const beProjects = await apiGet<BEProject[]>(`/projects${qs}`);
  const beAreas = await apiGet<BEArea[]>("/areas?include_archived=true");
  let list = beProjects.map((p) => beProjectToFe(p, beAreas));
  if (search.trim()) {
    const q = search.toLowerCase();
    list = list.filter(
      (p) => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q),
    );
  }
  return list;
}

export async function fetchProjectById(id: string): Promise<Project | undefined> {
  try {
    const be = await apiGet<BEProject>(`/projects/${id}`);
    const beAreas = await apiGet<BEArea[]>(`/areas?external_project_id=${id}`);
    return beProjectToFe(be, beAreas);
  } catch {
    return undefined;
  }
}

export async function fetchProjectKpis(): Promise<Kpi[]> {
  try {
    const projects = await apiGet<BEProject[]>("/projects?include_archived=true");
    const areas = await apiGet<BEArea[]>("/areas?include_archived=true");
    const units = await apiGet<BEUnit[]>("/units");
    const deals = await apiGet<BEDeal[]>("/deals");
    return [
      { label: "Tổng dự án", value: String(projects.length) },
      { label: "Phân khu", value: String(areas.length) },
      { label: "Tổng sản phẩm", value: String(units.length) },
      { label: "Giao dịch đang mở", value: String(deals.length) },
    ];
  } catch {
    return [];
  }
}

export interface ProjectCreateData {
  name: string;
  launch_date: string;
  location?: string | null;
}
export async function createProject(data: ProjectCreateData): Promise<Project> {
  const res = await apiPost<BEProjectWriteOut>("/projects", data);
  return beProjectToFe(res.record);
}

export async function updateProject(externalId: string, patch: Partial<ProjectCreateData>): Promise<Project> {
  const res = await apiPatch<BEProjectWriteOut>(`/projects/${externalId}`, patch);
  return beProjectToFe(res.record);
}

export async function deleteProject(externalId: string): Promise<void> {
  await apiDelete(`/projects/${externalId}`);
}

// ---------- Areas ----------
export async function fetchAreas(projectId: string, includeArchived = false): Promise<Area[]> {
  const archivedQs = includeArchived ? "&include_archived=true" : "";
  const beAreas = await apiGet<BEArea[]>(
    `/areas?external_project_id=${projectId}${archivedQs}`,
  );
  return beAreas.map(beAreaToFe);
}

export interface AreaCreateData {
  external_project_id: string;
  area_name: string;
  unit_type: string;
  bedrooms: number;
  area_sqm: number;
  total_units: number;
}
export async function createArea(data: AreaCreateData): Promise<Area> {
  const res = await apiPost<BEAreaWriteOut>("/areas", data);
  return beAreaToFe(res.record);
}

export async function updateArea(
  externalId: string,
  patch: Partial<Omit<AreaCreateData, "external_project_id">>,
): Promise<Area> {
  const res = await apiPatch<BEAreaWriteOut>(`/areas/${externalId}`, patch);
  return beAreaToFe(res.record);
}

export async function deleteArea(externalId: string): Promise<void> {
  await apiDelete(`/areas/${externalId}`);
}

// ---------- Units ----------
export interface UnitQuery {
  status?: string;
  type?: string;
  tower?: string;
  search?: string;
}
export async function fetchUnits(q: UnitQuery = {}): Promise<Unit[]> {
  const beUnits = await apiGet<BEUnit[]>("/units");
  let list = beUnits.map(beUnitToFe);
  if (q.status && q.status !== "all")
    list = list.filter((u) => u.status === q.status);
  if (q.search) {
    const s = q.search.toLowerCase();
    list = list.filter(
      (u) => u.code.toLowerCase().includes(s) || u.type.toLowerCase().includes(s),
    );
  }
  return list;
}

export interface UnitCreateData {
  external_area_id?: string;
  area_name?: string;
  unit_type?: string;
  unit_code: string;
  unit_status?: string;
}
export async function createUnit(data: UnitCreateData): Promise<Unit> {
  const res = await apiPost<BEUnitWriteOut>("/units", data);
  return beUnitToFe(res.record);
}

export async function updateUnit(externalId: string, patch: Record<string, unknown>): Promise<Unit> {
  const res = await apiPatch<BEUnitWriteOut>(`/units/${externalId}`, patch);
  return beUnitToFe(res.record);
}

export async function deleteUnit(externalId: string): Promise<void> {
  await apiDelete(`/units/${externalId}`);
}

// ---------- Deals ----------
export async function fetchDeals(): Promise<Deal[]> {
  const beDeals = await apiGet<BEDeal[]>("/deals");
  return beDeals.map(beDealToFe);
}

export async function fetchDealById(id: string): Promise<DealDetail> {
  const be = await apiGet<BEDeal>(`/deals/${id}`);
  return beDealToDetail(be);
}

export async function fetchDealKpis(): Promise<Kpi[]> {
  try {
    const deals = await apiGet<BEDeal[]>("/deals");
    const active = deals.filter((d) => d.deal_status !== "lost" && !d.deleted_at);
    const won = deals.filter((d) => d.deal_status === "sold");
    return [
      { label: "GD đang mở", value: String(active.length) },
      { label: "Đã chốt", value: String(won.length) },
      { label: "Tổng GD", value: String(deals.length) },
    ];
  } catch {
    return [];
  }
}

export interface DealCreateData {
  external_unit_id: string;
  deal_status: string;
  reserved_at?: string | null;
  sold_at?: string | null;
  lost_at?: string | null;
}
export async function createDeal(data: DealCreateData): Promise<Deal> {
  const res = await apiPost<BEDealWriteOut>("/deals", data);
  return beDealToFe(res.record);
}

export async function updateDeal(externalId: string, patch: Partial<DealCreateData>): Promise<Deal> {
  const res = await apiPatch<BEDealWriteOut>(`/deals/${externalId}`, patch);
  return beDealToFe(res.record);
}

export async function deleteDeal(externalId: string): Promise<void> {
  await apiDelete(`/deals/${externalId}`);
}

// ---------- Sales Team (not in backend - return empty) ----------
export interface StaffQuery {
  role?: string;
  project?: string;
  status?: string;
  search?: string;
}
export async function fetchSalesTeam(_q: StaffQuery = {}): Promise<SalesStaff[]> {
  return [];
}
export async function fetchSalesTeamKpis(): Promise<Kpi[]> {
  return [];
}

// Re-export adapters for direct use
export { dealStatusToStage, stageToDealStatus } from "./adapters";
