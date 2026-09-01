# LLM-as-Judge Rubric (tùy chọn, CHƯA nối tự động vào runner.py)

Dùng khi cần chấm **Response Quality** (khía cạnh chủ quan: hành văn, tính
đầy đủ, dễ hành động) cho câu trả lời `/chat` — sau khi đã có `LLM_API_KEY`
hợp lệ để agent thật sự trả lời.

**Không dùng LLM-as-judge cho bất kỳ điều nào sau đây** — những thứ này PHẢI
chấm bằng đối chiếu dữ liệu tất định (`runner.py` đã làm), vì một judge LLM
có thể bị chính đoạn văn hoa mỹ nhưng sai sự thật đánh lừa:

- mã căn, điểm ranking, tên phân khu có tồn tại thật không (`gt_all_unit_codes`),
- tool nào đã được gọi (`tool_calls` trong `ChatResponse`),
- kết quả phân quyền/HITL (status code + error_code),
- tính hợp lệ schema (Pydantic).

## Prompt

```
Bạn là giám khảo chấm chất lượng câu trả lời của một AI tư vấn bán hàng bất
động sản. KHÔNG được tự kiểm tra số liệu bằng kiến thức nền của bạn — chỉ so
sánh CÂU TRẢ LỜI với TOOL_RESULTS đã cho. Nếu một câu trong câu trả lời có số
liệu không xuất hiện trong TOOL_RESULTS, đó là vi phạm groundedness dù số đó
có đúng ngoài đời hay không.

CÂU HỎI NGƯỜI DÙNG:
<message>

TOOL_RESULTS (nguồn sự thật DUY NHẤT được phép dùng):
<tool_results_json>

CÂU TRẢ LỜI CẦN CHẤM:
<response_text>

Chấm theo thang 0-2 cho từng tiêu chí, trả về DUY NHẤT JSON:
{
  "correctness": 0-2,       // số liệu khớp TOOL_RESULTS, không suy diễn thêm
  "groundedness": 0-2,      // mọi khẳng định có thể truy ngược về TOOL_RESULTS
  "relevance": 0-2,         // trả lời đúng trọng tâm câu hỏi, không lạc đề
  "completeness": 0-2,      // đủ các phần câu hỏi yêu cầu (đặc biệt multi-intent)
  "clarity_actionability": 0-2,  // rõ ràng, có hành động cụ thể nếu phù hợp
  "rationale": "<1-2 câu giải thích điểm thấp nhất, nếu có>"
}
```

## Cách nối vào runner (khi có LLM_API_KEY thật)

1. Sau khi `run_chat_case()` có `response_text` + `tool_calls`, gọi lại chính
   những tool đó (đã có kết quả JSON trong `ChatResponse` nếu bạn mở rộng
   endpoint để trả `sources`/context, hoặc gọi lại trực tiếp hàm tool trong
   `src/agents/advisory_tools.py` — CHỈ để lấy TOOL_RESULTS làm input cho
   judge, không dùng để tự chấm điểm thay).
2. Gọi model judge (nên dùng model KHÁC hoặc cùng nhà cung cấp nhưng tách
   biệt khỏi model đang được đánh giá, tránh thiên vị).
3. Cộng điểm 5 tiêu chí (tối đa 10) vào `RunResult`, đưa vào scorecard dưới
   một mục `response_quality_mean` riêng — KHÔNG gộp chung với Safety/HITL
   hay Schema Validity (nguyên tắc §25.12: safety không được bù bằng điểm
   trung bình từ tiêu chí khác).
