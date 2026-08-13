# Product Requirements Document

**Tên sản phẩm:** AbsorptionForecast AI Agent — Trợ lý dự báo tồn kho & tốc độ hấp thụ căn hộ
**Loại sản phẩm:** AI Agent phân tích dữ liệu & dự báo chuỗi thời gian, có giải thích (explainable) và đề xuất hành động
**Lĩnh vực:** Bất động sản — Kinh doanh & quản lý bán hàng dự án căn hộ
**Ngày cập nhật:** 01/08/2026
**Product Owner:** G21 - T100 - Nguyễn Đức Đạt, Bùi Hoàng Vương, Nguyễn Trọng Nam, Đặng Tiến Thành
**Tài liệu kỹ thuật:** [SRS.md](SRS.md) — nguồn tham chiếu cho phạm vi và kiến trúc
---

## 1. Bối cảnh

Trong các dự án chung cư / khu đô thị quy mô lớn, sản phẩm được chia thành nhiều phân khu và loại căn (diện tích, hướng, tầng, số phòng ngủ). Tốc độ hấp thụ giữa các nhóm rất không đồng đều: có loại "cháy hàng", có loại tồn kho kéo dài.

Ban kinh doanh cần liên tục biết:

- Phân khu / loại căn nào **sắp hết hàng** → cân nhắc tăng giá, siết ưu đãi.
- Phân khu / loại căn nào **bán chậm** → tập trung nguồn lực sale, đẩy chính sách kích cầu.

Dữ liệu để trả lời hai câu hỏi đó **đã tồn tại trong doanh nghiệp** nhưng nằm rải rác ở nhiều file Excel do từng nhóm sale tự quản lý, mỗi file một định dạng, tổng hợp thủ công theo tuần. Vì vậy giá trị cốt lõi của sản phẩm không chỉ là dự báo, mà trước hết là **gom dữ liệu phân tán về một pipeline chuẩn hoá và đã kiểm tra**, rồi biến dữ liệu đó thành thông tin hành động được trên dashboard.

---

## 2. Vấn đề

Doanh nghiệp thiếu năng lực ra quyết định dựa trên trạng thái cập nhật của giỏ hàng và tốc độ hấp thụ theo từng phân khu / loại căn. Vấn đề này gồm bốn lớp, xếp theo thứ tự phải giải quyết:

| # | Nhóm vấn đề | Biểu hiện cụ thể |
| --- | --- | --- |
| **P1** | **Dữ liệu phân tán, quản lý thủ công** | Số liệu bán hàng & tồn kho nằm ở nhiều file Excel/CSV do từng nhóm tự giữ; không có template chung; sai định dạng, thiếu trường và bản ghi trùng chỉ bị phát hiện khi tổng hợp; không có phiên bản dữ liệu nào được coi là chuẩn |
| **P2** | **Báo cáo chậm và thiếu nhất quán** | Báo cáo tổng hợp thủ công theo tuần / theo đợt; hai người tổng hợp cùng một kỳ có thể ra hai con số khác nhau; khi lãnh đạo hỏi "tuần này bán được bao nhiêu" thì mất nhiều giờ để dựng lại số |
| **P3** | **Không có tầm nhìn dự báo và mức độ rủi ro** | Chỉ nhìn được số đã bán trong quá khứ, không biết phân khu nào sẽ hết hàng vào ngày nào; không có mức độ tin cậy đi kèm; việc phán đoán "còn bán được bao lâu" dựa hoàn toàn vào kinh nghiệm cá nhân |
| **P4** | **Không có cơ chế phê duyệt và vết kiểm toán** | Chính sách giá / chiết khấu được quyết qua trao đổi miệng hoặc chat; sau vài tuần không truy được ai đề xuất, ai duyệt, dựa trên số liệu nào; không có cơ sở để rà soát lại quyết định |

**Tác động nếu không giải quyết**

| Hệ quả | Mô tả |
| --- | --- |
| Chậm ra quyết định | Chính sách giá / chiết khấu điều chỉnh trễ, thiếu cơ sở định lượng |
| Bỏ lỡ thời điểm tối ưu | Chậm tăng giá khi cầu cao, chậm kích cầu khi hàng tồn lâu |
| Lãng phí nguồn lực sale | Phân bổ nhân sự tư vấn không dựa trên dữ liệu tốc độ bán thực tế |
| Không rà soát được quyết định | Không đo được chính sách nào hiệu quả vì thiếu vết lưu đề xuất và phê duyệt |

