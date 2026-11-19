from fastapi.testclient import TestClient
import httpx
import pytest
from uuid import UUID

from app import auth
from app.config import get_settings
from app.main import create_app
from app import organizations

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def configure_auth(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "_verify_sync", lambda token, url: {"sub": "user-123"})


@pytest.mark.parametrize("exc_cls", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError, httpx.HTTPError])
def test_endpoint_handles_upstream_network_errors(monkeypatch, exc_cls):
    configure_auth(monkeypatch)

    class FailingClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            raise exc_cls("failure")

    monkeypatch.setattr(organizations.httpx, "AsyncClient", FailingClient)
    try:
        response = TestClient(create_app()).get(
            f"/api/v1/organizations/{ORG_ID}",
            headers={"Authorization": "Bearer valid-token"}
        )
        assert response.status_code == 503
        assert response.json()["error"]["message"] == "Organization service unavailable"
        assert response.headers["x-request-id"]
    finally:
        get_settings.cache_clear()


def test_endpoint_handles_malformed_json_response(monkeypatch):
    configure_auth(monkeypatch)

    class MalformedClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            return httpx.Response(200, content=b"not-json-content")

    monkeypatch.setattr(organizations.httpx, "AsyncClient", MalformedClient)
    try:
        response = TestClient(create_app()).get(
            f"/api/v1/organizations/{ORG_ID}",
            headers={"Authorization": "Bearer valid-token"}
        )
        assert response.status_code == 503
        assert response.json()["error"]["message"] == "Organization service unavailable"
        assert response.headers["x-request-id"]
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("status_code", [401, 403, 500, 502, 503])
def test_upstream_error_status_codes(monkeypatch, status_code):
    configure_auth(monkeypatch)

    class UpstreamStatusClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            return httpx.Response(status_code, json={"error": "failed"})

    monkeypatch.setattr(organizations.httpx, "AsyncClient", UpstreamStatusClient)
    try:
        response = TestClient(create_app()).get(
            f"/api/v1/organizations/{ORG_ID}",
            headers={"Authorization": "Bearer valid-token"}
        )
        if status_code in (401, 403):
            assert response.status_code == 404
        else:
            assert response.status_code == 503
        assert response.headers["x-request-id"]
    finally:
        get_settings.cache_clear()


def test_camel_case_serialization(monkeypatch):
    configure_auth(monkeypatch)

    async def read(org_id, token):
        return organizations.OrganizationResponse(
            id=ORG_ID,
            name="Acme",
            slug="acme",
            created_at="2026-01-01T00:00:00Z"
        )

    monkeypatch.setattr("app.main.read_organization", read)
    try:
        response = TestClient(create_app()).get(
            f"/api/v1/organizations/{ORG_ID}",
            headers={"Authorization": "Bearer valid-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "createdAt" in data
        assert "created_at" not in data
        assert data["createdAt"] == "2026-01-01T00:00:00Z"
        assert response.headers["x-request-id"]
    finally:
        get_settings.cache_clear()


def test_error_responses_include_request_id(monkeypatch):
    """Test that all error responses include X-Request-ID header."""
    configure_auth(monkeypatch)

    # Test 503 error response
    class FailingClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(organizations.httpx, "AsyncClient", FailingClient)
    try:
        response = TestClient(create_app()).get(
            f"/api/v1/organizations/{ORG_ID}",
            headers={"Authorization": "Bearer valid-token", "x-request-id": "custom-request-id"}
        )
        assert response.status_code == 503
        assert response.headers["x-request-id"] == "custom-request-id"
        assert response.json()["requestId"] == "custom-request-id"
    finally:
        get_settings.cache_clear()


def test_404_response_includes_request_id(monkeypatch):
    """Test that 404 responses include X-Request-ID header."""
    configure_auth(monkeypatch)

    class NotFoundClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            return httpx.Response(200, json=[])

    monkeypatch.setattr(organizations.httpx, "AsyncClient", NotFoundClient)
    try:
        response = TestClient(create_app()).get(
            f"/api/v1/organizations/{ORG_ID}",
            headers={"Authorization": "Bearer valid-token", "x-request-id": "notfound-id"}
        )
        assert response.status_code == 404
        assert response.headers["x-request-id"] == "notfound-id"
        assert response.json()["requestId"] == "notfound-id"
    finally:
        get_settings.cache_clear()


def test_create_organization_success(monkeypatch):
    configure_auth(monkeypatch)
    async def create(request, user_id, token):
        assert request.name == "New Org"
        assert request.slug == "new-org"
        assert user_id == UUID("00000000-0000-0000-0000-000000000123")
        return organizations.OrganizationResponse(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            name="New Org",
            slug="new-org",
            created_at="2026-01-01T00:00:00Z"
        )
    monkeypatch.setattr("app.main.create_organization", create)
    monkeypatch.setattr(auth, "_verify_sync", lambda t, url: {"sub": "00000000-0000-0000-0000-000000000123"})
    try:
        response = TestClient(create_app()).post(
            "/api/v1/organizations",
            headers={"Authorization": "Bearer valid-token"},
            json={"name": "New Org", "slug": "new-org"}
        )
        assert response.status_code == 201
        assert response.json()["name"] == "New Org"
    finally:
        get_settings.cache_clear()


def test_create_organization_validation_error(monkeypatch):
    configure_auth(monkeypatch)
    response = TestClient(create_app()).post(
        "/api/v1/organizations",
        headers={"Authorization": "Bearer valid-token"},
        json={"name": "", "slug": "invalid-slug!"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"



def test_unhandled_500_includes_x_request_id(monkeypatch):
    """Test that unhandled 500 responses include X-Request-ID header."""
    configure_auth(monkeypatch)

    async def boom(org_id, token):
        raise RuntimeError("database crashed")

    monkeypatch.setattr("app.main.read_organization", boom)
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        response = client.get(
            f"/api/v1/organizations/{ORG_ID}",
            headers={"Authorization": "Bearer valid-token", "x-request-id": "crash-500-id"}
        )
        assert response.status_code == 500
        assert response.headers["x-request-id"] == "crash-500-id"
        assert response.json()["requestId"] == "crash-500-id"
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    finally:
        get_settings.cache_clear()