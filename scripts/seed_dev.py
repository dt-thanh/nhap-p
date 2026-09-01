"""Nạp dữ liệu mẫu cho môi trường dev/test.

    python -m scripts.seed_dev            # nạp / cập nhật
    python -m scripts.seed_dev --reset    # xoá đúng các bản ghi do seed tạo, rồi nạp lại
    python -m scripts.seed_dev --counts   # chỉ in số dòng hiện có, không ghi

Ba quyết định thiết kế đáng giải thích:

1. **Khoá chính tất định.** Mọi id đều là `uuid5(NS_SEED, "<bảng>:<khoá nghiệp vụ>")`.
   Chạy lần thứ hai sinh ra đúng các id cũ nên câu lệnh rơi vào `ON CONFLICT (pk)
   DO UPDATE` — không nhân bản, không cần cột đánh dấu, và `--reset` biết chính xác
   dòng nào là của seed để xoá mà không đụng dữ liệu người dùng nhập tay.

2. **Phản chiếu schema từ DB thật** thay vì khai lại 21 bảng. `src/models/tables.py`
   mới chỉ mô tả 8 bảng của luồng nạp file; khai thêm 13 bảng nữa ở đây sẽ tạo ra
   một bản sao thứ hai của schema, và bản sao đó sẽ lệch. Phản chiếu bảo đảm tên
   cột luôn khớp migration đã chạy.

3. **Ngày tháng neo vào hằng số `BASE_DATE`**, không dùng `date.today()`. Cùng một
   lần chạy hôm nay và tháng sau phải cho ra cùng một tập dữ liệu, nếu không thì
   test dựa trên seed sẽ hỏng theo thời gian.

Dữ liệu là hư cấu hoàn toàn: tên người, email `@demo.local`, số điện thoại và mã
bản ghi đều bịa, tiền tố `DEMO-`. `password_hash` cố tình KHÔNG phải hash hợp lệ
nên không đăng nhập được bằng bất kỳ mật khẩu nào.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from src.db import get_engine

# --------------------------------------------------------------------------
# Hạ tầng tất định
# --------------------------------------------------------------------------

# Namespace cố định. Đổi giá trị này = đổi toàn bộ id = seed cũ thành mồ côi,
# nên coi như hằng số bất biến của dự án.
NS_SEED = uuid.UUID("3f2b71a4-9c8d-5e10-b6a7-0d4c1e93f27b")

# Non-CRM lineage — obviously distinct from any real Mini CRM instance id
# (`mini-crm-dev`) and from the other dev-only fixtures' identities
# (`crm_real_data_fixture`/`ai-dev-fixture`, `synthetic_demo`/
# `synthetic-demo-2026`), so a `projects`/`areas` row from this script can
# never be mistaken for real synced data or another fixture's data.
SEED_DEV_SOURCE_SYSTEM = "seed_dev_fixture"
SEED_DEV_SOURCE_INSTANCE_ID = "seed-dev-local"

BASE_DATE = date(2025, 4, 1)
BASE_TS = datetime(2025, 4, 1, 3, 0, tzinfo=UTC)


def uid(kind: str, key: str) -> uuid.UUID:
    """Id tất định cho một thực thể seed."""
    return uuid.uuid5(NS_SEED, f"{kind}:{key}")


def spread(key: str, lo: int, hi: int) -> int:
    """Số nguyên trong [lo, hi] suy ra từ `key` — biến thiên nhưng lặp lại được.

    Không dùng `random` vì cần giá trị ổn định qua các phiên bản Python.
    """
    digest = hashlib.sha256(key.encode()).hexdigest()
    return lo + int(digest[:8], 16) % (hi - lo + 1)


def ts(day_offset: int, hour: int = 3) -> datetime:
    return BASE_TS + timedelta(days=day_offset, hours=hour - 3)


def d(day_offset: int) -> date:
    return BASE_DATE + timedelta(days=day_offset)


def row_hash(*parts: object) -> str:
    """Vân tay dòng nguồn, cùng vai trò với hash mà ImportService sinh ra."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


# --------------------------------------------------------------------------
# Nội dung seed
# --------------------------------------------------------------------------

NO_LOGIN = "!seed-account-not-a-credential"  # không phải bcrypt -> verify luôn thất bại

USERS = [
    ("admin-1", "quan.tri@demo.local", "DEMO Trần Quốc An", "admin", True, 0),
    ("admin-2", "giam.sat@demo.local", "DEMO Lê Bảo Châu", "admin", False, 1),
    ("manager-1", "quan.ly.mien.bac@demo.local", "DEMO Nguyễn Minh Đức", "manager", True, 2),
    ("manager-2", "quan.ly.mien.nam@demo.local", "DEMO Phạm Thu Hà", "manager", True, 3),
    ("analyst-1", "phan.tich.1@demo.local", "DEMO Võ Hoàng Long", "analyst", True, 4),
    ("analyst-2", "phan.tich.2@demo.local", "DEMO Đỗ Khánh Vy", "analyst", False, 5),
]

