# AbsorptionForecast AI Agent

> **Team ZeroToZeros** · VinUni AI20K Build Phase · Cohort 3

[![Gate](https://img.shields.io/badge/Gate%202-MVP-blue)]()
[![PRs](https://img.shields.io/badge/PR%20merged-21-green)]()
[![Tests](https://img.shields.io/badge/tests-1337%20passed-green)]()

---

## 1. Giới thiệu sản phẩm và chức năng:

Một dự án bất động sản có thể bao gồm nhiều phân khu, với hàng trăm đến hàng nghìn sản phẩm đang mở bán. Trong bối cảnh đó, Chủ đầu tư cần biết sản phẩm nào có khả năng được thị trường hấp thụ tốt, sản phẩm nào đang có nguy cơ bán chậm và yếu tố nào đang tác động đến khả năng bán hàng.

Tuy nhiên, việc đánh giá khả năng hấp thụ hiện nay thường dựa nhiều vào kinh nghiệm cá nhân, cảm tính và các file Excel được cập nhật thủ công. Điều này dẫn đến ba vấn đề lớn:

Thiếu nhất quán: Mỗi người có thể đưa ra một đánh giá khác nhau về khả năng bán của cùng một sản phẩm.
Thiếu khả năng giải thích: Khó xác định rõ vì sao một căn hộ có khả năng hấp thụ cao hơn căn khác và yếu tố nào đang tạo ra sự khác biệt.
Chậm phản ứng với thị trường: Khi giá bán, chính sách, tình trạng sản phẩm, giao dịch hoặc tốc độ hấp thụ thay đổi, kết quả đánh giá khó được cập nhật kịp thời.

### Bài toán đặt ra

Làm thế nào để xếp hạng khả năng hấp thụ của hàng trăm, hàng nghìn bất động sản một cách khách quan, nhất quán và có thể giải thích?

AbsorpIQ giải quyết bài toán này bằng cách xây dựng một Ranking Engine dựa trên AHP (Analytic Hierarchy Process) để định lượng và xếp hạng khả năng hấp thụ của từng sản phẩm dựa trên các tiêu chí đầu vào.

Trên nền tảng đó, AI Agent phân tích kết quả xếp hạng, giải thích và đồng thời tổng hợp các insight và đưa ra khuyến nghị để Chủ đầu tư có thêm góc nhìn khách quan về thị trường.

Mục tiêu cuối cùng: biến dữ liệu bán hàng và thị trường thành một hệ thống xếp hạng khả năng hấp thụ có cơ sở, có thể giải thích và hỗ trợ Chủ đầu tư điều chỉnh chính sách, sản phẩm và chiến lược kinh doanh.

| Lớp | Bản chất | Ai làm |
|---|---|---|
| **Xếp hạng** | Công thức **tất định** — tổng có trọng số các đặc trưng đã chuẩn hoá về `[0,1]` | Máy, không có LLM |
| **Tư vấn** | Giải thích bảng xếp hạng, đề xuất hành động | LangGraph agent + LLM |
| **Phê duyệt** | Mọi khuyến nghị phải qua người duyệt mới được thi hành | **Con người** |


📐 Kiến trúc chi tiết + sơ đồ: **[`docs/architecture.md`](docs/architecture.md)**
🧪 Bằng chứng đánh giá: **[`eval/results/report.md`](eval/results/report.md)**

---

## 2. Hướng dẫn chạy

> **SSO qua Keycloak:** Keycloak là nhà cung cấp danh tính DUY NHẤT ở runtime
> cho cả Mini CRM và Product (Microsoft Entra ID đã bị gỡ khỏi mã nguồn). Xem
> runbook đầy đủ: [docs/keycloak-sso.md](docs/keycloak-sso.md) — gồm login
> `demo`, Register, cấp admin, và reset Keycloak.

### 2.0 Services và cổng (đã xác minh qua `docker compose config`)

Tên project Compose: `absorptionforecast`.

| Service | Là gì | Cổng host | Cổng container | Volume dữ liệu |
|---|---|---|---|---|
| `db` | PostgreSQL của AbsorpIQ/Product | `5432` | `5432` | `pgdata` |
| `minicrm_db` | PostgreSQL của Mini CRM — DB riêng, KHÔNG chung với `db` | `5434` | `5432` | `minicrm_pgdata` |
| `keycloak` | IdP OIDC duy nhất (realm `p100`) | `9090` | `8080` | `keycloak_data` |
| `redis` | Hàng đợi RQ (worker/scheduler) | `6379` | `6379` | không có — `appendonly no`, mất trạng thái ngay khi container dừng |
| `api` | Backend AbsorpIQ (FastAPI) | `8000` | `8000` | — (đọc `db`) |
| `worker` | Job Prophet/LangGraph nền | — | — | — |
| `scheduler` | Lên lịch job 02:00 hằng ngày | — | — | — |
| `frontend` | Giao diện Product (Vite dev) | `5173` | `5173` | — |
| `minicrm` | Backend Mini CRM (FastAPI) | `8100` | `8000` | — (đọc `minicrm_db`) |
| `crm-frontend` | Giao diện Mini CRM (Vite dev) | `5174` | `5174` | — |

Volume file upload: `uploads` (dùng chung bởi `api`/`worker`/`scheduler`).

### 2.1 Lần đầu chạy — MỘT lệnh, không cần dán tay khoá API

```bash
# 1. Clone
git clone <repo-url> team-ZeroToZeros
cd team-ZeroToZeros

# 2. Sinh .env còn thiếu (KHÔNG ghi đè file đã có)
bash scripts/bootstrap_env.sh          # = make bootstrap

# 3. Điền các biến còn TRỐNG trong .env: JWT_SECRET (openssl rand -hex 32),
#    POSTGRES_PASSWORD, LLM_API_KEY (xem §3), KEYCLOAK_ADMIN_PASSWORD,
#    OIDC_CLIENT_SECRET/MINICRM_OIDC_CLIENT_SECRET (khớp docker/keycloak/p100-realm.json)
#    KHÔNG cần điền MINICRM_SYNC_API_KEY — bước 4 tự cấp và tự đưa khoá tới
#    Mini CRM, không qua .env nữa.

# 4. Hard reset data-only — giữ schema, volumes, Keycloak và credential
./scripts/dev-reset.sh --yes           # = make dev-reset
# Tuỳ chọn: reset rồi nạp fixture Mini CRM qua API/outbox
./scripts/dev-reset.sh --yes --seed

# 5. Đợi tới khi mọi service = healthy (KHÔNG phải "Restarting")
docker compose ps
```

`./scripts/dev-reset.sh --yes` (hay `make dev-reset`) dừng các service ứng dụng,
chạy `alembic upgrade head` cho cả hai database, rồi `TRUNCATE` đúng allowlist
trong `scripts/dev-hard-reset-minicrm.sql` và
`scripts/dev-hard-reset-absorpiq.sql`. Script không dùng `docker compose down -v`,
không xoá volume/Keycloak/.env, giữ `alembic_version` và AbsorpIQ
`sync_credentials`, sau đó khởi động lại stack. `--seed` là tuỳ chọn và gửi
fixture qua Mini CRM API/transactional outbox; mặc định reset để database rỗng.
Không có `--yes`: chỉ in kế hoạch, KHÔNG đụng gì.

Để bootstrap credential riêng trong local development, chạy:
`docker compose run --rm api python -m scripts.bootstrap_dev --no-seed`.
Lệnh này đảm bảo đúng một credential active cho `mini_crm / mini-crm-dev`:
giữ nguyên một row, cấp mới khi thiếu, hoặc revoke phần dư trong development.
Raw key mới được ghi vào `.dev-secrets/minicrm_sync_api_key` với mode `0600`;
không ghi secret vào log hay source control.

**Vì sao không còn phải dán tay khoá.** `.env`/`minicrm/.env` chỉ tồn tại trên
HOST và KHÔNG mount vào container `api` — một tiến trình bên trong đó không
thể tự ghi ra file host. Thay vào đó, `docker-compose.yml` mount
`./.dev-secrets:/app/.dev-secrets` (READ-WRITE, chỉ cho `api`) để
`bootstrap_dev.py` ghi khoá thẳng ra HOST, rồi Compose `secrets:` mount ĐÚNG
file đó READ-ONLY vào `minicrm` tại `/run/secrets/minicrm_sync_api_key` —
`minicrm/app/config.py::sync_api_key_value` đọc đúng đường này trước tiên.
`.dev-secrets/` nằm trong `.gitignore`, mode `700` (file bên trong mode
`600`) — không ai commit nhầm được.

`scripts/bootstrap_dev.py` tự nó KHÔNG bao giờ tự xoay một credential đang
sống, KHÔNG bao giờ in khoá thô ra ngoài log (trừ khi không truyền
`--credential-output-file` VÀ không chạm được `.env` — khi đó nó in ra đúng
một lần kèm hướng dẫn dán tay, một lưới an toàn cuối, không phải đường mặc
định), và từ chối chạy khi `APP_ENV=production`.

Cờ hỗ trợ (`python -m scripts.bootstrap_dev --help`):

| Cờ | Tác dụng |
|---|---|
| `--dry-run` | Chỉ in kế hoạch, không ghi database/file nào |
| `--print-status` | Chỉ đọc và in trạng thái hiện tại (migration/seed/credential), không ghi gì |
| `--no-seed` | Bỏ qua bước seed dev fixture |
| `--no-credential` | Bỏ qua bước bảo đảm sync credential |
| `--force-reseed --yes` | Xoá các dòng seed-managed rồi nạp lại (chỉ đụng dữ liệu do seed sở hữu) |
| `--rotate-credential --yes` | Xoay credential đang active (đòi `APP_ENV=development`); ghi khoá mới qua cùng cơ chế handoff |
| `--credential-output-file PATH` | Ghi khoá thô (nếu vừa cấp/xoay) ra ĐÚNG file này, mode `0600`, không in ra — dùng bởi `dev-reset.sh`/`dev-up.sh` |

| Địa chỉ | Nội dung |
|---|---|
| http://localhost:5173 | **Giao diện chính** |
| http://localhost:8000/docs | Swagger UI — 68 endpoint |
| http://localhost:8000/health | Health check |
| http://localhost:8100/docs | Mini CRM (hệ nguồn) |

**Dữ liệu mẫu có sẵn.** Migration `0019` và `0021` seed sẵn **4 dự án** Vinhomes:
58 phân khu, 1.991 căn, 1.294 giao dịch. Mở lên là có số liệu ngay — không cần
import gì.

### Nạp bảng xếp hạng lần đầu

Trang Ranking sẽ trống cho tới khi có một lần chạy. Chạy lệnh này:

```bash
curl -X POST "http://localhost:8000/api/v1/ranking/run?external_project_id=prj_smc"
```

Hoặc bấm nút **“Tính lại”** trên trang `/ranking`.

> Đăng nhập nay đi qua Keycloak/OIDC thật (§2.8), KHÔNG còn bypass mặc định.
> `DEV_AUTH_BYPASS` mặc định `false` trong `docker-compose.yml` — chỉ đặt
> `true` ở `.env` cục bộ khi cố tình bỏ qua SSO cho một phiên debug, và nó chỉ
> có tác dụng khi `APP_ENV=development` (xem §2.12).

### 2.2 Khởi động bình thường (đã bootstrap rồi)

```bash
./scripts/dev-up.sh            # = make dev-up
```

An toàn tuyệt đối: KHÔNG bao giờ xoá volume, KHÔNG bao giờ xoay credential.
Đòi `.dev-secrets/minicrm_sync_api_key` đã tồn tại (từ một lần `dev-reset.sh`
trước đó) — nếu chưa, dừng lại với hướng dẫn chạy `dev-reset.sh` thay vì tự
cấp credential (tách biệt có chủ đích: "khởi động" không phải "cấp phát").
Script tự chờ healthcheck từng service rồi chạy `bootstrap_dev.py` một lần
(idempotent — chỉ xác nhận migration/seed/credential vẫn đúng, không ghi gì
mới nếu đã đúng).

Nếu chỉ cần khởi động thô, không cần các bước chờ/xác nhận của `dev-up.sh`:

```bash
docker compose up -d
```

cũng an toàn như nhau (không xoá volume, không xoay credential) — Compose tự
phát hiện nội dung `.dev-secrets/minicrm_sync_api_key` không đổi và không
đụng tới container `minicrm` đang chạy.

### 2.3 Build lại sau khi đổi source hoặc `.env`

```bash
docker compose up -d --build --force-recreate
# hoặc chỉ hai service vừa đổi:
docker compose up -d --build --force-recreate api minicrm
```

Volume có tên (`pgdata`, `minicrm_pgdata`, `keycloak_data`, `uploads`) được
GIỮ NGUYÊN qua lệnh này — chỉ container bị dựng lại, không mất dữ liệu.

### 2.4 Dừng toàn bộ, giữ nguyên dữ liệu

```bash
docker compose stop
```

Dừng mọi container của project nhưng giữ nguyên: volume `pgdata` (AbsorpIQ),
`minicrm_pgdata` (Mini CRM), `keycloak_data` (realm/user Keycloak), `uploads`,
container, và network. Khởi động lại đúng các container đó:

```bash
docker compose start
```

`start` chỉ hoạt động khi container còn tồn tại (chưa bị `rm`/`down`).

### 2.5 Gỡ container/network nhưng giữ volume

```bash
docker compose down --remove-orphans
docker compose up -d
```

Khác `stop`: container/network bị xoá và dựng lại từ đầu ở `up` kế tiếp,
nhưng named volume (nơi dữ liệu thật sự nằm) không bị đụng tới.

### 2.6 ⚠️ Hard reset dữ liệu (CHỈ local development)

```bash
./scripts/dev-reset.sh --yes           # = make dev-reset
```

Lệnh này chỉ cho `APP_ENV=development` và database local/dev/test đã được
allowlist. Nó giữ schema, volumes, migration history, Keycloak và credential
đang active; sau `alembic upgrade head`, hai file SQL explicit allowlist sẽ xoá
domain, ingestion, forecast/ranking, audit và local-auth rows. Không dùng
`CASCADE`; bảng public mới hoặc revision sai sẽ làm lệnh dừng trước khi xóa.

**Cảnh báo — đọc trước khi chạy:**
- Xoá rows data của cả Mini CRM và AbsorpIQ; không drop table và không xoá
  `alembic_version`.
- Xoá local-auth rows trong PostgreSQL (`crm_users`, `users`, sessions/tokens,
  audit/settings liên quan). Keycloak là hệ thống ngoài hai schema này và không
  bị reset; đăng nhập JIT có thể tạo lại user theo cấu hình OIDC.
- Giữ `sync_credentials` và `.dev-secrets/minicrm_sync_api_key`; nếu không có
  active credential, script chỉ cảnh báo và không tự cấp credential.
- Với `--seed`, Mini CRM vẫn được seed; bước đồng bộ AbsorpIQ được bỏ qua khi
  không có active credential.
- Các sequences/external IDs không bị reset để giữ chính sách không tái sử dụng
  ID của Mini CRM.
- Dữ liệu cũ **KHÔNG được khôi phục**. Đây không phải lệnh dừng bình thường
  (`docker compose stop`, xem §2.4); chỉ dùng khi thật sự muốn xóa data dev.
- Sao lưu TRƯỚC nếu có dữ liệu dev đáng giữ (xem §2.7).
- Không có `--yes`: script chỉ in kế hoạch, không đụng gì — an toàn để chạy
  thử trước.

### 2.6.1 Xoá business data AbsorpIQ và dựng lại từ Mini CRM (chỉ local dev)

Khi cần làm sạch riêng dữ liệu đích nhưng vẫn giữ schema, migration, user,
settings, Keycloak và sync credential, dùng:

```bash
# Chỉ kiểm tra danh sách/bảo vệ, không ghi
docker compose run --rm -e RUN_MIGRATIONS=false api \
  python -m scripts.clear_absorpiq_data --dry-run

# Một lệnh: alembic upgrade head -> clear business tables ->
# MiniCRM PATCH/transactional outbox/relay -> AbsorpIQ projection
./scripts/dev-reseed-from-minicrm.sh --yes   # = make dev-reseed-from-minicrm
```

Lệnh này chỉ chạy khi `APP_ENV=development`, database/host nằm trong allowlist
local và revision là `0034_expert_ranking_governance`. Nó không dùng `CASCADE`,
không xoá `users`, `refresh_tokens`, `settings`, `sync_credentials` hoặc
`alembic_version`, không chạm database/volume Mini CRM hay Keycloak, và bắt
buộc có `--yes`. Fixture được gửi lại bằng PATCH qua Mini CRM API với
`--refresh-existing`; không có SQL ghi trực tiếp và không xoá/replay outbox cũ.
Nếu migration thêm bảng public mới, script dừng để bảng đó được phân loại
tường minh trước khi cho phép xóa.

### 2.7 Sao lưu trước khi reset

```bash
docker compose exec -T db pg_dump \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  > "backup-absorpiq-$(date +%Y%m%d-%H%M%S).sql"

docker compose exec -T minicrm_db pg_dump \
  -U "$MINICRM_POSTGRES_USER" -d "$MINICRM_POSTGRES_DB" \
  > "backup-minicrm-$(date +%Y%m%d-%H%M%S).sql"
```

Đọc `$POSTGRES_USER`/`$MINICRM_POSTGRES_USER` từ `.env` trước khi chạy (không
gõ tay mật khẩu vào lệnh). Cho một quy trình migrate CÓ chủ đích (không phải
reset), dùng `scripts/migrate.sh` — nó tự sao lưu + xác minh trước khi migrate
(xem §8).

### 2.8 Smoke test đăng nhập

1. Dựng stack (§2.1/§2.2), đợi `keycloak`/`api`/`minicrm` healthy.
2. Mở `http://localhost:5173` (Product) hoặc `http://localhost:5174` (Mini CRM).
3. Đăng nhập qua Keycloak (realm `p100`).
4. Xác nhận callback về đúng app, HTTP không phải lỗi.
5. Xác nhận URL sau callback KHÔNG còn chứa `code=`/`state=`.
6. Xác nhận vai trò/phạm vi dự án đúng kỳ vọng (`GET /api/v1/auth/me` hoặc
   `GET /auth/me`).
7. Dùng cửa sổ Ẩn danh (Incognito) để test một phiên đăng nhập MỚI — tránh
   phiên/cookie cũ che lấp lỗi thật.

### 2.9 Smoke test đồng bộ Mini CRM → AbsorpIQ

`./scripts/dev-reset.sh --yes` đã TỰ chạy bản rút gọn của smoke test này ở
bước cuối (tạo project thật, xác nhận `crm_source_records` trong AbsorpIQ,
thoát mã khác 0 nếu thất bại). Làm lại thủ công (vd. sau `dev-up.sh`, không
qua reset) theo các bước sau:

1. Tạo một project dev-only trong Mini CRM (UI `5174` hoặc `POST /projects`).
2. Xác nhận ghi nghiệp vụ thành công (response `201`).
3. Xác nhận có dòng mới trong `crm_outbox`
   (`docker compose exec -T minicrm_db psql -U minicrm -d minicrm -c "SELECT id, entity, http_status FROM crm_outbox ORDER BY created_at DESC LIMIT 5;"`).
4. Theo dõi log relay: `docker compose logs -f minicrm | grep -i relay`.
5. Xác nhận `http_status` của dòng outbox là `202` (lô mới) hoặc `200`
   (`replayed=true`, lô đã gửi trước đó) — KHÔNG dừng lại ở "thấy 202" mà đi
   tiếp bước 6-8.
6. Poll AbsorpIQ: dòng đó phải có `sync_run_id` thật (không rỗng).
7. Kiểm chiếu bằng `external_id` trong bảng tương ứng của AbsorpIQ
   (`projects`/`areas`/`units`/`deals`) — phải tồn tại đúng bản ghi.
8. Gửi lại đúng lô đó (`POST /outbox/{external_batch_id}/resend` cho lô v1,
   hoặc để relay tự động xử lý cho lô v2) và xác nhận KHÔNG có bản ghi thứ hai
   được tạo (`replayed=true`, cùng `sync_run_id`).

### 2.10 Bảng lỗi thường gặp (auth + sync)

| Lỗi | Ý nghĩa | Lệnh kiểm tra đầu tiên | Cách sửa an toàn | Rủi ro mất dữ liệu |
|---|---|---|---|---|
| `No module named scripts.bootstrap_dev` (hoặc bất kỳ `scripts.*` nào) | Image `api` đang chạy CŨ HƠN lần sửa/thêm file trong `scripts/` gần nhất — `scripts/` KHÔNG bind-mount (chỉ `src`/`alembic`/`data`/`uploads` có), nên `docker compose run`/`up` không tự thấy file mới cho tới khi image được build lại | So `docker image inspect absorptionforecast-backend:dev --format '{{.Created}}'` với `stat -c '%y' scripts/<file>.py` | `docker compose build api` (hoặc `docker compose up -d --build api`) rồi chạy lại lệnh | Không |
| Scripts thiếu trong image dù đã build | `.dockerignore` lỡ thêm dòng loại trừ `scripts/`, hoặc `COPY` trong `Dockerfile` không phải `COPY . .` | `docker compose run --rm api python -c "import pathlib; print(pathlib.Path('/app/scripts').exists())"` | Sửa `.dockerignore`/`Dockerfile` CHỈ nếu bằng chứng cho thấy đúng đây là nguyên nhân — repo hiện tại KHÔNG loại trừ `scripts/` | Không |
| Nghi thiếu `scripts/__init__.py` | KHÔNG phải nguyên nhân ở repo này — `scripts/` là namespace package (PEP 420), mọi script khác (`sync_credentials.py`, `seed_dev.py`) đã chạy được qua `python -m scripts.<tên>` mà không cần `__init__.py`, cả trên host lẫn trong image | `python -m scripts.sync_credentials --help` (host hoặc trong container) | Không tạo `__init__.py` trừ khi có bằng chứng NGƯỢC LẠI (một script cụ thể lỗi import mà lỗi đó biến mất khi thêm file) | Không |
| `401 INVALID_API_KEY` | Khoá gửi lên không khớp `key_hash` nào trong `sync_credentials` | `docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM sync_credentials WHERE revoked_at IS NULL;"` | `./scripts/dev-up.sh` (cấp nếu thiếu, giữ nguyên nếu đã có, tự đưa khoá tới Mini CRM) | Không |
| `403 INSTANCE_MISMATCH` | Khoá hợp lệ nhưng `source_instance_id` trong payload không khớp khoá | So `MINICRM_SOURCE_INSTANCE_ID` (Mini CRM) với `source_instance_id` của khoá (`python -m scripts.sync_credentials list`) | Sửa biến môi trường lệch, KHÔNG đổi tên identity ngầm | Không |
| `422` lỗi hợp đồng (contract validation) | Payload sai schema `crm_sync_v1`/`v2` | Xem `error_code`/`json_path` trong response | Sửa payload phía Mini CRM theo đúng field bắt buộc | Không |
| `sync_credentials` rỗng, hoặc `.dev-secrets/minicrm_sync_api_key` thiếu | Chưa từng bootstrap local hoặc credential handoff bị thiếu | `python -m scripts.bootstrap_dev --print-status`; `ls -la .dev-secrets/` | `./scripts/dev-reset.sh --yes` — chỉ cấp handoff mới khi database chưa có credential active | Không (chỉ tạo mới) |
| `invalid mount config ... bind source path does not exist` khi `up`/`run` chạm `minicrm` | `.dev-secrets/minicrm_sync_api_key` chưa tồn tại — Compose `secrets:` đòi file có SẴN trước khi tạo container | `ls .dev-secrets/minicrm_sync_api_key` | Chạy `./scripts/dev-reset.sh --yes` trước (nó tạo file này trước khi đụng tới `minicrm`) | Không |
| Outbox kẹt ở `http_status=401` | Lô đã gửi lúc chưa có credential hợp lệ — 4xx là ngõ cụt vĩnh viễn theo thiết kế, không tự retry | `SELECT id, entity, http_status FROM crm_outbox WHERE http_status=401;` | Sau khi credential đã đúng: lô v1 dùng `POST /outbox/{id}/resend`; lô v2 (`projects`/`areas`/`units_v2`/`deals_v2`) do vòng relay tự động xử lý lại (điều kiện: `http_status IS NULL OR >= 500` — 401 cũ cần gọi lại `crud.deliver()` một lần thủ công nếu cần phục hồi ngay, xem `minicrm/app/relay.py`) | Không |
| Keycloak `invalid_grant` | Code đã dùng (một lần), hết hạn, hoặc PKCE verifier sai | Kiểm log `api`/`minicrm`: `docker compose logs api \| grep oidc` | Đăng nhập lại từ đầu bằng URL `/auth/login` mới, không tái dùng URL callback cũ | Không |
| Keycloak `not_allowed` / `offline_access` | Scope yêu cầu `offline_access` nhưng client/user chưa được cấp quyền offline token | Kiểm `OIDC_SCOPES`/`MINICRM_OIDC_SCOPES` trong `.env` | Đặt đúng `openid profile email` (KHÔNG thêm `offline_access` trừ khi đã cấu hình Keycloak cho phép) | Không |
| `connection refused` tới database | Service `db`/`minicrm_db` chưa healthy, hoặc sai host/port | `docker compose ps` | Đợi `healthy`, hoặc kiểm `DATABASE_URL`/`MINICRM_DATABASE_URL` đúng cổng ở §2.0 | Không |
| Sai hostname/cổng Docker | Dùng `localhost` bên trong container, hoặc dùng tên service từ host | Xem bảng §2.0 | Trong container: dùng tên service (`db`, `keycloak`, `api`...). Từ host/browser: dùng `localhost` + cổng publish | Không |
| Alembic nhiều head / lệch revision | Hai nhánh migration chưa merge | `docker compose exec api alembic heads` | Viết merge revision, KHÔNG tự sửa `alembic_version` bằng tay | Có nếu sửa tay sai |
| Credential handoff mất nhưng DB còn credential active | File `.dev-secrets/minicrm_sync_api_key` bị thiếu trong khi runtime credential vẫn còn | `python -m scripts.bootstrap_dev --print-status`; `ls -la .dev-secrets/` | Dừng và khôi phục handoff theo quy trình credential đã phê duyệt; `dev-reset.sh` cố ý không tự xoay credential trong trường hợp này | Không |
| Nhiều credential active cho cùng identity | `issue` bị gọi tay nhiều lần ngoài `bootstrap_dev` | `python -m scripts.sync_credentials list --source-instance-id mini-crm-dev` | Trong `APP_ENV=development`, `bootstrap_dev` giữ credential mới nhất và thu hồi phần dư bằng service chính thức; môi trường khác bị từ chối | Không (revoke, không xoá) |

### 2.11 Xử lý bí mật (secrets)

- `.dev-secrets/minicrm_sync_api_key` là nơi DUY NHẤT khoá sync đồng bộ thô
  từng chạm filesystem — mode `700`/`600`, nằm trong `.gitignore`, KHÔNG BAO
  GIỜ commit (`git check-ignore -v .dev-secrets/minicrm_sync_api_key` để tự
  kiểm). `docker-compose.yml` mount nó READ-ONLY vào `minicrm` qua khối
  `secrets:` tại `/run/secrets/minicrm_sync_api_key` — không lộ qua biến môi
  trường container, không lộ qua `docker inspect`/`docker compose config`.
- `.env`/`minicrm/.env` vẫn trong `.gitignore` như trước — `MINICRM_SYNC_API_KEY`
  ở đó giờ chỉ là đường TƯƠNG THÍCH NGƯỢC (host-mode/test), không còn bắt
  buộc phải điền.
- `scripts/bootstrap_dev.py --credential-output-file <path>` ghi khoá thô
  thẳng vào file, KHÔNG in ra stdout/stderr, mode `0600` ngay từ lúc tạo file
  (không có khoảng hở quyền mặc định trước khi bị siết lại).
- Danh sách khoá (`python -m scripts.sync_credentials list`) chỉ hiện
  `key_prefix` (không bí mật, dùng để nhận diện) — không bao giờ hiện `key_hash`
  hay khoá thô.
- Không đưa khoá/mật khẩu vào migration, `Dockerfile`, `docker-compose.yml`
  (chỉ có ĐƯỜNG DẪN tới file bí mật, không phải giá trị), README,
  `pipeline_status.md`, source code, test fixture, hay log.

### 2.12 Cảnh báo production

- `scripts/bootstrap_dev.py`/`dev-reset.sh`/`dev-up.sh` CHỈ dành cho dev cục
  bộ — TỪ CHỐI chạy khi `APP_ENV=production`; `--credential-output-file`
  cùng `--rotate-credential` còn đòi thêm đúng `APP_ENV=development`.
- `.dev-secrets/` là cơ chế handoff DEV-ONLY, không phải secret manager.
  KHÔNG dùng nó để cấp credential production. Production cần một secret
  manager thật và quy trình migrate tường minh (`scripts/migrate.sh`, sao lưu
  trước — xem §8).
- KHÔNG BAO GIỜ commit bí mật dạng plaintext.
- KHÔNG chạy `docker compose down -v` (hay `./scripts/dev-reset.sh --yes`)
  tuỳ tiện — đó là lệnh HUỶ DỮ LIỆU, không phải lệnh dừng bình thường (xem
  §2.4 vs §2.6).

---

## 3. Biến môi trường

### Bắt buộc phải điền

| Biến | Ý nghĩa | Lấy ở đâu |
|---|---|---|
| `LLM_API_KEY` | Khoá OpenAI. Thiếu thì `/health`, ranking và test vẫn chạy; chỉ `/chat` và `/agent/recommendations` lỗi | platform.openai.com |
| `JWT_SECRET` | `openssl rand -hex 32` | tự sinh |
| `POSTGRES_PASSWORD` | Đổi khỏi mặc định | tự đặt |
| `AI_LOG_API_KEY` | Log sử dụng AI cho BTC | link mời của BTC |

### Đáng biết

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `LLM_MODEL` | `gpt-4o-mini` | Model dùng cho cả planner lẫn synthesis |
| `LLM_PROVIDER` | `openai` | |
| `LLM_TEMPERATURE` | `0.7` | |
| `APP_ENV` | `development` | Đặt `production` sẽ **chặn** auto-migration và dev bypass |
| `DEV_AUTH_BYPASS` | `false` trong `.env`, **`true`** cho service `api` trong compose | Bỏ qua auth khi chạy local |
| `DASHBOARD_BUSINESS_VIEWER_TOKEN` | — | Token vai trò *xem* |
| `DASHBOARD_PIPELINE_OPERATOR_TOKEN` | — | Token vai trò *vận hành* (duyệt/từ chối) |
| `DASHBOARD_ADMIN_TOKEN` | — | Token vai trò *quản trị* (config) |
| `DASHBOARD_PROJECT_SCOPE` | — | JSON `{"<token>": ["P-0001"]}` hoặc `{"<token>": "ALL"}` |
| `MINICRM_SYNC_API_KEY` | — | Khoá Mini CRM dùng để đẩy lô đồng bộ sang backend |
| `CLOUDINARY_*` | — | Ảnh bìa dự án. Bỏ trống thì tính năng ảnh tắt, phần còn lại vẫn chạy |
| `LANGCHAIN_TRACING_V2` | `false` | Bật lên mà không có key thật sẽ làm test lỗi 403 |

Danh sách đầy đủ: [`.env.example`](.env.example) — **không bao giờ commit `.env`**.

---

## 4. Câu hỏi mẫu để thử

Gõ vào ô chat ở trang `/ai-agent`, hoặc gọi API:

```bash
curl -X POST 'http://localhost:8000/api/v1/chat?project_id=prj_smc' \
  -H 'Content-Type: application/json' \
  -d '{"message":"Phân khu nào đang bán chậm nhất và tôi nên ưu tiên căn nào?"}'
```

| Câu hỏi | Tool agent sẽ chọn |
|---|---|
| *Phân khu nào của dự án Riverside đang bán chậm nhất và tôi nên ưu tiên căn nào?* | `project_overview` · `compare_areas` · `top_ranked_units` · `area_ranking_risks` |
| *So sánh các phân khu của Smart City theo tốc độ bán.* | `compare_areas` |
| *Top 10 căn nên ưu tiên tuần này?* | `top_ranked_units` |
| *Hiện có bao nhiêu dự án và quy mô từng dự án?* | `portfolio_overview` |
| *Phân khu nào tồn kho nhiều mà bán chậm?* | `inventory_hotspots` |
| *Cơ cấu loại căn của dự án này ra sao?* | `unit_mix_overview` |
| *Chính sách chiết khấu hiện tại là gì?* | `policy_snapshot` |
| *Bao nhiêu phần trăm căn đã được chấm điểm?* | `ranking_coverage` |

Mã dự án dùng để thử: `prj_smc` (Smart City) · `prj_rvs` (Riverside) ·
`prj_op1` (Ocean Park 1) · `prj_tmc` (Times City).

Mỗi câu trả lời kèm `sources[]` ghi rõ tool nào, dữ liệu tính đến lúc nào. Agent
được chỉ thị **chỉ dùng kết quả tool**; khi thiếu dữ liệu nó nói "chưa đủ dữ liệu"
thay vì suy đoán.

---

## 5. Luồng người dùng end-to-end

```
Mini CRM (nhập dữ liệu)  ──lô JSON──▶  Backend (bản sao)
                                          │
                                          ▼
                              Tính đặc trưng → chuẩn hoá [0,1]
                                          │
                              Config trọng số (published)
                                          ▼
                              ranking_scores  +  contributions
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                           ▼
        GET /ranking (chỉ đọc)              POST /agent/recommendations
        → trang /ranking                    → LangGraph → pending_approval
                                                          │
                                            ┌─────────────┴─────────────┐
                                            ▼                           ▼
                                      Từ chối (dừng)              Duyệt (người)
                                                                        │
                                                          Xác nhận riêng ▼
                                                          Chiến dịch ưu tiên
                                                          (đúng MỘT lần)
```

**Vòng phê duyệt của con người là ràng buộc cứng.** Bốn chốt chặn ở tầng API:

| Tình huống | Mã lỗi | HTTP |
|---|---|---|
| Chưa duyệt mà đòi thực thi | `APPROVAL_REQUIRED` | 409 |
| Đã duyệt nhưng thiếu xác nhận riêng | `CONFIRMATION_REQUIRED` | 409 |
| Thực thi lần thứ hai | `ALREADY_EXECUTED` | 409 |
| Hành động ngoài danh sách cho phép | `ACTION_NOT_ALLOWED` | 422 |

Gọi thẳng endpoint cũng không lách được — xem bằng chứng ở
[`eval/results/report.md`](eval/results/report.md).

---

## 6. Cấu trúc thư mục

```
src/
├─ agents/          LangGraph: graph, state, nodes, advisory tools (10 tool chỉ-đọc)
├─ api/             Router FastAPI: ranking · agent · dashboard · sync · files · ops
├─ ranking/         engine.py (hàm thuần, không I/O) · service.py · bands.py
├─ services/        Nghiệp vụ: absorption, sync, reconciliation, dashboard_auth, llm
├─ jobs/            Job chạy nền qua RQ: parse_upload, rank_project, recompute_domain
├─ models/          Bảng SQLAlchemy + schema Pydantic
└─ main.py          Khởi tạo app, đăng ký router, middleware

minicrm/            Mini CRM — hệ nguồn ĐỘC LẬP, không import gì từ src/
frontend/           React + Vite (giao diện chính, 16 trang)
crm-frontend/       Giao diện Mini CRM (React + TS + Tailwind)
alembic/versions/   23 migration
tests/              1.400+ test — theo tầng: api · services · jobs · ranking · migrations
docs/
├─ architecture.md  ★ Sơ đồ kiến trúc (deliverable Gate 2)
├─ ranking/         Workflow động cơ xếp hạng + kế hoạch triển khai
├─ crm/             Hợp đồng đồng bộ, fixture, kiểm thử tuân thủ
└─ product/         PRD · SRS · hướng dẫn GitFlow
eval/results/       ★ Bằng chứng đánh giá (deliverable Gate 2)
presentation/       ★ Kịch bản demo (deliverable Gate 2)
```

---

## 7. Test & lint

```bash
make test-docker        # pytest trong container
make lint-docker        # ruff — đúng như CI
make build-frontend     # kiểm tra frontend build được
```

### Test có đụng database

Bộ test có module `TRUNCATE`/`DELETE` toàn bảng, nên **có một chốt an toàn**:
chạy pytest trỏ vào database dev sẽ bị **từ chối**. Dùng script chuyên dụng —
nó tự tạo database `<POSTGRES_DB>_test` riêng:

```bash
bash scripts/test_db.sh                      # mặc định: test_import_records.py
TEST_TARGET="tests/" bash scripts/test_db.sh # toàn bộ
TEST_TARGET="tests/test_ranking" bash scripts/test_db.sh -v
```

### Frontend

```bash
cd frontend && npm install && npm test       # vitest
```
---

## 8. Migration database

Đổi schema **chỉ đi qua đường này** — `alembic upgrade` trực tiếp bỏ qua bước sao
lưu và bước xác minh:

```bash
make migrate                                 # lên revision mới nhất
make migrate rev=0023_config_publish_stamp   # tới một revision cụ thể
make revision m="mô tả thay đổi"             # tạo revision mới
```

`scripts/migrate.sh` cưỡng chế thứ tự: **sao lưu → kiểm bản sao lưu đọc được →
migrate → xác minh**. Xem [`docs/runbooks/migrations.md`](docs/runbooks/migrations.md).

> 💡 **Đặt tên revision dưới 32 ký tự.** `alembic_version.version_num` là
> `varchar(32)`; id dài hơn sẽ chạy hết migration rồi mới hỏng ở bước ghi phiên
> bản, và container `api` rơi vào vòng lặp khởi động lại.

---

## 9. Lỗi thường gặp

**Container `api` cứ `Restarting`** → xem `docker compose logs api`. Nguyên nhân
hay gặp nhất là migration lỗi (xem ghi chú 32 ký tự ở §8).

**`password authentication failed`** sau khi đổi `POSTGRES_USER`/`POSTGRES_PASSWORD`
→ PostgreSQL chỉ áp dụng hai biến này **lần đầu tạo volume**. Xử lý (⚠️ mất dữ liệu
— đọc cảnh báo đầy đủ ở §2.6 trước khi chạy): quy trình reset ở §2.6.

**`port is already allocated`** (5432/5434/6379/8000/8100/5173/5174/9090) → máy
đang chạy PostgreSQL/Redis/dịch vụ khác trên cùng cổng sẵn (xem bảng cổng đầy đủ
ở §2.0). Tắt dịch vụ kia, hoặc bỏ mục `ports:` trong `docker-compose.yml` — các
service vẫn nối nhau qua tên service.

**Trang `/ranking` trống** → chưa có lần chạy nào. Chạy
`POST /api/v1/ranking/run?external_project_id=prj_smc`.

**403 `PROJECT_OUT_OF_SCOPE`** → token đang bị giới hạn phạm vi dự án. Xem
`DASHBOARD_PROJECT_SCOPE` trong `.env`.

**Lỗi auth (Keycloak) hoặc sync (Mini CRM → AbsorpIQ)** → xem bảng đầy đủ ở
§2.10, gồm `401 INVALID_API_KEY`, `403 INSTANCE_MISMATCH`, outbox kẹt ở `401`,
Keycloak `invalid_grant`/`not_allowed`, và credential mất sau reset volume.

---

## 10. Trạng thái Gate 2

| Deliverable | Trạng thái |
|---|---|
| MVP demo end-to-end | ✅ Chạy được — kịch bản ở `presentation/demo_script.md` |
| Architecture diagram | ✅ `docs/architecture.md` |
| ≥ 10 PR merged | ✅ **21 PR** |
| README | ✅ file này |
| Eval evidence ≥ 5 test case | ✅ **10 test case** ở `eval/results/report.md` |


## 11. Đội ngũ

Team ZeroToZeros — VinUni AI20K Build Phase, Cohort 3.
Mentor: Nguyễn Như Chiến, Nguyễn Thành Vinh.

Quy ước nhánh và commit: [`docs/product/GitFlow_CommitFlow_Guidelines.md`](docs/product/GitFlow_CommitFlow_Guidelines.md).

## 12. License

MIT — sử dụng tự do cho mục đích giáo dục.
