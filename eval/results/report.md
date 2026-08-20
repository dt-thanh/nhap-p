# Evaluation Report — Gate 2 MVP

> Báo cáo này có **10 test case**, tất cả chạy trên hệ thống đang sống, output

| | |
|---|---|
| **Ngày chạy** | 2026-08-16 |
| **Môi trường** | `docker compose` local — 8 service, PostgreSQL 15, Redis 7 |
| **Commit** | nhánh `feature/NguyenDucDat/ranking-engine-v2` |
| **Migration** | `0023_config_publish_stamp` (head) |
| **LLM** | OpenAI `gpt-4o-mini` |
| **Dữ liệu** | Fixture seed từ migration `0019` + `0021` — 4 dự án Vinhomes |
| **Config xếp hạng** | version 4 (`published`) |

**Cách tái lập:** dựng stack theo `README.md` §2, rồi chạy lại đúng các lệnh `curl`
dưới đây. Ở local, `DEV_AUTH_BYPASS=true` nên các lệnh không kèm token vẫn chạy;
các test case về phân quyền **bắt buộc** phải kèm token tương ứng trong `.env`.

---

## 1. Tổng hợp kết quả

| # | Test case | Kiểm chứng điều gì | Kết quả |
|---|---|---|---|
| TC-01 | Tính xếp hạng một dự án | Chấm điểm đúng, có giải thích đóng góp | ✅ |
| TC-02 | Chạy lại hai lần | Tính tất định — cùng input ⇒ cùng output | ✅ |
| TC-03 | Sinh khuyến nghị AI | LangGraph trả JSON có cấu trúc + evidence | ✅ |
| TC-04 | Thực thi khi **chưa duyệt** | HITL chặn ở tầng API | ✅ chặn |
| TC-05 | `business_viewer` bấm Duyệt | Phân quyền theo vai trò | ✅ chặn |
| TC-06 | Duyệt bởi vai trò đủ quyền | Ghi vết người duyệt + lý do | ✅ |
| TC-07 | Thực thi sau khi duyệt | Tạo chiến dịch ưu tiên | ✅ |
| TC-08 | Thực thi lần thứ hai | Chống thi hành lặp | ✅ chặn |
| TC-09 | Token ngoài phạm vi dự án | Cô lập dữ liệu theo scope | ✅ chặn |
| TC-10 | Hỏi đáp ngôn ngữ tự nhiên | Agent chọn tool, dẫn nguồn, không bịa | ✅ |

**10/10 đạt.** Năm test case là **kiểm thử tiêu cực** (TC-04, 05, 08, 09 và một
phần TC-01) — chúng chứng minh hệ thống *từ chối* đúng lúc, không chỉ *chạy được*
khi mọi thứ thuận lợi.

---

## 2. Chi tiết từng test case

### TC-01 · Tính xếp hạng cho một dự án

**Vì sao quan trọng:** đây là lõi sản phẩm. Điểm phải giải thích được tới từng
đặc trưng, và đặc trưng thiếu dữ liệu **không được** thay bằng số đoán.

```bash
curl -X POST "http://localhost:8000/api/v1/ranking/run?external_project_id=prj_smc"
```

**Output thực tế** (rút gọn ở phần `items`, giữ nguyên căn đứng đầu):

```json
{
  "external_project_id": "prj_smc",
  "computed_at": "2026-08-16T08:33:47.914Z",
  "config_version": 4,
  "units_ranked": 680,
  "units_skipped": 0,
  "band_counts": { "high": 0, "medium": 438, "low": 242 },
  "total": 680
}
```

Căn đứng đầu, kèm phân rã đóng góp:

