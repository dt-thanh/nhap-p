# Product Requirements Document

**Tên sản phẩm:** AbsorptionForecast — Tầng dữ liệu canonical & trợ lý dự báo tốc độ hấp thụ căn hộ
**Loại sản phẩm:** Tầng dữ liệu canonical cho bán hàng / tồn kho, kèm AI Agent phân tích & dự báo chuỗi thời gian có giải thích (explainable) và đề xuất hành động
**Lĩnh vực:** Bất động sản — Kinh doanh & quản lý bán hàng dự án căn hộ
**Ngày cập nhật:** 08/08/2026
**Product Owner:** G21 - T100 - Nguyễn Đức Đạt, Bùi Hoàng Vương, Nguyễn Trọng Nam, Đặng Tiến Thành
**Tài liệu kỹ thuật:** [SRS.md](SRS.md) — nguồn tham chiếu cho phạm vi và kiến trúc
**Brief:** [AbsorptionForecast_AI_Agent_Brief.md](AbsorptionForecast_AI_Agent_Brief.md) · [bản rút gọn](AbsorptionForecast_Minimalism-Brief.md)

---

## 0. Tóm tắt điều hành

**AbsorptionForecast dựng một tầng dữ liệu canonical đã được kiểm tra từ các nguồn bán hàng / tồn kho đã được duyệt. PostgreSQL là nguồn sự thật duy nhất cho dữ liệu đã chuẩn hoá. Dashboard, phân tích, dự báo và AI agent đều đọc từ chính nguồn canonical đó.**

Sản phẩm **không phải** chỉ là một dashboard, và **không** coi file thô là hệ thống ghi nhận: file tải lên là artifact nguồn và bản ghi lineage, còn bảng canonical trong PostgreSQL mới là dữ liệu có thẩm quyền.

Lộ trình: **MVP 1** dựng tầng canonical (ingestion → validation → normalization → canonical → absorption → dashboard); **MVP 2** thêm dự báo, giải thích và cảnh báo đọc từ canonical; **MVP 3** thêm xác thực, phân quyền, phê duyệt và audit trail.

---

## 1. Bối cảnh

Trong các dự án chung cư / khu đô thị quy mô lớn, sản phẩm được chia thành nhiều phân khu và loại căn (diện tích, hướng, tầng, số phòng ngủ). Tốc độ hấp thụ giữa các nhóm rất không đồng đều: có loại "cháy hàng", có loại tồn kho kéo dài.

Ban kinh doanh cần liên tục biết:

- Phân khu / loại căn nào **sắp hết hàng** → cân nhắc tăng giá, siết ưu đãi.
- Phân khu / loại căn nào **bán chậm** → tập trung nguồn lực sale, đẩy chính sách kích cầu.

Dữ liệu để trả lời hai câu hỏi đó **đã tồn tại trong doanh nghiệp**, và doanh nghiệp **đã có** quy trình báo cáo riêng. Cái chưa có là một **biểu diễn dữ liệu đã chuẩn hoá, đã kiểm tra theo từng dòng và có truy vết nguồn**, để phân tích, dự báo và AI agent cùng dựa vào thay vì mỗi tiêu dùng lại đọc lại và diễn giải lại file theo cách riêng.

Vì vậy giá trị cốt lõi của sản phẩm không phải là "thêm một dashboard", mà là **tầng dữ liệu canonical** bên dưới: đã kiểm tra, đã chuẩn hoá, chống trùng, giữ lineage — và mọi tiêu dùng đọc chung.

---

## 2. Vấn đề

Sản phẩm giải quyết bốn lớp vấn đề, xếp theo thứ tự phải giải quyết:

| # | Nhóm vấn đề | Biểu hiện cụ thể |
| --- | --- | --- |
| **P1** | **Dữ liệu nạp vào không được kiểm tra ở mức từng dòng** | Thiếu trường bắt buộc, sai kiểu ngày, số căn âm, bản ghi trùng chỉ lộ ra ở khâu dùng số; không có cơ chế từ chối một lô hỏng trước khi nó ảnh hưởng tới kết quả; không chặn được việc nạp lại cùng một lô |
| **P2** | **Không có tầng dữ liệu chuẩn hoá dùng chung** | Phân tích, dự báo và AI agent không có một biểu diễn chung để đọc; không truy được một con số trên báo cáo về lô dữ liệu và file đã sinh ra nó |
| **P3** | **Không có tầm nhìn dự báo và mức độ rủi ro** | Chỉ nhìn được số đã bán trong quá khứ, không biết phân khu nào sẽ hết hàng vào ngày nào; không có mức độ tin cậy đi kèm; việc phán đoán "còn bán được bao lâu" dựa hoàn toàn vào kinh nghiệm cá nhân |
| **P4** | **Không có cơ chế phê duyệt và vết kiểm toán** | Chính sách giá / chiết khấu được quyết qua trao đổi miệng hoặc chat; sau vài tuần không truy được ai đề xuất, ai duyệt, dựa trên số liệu nào; không có cơ sở để rà soát lại quyết định |

**Tác động nếu không giải quyết**

| Hệ quả | Mô tả |
| --- | --- |
| Kết quả phân tích không đáng tin | Dự báo và cảnh báo kế thừa nguyên vẹn lỗi của lô dữ liệu đầu vào mà không ai phát hiện được |
| Không truy vết được | Không chỉ ra được một con số đến từ lô nào, file nào, ai nạp |
| Chậm ra quyết định | Chính sách giá / chiết khấu điều chỉnh trễ, thiếu cơ sở định lượng |
| Bỏ lỡ thời điểm tối ưu | Chậm tăng giá khi cầu cao, chậm kích cầu khi hàng tồn lâu |
| Không rà soát được quyết định | Không đo được chính sách nào hiệu quả vì thiếu vết lưu đề xuất và phê duyệt |

**Giả định đã gỡ bỏ.** Các phiên bản trước của PRD khẳng định khách hàng đang chịu cảnh dữ liệu phân tán, không có nguồn sự thật, tổng hợp Excel thủ công hằng tuần và báo cáo thiếu nhất quán. Những khẳng định đó **chưa được kiểm chứng** và đã bị gỡ khỏi cả bốn tài liệu. Muốn dùng lại phải xác nhận trực tiếp với khách hàng — `[NEEDS CONFIRMATION]`.

**Phạm vi phiên bản đầu:** sản phẩm giải quyết **tầng dữ liệu canonical đã kiểm tra (P1, P2)** và **hỗ trợ ra quyết định có kiểm soát (P3, P4)**. Đây **không** phải data warehouse và **không** phải dự án tích hợp CRM/ERP toàn doanh nghiệp — dữ liệu vào qua file Excel/CSV theo template từ nguồn đã được duyệt, kết nối API với hệ thống nguồn nằm ngoài phạm vi.

---

## 3. Mục tiêu

### 3.1 Mục tiêu sản phẩm (MVP)

