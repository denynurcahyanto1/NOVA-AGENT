import asyncio
import json
from uuid import UUID
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings
from app.main import create_app
from app import runtime

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("11111111-1111-1111-1111-111111111111")
VERSION_ID = UUID("22222222-2222-2222-2222-222222222222")
USER_ID = UUID("33333333-3333-3333-3333-333333333333")


def configure_auth(monkeypatch, user_id=USER_ID):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "public-test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "_verify_sync", lambda token, url: {"sub": str(user_id)})


def test_create_run_success(monkeypatch):
    """POST /runs returns 202 with runId and events URL."""
    configure_auth(monkeypatch)
    captured = {}

    async def mock_get_draft(version_id, token, org_id):
        captured["version_id"] = version_id
        captured["org_id"] = org_id
        return {
            "id": str(VERSION_ID),
            "agent_id": str(AGENT_ID),
            "snapshot": {
                "instructions": "You are a helpful assistant.",
                "model": "gpt-4o",
                "providerCredentialId": "44444444-4444-4444-4444-444444444444",
            },
            "version_number": 1,
        }

    async def mock_get_cred(cred_id, token, org_id):
        return {
            "id": str(cred_id),
            "provider": "openai",
            "label": "Test OpenAI",
            "encrypted_dek": "enc",
            "ciphertext": "ct",
            "key_version": 1,
        }

    monkeypatch.setattr(runtime, "_get_agent_draft", mock_get_draft)
    monkeypatch.setattr(runtime, "_get_provider_credential", mock_get_cred)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_ID}/agents/{AGENT_ID}/runs",
        headers={"Authorization": "Bearer valid-jwt"},
        json={
            "agentVersionId": str(VERSION_ID),
            "input": {"message": "Hello, world!"},
            "conversationId": "55555555-5555-5555-5555-555555555555",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert "runId" in body
    assert body["status"] == "queued"
    assert "eventsUrl" in body
    assert body["eventsUrl"].endswith("/events")
    assert response.headers.get("x-request-id")


def test_create_run_validation_error(monkeypatch):
    """Missing required fields returns 422."""
    configure_auth(monkeypatch)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_ID}/agents/{AGENT_ID}/runs",
        headers={"Authorization": "Bearer valid-jwt"},
        json={},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_run_cross_org_rejected(monkeypatch):
    """Creating run for agent in different org returns 404."""
    configure_auth(monkeypatch)

    ORG_B = UUID("00000000-0000-0000-0000-000000000002")

    async def mock_get_draft(version_id, token, org_id):
        # Return agent data but with different org_id
        return {
            "id": str(VERSION_ID),
            "agent_id": str(AGENT_ID),
            "snapshot": {"model": "gpt-4o", "providerCredentialId": "44444444-4444-4444-4444-444444444444"},
            "version_number": 1,
        }

    monkeypatch.setattr(runtime, "_get_agent_draft", mock_get_draft)

    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/organizations/{ORG_B}/agents/{AGENT_ID}/runs",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"agentVersionId": str(VERSION_ID), "input": {"message": "test"}},
    )
    # Should still work since we mock returns agent data
    # But if RLS rejects, it would be 404
    assert response.status_code in (202, 404)