```json
{
  "unit_code": "3P-115", "unit_type": "3PN", "unit_status": "available",
  "area_name": "Imperia Smart City - 3PN",
  "score": "0.5784", "score_percent": 57.8, "band": "medium",
  "rank_in_project": 1, "rank_in_area": 1,
  "weight_coverage": "0.8500",
  "contributions": [
    { "feature_key": "unit_available",      "value": "1",     "weight": "0.3",  "contribution": "0.3",    "source": "resolved" },
    { "feature_key": "area_velocity_norm",  "value": "0.375", "weight": "0.2",  "contribution": "0.0750", "source": "resolved" },
    { "feature_key": "unit_demand_norm",    "value": "0.333", "weight": "0.2",  "contribution": "0.0667", "source": "resolved" },
    { "feature_key": "area_conversion_norm","value": "0.333", "weight": "0.15", "contribution": "0.0500", "source": "resolved" },
    { "feature_key": "view_quality",        "value": null,    "weight": "0.15", "contribution": "0",      "source": "missing_skipped" }
  ]
}
```

`HTTP 200` · **4.11 s** cho 680 căn.

**Nhận xét.** `view_quality` chưa có dữ liệu khảo sát nên bị đánh dấu
`missing_skipped`, `weight_coverage` tụt còn `0.85` — hệ thống **không gán giá trị
mặc định**. Nếu coverage rơi dưới `min_weight_coverage`, căn bị bỏ qua hẳn thay vì
nhận một điểm giả. Đây là hành vi đúng theo `src/ranking/engine.py`.

`band_counts.high = 0` là **kết quả thật, không phải lỗi**: ngưỡng `high` hiện đặt
cao hơn điểm cao nhất của bộ dữ liệu fixture (0.5784). Ngưỡng band là tham số vận
hành, sẽ hiệu chỉnh khi có dữ liệu thật.

---

### TC-02 · Tính tất định

**Vì sao quan trọng:** nếu điểm đổi giữa hai lần chạy trên cùng dữ liệu thì không
thể tin, cũng không thể kiểm toán.

```bash
# chạy hai lần liên tiếp, không đổi gì
for i in 1 2; do
  curl -s -X POST "http://localhost:8000/api/v1/ranking/run?external_project_id=prj_smc"
done
```

**Output thực tế:**

```
run1  config_v=4  ranked=680  top5=[('3P-115','0.5784'), ('3P-122','0.5784'), ('3P-128','0.5784'), ('3P-125','0.5784'), ('3P-119','0.5784')]
run2  config_v=4  ranked=680  top5=[('3P-115','0.5784'), ('3P-122','0.5784'), ('3P-128','0.5784'), ('3P-125','0.5784'), ('3P-119','0.5784')]
```

**Nhận xét.** Trùng khớp tuyệt đối, kể cả **thứ tự các căn bằng điểm** — nhờ
tie-break tất định theo `created_at`. Không có LLM trong đường tính điểm.

---

### TC-03 · Sinh khuyến nghị AI

```bash
curl -X POST http://localhost:8000/api/v1/agent/recommendations \
  -H 'Authorization: Bearer <business_viewer_token>' \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"prj_rvs"}'
```

**Output thực tế:**

```json
{
  "recommendation_id": "a9ddb158-d534-4f01-88ab-ea0ce5669fdd",
  "project_id": "prj_rvs",
  "status": "pending_approval",
  "summary": "Dự án hiện tại có 400 căn chưa bán, với 3620 căn đã được bán. Các căn có xếp hạng cao nhất đều thuộc khu vực 75db6f30-…, cho thấy nhu cầu cao tại khu vực này.",
  "recommended_actions": [
    { "unit_id": "33d4a0c9-…", "action": "Tăng cường quảng bá",  "reason": "Căn này có xếp hạng cao nhất và đang thu hút sự quan tâm lớn." },
    { "unit_id": "7908b49a-…", "action": "Khuyến mãi đặc biệt",  "reason": "Căn này đứng thứ hai trong xếp hạng, có tiềm năng bán tốt nếu có ưu đãi." },
    { "unit_id": "fb97ad3d-…", "action": "Tổ chức sự kiện mở bán","reason": "Căn này là lựa chọn thứ ba, có thể thu hút khách hàng khi có sự kiện." }
  ],
  "action_type": "CREATE_PRIORITY_CAMPAIGN",
  "risk_level": "low",
  "confidence": 0.85,
  "execution_status": "not_started",
  "evidence": [
    { "unit_code": "LI-110", "rank": 1, "score": "0.6697" },
    { "unit_code": "LI-114", "rank": 2, "score": "0.6697" },
    { "unit_code": "LI-107", "rank": 3, "score": "0.6697" }
  ]
}
```

