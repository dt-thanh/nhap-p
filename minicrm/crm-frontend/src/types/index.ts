// ============================================================
// HỢP ĐỒNG DỮ LIỆU (data contract) giữa Frontend và Backend/AI.
// Backend team dựa vào đây để trả JSON đúng format.
// ============================================================

// ---------- Auth ----------
export interface User {
  id: string;
  name: string;
  email: string;
  role: string; // vd "Sales Director" — BE quyết phân quyền, FE chỉ hiển thị
  avatarUrl?: string;
}
export interface LoginResponse {
  token: string;
  user: User;
}

// ---------- Chung ----------
export interface Kpi {
  label: string;
  value: string;
  delta?: number; // % thay đổi; dương = tăng
  deltaNote?: string;
  icon?: string;
}

// ---------- Project (Dự án) ----------
export type ProjectStatus = "active" | "pre_launch" | "planning";
export interface Project {
  id: string;
  name: string;
  tagline: string;
  location: string;
  thumbnailUrl: string;
  areas: number; // số phân khu
  totalUnits: number;
  soldUnits: number;
  activeDeals: number;
  status: ProjectStatus;
  manager: { name: string; title: string; avatarUrl?: string };
}

// ---------- Area / Phân khu ----------
export type AreaStatus = "on_track" | "behind";
export interface Area {
  id: string;
  projectId: string;
  name: string;
  type: string; // "Low Rise", "High Rise"...
  totalUnits: number;
  available: number;
  reserved: number;
  sold: number;
  activeDeals: number;
  salesVelocity: number; // units/day
  absorption: number; // %
  status: AreaStatus;
  thumbnailUrl?: string;
}

// ---------- Unit (Sản phẩm / Căn) ----------
export type UnitStatus = "available" | "reserved" | "sold";
export interface Unit {
  id: string;
  code: string; // "P2-1805"
  projectId: string;
  areaId: string;
  tower: string;
  floor: number;
  type: string; // "3 Bedroom"
  sizeSqft: number;
  price: number; // VND
  facing?: string;
  status: UnitStatus;
  imageUrl?: string;
  notes?: string;
}

// ---------- Deal (Giao dịch) ----------
export type DealStage =
  | "new" | "contacted" | "qualified" | "viewing" | "booking" | "won" | "lost";
export interface Deal {
  id: string; // "DEAL-2024-0587"
  unitCode: string;
  projectName: string;
  areaName?: string;
  buyerName: string;
  value: number; // VND
  stage: DealStage;
  assignedTo: { name: string; avatarUrl?: string };
  closeDate: string;
  createdAt: string;
}
export interface DealActivity {
  id: string;
  type: "call_out" | "call_in" | "viewing" | "booking" | "reminder" | "note";
  title: string;
  description: string;
  by: string;
  at: string;
  tag?: string;
}
export interface DealDetail extends Deal {
  customer: {
    name: string; phone: string; email: string; address: string;
    type: string; source: string; firstContact: string;
  };
  property: { project: string; area: string; unit: string; unitStatus: UnitStatus; type: string; price: number };
  financial: { unitPrice: number; discount: number; netPrice: number; bookingFee: number; paymentPlan: string };
  activities: DealActivity[];
}

// ---------- Sales Team ----------
export type StaffStatus = "active" | "on_leave" | "inactive";
export interface SalesStaff {
  id: string;
  name: string;
  email: string;
  role: string; // "Senior Broker"...
  assignedProject: string;
  activeDeals: number;
  wonDeals: number;
  revenueYTD: number;
  conversionRate: number; // %
  status: StaffStatus;
  avatarUrl?: string;
  phone?: string;
}
