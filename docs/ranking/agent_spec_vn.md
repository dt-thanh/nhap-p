# Đặc Tả & Ràng Buộc Kỹ Thuật Đối Với AI Agent (Advisory & RAG Agent)

Tài liệu này đặc tả chi tiết về **Kiến trúc, Quy tắc hoạt động, Ràng buộc Prompt** và **Hệ thống công cụ** dành cho AI Agent (Advisory & RAG Agent) trong dự án. Tài liệu này được dịch nghĩa và chuẩn hóa từ các quyết định thiết kế trong [`ranking_consultant.md`](file:///d:/vinailab/Change/P-100/docs/ranking/ranking_consultant.md).

---

## 1. Phân Định Trách Nhiệm Kiến Trúc (Separation of Responsibilities)

Hệ thống tuân thủ nghiêm ngặt ranh giới tách biệt giữa tính toán số liệu và LLM:
*   **Hệ thống tính toán (Model)**: Điểm số phẳng, điểm số phân cấp, thứ hạng, và tỷ lệ bán hàng được tính toán tất định bằng SQL/Python trong [`engine.py`](file:///d:/vinailab/Change/P-100/src/ranking/engine.py) và [`service.py`](file:///d:/vinailab/Change/P-100/src/ranking/service.py).
*   **Giải thích (Attribution)**: Định nghĩa các đóng góp giá trị thông qua trường đóng góp (`contributions` JSONB) tính toán trực tiếp từ cơ sở dữ liệu.
*   **Truy xuất bằng chứng (Retrieval)**: Hệ thống `pgvector` thực hiện tìm kiếm ngữ nghĩa các văn bản bằng chứng hỗ trợ.
*   **Tác nhân AI (Agent)**: Chỉ thực hiện nhiệm vụ **tổng hợp lời giải thích** (Synthesize) từ dữ liệu đã có và các đoạn tài liệu tìm thấy để đưa ra khuyến nghị dễ đọc cho đội bán hàng.

> [!CAUTION]
> **Ràng buộc tuyệt đối về tính toán**
> AI Agent không bao giờ được phép tự tính toán điểm số, xếp hạng, hoặc thay đổi trọng số của các tiêu chí. Mọi phép toán phải được thực thi ở tầng SQL/Python và truyền vào Agent dưới dạng dữ liệu tĩnh.

---

## 2. Kiến Trúc LangGraph Của Agent

AI Agent được xây dựng dưới dạng một đồ thị LangGraph gồm 2 nút (nằm trong [`graph.py`](file:///d:/vinailab/Change/P-100/src/agents/graph.py)):

```text
               ┌──────────────┐
  Đầu vào ────►│ analyze_node │ (Không dùng LLM - Chỉ định dạng dữ liệu)
               └──────┬───────┘
                      │
                      ▼
               ┌──────────────┐
               │ respond_node │ (Có dùng LLM - Gọi mô hình tạo khuyến nghị)
               └──────────────┘
```

1.  **Nút `analyze_node` (Không dùng LLM)**: Nhận kết quả xếp hạng và đóng góp từ cơ sở dữ liệu, định dạng thành cấu trúc Markdown sạch để chuẩn bị đưa vào prompt.
2.  **Nút `respond_node` (Có dùng LLM)**: Gọi mô hình ngôn ngữ (LLM) để soạn thảo văn bản phản hồi dựa trên dữ liệu tĩnh đã định dạng.

---

## 3. Ràng Buộc Prompt & Chống Bịa Đặt Số Liệu

System prompt của nút `respond_node` (nằm trong [`ranking_node.py`](file:///d:/vinailab/Change/P-100/src/agents/nodes/ranking_node.py)) phải tuân thủ các quy tắc chống bịa đặt (anti-fabrication):

*   **Tuyệt đối trung thực với dữ liệu**: Agent chỉ được sử dụng các số liệu được truyền vào từ nút `analyze`. Không được tự bổ sung các số liệu tài chính hoặc bán hàng nằm ngoài ngữ cảnh.
*   **Không tự phát minh căn hộ**: Không được phép nhắc đến bất kỳ mã căn hộ (unit_id) nào nằm ngoài danh sách được cung cấp.
*   **Tuyên bố từ chối trách nhiệm (Disclaimer)**: Mọi câu trả lời của Agent phải bắt đầu hoặc đi kèm tuyên bố: *"Khuyến nghị này là một đề xuất hỗ trợ quyết định và đang chờ con người phê duyệt, không phải là cam kết kết quả bán hàng."*
*   **Không diễn giải sai lệch chỉ số**: Không được giải thích điểm xếp hạng (Absorption Score) là xác suất bán thành công hay lợi nhuận tài chính dự kiến.

---

## 4. Hệ Thống Công Cụ Hạn Chế (Advisory Tools Allow-list)

Khi hoạt động trong chế độ tư vấn hội thoại, Agent chỉ được phép tương tác với cơ sở dữ liệu thông qua danh sách công cụ tĩnh được cấu hình tại [`advisory_tools.py`](file:///d:/vinailab/Change/P-100/src/agents/advisory_tools.py):

*   **Danh sách trắng `ALLOWED_ADVISORY_TOOLS`**: Chỉ chứa các hàm đọc dữ liệu tất định (ví dụ: `get_ranking_result`, `get_feature_attributions`).
*   **Bộ lọc mã độc `_sanitize_tool_plan`**: Phân tích kế hoạch chạy tool do LLM đề xuất. Nếu LLM yêu cầu gọi bất kỳ tool nào nằm ngoài danh sách trắng, tool đó sẽ bị loại bỏ ngay lập tức trước khi thực thi.

---

## 5. Quy Tắc Nghiêm Cấm Đối Với Agent (The Agent Must Not)

Tài liệu thiết kế quy định Agent **không được phép** thực hiện các hành động sau:
1.  Tự ý đọc trực tiếp các tệp tin hoặc thư mục hệ thống mà không thông qua cơ chế truy xuất có kiểm soát.
2.  Tự ý chọn mẫu số (denominator) khi tính toán tỷ lệ hấp thụ.
3.  Tự sắp xếp thứ tự ưu tiên của các căn hộ dựa trên văn bản tự do.
4.  Tự đưa ra các lập luận nguyên nhân - kết quả (causal claims) không có dữ liệu đối chứng (ví dụ: "căn này bán chạy vì có vị trí gần hồ").
5.  Trích dẫn các tài liệu bằng chứng không khớp về mặt thực thể (entity), thời gian (time), hoặc địa lý (geography).
6.  Che giấu dữ liệu bị thiếu (`missing`) hoặc các phán đoán mâu thuẫn của chuyên gia.

---

## 6. Hệ Thống RAG & Trích Dẫn Bằng Chứng (Citation Validation)

Khi giải trình các phán đoán chấm điểm của chuyên gia đối với các chỉ số vĩ mô (Market) hoặc dự án (Project), Agent sử dụng hệ thống RAG để đối chiếu tài liệu bằng chứng:

*   **Quy tắc từ chối (Abstention Rule)**: Nếu tài liệu bằng chứng liên kết bị thiếu thông tin hoặc không đủ độ tin cậy, Agent **bắt buộc phải từ chối giải thích** thay vì cố gắng diễn dịch lại nội dung.
*   **Định dạng trích dẫn chuẩn hóa**: Mọi trích dẫn tài liệu tham khảo phải được xuất ra dưới dạng các thẻ liên kết tĩnh trỏ thẳng đến trang PDF chứa thông tin (ví dụ: định dạng `[D#:p#]` - trong đó `D` là ID tài liệu, `p` là số trang). Thẻ này sẽ hiển thị trên giao diện người dùng thành các chip clickable để mở file PDF.

---

## 7. Ràng Buộc Phê Duyệt Thủ Công (Human-in-the-Loop Gate)

Đây là cơ chế bảo vệ an toàn của hệ thống để ngăn chặn Agent tự động đưa ra các tư vấn sai lệch:

*   **Trạng thái mặc định**: Mọi khuyến nghị tư vấn (recommendation) do Agent tạo ra đều được lưu vào bảng `agent_recommendations` với trạng thái ban đầu luôn luôn là `'pending_approval'`.
*   **Cổng duyệt thủ công**: Trạng thái chỉ có thể chuyển sang `'approved'` hoặc `'rejected'` bởi người dùng kiểm duyệt thông qua các endpoint HTTP:
    - `POST /api/v1/agent/recommendations/{id}/approve`
    - `POST /api/v1/agent/recommendations/{id}/reject`
*   **Canh phòng mã nguồn**: Ràng buộc này được bảo vệ ở mức kiểm thử AST (Abstract Syntax Tree) trong file `test_ranking_boundary.py`, đảm bảo không lập trình viên nào có thể viết mã bypass hoặc tự động phê duyệt trạng thái mà không đi qua 2 endpoint trên.