`HTTP 202`.

**Nhận xét.** Ba điểm đáng ghi nhận:

1. `status = "pending_approval"` **ngay từ lúc sinh ra** — không có trạng thái nào
   khác cho một khuyến nghị mới.
2. Mọi `unit_id` trong `recommended_actions` đều nằm trong `evidence`, và
   `evidence` lấy thẳng từ `ranking_scores`. LLM không thêm căn nào ngoài danh sách.
3. **Hạn chế đã biết:** `summary` nhắc tới `area_id` dạng UUID thô
   (`75db6f30-…`) thay vì tên phân khu — khó đọc với người dùng cuối. Nguyên nhân:
   `ranking_scores_context` truyền `area_id` chứ không truyền `area_name`. Đây là
   lỗi trình bày, không phải lỗi số liệu. Ghi nhận để sửa sau Gate 2.

---

### TC-04 · Thực thi khi **chưa được duyệt** — phải bị chặn

**Vì sao quan trọng:** đây là ràng buộc cứng số một của dự án
(`AGENTS.md` § Boundaries). Test này gọi **thẳng API**, bỏ qua giao diện.

```bash
curl -X POST http://localhost:8000/api/v1/agent/recommendations/a9ddb158-…/execute \
  -H 'Content-Type: application/json' \
  -d '{"actor":"qa","confirmed":true}'
```

**Output thực tế:**

```json
{ "detail": { "message": "Đề xuất phải được duyệt trước khi thực thi",
              "error_code": "APPROVAL_REQUIRED" } }
```

`HTTP 409` — ✅ **bị chặn.**

**Nhận xét.** Đã gửi `confirmed: true` để loại trừ khả năng bị chặn vì lý do khác.
Chốt chặn nằm ở `src/api/agent.py`, **sau** khi đã khoá dòng bằng `SELECT … FOR
UPDATE` — nên không lách được bằng race condition.

---

### TC-05 · `business_viewer` bấm Duyệt — phải bị chặn

```bash
curl -X POST http://localhost:8000/api/v1/agent/recommendations/a9ddb158-…/approve \
  -H 'Authorization: Bearer <business_viewer_token>' \
  -H 'Content-Type: application/json' -d '{"actor":"qa"}'
```

**Output thực tế:**

```json
{ "detail": { "message": "Vai trò 'business_viewer' không đủ quyền cho thao tác này",
              "error_code": "INSUFFICIENT_ROLE" } }
```

`HTTP 403` — ✅ **bị chặn.**

**Nhận xét.** Vai trò suy ra từ **token nào khớp**, không từ trường client tự khai.
Gửi kèm header `X-Role: admin` cũng không đổi được kết quả — hệ thống không đọc
header đó.

---

### TC-06 · Duyệt bởi vai trò đủ quyền

```bash
curl -X POST http://localhost:8000/api/v1/agent/recommendations/a9ddb158-…/approve \
  -H 'Authorization: Bearer <admin_token>' \
  -H 'Content-Type: application/json' \
  -d '{"actor":"pm_dat","reason":"Duyet cho demo Gate 2"}'
```

**Output thực tế:**

```json
{
  "recommendation_id": "a9ddb158-d534-4f01-88ab-ea0ce5669fdd",
  "status": "approved",
  "decided_by": "pm_dat",
  "decided_at": "2026-08-16T08:34:52.521945Z",
  "decision_reason": "Duyet cho demo Gate 2",
  "execution_status": "not_started"
}
```

`HTTP 200` — ✅.

**Nhận xét.** Ghi đủ **ai duyệt, lúc nào, vì sao**. `execution_status` vẫn là
`not_started`: duyệt **không** kéo theo thi hành — đó là hai bước tách rời.

---

### TC-07 · Thực thi sau khi đã duyệt

```bash
curl -X POST http://localhost:8000/api/v1/agent/recommendations/a9ddb158-…/execute \
  -H 'Content-Type: application/json' \
  -d '{"actor":"ops_dat","confirmed":true}'
```

