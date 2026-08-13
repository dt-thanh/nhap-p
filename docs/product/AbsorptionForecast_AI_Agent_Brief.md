# PRODUCT / PROJECT BRIEF

**Loại đề tài:** AI Agent phân tích & dự báo (Forecasting / Decision-support Agent)
**Lĩnh vực:** Bất động sản — Kinh doanh & quản lý bán hàng dự án căn hộ
**Ngày cập nhật:** 01/08/2026
**Tài liệu chi tiết:** [PRD.md](PRD.md) (sản phẩm) · [SRS.md](SRS.md) (kỹ thuật)

---

## 1. Tóm tắt điều hành

**AbsorptionForecast AI Agent** gom dữ liệu bán hàng & tồn kho đang nằm rải rác trong các file Excel/CSV nội bộ về **một pipeline chuẩn hoá và đã kiểm tra**, tính tốc độ hấp thụ theo từng phân khu / loại căn, dự báo **ngày dự kiến hết hàng kèm khoảng tin cậy 90%**, giải thích kết quả bằng tiếng Việt, và đưa đề xuất hành động qua **luồng phê duyệt của quản lý kinh doanh có lưu vết kiểm toán**.

Sản phẩm là công cụ hỗ trợ ra quyết định cho ban kinh doanh dự án căn hộ — không phải nền tảng tích hợp CRM/ERP, không tự động thực thi thay đổi giá hay chính sách chiết khấu.

| Hạng mục | Nội dung |
| --- | --- |
| **Tên đề tài** | AbsorptionForecast AI Agent — Trợ lý dự báo tồn kho & tốc độ hấp thụ căn hộ |
| **Loại đề tài** | AI Agent phân tích dữ liệu & dự báo chuỗi thời gian, có giải thích và đề xuất hành động |
| **Ngành / Lĩnh vực** | Bất động sản — Kinh doanh, quản lý bán hàng dự án căn hộ |
| **Product Owner** | G21 - T100 — Nguyễn Đức Đạt, Bùi Hoàng Vương, Nguyễn Trọng Nam, Đặng Tiến Thành |
| **Người phê duyệt nghiệp vụ** | Quản lý kinh doanh (Sales Manager) |
| **Thời gian** | 3 MVP × 1 tuần + kiểm thử & triển khai = 5 tuần, sau đó 1–2 tuần pilot |

---

## 2. Bối cảnh & vấn đề

### 2.1 Bối cảnh

Trong các dự án chung cư / khu đô thị quy mô lớn, sản phẩm được chia thành nhiều phân khu và loại căn (diện tích, hướng, tầng, số phòng ngủ). Tốc độ hấp thụ giữa các nhóm rất không đồng đều: có loại "cháy hàng", có loại tồn kho kéo dài.

Ban kinh doanh cần biết liên tục: phân khu nào **sắp hết hàng** để tăng giá / siết ưu đãi, phân khu nào **bán chậm** để tập trung nguồn lực và kích cầu. Dữ liệu trả lời hai câu hỏi này đã tồn tại trong doanh nghiệp — nhưng ở dạng phân tán và thủ công.

### 2.2 Bốn lớp vấn đề

| # | Vấn đề | Biểu hiện |
| --- | --- | --- |
| **P1** | **Dữ liệu phân tán, quản lý thủ công** | Số liệu bán hàng & tồn kho nằm ở nhiều file Excel/CSV do từng nhóm tự giữ, mỗi file một định dạng; lỗi thiếu trường, sai định dạng, trùng bản ghi chỉ lộ ra khi tổng hợp; không có phiên bản nào được coi là chuẩn |
| **P2** | **Báo cáo chậm và thiếu nhất quán** | Tổng hợp thủ công theo tuần / theo đợt; hai người làm cùng một kỳ có thể ra hai con số khác nhau; mất nhiều giờ để dựng lại số khi lãnh đạo hỏi |
| **P3** | **Thiếu tầm nhìn dự báo và mức độ rủi ro** | Chỉ nhìn được số đã bán trong quá khứ; không biết phân khu nào hết hàng vào ngày nào, không có mức độ tin cậy đi kèm; phán đoán dựa hoàn toàn vào kinh nghiệm cá nhân |
| **P4** | **Thiếu cơ chế phê duyệt và vết kiểm toán** | Chính sách giá / chiết khấu quyết qua trao đổi miệng hoặc chat; sau vài tuần không truy được ai đề xuất, ai duyệt, dựa trên số liệu nào |

