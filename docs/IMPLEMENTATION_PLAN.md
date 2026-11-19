# NOVA AGENT — Implementation Plan

## Status

**Phase 1 complete; Phase 2 foundation in progress.** This plan is proposed sequencing, not evidence of delivered product functionality. The repository contains minimal backend and frontend scaffolds, but no authentication, migrations, tenant enforcement, or production integrations.

## Guiding constraints

- Preserve the target stack: Next.js/TypeScript/Tailwind/shadcn/ui; FastAPI/Pydantic/SQLAlchemy Async; PostgreSQL/Supabase Auth/RLS; Redis; Docker; SSE.
- Implement multi-tenancy as a database and backend invariant from the first schema migration.
- Build only working vertical slices; no static dashboard feature is considered complete without its secure backend/API/test coverage.
- Do not deploy, create external production resources, migrate a real database, or select paid providers without approval.

## Phase 1 deliverables and acceptance criteria

| Deliverable | Acceptance criterion | Status |
| --- | --- | --- |
| `SYSTEM_ARCHITECTURE.md` | Component boundaries, tenancy, security, runtime, observability, and implementation/proposal distinction documented | Complete in this documentation task |
| `DATABASE_DESIGN.md` | Entities, RLS/tenant model, immutable versions, and transaction invariants documented | Complete in this documentation task |
| `API_SPECIFICATION.md` | Auth, organization-scoped API groups, failures, SSE, and security contracts documented | Complete in this documentation task |
| Approval record | Product owner confirms unresolved architecture decisions before major implementation | Pending |

## Implementation sequence

### Phase 2 — Foundation

**Owners:** Backend, Frontend, Security, DevOps; Architect reviews boundaries.

1. Create backend and frontend application skeletons plus local Docker Compose.
2. Configure typed environment loading, safe error handling, correlation IDs, structured logging, and secret redaction.
3. Integrate Supabase Auth session verification in FastAPI and the frontend.
4. Implement organizations, memberships, RBAC permission checks, tenant context, PostgreSQL migrations, and RLS policies.
5. Add baseline tests: auth, permission, RLS cross-tenant negatives, database migrations, frontend/backend connectivity.

**Exit criteria:** authenticated users can create/select an organization; unauthorized and cross-tenant access are rejected by backend and RLS tests; no provider credentials are stored or exposed.

### Phase 3 — Agent backend

**Owners:** Backend, Security, QA; Architect reviews version invariants.

1. Implement agent draft CRUD and immutable agent version publication/rollback.
2. Implement provider credential encryption abstraction, validation lifecycle, and audit logs.
3. Implement run records, idempotency, state transitions, usage capture contract, and SSE event persistence.
4. Add migration, API, concurrent-publish, rollback-ownership, credential-redaction, and cross-tenant tests.

**Exit criteria:** an authorized tenant can publish an agent safely, start a run referencing an exact immutable version, and inspect an audited run without receiving secrets.

### Phase 4 — Frontend

**Owners:** Frontend, QA.

1. Build authenticated dashboard shell, organization switcher, role-aware navigation, loading/error/empty states.
2. Build agent list, draft editor, version history, publish/rollback UI, provider settings, and execution history using real APIs.
3. Add client validation, accessibility checks, component tests, and end-to-end critical flows.

**Exit criteria:** each visible control uses implemented APIs and correctly presents authorization, errors, and streaming states.

### Phase 5 — AI engine

**Owners:** AI Engineer, Backend, Security, QA.

1. Implement provider adapter interface, capability routing, model validation, retry/timeout policy, and health checks.
2. Implement agent runtime with SSE, cancellation, token/cost recording, and safe tool-calling controls.
3. Implement conversation memory and knowledge ingestion/retrieval after data retention and vector-store decisions are approved.

**Exit criteria:** a real approved provider can execute a published agent with streaming, terminal state, usage, and failure recovery verified end to end. Mocks only support isolated tests.