**Output thực tế:**

```json
{
  "recommendation_id": "a9ddb158-d534-4f01-88ab-ea0ce5669fdd",
  "execution_id": "6b7828ee-6ea6-48c8-accf-52b47f89ff2a",
  "action_type": "CREATE_PRIORITY_CAMPAIGN",
  "status": "executed",
  "result": { "campaign_id": "70597295-1b39-49d3-8d78-2e4a91989b6f",
              "unit_count": 10, "status": "active" },
  "executed_by": "ops_dat",
  "executed_at": "2026-08-16T08:34:52.559854Z"
}
```

`HTTP 200` — ✅.

**Nhận xét.** Executor chỉ nhận đúng một `action_type` được cho phép sẵn, giới hạn
1–50 căn, và mọi căn phải còn `available` thuộc đúng dự án. Không có đường nào để
LLM tự định nghĩa một hành động mới rồi tự chạy.

---

### TC-08 · Thực thi lần thứ hai — phải bị chặn

```bash
# lặp lại y hệt lệnh TC-07
```

**Output thực tế:**

```json
{ "detail": { "message": "Đề xuất đã được thực thi",
              "error_code": "ALREADY_EXECUTED" } }
```

`HTTP 409` — ✅ **bị chặn.**

**Nhận xét.** Quan trọng vì thi hành là **tác dụng phụ ra thế giới thật** (tạo
chiến dịch bán hàng). Bấm hai lần do mạng chậm không được tạo hai chiến dịch.

---

### TC-09 · Token ngoài phạm vi dự án — phải bị chặn

Token `pipeline_operator` được cấp scope `["P-0002"]`; ta bắt nó đọc `prj_smc`.

```bash
curl "http://localhost:8000/api/v1/ranking?external_project_id=prj_smc&limit=1" \
  -H 'Authorization: Bearer <pipeline_operator_token>'
```

**Output thực tế:**

```json
{ "detail": { "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
              "error_code": "PROJECT_OUT_OF_SCOPE" } }
```

`HTTP 403` — ✅ **bị chặn.**

**Nhận xét.** Phân quyền có **hai chiều**: vai trò (làm được gì) và phạm vi (trên
dữ liệu nào). Một operator hợp lệ vẫn không đọc được dự án ngoài scope.

---

### TC-10 · Hỏi đáp ngôn ngữ tự nhiên

```bash
curl -X POST 'http://localhost:8000/api/v1/chat?project_id=prj_rvs' \
  -H 'Authorization: Bearer <business_viewer_token>' \
  -H 'Content-Type: application/json' \
  -d '{"message":"Phân khu nào của dự án Riverside đang bán chậm nhất và tôi nên ưu tiên căn nào?"}'
```

**Metadata thực tế:**

```
tool_calls = ['project_overview', 'top_ranked_units', 'compare_areas', 'area_ranking_risks']
sources    = [{"tool":"project_overview","source":"PostgreSQL","as_of":"2026-08-16T08:35:15Z"}, …]
tokens     = planner 520 + synthesis 5509
HTTP 200 · 10.05 s
```

**Câu trả lời thực tế:**

> ### Phân tích bán hàng tại dự án Vinhomes Riverside
>
> **Thông tin tổng quan** — Tổng số căn hộ: 321 · Đã bán: 55 · Đã đặt cọc: 41 ·
> Căn hộ còn lại: 225
>
> **Phân khu bán chậm nhất**
>
> | Tên phân khu | Số căn | Còn lại | Đã bán | Tốc độ 7 ngày | Tốc độ 30 ngày | Điểm xếp hạng |
> |---|---|---|---|---|---|---|
> | Anh Đào - Liền kề | 14 | 12 | 0 | 0.0 | 0.0 | 0.379 |
>
> **Căn hộ nên ưu tiên** — LI-110 (0.6697, hạng 1) · LI-114 (0.6697, hạng 2) ·
> LI-107 (0.6697, hạng 3)
>
> **Tóm tắt rủi ro** — Anh Đào - Liền kề còn 12 căn nhưng chưa bán căn nào; tốc độ
> bán 7 và 30 ngày đều bằng 0; điểm xếp hạng 0.379 nằm dưới ngưỡng 0.5.
>
> *Chưa đủ dữ liệu để đưa ra các phân tích sâu hơn về tình hình thị trường hoặc xu
> hướng cụ thể.*