**Tác động:** chậm ra quyết định giá / chiết khấu · bỏ lỡ thời điểm tăng giá khi cầu cao hoặc kích cầu khi tồn lâu · phân bổ nhân sự sale không theo dữ liệu · không rà soát được chính sách nào thực sự hiệu quả.

**Ranh giới bài toán:** sản phẩm giải quyết **hợp nhất dữ liệu nội bộ (P1–P2)** và **hỗ trợ ra quyết định có kiểm soát (P3–P4)**. Đồng bộ với CRM/ERP, đồng bộ đa kênh Zalo và vận hành nhiều chủ đầu tư trên cùng hệ thống **không** thuộc bài toán này.

---

## 3. Giá trị sản phẩm

AbsorptionForecast biến dữ liệu bán hàng rời rạc thành một nguồn sự thật duy nhất, rồi biến nguồn dữ liệu đó thành ba thứ ban kinh doanh cần: **biết đang ở đâu** (tốc độ hấp thụ theo phân khu, cập nhật hằng ngày), **biết sắp tới thế nào** (ngày dự kiến hết hàng, khoảng tin cậy, lời giải thích dễ hiểu và thứ tự ưu tiên hành động), và **biết quyết định của mình được ghi nhận thế nào** (đề xuất phải qua phê duyệt, mọi quyết định đều truy ngược được về dữ liệu gốc). Sản phẩm không thay quản lý kinh doanh ra quyết định — nó rút ngắn thời gian từ lúc dữ liệu phát sinh đến lúc có một đề xuất đủ cơ sở để duyệt.

Sản phẩm phát triển theo 3 giai đoạn: **Dữ liệu → Dự báo/AI → Phê duyệt & Phân quyền**.

---

## 4. Đối tượng người dùng

| Vai trò | Mô tả |
| --- | --- |
| **Nhân viên kinh doanh (Sales Staff)** | Xem tốc độ hấp thụ và cảnh báo cạn hàng của các phân khu mình phụ trách để chủ động tư vấn khách |
| **Quản lý kinh doanh (Sales Manager)** | Nạp dữ liệu, xem toàn dự án, duyệt / từ chối đề xuất chính sách, tra cứu lịch sử quyết định, quản trị người dùng |
| **Ban điều hành (Viewer)** | Xem dashboard tổng hợp toàn dự án ở chế độ chỉ đọc để ra quyết định chiến lược |

---

## 5. Lộ trình 3 MVP

### MVP 1 — Nạp dữ liệu & Dashboard tốc độ hấp thụ *(giải quyết P1, P2)*

- **Mục tiêu:** thay báo cáo Excel thủ công bằng một pipeline dữ liệu chuẩn hoá duy nhất; upload xong là xem được ngay tốc độ hấp thụ theo phân khu.
- **Năng lực chính:** upload Excel/CSV theo template · kiểm tra từng dòng và báo lỗi kèm số dòng · lưu dữ liệu tập trung · dashboard biểu đồ xu hướng, bộ lọc phân khu, chỉ số tổng hợp.
- **Tiêu chí thành công:** import được file thật của dự án pilot; số liệu dashboard đối chiếu khớp báo cáo Excel hiện tại; biểu đồ tải dưới 2 giây.
- **Không làm ở giai đoạn này:** dự báo, giải thích AI, đăng nhập, phê duyệt.

### MVP 2 — Dự báo, giải thích & cảnh báo *(giải quyết P3)*

