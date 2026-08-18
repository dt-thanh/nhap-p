import type {
  User, Project, Area, Unit, Deal, DealDetail, SalesStaff, Kpi,
} from "../types";

const img = (id: string) =>
  `https://images.unsplash.com/${id}?auto=format&fit=crop&w=600&q=70`;
const av = (n: number) => `https://i.pravatar.cc/120?img=${n}`;

// ---------- User đăng nhập ----------
export const currentUser: User = {
  id: "u_001",
  name: "Michael Chen",
  email: "admin@absorptioncrm.com",
  role: "Giám đốc Kinh doanh",
  avatarUrl: av(12),
};

// ---------- Dashboard KPIs ----------
export const dashboardKpis: Kpi[] = [
  { label: "Tổng sản phẩm", value: "1.248", deltaNote: "so với kỳ trước" },
  { label: "Đã bán", value: "428", delta: 18.6, deltaNote: "so với kỳ trước" },
  { label: "Giao dịch đang mở", value: "156", delta: 12.3, deltaNote: "so với kỳ trước" },
  { label: "Nhân viên sales", value: "24", deltaNote: "so với kỳ trước" },
];

export const salesTrend = Array.from({ length: 31 }, (_, i) => {
  const base = 30 + Math.sin(i / 2) * 20 + i * 1.3;
  return {
    day: `${i + 1}/5`,
    current: Math.max(10, Math.round(base + (Math.random() * 16 - 8))),
    previous: Math.max(8, Math.round(base - 12 + (Math.random() * 14 - 7))),
  };
});

export const unitStatusBreakdown = [
  { name: "Còn trống", value: 612, pct: 49, color: "#17976E" },
  { name: "Đã đặt chỗ", value: 236, pct: 19, color: "#C6982F" },
  { name: "Đã bán", value: 400, pct: 32, color: "#D8DCE3" },
];

// ---------- Projects ----------
export const projectsMock: Project[] = [
  { id: "p_ocean2", name: "Ocean Park 2", tagline: "Luxury Waterfront Living", location: "TP. Thủ Đức", thumbnailUrl: img("photo-1545324418-cc1a3fa10c00"), areas: 6, totalUnits: 1248, soldUnits: 612, activeDeals: 24, status: "active", manager: { name: "Michael Chen", title: "Giám đốc Kinh doanh", avatarUrl: av(12) } },
  { id: "p_smart", name: "Smart City", tagline: "Khu đô thị tích hợp", location: "Hà Nội", thumbnailUrl: img("photo-1486406146926-c627a92ad1ab"), areas: 8, totalUnits: 2156, soldUnits: 1128, activeDeals: 32, status: "active", manager: { name: "Sarah Lim", title: "Quản lý Dự án", avatarUrl: av(45) } },
  { id: "p_grand", name: "Grand Park", tagline: "Sống giữa thiên nhiên", location: "TP. Thủ Đức", thumbnailUrl: img("photo-1512917774080-9991f1c4c750"), areas: 5, totalUnits: 892, soldUnits: 356, activeDeals: 18, status: "active", manager: { name: "Daniel Wong", title: "Quản lý Dự án", avatarUrl: av(33) } },
  { id: "p_marina", name: "Marina Vista Residences", tagline: "Waterfront Luxury Living", location: "Đà Nẵng", thumbnailUrl: img("photo-1502672260266-1c1ef2d93688"), areas: 4, totalUnits: 628, soldUnits: 236, activeDeals: 12, status: "pre_launch", manager: { name: "Emily Tan", title: "Quản lý Sales", avatarUrl: av(48) } },
  { id: "p_reserve", name: "The Reserve", tagline: "Exclusive Garden Homes", location: "Nha Trang", thumbnailUrl: img("photo-1600585154340-be6161a56a0c"), areas: 3, totalUnits: 180, soldUnits: 32, activeDeals: 6, status: "planning", manager: { name: "Kevin Ng", title: "Giám đốc Dự án", avatarUrl: av(52) } },
];

