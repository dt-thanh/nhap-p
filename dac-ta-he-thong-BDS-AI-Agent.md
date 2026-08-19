## 1. Bối cảnh

Trong mỗi đợt mở bán dự án BĐS, chủ đầu tư (CĐT) cần các loại hỗ trợ khác nhau theo từng giai đoạn:

- **Giai đoạn mở đặt chỗ (booking):** cần dữ liệu BĐS + dữ liệu người booking để đo lường thị trường (mức độ quan tâm, khả năng hấp thụ), từ đó quyết định thời điểm mở bán và chính sách giá phù hợp. Booking thấp hơn kỳ vọng → điều chỉnh truyền thông hoặc dời lịch. Booking cao → có thể tăng giá đợt sau.
- **Giai đoạn mở bán:** dữ liệu giao dịch (số căn bán, số căn đặt cọc...) cập nhật real-time, dùng để điều chỉnh giá, chiết khấu, chính sách bán hàng cho các đợt kế tiếp nhằm tối ưu doanh thu/lợi nhuận.

Hệ thống cần một **trang tổng quan theo dõi thị trường**, một **công cụ chấm điểm mức độ quan tâm (interest score)**, một **cơ chế mô phỏng dữ liệu/kịch bản** (vì chưa có dữ liệu thật), và một **AI Agent** hỗ trợ phân tích, đề xuất hành động — với các hành động nhạy cảm luôn cần con người phê duyệt.

## 2. Mục tiêu hệ thống

1. Theo dõi trạng thái toàn bộ dự án và từng căn hộ theo thời gian thực (hoặc gần thời gian thực).
2. Cung cấp công cụ chấm điểm mức độ quan tâm thị trường (rule-based tạm thời, có khả năng thay thế bằng model thật sau này mà không đổi cấu trúc hệ thống).
3. Cho phép mô phỏng dữ liệu và kịch bản kinh doanh để kiểm thử hệ thống khi chưa có dữ liệu thật.
4. Xây dựng AI Agent phân tích dữ liệu theo từng pha bán hàng, giải thích nguyên nhân, đề xuất hành động cụ thể, và chỉ thực thi khi được con người duyệt.
5. Lưu vết (audit trail) toàn bộ thay đổi trạng thái, chính sách, và đề xuất AI để có thể truy vết và đánh giá hiệu quả sau này.

## 3. Module 1 — Trang chủ: Bảng theo dõi thị trường hiện tại

**Hiển thị: Làm trong dashboard"
- Banner trạng thái pha hiện tại của toàn dự án: *Không có sự kiện *Mở Đặt Chỗ Đợt 1 → Mở Bán Đợt 1 → Mở Đặt Chỗ Đợt 2 → ...* (Admin chỉnh tay).
- Danh sách/bản đồ căn hộ lấy từ database, mỗi căn hiển thị: mã căn, tòa/tầng, diện tích, giá, và **trạng thái căn**: `Còn trống / Đã bán / Tạm khóa (không giao dịch)`.
- Khi Admin đổi pha dự án (VD: Đặt chỗ → Mở bán), cần **quy tắc cascade** rõ ràng: Cửa sổ sẽ alert hỏi, bạn chắc chắn chưa, khi mở bán thì trường trạng thái căn có thể được thay đổi: từ còn trống sang đã bán ( nó chỉ đơn giản mô tả quá trình nghiệp vụ khi căn đó được mua)
- Bộ lọc: theo tòa, tầng, loại căn, khoảng giá, trạng thái.
- KPI tổng quan: tổng số căn, số đã bán/đặt cọc/booking/còn trống, tỷ lệ hấp thụ (%), doanh thu tích lũy theo đợt.

## 5. Module 2 — Quản lý đợt (Phase/Event Management)

- CRUD cho từng "đợt": tên, loại (đặt chỗ/mở bán), ngày bắt đầu-kết thúc, danh sách căn áp dụng, chính sách giá/chiết khấu áp dụng cho đợt đó.
- Versioning chính sách giá theo từng đợt, để so sánh giữa các đợt (đây là dữ liệu quan trọng cho AI Agent phân tích yếu tố "giá" ảnh hưởng kết quả bán).
- Log lại thời điểm chuyển pha để đối chiếu với dữ liệu bán hàng theo thời gian.