# (khoá, tên, ngày mở bán, trạng thái, có ảnh bìa, người tạo, người duyệt, lý do)
PROJECTS = [
    ("P01", "DEMO Khu đô thị Ánh Dương", d(-30), "active", True, "manager-1", "admin-1", None),
    ("P02", "DEMO Căn hộ Bến Xanh", d(120), "pending", False, "manager-2", None, None),
    (
        "P03",
        "DEMO Khu thấp tầng Cẩm Lệ",
        d(-200),
        "rejected",
        False,
        "manager-2",
        "admin-1",
        "Hồ sơ pháp lý phân khu chưa đầy đủ, đề nghị bổ sung rồi nộp lại.",
    ),
    ("P04", "DEMO Tháp đôi Duyên Hải", d(-660), "archived", True, "manager-1", "admin-1", None),
]

# (khoá, dự án, tên phân khu, loại căn, số phòng ngủ, diện tích, tổng căn, trạng thái)
AREAS = [
    ("A01", "P01", "DEMO Toà A1", "Căn hộ", 2, Decimal("68.50"), 120, "active"),
    ("A02", "P01", "DEMO Toà A1", "Duplex", 3, Decimal("92.00"), 80, "active"),
    ("A03", "P01", "DEMO Toà A2", "Studio", 0, Decimal("32.00"), 60, "active"),
    ("A04", "P01", "DEMO Dãy Shophouse", "Shophouse", 1, Decimal("110.00"), 12, "pending"),
    ("A05", "P02", "DEMO Toà B1", "Căn hộ", 1, Decimal("45.00"), 150, "active"),
    ("A06", "P02", "DEMO Toà B1", "Penthouse", 4, Decimal("210.50"), 6, "active"),
    ("A07", "P03", "DEMO Dãy nhà phố C", "Nhà phố", 4, Decimal("180.00"), 24, "rejected"),
    ("A08", "P04", "DEMO Toà D1", "Căn hộ", 2, Decimal("70.00"), 100, "archived"),
    ("A09", "P04", "DEMO Toà D1", "Duplex", 3, Decimal("145.00"), 0, "archived"),
    ("A10", "P04", "DEMO Toà D2", "Căn hộ", 2, Decimal("70.00"), 100, "archived"),
]

# Các phân khu có dữ liệu bán hàng / tồn kho / hấp thụ dày để đủ cho biểu đồ,
# phân trang và các phép tổng hợp trên dashboard.
#
# Danh sách này cố ý chạm tới CẢ BỐN dự án. `GET /projects` sắp theo tên và
# `activeProjectId()` ở frontend lấy phần tử đầu tiên, nên dự án mặc định của
# giao diện phụ thuộc vào thứ tự chữ cái — nếu chỉ nhồi dữ liệu cho một dự án
# thì người mở demo lên rất dễ rơi vào đúng dự án rỗng và tưởng hệ thống hỏng.
BUSY_AREAS = ["A01", "A02", "A03", "A05", "A07", "A08"]
SERIES_DAYS = 60

# (khoá, dự án, tên file, trạng thái, rows_ok, rows_failed, người tải, lệch ngày)
UPLOAD_FILES = [
    ("F01", "P01", "DEMO_ban_hang_thang_02.xlsx", "completed", 240, 0, "analyst-1", -45),
    ("F02", "P01", "DEMO_ton_kho_quy_1.xlsx", "completed", 48, 2, "analyst-1", -40),
    ("F03", "P02", "DEMO_ban_hang_mo_ban.csv", "completed", 60, 0, "analyst-2", -20),
    ("F04", "P01", "DEMO_ban_hang_loi_dinh_dang.csv", "failed", 0, 10, "analyst-2", -10),
    ("F05", "P02", "DEMO_phan_khu_bo_sung.xlsx", "processing", 0, 0, None, -1),
    ("F06", "P03", "DEMO_ton_kho_cho_duyet.xlsx", "pending", 0, 0, "manager-2", 0),
    ("F07", "P03", "DEMO_ban_hang_nha_pho.xlsx", "completed", 60, 0, "manager-2", -35),
    ("F08", "P04", "DEMO_ban_hang_luu_tru.csv", "completed", 60, 0, "analyst-1", -50),
]

# Tệp nguồn (bán hàng, tồn kho) của từng dự án. `sales_records.file_id` không bị
# ràng buộc phải cùng dự án với phân khu, nhưng để lệch thì màn hình "xem nguồn
# của dòng này" sẽ dẫn người dùng sang tệp của dự án khác.
PROJECT_FILES = {
    "P01": ("F01", "F02"),
    "P02": ("F03", "F03"),
    "P03": ("F07", "F07"),
    "P04": ("F08", "F08"),
}

# Lỗi validate của F04 — phủ các mã lỗi mà ImportService/parser thật sinh ra,
# gồm cả trường hợp lỗi không gắn với cột nào (column_name NULL).
UPLOAD_ERROR_SPECS = [
    (3, "sold_date", "INVALID_DATE", "Giá trị '31/02/2025' không phải ngày hợp lệ."),
    (4, "units_sold", "NOT_A_NUMBER", "Giá trị 'mười hai' không phải số nguyên."),
    (5, "units_sold", "NEGATIVE_VALUE", "Số căn bán ra không được âm."),
    (6, "area_name", "AREA_NOT_FOUND", "Không tìm thấy phân khu 'Toà A9' trong dự án."),
    (7, "area_name", "AREA_AMBIGUOUS", "Tên phân khu khớp nhiều hơn một bản ghi."),
    (8, "external_record_id", "REQUIRED_MISSING", "Thiếu mã bản ghi bắt buộc."),
    (9, "sold_date", "REQUIRED_MISSING", "Thiếu ngày bán."),
    (10, "unit_type", "REQUIRED_MISSING", "Thiếu loại căn."),
    (11, None, "ROW_TOO_SHORT", "Dòng có ít cột hơn tiêu đề."),
    (12, None, "DUPLICATE_ROW", "Dòng trùng với dòng 4 trong cùng tệp."),
]

