import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

POLICY_FILE = Path(__file__).resolve().parents[2] / "data" / "discount_policies.json"


def initial_phases():
    return [
        {"id": "not_started", "label": "Chưa triển khai", "kind": "idle", "release": None},
        {"id": "booking_1", "label": "Mở đặt chỗ đợt 1", "kind": "booking", "release": 1},
        {"id": "sale_1", "label": "Mở bán đợt 1", "kind": "sale", "release": 1},
        {"id": "booking_2", "label": "Mở đặt chỗ đợt 2", "kind": "booking", "release": 2},
        {"id": "sale_2", "label": "Mở bán đợt 2", "kind": "sale", "release": 2},
    ]


def _release_for_index(index: int) -> int:
    if index < 38:
        return 1
    if index < 72:
        return 2
    return 3


def make_units():
    rows = []
    for i in range(100):
        beds = i % 3 + 1
        area = 48 + beds * 13 + (i % 5) * 2
        release = _release_for_index(i)
        rows.append(
            {
                "id": f"{'A' if i < 50 else 'B'}{i // 8 + 2:02d}.{i % 8 + 1:02d}",
                "tower": "The River" if i < 50 else "The Park",
                "floor": i // 8 + 2,
                "type": f"{beds}PN",
                "area": area,
                "view": ["Sông", "Công viên", "Nội khu", "Thành phố"][i % 4],
                "price": round(area * (52 + i % 9) / 100, 1),
                "score": 34 + i * 17 % 63,
                "trend": -3 if i % 4 else 5,
                "status": "Tạm khóa",
                "release": release,
                "phase_id": f"sale_{release}",
            }
        )
    return rows


