// ============================================================
// ADAPTER LAYER — maps between MiniCRM backend schemas and
// frontend display types. Frontend types have rich UI fields
// (thumbnails, managers, etc.) that the backend doesn't own.
// This adapter provides sensible defaults for those fields.
// ============================================================

import type {
  Project,
  Area,
  Unit,
  SyncState,
  Deal,
  DealDetail,
  DealStage,
  UnitStatus,
} from "../types";

// ---- Backend response types (mirrors minicrm/app/schemas.py) ----

export interface BEProject {
  external_id: string;
  name: string;
  location?: string | null;
  launch_date: string;
  status: "active" | "archived";
  source_revision: number;
  created_at: string;
  updated_at: string;
  mirrored_at?: string | null;
  mirrored_revision?: number | null;
  last_sync_batch_id?: string | null;
}

export interface BEProjectWriteOut {
  record: BEProject;
}

export interface BEArea {
  external_id: string;
  external_project_id: string;
  area_name: string;
  unit_type: string;
  bedrooms: number;
  area_sqm: number;
  total_units: number;
  status: "active" | "archived";
  source_revision: number;
  created_at: string;
  updated_at: string;
  mirrored_at?: string | null;
  mirrored_revision?: number | null;
  last_sync_batch_id?: string | null;
}

export interface BEAreaWriteOut {
  record: BEArea;
}

export interface BEUnit {
  external_id: string;
  area_name: string;
  unit_type: string;
  unit_code: string;
  unit_status: "available" | "reserved" | "sold" | "blocked";
  source_revision: number;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
  mirrored_at?: string | null;
  mirrored_revision?: number | null;
  last_sync_batch_id?: string | null;
}

export interface BEUnitWriteOut {
  record: BEUnit;
  sync: unknown;
}

export interface BEDeal {
  external_id: string;
  external_unit_id: string;
  deal_status: string;
  reserved_at?: string | null;
  sold_at?: string | null;
  lost_at?: string | null;
  source_revision: number;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
  mirrored_at?: string | null;
  mirrored_revision?: number | null;
  last_sync_batch_id?: string | null;
}

export interface BEDealWriteOut {
  record: BEDeal;
  sync: unknown;
}

export interface BELoginOut {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: {
    id: string;
    login: string;
    email: string | null;
    status: string;
    role: string;
    created_at: string;
  };
}

// ---- Status mappings ----

const DEAL_STATUS_TO_STAGE: Record<string, DealStage> = {
  lead: "new",
  qualified: "qualified",
  interested: "contacted",
  viewing: "viewing",
  reserved: "booking",
  sold: "won",
  lost: "lost",
};

const STAGE_TO_DEAL_STATUS: Record<DealStage, string> = {
  new: "lead",
  contacted: "interested",
  qualified: "qualified",
  viewing: "viewing",
  booking: "reserved",
  won: "sold",
  lost: "lost",
};

export function dealStatusToStage(status: string): DealStage {
  return DEAL_STATUS_TO_STAGE[status] ?? "new";
}

export function stageToDealStatus(stage: DealStage): string {
  return STAGE_TO_DEAL_STATUS[stage] ?? "lead";
}

// ---- Project adapter ----

export function beProjectToFe(be: BEProject, areas?: BEArea[]): Project {
  const projectAreas = areas?.filter(
    (a) => a.external_project_id === be.external_id,
  );
  const totalUnits = projectAreas?.reduce((s, a) => s + a.total_units, 0) ?? 0;

  return {
    id: be.external_id,
    name: be.name,
    tagline: `Ngày mở bán: ${be.launch_date}`,
    location: be.location ?? "—",
    thumbnailUrl: "",
    areas: projectAreas?.length ?? 0,
    totalUnits,
    soldUnits: 0,
    activeDeals: 0,
    status: be.status === "active" ? "active" : "planning",
    manager: { name: "—", title: "—" },
  };
}

// ---- Area adapter ----

export function beAreaToFe(be: BEArea): Area {
  return {
    id: be.external_id,
    projectId: be.external_project_id,
    name: be.area_name,
    type: be.unit_type,
    totalUnits: be.total_units,
    available: be.total_units, // will be enriched when unit counts available
    reserved: 0,
    sold: 0,
    activeDeals: 0,
    salesVelocity: 0,
    absorption: 0,
    status: "on_track",
    bedrooms: be.bedrooms,
    area_sqm: be.area_sqm,
  };
}

/** Suy trạng thái mirror từ ba trường backend trả về.
 *
 *  `mirrored_revision === source_revision` là ĐIỀU KIỆN DUY NHẤT để coi là đã
 *  đồng bộ — không phải "có `mirrored_at` là xong". Một bản ghi đã sync ở
 *  revision 2 rồi được sửa thành revision 3 vẫn còn `mirrored_at` cũ; nó đang
 *  PENDING, và hiển thị nó là "đã đồng bộ" sẽ nói dối đúng vào lúc quan trọng.
 *
 *  Chưa từng mirror (`null`) = `pending`, KHÔNG phải `failed`: đường relay chạy
 *  mỗi 5 giây, nên trạng thái bình thường ngay sau khi tạo là "đang chờ". Ta
 *  không có tín hiệu nào ở đây để khẳng định một lần đẩy đã hỏng hẳn — muốn
 *  biết điều đó phải hỏi `/outbox`, và đó là mặt vận hành, không phải mặt này. */
function toSyncState(be: {
  source_revision: number;
  mirrored_revision?: number | null;
  mirrored_at?: string | null;
  last_sync_batch_id?: string | null;
}): SyncState {
  const mirrored = be.mirrored_revision ?? null;
  return {
    status: mirrored !== null && mirrored >= be.source_revision ? "synced" : "pending",
    sourceRevision: be.source_revision,
    mirroredRevision: mirrored,
    mirroredAt: be.mirrored_at ?? null,
    lastSyncBatchId: be.last_sync_batch_id ?? null,
  };
}

// ---- Unit adapter ----

export function beUnitToFe(be: BEUnit): Unit {
  const unitStatus: UnitStatus =
    be.unit_status === "blocked" ? "reserved" : be.unit_status;
  return {
    id: be.external_id,
    code: be.unit_code,
    projectId: "",
    areaId: "",
    tower: be.area_name,
    floor: 0,
    type: be.unit_type,
    sizeSqft: 0,
    price: 0,
    status: unitStatus,
    sync: toSyncState(be),
  };
}

// ---- Deal adapter ----

export function beDealToFe(be: BEDeal): Deal {
  return {
    id: be.external_id,
    unitCode: be.external_unit_id,
    projectName: "—",
    buyerName: "—",
    value: 0,
    stage: dealStatusToStage(be.deal_status),
    assignedTo: { name: "—" },
    closeDate: be.sold_at ?? be.reserved_at ?? "",
    createdAt: be.created_at,
  };
}

export function beDealToDetail(be: BEDeal): DealDetail {
  const base = beDealToFe(be);
  return {
    ...base,
    customer: {
      name: "—",
      phone: "—",
      email: "—",
      address: "—",
      type: "—",
      source: "—",
      firstContact: be.created_at,
    },
    property: {
      project: "—",
      area: "—",
      unit: be.external_unit_id,
      unitStatus: "available",
      type: "—",
      price: 0,
    },
    financial: {
      unitPrice: 0,
      discount: 0,
      netPrice: 0,
      bookingFee: 0,
      paymentPlan: "—",
    },
    activities: [],
  };
}
