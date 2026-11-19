from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import CamelModel, get_settings


class OrganizationResponse(CamelModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    name: str
    slug: str
    created_at: str = Field(alias="createdAt")


class CreateOrganizationRequest(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r'^[a-z0-9]+([_-][a-z0-9]+)*$')


async def read_organization(organization_id: UUID, access_token: str) -> OrganizationResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Organization access is not configured")

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/organizations"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }
    params = {"id": f"eq.{organization_id}", "select": "id,name,slug,created_at"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(url, headers=headers, params=params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Organization service unavailable") from None

    if response.status_code == 200:
        try:
            rows = response.json()
        except (ValueError, httpx.DecodingError):
            raise HTTPException(status_code=503, detail="Organization service unavailable") from None
        if isinstance(rows, list) and rows:
            try:
                return OrganizationResponse.model_validate(rows[0])
            except (ValueError, TypeError):
                raise HTTPException(status_code=503, detail="Organization service unavailable") from None
        raise HTTPException(status_code=404, detail="Organization not found")
    if response.status_code in (401, 403):
        raise HTTPException(status_code=404, detail="Organization not found")
    raise HTTPException(status_code=503, detail="Organization service unavailable")


async def create_organization(request: CreateOrganizationRequest, user_id: UUID, access_token: str) -> OrganizationResponse:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Organization access is not configured")

    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/organizations"
    headers = {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Prefer": "return=representation",
    }
    data = {
        "name": request.name,
        "slug": request.slug,
        "created_by": str(user_id),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.post(url, headers=headers, json=data)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="Organization service unavailable") from None

    if response.status_code == 201:
        try:
            rows = response.json()
            return OrganizationResponse.model_validate(rows[0])
        except (ValueError, TypeError):
            raise HTTPException(status_code=503, detail="Organization service unavailable") from None
    if response.status_code == 409:
        raise HTTPException(status_code=409, detail="Organization with this slug already exists")
    raise HTTPException(status_code=503, detail="Organization service unavailable")