- **Mục tiêu:** chuyển từ nhìn lại quá khứ sang nhìn trước rủi ro tồn kho.
- **Năng lực chính:** dự báo Prophet chạy tự động hằng ngày · ngày dự kiến hết hàng kèm khoảng tin cậy 90% · giải thích tiếng Việt các yếu tố ảnh hưởng · cảnh báo cạn hàng theo ngưỡng do quản lý cấu hình · xếp hạng phân khu theo mức rủi ro kèm hướng hành động · hiển thị tiến độ chạy dự báo theo thời gian thực · báo cáo sai số dự báo (MAPE).
- **Tiêu chí thành công:** 100% dự báo có khoảng tin cậy và giả định; mỗi dự báo có đoạn giải thích đọc hiểu được; cảnh báo đúng phân khu và đúng số ngày; MAPE được đo trên dữ liệu pilot.
- **Không làm ở giai đoạn này:** phê duyệt, phân quyền, so sánh nhiều mô hình, mô phỏng what-if.

### MVP 3 — Phê duyệt, phân quyền & kiểm toán *(giải quyết P4)*

- **Mục tiêu:** biến đề xuất của hệ thống thành quyết định có người chịu trách nhiệm và có vết truy ngược.
- **Năng lực chính:** đăng nhập và phân quyền 3 vai trò · đề xuất mặc định *Chờ duyệt*, chỉ có hiệu lực sau khi Manager duyệt, từ chối bắt buộc nêu lý do · nhật ký kiểm toán không sửa được · trạng thái đề xuất cập nhật ngay trên màn hình đang mở · gán vai trò và phân khu phụ trách.
- **Tiêu chí thành công:** 100% đề xuất đi qua bước duyệt, 0 trường hợp có hiệu lực mà không có quyết định của Manager; nhân viên không truy cập được dữ liệu ngoài phạm vi phân công; từ một quyết định truy ngược được về dự báo nguồn và file dữ liệu đã tạo ra nó.
- **Không làm ở giai đoạn này:** SSO/OAuth2, xác thực đa yếu tố, thông báo ngoài ứng dụng, phân quyền theo từng trường dữ liệu.

---

## 6. Luồng nghiệp vụ chính

1. **Nạp và kiểm tra dữ liệu** — Manager tải file Excel/CSV lên; hệ thống kiểm tra từng dòng, trả danh sách lỗi để sửa; dữ liệu hợp lệ được lưu và tính lại tốc độ hấp thụ; nhân viên mở dashboard xem phân khu mình phụ trách.
2. **Chạy dự báo và đọc giải thích** — job dự báo chạy tự động hằng ngày (hoặc Manager kích hoạt sau khi nạp dữ liệu mới); người dùng theo dõi tiến độ trên màn hình; kết quả gồm ngày dự kiến hết hàng, khoảng tin cậy, giải thích tiếng Việt, cảnh báo và bảng xếp hạng rủi ro.
3. **Duyệt đề xuất và truy vết** — Manager mở danh sách đề xuất *Chờ duyệt*, xem dự báo và giải thích kèm theo, duyệt hoặc từ chối kèm lý do; trạng thái cập nhật ngay cho các thành viên đang mở dashboard; quyết định được ghi vào nhật ký kiểm toán để rà soát về sau.

---

## 7. Ngoài phạm vi

| Hạng mục | Ghi chú |
| --- | --- |
| Tích hợp CRM/ERP theo API | Dữ liệu vào chỉ qua Excel/CSV theo template |
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

Luồng xử lý: dữ liệu bán hàng / tồn kho → lưu trữ tập trung → dự báo & phân tích → giải thích bằng ngôn ngữ tự nhiên → dashboard → quản lý duyệt → hành động chính sách.

| Thành phần | Công nghệ |
| --- | --- |
| Dữ liệu | PostgreSQL — nguồn sự thật duy nhất cho dữ liệu, dự báo, đề xuất và nhật ký kiểm toán |
| Dự báo | Prophet (Python) |
| Điều phối & giải thích | LangGraph + LLM |
| API | FastAPI |
| Giao diện | ReactJS |
| Cập nhật real-time | WebSocket |
| Triển khai | Fly.io (thay thế: Render) |

---

## 9. Ràng buộc

