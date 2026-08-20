# AbsorptionForecast AI Agent

> **Team ZeroToZeros** · VinUni AI20K Build Phase · Cohort 3

[![Gate](https://img.shields.io/badge/Gate%202-MVP-blue)]()
[![PRs](https://img.shields.io/badge/PR%20merged-21-green)]()
[![Tests](https://img.shields.io/badge/tests-1337%20passed-green)]()

---

## 1. Giới thiệu sản phẩm và chức năng:

Một dự án bất động sản có nhiều phân khu, với hàng trăm, hàng nghìn căn đang mở bán. Nhưng bộ phận sale, nhà đâù tư chỉ có thể phân tích, dự đoán theo cảm tính cá nhân.

Vì vậy, bài toán đặt ra là:

“Với nguồn lực có hạn, tuần này đội sale nên ưu tiên căn nào trước?”

Hiện tại, quyết định này thường dựa vào kinh nghiệm cá nhân và các file Excel được cập nhật thủ công. Điều đó dẫn đến ba vấn đề lớn:

- Thiếu nhất quán: mỗi sale có thể đưa ra một thứ tự ưu tiên khác nhau.
- Thiếu khả năng giải thích: khó trả lời rõ ràng vì sao căn A được ưu tiên hơn căn B.
- Không phản ứng kịp với dữ liệu: khi tình trạng căn, giao dịch hoặc tốc độ bán thay đổi, danh sách ưu tiên không tự động cập nhật.

Vì vậy, dự án xây dựng một baseline Ranking Engine để xếp hạng thứ tự ưu tiên rõ ràng, nhất quán kết hợp với AI AGENT có thể giải thích, gợi ý và tư vấn.

| Lớp | Bản chất | Ai làm |
|---|---|---|
| **Xếp hạng** | Công thức **tất định** — tổng có trọng số các đặc trưng đã chuẩn hoá về `[0,1]` | Máy, không có LLM |
| **Tư vấn** | Giải thích bảng xếp hạng, đề xuất hành động | LangGraph agent + LLM |
| **Phê duyệt** | Mọi khuyến nghị phải qua người duyệt mới được thi hành | **Con người** |


📐 Kiến trúc chi tiết + sơ đồ: **[`docs/architecture.md`](docs/architecture.md)**
🧪 Bằng chứng đánh giá: **[`eval/results/report.md`](eval/results/report.md)**

---

## 2. Hướng dẫn chạy

### Các bước

```bash
# 1. Clone
git clone <repo-url> team-ZeroToZeros
cd team-ZeroToZeros

# 2. Tạo .env (BẮT BUỘC — compose sẽ lỗi nếu thiếu)
cp .env.example .env

# 3. Sinh JWT secret rồi dán vào .env
openssl rand -hex 32

# 4. Điền LLM_API_KEY vào .env (xem §3)

# 5. Dựng toàn bộ stack — migration chạy tự động
docker compose up -d --build

# 6. Đợi tới khi api = healthy (KHÔNG phải "Restarting")
docker compose ps
```

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

> Ở local, service `api` đặt `DEV_AUTH_BYPASS=true` (trong `docker-compose.yml`)
> nên gọi API không cần token. Cơ chế này **chỉ có tác dụng khi
> `APP_ENV=development`** và phải tắt khi deploy thật.

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
→ PostgreSQL chỉ áp dụng hai biến này **lần đầu tạo volume**. Xử lý (⚠️ mất dữ liệu):
`docker compose down -v && docker compose up -d`.

**`port is already allocated`** (5432/5433/6379/8000/8100/5173) → máy đang chạy
PostgreSQL/Redis sẵn. Tắt dịch vụ kia, hoặc bỏ mục `ports:` trong
`docker-compose.yml` — các service vẫn nối nhau qua tên service.

**Trang `/ranking` trống** → chưa có lần chạy nào. Chạy
`POST /api/v1/ranking/run?external_project_id=prj_smc`.

**403 `PROJECT_OUT_OF_SCOPE`** → token đang bị giới hạn phạm vi dự án. Xem
`DASHBOARD_PROJECT_SCOPE` trong `.env`.

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