# (khoá, dự án, kiểu kích hoạt, trạng thái, tổng, thành công, thất bại, lệch ngày)
FORECAST_JOBS = [
    ("J01", "P01", "scheduled", "completed", 4, 4, 0, -14),
    ("J02", "P02", "manual", "completed", 2, 1, 1, -7),
    ("J03", "P01", "scheduled", "failed", 4, 0, 4, -3),
    ("J04", "P01", "manual", "running", 4, 1, 0, 0),
]

# (khoá, job, phân khu, file, nhãn tin cậy, có ngày bán hết, có MAPE)
FORECASTS = [
    ("FC1", "J01", "A01", "F01", "high", True, True),
    ("FC2", "J01", "A02", "F01", "medium", True, True),
    ("FC3", "J01", "A03", "F01", "low", False, None),
    ("FC4", "J01", "A04", "F01", "low", False, None),
    ("FC5", "J02", "A05", "F03", "high", True, True),
    ("FC6", "J02", "A06", "F03", "medium", False, None),
]

HORIZON = 30

# (khoá, forecast, phân khu, loại, còn lại ngày, ngưỡng, mức, trạng thái)
ALERTS = [
    ("AL1", "FC1", "A01", "SELLOUT_RISK", 24, 45, "critical", "open"),
    ("AL2", "FC1", "A01", "VELOCITY_DROP", 90, 45, "medium", "closed"),
    ("AL3", "FC2", "A02", "SELLOUT_RISK", 38, 45, "high", "open"),
    ("AL4", "FC3", "A03", "DATA_GAP", 120, 45, "low", "dismissed"),
    ("AL5", "FC5", "A05", "SELLOUT_RISK", 41, 45, "high", "closed"),
    ("AL6", "FC6", "A06", "VELOCITY_DROP", 300, 45, "low", "open"),
]

# (khoá, forecast, phân khu, mức rủi ro, hành động)
SUGGESTIONS = [
    ("SG1", "FC1", "A01", "high", "TANG_GIA", "Tốc độ hấp thụ vượt 1.5 căn/ngày ba tuần liên tiếp."),
    ("SG2", "FC1", "A01", "medium", "GIU_NGUYEN", "Biến động trong khoảng dự báo, chưa cần can thiệp."),
    ("SG3", "FC2", "A02", "high", "MO_BAN_THEM", "Tồn kho dự kiến hết trước kỳ mở bán kế tiếp."),
    ("SG4", "FC3", "A03", "low", "GIAM_GIA", "Hấp thụ chậm hơn trung bình dự án 40%."),
    ("SG5", "FC5", "A05", "medium", "TANG_CHIET_KHAU", "Tốc độ giảm dần bốn tuần liên tiếp."),
    ("SG6", "FC6", "A06", "low", "GIU_NGUYEN", "Mẫu quá nhỏ, khoảng tin cậy rộng."),
]

# (khoá, suggestion, phân khu, trạng thái, phiên bản)
PROPOSALS = [
    ("PR1", "SG1", "A01", "approved", 2),
    ("PR2", "SG2", "A01", "open", 1),
    ("PR3", "SG3", "A02", "rejected", 1),
    ("PR4", "SG4", "A03", "cancelled", 3),
    ("PR5", "SG5", "A05", "approved", 1),
    ("PR6", "SG6", "A06", "open", 1),
]

# (khoá, proposal, người duyệt, quyết định, lý do)
APPROVALS = [
    ("AP1", "PR1", "manager-1", "approved", "Đồng ý điều chỉnh giá rổ hàng tầng trung."),
    ("AP2", "PR3", "manager-2", "rejected", "Chưa đủ dữ liệu tồn kho thực tế để mở bán thêm."),
    ("AP3", "PR5", "admin-1", "approved", "Chấp thuận mức chiết khấu trong hạn mức được uỷ quyền."),
]

# (khoá, forecast, trạng thái, mã lỗi, số lần thử lại)
LLM_CALLS = [
    ("LC1", "FC1", "success", None, 0),
    ("LC2", "FC2", "success", None, 0),
    ("LC3", "FC3", "error", "RATE_LIMITED", 2),
    ("LC4", "FC4", "timeout", "DEADLINE_EXCEEDED", 3),
    ("LC5", "FC5", "success", None, 1),
    ("LC6", "FC6", "error", "INVALID_RESPONSE", 1),
]

