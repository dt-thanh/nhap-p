## 📦 Project Template

| Hạng mục | Nội dung | Trạng thái |
| --- | --- | --- |
| **Cấu trúc thư mục** | Tách rõ agent / api / services / models / jobs — dễ mở rộng, dễ test | ✅ Sẵn sàng |
| **Code mẫu cốt lõi** | LangGraph agent (state, nodes, tools), FastAPI routes, Pydantic settings & schemas | ✅ Sẵn sàng |
| **Docker** | Dockerfile multi-stage (chạy non-root), `docker-compose.yml` 6 service | ✅ Chạy được bằng 1 lệnh |
| **CI/CD** | GitHub Actions chạy lint + test **trong Docker** | ✅ Sẵn sàng |
| **Logging production** | Log JSON có cấu trúc, `request_id`, tự động che secret/PII | ✅ Sẵn sàng |
| **Queue & scheduler** | Redis + RQ worker + APScheduler chạy job nền tách khỏi API | ✅ Sẵn sàng |
| **Database & migration** | PostgreSQL 15 + Alembic | ⚠️ Khung sẵn, **chưa có migration nào** |
| **Frontend** | React + Vite, proxy sẵn về backend | ⚠️ Khung tối thiểu, chưa có test runner |
| **AI Usage Logging** | Hook tự động cho 6 công cụ AI | ✅ Sẵn sàng |
| **Technical Guidebook** | 10 chương trong `docs/guide/` | ✅ Đọc offline được |
| **Deliverables checklist** | `docs/guide/deliverables/checklist.md` | ✅ Sẵn sàng |

---

## ⚡ Quick Start

### Bước 1: Clone

```bash
git clone <repo-url> team-YOUR_TEAM_NAME
cd team-YOUR_TEAM_NAME

# Nếu bắt đầu dự án mới: xoá git history cũ
rm -rf .git && git init && git add . && git commit -m "feat: khởi tạo dự án từ template"
```

### Bước 2: Cấu hình môi trường

```bash
# BẮT BUỘC — docker compose sẽ báo lỗi nếu thiếu .env
cp .env.example .env

# Sinh JWT secret
openssl rand -hex 32
```

Mở `.env` và điền tối thiểu 4 giá trị:

| Biến | Ý nghĩa | Bắt buộc |
| --- | --- | --- |
| `LLM_API_KEY` | Khoá LLM. Thiếu thì `/health` và test vẫn chạy, chỉ `/api/v1/chat` lỗi | Khi cần gọi LLM |
| `JWT_SECRET` | Dán chuỗi vừa sinh ở trên | ✅ |
| `POSTGRES_PASSWORD` | Đổi khỏi giá trị mặc định | ✅ |
| `AI_LOG_API_KEY` | Key riêng từ link mời của BTC | ✅ |

> `.env` đã nằm trong `.gitignore` — **không bao giờ commit file này**.

### Bước 3: Cài AI Logging Hooks

```bash
# Linux / macOS / Git Bash
bash scripts/setup_hooks.sh

# Windows PowerShell
# powershell -ExecutionPolicy Bypass -File scripts\setup_hooks.ps1
```

Chạy một lần sau khi clone. Script cài git hook `pre-push` để tự gom và nộp AI log.

### Bước 4: Chạy toàn bộ stack

```bash
docker compose up -d --build      # hoặc: make up
docker compose ps                 # kiểm tra 6 service
```

| Địa chỉ | Nội dung |
| --- | --- |
| http://localhost:8000/health | Health check API |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:5173 | Frontend (React + Vite) |

```bash
docker compose logs -f api worker scheduler   # xem log — hoặc: make logs
docker compose down                           # dừng, GIỮ dữ liệu — hoặc: make down
docker compose down -v                        # ⚠️ XOÁ luôn dữ liệu DB — hoặc: make reset
```

**6 service và cách nối nhau**

