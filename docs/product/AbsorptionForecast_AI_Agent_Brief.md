# PRODUCT / PROJECT BRIEF

**Loại đề tài:** Tầng dữ liệu canonical + AI Agent phân tích & dự báo (Forecasting / Decision-support Agent)
**Lĩnh vực:** Bất động sản — Kinh doanh & quản lý bán hàng dự án căn hộ
**Ngày cập nhật:** 08/08/2026
**Tài liệu chi tiết:** [PRD.md](PRD.md) (sản phẩm) · [SRS.md](SRS.md) (kỹ thuật) · [Brief rút gọn](AbsorptionForecast_Minimalism-Brief.md)

---

## 1. Tóm tắt điều hành

**AbsorptionForecast dựng một tầng dữ liệu canonical đã được kiểm tra từ các nguồn bán hàng / tồn kho đã được duyệt. PostgreSQL là nguồn sự thật duy nhất cho dữ liệu đã chuẩn hoá. Dashboard, phân tích, dự báo và AI agent đều đọc từ chính nguồn canonical đó.**

Trên nền dữ liệu đó, sản phẩm tính tốc độ hấp thụ theo từng phân khu / loại căn, dự báo **ngày dự kiến hết hàng kèm khoảng tin cậy 90%**, giải thích kết quả bằng tiếng Việt, và đưa đề xuất hành động qua **luồng phê duyệt của quản lý kinh doanh có lưu vết kiểm toán**.

Hai điều sản phẩm **không phải**:

- **Không phải chỉ là một dashboard.** Giá trị nằm ở tầng dữ liệu đã chuẩn hoá và đã kiểm tra bên dưới; dashboard chỉ là một trong nhiều tiêu dùng của tầng đó.
- **Không coi file thô là hệ thống ghi nhận.** File tải lên là artifact nguồn và bản ghi lineage; bảng canonical trong PostgreSQL mới là dữ liệu có thẩm quyền.

Sản phẩm là công cụ hỗ trợ ra quyết định cho ban kinh doanh dự án căn hộ — không phải nền tảng tích hợp CRM/ERP, không phải data warehouse, không tự động thực thi thay đổi giá hay chính sách chiết khấu.

| Hạng mục | Nội dung |
| --- | --- |
| **Tên đề tài** | AbsorptionForecast — Tầng dữ liệu canonical & trợ lý dự báo tốc độ hấp thụ căn hộ |
| **Loại đề tài** | Tầng dữ liệu canonical cho bán hàng / tồn kho, kèm AI Agent phân tích & dự báo chuỗi thời gian có giải thích và đề xuất hành động |
| **Ngành / Lĩnh vực** | Bất động sản — Kinh doanh, quản lý bán hàng dự án căn hộ |
| **Product Owner** | G21 - T100 — Nguyễn Đức Đạt, Bùi Hoàng Vương, Nguyễn Trọng Nam, Đặng Tiến Thành |
| **Người phê duyệt nghiệp vụ** | Quản lý kinh doanh (Sales Manager) |
| **Thời gian** | 3 MVP × 1 tuần + kiểm thử & triển khai = 5 tuần, sau đó 1–2 tuần pilot |

---

## 2. Bối cảnh & vấn đề

### 2.1 Bối cảnh

Trong các dự án chung cư / khu đô thị quy mô lớn, sản phẩm được chia thành nhiều phân khu và loại căn (diện tích, hướng, tầng, số phòng ngủ). Tốc độ hấp thụ giữa các nhóm rất không đồng đều: có loại "cháy hàng", có loại tồn kho kéo dài.

Ban kinh doanh cần biết liên tục: phân khu nào **sắp hết hàng** để tăng giá / siết ưu đãi, phân khu nào **bán chậm** để tập trung nguồn lực và kích cầu. Dữ liệu trả lời hai câu hỏi này **đã tồn tại** trong doanh nghiệp, và doanh nghiệp **đã có** quy trình báo cáo của riêng mình.

