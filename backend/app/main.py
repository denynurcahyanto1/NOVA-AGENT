import logging
from typing import Optional
from uuid import UUID
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.auth import get_access_token, get_current_user
from app.organizations import (
    read_organization,
    create_organization,
    OrganizationResponse,
    CreateOrganizationRequest,
)
from app.agents import (
    create_agent,
    publish_agent_version,
    CreateAgentRequest,
    AgentResponse,
    PublishAgentResponse,
)
from app.runtime import (
    create_agent_run,
    stream_run_events,
    get_run_status,
    StartRunRequest,
    StartRunResponse,
    AgentRun,
    _runs,
)
from app.tasks import (
    create_task,
    get_task_state,
    execute_task,
    CreateTaskRequest,
    TaskResponse,
    TaskStateResponse,
)
from app import workflows
from app.workflows import (
    create_workflow,
    get_workflow,
    update_workflow,
    publish_workflow_version,
    rollback_workflow,
    execute_workflow_run,
    stream_workflow_run_events,
    get_workflow_run_status,
    CreateWorkflowRequest,
    UpdateWorkflowRequest,
    WorkflowResponse,
    PublishWorkflowResponse,
    RollbackWorkflowRequest,
    StartWorkflowRunRequest,
    StartWorkflowRunResponse,
    WorkflowRunResponse,
)

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("nova.api")


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name, version="0.1.0")

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")
        if not request_id or len(request_id) > 128 or any(ord(c) < 32 for c in request_id):
            request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed."},
                     "requestId": request_id},
            headers={"X-Request-ID": request_id},
        )

    from fastapi import HTTPException
    @application.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": exc.detail},
                     "requestId": request_id},
            headers={"X-Request-ID": request_id},
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", str(uuid4()))
        logger.error("Unhandled request error", extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
                     "requestId": request_id},
            headers={"X-Request-ID": request_id},
        )

    @application.get("/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    @application.get("/api/v1/me", tags=["identity"])
    async def me(claims=Depends(get_current_user)):
        return {"userId": claims["sub"]}

    @application.post(
        "/api/v1/organizations",
        tags=["organizations"],
        response_model=OrganizationResponse,
        status_code=201,
    )
    async def post_organization(
        request: CreateOrganizationRequest,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> OrganizationResponse:
        return await create_organization(request, UUID(claims["sub"]), access_token)

    @application.get(
        "/api/v1/organizations/{organization_id}",
        tags=["organizations"],
        response_model=OrganizationResponse,
    )
    async def get_organization(
        organization_id: UUID,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> OrganizationResponse:
        return await read_organization(organization_id, access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/agents",
        tags=["agents"],
        response_model=AgentResponse,
        status_code=201,
    )
    async def post_agent(
        organization_id: UUID,
        request: CreateAgentRequest,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> AgentResponse:
        return await create_agent(organization_id, request, access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/agents/{agent_id}/publish",
        tags=["agents"],
        response_model=PublishAgentResponse,
        status_code=201,
    )
    async def publish_agent(
        organization_id: UUID,
        agent_id: UUID,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> PublishAgentResponse:
        return await publish_agent_version(organization_id, agent_id, UUID(claims["sub"]), access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/agents/{agent_id}/runs",
        tags=["runs"],
        response_model=StartRunResponse,
        status_code=202,
    )
    async def post_agent_run(
        organization_id: UUID,
        agent_id: UUID,
        request: StartRunRequest,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> StartRunResponse:
        return await create_agent_run(organization_id, agent_id, request, UUID(claims["sub"]), access_token)

    @application.get(
        "/api/v1/organizations/{organization_id}/runs/{run_id}/events",
        tags=["runs"],
    )
    async def get_run_events(
        organization_id: UUID,
        run_id: UUID,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
        last_event_id: Optional[int] = None,
    ):
        from fastapi.responses import StreamingResponse
        # Verify run exists and belongs to org before starting stream
        state = _runs.get(run_id)
        if not state or state.org_id != organization_id:
            raise HTTPException(status_code=404, detail="Run not found")
        return StreamingResponse(
            stream_run_events(organization_id, run_id, access_token, last_event_id),
            media_type="text/event-stream",
        )

    @application.get(
        "/api/v1/organizations/{organization_id}/runs/{run_id}",
        tags=["runs"],
        response_model=AgentRun,
    )
    async def get_run(
        organization_id: UUID,
        run_id: UUID,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> AgentRun:
        return await get_run_status(organization_id, run_id, access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/tasks",
        tags=["tasks"],
        response_model=TaskResponse,
        status_code=201,
    )
    async def post_task(
        organization_id: UUID,
        request: CreateTaskRequest,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> TaskResponse:
        return await create_task(organization_id, request, UUID(claims["sub"]), access_token)

    @application.get(
        "/api/v1/organizations/{organization_id}/tasks/{task_id}",
        tags=["tasks"],
        response_model=TaskStateResponse,
    )
    async def get_task(
        organization_id: UUID,
        task_id: UUID,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> TaskStateResponse:
        return await get_task_state(organization_id, task_id, access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/tasks/{task_id}/execute",
        tags=["tasks"],
        response_model=TaskStateResponse,
        status_code=202,
    )
    async def execute_task_endpoint(
        organization_id: UUID,
        task_id: UUID,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> TaskStateResponse:
        return await execute_task(organization_id, task_id, UUID(claims["sub"]), access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/workflows",
        tags=["workflows"],
        response_model=WorkflowResponse,
        status_code=201,
    )
    async def post_workflow(
        organization_id: UUID,
        request: CreateWorkflowRequest,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> WorkflowResponse:
        return await create_workflow(organization_id, request, access_token)

    @application.get(
        "/api/v1/organizations/{organization_id}/workflows/{workflow_id}",
        tags=["workflows"],
        response_model=WorkflowResponse,
    )
    async def get_workflow_endpoint(
        organization_id: UUID,
        workflow_id: UUID,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> WorkflowResponse:
        return await get_workflow(organization_id, workflow_id, access_token)

    @application.patch(
        "/api/v1/organizations/{organization_id}/workflows/{workflow_id}",
        tags=["workflows"],
        response_model=WorkflowResponse,
    )
    async def patch_workflow(
        organization_id: UUID,
        workflow_id: UUID,
        request: UpdateWorkflowRequest,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> WorkflowResponse:
        return await update_workflow(organization_id, workflow_id, request, access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/workflows/{workflow_id}/publish",
        tags=["workflows"],
        response_model=PublishWorkflowResponse,
        status_code=201,
    )
    async def publish_workflow(
        organization_id: UUID,
        workflow_id: UUID,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> PublishWorkflowResponse:
        return await publish_workflow_version(organization_id, workflow_id, UUID(claims["sub"]), access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/workflows/{workflow_id}/rollback",
        tags=["workflows"],
        response_model=WorkflowResponse,
    )
    async def rollback_workflow_endpoint(
        organization_id: UUID,
        workflow_id: UUID,
        request: RollbackWorkflowRequest,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> WorkflowResponse:
        return await rollback_workflow(organization_id, workflow_id, request, UUID(claims["sub"]), access_token)

    @application.post(
        "/api/v1/organizations/{organization_id}/workflows/{workflow_id}/runs",
        tags=["workflows"],
        response_model=StartWorkflowRunResponse,
        status_code=202,
    )
    async def post_workflow_run(
        organization_id: UUID,
        workflow_id: UUID,
        request: StartWorkflowRunRequest,
        access_token: str = Depends(get_access_token),
        claims=Depends(get_current_user),
    ) -> StartWorkflowRunResponse:
        return await execute_workflow_run(organization_id, workflow_id, request, UUID(claims["sub"]), access_token)

    @application.get(
        "/api/v1/organizations/{organization_id}/workflows/{workflow_id}/runs/{run_id}/events",
        tags=["workflows"],
    )
    async def get_workflow_run_events(
        organization_id: UUID,
        workflow_id: UUID,
        run_id: UUID,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
        last_event_id: Optional[int] = None,
    ):
        from fastapi.responses import StreamingResponse
        state = workflows._workflow_runs.get(run_id)
        if not state or state.org_id != organization_id or state.workflow_id != workflow_id:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        return StreamingResponse(
            stream_workflow_run_events(organization_id, workflow_id, run_id, access_token, last_event_id),
            media_type="text/event-stream",
        )

    @application.get(
        "/api/v1/organizations/{organization_id}/workflows/{workflow_id}/runs/{run_id}",
        tags=["workflows"],
        response_model=WorkflowRunResponse,
    )
    async def get_workflow_run(
        organization_id: UUID,
        workflow_id: UUID,
        run_id: UUID,
        access_token: str = Depends(get_access_token),
        _claims=Depends(get_current_user),
    ) -> WorkflowRunResponse:
        return await get_workflow_run_status(organization_id, workflow_id, run_id, access_token)

    return application


app = create_app()
