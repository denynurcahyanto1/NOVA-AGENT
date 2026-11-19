-- Initial tenant boundary. Authorization claims come only from auth.uid(),
-- never user-editable user_metadata.
create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(trim(name)) between 1 and 120),
  slug text not null unique check (slug ~ '^[a-z0-9]+([_-][a-z0-9]+)*$'),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.organization_memberships (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('owner', 'admin', 'member')),
  created_at timestamptz not null default now(),
  unique (organization_id, user_id)
);

create index organization_memberships_user_org_idx
  on public.organization_memberships (user_id, organization_id);

alter table public.organizations enable row level security;
alter table public.organizations force row level security;
alter table public.organization_memberships enable row level security;
alter table public.organization_memberships force row level security;

grant select, insert on public.organizations to authenticated;
grant select, insert on public.organization_memberships to authenticated;

create policy organizations_read_member on public.organizations
  for select to authenticated
  using (exists (
    select 1 from public.organization_memberships m
    where m.organization_id = id and m.user_id = (select auth.uid())
  ));

create policy organizations_create_self on public.organizations
  for insert to authenticated
  with check (created_by = (select auth.uid()));

create policy memberships_read_self on public.organization_memberships
  for select to authenticated
  using (user_id = (select auth.uid()));

-- First owner can only add themselves to an organization they created.
-- Invitations and role changes are intentionally absent pending a reviewed API.
create policy memberships_create_initial_owner on public.organization_memberships
  for insert to authenticated
  with check (
    user_id = (select auth.uid())
    and role = 'owner'
    and exists (
      select 1 from public.organizations o
      where o.id = organization_id and o.created_by = (select auth.uid())
    )
  );
