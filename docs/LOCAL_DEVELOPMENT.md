# Local development

## Python backend

```powershell
cd backend
python -m venv venv
./venv/Scripts/pip install --upgrade pip
./venv/Scripts/pip install -e .[dev]
./venv/Scripts/pytest -v
```

The `pyproject.toml` defines `dev` extra with `pytest`, `pytest-asyncio`. All tests must pass locally before commit.

## Supabase

Install the Supabase CLI and Docker-compatible runtime, then run from the repository root:

```powershell
supabase start
supabase db reset
```

The local stack provides PostgreSQL and Supabase Auth. The initial migration creates organizations and memberships with RLS; it does not affect any hosted project. Keep generated local credentials out of commits. Stop the stack with `supabase stop`.

## Redis

Redis is not started yet. Add it when a worker consumer is implemented, with a local-only bind address and an explicit persistence/retention choice. Do not connect this scaffold to a production Redis service.

## Current migration scope

The first migration allows an authenticated user to create an organization and add only themselves as its initial owner. Organization reads require membership. Invitations, membership role changes, backend service credentials, and role-based API permissions are intentionally not implemented; these require a reviewed backend authorization path.