Cái chưa có là một **biểu diễn dữ liệu đã chuẩn hoá, đã kiểm tra và có truy vết nguồn** để phân tích, dự báo và AI agent cùng dựa vào — thay vì mỗi tiêu dùng lại đọc lại và diễn giải lại file theo cách riêng.

### 2.2 Bốn lớp vấn đề

| # | Vấn đề | Biểu hiện |
| --- | --- | --- |
| **P1** | **Dữ liệu nạp vào không được kiểm tra ở mức từng dòng** | Thiếu trường bắt buộc, sai kiểu ngày, số căn âm, bản ghi trùng chỉ lộ ra ở khâu dùng số; không có cơ chế từ chối lô dữ liệu hỏng trước khi nó ảnh hưởng đến kết quả |
| **P2** | **Không có tầng dữ liệu chuẩn hoá dùng chung** | Phân tích, dự báo và AI agent không có một biểu diễn chung để đọc; không truy được một con số trên báo cáo về lô dữ liệu và file đã sinh ra nó |
| **P3** | **Thiếu tầm nhìn dự báo và mức độ rủi ro** | Chỉ nhìn được số đã bán trong quá khứ; không biết phân khu nào hết hàng vào ngày nào, không có mức độ tin cậy đi kèm; phán đoán dựa hoàn toàn vào kinh nghiệm cá nhân |
| **P4** | **Thiếu cơ chế phê duyệt và vết kiểm toán** | Chính sách giá / chiết khấu quyết qua trao đổi miệng hoặc chat; sau vài tuần không truy được ai đề xuất, ai duyệt, dựa trên số liệu nào |

**Tác động:** kết quả phân tích và dự báo phụ thuộc chất lượng lô nạp mà không ai kiểm được · chậm ra quyết định giá / chiết khấu · bỏ lỡ thời điểm tăng giá khi cầu cao hoặc kích cầu khi tồn lâu · không rà soát được chính sách nào thực sự hiệu quả vì thiếu vết truy ngược.

**Ranh giới bài toán:** sản phẩm giải quyết **tầng dữ liệu canonical đã kiểm tra (P1–P2)** và **hỗ trợ ra quyết định có kiểm soát (P3–P4)**. Data warehouse, đồng bộ CRM/ERP, đồng bộ đa kênh Zalo và vận hành nhiều chủ đầu tư trên cùng hệ thống **không** thuộc bài toán này.

*Ghi chú về giả định đã gỡ bỏ:* các phiên bản trước của tài liệu này khẳng định khách hàng đang chịu cảnh "dữ liệu phân tán", "không có nguồn sự thật", "tổng hợp Excel thủ công hằng tuần" và "báo cáo thiếu nhất quán". Những khẳng định đó **chưa được kiểm chứng với khách hàng** và đã bị gỡ khỏi mọi tài liệu. Nếu muốn dùng lại làm cơ sở bán hàng, phải xác nhận trực tiếp — `[NEEDS CONFIRMATION]`.

---

## 3. Giá trị sản phẩm

Giá trị cốt lõi là **tầng dữ liệu canonical**: một biểu diễn đã chuẩn hoá, đã kiểm tra theo từng dòng, có truy vết về lô và file nguồn, nằm trong PostgreSQL và được mọi tiêu dùng đọc chung.

Trên nền đó, sản phẩm cho ban kinh doanh ba thứ: **biết đang ở đâu** (tốc độ hấp thụ theo phân khu, tính từ dữ liệu canonical, kèm mốc cập nhật và trạng thái chất lượng dữ liệu), **biết sắp tới thế nào** (ngày dự kiến hết hàng, khoảng tin cậy, giải thích tiếng Việt, thứ tự ưu tiên hành động), và **biết quyết định của mình được ghi nhận thế nào** (đề xuất phải qua phê duyệt, mọi quyết định truy ngược được về dữ liệu canonical và lô nguồn).

