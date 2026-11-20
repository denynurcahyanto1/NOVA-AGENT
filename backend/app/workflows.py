import asyncio
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import CamelModel, get_settings

logger = logging.getLogger("nova.workflows")


# ========== Request/Response Models ==========

class CreateWorkflowRequest(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    graph: Dict[str, Any] = Field(default_factory=dict, description="Draft graph with nodes and edges")


class UpdateWorkflowRequest(CamelModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    graph: Optional[Dict[str, Any]] = Field(default=None)


class WorkflowResponse(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    organization_id: UUID = Field(alias="organizationId")
    name: str
    description: Optional[str] = None
    graph: Dict[str, Any]
    draft_revision: int = Field(alias="draftRevision")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class WorkflowVersionInfo(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    version_number: int = Field(alias="versionNumber")
    published_at: str = Field(alias="publishedAt")
    published_by: UUID = Field(alias="publishedBy")


class PublishWorkflowResponse(CamelModel):
    version: WorkflowVersionInfo


class RollbackWorkflowRequest(CamelModel):
    source_version_id: UUID = Field(alias="sourceVersionId")


class StartWorkflowRunRequest(CamelModel):
    workflow_version_id: UUID = Field(alias="workflowVersionId")
    input: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, alias="idempotencyKey")


class StartWorkflowRunResponse(CamelModel):
    run_id: UUID = Field(alias="runId")
    status: str
    events_url: str = Field(alias="eventsUrl")


class WorkflowRunResponse(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    organization_id: UUID = Field(alias="organizationId")
    workflow_id: UUID = Field(alias="workflowId")
    workflow_version_id: UUID = Field(alias="workflowVersionId")
    status: str
    input: Dict[str, Any]
    output: Optional[Dict[str, Any]] = None
    current_node_id: Optional[str] = Field(default=None, alias="currentNodeId")
    error: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = Field(default=None, alias="startedAt")
    ended_at: Optional[str] = Field(default=None, alias="endedAt")
    created_at: str = Field(alias="createdAt")


class WorkflowRunEvent(BaseModel):
    event_type: str
    payload: Dict[str, Any]
    sequence: int
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ========== Internal State ==========

@dataclass
class WorkflowRunState:
    run_id: UUID
    org_id: UUID
    workflow_id: UUID
    workflow_version_id: UUID
    input_data: Dict[str, Any]
    user_id: UUID
    status: str = "queued"
    context: Dict[str, Any] = field(default_factory=dict)
    node_states: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    current_node_id: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    sequence: int = 0
    events: List[WorkflowRunEvent] = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


_workflow_runs: Dict[UUID, WorkflowRunState] = {}


# ========== DAG Validation & Execution ==========

def validate_dag(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[str]:
    """Validate DAG and return topological order. Raises ValueError if cycle detected."""
    node_ids = {n["id"] for n in nodes}
    if not node_ids:
        return []

    # Build adjacency list and in-degree
    adj = defaultdict(list)
    in_degree = defaultdict(int)

    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_ids or target not in node_ids:
            raise ValueError(f"Edge references non-existent node: {source} -> {target}")
        adj[source].append(target)
        in_degree[target] += 1

    # Kahn's algorithm
    queue = deque([n for n in node_ids if in_degree[n] == 0])
    topo_order = []

    while queue:
        node = queue.popleft()
        topo_order.append(node)
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(topo_order) != len(node_ids):
        raise ValueError("Cycle detected in workflow graph")

    return topo_order


async def _get_workflow_version(
    workflow_version_id: UUID, access_token: str, org_id: UUID
) -> Dict[str, Any]:
    """Fetch workflow version snapshot with nodes and edges."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Workflow service is not configured")

    base_url = settings.supabase_url.rstrip("/")
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        # Get version snapshot
        version_resp = await client.get(
            f"{base_url}/rest/v1/workflow_versions",
            headers=headers,
            params={
                "id": f"eq.{workflow_version_id}",
                "organization_id": f"eq.{org_id}",
                "select": "id,workflow_id,snapshot,version_number",
                "limit": "1",
            },
        )
        if version_resp.status_code != 200:
            raise HTTPException(status_code=503, detail="Workflow service unavailable")
        try:
            version_rows = version_resp.json()
        except (ValueError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Workflow service unavailable")
        if not isinstance(version_rows, list) or not version_rows:
            raise HTTPException(status_code=404, detail="Workflow version not found")
        version_data = version_rows[0]

        # Get nodes
        nodes_resp = await client.get(
            f"{base_url}/rest/v1/workflow_nodes",
            headers=headers,
            params={
                "workflow_version_id": f"eq.{workflow_version_id}",
                "organization_id": f"eq.{org_id}",
                "select": "node_id,node_type,config,position",
            },
        )
        if nodes_resp.status_code != 200:
            raise HTTPException(status_code=503, detail="Workflow service unavailable")
        try:
            nodes = nodes_resp.json()
        except (ValueError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Workflow service unavailable")

        # Get edges
        edges_resp = await client.get(
            f"{base_url}/rest/v1/workflow_edges",
            headers=headers,
            params={
                "workflow_version_id": f"eq.{workflow_version_id}",
                "organization_id": f"eq.{org_id}",
                "select": "source_node_id,target_node_id,condition",
            },
        )
        if edges_resp.status_code != 200:
            raise HTTPException(status_code=503, detail="Workflow service unavailable")
        try:
            edges = edges_resp.json()
        except (ValueError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Workflow service unavailable")

    return {
        "version": version_data,
        "nodes": nodes,
        "edges": edges,
    }


async def _create_workflow_run(
    org_id: UUID,
    workflow_id: UUID,
    workflow_version_id: UUID,
    user_id: UUID,
    input_data: Dict[str, Any],
    idempotency_key: Optional[str],
    access_token: str,
) -> StartWorkflowRunResponse:
    """Create workflow run record in Supabase."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Workflow service is not configured")

    run_id = UUID(int=0)  # Will be generated by DB
    import uuid
    run_id = uuid.uuid4()

    events_url = f"/api/v1/organizations/{org_id}/workflows/{workflow_id}/runs/{run_id}/events"

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/workflow_runs"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Prefer": "return=representation",
    }
    payload = {
        "id": str(run_id),
        "organization_id": str(org_id),
        "workflow_id": str(workflow_id),
        "workflow_version_id": str(workflow_version_id),
        "requested_by": str(user_id),
        "status": "queued",
        "input": input_data,
        "context": {},
        "node_states": {},
        "idempotency_key": idempotency_key,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code == 409:
        raise HTTPException(status_code=409, detail="Idempotency key already used")
    if response.status_code != 201:
        raise HTTPException(status_code=503, detail="Workflow service unavailable")

    state = WorkflowRunState(
        run_id=run_id,
        org_id=org_id,
        workflow_id=workflow_id,
        workflow_version_id=workflow_version_id,
        input_data=input_data,
        user_id=user_id,
    )
    _workflow_runs[run_id] = state

    # Start background execution
    state.task = asyncio.create_task(_execute_workflow_run(state, access_token))

    return StartWorkflowRunResponse(run_id=run_id, status="queued", events_url=events_url)


async def _emit_event(state: WorkflowRunState, event_type: str, payload: Dict[str, Any]):
    state.sequence += 1
    event = WorkflowRunEvent(
        event_type=event_type, payload=payload, sequence=state.sequence
    )
    state.events.append(event)
    logger.info(f"Workflow run {state.run_id}: {event_type} seq={state.sequence}")


async def _execute_workflow_run(state: WorkflowRunState, access_token: str):
    """Background task to execute workflow DAG."""
    try:
        await _emit_event(state, "run.status", {"status": "running"})
        state.status = "running"
        state.started_at = datetime.now(timezone.utc).isoformat()

        # Fetch version with nodes/edges
        version_data = await _get_workflow_version(
            state.workflow_version_id, access_token, state.org_id
        )

        version = version_data["version"]
        nodes = version_data["nodes"]
        edges = version_data["edges"]

        # Build node map
        node_map = {n["node_id"]: n for n in nodes}
        node_ids = list(node_map.keys())

        # Validate DAG
        try:
            topo_order = validate_dag(
                [{"id": n["node_id"]} for n in nodes],
                [{"source": e["source_node_id"], "target": e["target_node_id"]} for e in edges],
            )
        except ValueError as e:
            await _emit_event(state, "run.failed", {"error": str(e)})
            state.status = "failed"
            state.error = {"message": str(e)}
            state.ended_at = datetime.now(timezone.utc).isoformat()
            return

        # Build adjacency for execution
        adj = defaultdict(list)
        edge_conditions = {}
        for edge in edges:
            adj[edge["source_node_id"]].append(edge["target_node_id"])
            if edge.get("condition"):
                edge_conditions[(edge["source_node_id"], edge["target_node_id"])] = edge["condition"]

        # Initialize node states
        for node_id in node_ids:
            state.node_states[node_id] = {"status": "pending", "output": None}

        await _emit_event(state, "run.status", {"status": "running", "total_nodes": len(node_ids)})

        # Execute nodes in topological order
        for node_id in topo_order:
            node = node_map[node_id]
            node_type = node["node_type"]
            config = node.get("config", {})

            state.current_node_id = node_id
            state.node_states[node_id] = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()}

            await _emit_event(state, "node.started", {"node_id": node_id, "node_type": node_type})

            try:
                if node_type == "agent":
                    # Execute agent node
                    output = await _execute_agent_node(state, node_id, config, access_token)
                elif node_type == "tool":
                    # Execute tool node
                    output = await _execute_tool_node(state, node_id, config, access_token)
                elif node_type == "condition":
                    # Evaluate condition
                    output = await _evaluate_condition(state, node_id, config)
                elif node_type in ("start", "end"):
                    output = {"status": "passed"}
                elif node_type == "parallel":
                    # Fork - handled by DAG structure
                    output = {"status": "forked"}
                elif node_type == "join":
                    # Join - wait for all predecessors
                    output = {"status": "joined"}
                else:
                    output = {"status": "completed"}

                state.node_states[node_id] = {
                    "status": "completed",
                    "output": output,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                }
                await _emit_event(state, "node.completed", {"node_id": node_id, "output": output})

            except Exception as e:
                logger.exception(f"Node {node_id} failed: {e}")
                state.node_states[node_id] = {
                    "status": "failed",
                    "error": str(e),
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                }
                await _emit_event(state, "node.failed", {"node_id": node_id, "error": str(e)})
                raise

        # All nodes completed
        await _emit_event(state, "run.completed", {"output": "Workflow completed successfully"})
        state.status = "completed"
        state.ended_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        await _emit_event(state, "run.failed", {"error": str(e)})
        state.status = "failed"
        state.error = {"message": str(e)}
        state.ended_at = datetime.now(timezone.utc).isoformat()


async def _execute_agent_node(
    state: WorkflowRunState, node_id: str, config: Dict[str, Any], access_token: str
) -> Dict[str, Any]:
    """Execute an agent node."""
    from app.runtime import create_agent_run, StartRunRequest

    agent_id = config.get("agent_id")
    agent_version_id = config.get("agent_version_id")
    prompt_template = config.get("prompt_template", "{{ input }}")

    if not agent_id or not agent_version_id:
        raise ValueError(f"Agent node {node_id} missing agent_id or agent_version_id")

    # Build input from template
    prompt = prompt_template.replace("{{ input }}", json.dumps(state.input_data))
    prompt = prompt.replace("{{ context }}", json.dumps(state.context))

    run_request = StartRunRequest(
        agent_version_id=UUID(agent_version_id),
        input={"message": prompt},
        conversation_id=None,
    )

    run_resp = await create_agent_run(
        state.org_id, UUID(agent_id), run_request, state.user_id, access_token
    )
    # In production, wait for completion and return output
    return {"run_id": str(run_resp.run_id), "status": "started"}


async def _execute_tool_node(
    state: WorkflowRunState, node_id: str, config: Dict[str, Any], access_token: str
) -> Dict[str, Any]:
    """Execute a tool node."""
    # Placeholder for tool execution
    return {"status": "completed", "output": "tool executed"}


async def _evaluate_condition(
    state: WorkflowRunState, node_id: str, config: Dict[str, Any]
) -> Dict[str, Any]:
    """Evaluate condition node."""
    expression = config.get("expression", "true")
    # Simple eval - in production use a safe expression evaluator
    result = bool(eval(expression, {"input": state.input_data, "context": state.context}))
    return {"result": result}


async def _persist_workflow_run(state: WorkflowRunState):
    """Persist workflow run state to Supabase."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        return

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/workflow_runs"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {state.input_data.get('access_token', '')}",
    }
    payload = {
        "status": state.status,
        "context": state.context,
        "node_states": state.node_states,
        "current_node_id": state.current_node_id,
        "error": state.error,
        "ended_at": state.ended_at,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        await client.patch(
            f"{url}?id=eq.{state.run_id}",
            headers=headers,
            json=payload,
        )


async def create_workflow(
    org_id: UUID,
    request: CreateWorkflowRequest,
    access_token: str,
) -> WorkflowResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Workflow service is not configured")

    # Validate graph structure
    if request.graph:
        nodes = request.graph.get("nodes", [])
        edges = request.graph.get("edges", [])
        if nodes or edges:
            try:
                validate_dag(
                    [{"id": n["id"]} for n in nodes],
                    [{"source": e["source"], "target": e["target"]} for e in edges],
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/workflows"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Prefer": "return=representation",
    }
    payload = {
        "organization_id": str(org_id),
        "name": request.name,
        "description": request.description,
        "graph": request.graph,
        "draft_revision": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.post(url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable") from None

    if response.status_code == 201:
        try:
            rows = response.json()
            if isinstance(rows, list) and rows:
                return WorkflowResponse.model_validate(rows[0])
        except (ValueError, TypeError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Workflow service unavailable") from None
        raise HTTPException(status_code=503, detail="Workflow service unavailable")

    if response.status_code in (401, 403, 404):
        raise HTTPException(status_code=404, detail="Organization not found")

    raise HTTPException(status_code=503, detail="Workflow service unavailable")


async def get_workflow(
    org_id: UUID,
    workflow_id: UUID,
    access_token: str,
) -> WorkflowResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Workflow service is not configured")

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/workflows"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }
    params = {
        "id": f"eq.{workflow_id}",
        "organization_id": f"eq.{org_id}",
        "select": "id,organization_id,name,description,graph,draft_revision,created_at,updated_at",
        "limit": "1",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(url, headers=headers, params=params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable") from None

    if response.status_code == 200:
        try:
            rows = response.json()
            if isinstance(rows, list) and rows:
                return WorkflowResponse.model_validate(rows[0])
        except (ValueError, TypeError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Workflow service unavailable") from None
        raise HTTPException(status_code=404, detail="Workflow not found")

    if response.status_code in (401, 403):
        raise HTTPException(status_code=404, detail="Workflow not found")

    raise HTTPException(status_code=503, detail="Workflow service unavailable")


async def update_workflow(
    org_id: UUID,
    workflow_id: UUID,
    request: UpdateWorkflowRequest,
    access_token: str,
) -> WorkflowResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Workflow service is not configured")

    # Validate graph if provided
    if request.graph:
        nodes = request.graph.get("nodes", [])
        edges = request.graph.get("edges", [])
        if nodes or edges:
            validate_dag(
                [{"id": n["id"]} for n in nodes],
                [{"source": e["source"], "target": e["target"]} for e in edges],
            )

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/workflows"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Prefer": "return=representation",
    }
    params = {
        "id": f"eq.{workflow_id}",
        "organization_id": f"eq.{org_id}",
    }
    payload = {"draft_revision": {"increment": 1}}
    if request.name is not None:
        payload["name"] = request.name
    if request.description is not None:
        payload["description"] = request.description
    if request.graph is not None:
        payload["graph"] = request.graph

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.patch(url, headers=headers, params=params, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable") from None

    if response.status_code == 200:
        try:
            rows = response.json()
            if isinstance(rows, list) and rows:
                return WorkflowResponse.model_validate(rows[0])
        except (ValueError, TypeError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Workflow service unavailable") from None
        raise HTTPException(status_code=404, detail="Workflow not found")

    if response.status_code in (401, 403, 404):
        raise HTTPException(status_code=404, detail="Workflow not found")

    raise HTTPException(status_code=503, detail="Workflow service unavailable")


async def publish_workflow_version(
    org_id: UUID,
    workflow_id: UUID,
    user_id: UUID,
    access_token: str,
) -> PublishWorkflowResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Workflow service is not configured")

    base_url = settings.supabase_url.rstrip("/")
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }

    # Get current draft directly; _get_workflow_version expects a version UUID,\n    # not a synthetic workflow:<uuid> identifier.\n    workflow_url = f"{base_url}/rest/v1/workflows"
    workflow_resp = await httpx.AsyncClient(timeout=httpx.Timeout(5.0)).get(
        workflow_url, headers=headers,
        params={"id": f"eq.{workflow_id}", "organization_id": f"eq.{org_id}", "select": "graph,draft_revision", "limit": "1"}
    )
    if workflow_resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Workflow service unavailable")
    try:
        workflow_rows = workflow_resp.json()
    except (ValueError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable")
    if not workflow_rows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    draft = workflow_rows[0]
    draft_revision = draft.get("draft_revision", 0)
    graph = draft.get("graph", {"nodes": [], "edges": []})

    # Validate DAG
    validate_dag(
        [{"id": n["id"]} for n in graph.get("nodes", [])],
        [{"source": e["source"], "target": e["target"]} for e in graph.get("edges", [])],
    )

    # Get next version number
    versions_url = f"{base_url}/rest/v1/workflow_versions"
    versions_resp = await httpx.AsyncClient(timeout=httpx.Timeout(5.0)).get(
        versions_url, headers=headers,
        params={"workflow_id": f"eq.{workflow_id}", "organization_id": f"eq.{org_id}", "select": "version_number", "order": "version_number.desc", "limit": "1"}
    )
    if versions_resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Workflow service unavailable")
    try:
        version_rows = versions_resp.json()
    except (ValueError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable")
    next_version = 1
    if version_rows:
        next_version = version_rows[0]["version_number"] + 1

    # Insert workflow version
    insert_payload = {
        "organization_id": str(org_id),
        "workflow_id": str(workflow_id),
        "version_number": next_version,
        "snapshot": graph,
        "published_by": str(user_id),
    }
    insert_headers = {**headers, "Prefer": "return=representation"}

    version_resp = await httpx.AsyncClient(timeout=httpx.Timeout(5.0)).post(
        versions_url, headers=insert_headers, json=insert_payload
    )
    if version_resp.status_code != 201:
        raise HTTPException(status_code=503, detail="Workflow service unavailable")

    try:
        inserted = version_resp.json()
        if inserted:
            row = inserted[0]
            return PublishWorkflowResponse(
                version=WorkflowVersionInfo.model_validate(row)
            )
    except (ValueError, TypeError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable")

    raise HTTPException(status_code=503, detail="Workflow service unavailable")


async def rollback_workflow(
    org_id: UUID,
    workflow_id: UUID,
    request: RollbackWorkflowRequest,
    user_id: UUID,
    access_token: str,
) -> WorkflowResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Workflow service is not configured")

    base_url = settings.supabase_url.rstrip("/")
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }

    # Get source version
    version_url = f"{base_url}/rest/v1/workflow_versions"
    version_resp = await httpx.AsyncClient(timeout=httpx.Timeout(5.0)).get(
        version_url, headers=headers,
        params={"id": f"eq.{request.source_version_id}", "organization_id": f"eq.{org_id}", "workflow_id": f"eq.{workflow_id}", "select": "snapshot", "limit": "1"}
    )
    if version_resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Workflow service unavailable")
    try:
        version_rows = version_resp.json()
    except (ValueError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable")
    if not version_rows:
        raise HTTPException(status_code=404, detail="Source version not found")

    snapshot = version_rows[0]["snapshot"]

    # Update workflow draft
    workflow_url = f"{base_url}/rest/v1/workflows"
    update_payload = {
        "graph": snapshot,
        "draft_revision": {"increment": 1},
    }
    update_resp = await httpx.AsyncClient(timeout=httpx.Timeout(5.0)).patch(
        workflow_url, headers=headers,
        params={"id": f"eq.{workflow_id}", "organization_id": f"eq.{org_id}"},
        json=update_payload,
    )
    if update_resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Workflow service unavailable")

    try:
        updated = update_resp.json()
        if updated:
            return WorkflowResponse.model_validate(updated[0])
    except (ValueError, TypeError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Workflow service unavailable")

    raise HTTPException(status_code=503, detail="Workflow service unavailable")


async def execute_workflow_run(
    org_id: UUID,
    workflow_id: UUID,
    request: StartWorkflowRunRequest,
    user_id: UUID,
    access_token: str,
) -> StartWorkflowRunResponse:
    return await _create_workflow_run(
        org_id, workflow_id, request.workflow_version_id, user_id,
        request.input, request.idempotency_key, access_token
    )


async def stream_workflow_run_events(
    org_id: UUID,
    workflow_id: UUID,
    run_id: UUID,
    access_token: str,
    last_event_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """SSE generator for workflow run events."""
    state = _workflow_runs.get(run_id)
    if not state or state.org_id != org_id or state.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Workflow run not found")

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
    max_wait = 60
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


async def get_workflow_run_status(
    org_id: UUID,
    workflow_id: UUID,
    run_id: UUID,
    access_token: str,
) -> WorkflowRunResponse:
    state = _workflow_runs.get(run_id)
    if not state or state.org_id != org_id or state.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    return WorkflowRunResponse(
        id=state.run_id,
        organization_id=state.org_id,
        workflow_id=state.workflow_id,
        workflow_version_id=state.workflow_version_id,
        status=state.status,
        input=state.input_data,
        output=None,
        current_node_id=state.current_node_id,
        error=state.error,
        started_at=state.started_at,
        ended_at=state.ended_at,
        created_at=datetime.now(timezone.utc).isoformat(),
    )