**Phạm vi phiên bản đầu:** sản phẩm giải quyết **hợp nhất dữ liệu nội bộ (P1, P2)** và **hỗ trợ ra quyết định có kiểm soát (P3, P4)**. Đây **không** phải dự án tích hợp CRM/ERP toàn doanh nghiệp — dữ liệu vào qua file Excel/CSV theo template, kết nối API với hệ thống nguồn nằm ngoài phạm vi.

---

## 3. Mục tiêu

### 3.1 Mục tiêu sản phẩm (MVP)

| ID | Mục tiêu | Chỉ tiêu (SMART) trong phạm vi MVP |
| --- | --- | --- |
| O1 | Rút ngắn thời gian phát hiện biến động tốc độ hấp thụ | Dashboard cập nhật tối thiểu 1 lần/ngày; độ trễ từ khi có dữ liệu bán hàng đến khi hệ thống cảnh báo < 24 giờ |
| O2 | Nhắm đúng đối tượng cần chính sách kích cầu | Agent xếp nhóm phân khu / loại căn theo mức rủi ro tồn kho dựa trên tốc độ hấp thụ dự báo; giảm số phân khu phải nhận mức chiết khấu tối đa so với cách áp dụng đại trà |
| O3 | Cải thiện tỷ lệ hấp thụ ở phân khu được theo dõi | So sánh tỷ lệ hấp thụ giữa nhóm có áp dụng đề xuất của Agent và nhóm không áp dụng trong pilot (baseline ngành ~70%, DXS-FERI 6T/2026) |
| O4 | Nâng độ chính xác dự báo so với ước tính cảm tính | MAPE được đo và công bố sau mỗi chu kỳ đánh giá; 100% dự báo hiển thị kèm khoảng tin cậy |
| O5 | Đảm bảo kiểm soát của con người | 100% đề xuất chính sách được quản lý kinh doanh duyệt trước khi áp dụng; 0 trường hợp tự động thực thi, kiểm chứng qua log |

### 3.2 Ngoài mục tiêu (Non-goals)

- Agent **không** tự động thực thi thay đổi giá bán / chính sách chiết khấu.
- Không xử lý giao dịch tài chính, thanh toán, ký kết hợp đồng.
- Không thay thế vai trò tư vấn của nhân viên kinh doanh.

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

### 5.1 MVP 1 — Nạp dữ liệu & Dashboard tốc độ hấp thụ

**Mục tiêu:** thay báo cáo Excel thủ công bằng một pipeline dữ liệu chuẩn hoá duy nhất; sau khi upload file, người dùng xem được ngay tốc độ hấp thụ theo phân khu / loại căn. Giải quyết P1 và P2.

**Tính năng bắt buộc**

- Upload file Excel/CSV bán hàng & tồn kho theo template (tối đa 20 MB), chống upload trùng.
- Validate theo dòng: thiếu trường, sai định dạng, số căn âm, bản ghi trùng — báo lỗi kèm số dòng và tên cột; file lỗi không làm hỏng dữ liệu đã có.
- Tính tốc độ hấp thụ theo phân khu / loại căn.
- Dashboard: biểu đồ xu hướng theo thời gian, bộ lọc phân khu, thẻ tổng hợp tồn kho / đã bán / tốc độ 30 ngày, mốc cập nhật gần nhất.
- Theo dõi tiến độ xử lý file bằng polling.

**Ngoài phạm vi MVP 1**

- Dự báo, ngày dự kiến hết hàng, khoảng tin cậy.
- Giải thích bằng LLM và đề xuất hành động.
- Đăng nhập, phân quyền, phê duyệt, audit log.
- Cập nhật đẩy real-time.

**Tiêu chí thành công**

- Import được file thật của dự án pilot; toàn bộ lỗi dữ liệu hiện đúng theo dòng.
- Dashboard hiển thị đúng tốc độ hấp thụ cho 2–3 phân khu pilot, đối chiếu khớp báo cáo Excel hiện tại.
- Biểu đồ tốc độ hấp thụ tải dưới 2 giây ở quy mô pilot.

### 5.2 MVP 2 — Dự báo, giải thích & cảnh báo