| ID | Mục tiêu | Chỉ tiêu (SMART) trong phạm vi MVP |
| --- | --- | --- |
| O0 | Dựng tầng dữ liệu canonical đáng tin cậy | 100% bản ghi canonical đến từ đường ingestion và giữ được tham chiếu tới lô/file nguồn; 0 lô nạp một phần; 0 bản ghi nhân bản do nạp trùng |
| O1 | Rút ngắn thời gian từ lúc nạp dữ liệu đến lúc phát hiện biến động | Dashboard cập nhật tối thiểu 1 lần/ngày; độ trễ từ khi nạp lô mới đến khi dashboard & cảnh báo phản ánh < 24 giờ |
| O2 | Nhắm đúng đối tượng cần chính sách kích cầu | Agent xếp nhóm phân khu / loại căn theo mức rủi ro tồn kho dựa trên tốc độ hấp thụ dự báo; giảm số phân khu phải nhận mức chiết khấu tối đa so với cách áp dụng đại trà |
| O3 | Cải thiện tỷ lệ hấp thụ ở phân khu được theo dõi | So sánh tỷ lệ hấp thụ giữa nhóm có áp dụng đề xuất của Agent và nhóm không áp dụng trong pilot (baseline ngành ~70%, DXS-FERI 6T/2026) |
| O4 | Nâng độ chính xác dự báo so với ước tính cảm tính | MAPE được đo và công bố sau mỗi chu kỳ đánh giá; 100% dự báo hiển thị kèm khoảng tin cậy |
| O5 | Đảm bảo kiểm soát của con người | 100% đề xuất chính sách được quản lý kinh doanh duyệt trước khi áp dụng; 0 trường hợp tự động thực thi, kiểm chứng qua log |

### 3.2 Ngoài mục tiêu (Non-goals)

- Agent **không** tự động thực thi thay đổi giá bán / chính sách chiết khấu.
- Không xử lý giao dịch tài chính, thanh toán, ký kết hợp đồng.
- Không thay thế vai trò tư vấn của nhân viên kinh doanh.
- **Không** dựng data warehouse / lakehouse riêng — tầng canonical nằm trong chính PostgreSQL của ứng dụng.
- **Không** trở thành nguồn sự thật cho toàn doanh nghiệp — phạm vi thẩm quyền giới hạn ở dữ liệu đã nạp qua ingestion của sản phẩm này.

---

## 4. Người dùng (Personas)

Sản phẩm có đúng ba vai trò, khớp với phân quyền trong SRS.

| Persona | Vai trò hệ thống | Bối cảnh | Nhu cầu cốt lõi |
| --- | --- | --- | --- |
| **Nhân viên kinh doanh (Sales Staff)** | `sales_staff` | Phụ trách một số phân khu / loại căn | Xem tốc độ hấp thụ và cảnh báo của nhóm mình phụ trách để chủ động tư vấn khách |
| **Quản lý kinh doanh (Sales Manager)** | `sales_manager` | Chịu trách nhiệm chính sách giá / chiết khấu toàn dự án | Nạp dữ liệu, xem toàn dự án, hiểu lý do dự báo, duyệt / từ chối đề xuất (HITL), tra cứu audit log, quản trị người dùng |
| **Ban điều hành / Lãnh đạo dự án** | `viewer` | Ra quyết định chiến lược | Xem dashboard tổng hợp toàn dự án ở chế độ chỉ đọc |

*Ghi chú:* Ban điều hành dùng chung dashboard tổng hợp với Sales Manager ở quyền chỉ đọc, không xây màn hình riêng và không xem audit log.

---

## 5. Phạm vi MVP

Sản phẩm giao theo **3 MVP, mỗi MVP 1 tuần**, cộng thời gian kiểm thử và pilot (tổng 5 tuần). Mỗi MVP là một lát cắt dùng được, không phải một tầng kỹ thuật rời rạc.

### 5.1 MVP 1 — Canonical Data Store, Data Ingestion, Validation & Absorption Dashboard

**Mục tiêu:** dựng tầng dữ liệu canonical trong PostgreSQL từ nguồn bán hàng / tồn kho đã được duyệt, và phục vụ dashboard tốc độ hấp thụ **từ chính tầng đó**. Giải quyết P1 và P2.

**Tám năng lực bắt buộc**

1. **Nạp dữ liệu đã được duyệt** — upload Excel/CSV bán hàng, tồn kho và danh mục phân khu theo template (tối đa 20 MB), gắn với một dự án cụ thể.
2. **Kiểm tra từng dòng và báo lỗi** — thiếu trường bắt buộc, sai kiểu ngày, số căn âm, giá trị ngoài tập cho phép; lỗi trả kèm **số dòng và tên cột**, tải được ra CSV để sửa; lô vượt ngưỡng tỷ lệ lỗi bị từ chối nguyên vẹn, không nạp một phần.
3. **Chuẩn hoá và lưu dữ liệu canonical** — ánh xạ tên cột tiếng Việt/tiếng Anh về lược đồ chuẩn, phân giải tên phân khu → `area_id`, ghi vào bảng canonical trong **một transaction**.
4. **Chặn nạp trùng** — checksum SHA-256 theo phạm vi dự án; upload lại cùng một file bị từ chối và chỉ ra lô đã nạp trước đó.
5. **Giữ lineage nguồn** — mỗi bản ghi canonical mang tham chiếu tới lô nạp và file nguồn; file gốc được lưu lại phục vụ kiểm toán và nạp lại.
6. **Tính tốc độ hấp thụ từ dữ liệu canonical** — bảng tổng hợp hấp thụ theo ngày được tính lại từ bản ghi bán hàng canonical, không tính lại từ file.
7. **Dashboard và (về sau) dự báo đọc cùng nguồn canonical** — biểu đồ xu hướng, bộ lọc phân khu, thẻ tổng hợp tồn kho / đã bán / tốc độ 30 ngày.
8. **Hiển thị độ tươi và chất lượng dữ liệu** — mốc cập nhật gần nhất, và trạng thái chất lượng của từng điểm dữ liệu (đủ / chưa đủ cửa sổ lịch sử, ngày được điền bù).

Theo dõi tiến độ xử lý lô bằng polling.

**Ngoài phạm vi MVP 1**

- Dự báo, ngày dự kiến hết hàng, khoảng tin cậy.
- Giải thích bằng LLM và đề xuất hành động.
- Đăng nhập, phân quyền, phê duyệt, audit log.
- Cập nhật đẩy real-time.

**Tiêu chí thành công**

- Nạp được lô dữ liệu thật của dự án pilot vào bảng canonical; toàn bộ lỗi dữ liệu hiện đúng theo dòng và cột.
- Nạp lại cùng một lô bị chặn; không có bản ghi nhân bản trong bảng canonical.
- Từ một con số trên dashboard truy ngược được về lô nạp và file nguồn.
- Dashboard hiển thị tốc độ hấp thụ cho 2–3 phân khu pilot, khớp với số liệu khách hàng tự đối chiếu.
- Biểu đồ tốc độ hấp thụ tải dưới 2 giây ở quy mô pilot.

