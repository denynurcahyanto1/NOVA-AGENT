# NOVA AGENT — API Specification

## Status and scope

**Phase 1 API contract proposal; limited foundation implemented.** FastAPI now exposes `/health` and `/api/v1/me`. Identity verifies Supabase JWTs; tenant-scoped API paths, payloads, SSE, and permissions below remain proposals subject to implementation and security review.

## API conventions

- Base path: `/api/v1`.
- Authentication: bearer access token issued by Supabase Auth; FastAPI strictly enforces asymmetric JWT validation (ES256/RS256 via JWKS). Legacy symmetric HS256 and `none` algorithms are rejected. Validation mandates signature, issuer (`{SUPABASE_URL}/auth/v1`), audience (`authenticated`), expiration (`exp`), issued-at (`iat`), and subject (`sub`).
- Tenant selection: an explicit organization path parameter or active-organization claim/header validated against membership. A client header alone never grants tenant access.
- JSON uses `camelCase` at the HTTP boundary; internal model naming is an implementation decision.
- Mutating endpoints accept `Idempotency-Key` where duplicate retries could create a version, run, membership action, or external effect.
- Errors use `application/problem+json` with stable `code`, HTTP `status`, request/correlation ID, and safe detail. Secrets and provider responses are redacted.
- Pagination uses a cursor (`limit`, `cursor`) rather than offset for high-volume run/audit/event data.

## Authorization levels (proposed)

`owner` manages organization/billing; `admin` manages members and security-sensitive configuration; `builder` manages agents/workflows; `member` executes permitted resources; `viewer` reads permitted records. Exact permission matrix is unresolved and must be approved before Phase 2.

## Endpoint groups

| Group | Proposed endpoints | Authorization |
| --- | --- | --- |
| Identity | `GET /me`, `PATCH /me` | Authenticated user |
| Organizations | `GET/POST /organizations`, `GET/PATCH /organizations/{orgId}` | Membership; owner/admin for mutations |
| Memberships | `GET/POST /organizations/{orgId}/members`, `PATCH/DELETE .../members/{memberId}` | Owner/admin |
| Agents | `GET/POST /organizations/{orgId}/agents`, `GET/PATCH/DELETE /agents/{agentId}` | Builder or higher; read per role |
| Agent versions | `GET /agents/{agentId}/versions`, `POST /agents/{agentId}/publish`, `POST /agents/{agentId}/rollback` | Builder or higher |
| Agent runs | `POST /agents/{agentId}/runs`, `GET /runs/{runId}`, `POST /runs/{runId}/cancel`, `GET /runs/{runId}/events` | Execute/read permission on same tenant/resource |
| Providers | `GET/POST /organizations/{orgId}/provider-credentials`, `POST .../{credentialId}/validate`, `DELETE .../{credentialId}` | Admin or delegated credential permission |
| Workflows | CRUD, publish/rollback, run, run events under organization/workflow paths | Builder/execute permission |
| Memory/knowledge | Conversation, memory, document ingestion, retrieval-job endpoints | Tenant/resource-scoped permission |
| Usage/audit | `GET /organizations/{orgId}/usage`, `GET .../audit-logs` | Admin/owner; filtered by role |
| Webhooks | `POST /webhooks/{provider}` | Verified signature; no browser auth |

## Representative contracts

### Create agent

`POST /api/v1/organizations/{orgId}/agents`

Request:

```json
{
  "name": "Support triage",
  "description": "Classifies incoming support requests",
  "draftConfig": {
    "instructions": "...",
    "providerCredentialId": "uuid",
    "model": "provider-model-id",
    "toolIds": []
  }
}
```

Response `201` returns the mutable draft metadata. It excludes credential ciphertext, decrypted secret material, and internal encryption metadata.

### Publish agent

`POST /api/v1/organizations/{orgId}/agents/{agentId}/publish`

Headers: `Idempotency-Key: <uuid>`

Response `201`:

```json
{
  "version": {
    "id": "uuid",
    "versionNumber": 3,
    "publishedAt": "2026-11-19T00:00:00Z"
  }
}
```

Behavior: validates draft and organization ownership, then atomically creates the immutable version and audit record. A retry with the same idempotency key returns the original outcome. Published snapshots have no update endpoint.

### Roll back agent draft

`POST /api/v1/organizations/{orgId}/agents/{agentId}/rollback`

Request: `{ "sourceVersionId": "uuid" }`

Behavior: copies an organization-owned historical version to the draft, records audit data, and returns the new draft revision. It does not republish or alter historical versions/runs.

### Start agent run