**Mục tiêu:** chuyển từ nhìn lại quá khứ sang nhìn trước rủi ro — mỗi phân khu có ngày dự kiến hết hàng, mức độ tin cậy, lời giải thích dễ hiểu và thứ tự ưu tiên hành động. Giải quyết P3.

**Tính năng bắt buộc**

- Job dự báo chạy tự động 02:00 hằng ngày; cho phép Manager kích hoạt thủ công (giới hạn tần suất).
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

### 5.3 MVP 3 — Phê duyệt HITL, phân quyền & kiểm toán

**Mục tiêu:** biến đề xuất của hệ thống thành quyết định có người chịu trách nhiệm và có vết truy ngược; mỗi vai trò chỉ thấy phần dữ liệu thuộc phạm vi của mình. Giải quyết P4.

**Tính năng bắt buộc**

- Đăng nhập; phân quyền 3 vai trò: Sales Staff chỉ thấy phân khu được phân công, Sales Manager thấy toàn dự án và có quyền duyệt, Viewer chỉ đọc.
- Đề xuất mặc định ở trạng thái *Chờ duyệt*; Manager duyệt hoặc từ chối, từ chối bắt buộc nêu lý do; chỉ đề xuất *Đã duyệt* mới được đánh dấu có hiệu lực.
- Audit log không sửa được: ghi ai làm gì, lúc nào, trên dữ liệu đầu vào phiên bản nào; có màn hình tra cứu cho Manager.
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
- Với một quyết định bất kỳ, truy ngược được dự báo nguồn và file dữ liệu đã tạo ra nó.

### 5.4 Ngoài phạm vi toàn sản phẩm (cân nhắc sau pilot)

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

**Luồng 1 — Nạp dữ liệu và xem dashboard (MVP 1)**

1. Sales Manager tải file Excel/CSV bán hàng & tồn kho lên hệ thống.
2. Hệ thống kiểm tra từng dòng; nếu có lỗi, trả danh sách lỗi kèm số dòng để Manager sửa và tải lại.
3. Dữ liệu hợp lệ được lưu, tốc độ hấp thụ được tính lại.
4. Sales Staff mở dashboard, chọn phân khu phụ trách và xem biểu đồ xu hướng cùng các chỉ số tổng hợp.

**Luồng 2 — Chạy dự báo và đọc giải thích (MVP 2)**

1. Job dự báo chạy tự động lúc 02:00, hoặc Manager kích hoạt thủ công sau khi nạp dữ liệu mới.
2. Người dùng theo dõi tiến độ job ngay trên màn hình.
3. Job hoàn tất: mỗi phân khu có ngày dự kiến hết hàng, khoảng tin cậy 90% và đoạn giải thích tiếng Việt.
4. Hệ thống sinh cảnh báo cho phân khu dưới ngưỡng và xếp hạng toàn bộ phân khu theo mức rủi ro tồn kho.
5. Manager đọc giải thích, đối chiếu với hiểu biết thực tế để đánh giá độ hợp lý của dự báo.

**Luồng 3 — Duyệt đề xuất và truy vết (MVP 3)**

1. Người dùng đăng nhập; hệ thống chỉ hiển thị dữ liệu trong phạm vi vai trò.
2. Manager mở danh sách đề xuất đang *Chờ duyệt*, xem dự báo nguồn và giải thích kèm theo.
3. Manager duyệt, hoặc từ chối kèm lý do bắt buộc.
4. Trạng thái đề xuất cập nhật ngay trên màn hình của các thành viên đang mở dashboard.
5. Quyết định được ghi vào audit log; sau này Manager tra cứu lại được ai duyệt, lúc nào, dựa trên dữ liệu đầu vào nào.

### 6.2 User Stories