Điểm phân biệt: dashboard, phân tích và dự báo **đọc cùng một nguồn**. Không có nhánh nào đọc lại file thô, nên hai màn hình không thể ra hai con số khác nhau vì đọc hai bản dữ liệu khác nhau.

Sản phẩm phát triển theo 3 giai đoạn: **Tầng dữ liệu canonical → Dự báo/AI → Phê duyệt & Phân quyền**.

---

## 4. Đối tượng người dùng

| Vai trò | Mô tả |
| --- | --- |
| **Nhân viên kinh doanh (Sales Staff)** | Xem tốc độ hấp thụ và cảnh báo cạn hàng của các phân khu mình phụ trách để chủ động tư vấn khách |
| **Quản lý kinh doanh (Sales Manager)** | Nạp dữ liệu, xem toàn dự án, duyệt / từ chối đề xuất chính sách, tra cứu lịch sử quyết định, quản trị người dùng |
| **Ban điều hành (Viewer)** | Xem dashboard tổng hợp toàn dự án ở chế độ chỉ đọc để ra quyết định chiến lược |

---

## 5. Lộ trình 3 MVP

### MVP 1 — Canonical Data Store, Data Ingestion, Validation & Absorption Dashboard *(giải quyết P1, P2)*

- **Mục tiêu:** dựng tầng dữ liệu canonical trong PostgreSQL từ nguồn đã được duyệt, và phục vụ dashboard tốc độ hấp thụ **từ chính tầng đó**.
- **Năng lực chính:**
  1. Nạp dữ liệu bán hàng / tồn kho đã được duyệt (Excel/CSV theo template).
  2. Kiểm tra từng dòng và báo lỗi kèm số dòng / tên cột.
  3. Chuẩn hoá và lưu vào bảng canonical.
  4. Chặn nạp trùng.
  5. Giữ lineage tới file nguồn và lô nạp.
  6. Tính tốc độ hấp thụ từ dữ liệu canonical.
  7. Dashboard — và về sau là dự báo — đọc cùng một nguồn canonical.
  8. Hiển thị độ tươi và trạng thái chất lượng dữ liệu.
- **Tiêu chí thành công:** nạp được file thật của dự án pilot; mọi dòng lỗi được báo đúng vị trí và lô hỏng bị từ chối nguyên vẹn; từ một con số trên dashboard truy ngược được về lô và file nguồn; biểu đồ tải dưới 2 giây ở quy mô pilot.
- **Không làm ở giai đoạn này:** dự báo, giải thích AI, đăng nhập, phê duyệt.

### MVP 2 — Dự báo, giải thích & cảnh báo *(giải quyết P3)*

- **Mục tiêu:** chuyển từ nhìn lại quá khứ sang nhìn trước rủi ro tồn kho, **đọc đầu vào từ tầng canonical**.
- **Năng lực chính:** dự báo Prophet chạy tự động hằng ngày · ngày dự kiến hết hàng kèm khoảng tin cậy 90% · giải thích tiếng Việt các yếu tố ảnh hưởng · cảnh báo cạn hàng theo ngưỡng do quản lý cấu hình · xếp hạng phân khu theo mức rủi ro kèm hướng hành động · hiển thị tiến độ chạy dự báo theo thời gian thực · báo cáo sai số dự báo (MAPE).
- **Tiêu chí thành công:** 100% dự báo có khoảng tin cậy và giả định; mỗi dự báo có đoạn giải thích đọc hiểu được; cảnh báo đúng phân khu và đúng số ngày; MAPE được đo trên dữ liệu pilot.
- **Ràng buộc dữ liệu:** AI agent và Prophet đọc bảng canonical, **không** đọc file thô; kết quả dự báo ghi vào bảng dẫn xuất riêng, không ghi đè dữ liệu bán hàng / tồn kho.
- **Không làm ở giai đoạn này:** phê duyệt, phân quyền, so sánh nhiều mô hình, mô phỏng what-if.