export const projectKpis: Kpi[] = [
  { label: "Tổng dự án", value: "16", deltaNote: "so với kỳ trước" },
  { label: "Địa điểm", value: "8", deltaNote: "so với kỳ trước" },
  { label: "Phân khu", value: "48", delta: 9.1, deltaNote: "so với kỳ trước" },
  { label: "Tổng sản phẩm", value: "12.842", delta: 14.3, deltaNote: "so với kỳ trước" },
  { label: "Giao dịch đang mở", value: "156", delta: 12.3, deltaNote: "so với kỳ trước" },
];

// ---------- Areas (phân khu của Ocean Park 2 / The Paris) ----------
export const areasMock: Area[] = [
  { id: "a_jardin", projectId: "p_ocean2", name: "Le Jardin", type: "Thấp tầng", totalUnits: 72, available: 28, reserved: 18, sold: 26, activeDeals: 6, salesVelocity: 1.1, absorption: 3.2, status: "on_track", thumbnailUrl: img("photo-1600607687939-ce8a6c25118c") },
  { id: "a_seine", projectId: "p_ocean2", name: "Le Seine", type: "Thấp tầng", totalUnits: 68, available: 30, reserved: 14, sold: 24, activeDeals: 5, salesVelocity: 0.9, absorption: 2.8, status: "on_track", thumbnailUrl: img("photo-1600566753086-00f18fb6b3ea") },
  { id: "a_louvre", projectId: "p_ocean2", name: "Le Louvre", type: "Thấp tầng", totalUnits: 78, available: 32, reserved: 18, sold: 28, activeDeals: 7, salesVelocity: 1.0, absorption: 3.0, status: "on_track", thumbnailUrl: img("photo-1600585154526-990dced4db0d") },
  { id: "a_montmartre", projectId: "p_ocean2", name: "Le Montmartre", type: "Thấp tầng", totalUnits: 68, available: 22, reserved: 18, sold: 28, activeDeals: 6, salesVelocity: 0.5, absorption: 2.2, status: "behind", thumbnailUrl: img("photo-1580587771525-78b9dba3b914") },
];

// ---------- Units ----------
const unitTypes = ["1 Phòng ngủ", "2 Phòng ngủ", "3 Phòng ngủ", "2 Phòng ngủ Premium", "3 Phòng ngủ Premium"];
const statuses: Unit["status"][] = ["available", "reserved", "sold"];
export const unitsMock: Unit[] = Array.from({ length: 48 }, (_, i) => {
  const tower = i % 2 === 0 ? "Paris 2" : "Paris 1";
  const floor = 18 - Math.floor(i / 3);
  const t = i % unitTypes.length;
  return {
    id: `unit_${i}`,
    code: `${tower === "Paris 2" ? "P2" : "P1"}-${floor}${String((i % 8) + 1).padStart(2, "0")}`,
    projectId: "p_ocean2", areaId: "a_jardin",
    tower, floor, type: unitTypes[t],
    sizeSqft: 650 + t * 130 + (i % 5) * 20,
    price: (1.4 + t * 0.28 + (i % 4) * 0.05) * 1_000_000_000,
    facing: ["Hướng thành phố (Đông Nam)", "Hướng sông", "Hướng công viên"][i % 3],
    status: statuses[i % 3],
    imageUrl: img("photo-1522708323590-d24dbb6b0267"),
    notes: "Căn tầng cao, view thành phố không bị che. Phòng khách và bếp rộng rãi. Gần sảnh thang máy.",
  };
});