# (khoá, người dùng, vai trò ghi nhận, hành động, loại thực thể, khoá thực thể, lệch ngày)
AUDIT_LOGS = [
    ("AU01", "manager-1", "manager", "PROJECT_CREATED", "project", "P01", -30),
    ("AU02", "admin-1", "admin", "PROJECT_APPROVED", "project", "P01", -29),
    ("AU03", "manager-2", "manager", "PROJECT_CREATED", "project", "P03", -25),
    ("AU04", "admin-1", "admin", "PROJECT_REJECTED", "project", "P03", -24),
    ("AU05", "manager-1", "manager", "AREA_CREATED", "area", "A01", -28),
    ("AU06", "manager-1", "manager", "AREA_UPDATED", "area", "A02", -21),
    ("AU07", "analyst-1", "analyst", "FILE_UPLOADED", "upload_file", "F01", -45),
    ("AU08", "analyst-2", "analyst", "FILE_UPLOAD_FAILED", "upload_file", "F04", -10),
    ("AU09", None, "system", "FORECAST_JOB_STARTED", "forecast_job", "J01", -14),
    ("AU10", None, "system", "FORECAST_JOB_FAILED", "forecast_job", "J03", -3),
    ("AU11", "manager-1", "manager", "PROPOSAL_APPROVED", "proposal", "PR1", -12),
    ("AU12", "manager-2", "manager", "PROPOSAL_REJECTED", "proposal", "PR3", -11),
]

# (khoá, người dùng, lệch ngày tạo, số ngày sống, đã thu hồi, bị thay bởi)
REFRESH_TOKENS = [
    ("RT1", "admin-1", -2, 30, False, None),
    ("RT2", "manager-1", -1, 30, False, None),
    ("RT3", "analyst-1", -90, 30, False, None),  # đã hết hạn tự nhiên
    ("RT4", "manager-2", -20, 30, True, "RT5"),  # bị xoay vòng
    ("RT5", "manager-2", -5, 30, False, None),
]

USER_AREAS = [
    ("manager-1", "A01"),
    ("manager-1", "A02"),
    ("manager-1", "A03"),
    ("manager-1", "A04"),
    ("manager-2", "A05"),
    ("manager-2", "A06"),
    ("manager-2", "A07"),
    ("analyst-1", "A01"),
    ("analyst-1", "A05"),
    ("analyst-2", "A08"),
]

SETTINGS = [
    ("demo.forecast.horizon_days", {"value": HORIZON, "unit": "day"}, "admin-1"),
    ("demo.alert.sellout_threshold_days", {"value": 45, "unit": "day"}, "admin-1"),
    ("demo.upload.error_rate_threshold", {"value": 0.2, "unit": "ratio"}, "admin-1"),
    ("demo.absorption.min_history_days", {"value": 30, "unit": "day"}, "manager-1"),
    ("demo.ui.default_granularity", {"value": "day", "options": ["day", "week", "month"]}, "manager-1"),
]


# --------------------------------------------------------------------------
# Dựng các bộ dòng
# --------------------------------------------------------------------------