- **Phê duyệt bắt buộc:** mọi quyết định chính sách dựa trên dự báo phải qua quản lý kinh doanh; hệ thống không tự thực thi thay đổi giá / chính sách.
- **Minh bạch dự báo:** mỗi dự báo kèm khoảng tin cậy và giả định rõ ràng, tránh hiểu lầm là số liệu chắc chắn.
- **Bảo mật dữ liệu:** dữ liệu bán hàng là thông tin nhạy cảm — phân quyền theo vai trò, dữ liệu khách hàng được ẩn danh trước khi đưa vào hệ thống.
- **Kiểm soát chi phí:** giới hạn tần suất tính lại mô hình và gọi LLM ở mức 1 lần/ngày/phân khu trừ khi có dữ liệu mới.

---

## 10. Mục tiêu kinh doanh

| ID | Mục tiêu | Chỉ tiêu |
| --- | --- | --- |
| **O1** | Rút ngắn thời gian phát hiện biến động tốc độ hấp thụ | Dashboard cập nhật tối thiểu 1 lần/ngày; độ trễ từ khi có dữ liệu đến khi cảnh báo < 24 giờ *(baseline: báo cáo Excel theo tuần)* |
| **O2** | Nhắm đúng đối tượng cần chính sách kích cầu | Xếp nhóm phân khu theo mức rủi ro tồn kho; giảm số phân khu phải nhận mức chiết khấu tối đa so với cách áp dụng đại trà *(baseline thị trường: chiết khấu 10–30% trên diện rộng)* |
| **O3** | Cải thiện tỷ lệ hấp thụ ở phân khu được theo dõi | So sánh tỷ lệ hấp thụ giữa nhóm áp dụng và không áp dụng đề xuất trong pilot *(baseline ngành ~70%, DXS-FERI 6T/2026)* |
| **O4** | Nâng độ chính xác dự báo so với ước tính cảm tính | MAPE được đo và công bố sau mỗi chu kỳ đánh giá; 100% dự báo kèm khoảng tin cậy |
| **O5** | Đảm bảo kiểm soát của con người | 100% đề xuất được duyệt trước khi áp dụng; 0 trường hợp tự động thực thi, kiểm chứng qua log |

---

## 11. Chỉ số thành công của MVP

| Chỉ số | Cách đo |
| --- | --- |
| Tỷ lệ import thành công | % file upload được xử lý thành công; số lỗi dữ liệu phát hiện được theo dòng |
| Độ tươi của dashboard | Dữ liệu và dự báo cập nhật tối thiểu 1 lần/ngày; hiển thị mốc cập nhật gần nhất |
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
| **Tuần 2** | **MVP 1** — pipeline import & kiểm tra dữ liệu, dashboard tốc độ hấp thụ |
| **Tuần 3** | **MVP 2** — dự báo Prophet, giải thích, cảnh báo cạn hàng, tiến độ real-time |
| **Tuần 4** | **MVP 3** — đăng nhập & phân quyền, luồng phê duyệt, nhật ký kiểm toán |
| **Tuần 5** | Kiểm thử đầu-cuối, tối ưu mô hình, triển khai môi trường pilot, demo & thu phản hồi |

---

## 13. Rủi ro & phương án giảm thiểu

| Rủi ro | Ảnh hưởng | Giảm thiểu |
| --- | --- | --- |
| Không kịp có dữ liệu thực tế của dự án pilot | Trễ toàn bộ tiến độ | Yêu cầu dữ liệu ngay Tuần 1; phát triển song song trên bộ dữ liệu mẫu đúng template |
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
- **Mốc thành công ban đầu:** MAPE ở mức chấp nhận được trên dữ liệu pilot; có ít nhất 1 đề xuất được quản lý duyệt và áp dụng thực tế; đa số người dùng đánh giá dashboard hữu ích hơn báo cáo Excel hiện tại.
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
- Dữ liệu được cung cấp qua file Excel/CSV định kỳ theo template quy định.
- Có ít nhất một quản lý kinh doanh tham gia làm đầu mối phê duyệt trong giai đoạn thử nghiệm.
- Ngưỡng cảnh báo cạn hàng mặc định 30 ngày tồn kho dự kiến, quản lý được phép điều chỉnh.