### 5.2 MVP 2 — Dự báo, giải thích & cảnh báo

**Mục tiêu:** chuyển từ nhìn lại quá khứ sang nhìn trước rủi ro — mỗi phân khu có ngày dự kiến hết hàng, mức độ tin cậy, lời giải thích dễ hiểu và thứ tự ưu tiên hành động. Giải quyết P3. **Toàn bộ đầu vào đọc từ tầng canonical của MVP 1.**

**Tính năng bắt buộc**

- Job dự báo chạy tự động 02:00 hằng ngày; cho phép Manager kích hoạt thủ công (giới hạn tần suất). Đầu vào là bảng hấp thụ canonical, **không** phải file thô.
- Prophet dự báo tốc độ bán và **ngày dự kiến hết hàng**, kèm **khoảng tin cậy 90%**; gắn nhãn "độ tin cậy thấp" khi dữ liệu mỏng.
- Giải thích tiếng Việt cho mỗi dự báo: yếu tố chính (xu hướng, mùa vụ, thay đổi tồn kho) và giả định đã dùng.
- Cảnh báo cạn hàng trong ứng dụng khi số ngày tồn kho dự kiến dưới ngưỡng do Manager cấu hình (mặc định 30 ngày).
- Xếp hạng phân khu theo mức rủi ro tồn kho, kèm hướng hành động đề xuất (siết ưu đãi / kích cầu) — ở MVP 2 chỉ hiển thị, chưa duyệt.
- Theo dõi tiến độ job dự báo theo thời gian thực, có fallback khi mất kết nối.
- Báo cáo MAPE theo phân khu.

**Ngoài phạm vi MVP 2**

- Luồng phê duyệt HITL — đề xuất chưa có trạng thái duyệt.
- Đăng nhập, phân quyền, audit log.
- So sánh nhiều mô hình dự báo, mô phỏng what-if, tự động huấn luyện lại.
- Cảnh báo ngoài ứng dụng.

**Tiêu chí thành công**

- 100% dự báo hiển thị kèm khoảng tin cậy 90% và giả định.
- Mỗi dự báo có đoạn giải thích tiếng Việt đọc hiểu được, không cần đọc số thô.
- Cảnh báo cạn hàng đúng phân khu, đúng số ngày so với ngưỡng cấu hình.
- MAPE được tính trên tập kiểm chứng của dữ liệu pilot và ghi nhận trong báo cáo.
- Mỗi dự báo ghi lại lô dữ liệu nguồn đã dùng; không có thao tác nào của dự báo ghi đè dữ liệu bán hàng / tồn kho canonical.

### 5.3 MVP 3 — Phê duyệt HITL, phân quyền & kiểm toán

**Mục tiêu:** biến đề xuất của hệ thống thành quyết định có người chịu trách nhiệm và có vết truy ngược; mỗi vai trò chỉ thấy phần dữ liệu thuộc phạm vi của mình. Giải quyết P4.

**Tính năng bắt buộc**

- Đăng nhập; phân quyền 3 vai trò: Sales Staff chỉ thấy phân khu được phân công, Sales Manager thấy toàn dự án và có quyền duyệt, Viewer chỉ đọc.
- Đề xuất mặc định ở trạng thái *Chờ duyệt*; Manager duyệt hoặc từ chối, từ chối bắt buộc nêu lý do; chỉ đề xuất *Đã duyệt* mới được đánh dấu có hiệu lực.
- Audit log chỉ ghi thêm (append-only), không sửa và không xoá: ghi ai làm gì, lúc nào, trên lô dữ liệu canonical nào; có màn hình tra cứu cho Manager.
- Trạng thái đề xuất cập nhật real-time cho các phiên đang mở, có fallback khi mất kết nối.
- Quản trị người dùng: gán vai trò và phân khu phụ trách.

**Ngoài phạm vi MVP 3**

- SSO / OAuth2, xác thực đa yếu tố.
- Thông báo ngoài ứng dụng khi có đề xuất chờ duyệt.
- Xuất audit log sang hệ thống lưu trữ / giám sát bên ngoài.
- Phân quyền chi tiết theo từng trường dữ liệu; mô hình multi-tenant nhiều chủ đầu tư.

**Tiêu chí thành công**

- 100% đề xuất đi qua bước duyệt; 0 trường hợp có hiệu lực mà không có quyết định của Manager, kiểm chứng được qua log.
- Sales Staff không truy cập được dữ liệu phân khu ngoài phân công, kể cả khi gọi thẳng API.
- Với một quyết định bất kỳ, truy ngược được chuỗi **quyết định → dự báo → dữ liệu canonical → lô/file nguồn**.

### 5.4 Ngoài phạm vi toàn sản phẩm (cân nhắc sau pilot)

- Data warehouse / lakehouse riêng tách khỏi PostgreSQL của ứng dụng.
- Kết nối trực tiếp CRM/ERP theo API — phiên bản đầu chỉ nhận Excel/CSV.
- SSO / OAuth2 và xác thực đa yếu tố (MFA).
- Multi-tenant phục vụ nhiều chủ đầu tư trên cùng hệ thống.
- Cảnh báo đa kênh: email, Zalo, Slack.
- So sánh & tự chọn nhiều mô hình dự báo (ARIMA, ML khác); tự động huấn luyện lại mô hình.
- Mô phỏng what-if tác động thay đổi giá / chính sách.
- Agent tự động thực thi thay đổi giá / chính sách chiết khấu.

---

## 6. Luồng người dùng & User Stories

### 6.1 Ba luồng chính

**Luồng 1 — Nạp dữ liệu vào tầng canonical và đọc dashboard (MVP 1)**

1. Sales Manager tạo dự án và phân khu (master data), rồi tải lô dữ liệu bán hàng / tồn kho đã được duyệt lên hệ thống.
2. Hệ thống tính checksum để chặn nạp trùng, rồi kiểm tra từng dòng; nếu có lỗi, trả danh sách lỗi kèm số dòng và tên cột để Manager sửa và nạp lại. Lô vượt ngưỡng lỗi bị từ chối nguyên vẹn.
3. Lô hợp lệ được chuẩn hoá và ghi vào **bảng canonical** trong một transaction, kèm tham chiếu tới lô nạp và file nguồn; tốc độ hấp thụ được tính lại **từ dữ liệu canonical**.
4. Sales Staff mở dashboard — đọc từ chính tầng canonical đó — chọn phân khu phụ trách, xem biểu đồ xu hướng, các chỉ số tổng hợp, mốc cập nhật gần nhất và trạng thái chất lượng dữ liệu.

**Luồng 2 — Chạy dự báo và đọc giải thích (MVP 2)**

