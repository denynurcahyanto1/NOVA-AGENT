# NOVA AGENT — System Architecture

## Status and scope

**Phase 1 design proposal — not implemented.** The repository currently contains only tracking documents and empty `frontend/`, `backend/`, and `infra/` directories. No services, database, authentication, AI runtime, or deployment described below exists yet.

This document proposes a modular SaaS architecture aligned with the master directive. Major service, billing, and deployment choices remain approval-gated before Phase 2 implementation.

## Architecture decisions proposed

| Area | Proposal | Rationale |
| --- | --- | --- |
| Application shape | Modular monolith first: Next.js frontend, FastAPI API/runtime, PostgreSQL/Supabase, Redis worker support | Keeps Phase 2–5 operationally simple while preserving clear module boundaries for later extraction. |
| Identity | Supabase Auth issues user sessions; FastAPI verifies Supabase JWTs | Managed authentication with backend-enforced authorization. |
| Tenancy | Organizations are the tenant boundary; all organization-owned data carries `organization_id` | Enables consistent authorization, RLS, quotas, billing, and audit scope. |
| Streaming | Server-Sent Events (SSE) for agent and workflow execution events | Matches the directive and is suitable for one-way server-to-client progress streams. |
| Background work | Redis-backed worker queue for runs, schedules, retries, and indexing | Keeps long-running work out of request handlers. |
| Credentials | Envelope-encrypted provider credentials, decrypted only within the runtime worker | Prevents plaintext persistence and frontend exposure. |

## Proposed component boundaries

```text
Browser
  │ HTTPS + Supabase session
  ▼
Next.js App Router (frontend)
  │ REST / SSE, no provider secrets
  ▼
FastAPI application
  ├─ AuthN/AuthZ + tenant context
  ├─ Organization/RBAC module
  ├─ Agent + immutable version module
  ├─ Provider credential module
  ├─ Runtime/orchestration module
  ├─ Workflow module
  ├─ Memory/knowledge module
  ├─ Usage, quota, audit, analytics module
  └─ SSE event gateway
  │                 │
  ▼                 ▼
PostgreSQL/Supabase  Redis queue/cache
  │                 │
  ▼                 ▼
Supabase Auth/RLS   Worker processes
                         │
                         ▼
                    LLM providers and approved tools
```

### Frontend (proposed)

- Next.js App Router, React, TypeScript, Tailwind, shadcn/ui, and Zod.
- Uses Supabase Auth client support for session lifecycle.
- Calls only authenticated FastAPI APIs. It never receives provider credentials, encryption keys, or internal tool tokens.
- Displays SSE execution updates with reconnect and terminal-state reconciliation through `GET` run endpoints.

### API and runtime (proposed)

- A FastAPI modular monolith owns business rules, authorization, request validation, and OpenAPI documentation.
- SQLAlchemy Async accesses PostgreSQL under an explicit per-request transaction boundary.
- A runtime worker executes published agent/workflow versions only. It writes append-oriented events, usage, and final run status.
- Tool calls use an allowlist, validated inputs, constrained network policy, timeout, output limits, and audit events. Arbitrary user-provided URLs are not fetched without SSRF controls.

### Data and infrastructure (proposed)

- PostgreSQL is the system of record; Supabase provides Auth and database/RLS capabilities.
- Redis supports queueing, ephemeral run coordination, idempotency locks, rate limiting, and cache—not durable business state.
- Docker Compose supports local development. Production topology, managed Redis choice, Nginx, backups, monitoring, and CI/CD are deferred to Phase 10.

## Tenant isolation model

1. A request authenticates a Supabase subject and resolves an active organization membership.
2. The FastAPI authorization layer verifies the requested organization and permission before every action.
3. Backend database transactions set a trusted tenant context for RLS (for example, a transaction-local `app.organization_id`), never a client-supplied header alone.
4. Every organization-owned table has a non-null `organization_id`, index coverage, foreign keys, and RLS policy tied to validated membership/context.
5. Global/operator-only data is separated from tenant data. Cross-tenant administration requires an explicit platform-admin path and is audited.
6. Background workers must load the owning organization from the persisted run and establish the same tenant context before accessing data.

## Versioning and execution invariants

- An agent has an editable draft configuration and immutable `agent_versions` snapshots.
- Publishing creates a new immutable version in one transaction; published snapshot fields cannot be updated or deleted through normal application paths.
- `agent_runs.agent_version_id` is required and never changes. A run never executes a mutable draft.
- Rollback copies a selected, organization-owned historical version into the draft. It does not alter history or silently publish.
- Workflow versions follow the same snapshot pattern; workflow runs reference an exact workflow version.
- Version numbers are unique per parent resource and allocated under a row lock or equivalent transaction-safe mechanism.

## Reliability and observability (proposed)

- Each run has a state machine: `queued → running → succeeded|failed|cancelled|timed_out`; only valid transitions are accepted.
- Mutating API requests support idempotency keys where retries could duplicate effects (publish, run start, membership invitation, webhook dispatch).
- Workers use retries with bounded attempts, exponential backoff, and a durable failure record. Provider/tool calls receive explicit timeout and cancellation handling.
- Structured logs include correlation IDs, organization ID where permitted, actor ID, and resource IDs; they exclude prompts where retention disallows them and always exclude secrets.
- Metrics cover latency, queue depth, run outcomes, token/cost usage, provider errors, and tenant quota events.

## Security requirements

- Enforce server-side RBAC and database RLS; frontend checks are usability only.
- Encrypt provider credentials at rest; redact secrets from logs, errors, audit details, API responses, analytics, and client state.
- Validate all request bodies with Pydantic/Zod at the appropriate boundary; apply authorization before resource lookup where possible.
- Use audit logs for security-sensitive actions: membership/role changes, credential lifecycle, publish/rollback, agent execution, workflow approval, and subscription/quota changes.
- Apply rate limits by actor, organization, IP where appropriate, and endpoint risk.

## Existing implementation

None of the components in this document are implemented or verified in this repository. The only confirmed repository content is Phase 0 tracking documentation and empty top-level module directories.

## Approval-gated decisions

- Managed queue/worker technology and deployment topology.
- Encryption key management provider and rotation process.
- Billing provider, pricing model, and invoice/tax scope.
- Knowledge storage/vector search provider and retention policy.
- Whether external tools run in an isolated sandbox from the first production release.