## 6. Module 3 — Công cụ chấm điểm mức độ quan tâm (Interest Score) — rule-based tạm thời

**Nhóm trường dữ liệu đầu vào cần có trong bộ dữ liệu mô phỏng:**
- *Đặc điểm căn hộ:* diện tích, số phòng ngủ, tầng, hướng, view, loại hình, mức giá, giá/m².
- *Dữ liệu khảo sát khách hàng:* mức độ quan tâm tự đánh giá (thang điểm), ngân sách dự kiến, mục đích mua (ở thực/đầu tư).
- *Dữ liệu hành vi booking:* số lượt xem, số lượt giữ chỗ, thời gian phản hồi sau khi được tư vấn, số lần huỷ giữ chỗ.
- *Dữ liệu thị trường khu vực:* giá trung bình khu vực, tốc độ hấp thụ của các dự án lân cận (nếu có).

**Công thức rule-based đề xuất (weighted sum, chuẩn hóa 0–100):**

```
Interest_Score = w1 * booking_rate_norm
               + w2 * survey_interest_avg_norm
               + w3 * view_count_norm
               + w4 * price_competitiveness_norm
               + w5 * area_absorption_norm

(w1..w5 mặc định: 0.30, 0.25, 0.15, 0.15, 0.15 — có thể chỉnh)
```

- Output: điểm 0–100 + phân loại `Hot (≥75) / Bình thường (40–74) / Thấp (<40)` + xu hướng tăng/giảm so với kỳ trước.
- Thiết kế tool này như một **service độc lập** (hàm/API riêng) để khi tool thật (rule-based hoàn chỉnh hoặc ML) sẵn sàng, chỉ cần thay thế implementation mà không đổi interface gọi từ AI Agent hay dashboard.

## 7. Module 4 — Bộ dữ liệu mô phỏng (~100 mẫu)

Trước mắt project chưa có data và market interest score, bạn hãy tạo cho tôi một tập dữ liệu nhỏ mô phỏng khoảng 100 mẫu, bạn hãy tự linh hoạt với dữ liệu này. Nên có đặc điểm căn hộ, market interest score.


## 8. Module 5 — Nút mô phỏng kịch bản (Scenario Simulation)

- Thiết kế theo dạng **plugin/registry** để dễ thêm kịch bản mới về sau, không phải sửa code lõi mỗi lần thêm kịch bản.
- Mỗi kịch bản gồm: tên, mô tả, pha áp dụng (booking/mở bán), hành động thực thi lên DB, tham số cấu hình (VD: % số căn bị ảnh hưởng, khung thời gian).
- Ví dụ kịch bản đầu tiên: **"Sóng mua lớn"** (áp dụng trong "Mở Bán") → chuyển ngẫu nhiên X% căn từ "booking" sang "đặt cọc" trong khung thời gian ngắn, sinh transaction tương ứng.
- **Bổ sung quan trọng:** mỗi lần chạy kịch bản phải được log lại (ai chạy, lúc nào, kết quả cụ thể) — đây chính là dữ liệu "diễn biến theo thời gian" mà AI Agent sẽ phân tích ở Module 6.

## 9. Thêm 1 Tab riêng: AI Agent

### 9.1 Kiến trúc đề xuất
LLM + tool-calling, với bộ tool sau:

1. `query_units(criteria)` — tìm căn theo tiêu chí khách hàng đưa ra → trả top căn phù hợp kèm lý do.
2. `get_interest_score(unit_id | unit_type)` — gọi tool rule-based ở Module 3 → điểm, xu hướng, top loại căn/căn hot.
3. `compare_segments(a, b)` — so sánh 2 căn/nhóm căn → yếu tố khác biệt chính, yếu tố tác động mạnh nhất.
4. `classify_inventory()` — kết hợp inventory + interest score + thời gian tồn kho + kết quả bán các phiên trước/hiện tại → phân nhóm `Hot / Bình thường / Khó bán / Cơ hội cần đẩy`.
5. `get_sales_trend(time_range)` — phân tích dữ liệu bán hàng theo thời gian, giải thích yếu tố tác động (giá, chiết khấu, loại căn, thời điểm).
6. `propose_action(...)` — sinh đề xuất hành động cụ thể (ưu tiên sale căn nào, mở thêm hàng, tăng/giảm chiết khấu, điều chỉnh đợt mở bán, đổi giá đợt sau).