// ---------- Deals (Kanban) ----------
const buyers = ["Nguyễn Văn An","Trần Thị Bình","Lê Hoàng Cường","Phạm Gia Dũng","Đỗ Quỳnh Em","Vũ Minh Phúc","Bùi Ngọc Giang","Hoàng Việt Hải","Đặng Thu Hà","Ngô Bảo Khánh","Lý Tuấn Long","Mai Phương Mai"];
const salesPeople = [
  { name: "James Tan", avatarUrl: av(12) }, { name: "Sarah Lim", avatarUrl: av(45) },
  { name: "Daniel Wong", avatarUrl: av(33) }, { name: "Kevin Ng", avatarUrl: av(52) },
  { name: "Michelle Yeoh", avatarUrl: av(48) },
];
const stages: Deal["stage"][] = ["new","contacted","qualified","viewing","booking","won"];
export const dealsMock: Deal[] = [];
let dcount = 587;
stages.forEach((stage) => {
  const perCol = stage === "won" ? 3 : stage === "booking" ? 3 : 4;
  for (let j = 0; j < perCol; j++) {
    const b = buyers[(dcount + j) % buyers.length];
    dealsMock.push({
      id: `DEAL-2024-0${dcount--}`,
      unitCode: `${["A","B","C"][j % 3]}-${1000 + Math.floor(Math.random() * 800)}`,
      projectName: ["Marina Vista Residences","Harbour Suites","Ocean Park 2"][j % 3],
      areaName: "Tower A",
      buyerName: b,
      value: (1.1 + Math.random() * 1.4) * 1_000_000_000,
      stage,
      assignedTo: salesPeople[j % salesPeople.length],
      closeDate: "2024-06-10",
      createdAt: "2024-05-28",
    });
  }
});

export const dealPipelineKpis: Kpi[] = [
  { label: "Giao dịch đang mở", value: "156", delta: 12.3, deltaNote: "so với kỳ trước" },
  { label: "Giao dịch đặt chỗ", value: "28", delta: 7.6, deltaNote: "so với kỳ trước" },
  { label: "Chốt trong tháng", value: "14", delta: 27.1, deltaNote: "so với kỳ trước" },
  { label: "Giá trị pipeline", value: "28,64 tỷ", delta: 18.4, deltaNote: "so với kỳ trước" },
];

// ---------- Deal Detail ----------
export const dealDetailMock: DealDetail = {
  id: "DEAL-2024-0587", unitCode: "A-1203", projectName: "Marina Vista Residences",
  areaName: "Tower A", buyerName: "Nguyễn Văn A", value: 5_820_000_000, stage: "viewing",
  assignedTo: { name: "James Tan", avatarUrl: av(12) }, closeDate: "2024-06-10", createdAt: "2024-05-28",
  customer: { name: "Nguyễn Văn A", phone: "+84 912 345 678", email: "nguyenvana@gmail.com", address: "TP. Hồ Chí Minh", type: "Cá nhân", source: "Website", firstContact: "2024-05-28" },
  property: { project: "Marina Vista Residences", area: "Tower A", unit: "A-1203", unitStatus: "available", type: "2 Phòng ngủ", price: 5_820_000_000 },
  financial: { unitPrice: 5_820_000_000, discount: -120_000_000, netPrice: 5_700_000_000, bookingFee: 200_000_000, paymentPlan: "Chuẩn 30% - 70%" },
  activities: [
    { id: "ac1", type: "call_out", title: "Gọi ra", description: "Trao đổi tình trạng căn và hẹn xem nhà ngày 31/5/2024 lúc 10:00.", by: "James Tan", at: "Hôm nay, 11:30", tag: "Ghi chú cuộc gọi" },
    { id: "ac2", type: "viewing", title: "Đã lên lịch xem nhà", description: "Xem căn A-1203 vào 31/5/2024 lúc 10:00.", by: "James Tan", at: "Hôm nay, 10:15" },
    { id: "ac3", type: "booking", title: "Đã thanh toán đặt chỗ", description: "Đã nhận phí đặt chỗ 200.000.000 ₫. Số biên nhận: RCPT-2024-1342.", by: "James Tan", at: "30/5/2024, 14:45", tag: "Cập nhật đặt chỗ" },
    { id: "ac4", type: "reminder", title: "Nhắc theo dõi", description: "Nhắc theo dõi sau lần liên hệ đầu tiên.", by: "Hệ thống", at: "29/5/2024, 09:00" },
    { id: "ac5", type: "call_in", title: "Gọi vào", description: "Khách hỏi về các căn 2 phòng ngủ và giá.", by: "James Tan", at: "28/5/2024, 16:20", tag: "Ghi chú cuộc gọi" },
  ],
};