### Phase 6 — Multi-agent orchestration

**Owners:** AI Engineer, Backend, QA, Security.

1. Define task lifecycle, persisted shared context, dependency graph, delegation permissions, aggregation, and retries.
2. Execute each subtask under the parent organization and version context.
3. Add observability and failure/recovery tests.

**Exit criteria:** a real multi-agent task executes dependencies, records results/audit events, and cannot cross tenant boundaries.

### Phase 7 — Workflow engine

**Owners:** Backend, AI Engineer, Frontend, QA.

1. Implement workflow drafts/immutable versions and validated node graph.
2. Add manual, schedule, and verified webhook triggers; agent/LLM/condition/approval/output nodes.
3. Persist recoverable execution state and show it through the frontend.

**Exit criteria:** a versioned workflow can run, pause for approval, resume safely, recover/retry as designed, and expose history.

### Phase 8 — QA and validation

**Owner:** QA; every specialist fixes findings in their owned area.

- Run unit, integration, API, database/RLS, frontend, end-to-end, regression, performance, and multi-agent tests.
- Record actual commands and results. Resolve critical and high-severity failures before advancing.

### Phase 9 — Security audit

**Owner:** Security.

- Audit authentication, RBAC/RLS, tenant isolation, secret handling, input validation, rate limiting, SSRF controls, webhooks, audit logs, dependencies, and configuration.
- Deliver evidence-backed `SECURITY_AUDIT.md`; do not perform destructive tests against production.

### Phase 10 — DevOps and deployment

**Owner:** DevOps.

- Finalize Docker, CI/CD, migrations, health checks, Nginx/HTTPS, Redis, logging, monitoring, backups, and recovery procedures.
- Deployment/DNS/production migration require explicit approval.

### Phases 11–12 — Documentation and release validation

**Owners:** Docs, QA, Project Manager.

- Produce implementation-accurate technical/user/admin/deployment documentation.
- Perform end-to-end acceptance against the directive and issue `FINAL_REPORT.md`, `RELEASE_CHECKLIST.md`, and `KNOWN_LIMITATIONS.md` only with verified evidence.

## Dependencies and ownership handoff

| Dependency | Needed before | Responsible review |
| --- | --- | --- |
| Product approval of tenancy/RBAC and service choices | Phase 2 schema/API work | Project Manager, Architect, Security |
| Encryption key-management decision | Provider credential persistence | Security, DevOps |
| Billing/subscription scope and provider | SaaS payment implementation | Project Manager, Backend, Security |
| Knowledge/vector storage and retention policy | Knowledge ingestion | Project Manager, AI Engineer, Security |
| Deployment target and domains | Phase 10 | Project Manager, DevOps |

## Risks and controls

| Risk | Control |
| --- | --- |
| Cross-tenant data access | Enforce organization scope in backend and RLS; test negative paths in every module. |
| Mutable configuration changes execution behavior | Runs bind immutable agent/workflow version IDs. |
| Concurrent publishing duplicates version numbers | Lock parent row and enforce per-parent unique version constraint. |
| Secret leakage | Encrypt credentials, redact logs/responses, prohibit frontend secret fields after save. |
| Long-running failures/duplicate work | Durable run states, idempotency, bounded retries, conditional transitions. |
| UI outpaces backend | Gate frontend features on real API contract and integration tests. |

## Unresolved decisions requiring approval

1. Initial role/permission matrix and organization invitation policy.
2. Credential key-management and rotation provider.
3. Queue/worker implementation and production Redis service.
4. Billing provider, plans, quotas, and tax/region scope.
5. Vector search/knowledge storage and retention/deletion requirements.
6. Production hosting topology, regions, data residency, observability vendor, and backup targets.
7. Tool execution isolation policy for the first production release.

## Existing implementation

No item after Phase 1 documentation is implemented or tested. No code, migration, deployment, or external resource was created by this task.
