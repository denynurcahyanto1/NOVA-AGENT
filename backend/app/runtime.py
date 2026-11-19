import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import CamelModel, get_settings
from app.llm import LLMProvider, LLMResponse, OpenAIProvider

logger = logging.getLogger("nova.runtime")


class RunEvent(BaseModel):
    event_type: str
    payload: Dict[str, Any]
    sequence: int
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class StartRunRequest(CamelModel):
    agent_version_id: UUID
    input: Dict[str, Any]
    conversation_id: Optional[UUID] = None


class StartRunResponse(CamelModel):
    run_id: UUID
    status: str
    events_url: str


class AgentRun(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    organization_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    status: str
    input: Dict[str, Any]
    conversation_id: Optional[UUID] = None
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


@dataclass
class RunState:
    run_id: UUID
    org_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    input_data: Dict[str, Any]
    conversation_id: Optional[UUID]
    status: str = "queued"
    sequence: int = 0
    events: List[RunEvent] = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    usage: Dict[str, int] = field(default_factory=dict)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


_runs: Dict[UUID, RunState] = {}


async def _get_agent_draft(agent_version_id: UUID, access_token: str, org_id: UUID) -> Dict[str, Any]:
    """Fetch agent version snapshot from Supabase."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Agent service is not configured")

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/agent_versions"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }
    params = {
        "id": f"eq.{agent_version_id}",
        "organization_id": f"eq.{org_id}",
        "select": "id,agent_id,snapshot,version_number",
    }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(url, headers=headers, params=params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Agent service unavailable")

    try:
        rows = response.json()
    except (ValueError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Agent version not found")

    return rows[0]


async def _get_provider_credential(provider_credential_id: UUID, access_token: str, org_id: UUID) -> Dict[str, Any]:
    """Fetch provider credential from Supabase (encrypted)."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Agent service is not configured")

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/provider_credentials"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }
    params = {
        "id": f"eq.{provider_credential_id}",
        "organization_id": f"eq.{org_id}",
        "select": "id,provider,label,encrypted_dek,ciphertext,key_version",
    }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(url, headers=headers, params=params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Provider credential service unavailable") from None

    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Provider credential service unavailable")

    try:
        rows = response.json()
    except (ValueError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Provider credential service unavailable") from None

    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Provider credential not found")

    return rows[0]


def _create_llm_provider(provider_credential: Dict[str, Any]) -> LLMProvider:
    """Create LLM provider instance from credential."""
    provider = provider_credential.get("provider", "").lower()
    # In production, decrypt the credential here
    # For now, use a placeholder API key
    api_key = "sk-placeholder"
    if provider == "openai":
        return OpenAIProvider(api_key=api_key)
    raise HTTPException(status_code=503, detail=f"Unsupported provider: {provider}")


async def _emit_event(state: RunState, event_type: str, payload: Dict[str, Any]):
    state.sequence += 1
    event = RunEvent(event_type=event_type, payload=payload, sequence=state.sequence)
    state.events.append(event)


async def _execute_agent_run(state: RunState, access_token: str):
    """Background task to execute the agent."""
    try:
        await _emit_event(state, "run.status", {"status": "running"})
        state.status = "running"
        state.started_at = datetime.now(timezone.utc).isoformat()

        # Fetch agent version snapshot
        agent_data = await _get_agent_draft(state.agent_version_id, access_token, state.org_id)
        agent_id = UUID(agent_data["agent_id"])
        snapshot = agent_data["snapshot"]

        # Get provider credential
        provider_credential_id = snapshot.get("providerCredentialId")
        if not provider_credential_id:
            raise HTTPException(status_code=500, detail="Provider credential not configured in agent snapshot")

        credential = await _get_provider_credential(UUID(provider_credential_id), access_token, state.org_id)
        provider = _create_llm_provider(credential)

        # Build prompt from input and snapshot instructions
        instructions = snapshot.get("instructions", "")
        user_message = state.input_data.get("message", "")
        prompt = f"{instructions}\n\nUser: {user_message}"

        model = snapshot.get("model", "gpt-4o")
        if not provider.validate_model(model):
            raise HTTPException(status_code=500, detail=f"Invalid model for provider: {model}")

        await _emit_event(state, "run.token", {"token": "Starting agent execution..."})

        # Call LLM
        response: LLMResponse = await provider.generate(prompt, model)

        # Emit tokens (simulated streaming)
        for chunk in response.content.split():
            await _emit_event(state, "run.token", {"token": chunk + " "})
            await asyncio.sleep(0.01)  # Simulate streaming delay

        # Emit usage
        usage = response.usage
        state.usage = usage
        await _emit_event(state, "run.usage", usage)

        # Complete
        await _emit_event(state, "run.completed", {
            "output": {"content": response.content},
            "usage": usage,
        })
        state.status = "completed"
        state.ended_at = datetime.now(timezone.utc).isoformat()

    except HTTPException as e:
        await _emit_event(state, "run.failed", {"error": e.detail})
        state.status = "failed"
        state.ended_at = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.exception(f"Run {state.run_id} failed: {e}")
        await _emit_event(state, "run.failed", {"error": "Internal execution error"})
        state.status = "failed"
        state.ended_at = datetime.now(timezone.utc).isoformat()


async def create_agent_run(
    org_id: UUID,
    agent_id: UUID,
    request: StartRunRequest,
    user_id: UUID,
    access_token: str,
) -> StartRunResponse:
    """Create a new agent run and return run ID + events URL."""
    # Validate agent version belongs to org
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Agent service is not configured")

    # Check agent version exists and belongs to org/agent
    version_data = await _get_agent_draft(request.agent_version_id, access_token, org_id)
    if UUID(version_data["agent_id"]) != agent_id:
        raise HTTPException(status_code=404, detail="Agent version not found for this agent")

    run_id = uuid.uuid4()
    events_url = f"/api/v1/organizations/{org_id}/runs/{run_id}/events"

    state = RunState(
        run_id=run_id,
        org_id=org_id,
        agent_id=agent_id,
        agent_version_id=request.agent_version_id,
        input_data=request.input,
        conversation_id=request.conversation_id,
    )
    _runs[run_id] = state

    # Start background execution
    state.task = asyncio.create_task(_execute_agent_run(state, access_token))

    return StartRunResponse(run_id=run_id, status="queued", events_url=events_url)


async def stream_run_events(
    org_id: UUID,
    run_id: UUID,
    access_token: str,
    last_event_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """SSE generator for run events."""
    state = _runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    if state.org_id != org_id:
        raise HTTPException(status_code=404, detail="Run not found")

    # Replay events from last_event_id
    start_idx = 0
    if last_event_id is not None:
        for i, event in enumerate(state.events):
            if event.sequence > last_event_id:
                start_idx = i
                break

    for event in state.events[start_idx:]:
        yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(event.payload)}\n\n"

    # Stream new events as they arrive
    last_sequence = state.sequence
    max_wait = 30  # Safety timeout for streaming loops (15 seconds)
    loops = 0
    while state.status in ("queued", "running") and loops < max_wait:
        await asyncio.sleep(0.5)
        loops += 1
        if state.sequence > last_sequence:
            for event in state.events[last_sequence:]:
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(event.payload)}\n\n"
            last_sequence = state.sequence

    # Final event if completed/failed
    if state.status in ("completed", "failed"):
        yield f"id: {state.sequence}\nevent: run.status\ndata: {json.dumps({'status': state.status})}\n\n"


async def get_run_status(
    org_id: UUID,
    run_id: UUID,
    access_token: str,
) -> AgentRun:
    """Get run status (for terminal reconciliation)."""
    state = _runs.get(run_id)
    if not state or state.org_id != org_id:
        raise HTTPException(status_code=404, detail="Run not found")

    return AgentRun(
        id=state.run_id,
        organization_id=state.org_id,
        agent_id=state.agent_id,
        agent_version_id=state.agent_version_id,
        status=state.status,
        input=state.input_data,
        conversation_id=state.conversation_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        started_at=state.started_at,
        ended_at=state.ended_at,
    )