def build_dataset() -> list[tuple[str, list[dict[str, Any]]]]:
    """Trả về danh sách (tên bảng, các dòng) ĐÚNG thứ tự phụ thuộc khoá ngoại."""
    u = {key: uid("users", key) for key, *_ in USERS}
    p = {key: uid("projects", key) for key, *_ in PROJECTS}
    a = {key: uid("areas", key) for key, *_ in AREAS}
    f = {key: uid("upload_files", key) for key, *_ in UPLOAD_FILES}
    j = {key: uid("forecast_jobs", key) for key, *_ in FORECAST_JOBS}
    fc = {key: uid("forecasts", key) for key, *_ in FORECASTS}
    sg = {key: uid("suggestions", key) for key, *_ in SUGGESTIONS}
    pr = {key: uid("proposals", key) for key, *_ in PROPOSALS}
    rt = {key: uid("refresh_tokens", key) for key, *_ in REFRESH_TOKENS}

    users = [
        {
            "id": u[key],
            "email": email,
            "password_hash": NO_LOGIN,
            "full_name": name,
            "role": role,
            "is_active": active,
            "created_at": ts(-365 + offset),
        }
        for key, email, name, role, active, offset in USERS
    ]

    projects = []
    for key, name, launch, status, has_cover, creator, reviewer, reason in PROJECTS:
        # P02 cố tình để trống headline/introduce: các trường mô tả là tuỳ chọn,
        # giao diện phải chịu được chuỗi rỗng.
        blank = key == "P02"
        projects.append(
            {
                "id": p[key],
                "name": name,
                "launch_date": launch,
                "created_at": ts(-300),
                "status": status,
                "headline": "" if blank else f"{name} — quỹ căn mở bán đợt mới",
                "introduce": ""
                if blank
                else (
                    f"{name} là dự án hư cấu dùng cho môi trường demo. "
                    "Mọi số liệu bán hàng, tồn kho và dự báo trong hồ sơ này đều do "
                    "script seed sinh ra, không phản ánh bất kỳ dự án có thật nào."
                ),
                "cover_image_url": (
                    f"https://res.cloudinary.com/demo/image/upload/v1/demo_seed/{key.lower()}_cover.jpg"
                    if has_cover
                    else None
                ),
                "cover_image_public_id": f"demo_seed/{key.lower()}_cover" if has_cover else None,
                "created_by": u[creator],
                "reviewed_by": u[reviewer] if reviewer else None,
                "reviewed_at": ts(-290) if reviewer else None,
                "review_reason": reason,
                # Non-CRM lineage stamp — this row must never be mistaken for
                # a real Mini CRM-synced project, and must never be
                # untraceable (source_system IS NULL).
                "external_id": key,
                "source_system": SEED_DEV_SOURCE_SYSTEM,
                "source_instance_id": SEED_DEV_SOURCE_INSTANCE_ID,
            }
        )

    areas = []
    for key, proj, area_name, unit_type, bedrooms, sqm, total, status in AREAS:
        has_cover = key in {"A01", "A05"}
        areas.append(
            {
                "id": a[key],
                "project_id": p[proj],
                "area_name": area_name,
                "unit_type": unit_type,
                "bedrooms": bedrooms,
                "area_sqm": sqm,
                "total_units": total,
                "created_at": ts(-280),
                "status": status,
                "headline": f"{area_name} · {unit_type}",
                "introduce": "" if key == "A09" else f"Quỹ căn {unit_type.lower()} thuộc {area_name} (dữ liệu demo).",
                "cover_image_url": (
                    f"https://res.cloudinary.com/demo/image/upload/v1/demo_seed/{key.lower()}_cover.jpg"
                    if has_cover
                    else None
                ),
                "cover_image_public_id": f"demo_seed/{key.lower()}_cover" if has_cover else None,
                "created_by": u["manager-1"] if proj in {"P01", "P04"} else u["manager-2"],
                "reviewed_by": u["admin-1"] if status in {"active", "rejected", "archived"} else None,
                "reviewed_at": ts(-275) if status in {"active", "rejected", "archived"} else None,
                "review_reason": ("Diện tích khai báo không khớp bản vẽ được duyệt." if status == "rejected" else None),
                # Non-CRM lineage stamp — see the matching comment on `projects` above.
                "external_id": key,
                "source_system": SEED_DEV_SOURCE_SYSTEM,
                "source_instance_id": SEED_DEV_SOURCE_INSTANCE_ID,
            }
        )

    upload_files = [
        {
            "id": f[key],
            "project_id": p[proj],
            "uploaded_by": u[uploader] if uploader else None,
            "filename": filename,
            "checksum": row_hash("upload_file", key),
            "status": status,
            "rows_ok": rows_ok,
            "rows_failed": rows_failed,
            "uploaded_at": ts(offset),
        }
        for key, proj, filename, status, rows_ok, rows_failed, uploader, offset in UPLOAD_FILES
    ]

    upload_errors = [
        {
            "id": uid("upload_errors", f"F04:{row_number}"),
            "file_id": f["F04"],
            "row_number": row_number,
            "column_name": column,
            "error_code": code,
            "message": message,
            "created_at": ts(-10),
        }
        for row_number, column, code, message in UPLOAD_ERROR_SPECS
    ]
    # Hai dòng lỗi của F02 để có file vừa completed vừa có lỗi cục bộ — trạng thái
    # này xảy ra thật khi tỷ lệ lỗi còn dưới ngưỡng từ chối.
    upload_errors += [
        {
            "id": uid("upload_errors", f"F02:{row_number}"),
            "file_id": f["F02"],
            "row_number": row_number,
            "column_name": "units_remaining",
            "error_code": "NEGATIVE_VALUE",
            "message": "Số căn còn lại không được âm.",
            "created_at": ts(-40),
        }
        for row_number in (17, 23)
    ]

    sales_records: list[dict[str, Any]] = []
    inventory_snapshots: list[dict[str, Any]] = []
    absorption_daily: list[dict[str, Any]] = []
    snapshot_types = ("opening", "closing", "manual", "derived")

    for area_key in BUSY_AREAS:
        area_id = a[area_key]
        area_spec = next(spec for spec in AREAS if spec[0] == area_key)
        file_key, inventory_file_key = PROJECT_FILES[area_spec[1]]
        total_units = area_spec[6]
        remaining = total_units
        recent = []

        for offset in range(SERIES_DAYS):
            day = d(-SERIES_DAYS + offset)
            # Xen kẽ ngày bằng 0 để chuỗi có cả điểm đáy, không phải nhiễu đều.
            sold = 0 if offset % 7 == 6 else spread(f"sold:{area_key}:{offset}", 0, 4)
            sold = min(sold, remaining)
            remaining -= sold
            recent.append(sold)

            ext = f"DEMO-SALE-{area_key}-{offset:04d}"
            sales_records.append(
                {
                    "id": uid("sales_records", f"{area_key}:{offset}"),
                    "area_id": area_id,
                    "file_id": f[file_key],
                    "sold_date": day,
                    "units_sold": sold,
                    "external_record_id": ext,
                    "source_row_hash": row_hash(area_key, day.isoformat(), sold, ext),
                    "created_at": ts(-SERIES_DAYS + offset),
                }
            )

            v7 = Decimal(sum(recent[-7:])) / Decimal(min(len(recent), 7))
            v30 = Decimal(sum(recent[-30:])) / Decimal(min(len(recent), 30))
            # Trước ngày thứ 30 chưa đủ lịch sử cho trung bình trượt 30 ngày ->
            # đánh dấu warning, đúng quy ước của AbsorptionCalculatorService.
            if offset < 30:
                quality = "warning"
            elif offset % 19 == 0:
                quality = "error"
            else:
                quality = "ok"
            absorption_daily.append(
                {
                    "id": uid("absorption_daily", f"{area_key}:{offset}"),
                    "area_id": area_id,
                    "stat_date": day,
                    "units_sold": sold,
                    "velocity_7d": v7.quantize(Decimal("0.0001")),
                    "velocity_30d": v30.quantize(Decimal("0.0001")),
                    "data_quality_status": quality,
                    # Ngày không có bản ghi nguồn được lấp đầy -> is_observed False.
                    "is_observed": sold > 0 or offset % 11 != 0,
                    "computed_at": ts(-SERIES_DAYS + offset, hour=23),
                }
            )

            if offset % 5 == 0:
                week = offset // 5
                snapshot_type = snapshot_types[week % len(snapshot_types)]
                inventory_snapshots.append(
                    {
                        "id": uid("inventory_snapshots", f"{area_key}:{offset}"),
                        "area_id": area_id,
                        "file_id": f[inventory_file_key],
                        "snapshot_date": day,
                        "units_remaining": remaining,
                        "snapshot_type": snapshot_type,
                        "source_row_hash": row_hash("inv", area_key, day.isoformat(), remaining, snapshot_type),
                        "created_at": ts(-SERIES_DAYS + offset, hour=22),
                    }
                )

    forecast_jobs = [
        {
            "id": j[key],
            "project_id": p[proj],
            "triggered_by": None if trigger == "scheduled" else u["manager-1"],
            "trigger_type": trigger,
            "status": status,
            "areas_total": total,
            "areas_succeeded": ok,
            "areas_failed": failed,
            "error_summary": (
                {"reason": "PROPHET_FIT_FAILED", "areas": failed} if status == "failed" or failed else None
            ),
            "started_at": ts(offset),
            # CHECK ck_forecast_jobs_finished_at_by_status: chỉ job đã kết thúc mới
            # được có finished_at.
            "finished_at": None if status in {"queued", "running"} else ts(offset, hour=4),
        }
        for key, proj, trigger, status, total, ok, failed, offset in FORECAST_JOBS
    ]

    forecasts = []
    forecast_points: list[dict[str, Any]] = []
    for key, job_key, area_key, file_key, confidence, has_sellout, has_mape in FORECASTS:
        cutoff = d(-SERIES_DAYS + SERIES_DAYS - 1)
        velocity = Decimal(spread(f"vel:{key}", 40, 260)) / Decimal(100)
        lower = (velocity * Decimal("0.7")).quantize(Decimal("0.0001"))
        upper = (velocity * Decimal("1.3")).quantize(Decimal("0.0001"))
        forecasts.append(
            {
                "id": fc[key],
                "area_id": a[area_key],
                "job_id": j[job_key],
                "file_id": f[file_key],
                "data_cutoff_date": cutoff,
                "run_at": ts(-14, hour=4),
                "horizon_days": HORIZON,
                "model_name": "prophet",
                "model_version": "1.1.5",
                "feature_version": "v1",
                "parameters": {
                    "seasonality_mode": "multiplicative",
                    "changepoint_prior_scale": 0.05,
                    "weekly_seasonality": True,
                },
                "velocity_forecast": velocity,
                "pred_lower": lower,
                "pred_upper": upper,
                "interval_level": Decimal("0.8000"),
                # CHECK ck_forecasts_sellout_date_gte_cutoff
                "sellout_date": (cutoff + timedelta(days=spread(f"so:{key}", 20, 200))) if has_sellout else None,
                "confidence_label": confidence,
                "mape": (Decimal(spread(f"mape:{key}", 300, 2500)) / Decimal(10000)) if has_mape else None,
            }
        )
        for step in range(HORIZON):
            yhat = velocity * (Decimal(1) + Decimal(spread(f"pt:{key}:{step}", -15, 15)) / Decimal(100))
            forecast_points.append(
                {
                    "id": uid("forecast_points", f"{key}:{step}"),
                    "forecast_id": fc[key],
                    "ds": cutoff + timedelta(days=step + 1),
                    "yhat": yhat.quantize(Decimal("0.0001")),
                    "yhat_lower": (yhat * Decimal("0.7")).quantize(Decimal("0.0001")),
                    "yhat_upper": (yhat * Decimal("1.3")).quantize(Decimal("0.0001")),
                }
            )

    explanations = [
        {
            "id": uid("explanations", key),
            "forecast_id": fc[key],
            "content_vi": (
                "Tốc độ hấp thụ dự kiến duy trì quanh mức trung bình 30 ngày gần nhất. "
                "Yếu tố ảnh hưởng mạnh nhất là chu kỳ theo tuần và đợt mở bán gần đây. "
                "Đây là nội dung demo do script seed sinh ra."
            ),
            "key_factors": [
                {"name": "weekly_seasonality", "weight": 0.42},
                {"name": "recent_launch", "weight": 0.31},
                {"name": "inventory_level", "weight": 0.27},
            ],
            "assumptions": {
                "no_price_change": True,
                "no_new_launch_within_horizon": True,
                "history_days": SERIES_DAYS,
            },
            "model_name": "claude-demo",
            "prompt_template_version": "v3",
            "generated_at": ts(-14, hour=5),
        }
        for key in ("FC1", "FC2", "FC3", "FC5")
    ]

    alerts = [
        {
            "id": uid("alerts", key),
            "forecast_id": fc[fc_key],
            "area_id": a[area_key],
            "alert_type": alert_type,
            "days_to_sellout": days,
            "threshold_days": threshold,
            "severity": severity,
            "status": status,
            "created_at": ts(-14, hour=5),
            # CHECK ck_alerts_closed_at_by_status
            "closed_at": None if status == "open" else ts(-12, hour=9),
        }
        for key, fc_key, area_key, alert_type, days, threshold, severity, status in ALERTS
    ]

    suggestions = [
        {
            "id": sg[key],
            "forecast_id": fc[fc_key],
            "area_id": a[area_key],
            "risk_level": risk,
            "action_type": action,
            "rationale": rationale,
            "created_at": ts(-14, hour=6),
        }
        for key, fc_key, area_key, risk, action, rationale in SUGGESTIONS
    ]

    proposals = [
        {
            "id": pr[key],
            "suggestion_id": sg[sg_key],
            "area_id": a[area_key],
            "status": status,
            "version": version,
            "created_at": ts(-13),
            # CHECK ck_proposals_closed_at_by_status
            "closed_at": None if status == "open" else ts(-12),
        }
        for key, sg_key, area_key, status, version in PROPOSALS
    ]

    approvals = [
        {
            "id": uid("approvals", key),
            "proposal_id": pr[pr_key],
            "user_id": u[user_key],
            "decision": decision,
            "reason": reason,
            "decided_at": ts(-12),
        }
        for key, pr_key, user_key, decision, reason in APPROVALS
    ]

    llm_calls = [
        {
            "id": uid("llm_calls", key),
            "forecast_id": fc[fc_key],
            "provider": "anthropic",
            "model_name": "claude-demo",
            "prompt_template_version": "v3",
            "prompt_tokens": spread(f"pt:{key}", 800, 2400),
            "completion_tokens": spread(f"ct:{key}", 120, 900),
            "latency_ms": spread(f"lat:{key}", 400, 9000),
            "cost_amount": Decimal(spread(f"cost:{key}", 100, 4200)) / Decimal(100000),
            "status": status,
            # CHECK ck_llm_calls_error_code_by_status
            "error_code": error_code,
            "retry_count": retries,
            "called_at": ts(-14, hour=5),
        }
        for key, fc_key, status, error_code, retries in LLM_CALLS
    ]

    entity_ids = {"project": p, "area": a, "upload_file": f, "forecast_job": j, "proposal": pr}
    audit_logs = [
        {
            "id": uid("audit_logs", key),
            "user_id": u[user_key] if user_key else None,
            "role": role,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_ids[entity_type][entity_key],
            "entity_key": entity_key,
            "payload": {"source": "seed_dev", "note": "Bản ghi kiểm toán demo."},
            "ip_address": f"198.51.100.{spread(key, 2, 250)}",  # dải TEST-NET-2, không định tuyến
            "user_agent": "DemoSeed/1.0 (dev fixture)",
            "created_at": ts(offset),
        }
        for key, user_key, role, action, entity_type, entity_key, offset in AUDIT_LOGS
    ]

    refresh_tokens = [
        {
            "id": rt[key],
            "user_id": u[user_key],
            "token_hash": row_hash("refresh_token", key),
            "expires_at": ts(created + lifetime),
            "revoked_at": ts(created + 1) if revoked else None,
            "replaced_by": rt[replaced_by] if replaced_by else None,
            "created_at": ts(created),
        }
        for key, user_key, created, lifetime, revoked, replaced_by in REFRESH_TOKENS
    ]

    user_areas = [
        {"user_id": u[user_key], "area_id": a[area_key], "assigned_at": ts(-270)} for user_key, area_key in USER_AREAS
    ]

    settings = [
        {"key": key, "value": value, "updated_by": u[user_key], "updated_at": ts(-200)}
        for key, value, user_key in SETTINGS
    ]

    # Thứ tự này là thứ tự chèn: cha luôn đứng trước con.
    return [
        ("users", users),
        ("projects", projects),
        ("settings", settings),
        ("refresh_tokens", refresh_tokens),
        ("areas", areas),
        ("upload_files", upload_files),
        ("forecast_jobs", forecast_jobs),
        ("audit_logs", audit_logs),
        ("upload_errors", upload_errors),
        ("user_areas", user_areas),
        ("sales_records", sales_records),
        ("inventory_snapshots", inventory_snapshots),
        ("absorption_daily", absorption_daily),
        ("forecasts", forecasts),
        ("forecast_points", forecast_points),
        ("explanations", explanations),
        ("alerts", alerts),
        ("suggestions", suggestions),
        ("llm_calls", llm_calls),
        ("proposals", proposals),
        ("approvals", approvals),
    ]


