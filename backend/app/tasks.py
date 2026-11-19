import asyncio
import logging
import httpx
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import CamelModel, get_settings

logger = logging.getLogger("nova.tasks")


class CreateTaskRequest(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    agent_ids: List[UUID] = Field(min_length=1)
    input: Dict[str, Any] = Field(default_factory=dict)
    config: Optional[Dict[str, Any]] = Field(default_factory=dict)


class TaskResponse(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    organization_id: UUID = Field(alias="organizationId")
    name: str
    description: Optional[str] = None
    agent_ids: List[UUID] = Field(alias="agentIds")
    status: str
    input: Dict[str, Any]
    config: Dict[str, Any]
    created_at: str = Field(alias="createdAt")
    started_at: Optional[str] = Field(default=None, alias="startedAt")
    ended_at: Optional[str] = Field(default=None, alias="endedAt")


class TaskStateResponse(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    status: str
    progress: Dict[str, Any] = Field(default_factory=dict)
    results: Dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(alias="updatedAt")


@dataclass
class TaskState:
    task_id: UUID
    org_id: UUID
    name: str
    description: Optional[str]
    agent_ids: List[UUID]
    input_data: Dict[str, Any]
    config: Dict[str, Any]
    status: str = "pending"
    progress: Dict[str, Any] = field(default_factory=dict)
    results: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    sub_run_ids: Dict[UUID, UUID] = field(default_factory=dict)  # agent_id -> run_id


_tasks: Dict[UUID, TaskState] = {}


async def create_task(
    org_id: UUID,
    request: CreateTaskRequest,
    user_id: UUID,
    access_token: str,
) -> TaskResponse:
    """Create a new multi-agent task."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Task service is not configured")

    # Validate all agents belong to org
    import httpx
    agent_url = f"{settings.supabase_url.rstrip('/')}/rest/v1/agents"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        for agent_id in request.agent_ids:
            params = {
                "id": f"eq.{agent_id}",
                "organization_id": f"eq.{org_id}",
                "select": "id",
                "limit": "1",
            }
            try:
                response = await client.get(agent_url, headers=headers, params=params)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
                raise HTTPException(status_code=503, detail="Agent service unavailable") from None

            if response.status_code != 200:
                raise HTTPException(status_code=503, detail="Agent service unavailable")

            try:
                rows = response.json()
            except (ValueError, httpx.DecodingError):
                raise HTTPException(status_code=503, detail="Agent service unavailable") from None

            if not isinstance(rows, list) or not rows:
                raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found in this organization")

    task_id = UUID(int=0)  # Will be replaced by DB on insert
    # For now, generate locally
    import uuid
    task_id = uuid.uuid4()

    state = TaskState(
        task_id=task_id,
        org_id=org_id,
        name=request.name,
        description=request.description,
        agent_ids=request.agent_ids,
        input_data=request.input,
        config=request.config,
    )
    _tasks[task_id] = state

    return TaskResponse(
        id=state.task_id,
        organization_id=state.org_id,
        name=state.name,
        description=state.description,
        agent_ids=state.agent_ids,
        status=state.status,
        input=state.input_data,
        config=state.config,
        created_at=state.created_at,
        started_at=state.started_at,
        ended_at=state.ended_at,
    )


async def get_task_state(
    org_id: UUID,
    task_id: UUID,
    access_token: str,
) -> TaskStateResponse:
    """Get current task state."""
    state = _tasks.get(task_id)
    if not state or state.org_id != org_id:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStateResponse(
        id=state.task_id,
        status=state.status,
        progress=state.progress,
        results=state.results,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


async def execute_task(
    org_id: UUID,
    task_id: UUID,
    user_id: UUID,
    access_token: str,
) -> TaskStateResponse:
    """Execute the multi-agent task (orchestrate agent runs)."""
    state = _tasks.get(task_id)
    if not state or state.org_id != org_id:
        raise HTTPException(status_code=404, detail="Task not found")

    if state.status == "running":
        raise HTTPException(status_code=409, detail="Task already running")
    if state.status == "completed":
        raise HTTPException(status_code=409, detail="Task already completed")

    state.status = "running"
    state.started_at = datetime.now(timezone.utc).isoformat()

    # For each agent, create a run
    # In Phase 6, we use the latest published version of each agent
    from app.runtime import create_agent_run, _get_agent_draft, _runs as runtime_runs
    import httpx

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        state.status = "failed"
        state.ended_at = datetime.now(timezone.utc).isoformat()
        raise HTTPException(status_code=503, detail="Task service is not configured")

    agent_url = f"{settings.supabase_url.rstrip('/')}/rest/v1/agent_versions"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }

    # Get latest published version for each agent
    for agent_id in state.agent_ids:
        params = {
            "agent_id": f"eq.{agent_id}",
            "organization_id": f"eq.{org_id}",
            "select": "id,version_number",
            "order": "version_number.desc",
            "limit": "1",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                response = await client.get(agent_url, headers=headers, params=params)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
            state.status = "failed"
            state.ended_at = datetime.now(timezone.utc).isoformat()
            raise HTTPException(status_code=503, detail="Agent service unavailable") from None

        if response.status_code != 200:
            state.status = "failed"
            state.ended_at = datetime.now(timezone.utc).isoformat()
            raise HTTPException(status_code=503, detail="Agent service unavailable")

        try:
            rows = response.json()
        except (ValueError, httpx.DecodingError):
            state.status = "failed"
            state.ended_at = datetime.now(timezone.utc).isoformat()
            raise HTTPException(status_code=503, detail="Agent service unavailable") from None

        if not isinstance(rows, list) or not rows:
            state.status = "failed"
            state.ended_at = datetime.now(timezone.utc).isoformat()
            raise HTTPException(status_code=404, detail=f"No published version found for agent {agent_id}")

        version_id = UUID(rows[0]["id"])
        version_number = rows[0]["version_number"]

        # Create run for this agent
        from app.runtime import StartRunRequest
        run_request = StartRunRequest(
            agent_version_id=version_id,
            input=state.input_data,
            conversation_id=None,
        )

        try:
            run_response = await create_agent_run(org_id, agent_id, run_request, user_id, access_token)
            state.sub_run_ids[agent_id] = run_response.run_id
            state.progress[str(agent_id)] = {
                "version_number": version_number,
                "run_id": str(run_response.run_id),
                "status": "queued",
            }
        except Exception as e:
            logger.error(f"Failed to create run for agent {agent_id}: {e}")
            state.status = "failed"
            state.ended_at = datetime.now(timezone.utc).isoformat()
            raise HTTPException(status_code=503, detail=f"Failed to start agent {agent_id}") from None

    # Start monitoring task in background
    asyncio.create_task(_monitor_task_completion(state, access_token))

    return TaskStateResponse(
        id=state.task_id,
        status=state.status,
        progress=state.progress,
        results=state.results,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


async def _monitor_task_completion(state: TaskState, access_token: str):
    """Monitor sub-run completion and update task state."""
    from app.runtime import _runs as runtime_runs

    while state.status == "running":
        await asyncio.sleep(1.0)

        all_completed = True
        any_failed = False

        for agent_id, run_id in state.sub_run_ids.items():
            run_state = runtime_runs.get(run_id)
            if not run_state:
                continue

            if run_state.status in ("completed", "failed"):
                state.progress[str(agent_id)]["status"] = run_state.status
                if run_state.status == "completed":
                    state.results[str(agent_id)] = {
                        "output": "completed",  # In production, fetch actual output
                    }
                else:
                    any_failed = True
            else:
                all_completed = False

        if any_failed:
            state.status = "failed"
            state.ended_at = datetime.now(timezone.utc).isoformat()
            break

        if all_completed and state.sub_run_ids:
            state.status = "completed"
            state.ended_at = datetime.now(timezone.utc).isoformat()
            break