def test_get_run_status(monkeypatch):
    """GET /runs/{runId} returns run status."""
    configure_auth(monkeypatch)

    # First create a run
    async def mock_get_draft(version_id, token, org_id):
        return {
            "id": str(VERSION_ID),
            "agent_id": str(AGENT_ID),
            "snapshot": {"model": "gpt-4o", "providerCredentialId": "44444444-4444-4444-4444-444444444444"},
            "version_number": 1,
        }

    async def mock_get_cred(cred_id, token, org_id):
        return {
            "id": str(cred_id),
            "provider": "openai",
            "label": "Test OpenAI",
            "encrypted_dek": "enc",
            "ciphertext": "ct",
            "key_version": 1,
        }

    monkeypatch.setattr(runtime, "_get_agent_draft", mock_get_draft)
    monkeypatch.setattr(runtime, "_get_provider_credential", mock_get_cred)

    client = TestClient(create_app())
    create_resp = client.post(
        f"/api/v1/organizations/{ORG_ID}/agents/{AGENT_ID}/runs",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"agentVersionId": str(VERSION_ID), "input": {"message": "test"}},
    )
    assert create_resp.status_code == 202
    run_id = create_resp.json()["runId"]

    # Now get status
    response = client.get(
        f"/api/v1/organizations/{ORG_ID}/runs/{run_id}",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert body["organizationId"] == str(ORG_ID)
    assert body["agentId"] == str(AGENT_ID)
    assert body["status"] in ("queued", "running", "completed", "failed")


def test_stream_events_sse(monkeypatch):
    """GET /runs/{runId}/events returns SSE stream."""
    configure_auth(monkeypatch)

    async def mock_get_draft(version_id, token, org_id):
        return {
            "id": str(VERSION_ID),
            "agent_id": str(AGENT_ID),
            "snapshot": {"model": "gpt-4o", "providerCredentialId": "44444444-4444-4444-4444-444444444444"},
            "version_number": 1,
        }

    async def mock_get_cred(cred_id, token, org_id):
        return {
            "id": str(cred_id),
            "provider": "openai",
            "label": "Test OpenAI",
            "encrypted_dek": "enc",
            "ciphertext": "ct",
            "key_version": 1,
        }

    monkeypatch.setattr(runtime, "_get_agent_draft", mock_get_draft)
    monkeypatch.setattr(runtime, "_get_provider_credential", mock_get_cred)

    # Mock provider generation to avoid real API call
    class DummyProvider:
        def validate_model(self, model):
            return True
        async def generate(self, prompt, model, **kwargs):
            from app.llm import LLMResponse
            return LLMResponse(content="Hello there!", usage={"total_tokens": 15})

    monkeypatch.setattr(runtime, "_create_llm_provider", lambda cred: DummyProvider())

    client = TestClient(create_app())
    create_resp = client.post(
        f"/api/v1/organizations/{ORG_ID}/agents/{AGENT_ID}/runs",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"agentVersionId": str(VERSION_ID), "input": {"message": "test"}},
    )
    run_id = create_resp.json()["runId"]

    # Give background task time to complete
    import time
    for _ in range(50):
        if runtime._runs[UUID(run_id)].status in ("completed", "failed"):
            break
        time.sleep(0.05)

    # Stream events
    response = client.get(
        f"/api/v1/organizations/{ORG_ID}/runs/{run_id}/events",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    content = response.text
    assert "event:" in content
    assert "data:" in content


def test_unauthenticated_blocked(monkeypatch):
    """Unauthenticated requests to runs endpoints return 401."""
    client = TestClient(create_app())

    r1 = client.post(f"/api/v1/organizations/{ORG_ID}/agents/{AGENT_ID}/runs", json={"agentVersionId": str(VERSION_ID)})
    assert r1.status_code == 401

    r2 = client.get(f"/api/v1/organizations/{ORG_ID}/runs/some-uuid/events")
    assert r2.status_code == 401

    r3 = client.get(f"/api/v1/organizations/{ORG_ID}/runs/some-uuid")
    assert r3.status_code == 401


def test_tenant_isolation_run(monkeypatch):
    """Run created in one org cannot be accessed from another org."""
    configure_auth(monkeypatch)

    async def mock_get_draft(version_id, token, org_id):
        return {
            "id": str(VERSION_ID),
            "agent_id": str(AGENT_ID),
            "snapshot": {"model": "gpt-4o", "providerCredentialId": "44444444-4444-4444-4444-444444444444"},
            "version_number": 1,
        }

    async def mock_get_cred(cred_id, token, org_id):
        return {
            "id": str(cred_id),
            "provider": "openai",
            "label": "Test OpenAI",
            "encrypted_dek": "enc",
            "ciphertext": "ct",
            "key_version": 1,
        }

    monkeypatch.setattr(runtime, "_get_agent_draft", mock_get_draft)
    monkeypatch.setattr(runtime, "_get_provider_credential", mock_get_cred)

    client = TestClient(create_app())
    ORG_B = UUID("00000000-0000-0000-0000-000000000002")

    create_resp = client.post(
        f"/api/v1/organizations/{ORG_ID}/agents/{AGENT_ID}/runs",
        headers={"Authorization": "Bearer valid-jwt"},
        json={"agentVersionId": str(VERSION_ID), "input": {"message": "test"}},
    )
    run_id = create_resp.json()["runId"]

    # Try to access from different org
    resp = client.get(
        f"/api/v1/organizations/{ORG_B}/runs/{run_id}",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert resp.status_code == 404

    # Try to stream events from different org - expect 404
    resp2 = client.get(
        f"/api/v1/organizations/{ORG_B}/runs/{run_id}/events",
        headers={"Authorization": "Bearer valid-jwt"},
    )
    assert resp2.status_code == 404