| ID | MVP | User Story | Tiêu chí chấp nhận |
| --- | --- | --- | --- |
| **FR-01** | 1 | Là quản lý kinh doanh, tôi muốn nạp dữ liệu bán hàng & tồn kho định kỳ để hệ thống luôn phản ánh giỏ hàng hiện tại. | Import Excel/CSV theo template quy định; báo lỗi rõ ràng theo dòng khi dữ liệu không hợp lệ; file lỗi không làm hỏng dữ liệu đã có. |
| **FR-02** | 1 | Là nhân viên kinh doanh, tôi muốn xem tốc độ hấp thụ của phân khu / loại căn mình phụ trách để biết căn nào cần tư vấn gấp. | Dashboard hiển thị tốc độ hấp thụ dưới dạng biểu đồ xu hướng, kèm mốc thời gian cập nhật gần nhất. |
| **FR-03** | 2 | Là quản lý kinh doanh, tôi muốn biết mỗi phân khu còn bán được bao lâu để chủ động điều chỉnh chính sách. | Mỗi phân khu có ngày dự kiến hết hàng, cập nhật sau mỗi lần chạy job dự báo hằng ngày. |
| **FR-04** | 2 | Là quản lý kinh doanh, tôi muốn xem khoảng tin cậy của mỗi dự báo để đánh giá độ rủi ro. | Mỗi số liệu dự báo hiển thị kèm khoảng tin cậy 90%; dự báo dựa trên dữ liệu mỏng được gắn nhãn "độ tin cậy thấp". |
| **FR-05** | 2 | Là quản lý kinh doanh, tôi muốn xem giải thích các yếu tố ảnh hưởng đến tốc độ bán để hiểu nguyên nhân. | Mỗi dự báo kèm đoạn giải thích tiếng Việt, liệt kê yếu tố chính (xu hướng, mùa vụ, thay đổi tồn kho) và giả định đã dùng. |
| **FR-06** | 2 | Là nhân viên kinh doanh, tôi muốn nhận cảnh báo khi một loại căn sắp hết hàng để chủ động tư vấn khách. | Cảnh báo trong ứng dụng khi số ngày tồn kho dự kiến < ngưỡng cấu hình; nêu rõ phân khu, ngày dự kiến hết hàng và mức tin cậy. |
| **FR-07** | 2 | Là quản lý kinh doanh, tôi muốn xem danh sách phân khu xếp theo mức rủi ro tồn kho để ưu tiên hành động. | Danh sách xếp hạng theo rủi ro tồn kho, kèm hướng hành động đề xuất (siết ưu đãi / kích cầu). |
| **FR-08** | 2 | Là người dùng, tôi muốn thấy tiến độ khi hệ thống đang chạy dự báo để biết khi nào có kết quả. | Màn hình hiển thị tiến độ theo thời gian thực; khi mất kết nối, tự chuyển sang cập nhật định kỳ mà không mất trạng thái. |
| **FR-09** | 3 | Là quản lý kinh doanh, tôi muốn duyệt hoặc từ chối đề xuất chính sách trước khi áp dụng. | Đề xuất mặc định *Chờ duyệt*; chỉ chuyển *Đã duyệt* khi Manager xác nhận; từ chối bắt buộc nêu lý do. |
| **FR-10** | 3 | Là nhân viên kinh doanh, tôi muốn thấy ngay khi một đề xuất được duyệt để hành động kịp thời. | Trạng thái đề xuất cập nhật trên màn hình đang mở mà không cần tải lại trang. |
| **FR-11** | 3 | Là quản lý kinh doanh, tôi muốn mỗi người chỉ thấy phần dữ liệu thuộc phạm vi của mình. | Sales Staff chỉ thấy phân khu được phân công; Viewer chỉ đọc; quyền được kiểm tra ở phía hệ thống, không chỉ ẩn trên giao diện. |
| **FR-12** | 3 | Là quản lý kinh doanh, tôi muốn tra cứu lại lịch sử đề xuất và quyết định để rà soát chính sách. | Mọi dự báo, đề xuất và quyết định được lưu kèm người thực hiện, thời điểm, phiên bản dữ liệu đầu vào; có màn hình tra cứu; bản ghi không sửa hay xoá được. |

---

## 7. Yêu cầu phi chức năng

Chi tiết kỹ thuật xem SRS mục 4; bảng dưới nêu mức yêu cầu ở góc nhìn sản phẩm.

