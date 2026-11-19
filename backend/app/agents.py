from typing import Any, Dict, Optional
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import ConfigDict, Field

from app.config import CamelModel, get_settings


class CreateAgentRequest(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    draft_config: Dict[str, Any] = Field(default_factory=dict, alias="draftConfig")


class AgentResponse(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    organization_id: UUID = Field(alias="organizationId")
    name: str
    draft_config: Dict[str, Any] = Field(default_factory=dict, alias="draftConfig")
    draft_revision: int = Field(default=0, alias="draftRevision")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class PublishedVersionInfo(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    version_number: int = Field(alias="versionNumber")
    published_at: str = Field(alias="publishedAt")


class PublishAgentResponse(CamelModel):
    version: PublishedVersionInfo


async def create_agent(
    org_id: UUID,
    request: CreateAgentRequest,
    access_token: str,
) -> AgentResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Agent service is not configured")

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/agents"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Prefer": "return=representation",
    }
    payload = {
        "organization_id": str(org_id),
        "name": request.name,
        "draft_config": request.draft_config,
        "draft_revision": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.post(url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    if response.status_code == 201:
        try:
            rows = response.json()
            if isinstance(rows, list) and rows:
                return AgentResponse.model_validate(rows[0])
        except (ValueError, TypeError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Agent service unavailable") from None
        raise HTTPException(status_code=503, detail="Agent service unavailable")

    if response.status_code in (401, 403, 404):
        # Non-enumerating 404 if org does not exist or user lacks permission
        raise HTTPException(status_code=404, detail="Organization not found")

    raise HTTPException(status_code=503, detail="Agent service unavailable")


async def publish_agent_version(
    org_id: UUID,
    agent_id: UUID,
    user_id: UUID,
    access_token: str,
) -> PublishAgentResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Agent service is not configured")

    base_url = settings.supabase_url.rstrip("/")
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }

    # Step 1: Query the agent draft, ensuring org_id match
    agent_url = f"{base_url}/rest/v1/agents"
    agent_params = {
        "id": f"eq.{agent_id}",
        "organization_id": f"eq.{org_id}",
        "select": "id,organization_id,name,draft_config,draft_revision",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            agent_resp = await client.get(agent_url, headers=headers, params=agent_params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    if agent_resp.status_code in (401, 403, 404):
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent_resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Agent service unavailable")

    try:
        agent_rows = agent_resp.json()
    except (ValueError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    if not isinstance(agent_rows, list) or not agent_rows:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent_data = agent_rows[0]
    draft_config = agent_data.get("draft_config") or {}

    # Step 2: Determine next version number by reading existing versions
    versions_url = f"{base_url}/rest/v1/agent_versions"
    versions_params = {
        "agent_id": f"eq.{agent_id}",
        "organization_id": f"eq.{org_id}",
        "select": "version_number",
        "order": "version_number.desc",
        "limit": "1",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            versions_resp = await client.get(versions_url, headers=headers, params=versions_params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    if versions_resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Agent service unavailable")

    try:
        version_rows = versions_resp.json()
    except (ValueError, httpx.DecodingError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    next_version = 1
    if isinstance(version_rows, list) and version_rows:
        next_version = int(version_rows[0]["version_number"]) + 1

    # Step 3: Insert immutable snapshot into agent_versions
    insert_headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Prefer": "return=representation",
    }
    insert_payload = {
        "organization_id": str(org_id),
        "agent_id": str(agent_id),
        "version_number": next_version,
        "snapshot": draft_config,
        "published_by": str(user_id),
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            insert_resp = await client.post(versions_url, headers=insert_headers, json=insert_payload)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None

    if insert_resp.status_code == 201:
        try:
            inserted_rows = insert_resp.json()
            if isinstance(inserted_rows, list) and inserted_rows:
                row = inserted_rows[0]
                return PublishAgentResponse(
                    version=PublishedVersionInfo.model_validate(row)
                )
        except (ValueError, TypeError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Agent service unavailable") from None
        raise HTTPException(status_code=503, detail="Agent service unavailable")

    if insert_resp.status_code in (401, 403, 404):
        raise HTTPException(status_code=404, detail="Agent not found")
    if insert_resp.status_code == 409:
        raise HTTPException(status_code=409, detail="Version conflict during publication")

    raise HTTPException(status_code=503, detail="Agent service unavailable")
