#!/usr/bin/env bash
# scripts/bootstrap_env.sh — dựng file .env cho lần chạy đầu tiên.
#
# Vì sao cần script này: `docker-compose.yml` cố ý dùng cú pháp `${VAR:?...}` cho
# hai bí mật (MINICRM_SYNC_API_KEY, MINICRM_AUTH_ADMIN_TOKEN) — Compose DỪNG NGAY
# nếu chúng trống, thay vì âm thầm khởi động với khoá rỗng. Đó là hành vi đúng,
# nhưng nó biến lần chạy đầu thành một chuỗi lỗi khó hiểu. Script này sinh sẵn
# các giá trị đó nên `make up` chạy được ngay từ lần đầu.
#
# AN TOÀN: chỉ TẠO file còn thiếu, KHÔNG bao giờ ghi đè file .env đã có (bí mật
# bạn đang dùng sẽ không bị mất). Chạy lại nhiều lần vô hại.
#
# MINICRM_SYNC_API_KEY sinh ở đây CHỈ là placeholder để Compose không chặn lần
# `up` đầu tiên (chưa có sync_credentials row nào khớp nó) — scripts/dev-reset.sh
# (qua scripts/ensure_sync_credential.sh) sẽ cấp khoá THẬT và ghi đè giá trị này
# ngay sau đó. Không dùng script này để xoay/đồng bộ khoá.

set -euo pipefail
cd "$(dirname "$0")/.."

gen() { python3 -c "import secrets; print(secrets.token_urlsafe(${1:-48}))"; }

created=0

# ---------- .env GỐC (Product/AbsorbIQ + 2 biến dùng chung với Mini CRM) ------
if [ -f .env ]; then
  echo "  .env đã có — giữ nguyên."
else
  cp .env.example .env
  SYNC_KEY="mcrm_$(gen 32)"
  ADMIN_TOKEN="admin_$(gen 32)"
  {
    echo ""
    echo "# --- Sinh tự động bởi scripts/bootstrap_env.sh ---"
    echo "# Hai biến này CỐ Ý trùng giá trị với minicrm/.env (cùng một Mini CRM"
    echo "# instance, cùng một khoá đã đăng ký trong sync_credentials)."
    echo "MINICRM_SYNC_API_KEY=${SYNC_KEY}"
    echo "MINICRM_AUTH_ADMIN_TOKEN=${ADMIN_TOKEN}"
    echo "SESSION_SECRET=$(gen 48)"
    echo "SESSION_COOKIE_SECURE=false"
    echo "# Tắt cửa dev khi đã bật Keycloak thật."
    echo "DEV_AUTH_BYPASS=true"
  } >> .env
  echo "  .env đã tạo (đã sinh sẵn khoá)."
  created=1
fi

# ---------- minicrm/.env -----------------------------------------------------
if [ -f minicrm/.env ]; then
  echo "  minicrm/.env đã có — giữ nguyên."
else
  cp minicrm/.env.example minicrm/.env
  # Đọc lại đúng hai giá trị từ .env gốc để hai bên KHỚP nhau. Sinh hai lần độc
  # lập sẽ tạo hai khoá khác nhau và đường đồng bộ sẽ 401 — lỗi rất khó đoán.
  # `tail -1` chứ KHÔNG phải `head -1`: `.env.example` đã có sẵn hai dòng
  # placeholder RỖNG cho hai khoá này, và giá trị thật do script sinh nằm ở CUỐI
  # file. Lấy dòng đầu sẽ bắt phải placeholder rỗng → hai file lệch khoá → đường
  # đồng bộ trả 401 với thông báo chẳng liên quan gì tới nguyên nhân.
  # `grep -v '=$'` loại nốt mọi dòng rỗng còn sót.
  SYNC_KEY="$(grep -E '^MINICRM_SYNC_API_KEY=' .env | grep -v '=$' | tail -1 | cut -d= -f2-)"
  ADMIN_TOKEN="$(grep -E '^MINICRM_AUTH_ADMIN_TOKEN=' .env | grep -v '=$' | tail -1 | cut -d= -f2-)"
  {
    echo ""
    echo "# --- Sinh tự động bởi scripts/bootstrap_env.sh ---"
    echo "MINICRM_SYNC_API_KEY=${SYNC_KEY}"
    echo "MINICRM_AUTH_ADMIN_TOKEN=${ADMIN_TOKEN}"
    echo "MINICRM_SESSION_SECRET=$(gen 48)"
    echo "MINICRM_AUTH_SIGNING_SECRET=$(gen 48)"
    echo "MINICRM_SESSION_COOKIE_SECURE=false"
    echo "# Bật đường token tĩnh để chạy/test được TRƯỚC khi có Keycloak."
    echo "# Đặt false ngay khi MINICRM_OIDC_* đã có giá trị thật."
    echo "MINICRM_LEGACY_TOKEN_AUTH_ENABLED=true"
  } >> minicrm/.env
  echo "  minicrm/.env đã tạo (khoá khớp với .env gốc)."
  created=1
fi

# ---------- .env.local cho CRM frontend --------------------------------------
if [ -f minicrm/crm-frontend/.env.local ]; then
  echo "  minicrm/crm-frontend/.env.local đã có — giữ nguyên."
else
  cp minicrm/crm-frontend/.env.example minicrm/crm-frontend/.env.local
  echo "  minicrm/crm-frontend/.env.local đã tạo."
  created=1
fi

echo ""
if [ "$created" -eq 1 ]; then
  echo "Xong. Token admin để gọi API/chạy test E2E:"
  grep -E '^MINICRM_AUTH_ADMIN_TOKEN=' .env | grep -v '=$' | tail -1 | cut -d= -f2-
else
  echo "Không có gì phải tạo — mọi file .env đã sẵn sàng."
fi