`POST /api/v1/organizations/{orgId}/agents/{agentId}/runs`

Request:

```json
{
  "agentVersionId": "uuid",
  "input": { "message": "Summarize this ticket" },
  "conversationId": "uuid"
}
```

Response `202` returns `runId`, `status: "queued"`, and an authenticated events URL. The server rejects draft IDs, cross-tenant versions, unavailable credentials, or exceeded quota.

### Stream run events

`GET /api/v1/organizations/{orgId}/runs/{runId}/events`

Response type: `text/event-stream`. Proposed event types are `run.status`, `run.token`, `tool.started`, `tool.completed`, `run.usage`, `run.completed`, and `run.failed`. Event payloads must be tenant-authorized, sequence-numbered, resumable using `Last-Event-ID`, and secret-redacted. The terminal state is confirmed with `GET /runs/{runId}`.

## Response and failure semantics

| Case | Status | Example code |
| --- | --- | --- |
| Invalid body | `422` | `validation_error` |
| No session/invalid token | `401` | `authentication_required` |
| Missing role, cross-tenant, or hidden resource | `403` or deliberately non-enumerating `404` | `forbidden` |
| Version conflict/invalid state transition | `409` | `state_conflict` |
| Idempotency key reused with different request | `409` | `idempotency_conflict` |
| Quota/rate limit | `429` | `quota_exceeded` / `rate_limited` |
| Provider unavailable | `502` or `503` | `provider_unavailable` |

## Webhook and external integration requirements

- Each webhook verifies provider signature, timestamp/replay window, and expected event source before parsing into domain events.
- Webhook processing is idempotent using a provider event identifier stored with a uniqueness constraint.
- Tool/integration credentials are never returned from any endpoint, including admin/list endpoints.
- Outbound requests require provider allowlists, timeouts, payload limits, and SSRF defenses.

## API acceptance criteria for implementation

1. OpenAPI is generated from FastAPI/Pydantic and reviewed against this document.
2. Every tenant endpoint has authentication, permission, tenant-context, and cross-tenant negative tests.
3. Publish/rollback/run creation satisfy the transaction invariants in `DATABASE_DESIGN.md`.
4. SSE replay, cancellation, terminal reconciliation, and secret redaction are integration-tested.
5. No contract claims an integration is operational until implemented with verified tests.

## Existing implementation

Implemented: `/health`, request IDs, safe error responses, authenticated `/api/v1/me`, and a tenant-scoped organization read (`GET /api/v1/organizations/{organization_id}`) routed through Supabase Data API under the user's JWT/RLS with camelCase serialization (`createdAt`), `response_model` validation, and robust upstream error handling (mapping timeouts, network errors, malformed JSON, and upstream status failures to 503 with X-Request-ID). Organization creation, membership administration, SSE streams, webhooks, and live auth/database integration tests are not yet available.

### Organization read response schema (`OrganizationResponse`)

```json
{
  "id": "00000000-0000-0000-0000-000000000001",
  "name": "Acme Corp",
  "slug": "acme-corp",
  "createdAt": "2026-01-01T00:00:00Z"
}
```
## Organization read endpoint validation notes

`GET /api/v1/organizations/{organization_id}` validates the bearer JWT against the configured Supabase JWKS, issuer (`{SUPABASE_URL}/auth/v1`), `authenticated` audience, required `exp`, `iat`, and `sub` claims, and strictly restricts allowed algorithms to `["ES256", "RS256"]`. Legacy `HS256` and insecure `none` are securely rejected before any upstream database call. It forwards the caller's bearer token and publishable API key to PostgREST; service-role keys are forbidden for user-scoped reads to preserve least privilege and prevent tenant authorization bypasses.

The migration `supabase/migrations/20261118210537_initial_organizations_memberships.sql` must have been applied before the endpoint can work. Required policy behavior: RLS enabled and forced on both `organizations` and `organization_memberships`; authenticated organization SELECT only where a matching membership has `user_id = auth.uid()`; membership SELECT limited to the caller; no unrestricted/anonymous SELECT. PostgREST returning no visible row is mapped to 404 to avoid confirming another tenant's organization exists (non-enumerating access control). Membership/invitation policies beyond initial self-owner creation are not implemented.

Automated cryptographic tests in `backend/tests/test_auth_integration.py` verify real EC/RSA signing and claim checks, and `backend/tests/test_tenant_isolation_integration.py` verifies tenant isolation and service-role key exclusion. Live multi-tenant staging verification requires following `docs/TENANT_ISOLATION_TEST_PLAN.md`. Do not run migrations against hosted projects without explicit approval.

