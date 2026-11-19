"""Integration tests for tenant isolation on GET /api/v1/organizations/{id}."""

import pytest
from uuid import UUID
from fastapi.testclient import TestClient
import httpx

from app.config import get_settings
from app.main import create_app
from app import auth, organizations

ORG_A_ID = UUID("00000000-0000-0000-0000-000000000001")
ORG_B_ID = UUID("00000000-0000-0000-0000-000000000002")

USER_A_ID = "user-a"
USER_B_ID = "user-b"

SUPABASE_URL = "https://example.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "public-test-key"


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", SUPABASE_PUBLISHABLE_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class MockRLSSupabaseClient:
    """Mock Supabase Data API that simulates RLS policy.
    
    RLS Policy rules:
    - User A is member of Org A only.
    - User B is member of Org B only.
    - When a user queries for an org they are not a member of, Supabase returns empty array [].
    """

    def __init__(self, user_id: str):
        self.user_id = user_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, headers, params):
        # Verify Authorization header contains the user token
        assert "Authorization" in headers
        auth_header = headers["Authorization"]
        assert auth_header.startswith("Bearer ")

        # Parse requested org id from params
        id_filter = params.get("id", "")
        requested_id = id_filter.replace("eq.", "")

        # RLS simulation:
        # User A can only see Org A
        if self.user_id == USER_A_ID and requested_id == str(ORG_A_ID):
            return httpx.Response(200, json=[{
                "id": str(ORG_A_ID),
                "name": "Org A",
                "slug": "org-a",
                "created_at": "2026-01-01T00:00:00Z"
            }])
        # User B can only see Org B
        elif self.user_id == USER_B_ID and requested_id == str(ORG_B_ID):
            return httpx.Response(200, json=[{
                "id": str(ORG_B_ID),
                "name": "Org B",
                "slug": "org-b",
                "created_at": "2026-01-02T00:00:00Z"
            }])
        else:
            # RLS hides records: returns empty list
            return httpx.Response(200, json=[])


def test_user_can_access_own_organization(monkeypatch):
    """User A successfully accesses Org A."""
    monkeypatch.setattr(auth, "_verify_sync", lambda t, url: {"sub": USER_A_ID})
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockRLSSupabaseClient(USER_A_ID))

    client = TestClient(create_app())
    response = client.get(
        f"/api/v1/organizations/{ORG_A_ID}",
        headers={"Authorization": "Bearer token-user-a"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(ORG_A_ID)
    assert data["name"] == "Org A"
    assert data["slug"] == "org-a"
    assert data["createdAt"] == "2026-01-01T00:00:00Z"


def test_user_cannot_access_other_organization_cross_org(monkeypatch):
    """User A attempts to access Org B -> returns 404 (non-enumerating)."""
    monkeypatch.setattr(auth, "_verify_sync", lambda t, url: {"sub": USER_A_ID})
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockRLSSupabaseClient(USER_A_ID))

    client = TestClient(create_app())
    response = client.get(
        f"/api/v1/organizations/{ORG_B_ID}",
        headers={"Authorization": "Bearer token-user-a"}
    )
    # Must be 404 to avoid leaking existence of Org B
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Organization not found"


def test_user_b_can_access_own_organization(monkeypatch):
    """User B successfully accesses Org B."""
    monkeypatch.setattr(auth, "_verify_sync", lambda t, url: {"sub": USER_B_ID})
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockRLSSupabaseClient(USER_B_ID))

    client = TestClient(create_app())
    response = client.get(
        f"/api/v1/organizations/{ORG_B_ID}",
        headers={"Authorization": "Bearer token-user-b"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(ORG_B_ID)
    assert data["name"] == "Org B"


def test_user_b_cannot_access_org_a(monkeypatch):
    """User B attempts to access Org A -> returns 404."""
    monkeypatch.setattr(auth, "_verify_sync", lambda t, url: {"sub": USER_B_ID})
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockRLSSupabaseClient(USER_B_ID))

    client = TestClient(create_app())
    response = client.get(
        f"/api/v1/organizations/{ORG_A_ID}",
        headers={"Authorization": "Bearer token-user-b"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Organization not found"


def test_unauthenticated_user_cannot_access_any_org(monkeypatch):
    """Missing or invalid token rejected before database access."""
    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_A_ID}")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Authentication required"


def test_service_role_key_is_never_forwarded(monkeypatch):
    """Verify that backend never uses or forwards a service-role key for reads."""
    seen_headers = {}

    class InspectingClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            seen_headers.update(headers)
            return httpx.Response(200, json=[{
                "id": str(ORG_A_ID), "name": "Org A", "slug": "org-a", "created_at": "2026-01-01T00:00:00Z"
            }])

    monkeypatch.setattr(auth, "_verify_sync", lambda t, url: {"sub": USER_A_ID})
    monkeypatch.setattr(organizations.httpx, "AsyncClient", InspectingClient)

    client = TestClient(create_app())
    response = client.get(
        f"/api/v1/organizations/{ORG_A_ID}",
        headers={"Authorization": "Bearer user-token-xyz"}
    )
    assert response.status_code == 200
    assert seen_headers["Authorization"] == "Bearer user-token-xyz"
    assert seen_headers["apikey"] == SUPABASE_PUBLISHABLE_KEY
    assert "service_role" not in str(seen_headers).lower()