### MVP 3 — Phê duyệt, phân quyền & kiểm toán *(giải quyết P4)*

- **Mục tiêu:** biến đề xuất của hệ thống thành quyết định có người chịu trách nhiệm và có vết truy ngược.
- **Năng lực chính:** đăng nhập và phân quyền 3 vai trò · đề xuất mặc định *Chờ duyệt*, chỉ có hiệu lực sau khi Manager duyệt, từ chối bắt buộc nêu lý do · nhật ký kiểm toán chỉ ghi thêm · trạng thái đề xuất cập nhật ngay trên màn hình đang mở · gán vai trò và phân khu phụ trách.
- **Tiêu chí thành công:** 100% đề xuất đi qua bước duyệt, 0 trường hợp có hiệu lực mà không có quyết định của Manager; nhân viên không truy cập được dữ liệu ngoài phạm vi phân công; từ một quyết định truy ngược được **quyết định → dự báo → dữ liệu canonical → lô/file nguồn**.
- **Không làm ở giai đoạn này:** SSO/OAuth2, xác thực đa yếu tố, thông báo ngoài ứng dụng, phân quyền theo từng trường dữ liệu.

> **Thời điểm xác thực và phê duyệt.** Không có tầng xác thực nào ở MVP 1 và MVP 2 — API chạy mở trong môi trường dev/pilot nội bộ. Đăng nhập, RBAC, luồng duyệt và audit log đều xuất hiện lần đầu ở MVP 3. Bốn tài liệu sản phẩm thống nhất theo mốc này.

---

## 6. Luồng nghiệp vụ chính

1. **Nạp dữ liệu vào tầng canonical** — Manager tạo dự án / phân khu, rồi tải lô dữ liệu bán hàng / tồn kho đã được duyệt lên; hệ thống tính checksum để chặn nạp trùng, kiểm tra từng dòng, trả danh sách lỗi kèm số dòng để sửa; lô hợp lệ được chuẩn hoá và ghi vào bảng canonical trong một transaction, kèm tham chiếu tới file và lô nguồn; tốc độ hấp thụ được tính lại **từ dữ liệu canonical**.
2. **Đọc dashboard từ nguồn canonical** — người dùng mở dashboard, chọn phân khu, xem biểu đồ xu hướng và thẻ tổng hợp, kèm mốc cập nhật gần nhất và trạng thái chất lượng dữ liệu. Dashboard không đọc file thô.
3. **Chạy dự báo và đọc giải thích (MVP 2)** — job dự báo chạy tự động hằng ngày (hoặc Manager kích hoạt sau khi nạp dữ liệu mới), đọc đầu vào từ bảng canonical; người dùng theo dõi tiến độ trên màn hình; kết quả gồm ngày dự kiến hết hàng, khoảng tin cậy, giải thích tiếng Việt, cảnh báo và bảng xếp hạng rủi ro — tất cả là bản ghi dẫn xuất.
4. **Duyệt đề xuất và truy vết (MVP 3)** — Manager mở danh sách đề xuất *Chờ duyệt*, xem dự báo và giải thích kèm theo, duyệt hoặc từ chối kèm lý do; trạng thái cập nhật ngay cho các thành viên đang mở dashboard; quyết định được ghi vào nhật ký kiểm toán, truy ngược được tới lô dữ liệu canonical và file nguồn.

---

## 7. Ngoài phạm vi