| Nhóm | Yêu cầu |
| --- | --- |
| **Bảo mật** | Đăng nhập bắt buộc và phân quyền 3 vai trò từ MVP 3, kiểm tra ở phía hệ thống; dữ liệu bán hàng không rời phạm vi nội bộ; dữ liệu khách hàng được ẩn danh trước khi đưa vào hệ thống |
| **Tần suất cập nhật** | Dữ liệu và dự báo cập nhật tối thiểu 1 lần/ngày (job 02:00); trạng thái job và đề xuất cập nhật real-time từ MVP 2/3 |
| **Hiệu năng** | Biểu đồ tốc độ hấp thụ tải < 2 giây ở quy mô pilot; job dự báo hoàn tất < 10 phút cho 500 phân khu |
| **Kiểm soát chi phí** | Không tính lại mô hình / gọi LLM quá 1 lần/ngày/phân khu trừ khi có dữ liệu mới; ghi nhận số lần gọi LLM để theo dõi chi phí |
| **Độ tin cậy** | 100% dự báo đi kèm khoảng tin cậy và giả định rõ ràng; mất kết nối real-time không làm gián đoạn sử dụng nhờ cơ chế cập nhật định kỳ thay thế |
| **Khả năng mở rộng** | Dữ liệu và API gắn theo dự án / phân khu để mở rộng sang dự án khác mà không đổi cấu trúc |
| **Khả năng kiểm toán** | Lưu toàn bộ lịch sử dự báo, đề xuất và quyết định duyệt / từ chối; bản ghi kiểm toán không sửa hay xoá được |

---

## 8. Kiến trúc & công nghệ

**Luồng xử lý:** dữ liệu bán hàng / tồn kho → tầng dữ liệu → Agent phân tích → mô hình dự báo + LLM diễn giải → dashboard → quản lý kinh doanh duyệt (HITL) → hành động chính sách.

| Tầng | Công nghệ | Vai trò |
| --- | --- | --- |
| Dữ liệu | PostgreSQL | Nguồn sự thật duy nhất: dữ liệu bán hàng, tồn kho, dự báo, đề xuất và audit log |
| Dự báo | Prophet (Python) | Dự báo tốc độ bán & ngày dự kiến hết hàng kèm khoảng tin cậy |
| Điều phối Agent | LangGraph | Điều phối luồng: phân tích → dự báo → giải thích → đề xuất hành động |
| Diễn giải | LLM | Sinh giải thích tiếng Việt cho mỗi dự báo |
| API | FastAPI | Cung cấp API cho dashboard và dịch vụ nội bộ |
| Giao diện | ReactJS | Dashboard biểu đồ cho nhân viên và quản lý kinh doanh |
| Cập nhật real-time | WebSocket | Đẩy tiến độ job dự báo (MVP 2) và thay đổi trạng thái đề xuất (MVP 3) |
| Triển khai | Fly.io (thay thế: Render) | Môi trường pilot với PostgreSQL quản lý sẵn |

---

## 9. Ràng buộc

- **HITL bắt buộc:** mọi quyết định chính sách bán hàng dựa trên dự báo phải được quản lý kinh doanh phê duyệt trước khi áp dụng; Agent không tự động thực thi thay đổi giá / chính sách.
- **Minh bạch dự báo:** mỗi dự báo phải kèm khoảng tin cậy và giả định rõ ràng, tránh hiểu lầm là số liệu chắc chắn.
- **Bảo mật dữ liệu:** dữ liệu bán hàng là thông tin nhạy cảm, tuân thủ chính sách bảo mật nội bộ và phân quyền chặt chẽ.
- **Kiểm soát chi phí:** giới hạn tần suất tính lại mô hình / gọi LLM.
- **Thời gian:** MVP hoàn thành trong 5 tuần, sau đó 1–2 tuần pilot thu thập phản hồi.

---

## 10. Giả định

Từ brief:

- Có sẵn dữ liệu lịch sử bán hàng và tồn kho tối thiểu vài tháng gần nhất để huấn luyện mô hình.
- Dữ liệu được cung cấp qua file Excel/CSV định kỳ (kết nối CRM/ERP nằm ngoài MVP).
- Có ít nhất một quản lý kinh doanh tham gia làm đầu mối phê duyệt (HITL) trong giai đoạn thử nghiệm.

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
| Độ chính xác dự báo | MAPE trung bình theo phân khu / loại căn | Đo được và công bố sau mỗi chu kỳ đánh giá; cải thiện qua các lần huấn luyện lại |
| Độ trễ phát hiện | Thời gian từ khi phát sinh dữ liệu bán hàng đến khi hệ thống cảnh báo | < 24 giờ |
| Thời gian ra quyết định | Thời gian trung bình từ khi phát hiện bất thường đến khi chính sách được duyệt | Giảm so với quy trình Excel hiện tại (baseline xác định ở Tuần 1) |
| Tỷ lệ cảnh báo chính xác | % cảnh báo cạn hàng được xác nhận đúng trên thực tế | Theo dõi & báo cáo cuối pilot |
| Tỷ lệ tuân thủ HITL | % đề xuất được duyệt trước khi áp dụng | 100%; 0 trường hợp tự động thực thi |
| Mức độ sử dụng | Số lượt truy cập dashboard/tuần của nhân viên & quản lý | Theo dõi hằng tuần trong pilot |
| Mức độ hài lòng | Khảo sát nhanh cuối pilot | Đa số người dùng đánh giá hữu ích hơn báo cáo Excel hiện tại |