1. Job dự báo chạy tự động lúc 02:00, hoặc Manager kích hoạt thủ công sau khi nạp dữ liệu mới; job đọc đầu vào từ bảng canonical.
2. Người dùng theo dõi tiến độ job ngay trên màn hình.
3. Job hoàn tất: mỗi phân khu có ngày dự kiến hết hàng, khoảng tin cậy 90% và đoạn giải thích tiếng Việt.
4. Hệ thống sinh cảnh báo cho phân khu dưới ngưỡng và xếp hạng toàn bộ phân khu theo mức rủi ro tồn kho.
5. Manager đọc giải thích, đối chiếu với hiểu biết thực tế để đánh giá độ hợp lý của dự báo.

**Luồng 3 — Duyệt đề xuất và truy vết (MVP 3)**

1. Người dùng đăng nhập; hệ thống chỉ hiển thị dữ liệu trong phạm vi vai trò.
2. Manager mở danh sách đề xuất đang *Chờ duyệt*, xem dự báo nguồn và giải thích kèm theo.
3. Manager duyệt, hoặc từ chối kèm lý do bắt buộc.
4. Trạng thái đề xuất cập nhật ngay trên màn hình của các thành viên đang mở dashboard.
5. Quyết định được ghi vào audit log append-only; sau này Manager tra cứu lại được ai duyệt, lúc nào, dựa trên lô dữ liệu canonical nào.

### 6.2 User Stories

| ID | MVP | User Story | Tiêu chí chấp nhận |
| --- | --- | --- | --- |
| **FR-01** | 1 | Là quản lý kinh doanh, tôi muốn nạp dữ liệu bán hàng & tồn kho định kỳ vào tầng canonical để hệ thống luôn phản ánh giỏ hàng hiện tại. | Nạp Excel/CSV theo template quy định; báo lỗi rõ ràng theo dòng và cột khi dữ liệu không hợp lệ; lô lỗi vượt ngưỡng bị từ chối nguyên vẹn, không làm hỏng dữ liệu canonical đã có. |
| **FR-01b** | 1 | Là quản lý kinh doanh, tôi muốn hệ thống chặn việc nạp lại cùng một lô để số liệu không bị đếm hai lần. | Upload trùng (cùng dự án, cùng checksum) bị từ chối và chỉ ra lô đã nạp trước đó; không có bản ghi nhân bản trong bảng canonical. |
| **FR-01c** | 1 | Là quản lý kinh doanh, tôi muốn biết một con số trên dashboard đến từ lô dữ liệu nào để đối chiếu khi có nghi ngờ. | Mỗi bản ghi canonical mang tham chiếu tới lô nạp và file nguồn; file gốc được giữ lại phục vụ kiểm toán và nạp lại. |
| **FR-02** | 1 | Là nhân viên kinh doanh, tôi muốn xem tốc độ hấp thụ của phân khu / loại căn mình phụ trách để biết căn nào cần tư vấn gấp. | Dashboard đọc từ tầng canonical, hiển thị tốc độ hấp thụ dưới dạng biểu đồ xu hướng, kèm mốc thời gian cập nhật gần nhất. |
| **FR-02b** | 1 | Là người dùng dashboard, tôi muốn biết dữ liệu đang xem tươi tới đâu và đáng tin tới đâu. | Hiển thị mốc cập nhật gần nhất của dự án và trạng thái chất lượng của từng điểm trong chuỗi (đủ / chưa đủ cửa sổ lịch sử, ngày được điền bù). |
| **FR-03** | 2 | Là quản lý kinh doanh, tôi muốn biết mỗi phân khu còn bán được bao lâu để chủ động điều chỉnh chính sách. | Mỗi phân khu có ngày dự kiến hết hàng, cập nhật sau mỗi lần chạy job dự báo hằng ngày. |
| **FR-04** | 2 | Là quản lý kinh doanh, tôi muốn xem khoảng tin cậy của mỗi dự báo để đánh giá độ rủi ro. | Mỗi số liệu dự báo hiển thị kèm khoảng tin cậy 90%; dự báo dựa trên dữ liệu mỏng được gắn nhãn "độ tin cậy thấp". |
| **FR-05** | 2 | Là quản lý kinh doanh, tôi muốn xem giải thích các yếu tố ảnh hưởng đến tốc độ bán để hiểu nguyên nhân. | Mỗi dự báo kèm đoạn giải thích tiếng Việt, liệt kê yếu tố chính (xu hướng, mùa vụ, thay đổi tồn kho) và giả định đã dùng. |
| **FR-06** | 2 | Là nhân viên kinh doanh, tôi muốn nhận cảnh báo khi một loại căn sắp hết hàng để chủ động tư vấn khách. | Cảnh báo trong ứng dụng khi số ngày tồn kho dự kiến < ngưỡng cấu hình; nêu rõ phân khu, ngày dự kiến hết hàng và mức tin cậy. |
| **FR-07** | 2 | Là quản lý kinh doanh, tôi muốn xem danh sách phân khu xếp theo mức rủi ro tồn kho để ưu tiên hành động. | Danh sách xếp hạng theo rủi ro tồn kho, kèm hướng hành động đề xuất (siết ưu đãi / kích cầu). |
| **FR-08** | 2 | Là người dùng, tôi muốn thấy tiến độ khi hệ thống đang chạy dự báo để biết khi nào có kết quả. | Màn hình hiển thị tiến độ theo thời gian thực; khi mất kết nối, tự chuyển sang cập nhật định kỳ mà không mất trạng thái. |
| **FR-09** | 3 | Là quản lý kinh doanh, tôi muốn duyệt hoặc từ chối đề xuất chính sách trước khi áp dụng. | Đề xuất mặc định *Chờ duyệt*; chỉ chuyển *Đã duyệt* khi Manager xác nhận; từ chối bắt buộc nêu lý do. |
| **FR-10** | 3 | Là nhân viên kinh doanh, tôi muốn thấy ngay khi một đề xuất được duyệt để hành động kịp thời. | Trạng thái đề xuất cập nhật trên màn hình đang mở mà không cần tải lại trang. |
| **FR-11** | 3 | Là quản lý kinh doanh, tôi muốn mỗi người chỉ thấy phần dữ liệu thuộc phạm vi của mình. | Sales Staff chỉ thấy phân khu được phân công; Viewer chỉ đọc; quyền được kiểm tra ở phía hệ thống, không chỉ ẩn trên giao diện. |
| **FR-12** | 3 | Là quản lý kinh doanh, tôi muốn tra cứu lại lịch sử đề xuất và quyết định để rà soát chính sách. | Mọi dự báo, đề xuất và quyết định được lưu kèm người thực hiện, thời điểm, lô dữ liệu canonical đã dùng; có màn hình tra cứu; bản ghi không sửa hay xoá được. |

---

## 6b. Quyền sở hữu dữ liệu (Data Ownership)