# --------------------------------------------------------------------------
# Ghi xuống DB
# --------------------------------------------------------------------------

CHUNK = 500


def _upsert(table: sa.Table, rows: list[dict[str, Any]]) -> Any:
    """INSERT ... ON CONFLICT (khoá chính) DO UPDATE.

    Vì id là tất định, lần chạy thứ hai va vào đúng dòng cũ. DO UPDATE (thay vì
    DO NOTHING) để sửa nội dung seed rồi chạy lại là thấy ngay giá trị mới.
    """
    stmt = pg_insert(table).values(rows)
    pk = {c.name for c in table.primary_key.columns}
    updates = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in pk}
    if not updates:  # bảng chỉ gồm khoá chính (không có ở schema này, nhưng đừng vỡ)
        return stmt.on_conflict_do_nothing(index_elements=sorted(pk))
    return stmt.on_conflict_do_update(index_elements=sorted(pk), set_=updates)


async def _referencing_columns(conn: Any) -> dict[str, list[tuple[str, list[str], list[str]]]]:
    """Bảng cha -> [(bảng con, cột con, cột cha)], đọc từ catalog của PostgreSQL.

    Đọc từ catalog thay vì khai tay: thêm khoá ngoại mới trong migration là hàm
    này tự biết, không cần ai nhớ sửa ở đây.
    """
    rows = (
        await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text AS parent, conrelid::regclass::text AS child, "
                "       pg_get_constraintdef(oid) AS def "
                "FROM pg_constraint WHERE contype = 'f'"
            )
        )
    ).all()

    graph: dict[str, list[tuple[str, list[str], list[str]]]] = {}
    for parent, child, definition in rows:
        head, tail = definition.split(" REFERENCES ")
        child_cols = [c.strip() for c in head.split("(", 1)[1].rstrip(")").split(",")]
        parent_cols = [c.strip() for c in tail.split("(", 1)[1].split(")")[0].split(",")]
        graph.setdefault(parent, []).append((child, child_cols, parent_cols))
    return graph