| Hạng mục | Ghi chú |
| --- | --- |
| Data warehouse / lakehouse riêng | Tầng canonical nằm trong chính PostgreSQL của ứng dụng |
| Tích hợp CRM/ERP theo API | Dữ liệu vào chỉ qua Excel/CSV theo template |
| Coi file thô là hệ thống ghi nhận | File gốc chỉ là artifact nguồn và bản ghi lineage |
| Dùng dữ liệu seed như dữ liệu khách hàng | Seed chỉ phục vụ dev/test |
| Thông báo qua Zalo, email, Slack | Cảnh báo hiển thị trong ứng dụng |
| SSO / OAuth2 | Đăng nhập bằng tài khoản riêng của hệ thống |
| Xác thực đa yếu tố (MFA) | — |
| Multi-tenant nhiều chủ đầu tư | Phạm vi 1 dự án pilot, có thể mở rộng theo dự án |
| So sánh mô hình ARIMA / mô hình học máy khác | Chỉ dùng Prophet |
| Mô phỏng what-if thay đổi giá / chính sách | — |
| Tự động huấn luyện lại mô hình | Huấn luyện lại do đội kỹ thuật chủ động |
| Agent tự động thực thi thay đổi giá / chiết khấu | Bắt buộc phê duyệt của con người |
| Giao dịch tài chính, thanh toán, ký hợp đồng | Ngoài bài toán |

---

## 8. Kiến trúc & công nghệ

```text
Excel/CSV hoặc nguồn dữ liệu đã được duyệt
        ↓
Ingestion (đường ghi duy nhất)
        ↓
Validation & normalization
        ↓
Canonical PostgreSQL data          ← nguồn sự thật duy nhất cho dữ liệu đã chuẩn hoá
        ↓
Absorption analytics
        ↓
Forecasting & AI explanation
        ↓
Alerts & recommendations
        ↓
Human approval & audit trail
```

| Thành phần | Công nghệ |
| --- | --- |
| Tầng dữ liệu canonical | PostgreSQL 15 — nguồn có thẩm quyền cho dữ liệu bán hàng, tồn kho, hấp thụ, dự báo và nhật ký kiểm toán đã chuẩn hoá |
| Truy cập dữ liệu & migration | SQLAlchemy Core + asyncpg · Alembic |
| Ingestion | FastAPI (nhận file) + worker nền (Redis + RQ) chạy parse / validate / nạp |
| Dự báo | Prophet (Python) |
| Điều phối & giải thích | LangGraph + LLM — đọc từ canonical, không đọc file thô |
| API | FastAPI |
| Giao diện | ReactJS |
| Cập nhật real-time | WebSocket (MVP 2/3) |
| Triển khai | Fly.io (thay thế: Render); Docker Compose cho dev |

---

## 8b. Quyền sở hữu dữ liệu

Bảy quy tắc dưới đây là ràng buộc kiến trúc, áp dụng cho cả bốn tài liệu sản phẩm và cho mã nguồn:

1. **Ingestion là đường ghi duy nhất** cho dữ liệu bán hàng / tồn kho nạp từ ngoài.
2. **Bảng canonical trong PostgreSQL là nguồn đọc** cho dashboard, phân tích và dự báo.
3. **File thô được giữ lại làm artifact nguồn và bản ghi lineage** — phục vụ kiểm toán và nạp lại, không phải hệ thống ghi nhận.
4. **AI agent không đọc file thô sau khi ingest** — chỉ đọc dữ liệu canonical.
5. **Dự báo và đề xuất là bản ghi dẫn xuất** — không bao giờ ghi đè dữ liệu bán hàng / tồn kho nguồn.
6. **Nhật ký kiểm toán chỉ ghi thêm** (append-only).
7. **Dữ liệu seed chỉ dùng cho dev/test**, không bao giờ trình bày như dữ liệu khách hàng.

**Trạng thái cài đặt thực tế** của từng hạng mục trong brief này — đọc từ mã nguồn và `pipeline_status.md`, không đọc từ tài liệu — xem bảng truy vết ở [PRD §18](PRD.md) (góc nhìn sản phẩm) và [SRS §9](SRS.md) (góc nhìn yêu cầu kỹ thuật). Một tính năng xuất hiện trong brief **không** đồng nghĩa với đã cài đặt.

