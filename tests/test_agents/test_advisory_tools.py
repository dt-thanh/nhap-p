import json

import pytest

from src.agents import advisory_tools


@pytest.mark.asyncio
async def test_gpt_plans_tool_then_synthesizes_database_result(monkeypatch):
    prompts = []
    replies = iter(
        [
            (json.dumps({"tools": ["portfolio_overview"]}), {"promptTokenCount": 10}),
            ("He thong hien co **1 du an**.", {"promptTokenCount": 20}),
        ]
    )

    async def fake_generate(prompt, **_kwargs):
        prompts.append(prompt)
        return next(replies)

    async def fake_portfolio(_scope=None):
        return {"project_count": 1, "projects": [{"project_id": "P-1", "name": "Project 1", "unit_count": 10}]}

    monkeypatch.setattr(advisory_tools, "generate_content", fake_generate)
    monkeypatch.setattr(advisory_tools, "portfolio_overview", fake_portfolio)
    response, calls, sources, usage = await advisory_tools.run_advisory_agent("project count", None, "ALL")

    assert response == "He thong hien co **1 du an**."
    assert calls == ["portfolio_overview"]
    assert sources[0]["source"] == "PostgreSQL"
    assert usage["planner"]["promptTokenCount"] == 10
    assert "TOOL_RESULTS" in prompts[1]
    assert "Project 1" in prompts[1]


@pytest.mark.asyncio
async def test_invalid_planner_output_falls_back_to_scoped_tool(monkeypatch):
    replies = iter([("not json", {}), ("Project analysis.", {})])

    async def fake_generate(_prompt, **_kwargs):
        return next(replies)

    async def fake_project(project_id, _scope=None):
        return {"project_id": project_id, "unit_count": 5}

    monkeypatch.setattr(advisory_tools, "generate_content", fake_generate)
    monkeypatch.setattr(advisory_tools, "project_overview", fake_project)
    response, calls, _, _ = await advisory_tools.run_advisory_agent("overview", "P-1", "ALL")

    assert response == "Project analysis."
    assert calls == ["project_overview"]


def test_tool_plan_rejects_unknown_or_write_tools():
    plan = advisory_tools._parse_tool_plan(
        '{"tools": ["portfolio_overview", "execute_sql", "update_units", "top_ranked_units"]}'
    )
    assert plan == ["portfolio_overview", "top_ranked_units"]


def test_ocean_park_short_name_matches_database_project_without_times_city():
    rows = [
        {'external_id': 'prj_op1', 'name': 'Vinhomes Ocean Park 1'},
        {'external_id': 'prj_tmc', 'name': 'Vinhomes Times City'},
    ]
    matches = advisory_tools._project_mentions_from_rows(
        'Ocean Park con nhung can nao nen tu van truoc?', rows
    )
    assert [(row['external_id'], score) for row, score in matches] == [
        ('prj_op1', len('ocean park'))
    ]


def test_business_priority_question_uses_ranking_without_technical_wording():
    plan = advisory_tools._deterministic_tool_plan(
        'Doi ban hang nen goi tu van nhung can nao truoc trong tuan nay?', 'prj_op1'
    )
    assert 'top_ranked_units' in plan


def test_business_readiness_question_checks_ranking_coverage():
    plan = advisory_tools._deterministic_tool_plan(
        'Du lieu hien tai co du de ra quyet dinh uu tien ban chua?', 'prj_op1'
    )
    assert 'top_ranked_units' in plan
    assert 'ranking_coverage' in plan


def test_project_scoped_question_removes_portfolio_unless_requested():
    plan = advisory_tools._sanitize_tool_plan(
        ["portfolio_overview"],
        "Trong dự án Vinhomes Smart City, tóm tắt rủi ro bán hàng",
        "VHSC",
    )

    assert plan == ["project_overview", "compare_areas", "top_ranked_units", "area_ranking_risks"]


@pytest.mark.asyncio
async def test_risk_question_uses_only_project_scoped_tools(monkeypatch):
    prompts = []
    replies = iter(
        [
            (json.dumps({"tools": ["portfolio_overview"]}), {"input_tokens": 10}),
            ("Rủi ro bán hàng chỉ dựa trên snapshot dự án.", {"input_tokens": 20}),
        ]
    )

    async def fake_generate(prompt, **_kwargs):
        prompts.append(prompt)
        return next(replies)

    async def fake_project(project_id, _scope=None):
        return {"project_id": project_id, "project_name": "Vinhomes Smart City", "status_counts": {"available": 574}}

    async def fake_compare(project_id, _scope=None):
        return {"areas": [{"name": "S1", "velocity_30d": 0.1, "available": 10}]}

    async def fake_ranking(project_id, _scope=None, limit=10):
        return {"items": [{"unit_code": "S1-0101", "score": 0.9, "rank": 1}]}

    async def fake_area_risks(project_id, _scope=None, limit=12):
        return {"areas": [{"name": "S1", "available": 10, "avg_score": 0.4, "low_score_available": 3}]}

    async def fail_portfolio(_scope=None):
        raise AssertionError("portfolio_overview must not run for a single-project risk question")

    monkeypatch.setattr(advisory_tools, "generate_content", fake_generate)
    monkeypatch.setattr(advisory_tools, "project_overview", fake_project)
    monkeypatch.setattr(advisory_tools, "compare_areas", fake_compare)
    monkeypatch.setattr(advisory_tools, "top_ranked_units", fake_ranking)
    monkeypatch.setattr(advisory_tools, "area_ranking_risks", fake_area_risks)
    monkeypatch.setattr(advisory_tools, "portfolio_overview", fail_portfolio)

    response, calls, _, _ = await advisory_tools.run_advisory_agent(
        "Trong dự án Vinhomes Smart City, tóm tắt rủi ro bán hàng",
        "VHSC",
        "ALL",
    )

    assert response == "Rủi ro bán hàng chỉ dựa trên snapshot dự án."
    assert calls == ["project_overview", "compare_areas", "top_ranked_units", "area_ranking_risks"]
    assert "đối thủ cạnh tranh" in prompts[1]
    assert "chỉ phân tích dự án đó" in prompts[1]


