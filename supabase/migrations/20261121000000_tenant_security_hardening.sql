-- Phase 11: Tenant isolation and relational integrity hardening.
-- This migration intentionally keeps the existing schema objects and tightens
-- constraints/policies in place. Apply only after reviewing the matching backend
-- changes; do not run against production until the staging test suite passes.

-- 1. Align the workflow draft column with the API contract.
alter table public.workflows
  rename column draft_graph to graph;

-- 2. Persist the optional agent description accepted by the API.
alter table public.agents
  add column if not exists description text
  check (description is null or char_length(description) <= 500);

-- 3. Create an organization and its initial owner membership atomically.
-- SECURITY DEFINER is required to avoid the initial-owner RLS bootstrap cycle.
-- The function derives identity only from auth.uid(), validates input, and is
-- executable only by authenticated users.
create or replace function public.create_organization_with_owner(
  p_name text,
  p_slug text
)
returns setof public.organizations
language plpgsql
security definer
set search_path = pg_catalog
as $function$
declare
  v_user_id uuid;
  v_organization public.organizations;
begin
  v_user_id := auth.uid();

  if v_user_id is null then
    raise exception 'Authentication required' using errcode = '42501';
  end if;

  if p_name is null or char_length(trim(p_name)) not between 1 and 120 then
    raise exception 'Organization name must contain 1 to 120 characters'
      using errcode = '22023';
  end if;

  if p_slug is null or p_slug !~ '^[a-z0-9]+([_-][a-z0-9]+)*$' then
    raise exception 'Invalid organization slug' using errcode = '22023';
  end if;

  insert into public.organizations (name, slug, created_by)
  values (trim(p_name), p_slug, v_user_id)
  returning * into v_organization;

  insert into public.organization_memberships (organization_id, user_id, role)
  values (v_organization.id, v_user_id, 'owner');

  return next v_organization;
end;
$function$;

revoke all on function public.create_organization_with_owner(text, text) from public;
revoke all on function public.create_organization_with_owner(text, text) from anon;
grant execute on function public.create_organization_with_owner(text, text) to authenticated;

-- 4. Add candidate keys needed by composite tenant-aware foreign keys.
alter table public.workflow_versions
  add constraint workflow_versions_org_id_id_key
  unique (organization_id, id);

alter table public.workflow_versions
  add constraint workflow_versions_org_workflow_id_id_key
  unique (organization_id, workflow_id, id);

alter table public.workflow_runs
  add constraint workflow_runs_org_id_id_key
  unique (organization_id, id);

alter table public.workflow_nodes
  add constraint workflow_nodes_org_version_node_key
  unique (organization_id, workflow_version_id, node_id);

-- 5. Replace single-column workflow references with tenant-consistent FKs.
alter table public.workflow_nodes
  drop constraint workflow_nodes_workflow_version_id_fkey;

alter table public.workflow_nodes
  add constraint workflow_nodes_org_version_fkey
  foreign key (organization_id, workflow_version_id)
  references public.workflow_versions (organization_id, id)
  on delete cascade;

alter table public.workflow_edges
  drop constraint workflow_edges_workflow_version_id_fkey;

alter table public.workflow_edges
  add constraint workflow_edges_org_version_fkey
  foreign key (organization_id, workflow_version_id)
  references public.workflow_versions (organization_id, id)
  on delete cascade;

alter table public.workflow_edges
  add constraint workflow_edges_source_node_fkey
  foreign key (organization_id, workflow_version_id, source_node_id)
  references public.workflow_nodes (organization_id, workflow_version_id, node_id)
  on delete cascade;

alter table public.workflow_edges
  add constraint workflow_edges_target_node_fkey
  foreign key (organization_id, workflow_version_id, target_node_id)
  references public.workflow_nodes (organization_id, workflow_version_id, node_id)
  on delete cascade;

alter table public.workflow_runs
  drop constraint workflow_runs_workflow_version_id_fkey;

alter table public.workflow_runs
  add constraint workflow_runs_org_workflow_version_fkey
  foreign key (organization_id, workflow_id, workflow_version_id)
  references public.workflow_versions (organization_id, workflow_id, id)
  on delete restrict;

alter table public.workflow_run_events
  drop constraint workflow_run_events_workflow_run_id_fkey;

alter table public.workflow_run_events
  add constraint workflow_run_events_org_run_fkey
  foreign key (organization_id, workflow_run_id)
  references public.workflow_runs (organization_id, id)
  on delete cascade;

-- 6. Scope idempotency keys to an organization instead of globally.
alter table public.workflow_runs
  drop constraint if exists workflow_runs_idempotency_key_key;

alter table public.workflow_runs
  add constraint workflow_runs_org_idempotency_key_key
  unique (organization_id, idempotency_key);

-- 6a. Correct the organization read policy: memberships also has an id column,
-- so the outer organizations primary key must be explicitly qualified.
drop policy if exists organizations_read_member on public.organizations;

