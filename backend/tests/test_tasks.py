import asyncio
from uuid import UUID
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings
from app.main import create_app
from app import tasks

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
AGENT_1 = UUID("11111111-1111-1111-1111-111111111111")
AGENT_2 = UUID("22222222-2222-2222-2222-222222222222")
USER_ID = UUID("33333333-3333-3333-3333-333333333333")


def configure_auth(monkeypatch, user_id=USER_ID):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "_verify_sync", lambda token, url: {"sub": str(user_id)})


def test_create_task_success(monkeypatch):
    """POST /tasks creates a new multi-agent task."""
    configure_auth(monkeypatch)

    async def mock_get_agents(url, headers, params):
        import httpx
        agent_id = params.get("id", "").replace("eq.", "")
        return httpx.Response(200, json=[{"id": agent_id}])

    class MockClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            return await mock_get_agents(url, headers, params)

    monkeypatch.setattr(tasks.httpx, "AsyncClient", MockClient)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_ID}/tasks",
        headers={"Authorization": "Bearer valid-jwt"},
        json={
            "name": "Multi-agent workflow",
            "description": "Process customer inquiry",
            "agentIds": [str(AGENT_1), str(AGENT_2)],
            "input": {"query": "How do I reset my password?"},
            "config": {"timeout": 300},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["name"] == "Multi-agent workflow"
    assert body["agentIds"] == [str(AGENT_1), str(AGENT_2)]
    assert body["status"] == "pending"
    assert "createdAt" in body


def test_create_task_validation_error(monkeypatch):
    """Empty agentIds or invalid payload returns 422."""
    configure_auth(monkeypatch)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_ID}/tasks",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"name": "Test", "agentIds": []},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_task_state(monkeypatch):
    """GET /tasks/{taskId} returns current task state."""
    configure_auth(monkeypatch)

    async def mock_get_agents(url, headers, params):
        import httpx
        agent_id = params.get("id", "").replace("eq.", "")
        return httpx.Response(200, json=[{"id": agent_id}])

    class MockClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            return await mock_get_agents(url, headers, params)

    monkeypatch.setattr(tasks.httpx, "AsyncClient", MockClient)

    client = TestClient(create_app())
    create_resp = client.post(
        f"/api/v1/organizations/{ORG_ID}/tasks",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"name": "Test Task", "agentIds": [str(AGENT_1)]},
    )
    task_id = create_resp.json()["id"]

    response = client.get(
        f"/api/v1/organizations/{ORG_ID}/tasks/{task_id}",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task_id
    assert body["status"] == "pending"
    assert "progress" in body
    assert "results" in body


def test_execute_task_starts_runs(monkeypatch):
    """POST /tasks/{taskId}/execute starts sub-runs for each agent."""
    configure_auth(monkeypatch)

    async def mock_get_agents(url, headers, params):
        import httpx
        agent_id = params.get("id", "").replace("eq.", "")
        return httpx.Response(200, json=[{"id": agent_id}])

    async def mock_get_versions(url, headers, params):
        import httpx
        return httpx.Response(200, json=[{"id": "33333333-3333-3333-3333-333333333333", "version_number": 1}])

    class MockClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            if "agent_versions" in url:
                return await mock_get_versions(url, headers, params)
            return await mock_get_agents(url, headers, params)

    monkeypatch.setattr(tasks.httpx, "AsyncClient", MockClient)

    # Mock runtime.create_agent_run
    async def mock_create_run(org_id, agent_id, request, user_id, token):
        from app.runtime import StartRunResponse
        import uuid
        return StartRunResponse(
            run_id=uuid.uuid4(),
            status="queued",
            events_url=f"/api/v1/organizations/{org_id}/runs/test/events",
        )

    monkeypatch.setattr("app.runtime.create_agent_run", mock_create_run)

    client = TestClient(create_app())
    create_resp = client.post(
        f"/api/v1/organizations/{ORG_ID}/tasks",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"name": "Execute Test", "agentIds": [str(AGENT_1), str(AGENT_2)]},
    )
    task_id = create_resp.json()["id"]

    exec_resp = client.post(
        f"/api/v1/organizations/{ORG_ID}/tasks/{task_id}/execute",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert exec_resp.status_code == 202
    body = exec_resp.json()
    assert body["status"] == "running"
    assert "progress" in body
    assert str(AGENT_1) in body["progress"]
    assert str(AGENT_2) in body["progress"]


def test_tenant_isolation_task(monkeypatch):
    """Tasks cannot be accessed across organizations."""
    configure_auth(monkeypatch)

    async def mock_get_agents(url, headers, params):
        import httpx
        agent_id = params.get("id", "").replace("eq.", "")
        return httpx.Response(200, json=[{"id": agent_id}])

    class MockClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers, params):
            return await mock_get_agents(url, headers, params)

    monkeypatch.setattr(tasks.httpx, "AsyncClient", MockClient)

    client = TestClient(create_app())
    ORG_B = UUID("00000000-0000-0000-0000-000000000002")

    create_resp = client.post(
        f"/api/v1/organizations/{ORG_ID}/tasks",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"name": "Test Task", "agentIds": [str(AGENT_1)]},
    )
    task_id = create_resp.json()["id"]

    # Try to access from different org
    resp = client.get(
        f"/api/v1/organizations/{ORG_B}/tasks/{task_id}",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert resp.status_code == 404

    # Try to execute from different org
    resp2 = client.post(
        f"/api/v1/organizations/{ORG_B}/tasks/{task_id}/execute",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert resp2.status_code == 404


def test_unauthenticated_blocked(monkeypatch):
    """Unauthenticated requests blocked."""
    client = TestClient(create_app())

    r1 = client.post(f"/api/v1/organizations/{ORG_ID}/tasks", json={"name": "Test", "agentIds": [str(AGENT_1)]})
    assert r1.status_code == 401

    r2 = client.get(f"/api/v1/organizations/{ORG_ID}/tasks/some-id")
    assert r2.status_code == 401

    r3 = client.post(f"/api/v1/organizations/{ORG_ID}/tasks/some-id/execute")
    assert r3.status_code == 401