| Service | Vai trò | Port |
| --- | --- | --- |
| `api` | FastAPI + WebSocket | 8000 |
| `worker` | RQ worker chạy job nền | — |
| `scheduler` | APScheduler, đẩy job theo lịch | — |
| `db` | PostgreSQL 15 | 5432 |
| `redis` | Hàng đợi job | 6379 |
| `frontend` | Vite dev server | 5173 |

`api`, `worker`, `scheduler` **dùng chung một image**, chỉ khác lệnh khởi động. Chúng gọi nhau bằng tên service (`db`, `redis`) qua mạng nội bộ của compose — không cần IP.

### Bước 5: Chạy test

```bash
make test-docker      # pytest trong container
make lint-docker      # ruff — đúng như CI chạy
make build-frontend   # kiểm tra frontend build được
```

### Bước 6: Migration database

```bash
make migrate                                              # alembic upgrade head
docker compose run --rm api alembic revision -m "init schema"
docker compose run --rm api alembic downgrade -1
```

> Hiện chưa có revision nào, nên `make migrate` chạy sạch nhưng chưa tạo bảng nghiệp vụ. Bạn tự tạo revision đầu tiên khi có model.

### Chạy backend ngoài Docker (tuỳ chọn)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Cần PostgreSQL + Redis chạy sẵn; sửa DATABASE_URL / REDIS_URL trong .env
# trỏ về localhost thay vì tên service db / redis
uvicorn src.main:app --reload --port 8000
```

### Bước 7: Đọc hướng dẫn

📖 Mở **[Technical Guidebook](https://phoenix.note.transformerlabs.ai/technical-book)** hoặc `docs/guide/chapter-01.md` và làm theo từng chương.

---

## 🔄 CI/CD — giải thích cho người mới

### CI là gì?

**CI (Continuous Integration)** = mỗi lần bạn push code hoặc mở Pull Request, GitHub tự động build và kiểm tra code giúp bạn. Nếu có lỗi, bạn biết trong vài phút thay vì đến lúc demo mới phát hiện.

Bạn không phải làm gì thêm — chỉ cần push, GitHub tự chạy.

### Repo này CI làm gì?

File cấu hình: `.github/workflows/ci.yml`. Chạy khi push lên `main`/`develop` hoặc mở PR vào `main`. Có **2 job chạy song song**:

**Job `backend`** — 6 bước:

1. Tạo `.env` từ `.env.example` với giá trị dành riêng cho CI (không phải secret thật).
2. Build image backend bằng Docker.
3. **Lint** bằng `ruff` — bắt lỗi cú pháp, import thừa, đặt tên sai chuẩn.
4. **Test** bằng `pytest` — chạy toàn bộ `tests/`.
5. **Kiểm tra migration** — `alembic upgrade head` phải chạy sạch trên DB trống.
6. Nếu có bước nào fail → in 200 dòng log cuối để bạn xem nguyên nhân, rồi dọn sạch container.

**Job `frontend`** — cài dependency bằng `npm ci` và chạy `npm run build`. Nếu frontend không build được, CI báo đỏ.

### Vì sao chạy CI trong Docker?

Đây là điểm quan trọng nhất và cũng dễ hiểu nhất:

> **"Chạy được trên máy tôi" không có nghĩa là chạy được trên máy bạn.**

Máy bạn có thể đang dùng Python 3.12, máy bạn cùng nhóm dùng 3.10, GitHub runner lại có sẵn thứ khác. Kết quả: test pass ở máy này, fail ở máy kia, mất cả buổi để tìm nguyên nhân.

Docker đóng gói **cùng một môi trường** — cùng Python 3.11, cùng phiên bản thư viện, cùng hệ điều hành nền. CI chạy đúng image mà bạn chạy ở local, nên:

- Test pass ở CI thì gần như chắc chắn pass ở máy bạn, và ngược lại.
- Không phải cài Python/Node lên runner rồi cầu mong phiên bản khớp.
- Image mà CI kiểm tra chính là image sẽ đem đi deploy.

### Local khác CI ở đâu?

| | Local (dev) | CI |
| --- | --- | --- |
| `.env` | Bạn tự tạo, có key thật | CI tự sinh từ `.env.example`, key giả |
| `APP_ENV` | `development` | `test` |
| `LOG_FORMAT` | `console` (có màu, dễ đọc) | `json` (máy đọc được) |
| Hot reload | Có (`--reload`, mount `./src`) | Không |
| Database | Volume `pgdata` giữ dữ liệu giữa các lần chạy | Luôn trống, xoá sau khi xong |
| LangSmith | Bật nếu bạn có key | Tắt (tránh spam lỗi 403) |

### Chạy đúng các kiểm tra của CI ở máy mình

```bash
make lint-docker      # giống bước Lint của CI
make test-docker      # giống bước Test của CI
make migrate          # giống bước kiểm tra migration
make build-frontend   # giống job frontend
```

Chạy 4 lệnh này trước khi push thì gần như không bao giờ bị CI báo đỏ.

### CD (deploy) — chưa có

Repo **chưa có** workflow tự động deploy. Việc deploy lên Render/Fly.io/Vercel bạn tự cấu hình. Image Docker đã sẵn sàng nên chỉ cần trỏ nền tảng vào `Dockerfile`.

---

## 📊 AI Usage Logging

### Là gì?

BTC yêu cầu mỗi đội nộp bằng chứng về cách dùng AI trong quá trình phát triển (Deliverable #4). Repo cài sẵn hook để việc này diễn ra tự động — bạn không phải ghi tay.

### Công cụ được hook sẵn

| Tool | Cơ chế | Config |
| --- | --- | --- |
| Claude Code | Hook trong settings | `.claude/settings.json` |
| Cursor | Hook | `.cursor/hooks.json` |
| OpenAI Codex CLI | Hook | `.codex/hooks.json` |
| Gemini CLI | Hook | `.gemini/settings.json` |
| GitHub Copilot | Hook | `.github/hooks/hooks.json` |
| Antigravity IDE | Quét transcript khi `git push` | `.agents/` |

Các công cụ web (ChatGPT…) không hook được, log thủ công:

```bash
bash scripts/_pyrun.sh scripts/log_manual.py --tool chatgpt --prompt "Câu bạn đã hỏi"
```

### Log những gì?

Ghi vào `.ai-log/session.jsonl`, mỗi dòng một sự kiện:

- Tên công cụ, loại sự kiện, thời điểm, thông tin git (branch, commit, email).
- Prompt của bạn — **cắt còn 1000 ký tự đầu**.
- Tóm tắt phản hồi và tool call (lệnh đã chạy, file đã sửa).

Khi `git push`, hook `pre-push` tự gom log và gửi lên server chấm điểm (`AI_LOG_SERVER`).

### Về bảo mật

`scripts/log_hook.py` **tự động che secret trước khi ghi**: khoá dạng `sk-...`, JWT, chuỗi `password=`/`token=`, và mật khẩu trong connection string đều bị thay bằng `«redacted»`.

Dù vậy, vẫn phải tự giữ kỷ luật:

- **Không commit `.env`** — đã có trong `.gitignore`, đừng dùng `git add -f`.
- **Không dán key thật vào prompt** cho AI tool.
- Nội dung `.ai-log/*.jsonl` cũng đã được gitignore.

> ⚠️ Nếu lỡ commit secret, đổi key ngay. Xoá commit không đủ — key vẫn nằm trong git history.

---

## 📁 Cấu trúc dự án

```
├── src/                      # Backend Python
│   ├── main.py               # 🚀 Entry point API — FastAPI app
│   ├── worker.py             # 🔨 Entry point RQ worker
│   ├── scheduler.py          # 🕑 Entry point scheduler (cron)
│   ├── config.py             # ⚙️ Pydantic Settings — đọc .env
│   ├── logging_config.py     # 📝 Log JSON + che secret + request_id
│   ├── middleware.py         #    Gắn X-Request-ID cho mỗi request
│   ├── task_queue.py         #    Kết nối Redis + hàng đợi job
│   ├── agents/               # 🧠 LangGraph Agent
│   │   ├── graph.py          #    State graph (nodes + edges)
│   │   ├── state.py          #    State schema (TypedDict)
│   │   ├── nodes/            #    Node functions
│   │   └── tools/            #    Agent tools
│   ├── api/routes.py         # 🌐 FastAPI endpoints
│   ├── models/schemas.py     # 📋 Pydantic schemas
│   ├── services/llm.py       # 🔧 LLM client + log lượt gọi
│   └── jobs/forecast.py      # ⏱️ Job chạy nền
├── frontend/                 # ⚛️ React + Vite
│   ├── Dockerfile            #    4 stage: deps → dev → build → prod(nginx)
│   └── vite.config.js        #    Proxy /api, /ws → api:8000
├── alembic/                  # 🗄️ Database migrations (chưa có revision)
├── docker/entrypoint.sh      # 🐳 Chạy migration rồi khởi động service
├── tests/                    # 🧪 pytest — 5 test mẫu
├── scripts/                  # 🔌 AI logging hooks
│   ├── log_hook.py           #    Auto-log + che secret
│   ├── log_antigravity.py    #    Quét transcript Antigravity
│   ├── log_manual.py         #    Log thủ công cho ChatGPT
│   ├── submit_log.py         #    Nộp log khi git push
│   └── setup_hooks.sh        #    Cài hook (chạy 1 lần)
├── docs/
│   ├── guide/                # 📖 Technical Guidebook — 10 chương
│   └── product/              # 📄 Tài liệu sản phẩm của đội (PRD, SRS)
├── .ai-log/                  # 📊 AI usage log (tự sinh, đã gitignore)
├── eval/ presentation/       # 📊🎤 Evaluation & Demo Day
├── Dockerfile                # 🐳 Backend — api/worker/scheduler dùng chung
├── docker-compose.yml        # 🐙 6 service
├── Makefile                  # 🛠 Lệnh tắt: up, down, test-docker, migrate…
├── .github/workflows/ci.yml  # ⚡ CI: backend + frontend
├── ARCHITECTURE.md           # 📐 Ghi chú kiến trúc
├── JOURNAL.md WORKLOG.md     # 📓 Deliverable #8, #9
└── README_boilerplate.md     # 📝 Mẫu README cho đội của bạn
```

## 🛠 Tech Stack

| Layer | Công nghệ | Phiên bản thực tế |
| --- | --- | --- |
| AI Agent | LangGraph + LangChain | 1.2 / 1.3 |
| Backend | FastAPI + Uvicorn (async) | 0.141 / 0.52 |
| LLM | OpenAI (mặc định `gpt-4o-mini`) | qua `langchain-openai` |
| Frontend | React + Vite | 18 / 6 |
| Database | PostgreSQL + asyncpg + Alembic | 15 |
| Queue | Redis + RQ | 7 / 2.10 |
| Scheduler | APScheduler | 3.11 |
| Logging | structlog (JSON) | 26 |
| DevOps | Docker + GitHub Actions | Python 3.11, Node 20 |
| Testing | pytest + pytest-asyncio | 9 / 1.4 |

## 📚 Technical Guidebook — 10 chương

Toàn bộ nằm ở `docs/guide/chapter-01.md` … `chapter-10.md`, đọc offline bằng VS Code / Obsidian / GitHub UI.

| Chương | Nội dung |
| --- | --- |
| 1 | Lời mở đầu — mục tiêu, cách dùng |
| 2 | Khởi tạo dự án — clone, setup, git workflow |
| 3 | Thiết kế kiến trúc — 3-tier, diagram, ADR |
| 4 | **LangGraph Agent** — state, nodes, edges, tools, RAG |
| 5 | FastAPI — routes, validation, error handling, streaming |
| 6 | Giao diện |
| 7 | DevOps — Docker, CI/CD, deploy, logging |
| 8 | Kiểm thử — unit test, integration test, RAGAS |
| 9 | Demo Day — 10 deliverables, checklist |
| 10 | Tài nguyên — khoá học, docs, BMAD method |

📖 **Đọc online:** [phoenix.note.transformerlabs.ai/technical-book](https://phoenix.note.transformerlabs.ai/technical-book) — đăng nhập bằng GitHub (account đã được BTC mời vào org), chọn tab **Technical Book**.

## 📋 10 Deliverables cho Demo Day

Checklist đầy đủ: `docs/guide/deliverables/checklist.md`

| # | Deliverable | Vị trí | Template có sẵn |
| --- | --- | --- | :---: |
| 1 | Source Code | `src/` | ✅ |
| 2 | README.md | Copy `README_boilerplate.md` → `README.md` | ✅ |
| 3 | Architecture Diagram | `ARCHITECTURE.md` + `docs/guide/architecture/` | ✅ |
| 4 | AI Logs | LangSmith (3 biến env) + `.ai-log/` tự động | ✅ |
| 5 | Live URL | Tự deploy (Render / Fly.io / Vercel) | ⚡ Dockerfile sẵn |
| 6 | Video Demo | `presentation/` | 📝 Bạn tự làm |
| 7 | Pitch Deck | `presentation/` | 📝 Bạn tự làm |
| 8 | Development Journal | `JOURNAL.md` | ✅ |
| 9 | Worklog | `WORKLOG.md` | ✅ |
| 10 | Evaluation Evidence | `eval/` | 📝 Bạn tự làm |

## ✅ Demo Day Submission Checklist

Chạy lần lượt trên máy sạch (đã `docker compose down -v`) trước khi nộp.

**Chạy được**

- [ ] `cp .env.example .env` và điền đủ `LLM_API_KEY`, `JWT_SECRET`, `POSTGRES_PASSWORD`, `AI_LOG_API_KEY`
- [ ] `docker compose up -d --build` lên đủ 6 service, `docker compose ps` không có service nào `unhealthy`
- [ ] `curl localhost:8000/health` trả `{"status":"ok",...}`
- [ ] http://localhost:8000/docs mở được và liệt kê đúng endpoint của đội
- [ ] http://localhost:5173 mở được và gọi được backend
- [ ] `make build-frontend` build thành công
- [ ] `docker compose logs api worker scheduler` không có `ERROR` bất thường

**Chất lượng code**

- [ ] `make test-docker` — toàn bộ test pass
- [ ] `make lint-docker` — `All checks passed!`
- [ ] `make migrate` chạy sạch trên DB trống *(lưu ý: repo hiện chưa có revision nào — đội phải tự tạo)*
- [ ] CI trên GitHub xanh cả 2 job: `backend` và `frontend`
- [ ] Đọc lại `.github/workflows/ci.yml`, giải thích được CI làm gì khi bị hỏi

**Bảo mật & tài liệu**

- [ ] `git ls-files | grep -E "^\.env$"` không ra kết quả — `.env` chưa từng bị commit
- [ ] Không có key thật trong `.env.example`, code, hay log
- [ ] README mô tả đúng repo: lệnh chạy, cấu trúc thư mục, biến môi trường đều khớp thực tế
- [ ] `README_boilerplate.md` đã được đổi thành README của đội (Deliverable #2)

**AI usage logging (Deliverable #4)**

- [ ] `bash scripts/setup_hooks.sh` đã chạy, `.git/hooks/pre-push` tồn tại
- [ ] `.ai-log/session.jsonl` có dữ liệu và nội dung đã được che secret
- [ ] `git push` chạy trót lọt bước submit log
- [ ] LangSmith đã bật (`LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, `LANGCHAIN_TRACING_V2`) nếu đội dùng

**Hồ sơ nộp**

- [ ] `JOURNAL.md` và `WORKLOG.md` cập nhật tới ngày nộp
- [ ] `eval/` có bằng chứng đánh giá, `presentation/` có video demo + pitch deck
- [ ] Live URL deploy được và truy cập được *(repo chỉ có CI, phần deploy đội tự cấu hình)*
- [ ] `git status` sạch, đã push nhánh cuối cùng lên remote

---

## 🩺 Lỗi thường gặp

**1. `docker compose up` báo thiếu `.env`**
Chưa chạy `cp .env.example .env`. Compose đọc `.env` cho cả biến `${VAR}` lẫn biến môi trường trong container.

**2. `password authentication failed for user ...` sau khi đổi `POSTGRES_USER`/`POSTGRES_PASSWORD`**
PostgreSQL chỉ áp dụng hai biến này **lần đầu tạo volume**. Đổi trong `.env` khi volume đã tồn tại thì DB vẫn giữ user/password cũ. Xử lý (⚠️ mất dữ liệu):

```bash
docker compose down -v && docker compose up -d
```

**3. `port is already allocated` (5432 / 6379 / 8000 / 5173)**
Máy bạn đang chạy PostgreSQL/Redis sẵn, hoặc một stack compose khác. Tắt dịch vụ kia, hoặc bỏ mục `ports:` của `db`/`redis` trong `docker-compose.yml` — các service backend vẫn kết nối được qua tên service.

**4. `docker compose run api ...` chạy migration mỗi lần**
Service `api` đặt `RUN_MIGRATIONS=true`, lệnh one-off kế thừa biến này. Thêm `-e RUN_MIGRATIONS=false` để bỏ qua — các target `make test-docker` / `make lint-docker` đã xử lý sẵn.

**5. Test báo lỗi 403 của LangSmith**
`.env` đang bật `LANGCHAIN_TRACING_V2=true` với key placeholder. Đặt `false` nếu chưa có key thật.

**6. `pip install -e ".[dev]"` báo lỗi**
Repo **không dùng** `pyproject.toml`. Cài bằng `pip install -r requirements.txt`.

---

## 📝 Notes — những điểm cần biết về repo này

Ghi rõ để bạn không mất thời gian tìm thứ không tồn tại:

1. **Repo đang chứa dự án thật của một đội.** `docs/product/` có PRD và SRS của "AbsorptionForecast AI Agent", `frontend/` và `src/jobs/` đã dựng theo bài toán đó. Nếu bạn dùng làm template cho đề tài khác, xoá hoặc thay nội dung `docs/product/`.
2. **Chưa có migration nào.** `alembic/versions/` trống. `make migrate` chạy sạch nhưng chưa tạo bảng — bạn tự viết revision đầu tiên.
3. **Frontend chưa có test runner.** `package.json` chỉ có `dev`, `build`, `preview`; chưa cài Vitest. `npm test` sẽ báo lỗi. CI chỉ kiểm tra frontend build được.
4. **Chưa có CD.** Chỉ có CI (build + test). Deploy phải tự cấu hình.
5. **`src/jobs/forecast.py` là stub.** Mới là điểm nối hạ tầng để xác nhận worker nhận được job; chưa có logic nghiệp vụ.
6. **`prophet` trong `requirements.txt` khá nặng** (kéo theo pandas, matplotlib, cmdstanpy). Nếu đề tài của bạn không dự báo chuỗi thời gian, xoá dòng đó để build nhanh hơn.
7. **`make test` / `make lint` chạy trên máy host**, cần bạn tự cài `requirements.txt`. Bản Docker là `make test-docker` / `make lint-docker`.

## 🔗 Liên kết

- 📖 **Technical Guidebook:** [phoenix.note.transformerlabs.ai/technical-book](https://phoenix.note.transformerlabs.ai/technical-book)
- 🏫 **AI20K Program:** VinUni AI20K Build Phase
- 👨‍🏫 **Mentor:** Đặng Hải Lộc

## 📄 License

MIT — Sử dụng tự do cho mục đích giáo dục.
