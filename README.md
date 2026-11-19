# NOVA AGENT

Universal AI agent SaaS platform. This repository is a new, clean starting point; no code was copied from the unrelated NVA Studio or Project-UMKM projects.

## Current state

Phase 0 discovery is complete for the available workspace. The actual NOVA AGENT codebase was not present, so this repository begins with project scope and phased implementation tracking only. No production features are implemented yet.

## Target stack

- Frontend: Next.js App Router, React, TypeScript, Tailwind CSS, shadcn/ui, Zod
- API: Python, FastAPI, Pydantic, SQLAlchemy Async
- Data/auth: PostgreSQL, Supabase Auth, Row Level Security
- Runtime: Redis, server-sent events, Docker

## Development status

See `docs/CURRENT_STATUS.md`, `docs/IMPLEMENTATION_PLAN.md`, and `docs/BLOCKERS.md` before starting implementation. Keep secrets in local environment files; never commit them.