---

## 9. Ràng buộc

- **Phê duyệt bắt buộc:** mọi quyết định chính sách dựa trên dự báo phải qua quản lý kinh doanh; hệ thống không tự thực thi thay đổi giá / chính sách.
- **Tính toàn vẹn của tầng canonical:** lô nạp vượt ngưỡng lỗi bị từ chối nguyên vẹn, không nạp một phần; mọi bản ghi canonical phải giữ được tham chiếu về lô và file nguồn.
- **Minh bạch dự báo:** mỗi dự báo kèm khoảng tin cậy và giả định rõ ràng, tránh hiểu lầm là số liệu chắc chắn.
- **Bảo mật dữ liệu:** dữ liệu bán hàng là thông tin nhạy cảm — phân quyền theo vai trò, dữ liệu khách hàng được ẩn danh trước khi đưa vào hệ thống.
- **Kiểm soát chi phí:** giới hạn tần suất tính lại mô hình và gọi LLM ở mức 1 lần/ngày/phân khu trừ khi có dữ liệu mới.

---

## 10. Mục tiêu kinh doanh

| ID | Mục tiêu | Chỉ tiêu |
| --- | --- | --- |
| **O0** | Dựng tầng dữ liệu canonical đáng tin cậy | 100% bản ghi canonical đến từ đường ingestion và giữ được tham chiếu tới lô/file nguồn; 0 lô nạp một phần; tỷ lệ lô bị từ chối và lý do được đo và báo cáo |
| **O1** | Rút ngắn thời gian từ lúc có dữ liệu đến lúc phát hiện biến động | Dashboard cập nhật tối thiểu 1 lần/ngày; độ trễ từ khi nạp lô mới đến khi dashboard & cảnh báo phản ánh < 24 giờ *(baseline hiện tại của khách hàng: `[NEEDS CONFIRMATION]`, chốt ở Tuần 1)* |
| **O2** | Nhắm đúng đối tượng cần chính sách kích cầu | Xếp nhóm phân khu theo mức rủi ro tồn kho; giảm số phân khu phải nhận mức chiết khấu tối đa so với cách áp dụng đại trà *(baseline thị trường: chiết khấu 10–30% trên diện rộng)* |
| **O3** | Cải thiện tỷ lệ hấp thụ ở phân khu được theo dõi | So sánh tỷ lệ hấp thụ giữa nhóm áp dụng và không áp dụng đề xuất trong pilot *(baseline ngành ~70%, DXS-FERI 6T/2026)* |
| **O4** | Nâng độ chính xác dự báo so với ước tính cảm tính | MAPE được đo và công bố sau mỗi chu kỳ đánh giá; 100% dự báo kèm khoảng tin cậy |
| **O5** | Đảm bảo kiểm soát của con người | 100% đề xuất được duyệt trước khi áp dụng; 0 trường hợp tự động thực thi, kiểm chứng qua log |

---

## 11. Chỉ số thành công của MVP

| Chỉ số | Cách đo |
| --- | --- |
| Tỷ lệ nạp thành công vào canonical | % lô upload được chuẩn hoá và ghi vào bảng canonical; số lỗi dữ liệu phát hiện được theo dòng; số lô bị từ chối do vượt ngưỡng lỗi |
| Độ phủ lineage | % bản ghi canonical truy ngược được về lô và file nguồn (mục tiêu: 100%) |
| Chặn nạp trùng | Số lần upload trùng bị chặn; 0 bản ghi nhân bản trong bảng canonical |
| Độ tươi & chất lượng dữ liệu | Dashboard hiển thị mốc cập nhật gần nhất và trạng thái chất lượng dữ liệu của chuỗi đang xem; dữ liệu và dự báo cập nhật tối thiểu 1 lần/ngày |
| Tỷ lệ hoàn tất job dự báo | % phân khu có dự báo sau mỗi lần chạy hằng ngày; số phân khu lỗi |
| Độ chính xác dự báo | MAPE trung bình theo phân khu, đo trên tập kiểm chứng của dữ liệu pilot |
| Khả năng theo dõi tiến độ | Người dùng thấy được tiến độ job dự báo theo thời gian thực; có cơ chế thay thế khi mất kết nối |
| Truy vết phê duyệt | 100% quyết định duyệt / từ chối có đủ người thực hiện, thời điểm, lý do; truy ngược được về dự báo và file dữ liệu nguồn |
| Mức độ sử dụng | Số lượt truy cập dashboard mỗi tuần của nhân viên và quản lý trong pilot |

