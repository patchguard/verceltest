-- Wickbound schema for Supabase Postgres.
-- Run once in the Supabase SQL editor.

create extension if not exists pgcrypto;

-- Game profile; id = Supabase Auth user id (JWT "sub").
create table if not exists public.users (
  id          uuid primary key references auth.users (id) on delete cascade,
  username    text not null check (username ~ '^[A-Za-z0-9_]{3,20}$'),
  role        text not null default 'player' check (role in ('player', 'admin')),
  created_at  timestamptz not null default now()
);
create unique index if not exists users_username_lower_idx on public.users (lower(username));

create table if not exists public.levels (
  id           uuid primary key default gen_random_uuid(),
  author_id    uuid not null references public.users (id) on delete cascade,
  title        text not null check (char_length(title) between 1 and 64),
  description  text not null default '' check (char_length(description) <= 500),
  data         jsonb not null,
  status       text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  review_note  text,
  reviewed_by  uuid references public.users (id) on delete set null,
  reviewed_at  timestamptz,
  created_at   timestamptz not null default now()
);
create index if not exists levels_status_created_idx on public.levels (status, created_at desc);
create index if not exists levels_author_created_idx on public.levels (author_id, created_at desc);

-- Every submission, accepted or rejected (audit trail + rate limiting).
create table if not exists public.score_submissions (
  id              bigint generated always as identity primary key,
  user_id         uuid not null references public.users (id) on delete cascade,
  board           text not null,                 -- 'global' or a level id
  level_id        uuid references public.levels (id) on delete cascade,
  score           integer not null check (score >= 0),
  duration_ms     integer not null check (duration_ms >= 0),
  client_version  text,
  status          text not null check (status in ('accepted', 'rejected')),
  reject_reason   text,
  created_at      timestamptz not null default now()
);
create index if not exists score_submissions_user_created_idx on public.score_submissions (user_id, created_at desc);

-- Best verified score per (board, user).
create table if not exists public.leaderboard (
  board          text not null,
  user_id        uuid not null references public.users (id) on delete cascade,
  score          integer not null,
  duration_ms    integer not null,
  submission_id  bigint not null references public.score_submissions (id) on delete cascade,
  achieved_at    timestamptz not null,
  primary key (board, user_id)
);
create index if not exists leaderboard_rank_idx on public.leaderboard (board, score desc, achieved_at asc);

-- Only the backend (connecting as the postgres role, which bypasses RLS) touches
-- these tables. RLS with no policies blocks direct access via the anon key / PostgREST.
alter table public.users             enable row level security;
alter table public.levels            enable row level security;
alter table public.score_submissions enable row level security;
alter table public.leaderboard       enable row level security;

-- Promote an admin:
--   update public.users set role = 'admin' where username = 'your_name';