async def _delete_seed_rows(conn: Any, meta: sa.MetaData, dataset: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """Xoá đúng các dòng seed sở hữu, theo thứ tự NGƯỢC với thứ tự chèn.

    Điểm tinh tế: một dòng seed vẫn có thể bị dữ liệu NGOÀI seed tham chiếu — ví dụ
    lập trình viên tạo tay một dự án và chọn `created_by` là tài khoản demo. Xoá
    tài khoản đó sẽ nổ khoá ngoại, mà cascade thì lại xoá mất dữ liệu người dùng.
    Nên ở đây giữ lại những dòng còn bị tham chiếu; chúng sẽ được upsert làm mới
    ngay sau đó. Vì đang xoá ngược thứ tự phụ thuộc, con của seed đã biến mất từ
    trước, nên phần còn tham chiếu chắc chắn không phải của seed.
    """
    graph = await _referencing_columns(conn)

    for name, rows in reversed(dataset):
        if not rows:
            continue
        table = meta.tables[name]
        pk = list(table.primary_key.columns)
        if len(pk) == 1:
            owned = pk[0].in_([r[pk[0].name] for r in rows])
        else:
            owned = sa.tuple_(*pk).in_([tuple(r[c.name] for c in pk) for r in rows])

        guards = []
        for child_name, child_cols, parent_cols in graph.get(name, []):
            if child_name == name:  # tự tham chiếu (refresh_tokens.replaced_by)
                continue
            child = sa.table(child_name, *(sa.column(c) for c in child_cols))
            guards.append(
                ~sa.exists(
                    sa.select(sa.literal(1))
                    .select_from(child)
                    .where(*(child.c[cc] == table.c[pc] for cc, pc in zip(child_cols, parent_cols, strict=True)))
                )
            )

        await conn.execute(sa.delete(table).where(sa.and_(owned, *guards)))


async def seed(*, reset: bool, engine: AsyncEngine | None = None) -> dict[str, int]:
    """Ghi toàn bộ dataset. `engine` để test trỏ vào database test riêng."""
    dataset = build_dataset()
    engine = engine or get_engine()
    meta = sa.MetaData()
    written: dict[str, int] = {}

    async with engine.begin() as conn:
        await conn.run_sync(meta.reflect, only=[name for name, _ in dataset])

        if reset:
            await _delete_seed_rows(conn, meta, dataset)

        for name, rows in dataset:
            table = meta.tables[name]
            for start in range(0, len(rows), CHUNK):
                await conn.execute(_upsert(table, rows[start : start + CHUNK]))
            written[name] = len(rows)

    return written


async def counts(tables: list[str], engine: AsyncEngine | None = None) -> dict[str, int]:
    engine = engine or get_engine()
    meta = sa.MetaData()
    async with engine.connect() as conn:
        await conn.run_sync(meta.reflect, only=tables)
        return {
            name: (await conn.execute(sa.select(sa.func.count()).select_from(meta.tables[name]))).scalar_one()
            for name in tables
        }


def _assert_development_confirmed(confirmed: bool) -> None:
    """`seed_dev.py` creates `projects`/`areas` rows stamped with a non-CRM
    lineage (`source_system='seed_dev_fixture'`) for exercising non-domain
    dev/test surfaces (users, forecasts, audit logs, ...). It must never run
    unconfirmed or outside development — see AGENTS.md's "MiniCRM is the sole
    owner of Project/Area/Unit/Deal" invariant."""
    import os

    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env and app_env != "development":
        raise RuntimeError(f"seed_dev.py refuses to run outside development (APP_ENV={app_env!r})")
    if not confirmed:
        raise RuntimeError("seed_dev.py requires --confirm-seed to write (this is a write, not a preview)")


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Nạp dữ liệu mẫu dev/test.")
    parser.add_argument("--reset", action="store_true", help="Xoá các bản ghi do seed tạo rồi nạp lại.")
    parser.add_argument("--counts", action="store_true", help="Chỉ in số dòng hiện có, không ghi gì.")
    parser.add_argument("--confirm-seed", action="store_true", help="Bắt buộc để ghi — bảo vệ khỏi ghi ngoài ý muốn.")
    args = parser.parse_args(argv)

    names = [name for name, _ in build_dataset()]

    if args.counts:
        for name, n in (await counts(names)).items():
            print(f"{name:22s} {n}")
        return 0

    _assert_development_confirmed(args.confirm_seed)

    written = await seed(reset=args.reset)
    actual = await counts(names)
    total = 0
    for name in names:
        print(f"{name:22s} seed={written[name]:5d}  bảng={actual[name]}")
        total += written[name]
    print(f"{'TỔNG':22s} seed={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
