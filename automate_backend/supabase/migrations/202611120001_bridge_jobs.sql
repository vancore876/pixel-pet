create extension if not exists pgcrypto with schema extensions;

create table if not exists public.jeffery_automate_jobs (
  id uuid primary key default gen_random_uuid(),
  actor_account_id bigint not null,
  actor_username text not null check (char_length(actor_username) between 3 and 32),
  operation text not null check (operation ~ '^[a-z][a-z0-9_]{1,63}$'),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'claimed', 'completed', 'failed', 'expired')),
  attempt_count smallint not null default 0 check (attempt_count between 0 and 20),
  bridge_id text,
  result jsonb,
  error_code text,
  created_at timestamptz not null default now(),
  claimed_at timestamptz,
  completed_at timestamptz,
  expires_at timestamptz not null default (now() + interval '10 minutes'),
  check (jsonb_typeof(payload) = 'object'),
  check (result is null or jsonb_typeof(result) in ('object', 'array'))
);

create index if not exists jeffery_automate_jobs_pending_idx
  on public.jeffery_automate_jobs (created_at)
  where status = 'pending';

create index if not exists jeffery_automate_jobs_actor_idx
  on public.jeffery_automate_jobs (actor_account_id, created_at desc);

alter table public.jeffery_automate_jobs enable row level security;
revoke all on public.jeffery_automate_jobs from anon, authenticated;
grant select, insert, update on public.jeffery_automate_jobs to service_role;

create or replace function public.jeffery_automate_claim_jobs(p_bridge_id text, p_limit integer default 5)
returns setof public.jeffery_automate_jobs
language sql
security definer
set search_path = pg_catalog, public
as $function$
  with claimable as (
    select id
    from public.jeffery_automate_jobs
    where expires_at > now()
      and attempt_count < 5
      and (status = 'pending' or (status = 'claimed' and claimed_at < now() - interval '2 minutes'))
    order by created_at
    for update skip locked
    limit least(greatest(coalesce(p_limit, 5), 1), 10)
  )
  update public.jeffery_automate_jobs as jobs
  set status = 'claimed', bridge_id = p_bridge_id, claimed_at = now(), attempt_count = attempt_count + 1
  from claimable
  where jobs.id = claimable.id
  returning jobs.*;
$function$;

revoke all on function public.jeffery_automate_claim_jobs(text, integer) from public, anon, authenticated;
grant execute on function public.jeffery_automate_claim_jobs(text, integer) to service_role;
