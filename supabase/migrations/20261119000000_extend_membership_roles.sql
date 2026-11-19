-- Phase 2 extension: Add builder/viewer roles to organization_memberships
-- Required for Phase 3 agent write policies (owner/admin/builder)

-- Drop existing check constraint and recreate with extended roles
alter table public.organization_memberships
  drop constraint if exists organization_memberships_role_check;

alter table public.organization_memberships
  add constraint organization_memberships_role_check
  check (role in ('owner', 'admin', 'builder', 'member', 'viewer'));