// ---------- Sales Team ----------
export const salesTeamKpis: Kpi[] = [
  { label: "Tổng nhân viên sales", value: "24", deltaNote: "so với kỳ trước" },
  { label: "Giao dịch đang mở", value: "156", delta: 12.3, deltaNote: "so với kỳ trước" },
  { label: "Chốt trong tháng", value: "28", delta: 27.6, deltaNote: "so với kỳ trước" },
  { label: "Tỷ lệ chuyển đổi", value: "17,9%", delta: 2.4, deltaNote: "so với kỳ trước" },
];

export const salesTeamMock: SalesStaff[] = [
  { id: "s1", name: "James Tan", email: "james.tan@absorptioncrm.com", role: "Senior Broker", assignedProject: "Marina Vista Residences", activeDeals: 12, wonDeals: 8, revenueYTD: 2_180_000_000, conversionRate: 18.6, status: "active", avatarUrl: av(12), phone: "+84 901 234 567" },
  { id: "s2", name: "Sarah Lim", email: "sarah.lim@absorptioncrm.com", role: "Broker", assignedProject: "Marina Vista Residences", activeDeals: 9, wonDeals: 6, revenueYTD: 1_560_000_000, conversionRate: 15.2, status: "active", avatarUrl: av(45), phone: "+84 902 000 111" },
  { id: "s3", name: "Daniel Wong", email: "daniel.wong@absorptioncrm.com", role: "Senior Broker", assignedProject: "Marina Vista Residences", activeDeals: 15, wonDeals: 10, revenueYTD: 2_350_000_000, conversionRate: 19.1, status: "active", avatarUrl: av(33), phone: "+84 903 222 333" },
  { id: "s4", name: "Michelle Yeoh", email: "michelle.yeoh@absorptioncrm.com", role: "Junior Broker", assignedProject: "Harbour Suites", activeDeals: 7, wonDeals: 4, revenueYTD: 1_125_000_000, conversionRate: 12.4, status: "active", avatarUrl: av(48), phone: "+84 904 444 555" },
  { id: "s5", name: "Kevin Ng", email: "kevin.ng@absorptioncrm.com", role: "Broker", assignedProject: "Marina Vista Residences", activeDeals: 11, wonDeals: 5, revenueYTD: 1_730_000_000, conversionRate: 14.8, status: "active", avatarUrl: av(52), phone: "+84 905 666 777" },
  { id: "s6", name: "Amanda Koh", email: "amanda.koh@absorptioncrm.com", role: "Associate", assignedProject: "The Orchard Collection", activeDeals: 6, wonDeals: 3, revenueYTD: 980_000_000, conversionRate: 10.2, status: "on_leave", avatarUrl: av(20), phone: "+84 906 888 999" },
  { id: "s7", name: "Jason Lee", email: "jason.lee@absorptioncrm.com", role: "Junior Broker", assignedProject: "Harbour Suites", activeDeals: 5, wonDeals: 2, revenueYTD: 720_000_000, conversionRate: 9.1, status: "active", avatarUrl: av(15), phone: "+84 907 111 222" },
  { id: "s8", name: "Rebecca Chua", email: "rebecca.chua@absorptioncrm.com", role: "Associate", assignedProject: "Marina Vista Residences", activeDeals: 4, wonDeals: 1, revenueYTD: 480_000_000, conversionRate: 7.5, status: "active", avatarUrl: av(9), phone: "+84 908 333 444" },
];

export const staffPerformance = [
  { month: "T1", revenue: 900, conversion: 18 },
  { month: "T2", revenue: 1200, conversion: 19 },
  { month: "T3", revenue: 1300, conversion: 18 },
  { month: "T4", revenue: 1600, conversion: 20 },
  { month: "T5", revenue: 1300, conversion: 19 },
];