def test_3pn_question_uses_unit_mix_tool():
    plan = advisory_tools._deterministic_tool_plan("Có bao nhiêu căn 3PN trong Vinhomes Times City", "vhtc")

    assert "project_overview" in plan
    assert "unit_mix_overview" in plan


def test_low_ranking_inventory_question_uses_area_risk_tool():
    plan = advisory_tools._deterministic_tool_plan(
        "Phân khu nào có nhiều căn còn lại nhưng điểm ranking thấp, cần kiểm tra lại chính sách bán hàng?",
        "vhtc",
    )

    assert "project_overview" in plan
    assert "compare_areas" in plan
    assert "top_ranked_units" in plan
    assert "area_ranking_risks" in plan


def test_inventory_policy_and_quality_questions_use_specialized_tools():
    inventory_plan = advisory_tools._deterministic_tool_plan(
        "Dự án này tồn kho còn lại ở phân khu nào cao nhất?", "vhtc"
    )
    coverage_plan = advisory_tools._deterministic_tool_plan(
        "Kiểm tra độ phủ ranking, có phân khu nào chưa có ranking không?", "vhtc"
    )
    reservation_plan = advisory_tools._deterministic_tool_plan(
        "Phân khu nào đang có áp lực giữ chỗ/booking cao?", "vhtc"
    )
    policy_plan = advisory_tools._deterministic_tool_plan(
        "Chính sách chiết khấu hiện tại đang áp dụng như thế nào?", "vhtc"
    )

    assert "inventory_hotspots" in inventory_plan
    assert "ranking_coverage" in coverage_plan
    assert "reservation_pressure" in reservation_plan
    assert "policy_snapshot" in policy_plan


@pytest.mark.asyncio
async def test_executor_runs_new_read_only_tools(monkeypatch):
    async def fake_inventory(project_id, _scope=None):
        return {"project_id": project_id, "areas": []}

    async def fake_coverage(project_id, _scope=None):
        return {"project_id": project_id, "coverage_ratio": 1}

    async def fake_reservation(project_id, _scope=None):
        return {"project_id": project_id, "areas": []}

    async def fake_policy(project_id=None, _scope=None):
        return {"project": {"project_id": project_id}, "rules": []}

    monkeypatch.setattr(advisory_tools, "inventory_hotspots", fake_inventory)
    monkeypatch.setattr(advisory_tools, "ranking_coverage", fake_coverage)
    monkeypatch.setattr(advisory_tools, "reservation_pressure", fake_reservation)
    monkeypatch.setattr(advisory_tools, "policy_snapshot", fake_policy)

    context, calls = await advisory_tools._execute_tool_plan(
        ["inventory_hotspots", "ranking_coverage", "reservation_pressure", "policy_snapshot"],
        "vhtc",
        "ALL",
    )

    assert calls == ["inventory_hotspots", "ranking_coverage", "reservation_pressure", "policy_snapshot"]
    assert context["inventory_hotspots"]["project_id"] == "vhtc"
    assert context["ranking_coverage"]["coverage_ratio"] == 1
    assert context["policy"]["project"]["project_id"] == "vhtc"


@pytest.mark.asyncio
async def test_named_project_question_infers_project_before_planning(monkeypatch):
    replies = iter(
        [
            (json.dumps({"tools": ["unit_mix_overview"]}), {"input_tokens": 10}),
            ("Vinhomes Times City có dữ liệu cơ cấu căn hộ.", {"input_tokens": 20}),
        ]
    )

    async def fake_generate(_prompt, **_kwargs):
        return next(replies)

    async def fake_infer(message, _scope=None):
        assert "Vinhomes Times City" in message
        return "vhtc"

    async def fake_project(project_id, _scope=None):
        return {"project_id": project_id, "project_name": "Vinhomes Times City", "status_counts": {}}

    async def fake_unit_mix(project_id, _scope=None):
        return {"project_id": project_id, "items": [{"bedrooms": 3, "unit_count": 80, "available": 60}]}

    monkeypatch.setattr(advisory_tools, "generate_content", fake_generate)
    monkeypatch.setattr(advisory_tools, "_infer_project_id_from_message", fake_infer)
    monkeypatch.setattr(advisory_tools, "project_overview", fake_project)
    monkeypatch.setattr(advisory_tools, "unit_mix_overview", fake_unit_mix)

    response, calls, _, _ = await advisory_tools.run_advisory_agent(
        "Có bao nhiêu căn 3PN trong Vinhomes Times City",
        None,
        "ALL",
    )

    assert response == "Vinhomes Times City có dữ liệu cơ cấu căn hộ."
    assert calls == ["project_overview", "unit_mix_overview"]