---

## 12. Tiêu chí chấp nhận MVP (Definition of Done)

Sản phẩm được coi là hoàn thành khi **tất cả** điều kiện sau đạt:

*MVP 1*

1. Import được file Excel/CSV thực tế của dự án pilot; dữ liệu sai được báo lỗi theo dòng, không làm hỏng dữ liệu đã có.
2. Dashboard hiển thị tốc độ hấp thụ theo phân khu / loại căn, cập nhật tự động ít nhất 1 lần/ngày.

*MVP 2*

3. Mỗi phân khu / loại căn có dự báo ngày dự kiến hết hàng kèm khoảng tin cậy 90%.
4. Cảnh báo cạn hàng hiển thị đúng khi tồn kho dự kiến dưới ngưỡng cấu hình.
5. Mỗi dự báo có đoạn giải thích tiếng Việt nêu yếu tố chính và giả định.
6. Danh sách phân khu xếp hạng theo rủi ro tồn kho kèm hướng hành động đề xuất.
7. Tiến độ job dự báo hiển thị theo thời gian thực; mất kết nối thì tự chuyển sang cập nhật định kỳ.
8. MAPE của mô hình được tính trên tập kiểm chứng của dữ liệu pilot và ghi nhận trong báo cáo.

*MVP 3*

9. Luồng HITL hoạt động đầy đủ: đề xuất chỉ có hiệu lực sau khi quản lý duyệt; log ghi đủ người duyệt, thời điểm, lý do.
10. Phân quyền hoạt động: nhân viên chỉ thấy phân khu phụ trách, quản lý thấy toàn dự án và có quyền duyệt, Viewer chỉ đọc.
11. Trạng thái đề xuất cập nhật real-time trên các phiên đang mở.
12. Audit log truy ngược được từ một quyết định về dự báo nguồn và file dữ liệu đầu vào.

*Chung*

13. Hệ thống được triển khai trên môi trường pilot và chạy được job dự báo hằng ngày lúc 02:00.

---

## 13. Kế hoạch triển khai (5 tuần)

| Tuần | Nội dung chính | Đầu ra |
| --- | --- | --- |
| **Tuần 1** | Chốt scope với ban kinh doanh: template dữ liệu, ngưỡng cảnh báo, phân khu pilot; thiết kế schema PostgreSQL và kiến trúc tổng thể | Bản chốt scope + template Excel/CSV + schema dữ liệu |
| **Tuần 2** | **MVP 1**: pipeline import & validate, tính tốc độ hấp thụ, dashboard biểu đồ xu hướng | MVP 1 chạy được trên dữ liệu pilot |
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
| Không kịp truy cập dữ liệu thực tế trong Tuần 2 | Trễ toàn bộ tiến độ | Chuẩn bị bộ dữ liệu mẫu tương đương để phát triển song song, thay bằng dữ liệu thật khi có |

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
| Tốc độ hấp thụ | Số căn bán được trên một đơn vị thời gian của một phân khu / loại căn |
| Tỷ lệ hấp thụ (absorption rate) | % sản phẩm đã bán trên tổng sản phẩm mở bán |
| Phân khu / loại căn | Đơn vị phân tích: nhóm căn hộ theo phân khu, diện tích, số phòng ngủ, hướng, tầng |
| Ngày dự kiến hết hàng | Ngày tồn kho của một phân khu / loại căn được dự báo về 0 theo tốc độ hấp thụ hiện tại |
| MAPE | Sai số phần trăm tuyệt đối trung bình — thước đo độ chính xác dự báo |
| HITL | Human-in-the-loop — cơ chế bắt buộc người duyệt trước khi áp dụng đề xuất |
| Audit log | Nhật ký ghi lại mọi dự báo, đề xuất và quyết định duyệt / từ chối; không sửa hay xoá được |
| Real-time (WebSocket) | Cập nhật đẩy thẳng lên màn hình đang mở — dùng cho tiến độ job dự báo và trạng thái đề xuất |