---

## 12. Kế hoạch triển khai

| Giai đoạn | Nội dung chính |
| --- | --- |
| **Tuần 1** | Chốt scope với ban kinh doanh: template dữ liệu, ngưỡng cảnh báo, phân khu pilot; thiết kế dữ liệu & kiến trúc |
| **Tuần 2** | **MVP 1** — ingestion & validation, tầng dữ liệu canonical, dashboard tốc độ hấp thụ đọc từ canonical |
| **Tuần 3** | **MVP 2** — dự báo Prophet, giải thích, cảnh báo cạn hàng, tiến độ real-time |
| **Tuần 4** | **MVP 3** — đăng nhập & phân quyền, luồng phê duyệt, nhật ký kiểm toán |
| **Tuần 5** | Kiểm thử đầu-cuối, tối ưu mô hình, triển khai môi trường pilot, demo & thu phản hồi |

---

## 13. Rủi ro & phương án giảm thiểu

| Rủi ro | Ảnh hưởng | Giảm thiểu |
| --- | --- | --- |
| Không kịp có dữ liệu thực tế của dự án pilot | Trễ toàn bộ tiến độ | Yêu cầu dữ liệu ngay Tuần 1; phát triển song song trên bộ dữ liệu seed dev/test đúng template — **seed không được trình bày như dữ liệu khách hàng** khi demo |
| Nguồn dữ liệu đầu vào chưa được khách hàng "duyệt" rõ ràng | Tầng canonical mất tính thẩm quyền ngay từ gốc | Chốt ở Tuần 1 ai là người ký nhận lô dữ liệu và theo template nào; ghi người nạp và thời điểm vào bản ghi lô `[NEEDS CONFIRMATION]` |
| Dữ liệu lịch sử mỏng / chất lượng thấp | Dự báo kém chính xác | Kiểm tra dữ liệu đầu vào; gắn nhãn "độ tin cậy thấp"; yêu cầu duyệt kỹ hơn khi thiếu dữ liệu |
| Người dùng phụ thuộc quá mức vào AI | Quyết định chính sách sai lệch | Duy trì phê duyệt bắt buộc; hiển thị khoảng tin cậy và giả định trên mọi đề xuất |
| Chi phí compute / gọi LLM vượt dự kiến | Vượt ngân sách pilot | Giới hạn 1 lần/ngày/phân khu; theo dõi số lượt gọi |
| Tiến độ 5 tuần không đủ cho toàn bộ phạm vi | Không nghiệm thu được | Ưu tiên hoàn thành lần lượt MVP 1 → 2 → 3; cắt hạng mục ưu tiên thấp sang giai đoạn pilot |

---

## 14. Các bên liên quan

| Bên liên quan | Vai trò |
| --- | --- |
| Product Owner (học viên) | Định nghĩa yêu cầu, phạm vi, ưu tiên tính năng |
| Ban Kinh doanh (Sales) | Người dùng chính; cung cấp dữ liệu nghiệp vụ và phản hồi |
| Đội kỹ thuật (Data/AI, Backend, Frontend) | Xây dựng, kiểm thử và triển khai hệ thống |
| Giảng viên / Mentor VinUni × Vingroup | Đánh giá tiến độ, góp ý chuyên môn |

---