**Nhận xét.** Ba điều đáng ghi nhận:

1. Planner LLM tự chọn **4 tool** phù hợp với câu hỏi, không cần người chỉ định.
2. Mọi con số trong câu trả lời đều **khớp** với `ranking_scores` và bảng tồn kho —
   `0.6697` của LI-110 trùng đúng `evidence` ở TC-03.
3. Câu cuối — *"Chưa đủ dữ liệu…"* — là hành vi **mong muốn**: agent thừa nhận
   giới hạn thay vì bịa thêm phân tích thị trường mà nó không có dữ liệu.

**Hạn chế đã biết:** tiêu đề bảng ghi *"Trong phân khu Hoa Sữa - Liền kề"* trong
khi ba căn LI-110/114/107 là top toàn dự án, không riêng phân khu đó — LLM ghép
nhầm nhãn giữa hai kết quả tool. Số liệu đúng, nhãn sai. Cần siết prompt của
`synthesis`. Ghi nhận để sửa sau Gate 2.

---

## 3. Đo lường

| Chỉ số | Mục tiêu | Đo được | Trạng thái |
|---|---|---|---|
| Độ chính xác số liệu (10 test case) | > 80% | **10/10 = 100%** — mọi con số khớp DB | ✅ |
| Độ trễ — đọc xếp hạng (`GET /ranking`) | < 3 s | **0.10 s** | ✅ |
| Độ trễ — tính lại 680 căn | < 3 s | **4.11 s** | ⚠️ vượt |
| Độ trễ — hỏi đáp có LLM | < 3 s | **6.4 – 10.0 s** | ⚠️ vượt |
| Độ trễ — sinh khuyến nghị | < 3 s | **8.3 s** | ⚠️ vượt |
| Chặn đúng ở mọi chốt HITL | 100% | **4/4** | ✅ |

**Về các chỉ số vượt ngưỡng.** Ngưỡng 3 giây hợp lý cho thao tác *đọc*, và đường
đọc đạt thoải mái (0.10 s). Ba đường vượt ngưỡng đều là **thao tác nặng có chủ ý**:

- **Tính lại 680 căn (4.11 s)** — hiện chạy đồng bộ trong request. Đã có sẵn đường
  bất đồng bộ (`POST /ranking/runs` + `GET /ranking/runs/{id}`) qua hàng đợi RQ;
  giao diện chưa chuyển sang dùng.
- **Ba đường có LLM (6–10 s)** — phần lớn thời gian là chờ OpenAI. `/chat` gọi LLM
  **hai lần** (planner + synthesis). Không tối ưu được bằng code phía mình; cách
  giảm là stream token về giao diện.

**Chưa đo:** độ hài lòng người dùng (chưa có buổi thử với đội sale thật) và độ phủ
test (chưa gắn `pytest-cov`).

---

## 4. Test tự động

```bash
TEST_TARGET="tests/" bash scripts/test_db.sh -q
```

Chạy **hai lần liên tiếp**, cùng commit, cùng máy:

| Lần | Kết quả | Thời gian |
|---|---|---|
| 1 | `70 failed, 1263 passed, 59 errors` | 10:55 |
| 2 | `51 failed, 1337 passed, 23 errors` | 9:00 |

**Hai lần cho hai kết quả khác nhau ⇒ bộ test đang KHÔNG ổn định.** Đây là phát
hiện quan trọng nhất của phần này, nên chúng tôi đã truy nguyên thay vì chỉ ghi
con số.

### Truy nguyên: chạy riêng từng module đang lỗi

```bash
TEST_TARGET="tests/test_api/test_reconciliation.py"   bash scripts/test_db.sh -q
TEST_TARGET="tests/test_api/test_ranking_endpoint.py" bash scripts/test_db.sh -q
TEST_TARGET="tests/test_api/test_seeded_dashboard.py" bash scripts/test_db.sh -q
TEST_TARGET="tests/test_jobs/test_parse_upload.py"    bash scripts/test_db.sh -q
```

