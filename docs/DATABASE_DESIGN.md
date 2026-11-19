# NOVA AGENT — Database Design

## Status and scope

**Phase 1 schema proposal — not implemented.** No PostgreSQL database, Supabase project schema, migration, RLS policy, or ORM model for NOVA AGENT has been created in this repository. Names below define the intended migration target, not existing tables.

## Data ownership and conventions

- UUID primary keys; `created_at` and `updated_at` as timezone-aware timestamps.
- All tenant-owned records carry non-null `organization_id` referencing `organizations.id`.
- Use `deleted_at` only for resources where soft deletion is approved; versions, audit records, and usage records remain append-only.
- Store structured configuration/snapshots in validated `jsonb`; index stable query fields as normal columns.
- Credential ciphertext, not plaintext, is stored in the database. Key material remains outside it.

## Proposed entity model

| Domain | Proposed tables | Key notes |
| --- | --- | --- |
| Identity and tenancy | `profiles`, `organizations`, `organization_memberships`, `roles`, `role_permissions`, `membership_roles` | `profiles.id` maps to Supabase Auth user ID. Membership is organization-scoped. |
| Agents | `agents`, `agent_versions`, `agent_runs`, `agent_run_events` | Draft is mutable; versions and run events are immutable/append-only. |
| Providers | `provider_credentials`, `provider_models`, `provider_health_checks` | Credentials hold envelope-encrypted ciphertext and metadata only. |
| Workflows | `workflows`, `workflow_versions`, `workflow_runs`, `workflow_run_events`, `workflow_approvals` | Versions snapshot graph/configuration; runs bind exact version. |
| Memory and knowledge | `conversations`, `conversation_messages`, `agent_memories`, `knowledge_documents`, `knowledge_chunks`, `retrieval_jobs` | Permissions and retention are organization-scoped. |
| SaaS and observability | `usage_records`, `subscriptions`, `plan_entitlements`, `quota_counters`, `audit_logs`, `idempotency_keys` | Usage/audit append-only; counter updates are transactional. |

## Core relationships

```text
profiles ──< organization_memberships >── organizations
organizations ──< agents ──< agent_versions ──< agent_runs
organizations ──< workflows ──< workflow_versions ──< workflow_runs
organizations ──< provider_credentials
organizations ──< knowledge_documents ──< knowledge_chunks
organizations ──< usage_records / audit_logs / subscriptions
```

## Key table outlines

### Identity and RBAC

- `profiles(id PK = auth.users.id, display_name, avatar_url, ...)` contains user profile data, not credentials.
- `organizations(id, name, slug, status, created_at, ...)` represents the tenant.
- `organization_memberships(id, organization_id, user_id, status, joined_at, ...)` has `UNIQUE(organization_id, user_id)`.
- `roles` and `role_permissions` define organization roles such as owner, admin, builder, member, viewer. Initial roles and exact permission matrix remain a product decision.

### Agents and versions

- `agents(id, organization_id, name, draft_config jsonb, draft_revision, archived_at, ...)` is the mutable aggregate root.
- `agent_versions(id, organization_id, agent_id, version_number, snapshot jsonb, published_by, published_at, ...)` has `UNIQUE(agent_id, version_number)` and an immutable snapshot.
- `agent_runs(id, organization_id, agent_id, agent_version_id NOT NULL, requested_by, status, input_ref, output_ref, started_at, ended_at, ...)` references the exact agent version used.
- `agent_run_events(id, organization_id, agent_run_id, sequence, event_type, payload, occurred_at)` has `UNIQUE(agent_run_id, sequence)` and supports ordered streaming/replay.

### Providers and credentials

- `provider_credentials(id, organization_id, provider, label, encrypted_dek, ciphertext, key_version, status, last_validated_at, ...)` stores no plaintext API key.
- `provider_models` is a provider/model capability cache; it must not contain credential material.
- Agent-version snapshots reference provider credential IDs only when a stable reference is required; authorization verifies the credential belongs to the same organization at execution time.

### Workflows

- `workflows` stores mutable draft graph metadata.
- `workflow_versions` stores an immutable validated graph snapshot with `UNIQUE(workflow_id, version_number)`.
- `workflow_runs` references `workflow_version_id NOT NULL`; node state and output references are persisted to enable recovery.
- Approval nodes create `workflow_approvals`; an approval decision is actor-attributed and immutable after submission.

### Memory and knowledge

- Conversation/memory records must have an explicit owner scope: organization, agent, workflow, user, or conversation as applicable.
- `knowledge_documents` tracks source, checksum, retention, visibility, and ingestion status.
- `knowledge_chunks` references its document and carries organization ID to preserve direct RLS enforcement. Vector-column choice is deferred.

## Tenant isolation requirements

Every tenant-owned table must satisfy all of the following:

1. `organization_id` is non-null, indexed, and protected by a foreign key.
2. RLS is enabled and forced where supported; select/insert/update/delete policies require the caller’s authenticated membership and trusted tenant context.
3. API queries include `organization_id` predicates even when RLS exists, so authorization intent is visible and database defense is layered.
4. Foreign-key relationships between tenant records must prevent cross-organization references. Use composite constraints `(organization_id, id)` or trigger/constraint validation where a simple FK cannot prove it.
5. Service/worker roles bypassing RLS are prohibited for normal tenant operations; if unavoidable, they establish transaction-local tenant context and are limited to audited, narrow code paths.

## Publishing and rollback transaction invariants

### Publish agent version

One serializable or appropriately locked transaction must:

1. Lock the `agents` row for the requested organization and confirm actor permission.
2. Validate draft configuration, organization-owned provider/tool references, and quota/plan constraints.
3. Allocate `version_number = previous + 1` under lock; enforce `UNIQUE(agent_id, version_number)` as the final concurrency guard.
4. Insert a complete immutable `agent_versions.snapshot`.
5. Write the audit record in the same transaction.
6. Commit atomically. On error, no version or audit record is partially published.

Workflow publishing follows the same pattern, with graph validation before insertion.

### Rollback

Rollback must lock the agent, verify organization ownership of both agent and source version, copy the historical snapshot into the mutable draft, increment `draft_revision`, and write an audit record. It never mutates `agent_versions` and never changes a run’s version reference. Publishing the restored draft is a separate transaction.

### Run creation and completion

- Creating an agent run validates tenant, permission, published version ownership, quota, and idempotency key in one transaction.
- Usage records, terminal run status, and quota counters are committed atomically or reconciled through an explicitly idempotent settlement process.
- State transitions use conditional updates (expected prior state) to reject duplicate worker completion.

## RLS policy pattern (proposed)

- Client access is limited to an authenticated user’s current organization membership.
- Backend uses a verified JWT subject plus a transaction-local active organization identifier; the database policy verifies both membership and row organization.
- Insert policies constrain `organization_id` to the active, authorized organization rather than trusting arbitrary input.
- Audit/usage/version tables are insert-only for application paths; users may receive constrained read access based on role.

Exact SQL must be authored and security-reviewed in Phase 2/3 before migrations are applied.

## Migration and retention rules

- Use reviewed, reversible-by-design Alembic migrations; do not use schema push as a production migration strategy.
- Migrations add indexes concurrently where production constraints require it and include data backfill plans before non-null constraints.
- Backups, retention, legal deletion, vector embedding lifecycle, and encrypted credential key rotation require explicit Phase 10/security decisions.

## Existing implementation

No proposed table, constraint, migration, RLS policy, index, or transaction exists in the NOVA AGENT repository today.