Bảy quy tắc dưới đây là ràng buộc kiến trúc bắt buộc, áp dụng cho cả bốn tài liệu sản phẩm và cho mã nguồn. Vi phạm bất kỳ quy tắc nào là lỗi thiết kế, không phải lựa chọn triển khai.

| # | Quy tắc | Hệ quả thiết kế |
| --- | --- | --- |
| 1 | **Ingestion là đường ghi duy nhất** cho dữ liệu bán hàng / tồn kho nạp từ ngoài | Không có endpoint, script hay job nào khác được ghi vào bảng bán hàng / tồn kho canonical |
| 2 | **Bảng canonical là nguồn đọc** cho dashboard, phân tích và dự báo | Không có nhánh nào đọc lại file thô để hiển thị hay tính toán; hai màn hình không thể ra hai con số vì đọc hai bản dữ liệu khác nhau |
| 3 | **File thô là artifact nguồn và bản ghi lineage** | Giữ lại để kiểm toán và nạp lại; **không** phải hệ thống ghi nhận |
| 4 | **AI agent không đọc file thô sau khi ingest** | Đầu vào của LangGraph/LLM và Prophet chỉ là dữ liệu canonical đã tổng hợp theo phân khu |
| 5 | **Dự báo và đề xuất là bản ghi dẫn xuất** | Ghi vào bảng riêng; không bao giờ `UPDATE` dữ liệu bán hàng / tồn kho nguồn |
| 6 | **Audit log chỉ ghi thêm** | Quyền của ứng dụng trên bảng audit chỉ có `INSERT`/`SELECT` |
| 7 | **Seed data chỉ dùng cho dev/test** | Không nằm trong migration, không xuất hiện trong luồng khách hàng, không được trình bày như dữ liệu thật khi demo |

---

## 7. Yêu cầu phi chức năng

Chi tiết kỹ thuật xem SRS mục 4; bảng dưới nêu mức yêu cầu ở góc nhìn sản phẩm.

| Nhóm | Yêu cầu |
| --- | --- |
| **Toàn vẹn tầng canonical** | Lô nạp vượt ngưỡng lỗi bị từ chối nguyên vẹn (transaction hoặc rollback); mọi bản ghi canonical giữ được tham chiếu lô/file nguồn; nạp trùng bị chặn ở mức dữ liệu, không chỉ ở giao diện |
| **Bảo mật** | Đăng nhập bắt buộc và phân quyền 3 vai trò từ MVP 3, kiểm tra ở phía hệ thống; dữ liệu bán hàng không rời phạm vi nội bộ; dữ liệu khách hàng được ẩn danh trước khi đưa vào hệ thống |
| **Tần suất cập nhật** | Dữ liệu và dự báo cập nhật tối thiểu 1 lần/ngày (job 02:00); trạng thái job và đề xuất cập nhật real-time từ MVP 2/3 |
| **Hiệu năng** | Biểu đồ tốc độ hấp thụ tải < 2 giây ở quy mô pilot; job dự báo hoàn tất < 10 phút cho 500 phân khu |
| **Kiểm soát chi phí** | Không tính lại mô hình / gọi LLM quá 1 lần/ngày/phân khu trừ khi có dữ liệu mới; ghi nhận số lần gọi LLM để theo dõi chi phí |
| **Độ tin cậy** | 100% dự báo đi kèm khoảng tin cậy và giả định rõ ràng; mất kết nối real-time không làm gián đoạn sử dụng nhờ cơ chế cập nhật định kỳ thay thế |
| **Khả năng mở rộng** | Dữ liệu và API gắn theo dự án / phân khu để mở rộng sang dự án khác mà không đổi cấu trúc |
| **Khả năng kiểm toán** | Lưu toàn bộ lịch sử dự báo, đề xuất và quyết định duyệt / từ chối; bản ghi kiểm toán không sửa hay xoá được |

---

## 8. Kiến trúc & công nghệ

**Luồng xử lý:**

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

| Tầng | Công nghệ | Vai trò |
| --- | --- | --- |
| Tầng dữ liệu canonical | PostgreSQL 15 | Nguồn có thẩm quyền cho dữ liệu bán hàng, tồn kho, hấp thụ, dự báo, đề xuất và audit log **đã chuẩn hoá** |
| Truy cập dữ liệu & migration | SQLAlchemy Core + asyncpg, Alembic | Định nghĩa và tiến hoá lược đồ canonical theo từng revision |
| Ingestion | FastAPI (nhận file) + Redis/RQ worker | Đường ghi duy nhất: parse → validate → normalize → ghi canonical trong một transaction |
| Dự báo | Prophet (Python) | Dự báo tốc độ bán & ngày dự kiến hết hàng kèm khoảng tin cậy, **đọc từ canonical** |
| Điều phối Agent | LangGraph | Điều phối luồng: đọc canonical → tổng hợp → giải thích → đề xuất hành động |
| Diễn giải | LLM | Sinh giải thích tiếng Việt cho mỗi dự báo, chỉ nhận số liệu tổng hợp theo phân khu |
| API | FastAPI | Cung cấp API đọc/ghi cho dashboard và dịch vụ nội bộ |
| Giao diện | ReactJS | Dashboard biểu đồ cho nhân viên và quản lý kinh doanh, **đọc từ canonical** |
| Cập nhật real-time | WebSocket | Đẩy tiến độ job dự báo (MVP 2) và thay đổi trạng thái đề xuất (MVP 3) |
| Lưu trữ file nguồn | Volume `uploads/` dùng chung API–worker | Giữ file gốc làm artifact lineage; không phải nguồn đọc của bất kỳ tính năng nào |
| Triển khai | Fly.io (thay thế: Render); Docker Compose cho dev | Môi trường pilot với PostgreSQL quản lý sẵn |

---

## 9. Ràng buộc

- **Nguồn sự thật:** PostgreSQL là nguồn sự thật duy nhất cho dữ liệu đã chuẩn hoá; ingestion là đường ghi duy nhất; file thô chỉ là artifact nguồn và bản ghi lineage (xem §6b).
- **HITL bắt buộc:** mọi quyết định chính sách bán hàng dựa trên dự báo phải được quản lý kinh doanh phê duyệt trước khi áp dụng; Agent không tự động thực thi thay đổi giá / chính sách.
- **Minh bạch dự báo:** mỗi dự báo phải kèm khoảng tin cậy và giả định rõ ràng, tránh hiểu lầm là số liệu chắc chắn.
- **Bảo mật dữ liệu:** dữ liệu bán hàng là thông tin nhạy cảm, tuân thủ chính sách bảo mật nội bộ và phân quyền chặt chẽ.
- **Kiểm soát chi phí:** giới hạn tần suất tính lại mô hình / gọi LLM.
- **Thời gian:** MVP hoàn thành trong 5 tuần, sau đó 1–2 tuần pilot thu thập phản hồi.

---

## 10. Giả định

Từ brief:

- Có sẵn dữ liệu lịch sử bán hàng và tồn kho tối thiểu vài tháng gần nhất để huấn luyện mô hình.
- Dữ liệu được cung cấp qua file Excel/CSV định kỳ từ nguồn đã được phía khách hàng duyệt (kết nối CRM/ERP nằm ngoài MVP).
- Có ít nhất một quản lý kinh doanh tham gia làm đầu mối phê duyệt (HITL) trong giai đoạn thử nghiệm.
- Khách hàng chấp nhận tầng canonical trong PostgreSQL là nguồn có thẩm quyền cho các số liệu mà sản phẩm này hiển thị và dự báo — trong phạm vi dữ liệu đã nạp qua ingestion, không phải cho toàn doanh nghiệp. `[NEEDS CONFIRMATION]`

Giả định bổ sung của PRD (cần xác nhận trong Tuần 1):

- Phạm vi pilot: **1 dự án, 2–3 phân khu / loại căn đại diện** (ít nhất 1 bán chạy, 1 bán chậm).
- Đơn vị dữ liệu tối thiểu: số căn bán được theo ngày, theo phân khu / loại căn.
- Ngưỡng cảnh báo cạn hàng mặc định: **30 ngày tồn kho dự kiến**, cho phép quản lý điều chỉnh.
- Khoảng tin cậy hiển thị mặc định: **90%**.
- Số lượng người dùng pilot: 3–5 nhân viên kinh doanh + 1 quản lý kinh doanh.

---

## 11. Chỉ số thành công (KPIs)

| KPI | Định nghĩa | Mục tiêu pilot |
| --- | --- | --- |
| Tỷ lệ nạp thành công vào canonical | % lô upload được chuẩn hoá và ghi vào bảng canonical; số lô bị từ chối và lý do | Đo và báo cáo mỗi tuần pilot |
| Độ phủ lineage | % bản ghi canonical truy ngược được về lô nạp và file nguồn | 100% |
| Chặn nạp trùng | Số lần upload trùng bị chặn; số bản ghi nhân bản trong bảng canonical | Bản ghi nhân bản = 0 |
| Độ chính xác dự báo | MAPE trung bình theo phân khu / loại căn | Đo được và công bố sau mỗi chu kỳ đánh giá; cải thiện qua các lần huấn luyện lại |
| Độ trễ phát hiện | Thời gian từ khi nạp lô dữ liệu mới đến khi hệ thống cảnh báo | < 24 giờ |
| Thời gian ra quyết định | Thời gian trung bình từ khi phát hiện bất thường đến khi chính sách được duyệt | Giảm so với baseline của khách hàng (baseline xác định ở Tuần 1 — `[NEEDS CONFIRMATION]`) |
| Tỷ lệ cảnh báo chính xác | % cảnh báo cạn hàng được xác nhận đúng trên thực tế | Theo dõi & báo cáo cuối pilot |
| Tỷ lệ tuân thủ HITL | % đề xuất được duyệt trước khi áp dụng | 100%; 0 trường hợp tự động thực thi |
| Mức độ sử dụng | Số lượt truy cập dashboard/tuần của nhân viên & quản lý | Theo dõi hằng tuần trong pilot |
| Mức độ hài lòng | Khảo sát nhanh cuối pilot | Đa số người dùng xác nhận số liệu trên dashboard khớp với số họ tự đối chiếu và dùng được để ra quyết định |

---

## 12. Tiêu chí chấp nhận MVP (Definition of Done)

Sản phẩm được coi là hoàn thành khi **tất cả** điều kiện sau đạt:

*MVP 1*

1. Nạp được file Excel/CSV thực tế của dự án pilot vào bảng canonical; dữ liệu sai được báo lỗi theo dòng và cột; lô vượt ngưỡng lỗi bị từ chối nguyên vẹn, không làm hỏng dữ liệu canonical đã có.
2. Nạp lại cùng một lô bị chặn; không có bản ghi nhân bản trong bảng canonical.
3. Mọi bản ghi canonical truy ngược được về lô nạp và file nguồn.
4. Dashboard đọc từ tầng canonical, hiển thị tốc độ hấp thụ theo phân khu / loại căn, kèm mốc cập nhật gần nhất và trạng thái chất lượng dữ liệu; cập nhật tự động ít nhất 1 lần/ngày.

*MVP 2*

5. Mỗi phân khu / loại căn có dự báo ngày dự kiến hết hàng kèm khoảng tin cậy 90%, tính từ dữ liệu canonical.
6. Cảnh báo cạn hàng hiển thị đúng khi tồn kho dự kiến dưới ngưỡng cấu hình.
7. Mỗi dự báo có đoạn giải thích tiếng Việt nêu yếu tố chính và giả định.
8. Danh sách phân khu xếp hạng theo rủi ro tồn kho kèm hướng hành động đề xuất.
9. Tiến độ job dự báo hiển thị theo thời gian thực; mất kết nối thì tự chuyển sang cập nhật định kỳ.
10. MAPE của mô hình được tính trên tập kiểm chứng của dữ liệu pilot và ghi nhận trong báo cáo.
11. Không thao tác nào của luồng dự báo ghi đè dữ liệu bán hàng / tồn kho canonical.

*MVP 3*

12. Luồng HITL hoạt động đầy đủ: đề xuất chỉ có hiệu lực sau khi quản lý duyệt; log ghi đủ người duyệt, thời điểm, lý do.
13. Phân quyền hoạt động: nhân viên chỉ thấy phân khu phụ trách, quản lý thấy toàn dự án và có quyền duyệt, Viewer chỉ đọc.
14. Trạng thái đề xuất cập nhật real-time trên các phiên đang mở.
15. Audit log append-only, truy ngược được chuỗi **quyết định → dự báo → dữ liệu canonical → lô/file nguồn**.

*Chung*

16. Hệ thống được triển khai trên môi trường pilot và chạy được job dự báo hằng ngày lúc 02:00.
17. Không có dữ liệu seed nào xuất hiện trong môi trường pilot của khách hàng.

---

## 13. Kế hoạch triển khai (5 tuần)

| Tuần | Nội dung chính | Đầu ra |
| --- | --- | --- |
| **Tuần 1** | Chốt scope với ban kinh doanh: template dữ liệu, ngưỡng cảnh báo, phân khu pilot; thiết kế schema PostgreSQL và kiến trúc tổng thể | Bản chốt scope + template Excel/CSV + schema dữ liệu |
| **Tuần 2** | **MVP 1**: ingestion & validation, tầng dữ liệu canonical (chuẩn hoá, chống trùng, lineage), tính tốc độ hấp thụ, dashboard đọc từ canonical | MVP 1 chạy được trên dữ liệu pilot |
| **Tuần 3** | **MVP 2**: dự báo Prophet + khoảng tin cậy, LangGraph agent giải thích & đề xuất, cảnh báo cạn hàng, tiến độ job real-time | MVP 2 chạy được, có báo cáo MAPE đầu tiên |
| **Tuần 4** | **MVP 3**: đăng nhập & phân quyền, luồng duyệt HITL, audit log, cập nhật đề xuất real-time | MVP 3 tích hợp đầy đủ theo Mục 12 |
| **Tuần 5** | Kiểm thử đầu-cuối, tối ưu mô hình, triển khai môi trường pilot, demo & thu thập phản hồi | Hệ thống triển khai + báo cáo MAPE + demo |

