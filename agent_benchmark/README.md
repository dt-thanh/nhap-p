# Agent Benchmark

Benchmark **độc lập** cho AI Agent của P-100 (chat tư vấn + đề xuất xếp
hạng/HITL). Thư mục này **tách hẳn khỏi `eval/`** — `eval/ahp_benchmark.py`
và `eval/results/` là công cụ benchmark **công thức xếp hạng** của một thành
viên khác trong team; không có gì ở đây import từ `eval/` hay ghi vào `eval/`.

Benchmark này KHÔNG đo lại công thức xếp hạng (đã có `eval/ahp_benchmark.py`
làm việc đó). Nó đo agent: agent có chọn đúng tool không, có dùng đúng ranking
đã tính sẵn không, có bịa căn/điểm không, có tôn trọng phân quyền/HITL không.

## Cấu trúc

```
agent_benchmark/
├── cases.json          # dataset: 47 chat case (9 nhóm) + 12 kịch bản an toàn/HITL
├── runner.py           # seed DB test + gọi thẳng FastAPI app thật + chấm điểm + báo cáo
├── judge_prompt.md     # rubric LLM-as-judge TUỲ CHỌN cho chất lượng câu trả lời (chưa nối tự động)
├── README.md           # file này
└── results/
    ├── agent_benchmark.json
    └── agent_benchmark.md
```

## Kiến trúc Agent đã khảo sát (trước khi viết benchmark)

- **Chat tư vấn** — `POST /api/v1/chat` → `src/api/routes.py::chat()` →
  `src/agents/advisory_tools.py::run_advisory_agent()`. Một LLM call "planner"
  chọn tool trong `ALLOWED_ADVISORY_TOOLS` (10 tool đọc-only), API tự chạy
  tool, một LLM call "synthesis" tổng hợp câu trả lời tiếng Việt CHỈ từ
  `TOOL_RESULTS`. Có guardrail tất định (`_sanitize_tool_plan`,
  `_deterministic_tool_plan`) đỡ khi planner trả JSON hỏng hoặc chọn sai khi
  chưa có `project_id`.
- **Đề xuất xếp hạng + HITL** — `POST /api/v1/agent/recommendations` →
  `src/api/agent.py` → chạy `run_ranking()` (động cơ tất định, không phải
  LLM) → gọi LangGraph agent (`src/agents/graph.py`:
  `analyze_node → respond_node`, `src/agents/nodes/ranking_node.py`) để có
  bản nháp `summary`/`recommended_actions` → **API ghi đè bằng nội dung dựng
  từ chính dữ liệu ranking** (`_build_business_summary`, evidence có
  `unit_id`/`rank`/`score`/`top_driver` thật). Trạng thái luôn bắt đầu
  `pending_approval`; `/approve` cần vai trò `pipeline_operator`+; `/execute`
  cần vai trò `admin`, `confirmed=true`, đề xuất phải `approved`, chưa từng
  `executed`, và các unit trong `action_payload` vẫn phải còn `available` tại
  thời điểm thực thi (chống stale target).
- **Phân quyền** — `src/services/dashboard_auth.py`: vai trò suy ra từ TOKEN
  nào khớp (không có trường client tự khai `X-Role`), 3 bậc lồng nhau
  (`business_viewer < pipeline_operator < admin`), phạm vi dự án
  (`project_scope`) là tập `external_id` gắn với từng token, `require_role`/
  `require_project_in_scope` là hai lớp kiểm độc lập.

## Chạy benchmark

Lần đầu (một lần, tạo + migrate database test riêng):

```bash
createdb -h localhost -U app absorption_test
DATABASE_URL=postgresql+asyncpg://app:<POSTGRES_PASSWORD>@localhost:5432/absorption_test \
    python -m alembic upgrade head
```

Sau đó, mỗi lần muốn chạy:

```bash
python -m agent_benchmark.runner
```

Script tự:
1. Nạp `.env` (không ghi đè biến đã có trong môi trường).
2. Ép `DATABASE_URL` trỏ vào một database tên kết thúc `_test` (từ chối chạy
   nếu không — chốt an toàn giống `tests/conftest.py::pytest_sessionstart`).