### 9.2 Hành vi theo từng pha

- **Trong "Mở Đặt Chỗ":** Agent phân tích dữ liệu booking/khảo sát → đề xuất căn nào nên mở cho phiên sau, chính sách/chiết khấu hợp lý cho phiên sau.
- **Trong "Mở Bán":** Agent phân tích dữ liệu bán hàng theo thời gian thực → giải thích yếu tố đang tác động đến kết quả bán → đề xuất hành động điều chỉnh cụ thể cho **thời điểm hiện tại** như là nên tập trung sale sản phẩm này, hoặc điều chỉnh chính sách chiếu khấu với sản phẩm chưa được book. 

### 9.3 Cơ chế con người phê duyệt (Human-in-the-loop) — bắt buộc cho hành động nhạy cảm

- Mọi đề xuất liên quan đến **thay đổi giá, chính sách chiết khấu, đổi trạng thái hàng loạt, mở/khoá đợt** đều hiển thị dưới dạng đề xuất kèm số liệu chứng minh + nút **"Áp dụng"**.
- Khi Admin bấm "Áp dụng" → ghi vào database tại đúng thời điểm đó, kèm: nội dung đề xuất gốc của AI, người duyệt, thời gian duyệt.
- Các tác vụ **không nhạy cảm** (tìm căn cho khách, tra cứu điểm quan tâm, so sánh, phân loại tồn kho) Agent có thể trả lời trực tiếp, không cần duyệt.
- **Cần chốt rõ danh sách "hành động nhạy cảm cần duyệt" vs "không cần duyệt"** — đây là một quyết định thiết kế quan trọng nên thống nhất sớm với đội dự án.

## 10. Audit Trail & Khả năng giải thích (bổ sung — chưa có trong yêu cầu gốc)

- Lưu lại **toàn bộ** đề xuất của AI, kể cả những đề xuất **không** được duyệt — để sau này đánh giá AI đề xuất đúng/sai bao nhiêu %, có cải thiện kết quả kinh doanh không.
- Mỗi thay đổi trạng thái/giá/chính sách trong hệ thống cần gắn nguồn gốc: thủ công (Admin) / kịch bản mô phỏng / AI đề xuất được duyệt.
- Đây là nền tảng để sau này huấn luyện hoặc tinh chỉnh lại rule-based/scoring model bằng dữ liệu thật.

## 11. Yêu cầu phi chức năng cần bổ sung

- **Cập nhật real-time** cho dashboard khi có giao dịch/booking mới (websocket hoặc polling định kỳ).
- **Khả năng mở rộng đa dự án** trong tương lai (nếu CĐT quản lý nhiều dự án cùng lúc).
- **Tách rời interface và implementation** của tool scoring, để khi có model thật thay thế rule-based tạm thời mà không phải sửa AI Agent hay dashboard.
- **Chế độ dữ liệu mô phỏng vs dữ liệu thật**: cần cờ (flag) rõ ràng để tránh nhầm lẫn dữ liệu demo với dữ liệu vận hành thật khi go-live.


Một số lưu ý

1. Ai có quyền bấm "Áp dụng" đề xuất AI — hiện tại chỉ Admin
2. Danh sách chính xác "hành động nhạy cảm cần duyệt" là gì? Như là hành đông thay đổi chính sách
3. Có cần tích hợp với CRM/ERP hiện có của CĐT không, có nhưng là sau này.
4. Trọng số (w1..w5) trong công thức interest score có cần điều chỉnh theo loại dự án/phân khúc không? Tùy vào dự án để điều chỉnh, vì đây mới là bước đầu.
5. Xóa các board hiện tại trong dashboard 