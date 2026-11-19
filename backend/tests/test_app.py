from fastapi.testclient import TestClient
import jwt
import pytest

from app import auth
from app.config import get_settings
from app.main import create_app
from app import organizations
from uuid import UUID
from fastapi import HTTPException
import httpx


def test_health_endpoint_returns_ok():
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_invalid_request_hides_validation_details():
    app = create_app()

    @app.post("/test-validation")
    async def endpoint(value: int):
        return {"value": value}

    response = TestClient(app).post("/test-validation", json={"value": "not-an-int"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "not-an-int" not in response.text


def test_request_id_header_is_preserved_when_safe():
    response = TestClient(create_app()).get("/health", headers={"x-request-id": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"


def test_identity_requires_bearer_token():
    response = TestClient(create_app()).get("/api/v1/me")
    assert response.status_code == 401


def test_identity_returns_verified_subject(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "_verify_sync", lambda token, url: {"sub": "user-123"})
    try:
        response = TestClient(create_app()).get(
            "/api/v1/me", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        assert response.json() == {"userId": "user-123"}
    finally:
        get_settings.cache_clear()


def test_identity_rejects_verification_failure(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    get_settings.cache_clear()

    def reject(token, url):
        raise jwt.InvalidTokenError("bad token")

    monkeypatch.setattr(auth, "_verify_sync", reject)
    try:
        response = TestClient(create_app()).get(
            "/api/v1/me", headers={"Authorization": "Bearer invalid"}
        )
        assert response.status_code == 401
        assert "bad token" not in response.text
    finally:
        get_settings.cache_clear()


def test_organization_route_requires_authentication():
    response = TestClient(create_app()).get(
        "/api/v1/organizations/00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code == 401


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def configure_auth(monkeypatch, verify):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "_verify_sync", verify)


def test_organization_authorized_jwt_and_minimal_response(monkeypatch):
    configure_auth(monkeypatch, lambda token, url: {"sub": "user-a", "exp": 9999999999})
    async def read(org_id, token):
        assert org_id == ORG_ID
        assert token == "valid-user-token"
        return organizations.OrganizationResponse(id=ORG_ID, name="Acme", slug="acme", created_at="2026-01-01T00:00:00Z")
    monkeypatch.setattr("app.main.read_organization", read)
    try:
        response = TestClient(create_app()).get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": "Bearer valid-user-token"})
        assert response.status_code == 200
        assert set(response.json()) == {"id", "name", "slug", "createdAt"}
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("error", [jwt.InvalidTokenError("bad"), jwt.ExpiredSignatureError("expired")])
def test_organization_rejects_invalid_or_expired_jwt(monkeypatch, error):
    def verify(token, url):
        raise error
    configure_auth(monkeypatch, verify)
    try:
        response = TestClient(create_app()).get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Invalid access token"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("target", [ORG_ID, UUID("00000000-0000-0000-0000-000000000002")])
def test_rls_denied_and_cross_organization_reads_are_not_found(monkeypatch, target):
    configure_auth(monkeypatch, lambda token, url: {"sub": "user-a"})
    async def denied(org_id, token):
        raise HTTPException(status_code=404, detail="Organization not found")
    monkeypatch.setattr("app.main.read_organization", denied)
    try:
        response = TestClient(create_app()).get(f"/api/v1/organizations/{target}", headers={"Authorization": "Bearer user-a-token"})
        assert response.status_code == 404
    finally:
        get_settings.cache_clear()


def test_supabase_unavailable_returns_generic_503(monkeypatch):
    configure_auth(monkeypatch, lambda token, url: {"sub": "user-a"})
    async def unavailable(org_id, token):
        raise HTTPException(status_code=503, detail="Organization service unavailable")
    monkeypatch.setattr("app.main.read_organization", unavailable)
    try:
        response = TestClient(create_app()).get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": "Bearer user-a-token"})
        assert response.status_code == 503
        assert "service unavailable" in response.text
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_supabase_request_forwards_user_token_and_not_service_role(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-test-key")
    get_settings.cache_clear()
    seen = {}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            seen.update(headers=headers, params=params)
            return httpx.Response(200, json=[{"id": str(ORG_ID), "name": "Acme", "slug": "acme", "created_at": "2026-01-01T00:00:00Z", "secret": "must-not-leak"}])
    monkeypatch.setattr(organizations.httpx, "AsyncClient", Client)
    try:
        result = await organizations.read_organization(ORG_ID, "user-jwt")
        assert seen["headers"]["Authorization"] == "Bearer user-jwt"
        assert seen["headers"]["apikey"] == "public-test-key"
        assert "service_role" not in str(seen["headers"])
        assert set(result.model_dump(by_alias=True)) == {"id", "name", "slug", "createdAt"}
    finally:
        get_settings.cache_clear()
