"""Controlled LangGraph orchestration based on the F:/Agent harness pattern."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from src.services.ai import AIServiceError, generate_content

from .advisory_tools import answer_expert_question
from .guardrails import validate_llm_output, validate_request
from .prompts import SYSTEM_PROMPT
from .state import AgentState
from .tools import build_context, project_evidence_document_ids


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFD", value.casefold()).replace("đ", "d").replace("Ä‘", "d")
    return "".join(c for c in text if unicodedata.category(c) != "Mn")
def _unit_ids(question: str) -> list[str]:
    return [f"U-{x}" for x in re.findall(r"\bU[- ]?(\d{4})\b", question, re.I)]
def _previous_question(history: list[dict[str, str]]) -> str:
    return next((x.get("content", "") for x in reversed(history) if x.get("role") == "user"), "")

def detect_intent(question: str, history: list[dict[str, str]] | None = None) -> tuple[str, dict[str, Any]]:
    folded, ids, history = _fold(question), _unit_ids(question), history or []
    top = re.search(r"\btop\s*(\d+)\b", folded); limit = max(1, min(int(top.group(1)), 50)) if top else 10
    if any(x in folded for x in ("ban la ai", "ban giup duoc gi")): return "about_agent", {}
    if any(x in folded for x in ("du bao", "forecast", "thang sau")):
        return "forecast_unavailable", {}
    # Match "hi" as a whole word; a substring check misclassified words such
    # as "hiện" and "ý nghĩa" as greetings.
    if any(x in folded for x in ("xin chao", "chao agent")) or re.search(r"\b(?:hello|hi)\b", folded):
        return "greeting", {}
    if "con cho co may chan" in folded or "cho co may chan" in folded: return "general_question", {}
    if any(x in folded for x in ("hap thu yeu", "hap thu kem", "ban cham", "tieu thu cham")) and "can" in folded:
        return "weak_absorption_unit", {}
    if "hap thu" in folded and "can" in folded:
        number = re.search(r"\b(\d+)\s*can", folded)
        return "absorption_units", {"limit": max(1, min(int(number.group(1)) if number else limit, 50))}
    if any(x in folded for x in ("giao viec", "ke hoach", "follow up", "follow-up", "sales hom nay", "sales hôm nay")) or ("sales" in folded and "ngay hom nay" in folded):
        return "business_plan", {}
    if any(x in folded for x in ("hoi gi", "nen hoi", "huong dan agent")): return "help", {}
    if any(x in folded for x in ("tai lieu", "bang chung", "chuyen gia", "co van", "rubric", "khung xep hang", "citation", "trich dan", "nguon tai lieu", "nguon nao", "trong so nao duoc de xuat")) or ("rui ro" in folded and "tu van" in folded):
        return "evidence_question", {}
    if len(ids) >= 2 and any(x in folded for x in ("so sanh", "khac nhau")): return "compare_units", {"first": ids[0], "second": ids[1]}
    if ids and any(x in folded for x in ("tai sao", "vi sao", "giai thich", "yeu to")): return "explain_unit", {"unit_id": ids[0]}
    if "phan khu" in folded and any(x in folded for x in ("cao nhat", "tot nhat", "hap thu", "nhieu can", "can theo doi", "ban nhanh", "ban cham", "ban tot hon", "so sanh")): return "aggregate_by_area", {}
    if any(x in folded for x in ("dang booking", "booking nao", "can booking")):
        return "list_units", {"deal_status": "reserved"}
    if any(x in folded for x in ("ton kho", "giao dich", "booking", "doanh thu", "bao nhieu can")) and any(x in folded for x in ("bao nhieu", "tinh hinh", "ra sao", "chinh xac")):
        return "project_summary", {}
    if "chot khach" in folded or ("phu hop" in folded and "tuan nay" in folded):
        return "closing_advice", {"limit": limit}
    if any(x in folded for x in ("khach hang", "tu van can")):
        return "rank_units", {"limit": limit}
    if any(x in folded for x in ("top", "uu tien", "nen chon", "xep hang", "cao nhat")) or (re.search(r"\b\d+\s*can", folded) and any(x in folded for x in ("day ban", "day nhanh", "tap trung"))):
        number = re.search(r"\b(\d+)\s*can", folded); return "rank_units", {"limit": max(1, min(int(number.group(1)) if number else limit, 50))}
    if any(x in folded for x in ("con hang", "dang ban", "ton kho", "danh sach", "liet ke")): return "list_units", {"unit_status": "available"} if any(x in folded for x in ("con hang", "dang ban")) else {}
    if ids: return "explain_unit", {"unit_id": ids[0]}
    previous = _previous_question(history)
    if previous and any(x in folded for x in ("can dau tien", "can nay", "giai thich them", "chi tiet hon", "nhung can do", "nhung can ay", "tai sao nhung can")):
        old_intent, old_args = detect_intent(previous, [])
        if old_intent == "rank_units": return "rank_units", old_args
    return "unsupported", {}

def _fallback(context: dict[str, Any], intent: str) -> str:
    if context.get("error"): return context["error"]
    if intent == "about_agent": return "Tôi là trợ lý kinh doanh AbsorpIQ. Tôi có thể tìm các căn nên tập trung, giải thích lý do và hỗ trợ lập danh sách follow-up cho đội sales."
    if intent == "forecast_unavailable": return "Tính năng Prophet cho kỳ tới chưa được triển khai; hiện chỉ có thể cung cấp ảnh chụp dữ liệu đã ghi nhận."
    if intent == "greeting": return "Chào anh/chị! Tôi có thể hỗ trợ tìm căn nên tập trung, giải thích lý do ưu tiên và gợi ý công việc cho đội sales."
    if intent == "general_question": return "Câu hỏi này nằm ngoài phạm vi tư vấn bất động sản của tôi. Tôi có thể hỗ trợ anh/chị về căn hộ, phân khu, lượng hàng còn lại và kế hoạch chăm sóc khách hàng."
    if intent == "weak_absorption_unit": return "Hiện dữ liệu chưa đủ để kết luận căn hộ nào có độ hấp thụ yếu nhất ở cấp từng căn. Hệ thống đang có trạng thái từng căn và số giao dịch, nhưng chỉ số hấp thụ được tổng hợp đáng tin cậy ở cấp phân khu. Tôi có thể xác định phân khu bán chậm nhất, sau đó lọc các căn còn hàng trong phân khu đó để đội sales xử lý."
    if intent == "absorption_units":
        units = context.get("top_ranked_units", [])
        requested = context.get("requested_unit_count", len(units))
        lines = [
            "## Kết luận",
            "Chưa thể xếp hạng độ hấp thụ riêng cho từng căn từ dữ liệu hiện có. Độ hấp thụ cần số bán và lượng hàng theo cùng kỳ; dữ liệu hiện tại mới đủ để đánh giá ở cấp phân khu.",
            "",
            f"Để không bỏ trống yêu cầu Top {requested}, dưới đây là {len(units)} căn có mức độ ưu tiên sản phẩm cao nhất. Đây là danh sách thay thế để sales tham khảo, không phải Top căn có độ hấp thụ cao:",
            "",
        ]
        lines.extend(f"{x['rank']}. **{x['unit_id']}** ({x.get('unit_code', '')}) — {x.get('area', '')}" for x in units)
        lines.extend(["", "## Cách dùng cho kinh doanh", "- Dùng danh sách này để chọn sản phẩm cần rà soát trước.", "- Muốn kết luận căn/phân khu bán nhanh, cần bổ sung lịch sử bán và tồn kho theo thời gian.", "- Mọi phân công hoặc quyết định bán vẫn cần người phụ trách kiểm tra và phê duyệt."])
        return "\n".join(lines)
    if intent == "help": return "Bạn có thể hỏi Top căn nên ưu tiên, vì sao một căn có điểm cao, so sánh hai căn hoặc phân khu nào đang dẫn đầu."
    if intent == "project_summary":
        summary = context.get("summary", {})
        return "\n".join([
            "## Tóm tắt tình hình dự án",
            f"Dự án {context.get('project', {}).get('name', 'hiện tại')} có {summary.get('unit_count', 0)} căn, trong đó còn hàng {summary.get('available_unit_count', 0)} căn.",
            f"Đã ghi nhận {summary.get('booking_count', 0)} căn đang booking/giữ chỗ và {summary.get('sold_deal_count', 0)} giao dịch đã bán.",
            summary.get("market_posture", ""),
            "",
            "Đây là ảnh chụp dữ liệu hiện tại. Agent chưa có dữ liệu giá bán và doanh thu thực tế nên không tự suy ra doanh thu hoặc khả năng chốt khách.",
        ])
    if intent == "rank_units":
        units = context.get("top_ranked_units", [])[:20]
        lines = ["## Top căn nên ưu tiên", "Mức ưu tiên dưới đây là tương đối trong snapshot hiện tại:", ""]
        lines.extend(
            f"{x.get('rank')}. **{x.get('unit_code') or x.get('unit_id')}** — {x.get('reason', 'chưa có cơ sở')}"
            for x in units
        )
        return "\n".join(lines) if units else "Chưa có căn phù hợp trong dữ liệu hiện tại."
    if intent == "aggregate_by_area":
        areas = sorted(context.get("areas", []), key=lambda x: (x.get("available_count", 0), x.get("unit_count", 0)), reverse=True)[:5]
        lines = ["## Phân khu cần ưu tiên rà soát", "Các phân khu dưới đây có lượng căn còn hàng cao hơn trong dữ liệu hiện tại:", ""]
        lines.extend(
            f"- **{x.get('area')}** — còn hàng {x.get('available_count', 0)}/{x.get('unit_count', 0)} căn; "
            f"nhu cầu {x.get('demand_level', 'chưa có')}; chuyển đổi {x.get('conversion_level', 'chưa có')}. "
            f"{x.get('narrative', '')}"
            for x in areas
        )
        lines.append("\nĐây là thứ tự rà soát hàng tồn, không phải kết luận về tốc độ bán hay doanh thu.")
        return "\n".join(lines)
    if intent == "business_plan":
        units = context.get("top_ranked_units", [])[:5]
        lines = ["## Gợi ý công việc hôm nay", "1. Gọi lại khách đang quan tâm các căn trong danh sách ưu tiên.", "2. Kiểm tra tình trạng hàng và giá bán trước khi tư vấn.", "3. Ghi nhận kết quả từng cuộc gọi và hẹn bước tiếp theo.", "", "Các căn nên bắt đầu rà soát: "]
        lines.append(", ".join(x.get("unit_id", "") for x in units) or "chưa có dữ liệu phù hợp")
        lines.append("",)
        lines.append("Đây là gợi ý tham khảo; trưởng nhóm sales cần xem xét và phân công chính thức.")
        return "\n".join(lines)
    if intent == "closing_advice":
        units = context.get("top_ranked_units", [])[:5]
        shortlist = ", ".join(x.get("unit_id", "") for x in units) or "chưa có dữ liệu phù hợp"
        return "\n".join([
            "## Kết luận cho đội sales",
            "Chưa thể khẳng định dự án hoặc một căn cụ thể sẽ chốt được trong tuần chỉ từ bảng ưu tiên hiện tại.",
            f"Đội sales có thể bắt đầu rà soát các căn: {shortlist}.",
            "",
            "Trước khi hẹn chốt, cần kiểm tra lại giỏ hàng, giá và chính sách hiện hành, tình trạng pháp lý, nhu cầu thực của khách và lịch sử follow-up. Đây là gợi ý tham khảo; quyết định cuối cùng vẫn cần người phụ trách xem xét.",
        ])
    if intent == "compare_units":
        units = context.get("top_ranked_units", [])
        if len(units) < 2:
            return "Chưa tìm thấy đủ hai căn trong phạm vi dữ liệu được cấp để so sánh."
        lines = ["## So sánh căn hộ"]
        for unit in units[:2]:
            factors = ", ".join(unit.get("top_contribution_factors", [])) or "chưa có đóng góp chi tiết"
            lines.append(
                f"- **{unit.get('unit_code') or unit.get('unit_id')}** — {unit.get('sellability_label', 'chưa có nhãn')}. "
                f"Lý do: {unit.get('reason', 'chưa có cơ sở')}. Yếu tố chính: {factors}."
            )
        lines.append("\nĐây là so sánh mức ưu tiên tương đối trong snapshot hiện tại, không phải xác suất bán.")
        return "\n".join(lines)
    if intent == "explain_unit":
        unit = (context.get("top_ranked_units") or [None])[0]
        if not unit:
            return "Chưa tìm thấy căn được hỏi trong phạm vi dữ liệu được cấp."
        factors = ", ".join(unit.get("top_contribution_factors", [])) or "chưa có đóng góp chi tiết"
        return "\n".join([
            "## Giải thích ưu tiên căn",
            f"**{unit.get('unit_code') or unit.get('unit_id')}** — {unit.get('sellability_label', 'chưa có nhãn')}.",
            f"Lý do: {unit.get('reason', 'chưa có cơ sở')}.",
            f"Yếu tố chính: {factors}.",
        ])
    if intent == "unsupported": return "Tôi có thể hỗ trợ tìm căn nên tập trung, giải thích lý do ưu tiên, so sánh căn hộ, xem phân khu và lập gợi ý follow-up cho sales. Anh/chị hãy thử hỏi cụ thể về một dự án hoặc mã căn."
    project, summary = context.get("project", {}), context.get("summary", {})
    lines = [f"## Kết luận\nDự án {project.get('name')} có {summary.get('unit_count', 0)} căn.\n\n## Top ưu tiên"]
    lines.extend(f"- #{x['rank']} **{x['unit_id']}** — {x['score']:.2f}/100 — {x.get('area', '')}" for x in context.get("top_ranked_units", [])[:5])
    lines.append("\nĐiểm là mức ưu tiên tương đối theo snapshot; đội sales cần kiểm tra trước khi quyết định.")
    return "\n".join(lines)

@traceable(name="absorpiq_agent", run_type="chain")
async def answer(question: str, project_id: str | None = None, allowed_external_ids: set[str] | None = None, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    history = history or []; state: AgentState = {"question": question, "project_id": project_id, "history": history, "events": []}
    async def ingest(s): return {"events": [*s.get("events", []), {"type": "turn/start"}, {"type": "system-prompt/assembled"}]}
    async def classify(s):
        intent, arguments = detect_intent(s["question"], s.get("history")); llm_classified = False
        if intent == "unsupported":
            try:
                raw, _ = await generate_content(
                    "Phân loại câu hỏi thành đúng một intent trong: project_summary, explain_unit, rank_units, list_units, compare_units, aggregate_by_area, about_agent, help, forecast_unavailable, unsupported. Chỉ dùng dữ liệu dự án cho câu hỏi vận hành; không chọn evidence_question ở bước này. Câu hỏi: " + s["question"],
                    system_prompt="Bạn là bộ phân loại an toàn. Không đoán mã căn hay dữ liệu không có.",
                )
                decision = json.loads(raw[raw.find("{"):raw.rfind("}") + 1]); candidate = decision.get("intent", "unsupported")
                if candidate in {"project_summary", "explain_unit", "rank_units", "list_units", "compare_units", "aggregate_by_area", "about_agent", "help", "forecast_unavailable", "unsupported"}:
                    ids = [x for x in decision.get("unit_ids", []) if re.fullmatch(r"U-\d{4}", str(x), re.I)]
                    intent = candidate; arguments = {"limit": max(1, min(int(decision.get("limit", 10)), 50))}
                    if intent == "explain_unit" and ids: arguments["unit_id"] = ids[0]
                    if intent == "compare_units" and len(ids) >= 2: arguments.update(first=ids[0], second=ids[1])
                    llm_classified = True
            except (AIServiceError, ValueError, TypeError, json.JSONDecodeError, KeyError):
                pass
        return {"intent": intent, "arguments": arguments, "llm_used": llm_classified, "events": [*s.get("events", []), {"type": "intent/classified", "intent": intent, "arguments": arguments, "llm_fallback": llm_classified}]}
    async def validate(s):
        reason = validate_request(s["question"], s.get("intent", "unsupported"), s.get("arguments", {})); return {"blocked": bool(reason), "answer": reason, "events": [*s.get("events", []), {"type": "input/rejected" if reason else "input/accepted"}]}
    async def execute(s):
        args = s.get("arguments", {})
        focus_ids = [args[key] for key in ("unit_id", "first", "second") if args.get(key)]
        context = await build_context(
            s["question"],
            s.get("project_id"),
            allowed_external_ids,
            max(args.get("limit", 10), len(focus_ids) or 0),
            args.get("metric") == "absorption" and "yeu" in _fold(s["question"]),
            unit_status=args.get("unit_status"),
            deal_status=args.get("deal_status"),
            focus_unit_ids=focus_ids or None,
        )
        events = [*s.get("events", []), {"type": "tool/execute", "tool": "read_only_project_analytics", "limit": args.get("limit", 10)}]
        if s.get("intent") == "evidence_question" and context.get("project", {}).get("internal_id"):
            import uuid
            document_ids = await project_evidence_document_ids(uuid.UUID(context["project"]["internal_id"]))
            if document_ids:
                context["evidence_answer"] = await answer_expert_question(s["question"], document_ids)
            else:
                context["evidence_answer"] = {"answer": None, "citations": [], "insufficient_evidence": True, "reason": "NO_READY_EVIDENCE"}
            events.append({"type": "tool/execute", "tool": "project_evidence_rag", "document_count": len(document_ids)})
        return {"context": context, "events": events}
    async def narrate(s):
        if s.get("blocked"): return {"answer": s.get("answer", ""), "llm_used": False}
        context, intent = s.get("context", {}), s.get("intent", "unsupported")
        if intent == "evidence_question":
            evidence = context.get("evidence_answer") or {}
            if evidence.get("answer"):
                markers = ", ".join(c["marker"] for c in evidence.get("citations", []) if c.get("marker"))
                suffix = f"\n\n**Nguồn tài liệu:** {markers}" if markers else ""
                return {"answer": f"{evidence['answer']}{suffix}", "llm_used": True}
            return {"answer": "Tôi chưa tìm thấy bằng chứng phù hợp trong tài liệu đã lưu của dự án. Anh/chị vui lòng kiểm tra lại phạm vi tài liệu hoặc bổ sung nguồn trước khi kết luận.", "llm_used": False}
        if intent in {"about_agent", "help", "unsupported", "greeting", "general_question", "weak_absorption_unit", "absorption_units", "project_summary", "aggregate_by_area", "closing_advice", "rank_units", "compare_units", "explain_unit", "forecast_unavailable"}: return {"answer": _fallback(context, intent), "llm_used": False}
        transcript = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in history[-8:]) or "(empty)"
        prompt = f"CONVERSATION_HISTORY:\n{transcript}\n\nUSER_QUESTION:\n{question}\n\nANALYTICS_RESULT (authoritative JSON):\n{json.dumps(context, ensure_ascii=False, default=str)}"
        try:
            text, _ = await generate_content(prompt, system_prompt=SYSTEM_PROMPT)
            if validate_llm_output(text, context): raise AIServiceError("OUTPUT_GUARDRAIL", "Câu trả lời chưa đáp ứng kiểm tra dữ liệu.")
            return {"answer": text, "llm_used": True}
        except AIServiceError: return {"answer": _fallback(context, intent), "llm_used": False}
    async def finish(s): return {"events": [*s.get("events", []), {"type": "turn/end"}]}
    graph = StateGraph(AgentState)
    for name, node in (("ingest", ingest), ("classify", classify), ("validate", validate), ("execute", execute), ("narrate", narrate), ("finish", finish)): graph.add_node(name, node)
    graph.add_edge(START, "ingest"); graph.add_edge("ingest", "classify"); graph.add_edge("classify", "validate")
    graph.add_conditional_edges("validate", lambda s: "finish" if s.get("blocked") else "execute"); graph.add_edge("execute", "narrate"); graph.add_edge("narrate", "finish"); graph.add_edge("finish", END)
    result = await graph.compile().ainvoke(state)
    return {"answer": result.get("answer", ""), "context": result.get("context", {}), "llm_used": bool(result.get("llm_used")), "intent": result.get("intent"), "events": result.get("events", [])}