class SimulationMarketRepository:
    """Mock repository; a PostgreSQL implementation can replace it without changing APIs."""

    def __init__(self):
        self._lock = RLock()
        self.units = make_units()
        self.phases = initial_phases()
        self.phase_index = 0
        self.logs = []
        self.policy = self._load_policy()
        self.proposals = [self._default_proposal()]
        self.scenarios = [
            {"id": "buying_wave", "name": "Sóng mua lớn", "phase": "sale", "description": "Một phần Booking chuyển thành Đã giao dịch."},
            {"id": "quiet_market", "name": "Thị trường trầm lắng", "phase": "booking", "description": "Một phần Booking quay về Còn trống."},
            {"id": "river_view_demand", "name": "Bùng nổ nhu cầu view sông", "phase": "booking", "description": "Căn view sông còn trống chuyển sang Booking."},
            {"id": "policy_boost", "name": "Ưu đãi kích cầu", "phase": "sale", "description": "Booking được chốt giao dịch và căn còn trống phát sinh Booking mới."},
            {"id": "cancellation_wave", "name": "Làn sóng hủy booking", "phase": "booking", "description": "Booking quay về Còn trống."},
        ]

    @property
    def proposal(self):
        return self.proposals[0]

    def snapshot(self):
        phase = self.phases[self.phase_index]
        active = self._active_units(phase)
        done = sum(u["status"] == "Đã giao dịch" for u in active)
        booking = sum(u["status"] == "Booking" for u in active)
        locked = sum(u["status"] == "Tạm khóa" for u in active)
        free = sum(u["status"] == "Còn trống" for u in active)
        avg_score = round(sum(u["score"] for u in active) / len(active)) if active else 0
        return {
            "project": {"id": "saigon-riverside", "name": "Saigon Riverside", "data_mode": "simulation"},
            "phase": phase,
            "phase_index": self.phase_index,
            "phases": self.phases,
            "active_units": active,
            "policy": self.policy,
            "metrics": {
                "active_total": len(active),
                "booking": booking,
                "available": free,
                "locked": locked,
                "transacted": done,
                "avg_signal": avg_score,
                "conversion_rate": round(done / len(active) * 100) if active else 0,
                "booking_rate": round(booking / len(active) * 100) if active else 0,
            },
        }

    def change_phase(self, direction, confirmed, actor):
        if not confirmed:
            raise ValueError("confirmation_required")
        if direction != "next":
            raise ValueError("phase_only_forward")
        with self._lock:
            self.phase_index = min(len(self.phases) - 1, self.phase_index + 1)
            self._activate_phase(self.phases[self.phase_index])
            return self.snapshot()

    def add_phase(self, kind, confirmed, actor):
        if not confirmed:
            raise ValueError("confirmation_required")
        with self._lock:
            next_release = max([p["release"] or 0 for p in self.phases]) + (1 if kind == "booking" else 0)
            if kind == "sale" and self.phases[-1]["kind"] == "booking":
                next_release = self.phases[-1]["release"]
            phase = {"id": f"{kind}_{next_release}", "label": f"{'Mở đặt chỗ' if kind == 'booking' else 'Mở bán'} đợt {next_release}", "kind": kind, "release": next_release}
            self.phases.append(phase)
            for unit in self.units:
                if unit["release"] < next_release and unit["status"] == "Tạm khóa":
                    unit["release"] = next_release
                    unit["phase_id"] = f"sale_{next_release}"
            return {"phase": phase, "phases": self.phases}

    def run_scenario(self, scenario_id, intensity, confirmed, actor):
        if not confirmed:
            raise ValueError("confirmation_required")
        scenario = next((s for s in self.scenarios if s["id"] == scenario_id), None)
        if not scenario:
            raise KeyError("scenario_not_found")
        phase = self.phases[self.phase_index]
        if scenario["phase"] != phase["kind"]:
            raise ValueError("scenario_not_available_for_phase")

        with self._lock:
            if scenario_id == "policy_boost":
                closed = self._move_units(phase, "Booking", "Đã giao dịch", intensity // 2)
                new_bookings = self._move_units(phase, "Còn trống", "Booking", intensity // 2)
                affected = closed + new_bookings
                source, target = "Booking/Còn trống", "Đã giao dịch/Booking"
            elif scenario_id == "buying_wave":
                affected = self._move_units(phase, "Booking", "Đã giao dịch", intensity)
                source, target = "Booking", "Đã giao dịch"
            elif scenario_id in {"quiet_market", "cancellation_wave"}:
                affected = self._move_units(phase, "Booking", "Còn trống", intensity)
                source, target = "Booking", "Còn trống"
            else:
                affected = self._move_units(phase, "Còn trống", "Booking", intensity, view="Sông")
                source, target = "Còn trống", "Booking"

            return {"scenario": scenario, "affected": affected, "from_status": source, "to_status": target, "snapshot": self.snapshot()}

    def generate_proposal(self, prompt, actor):
        with self._lock:
            phase = self.phases[self.phase_index]
            active = self._active_units(phase)
            if phase["kind"] == "booking":
                candidates = sorted([u for u in active if u["status"] in {"Booking", "Còn trống"}], key=lambda u: (-u["score"], u["price"]))[:12]
                action_type = "release_plan"
                title = f"Đề xuất đưa {len(candidates)} căn vào {self._sale_label_for_release(phase['release'])}"
                summary = "AI/Admin đề xuất chốt danh sách căn ưu tiên cho đợt mở bán kế tiếp dựa trên booking, tín hiệu quan tâm và khả năng hấp thụ."
                discount = 1.0
            else:
                candidates = sorted([u for u in active if u["status"] in {"Còn trống", "Booking"}], key=lambda u: (u["score"], -u["price"]))[:8]
                action_type = "discount_policy"
                title = f"Đề xuất chính sách riêng cho {len(candidates)} căn cần kích cầu"
                summary = "Đề xuất áp dụng ưu đãi có kiểm soát cho nhóm căn hấp thụ chậm hoặc giá trị cao trong giai đoạn hiện tại."
                discount = 2.5

            proposal = {
                "id": f"proposal-{uuid4().hex[:8]}",
                "status": "open",
                "action_type": action_type,
                "title": title,
                "summary": summary,
                "admin_prompt": prompt,
                "recommended_unit_ids": [u["id"] for u in candidates],
                "discount_percent": discount,
                "target_release": phase["release"] or 1,
                "evidence": [
                    f"Phase hiện tại: {phase['label']}",
                    f"Booking: {sum(u['status'] == 'Booking' for u in active)} căn",
                    f"Còn trống: {sum(u['status'] == 'Còn trống' for u in active)} căn",
                ],
                "created_by": actor,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.proposals.insert(0, proposal)
            return proposal

    def decide(self, proposal_id, decision, reason, confirmed, actor, unit_ids=None):
        if not confirmed:
            raise ValueError("confirmation_required")
        with self._lock:
            proposal = next((p for p in self.proposals if p["id"] == proposal_id), None)
            if not proposal:
                raise KeyError("proposal_not_found")
            if proposal["status"] != "open":
                raise ValueError("proposal_already_closed")

            selected = unit_ids or proposal.get("recommended_unit_ids", [])
            proposal.update(status=decision, decision_reason=reason, decided_by=actor, approved_unit_ids=selected)
            if decision == "approved":
                self._apply_approved_proposal(proposal, selected)
                self._log(actor, f"Phê duyệt đề xuất: {proposal['title']}", "admin_approved")
            return proposal

    def _apply_approved_proposal(self, proposal, selected):
        now = datetime.now(timezone.utc).isoformat()
        if proposal["action_type"] == "release_plan":
            target_release = proposal.get("target_release") or 1
            for unit in self.units:
                if unit["id"] in selected:
                    unit["release"] = target_release
                    unit["phase_id"] = f"sale_{target_release}"
                    if unit["status"] == "Tạm khóa":
                        unit["status"] = "Còn trống"
        change = {
            "id": f"change-{uuid4().hex[:8]}",
            "proposal_id": proposal["id"],
            "action_type": proposal["action_type"],
            "unit_ids": selected,
            "discount_percent": proposal.get("discount_percent"),
            "reason": proposal.get("summary"),
            "approved_at": now,
            "approved_by": proposal.get("decided_by"),
        }
        self.policy.setdefault("approved_changes", []).append(change)
        self.policy["version"] = int(self.policy.get("version", 1)) + 1
        self.policy["updated_at"] = now
        self._save_policy()

    def _active_units(self, phase):
        if phase["kind"] == "idle":
            return list(self.units)
        return [u for u in self.units if u["release"] == phase["release"]]

    def _activate_phase(self, phase):
        if phase["kind"] == "booking":
            for index, unit in enumerate(self._active_units(phase)):
                if unit["status"] == "Tạm khóa":
                    unit["status"] = "Booking" if index % 4 == 0 else "Còn trống"
        elif phase["kind"] == "sale":
            for index, unit in enumerate([u for u in self._active_units(phase) if u["status"] == "Booking"]):
                if index % 3 == 0:
                    unit["status"] = "Đã giao dịch"

    def _move_units(self, phase, source, target, intensity, view=None):
        candidates = [u for u in self._active_units(phase) if u["status"] == source and (view is None or u["view"] == view)]
        if not candidates:
            return 0
        affected = candidates[: max(1, round(len(candidates) * intensity / 100))]
        for unit in affected:
            unit["status"] = target
        return len(affected)

    def _default_proposal(self):
        return {
            "id": "proposal-discount-3pn",
            "status": "open",
            "action_type": "discount_policy",
            "title": "Điều chỉnh chiết khấu nhóm 3PN tầng thấp",
            "summary": "Tăng chiết khấu từ 1,5% lên 2,5% cho 8 căn trong 7 ngày.",
            "recommended_unit_ids": [u["id"] for u in self.units if u["type"] == "3PN" and u["floor"] <= 6][:8],
            "discount_percent": 2.5,
            "target_release": 1,
            "evidence": ["Tỷ lệ booking thấp", "Tồn kho 42 ngày", "Giá/m² cao hơn nhóm tương đương"],
        }

    def _sale_label_for_release(self, release):
        sale = next((p for p in self.phases if p["kind"] == "sale" and p["release"] == release), None)
        return sale["label"] if sale else f"Mở bán đợt {release}"

    def _load_policy(self):
        if POLICY_FILE.exists():
            return json.loads(POLICY_FILE.read_text(encoding="utf-8-sig"))
        return {"project_id": "saigon-riverside", "version": 1, "rules": [], "approved_changes": []}

    def _save_policy(self):
        POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
        POLICY_FILE.write_text(json.dumps(self.policy, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, actor, action, source):
        self.logs.insert(0, {"id": str(uuid4()), "actor": actor, "action": action, "source": source, "created_at": datetime.now(timezone.utc).isoformat()})


market_repository = SimulationMarketRepository()

