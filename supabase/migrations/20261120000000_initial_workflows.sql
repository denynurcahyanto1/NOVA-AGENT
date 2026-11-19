-- Phase 7: Workflow Engine
-- Tenant isolation via organization_id + RLS; immutable versions with composite FKs

-- 1. Workflows table (Mutable draft)
create table public.workflows (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null check (char_length(trim(name)) between 1 and 120),
  description text,
  draft_graph jsonb not null default '{"nodes": [], "edges": []}',
  draft_revision int not null default 0,
  archived_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, id)
);

-- 2. Workflow Versions table (Immutable snapshot, append-only)
create table public.workflow_versions (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  workflow_id uuid not null,
  version_number int not null,
  snapshot jsonb not null,
  published_by uuid not null references auth.users(id),
  published_at timestamptz not null default now(),
  unique (workflow_id, version_number),
  foreign key (organization_id, workflow_id) references public.workflows(organization_id, id) on delete cascade
);

-- 3. Workflow Nodes table (DAG nodes per version)
create table public.workflow_nodes (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  workflow_version_id uuid not null references public.workflow_versions(id) on delete cascade,
  node_id text not null,  -- stable identifier within the workflow
  node_type text not null check (node_type in ('agent', 'tool', 'condition', 'parallel', 'join', 'start', 'end')),
  config jsonb not null default '{}',
  position jsonb,  -- {x, y} for UI
  unique (workflow_version_id, node_id)
);

-- 4. Workflow Edges table (DAG edges per version)
create table public.workflow_edges (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  workflow_version_id uuid not null references public.workflow_versions(id) on delete cascade,
  source_node_id text not null,
  target_node_id text not null,
  condition jsonb,  -- optional condition for conditional edges
  unique (workflow_version_id, source_node_id, target_node_id)
);

-- 5. Workflow Runs table (Execution state)
create table public.workflow_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  workflow_id uuid not null references public.workflows(id) on delete cascade,
  workflow_version_id uuid not null references public.workflow_versions(id) on delete restrict,
  requested_by uuid not null references auth.users(id),
  status text not null check (status in ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')),
  input jsonb not null default '{}',
  output jsonb,
  context jsonb not null default '{}',  -- Shared context during execution
  node_states jsonb not null default '{}',  -- Per-node execution state
  current_node_id text,
  error jsonb,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  idempotency_key text unique,
  foreign key (organization_id, workflow_id) references public.workflows(organization_id, id) on delete cascade
);

-- 6. Workflow Run Events table (Append-only event log for streaming/replay)
create table public.workflow_run_events (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  workflow_run_id uuid not null references public.workflow_runs(id) on delete cascade,
  sequence int not null,
  event_type text not null,
  payload jsonb not null default '{}',
  occurred_at timestamptz not null default now(),
  unique (workflow_run_id, sequence)
);

-- 7. Indexes
create index workflows_org_id_idx on public.workflows(organization_id);
create index workflow_versions_org_id_idx on public.workflow_versions(organization_id);
create index workflow_versions_workflow_id_idx on public.workflow_versions(workflow_id);
create index workflow_nodes_version_idx on public.workflow_nodes(workflow_version_id);
create index workflow_edges_version_idx on public.workflow_edges(workflow_version_id);
create index workflow_runs_org_id_idx on public.workflow_runs(organization_id);
create index workflow_runs_workflow_id_idx on public.workflow_runs(workflow_id);
create index workflow_runs_version_id_idx on public.workflow_runs(workflow_version_id);
create index workflow_runs_status_idx on public.workflow_runs(status);
create index workflow_run_events_run_id_idx on public.workflow_run_events(workflow_run_id);

-- 8. RLS Setup
alter table public.workflows enable row level security;
alter table public.workflows force row level security;
alter table public.workflow_versions enable row level security;
alter table public.workflow_versions force row level security;
alter table public.workflow_nodes enable row level security;
alter table public.workflow_nodes force row level security;
alter table public.workflow_edges enable row level security;
alter table public.workflow_edges force row level security;
alter table public.workflow_runs enable row level security;
alter table public.workflow_runs force row level security;
alter table public.workflow_run_events enable row level security;
alter table public.workflow_run_events force row level security;

-- 9. Grants (least privilege)
grant select, insert, update, delete on public.workflows to authenticated;
grant select, insert on public.workflow_versions to authenticated;
grant select, insert on public.workflow_nodes to authenticated;
grant select, insert on public.workflow_edges to authenticated;
grant select, insert on public.workflow_runs to authenticated;
grant select, insert on public.workflow_run_events to authenticated;

-- 10. Policies for workflows (Mutable)
create policy workflows_read on public.workflows
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

create policy workflows_insert on public.workflows
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

create policy workflows_update on public.workflows
  for update to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  )
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

create policy workflows_delete on public.workflows
  for delete to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

-- 11. Policies for workflow_versions (Append-only)
create policy workflow_versions_read on public.workflow_versions
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

create policy workflow_versions_insert on public.workflow_versions
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

-- 12. Policies for workflow_nodes (Append-only per version)
create policy workflow_nodes_read on public.workflow_nodes
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

create policy workflow_nodes_insert on public.workflow_nodes
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

-- 13. Policies for workflow_edges (Append-only per version)
create policy workflow_edges_read on public.workflow_edges
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

create policy workflow_edges_insert on public.workflow_edges
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

-- 14. Policies for workflow_runs (Mutable during execution)
create policy workflow_runs_read on public.workflow_runs
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

create policy workflow_runs_insert on public.workflow_runs
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder', 'member')
    )
  );

create policy workflow_runs_update on public.workflow_runs
  for update to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder', 'member')
    )
  )
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder', 'member')
    )
  );

-- 15. Policies for workflow_run_events (Append-only)
create policy workflow_run_events_read on public.workflow_run_events
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

create policy workflow_run_events_insert on public.workflow_run_events
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder', 'member')
    )
  );