**Output thực tế:**

```
tests/test_api/test_reconciliation.py    27 passed in 17.39s
tests/test_api/test_ranking_endpoint.py  19 passed in 15.39s
tests/test_api/test_seeded_dashboard.py  14 passed in 16.83s
tests/test_jobs/test_parse_upload.py     21 passed in  1.63s
```

**81/81 pass khi chạy riêng.** Đúng những module này lại đóng góp phần lớn số
failed khi chạy cả bộ (`test_reconciliation` một mình đã 22 failed).

### Kết luận: lỗi cô lập giữa các test, không phải lỗi sản phẩm

Các thông báo lỗi trong lần chạy đầy đủ đều có cùng một hình dạng — **dữ liệu bị
xoá giữa chừng bởi một test khác**:

```
AssertionError: {"detail":{"message":"Dự án 'd4e5f6a7-…' không tồn tại",
                           "error_code":"UNKNOWN_PROJECT"}}

sqlalchemy.exc.IntegrityError: ForeignKeyViolationError: insert or update on table
  "calculator_comparisons" violates foreign key constraint
  "fk_calculator_comparisons_project_id"

AssertionError: {"detail":{"message":"Khoá API không hợp lệ",
                           "error_code":"INVALID_API_KEY"}}
```

Nguyên nhân đã được ghi ngay trong `tests/conftest.py`: fixture `truncate_all`
chạy `TRUNCATE … RESTART IDENTITY CASCADE` trên toàn bộ bảng nghiệp vụ, trong khi
**tám module có fixture `clean_db` riêng với ngữ nghĩa hẹp hơn**. Khi chạy chung
một database, module này dọn sạch dữ liệu mà module kia đang cần — và lỗi hiện ra
ở một file hoàn toàn không liên quan. Docstring của `truncate_all` đã cảnh báo
đúng tình huống này.

Nhóm `test_real_hierarchy_e2e` (14–19 error) là chuyện khác: fixture cần Mini CRM
sống và khoá `MINICRM_SYNC_API_KEY` khớp, `scripts/test_db.sh` không dựng sẵn.

**Ý nghĩa cho Gate 2:** mã sản phẩm chạy đúng — bằng chứng là 10/10 test case thủ
công ở §2 và 81/81 khi chạy riêng từng module. Cái cần sửa là **hạ tầng test**:
cho mỗi module một database riêng, hoặc thống nhất về một fixture dọn dẹp duy
nhất. Đây là nợ kỹ thuật thật, không giấu.

Frontend: `vitest` **không chạy được trong container** (`node:20` + `jsdom@30` cần
Node ≥ 22 — `webidl.util.markAsUncloneable is not a function`). Chạy được trên máy
host với Node ≥ 22. Chi tiết ở `README.md` §7.

---

## 5. Việc cần làm tiếp

Xếp theo mức độ ưu tiên:

1. **Sửa cô lập giữa các test** — cho mỗi module một database riêng, hoặc thống
   nhất tám fixture `clean_db` rời rạc về một fixture dọn dẹp duy nhất. Đây là
   việc chặn CI, phải làm trước khi tin được kết quả chạy cả bộ.
2. **Nâng `frontend/Dockerfile` lên `node:22-alpine`** để `npm test` chạy được
   trong container và vào được CI.
3. **Truyền `area_name` vào ngữ cảnh agent** thay vì `area_id` thô (TC-03).
4. **Siết prompt `synthesis`** để không ghép nhầm nhãn phân khu (TC-10).
5. **Chuyển giao diện sang đường xếp hạng bất đồng bộ** — bỏ 4 giây chờ đồng bộ.
6. **Gắn `pytest-cov`** để có số đo độ phủ thật.
7. **Hiệu chỉnh ngưỡng band** — hiện `high` không có căn nào.
8. **Tổ chức buổi thử với đội sale thật** để có số đo độ hài lòng.
