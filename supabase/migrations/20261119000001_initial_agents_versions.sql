-- Phase 3: Agent Draft & Immutable Versioning
-- Tenant isolation via organization_id + RLS; composite FK to prevent cross-tenant version injection

-- 1. Agents table (Mutable draft)
create table public.agents (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null check (char_length(trim(name)) between 1 and 120),
  draft_config jsonb not null default '{}',
  draft_revision int not null default 0,
  archived_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, id)  -- enables composite FK from agent_versions
);

-- 2. Agent Versions table (Immutable snapshot, append-only)
create table public.agent_versions (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  agent_id uuid not null,
  version_number int not null,
  snapshot jsonb not null,
  published_by uuid not null references auth.users(id),
  published_at timestamptz not null default now(),
  unique (agent_id, version_number),
  -- Composite FK guarantees version belongs to same org as its agent
  foreign key (organization_id, agent_id) references public.agents(organization_id, id) on delete cascade
);

-- 3. Indexes
create index agents_org_id_idx on public.agents(organization_id);
create index agent_versions_org_id_idx on public.agent_versions(organization_id);
create index agent_versions_agent_id_idx on public.agent_versions(agent_id);

-- 4. RLS Setup
alter table public.agents enable row level security;
alter table public.agents force row level security;
alter table public.agent_versions enable row level security;
alter table public.agent_versions force row level security;

-- 5. Grants (least privilege)
grant select, insert, update, delete on public.agents to authenticated;
grant select, insert on public.agent_versions to authenticated;

-- 6. Policies for agents (Mutable)
-- Read: any member of the organization
create policy agents_read on public.agents
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

-- Write: owner/admin/builder roles only; WAL constraint ensures org match on insert
create policy agents_insert on public.agents
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

create policy agents_update on public.agents
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

create policy agents_delete on public.agents
  for delete to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

-- 7. Policies for agent_versions (Append-only)
-- Read: any member of the organization (org_id enforced by policy)
create policy agent_versions_read on public.agent_versions
  for select to authenticated
  using (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
    )
  );

-- Insert: owner/admin/builder roles only; composite FK enforces org+agent match
create policy agent_versions_insert on public.agent_versions
  for insert to authenticated
  with check (
    exists (
      select 1 from public.organization_memberships m
      where m.organization_id = organization_id and m.user_id = auth.uid()
        and m.role in ('owner', 'admin', 'builder')
    )
  );

-- No UPDATE/DELETE policies → RLS blocks mutations (immutability by design)