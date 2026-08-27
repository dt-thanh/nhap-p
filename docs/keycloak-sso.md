# Keycloak SSO — Runbook

Keycloak là **nhà cung cấp danh tính DUY NHẤT ở runtime** của cả **Mini CRM**
và **Product/AbsorbIQ**. Microsoft Entra ID đã bị gỡ khỏi mã nguồn (migration
Keycloak-only — xem mục lịch sử trong `pipeline_status.md`); `AUTH_PROVIDER`/
`MINICRM_AUTH_PROVIDER` chỉ chấp nhận giá trị `keycloak`, và biến môi trường
`ENTRA_*`/`MINICRM_ENTRA_*` không còn được backend đọc — nếu chúng còn sót lại
trong một `.env` cũ, chúng bị bỏ qua hoàn toàn, không kích hoạt bất kỳ đường
xác thực nào.

SSO **không** chia sẻ cookie/token giữa hai app. Mỗi app có phiên riêng
(`minicrm_session`, `absorbiq_session`). Thứ dùng chung để SSO là **phiên đăng
nhập Keycloak** trong trình duyệt: đăng nhập ở Mini CRM tạo một SSO session ở
Keycloak; khi Product bắt đầu vòng OIDC của nó, Keycloak thấy session còn sống
và trả `code` ngay — không hỏi mật khẩu lần hai.

## URL

| Thành phần        | URL                                  |
|-------------------|--------------------------------------|
| CRM (frontend)    | http://localhost:5174                |
| Product (frontend)| http://localhost:5173                |
| Mini CRM API      | http://localhost:8100                |
| Product API       | http://localhost:8000                |
| Keycloak          | http://localhost:9090                |
| Keycloak Admin    | http://localhost:9090/admin          |
| Realm             | `p100`                               |

## Chuẩn bị `.env` (một lần)

```bash
cp .env.example .env
cp minicrm/.env.example minicrm/.env
```

Rồi điền các giá trị `CHANGE_ME_*` trong `.env` **gốc** (file này untracked —
KHÔNG commit). Tối thiểu cho luồng Keycloak:

- `KEYCLOAK_ADMIN_PASSWORD` — bắt buộc; sinh bằng
  `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
- `SESSION_SECRET` và `MINICRM_SESSION_SECRET` — hai secret khác nhau.
- `OIDC_CLIENT_SECRET` = `local-dev-absorbiq-secret`,
  `MINICRM_OIDC_CLIENT_SECRET` = `local-dev-minicrm-secret`
  (khớp `secret` trong `docker/keycloak/p100-realm.json` cho dev local).
- Hai secret bắt buộc của Mini CRM sync: `MINICRM_SYNC_API_KEY`,
  `MINICRM_AUTH_ADMIN_TOKEN` (dùng cùng giá trị ở `minicrm/.env`).

> `docker/keycloak/p100-realm.json` chứa client secret dev cố định để realm
> import chạy được bằng một lệnh. Đây là giá trị **chỉ dùng cho local**. Với
> staging/prod, tạo client secret riêng trong Keycloak Admin và đặt vào `.env`,
> đừng commit.

## Start / Stop / Logs

```bash
# Dựng toàn bộ stack (10 services, gồm Keycloak)
docker compose up -d --build

# Trạng thái + health
docker compose ps

# Logs Keycloak
docker compose logs -f keycloak

# Dừng (GIỮ dữ liệu)
docker compose stop
```

Keycloak mất ~30–60s cho lần khởi động đầu (import realm + build). `docker
compose ps` phải hiện `keycloak` là `healthy` trước khi login.

## Đăng nhập demo

1. Mở CRM: http://localhost:5174
2. Bấm **Đăng nhập bằng tài khoản công ty** → chuyển sang Keycloak.
3. Đăng nhập:
   - username: `demo`
   - password: `demo12345` (chỉ dev; đổi trong realm JSON hoặc Admin Console)
4. `demo` có realm role `admin`.

## Đăng ký người dùng mới (self-registration)

1. Ở trang đăng nhập Keycloak, bấm **Register**.
2. Điền form (không cần xác minh email ở local — `verifyEmail=false`).
3. Sau khi đăng ký, người dùng vào nhóm mặc định `/new-users` và nhận realm role
   **`business_viewer`** — **KHÔNG** phải admin.
4. Người dùng này đọc được phần business theo quyền `business_viewer` ở cả hai
   app (SSO dùng chung realm role).

## Cấp quyền admin cho một user

1. Mở Keycloak Admin: http://localhost:9090/admin (đăng nhập bằng
   `KEYCLOAK_ADMIN_USERNAME` / `KEYCLOAK_ADMIN_PASSWORD`).
2. Chọn realm **p100** → **Users** → chọn user → tab **Role mapping** →
   **Assign role** → chọn `admin`.
3. User đăng xuất/đăng nhập lại (hoặc chờ access token hết hạn ~5 phút) để nhận
   role mới.

## Đăng xuất

Bấm **Đăng xuất** trong app. Backend xoá cookie phiên cục bộ rồi trả URL
`end_session` của Keycloak; frontend điều hướng tới đó để kết thúc luôn phiên
SSO ở Keycloak — nếu bỏ bước này, lần "đăng nhập" kế tiếp sẽ vào thẳng lại.

## Reset CHỈ Keycloak (re-import realm)

Realm `p100` **chỉ import khi realm chưa tồn tại** trong volume `keycloak_data`.
Sửa `docker/keycloak/p100-realm.json` xong, muốn nạp lại thì xoá **RIÊNG** volume
Keycloak — **KHÔNG** dùng `docker compose down -v` (lệnh đó xoá cả Postgres của
Product lẫn Mini CRM):

```bash
docker compose rm -sf keycloak
docker volume rm absorptionforecast_keycloak_data
docker compose up -d keycloak
```

(Tên volume có tiền tố project `absorptionforecast_`; kiểm tra bằng
`docker volume ls | grep keycloak`.)

## An toàn bí mật

- KHÔNG commit `.env` / `minicrm/.env` (đã nằm trong `.gitignore`).
- File `*.env.example` chỉ chứa placeholder `CHANGE_ME_*`.
- Với môi trường thật: client secret, session secret, admin password đều tạo
  mới và giữ ngoài git.

## Lịch sử (retired)

Microsoft Entra ID từng là nhà cung cấp SSO ban đầu (CP4/CP5) trước khi Keycloak
được thêm vào làm IdP mặc định cho dev local, rồi trở thành nhà cung cấp DUY
NHẤT sau migration Keycloak-only. Toàn bộ code đường Entra (`app/entra.py` bên
Mini CRM, `src/services/entra_auth.py` bên Product), các biến `ENTRA_*`/
`MINICRM_ENTRA_*`, và bộ test offline riêng cho Entra đã bị gỡ khỏi runtime;
chi tiết migration nằm trong mục lịch sử của `pipeline_status.md`. Thiết kế vai
trò/claim vẫn provider-neutral (không có gì Entra-specific còn lại trong
`oidc.py`), nên thêm một IdP OIDC khác trong tương lai sẽ không cần đổi mô hình
role/scope hiện có.