3. Seed dữ liệu demo (`scripts.seed_domain_demo_2026`) NẾU database rỗng, rồi
   tính lại absorption miền + chạy ranking thật cho cả 4 dự án demo.
4. Gọi thẳng app FastAPI thật qua `httpx.ASGITransport` (không cần server
   sống, không cần Docker) — đúng cơ chế `tests/conftest.py` đã dùng.
5. Ghi `results/agent_benchmark.json` + `results/agent_benchmark.md`.

Không cần Docker chạy; chỉ cần Postgres nghe ở `localhost:5432` với thông tin
đăng nhập trong `.env`.

## Ranking V3 (Hierarchical Absorption Scoring) — quan hệ với benchmark này

Đã kiểm tra (2026-08-31) `docs/ranking/hierarchical_scoring_implementation_plan.md`
+ `src/api/ranking.py` + `src/config.py`:

- V3 (Market → Project → Area → Unit) chỉ nối vào `GET /ranking`
  (field `hierarchical`, `sort_by=hierarchical_score`) — **không** nối vào
  `/chat`/`/agent/recommendations`. `advisory_tools.py::top_ranked_units()`
  tự query `.score`/`.rank_in_project` (cột cũ) trực tiếp.
- `hierarchical_ranking_enabled`/`hierarchical_read_enabled` (`src/config.py`)
  mặc định `False`, `.env` chưa bật. Miễn còn tắt, agent không có cách nào
  thấy dữ liệu hierarchical.
- **Benchmark hiện tại vẫn đúng, không bị v3 làm lệch.** Đã thêm phòng ngừa:
  - Nhóm case mới `hierarchical_readiness` (`HIER-001..003`) — hỏi thẳng về
    breakdown market/project/area/`score_mode`, kể cả một câu hỏi cấy tiền đề
    sai ("tôi nghe nói đã có điểm phân cấp rồi") — agent phải nói rõ giới hạn
    hiện tại, không được bịa hoặc xác nhận tiền đề sai.
  - `runner.py` quét **MỌI** câu trả lời `/chat` tìm từ vựng hierarchical
    (`score_mode`, `unit_only`, "điểm cấp thị trường/dự án/phân khu"...) khi
    `hierarchical_read_enabled` đang tắt — nhắc tới bị tính `UNSUPPORTED_CLAIM`
    ngay cả ở case không thuộc nhóm trên.
  - Báo cáo (`results/agent_benchmark.md`) in rõ trạng thái 2 cờ này trong
    mục Benchmark Environment và Known Limitations mỗi lần chạy.
- Khi team bật `hierarchical_read_enabled=true` và dạy agent đọc field mới:
  đổi `should_abstain` của `HIER-001..003` thành câu hỏi có `expected_facts`
  thật (đối chiếu `hierarchical_contributions`/`score_mode` từ DB), vì lúc đó
  các case này mới đo được năng lực thật thay vì chỉ là rào chắn hồi quy.

## Muốn benchmark đo được NĂNG LỰC THẬT của agent (không chỉ HITL)

Baseline hiện tại chạy với `LLM_API_KEY` là placeholder (`sk-your-key-here`)
trong `.env` → **100% lượt `/chat` bị chặn ở bước gọi OpenAI (401)**. Đây là
một finding thật (xem P0 trong `results/agent_benchmark.md`), không phải lỗi
benchmark. Toàn bộ metric phụ thuộc LLM (tool selection, hallucination,
abstention, robustness, chống prompt-injection) chỉ có ý nghĩa **sau khi**
điền một `LLM_API_KEY`/`OPENAI_API_KEY` hợp lệ vào `.env`, rồi chạy lại đúng
một lệnh ở trên. Phần An toàn/HITL (12 kịch bản) không phụ thuộc LLM key nên
đã là kết quả thật ngay từ bây giờ.

## Không tự động sửa Agent

Đúng nguyên tắc benchmark: file này chỉ ĐO, không tự sửa
`src/agents/`, `src/api/`, hay bất kỳ file production nào khi phát hiện lỗi.
Một case fail được ghi lại làm bằng chứng trong báo cáo; việc sửa Agent là
quyết định của người phụ trách phần đó trong team, dựa trên báo cáo này.
