from uuid import UUID
import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings
from app.main import create_app
from app import agents

ORG_A = UUID("00000000-0000-0000-0000-000000000001")
ORG_B = UUID("00000000-0000-0000-0000-000000000002")
AGENT_ID = UUID("11111111-1111-1111-1111-111111111111")
USER_ID = UUID("22222222-2222-2222-2222-222222222222")


def configure_auth(monkeypatch, user_id=USER_ID):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "_verify_sync", lambda token, url: {"sub": str(user_id)})


def test_create_agent_draft_success(monkeypatch):
    """User can create an agent draft with valid payload and token."""
    configure_auth(monkeypatch)
    captured = {}

    class MockClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                201,
                json=[
                    {
                        "id": str(AGENT_ID),
                        "organization_id": str(ORG_A),
                        "name": json["name"],
                        "draft_config": json["draft_config"],
                        "draft_revision": 0,
                        "created_at": "2026-11-19T00:00:00Z",
                        "updated_at": "2026-11-19T00:00:00Z",
                    }
                ],
            )

    monkeypatch.setattr(agents.httpx, "AsyncClient", MockClient)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_A}/agents",
        headers={"Authorization": "Bearer valid-user-jwt"},
        json={
            "name": "Customer Support Agent",
            "description": "Handles inbound queries",
            "draftConfig": {
                "instructions": "You are a friendly support rep.",
                "model": "gpt-4o",
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(AGENT_ID)
    assert body["organizationId"] == str(ORG_A)
    assert body["name"] == "Customer Support Agent"
    assert body["draftConfig"]["model"] == "gpt-4o"
    assert "draftRevision" in body
    assert "createdAt" in body
    assert "updatedAt" in body

    # Token forwarding and publishable key verification
    assert captured["headers"]["Authorization"] == "Bearer valid-user-jwt"
    assert captured["headers"]["apikey"] == "public-test-key"
    assert "service_role" not in str(captured["headers"])
    assert captured["json"]["organization_id"] == str(ORG_A)


def test_create_agent_validation_error(monkeypatch):
    """Empty agent name or invalid payload rejected with 422."""
    configure_auth(monkeypatch)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_A}/agents",
        headers={"Authorization": "Bearer valid-user-jwt"},
        json={"name": ""},  # min_length=1 violated
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.headers.get("x-request-id")


def test_publish_agent_version_success_and_increments(monkeypatch):
    """Publish creates an immutable version and increments version_number."""
    configure_auth(monkeypatch)
    captured_insert = {}

    class MockClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers, params):
            if "agent_versions" in url:
                # Returns highest previous version = 1
                return httpx.Response(200, json=[{"version_number": 1}])
            # Returns agent draft
            return httpx.Response(
                200,
                json=[
                    {
                        "id": str(AGENT_ID),
                        "organization_id": str(ORG_A),
                        "name": "Customer Support Agent",
                        "draft_config": {"instructions": "v2 prompt", "model": "claude-3-5-sonnet"},
                        "draft_revision": 1,
                    }
                ],
            )

        async def post(self, url, headers, json):
            captured_insert["url"] = url
            captured_insert["headers"] = headers
            captured_insert["json"] = json
            return httpx.Response(
                201,
                json=[
                    {
                        "id": "33333333-3333-3333-3333-333333333333",
                        "organization_id": json["organization_id"],
                        "agent_id": json["agent_id"],
                        "version_number": json["version_number"],
                        "snapshot": json["snapshot"],
                        "published_by": json["published_by"],
                        "published_at": "2026-11-19T01:00:00Z",
                    }
                ],
            )

    monkeypatch.setattr(agents.httpx, "AsyncClient", MockClient)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_A}/agents/{AGENT_ID}/publish",
        headers={"Authorization": "Bearer valid-user-jwt"},
    )

    assert response.status_code == 201
    body = response.json()
    assert "version" in body
    assert body["version"]["id"] == "33333333-3333-3333-3333-333333333333"
    assert body["version"]["versionNumber"] == 2  # incremented from 1 -> 2
    assert body["version"]["publishedAt"] == "2026-11-19T01:00:00Z"

    # Verify snapshot received the draft_config
    assert captured_insert["json"]["snapshot"]["model"] == "claude-3-5-sonnet"
    assert captured_insert["json"]["version_number"] == 2
    assert captured_insert["json"]["published_by"] == str(USER_ID)
    assert captured_insert["headers"]["Authorization"] == "Bearer valid-user-jwt"
    assert captured_insert["headers"]["apikey"] == "public-test-key"
    assert "service_role" not in str(captured_insert["headers"])


def test_tenant_isolation_cross_org_access_returns_404(monkeypatch):
    """Attempting to access or publish an agent belonging to another org returns 404."""
    configure_auth(monkeypatch)

    class MockClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers, params):
            # Upstream RLS / query filter returns empty list when org_id does not match
            return httpx.Response(200, json=[])

        async def post(self, url, headers, json):
            # Upstream RLS denies insertion in other org
            return httpx.Response(403, json={"error": "forbidden"})

    monkeypatch.setattr(agents.httpx, "AsyncClient", MockClient)

    client = TestClient(create_app())

    # Try creating agent in Org B (which caller has no access to)
    resp_create = client.post(
        f"/api/v1/organizations/{ORG_B}/agents",
        headers={"Authorization": "Bearer user-token"},
        json={"name": "Attacker Agent"},
    )
    assert resp_create.status_code == 404

    # Try publishing agent with mismatched org
    resp_publish = client.post(
        f"/api/v1/organizations/{ORG_B}/agents/{AGENT_ID}/publish",
        headers={"Authorization": "Bearer user-token"},
    )
    assert resp_publish.status_code == 404


def test_unauthenticated_requests_blocked(monkeypatch):
    """Requests without valid Bearer token return 401."""
    client = TestClient(create_app())

    r1 = client.post(f"/api/v1/organizations/{ORG_A}/agents", json={"name": "Test"})
    assert r1.status_code == 401

    r2 = client.post(f"/api/v1/organizations/{ORG_A}/agents/{AGENT_ID}/publish")
    assert r2.status_code == 401