create policy organizations_read_member on public.organizations
  for select to authenticated
  using (exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = organizations.id
      and m.user_id = (select auth.uid())
  ));

-- 7. Rebuild agent policies with explicitly qualified outer-row references.
drop policy if exists agents_read on public.agents;
drop policy if exists agents_insert on public.agents;
drop policy if exists agents_update on public.agents;
drop policy if exists agents_delete on public.agents;
drop policy if exists agent_versions_read on public.agent_versions;
drop policy if exists agent_versions_insert on public.agent_versions;

create policy agents_read on public.agents
  for select to authenticated
  using (exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = agents.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy agents_insert on public.agents
  for insert to authenticated
  with check (exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = agents.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy agents_update on public.agents
  for update to authenticated
  using (exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = agents.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ))
  with check (exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = agents.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy agents_delete on public.agents
  for delete to authenticated
  using (exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = agents.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy agent_versions_read on public.agent_versions
  for select to authenticated
  using (exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = agent_versions.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy agent_versions_insert on public.agent_versions
  for insert to authenticated
  with check (
    published_by = (select auth.uid())
    and exists (
      select 1
      from public.organization_memberships m
      where m.organization_id = agent_versions.organization_id
        and m.user_id = (select auth.uid())
        and m.role in ('owner', 'admin', 'builder')
    )
  );

-- 8. Rebuild workflow policies with explicitly qualified outer-row references.
drop policy if exists workflows_read on public.workflows;
drop policy if exists workflows_insert on public.workflows;
drop policy if exists workflows_update on public.workflows;
drop policy if exists workflows_delete on public.workflows;
drop policy if exists workflow_versions_read on public.workflow_versions;
drop policy if exists workflow_versions_insert on public.workflow_versions;
drop policy if exists workflow_nodes_read on public.workflow_nodes;
drop policy if exists workflow_nodes_insert on public.workflow_nodes;
drop policy if exists workflow_edges_read on public.workflow_edges;
drop policy if exists workflow_edges_insert on public.workflow_edges;
drop policy if exists workflow_runs_read on public.workflow_runs;
drop policy if exists workflow_runs_insert on public.workflow_runs;
drop policy if exists workflow_runs_update on public.workflow_runs;
drop policy if exists workflow_run_events_read on public.workflow_run_events;
drop policy if exists workflow_run_events_insert on public.workflow_run_events;

create policy workflows_read on public.workflows
  for select to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflows.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy workflows_insert on public.workflows
  for insert to authenticated
  with check (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflows.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy workflows_update on public.workflows
  for update to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflows.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ))
  with check (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflows.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy workflows_delete on public.workflows
  for delete to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflows.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy workflow_versions_read on public.workflow_versions
  for select to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_versions.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy workflow_versions_insert on public.workflow_versions
  for insert to authenticated
  with check (
    published_by = (select auth.uid())
    and exists (
      select 1 from public.organization_memberships m
      where m.organization_id = workflow_versions.organization_id
        and m.user_id = (select auth.uid())
        and m.role in ('owner', 'admin', 'builder')
    )
  );

create policy workflow_nodes_read on public.workflow_nodes
  for select to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_nodes.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy workflow_nodes_insert on public.workflow_nodes
  for insert to authenticated
  with check (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_nodes.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy workflow_edges_read on public.workflow_edges
  for select to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_edges.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy workflow_edges_insert on public.workflow_edges
  for insert to authenticated
  with check (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_edges.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder')
  ));

create policy workflow_runs_read on public.workflow_runs
  for select to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_runs.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy workflow_runs_insert on public.workflow_runs
  for insert to authenticated
  with check (
    requested_by = (select auth.uid())
    and exists (
      select 1 from public.organization_memberships m
      where m.organization_id = workflow_runs.organization_id
        and m.user_id = (select auth.uid())
        and m.role in ('owner', 'admin', 'builder', 'member')
    )
  );

create policy workflow_runs_update on public.workflow_runs
  for update to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_runs.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder', 'member')
  ))
  with check (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_runs.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder', 'member')
  ));

create policy workflow_run_events_read on public.workflow_run_events
  for select to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_run_events.organization_id
      and m.user_id = (select auth.uid())
  ));

create policy workflow_run_events_insert on public.workflow_run_events
  for insert to authenticated
  with check (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = workflow_run_events.organization_id
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin', 'builder', 'member')
  ));

-- Explicitly keep RLS enabled and forced as a defense-in-depth check.
alter table public.agents force row level security;
alter table public.agent_versions force row level security;
alter table public.workflows force row level security;
alter table public.workflow_versions force row level security;
alter table public.workflow_nodes force row level security;
alter table public.workflow_edges force row level security;
alter table public.workflow_runs force row level security;
alter table public.workflow_run_events force row level security;