*Pilot 1–2 tuần sau Tuần 5: vận hành thực tế với dự án pilot, thu phản hồi hằng tuần, tổng kết KPI.*

---

## 14. Rủi ro & phương án giảm thiểu

| Rủi ro | Ảnh hưởng | Giảm thiểu |
| --- | --- | --- |
| Dữ liệu lịch sử không đầy đủ / chất lượng thấp | Dự báo kém chính xác | Validate dữ liệu đầu vào; gắn nhãn "độ tin cậy thấp"; yêu cầu duyệt kỹ hơn khi thiếu dữ liệu |
| Người dùng phụ thuộc quá mức vào AI | Quyết định chính sách sai lệch | Duy trì HITL bắt buộc; hướng dẫn người dùng hiểu giới hạn mô hình |
| Chi phí compute / LLM tăng khi mở rộng | Vượt ngân sách vận hành | Giới hạn tần suất tính lại; ghi nhận và theo dõi số lần gọi LLM |
| Không kịp truy cập dữ liệu thực tế trong Tuần 2 | Trễ toàn bộ tiến độ | Dùng bộ dữ liệu seed dev/test đúng template để phát triển song song, thay bằng dữ liệu thật khi có; **seed không được trình bày như dữ liệu khách hàng** khi demo |
| Nguồn dữ liệu đầu vào chưa được khách hàng "duyệt" rõ ràng | Tầng canonical mất tính thẩm quyền ngay từ gốc | Chốt Tuần 1: ai ký nhận lô dữ liệu, theo template nào, tần suất nào; ghi người nạp và thời điểm vào bản ghi lô — `[NEEDS CONFIRMATION]` |
| Khách hàng hiểu sản phẩm chỉ là "thêm một dashboard" | Không thấy giá trị của tầng canonical, từ chối tiếp nhận | Trình bày giá trị theo trục dữ liệu: kiểm tra theo dòng, chống trùng, lineage, một nguồn đọc chung — demo phần truy vết từ số liệu về file nguồn |

---

## 15. Các bên liên quan

| Bên liên quan | Vai trò |
| --- | --- |
| Product Owner (học viên) | Định nghĩa yêu cầu, phạm vi, ưu tiên tính năng |
| Ban Kinh doanh (Sales) | Người dùng chính; cung cấp dữ liệu nghiệp vụ và phản hồi |
| Đội kỹ thuật (Data/AI, Backend, Frontend) | Xây dựng, kiểm thử và triển khai hệ thống |
| Giảng viên / Mentor VinUni × Vingroup | Đánh giá tiến độ, góp ý chuyên môn |

---

## 16. Đề xuất hỗ trợ (Ask)

- **Dữ liệu:** quyền truy cập dữ liệu bán hàng & tồn kho lịch sử (đã ẩn danh) của ít nhất 1 dự án thực tế.
- **Đầu mối nghiệp vụ:** 1 quản lý kinh doanh làm đầu mối duyệt dự báo và xác nhận tiêu chí cảnh báo.
- **Cố vấn kỹ thuật:** hỗ trợ về lựa chọn mô hình dự báo và thiết kế kiến trúc LangGraph agent.
- **Hạ tầng:** ngân sách thử nghiệm cho compute và gọi API LLM ở mức nhỏ.
- **Thời gian:** 5 tuần MVP + 1–2 tuần pilot trước khi báo cáo kết quả cuối khoá.

---

## 17. Phụ lục — Định nghĩa

| Thuật ngữ | Ý nghĩa |
| --- | --- |
| **Dữ liệu canonical** | Biểu diễn đã chuẩn hoá, đã kiểm tra của dữ liệu bán hàng / tồn kho / hấp thụ, nằm trong PostgreSQL. Là nguồn có thẩm quyền cho mọi tiêu dùng: dashboard, phân tích, dự báo, AI agent |
| **Tầng dữ liệu canonical** | Tập hợp bảng canonical + các ràng buộc bảo đảm tính toàn vẹn của chúng (khoá, CHECK, UNIQUE chống trùng, khoá ngoại lineage) |
| **Ingestion** | Đường ghi duy nhất đưa dữ liệu từ ngoài vào tầng canonical: nhận file → parse → validate → normalize → ghi trong một transaction |
| **Lô nạp (batch)** | Một lần upload được ghi nhận thành một bản ghi lô, mang tên file, checksum, thời điểm, số dòng OK/lỗi. Mọi bản ghi canonical trỏ về lô đã sinh ra nó |
| **Lineage** | Chuỗi truy vết từ một bản ghi canonical (hoặc một con số dẫn xuất) về lô nạp và file nguồn |
| **Artifact nguồn (file thô)** | File gốc người dùng tải lên. Được giữ lại để kiểm toán và nạp lại; **không** phải hệ thống ghi nhận |
| **Bản ghi dẫn xuất** | Dự báo, giải thích, cảnh báo, đề xuất — tính ra từ dữ liệu canonical, ghi vào bảng riêng, không bao giờ ghi đè dữ liệu nguồn |
| **Seed data** | Bộ dữ liệu hư cấu dùng cho dev/test. Không nằm trong migration, không xuất hiện trong luồng khách hàng |
| Tốc độ hấp thụ | Số căn bán được trên một đơn vị thời gian của một phân khu / loại căn |
| Tỷ lệ hấp thụ (absorption rate) | % sản phẩm đã bán trên tổng sản phẩm mở bán |
| Phân khu / loại căn | Đơn vị phân tích: nhóm căn hộ theo phân khu, diện tích, số phòng ngủ, hướng, tầng |
| Ngày dự kiến hết hàng | Ngày tồn kho của một phân khu / loại căn được dự báo về 0 theo tốc độ hấp thụ hiện tại |
| MAPE | Sai số phần trăm tuyệt đối trung bình — thước đo độ chính xác dự báo |
| HITL | Human-in-the-loop — cơ chế bắt buộc người duyệt trước khi áp dụng đề xuất |
| Audit log | Nhật ký chỉ ghi thêm (append-only) mọi dự báo, đề xuất và quyết định duyệt / từ chối; không sửa hay xoá được |
| Real-time (WebSocket) | Cập nhật đẩy thẳng lên màn hình đang mở — dùng cho tiến độ job dự báo và trạng thái đề xuất |

---

## 18. Bảng truy vết (Traceability)

