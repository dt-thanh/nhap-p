"""Focused regression tests for the action-specific Advisor policy."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api import auth, dashboard
from src.services import dashboard_auth


def principal(*, role="business_viewer", subject="advisor-1", oidc_roles=(), is_ceo=False, scope=frozenset({"P-0001"})):
    return dashboard_auth.DashboardPrincipal(
        role=role,
        project_scope=scope,
        subject=subject,
        is_ceo=is_ceo,
        oidc_roles=frozenset(oidc_roles),
    )


@pytest.mark.asyncio
async def test_advisor_is_admitted_only_with_verified_advisor_role(monkeypatch):
    monkeypatch.setattr(dashboard_auth, "authenticate_dashboard", lambda *_args: _async(principal(oidc_roles=("CRM.ADVISOR",))))
    admitted = await dashboard_auth.require_advisor_analysis_authoring()(authorization=None, absorbiq_session=None)
    assert admitted.role == "business_viewer"
    assert admitted.oidc_roles == frozenset({"CRM.ADVISOR"})


@pytest.mark.asyncio
async def test_viewer_is_denied_advisor_analysis_authoring(monkeypatch):
    monkeypatch.setattr(dashboard_auth, "authenticate_dashboard", lambda *_args: _async(principal(oidc_roles=("CRM.Viewer",))))
    with pytest.raises(HTTPException) as exc:
        await dashboard_auth.require_advisor_analysis_authoring()(authorization=None, absorbiq_session=None)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


@pytest.mark.asyncio
async def test_sales_and_admin_are_denied_advisor_analysis_authoring(monkeypatch):
    for role in ("pipeline_operator", "admin"):
        monkeypatch.setattr(
            dashboard_auth,
            "authenticate_dashboard",
            lambda *_args, role=role: _async(principal(role=role, subject=None)),
        )
        with pytest.raises(HTTPException) as exc:
            await dashboard_auth.require_advisor_analysis_authoring()(authorization=None, absorbiq_session=None)
        assert exc.value.detail["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


@pytest.mark.asyncio
async def test_advisor_without_subject_fails_closed(monkeypatch):
    monkeypatch.setattr(
        dashboard_auth,
        "authenticate_dashboard",
        lambda *_args: _async(principal(subject=None, oidc_roles=("CRM.ADVISOR",))),
    )
    with pytest.raises(HTTPException) as exc:
        await dashboard_auth.require_advisor_analysis_authoring()(authorization=None, absorbiq_session=None)
    assert exc.value.detail["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


@pytest.mark.asyncio
async def test_advisor_without_server_scope_fails_closed(monkeypatch):
    monkeypatch.setattr(
        dashboard_auth,
        "authenticate_dashboard",
        lambda *_args: _async(principal(oidc_roles=("CRM.ADVISOR",), scope=frozenset())),
    )
    with pytest.raises(HTTPException) as exc:
        await dashboard_auth.require_advisor_analysis_read()(authorization=None, absorbiq_session=None)
    assert exc.value.detail["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,roles,is_ceo",
    [
        ("business_viewer", ("CRM.Viewer",), False),
        ("pipeline_operator", ("CRM.SALES",), False),
        ("admin", ("CRM.Admin",), False),
        ("admin", ("CRM.CEO",), True),
    ],
)
async def test_non_advisors_are_denied_every_advisor_analysis_read_and_write_gate(monkeypatch, role, roles, is_ceo):
    monkeypatch.setattr(
        dashboard_auth,
        "authenticate_dashboard",
        lambda *_args: _async(principal(role=role, oidc_roles=roles, is_ceo=is_ceo)),
    )
    for dependency in (dashboard_auth.require_advisor_analysis_read(), dashboard_auth.require_advisor_analysis_authoring()):
        with pytest.raises(HTTPException) as exc:
            await dependency(authorization=None, absorbiq_session=None)
        assert exc.value.detail["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


@pytest.mark.asyncio
async def test_only_verified_ceo_enters_reviewer_gate(monkeypatch):
    ceo = principal(role="admin", subject="ceo-1", oidc_roles=("CRM.CEO",), is_ceo=True)
    monkeypatch.setattr(dashboard_auth, "authenticate_dashboard", lambda *_args: _async(ceo))
    assert (await dashboard_auth.require_advisor_analysis_reviewer_visibility()(authorization=None, absorbiq_session=None)).is_ceo

    admin = principal(role="admin", subject="admin-1", oidc_roles=("CRM.Admin",), is_ceo=False)
    monkeypatch.setattr(dashboard_auth, "authenticate_dashboard", lambda *_args: _async(admin))
    with pytest.raises(HTTPException) as exc:
        await dashboard_auth.require_verified_ceo_analysis_review()(authorization=None, absorbiq_session=None)
    assert exc.value.detail["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,roles,is_ceo",
    [
        ("business_viewer", ("CRM.ADVISOR",), False),
        ("business_viewer", ("CRM.Viewer",), False),
        ("pipeline_operator", ("CRM.SALES",), False),
        ("admin", ("CRM.Admin",), False),
    ],
)
async def test_non_ceo_personas_cannot_enter_ceo_reviewer_routes(monkeypatch, role, roles, is_ceo):
    monkeypatch.setattr(
        dashboard_auth,
        "authenticate_dashboard",
        lambda *_args: _async(principal(role=role, oidc_roles=roles, is_ceo=is_ceo)),
    )
    with pytest.raises(HTTPException) as exc:
        await dashboard_auth.require_advisor_analysis_reviewer_visibility()(authorization=None, absorbiq_session=None)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "ADVISOR_ANALYSIS_FORBIDDEN"


def test_role_presentation_distinguishes_verified_advisor_from_viewer_and_legacy_session():
    advisor = dashboard_auth.resolve_role_presentation(principal(oidc_roles=("CRM.ADVISOR",)))
    viewer = dashboard_auth.resolve_role_presentation(principal(oidc_roles=("CRM.Viewer",)))
    legacy = dashboard_auth.resolve_role_presentation(principal(oidc_roles=()))

    assert advisor.role == "business_viewer"
    assert advisor.role_code == "advisor"
    assert advisor.role_label == "Advisor"
    assert advisor.capabilities.expert_analysis_authoring is True
    assert advisor.capabilities.advisor_analysis_authoring is True
    assert advisor.capabilities.advisor_analysis_review is False
    assert viewer.role_code == legacy.role_code == "viewer"
    assert viewer.capabilities.expert_analysis_authoring is False
    assert legacy.capabilities.expert_analysis_authoring is False


def test_role_presentation_preserves_ceo_admin_and_sales_boundaries():
    ceo = dashboard_auth.resolve_role_presentation(
        principal(role="admin", subject="ceo-1", oidc_roles=("CRM.CEO",), is_ceo=True)
    )
    admin = dashboard_auth.resolve_role_presentation(
        principal(role="admin", subject="admin-1", oidc_roles=("CRM.Admin",))
    )
    sales = dashboard_auth.resolve_role_presentation(
        principal(role="pipeline_operator", subject="sales-1", oidc_roles=("CRM.SALES",))
    )

    assert (ceo.role_code, ceo.capabilities.ceo_review) == ("ceo", True)
    assert ceo.capabilities.advisor_analysis_review is True
    assert ceo.capabilities.advisor_analysis_authoring is False
    assert (admin.role_code, admin.capabilities.ceo_review) == ("admin", False)
    assert (sales.role_code, sales.capabilities.ranking_recompute) == ("sales", True)
    assert sales.capabilities.global_config is False
    assert sales.capabilities.advisor_analysis_access is False


@pytest.mark.asyncio
async def test_current_user_endpoints_share_server_derived_metadata(monkeypatch):
    advisor_principal = principal(oidc_roles=("CRM.ADVISOR",))
    monkeypatch.setattr(auth, "authenticate_dashboard", lambda *_args: _async(advisor_principal))

    auth_response = await auth.me(authorization=None, absorbiq_session=None)
    auth_payload = __import__("json").loads(auth_response.body)
    permissions = await dashboard.me_permissions(advisor_principal)

    assert auth_payload["role"] == permissions.role == "business_viewer"
    assert auth_payload["role_code"] == permissions.role_code == "advisor"
    assert auth_payload["role_label"] == permissions.role_label == "Advisor"
    assert auth_payload["capabilities"] == permissions.capabilities


async def _async(value):
    return value