## 15. Quy mô thị trường

Theo số liệu quý I/2026 của Bộ Xây dựng và VARS, cả nước có hơn 1.360 dự án nhà ở đang triển khai với quy mô khoảng 654.000 căn; nguồn cung mới năm 2026 ước tính khoảng 150.000 sản phẩm — đây là quy mô tiềm năng (TAM) cho giải pháp số hoá quản lý và dự báo tốc độ bán hàng theo phân khu.

- **SAM:** nhóm chủ đầu tư có nhiều dự án / phân khu mở bán song song — nơi bài toán theo dõi hàng trăm loại căn phức tạp nhất. Bốn nhà phát triển lớn nhất (Vingroup, Masterise Homes, MIK, Sun Group) chiếm 64% tổng nguồn cung (VARS 2025).
- **SOM:** trong khuôn khổ chương trình đào tạo, pilot giới hạn ở **1 dự án với 2–3 phân khu đại diện** (ít nhất 1 bán chạy, 1 bán chậm).

*Số liệu thị trường mang tính tham khảo để minh hoạ quy mô cơ hội; phạm vi triển khai thực tế giới hạn ở SOM nêu trên.*

---

## 16. Kế hoạch xác thực (Traction)

- **Pilot phạm vi nhỏ:** 1 dự án, 2–3 phân khu đại diện, kiểm chứng mô hình dự báo trên dữ liệu thật.
- **Người dùng thử nghiệm:** 3–5 nhân viên kinh doanh + 1 quản lý kinh doanh, dùng dashboard trong suốt giai đoạn pilot, phản hồi hằng tuần.
- **Mốc thành công ban đầu:** nạp được toàn bộ lô dữ liệu pilot vào tầng canonical với lineage đầy đủ; MAPE ở mức chấp nhận được trên dữ liệu pilot; có ít nhất 1 đề xuất được quản lý duyệt và áp dụng thực tế; đa số người dùng xác nhận số liệu trên dashboard khớp với số họ tự đối chiếu.
- **Mở rộng sau pilot:** mở rộng dần sang toàn bộ phân khu của dự án, sau đó xem xét các dự án khác trong danh mục.

---

## 17. Đề xuất hỗ trợ (Ask)

- **Dữ liệu:** quyền truy cập dữ liệu bán hàng & tồn kho lịch sử (đã ẩn danh) của ít nhất 1 dự án thực tế.
- **Đầu mối nghiệp vụ:** 1 quản lý kinh doanh làm đầu mối duyệt dự báo và xác nhận tiêu chí cảnh báo cạn hàng.
- **Cố vấn kỹ thuật:** hỗ trợ về lựa chọn mô hình dự báo và thiết kế kiến trúc agent.
- **Hạ tầng:** ngân sách thử nghiệm cho compute và gọi API LLM ở mức nhỏ.
- **Thời gian:** 5 tuần hoàn thành 3 MVP, cộng 1–2 tuần pilot trước khi báo cáo kết quả cuối khoá.

---

## 18. Giả định

- Có sẵn dữ liệu lịch sử bán hàng và tồn kho tối thiểu vài tháng gần nhất để huấn luyện mô hình.
- Dữ liệu được cung cấp qua file Excel/CSV định kỳ theo template quy định, từ nguồn đã được phía khách hàng duyệt.
- Có ít nhất một quản lý kinh doanh tham gia làm đầu mối phê duyệt trong giai đoạn thử nghiệm.
- Ngưỡng cảnh báo cạn hàng mặc định 30 ngày tồn kho dự kiến, quản lý được phép điều chỉnh.
- Khách hàng chấp nhận tầng canonical trong PostgreSQL là nguồn có thẩm quyền cho các số liệu mà sản phẩm này hiển thị và dự báo — trong phạm vi dữ liệu đã nạp qua ingestion, không phải cho toàn bộ doanh nghiệp. `[NEEDS CONFIRMATION]`