Cột **Code/schema evidence** đọc trực tiếp từ mã nguồn, `alembic/versions/*` và `pipeline_status.md` (2026-08-07). Một hạng mục **không** được coi là đã cài đặt chỉ vì nó xuất hiện trong PRD hoặc SRS.

Trạng thái: `Confirmed` (quyết định sản phẩm đã chốt) · `Implemented` · `Partially implemented` · `Planned` · `Needs confirmation` · `Removed/reframed`.

| Requirement/Product claim | PRD | SRS | Code/schema evidence | Status |
|---|---|---|---|---|
| PostgreSQL là nguồn sự thật cho dữ liệu đã chuẩn hoá | §0, §6b, §8 | §1.2, §2.5, §5 | `alembic/versions/0001…0004` — 21 bảng; không có kho dữ liệu thứ hai trong repo | Implemented |
| Ingestion là đường ghi duy nhất cho sales/inventory | §6b | §2.5 | `src/api/files.py` → `src/jobs/parse_upload.py` → `src/services/import_records.py`; không có đường ghi nào khác trong `src/api/` | Implemented |
| Nạp Excel/CSV theo template, ≤ 20 MB | §5.1 (1) | FR-001 | `POST /api/v1/files/upload`; `src/services/file_upload.py`; `TEMPLATES = {sales, inventory, areas}` | Implemented |
| Kiểm tra từng dòng, báo lỗi theo dòng/cột | §5.1 (2) | FR-002 | `src/services/excel_parser.py`; bảng `upload_errors`; `GET /files/{id}/errors`, `/errors.csv` | Implemented |
| Lô vượt ngưỡng lỗi bị từ chối nguyên vẹn | §5.1 (2), §7 | NFR-R3 | `ImportRejectedError` + `session.begin()` một transaction; `settings.error_threshold` | Implemented |
| Chặn nạp trùng | §5.1 (4), FR-01b | FR-024 | `uq_upload_files_project_checksum`; `_find_duplicate()` → 409 `DUPLICATE_FILE` | Implemented |
| Lineage lô/file nguồn | §5.1 (5), FR-01c | FR-025 | `upload_files`; `sales_records.file_id`, `inventory_snapshots.file_id`, `source_row_hash`, `external_record_id` | Implemented |
| Chuẩn hoá & lưu canonical trong một transaction | §5.1 (3) | FR-002, NFR-R3 | `ImportService` — phân giải `area_name`→`area_id`, insert theo lô, cập nhật `rows_ok`/`rows_failed` | Implemented |
| Tính hấp thụ **từ** dữ liệu canonical | §5.1 (6), FR-03 | FR-003 | `src/services/absorption.py` — `recompute()` đọc `sales_records`, ghi `absorption_daily` | Implemented |
| Dashboard đọc cùng nguồn canonical | §5.1 (7), FR-02 | FR-004 | `GET /api/v1/absorption`, `/absorption/summary`; `frontend/src/api/endpoints.js` không có đường đọc file thô | Implemented |
| Hiển thị độ tươi dữ liệu | §5.1 (8), FR-02b | FR-026 | `AbsorptionSummaryOut.updated_at` | Implemented |
| Hiển thị trạng thái chất lượng dữ liệu | §5.1 (8), FR-02b | FR-027 | `absorption_daily.data_quality_status` (`ok`/`warning`; `error` có trong CHECK nhưng chưa có đường sinh), `is_observed` | Partially implemented |
| Master data: tạo/sửa dự án & phân khu | §6.1 | §5.2 | `POST`/`PATCH /api/v1/projects`, `/areas`; `frontend/src/pages/CatalogPage.jsx` | Implemented |
| Duyệt dự án / phân khu (`pending`→`active`) | — | §5.2 | Cột workflow có ở `0002`; **không có endpoint duyệt**, không nơi nào lọc theo `status` | Planned |
| Dự báo Prophet + CI 90% + ngày hết hàng | §5.2, FR-03/04 | FR-005…FR-008 | `src/jobs/forecast.py` là stub (`TODO (MVP 2)`); `prophet` có trong `requirements.txt` | Planned |
| Giải thích tiếng Việt bằng LangGraph + LLM | §5.2, FR-05 | FR-011 | `src/agents/graph.py` mới là scaffolding (`example_node`); `src/services/llm.py` có sẵn client | Planned |
| Cảnh báo cạn hàng, xếp hạng rủi ro | §5.2, FR-06/07 | FR-009, FR-012, FR-013 | Bảng `alerts`, `suggestions` đã có trong `0001`; chưa có service/endpoint | Planned |
| AI agent chỉ đọc canonical, không đọc file thô | §6b (4) | §2.5 | Chưa kiểm chứng được vì luồng agent chưa cài đặt; là ràng buộc bắt buộc cho MVP 2 | Confirmed |
| Xác thực & RBAC 3 vai trò | §5.3, FR-11 | FR-018 | **Không có tầng auth nào trong mã nguồn** (`pipeline_status.md` — Known Issues); `users`/`user_areas`/`refresh_tokens` mới chỉ có schema | Planned |
| Luồng phê duyệt HITL | §5.3, FR-09 | FR-014, FR-015 | `proposals`, `approvals` có schema; chưa có endpoint duyệt/từ chối | Planned |
| Audit log append-only | §6b (6), FR-12 | FR-016, NFR-L1 | Bảng `audit_logs` có schema; chưa có `AuditLogService`, chưa thu hồi quyền `UPDATE`/`DELETE` | Planned |
| Truy vết quyết định → dự báo → canonical → lô nguồn | §5.3 | §5.7.7 | Chuỗi khoá ngoại đã đủ trong schema (`forecasts.file_id` NOT NULL); chưa có đường đọc | Partially implemented |
| Cập nhật real-time qua WebSocket | §5.2/5.3, FR-08/10 | FR-021…FR-023 | Không có endpoint `/ws/*` trong `src/`; MVP 1 dùng polling | Planned |
| Base path API là `/api/v1` | §8 | §6 | `src/main.py` — `include_router(..., prefix="/api/v1")`; health là `GET /health` | Implemented |
| Seed chỉ dùng cho dev/test | §6b (7) | §2.5 | `scripts/seed_dev.py` — tiền tố `DEMO`, email `@demo.local`, `password_hash` cố ý không hợp lệ; không nằm trong migration | Implemented |
| "Khách hàng có dữ liệu phân tán / không có nguồn sự thật / tổng hợp Excel thủ công hằng tuần / báo cáo thiếu nhất quán" | §2 | §2.1 | Không có bằng chứng khách hàng ở bất kỳ nguồn nào trong repo | Removed/reframed |
| Baseline thời gian ra quyết định hiện tại của khách hàng | §11 | — | Chưa có | Needs confirmation |
| Ai ký nhận / duyệt lô dữ liệu đầu vào | §10, §14 | §2.5 | Chưa có; `upload_files.uploaded_by` luôn NULL vì chưa có auth | Needs confirmation |
