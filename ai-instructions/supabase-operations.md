---
name: supabase-operations
description: How to read/write Supabase safely — project-md-authoritative model, execution confirmation, live-DB source of truth, migration file discipline
when_to_read: before ANY database or SQL work, from any repo
sections:
  - Authoritative source is the project md
  - Suggested default for new projects
  - Access scope and least-privilege
  - Database execution confirmation
  - Live database is the source of truth
  - Writing migration files
---

> Any schema CHANGE (rename/add/drop/retype tables, columns, constraints, triggers, functions, enums) follows the standard process in `renames-and-schema-changes.md` — classification matrix, boundary inventory, phased execution, live verification. This doc covers Supabase-specific mechanics.

> **Machine-specific project runbooks live in `supabase-projects.local.md`** (not published): which connection method each project uses, its scripts, and its known quirks. Read that too when working on a project it covers.

# Supabase Database Operations
> Navigation: read this frontmatter + section list, then `grep` the `##` heading you need and read only that section. Do not read the whole doc.

## Authoritative source is the project md
The REAL way to read/write a given project's database lives in THAT project's md files (CLAUDE.md / AGENTS.md / GEMINI.md), or in `supabase-projects.local.md`. Always defer to it. This global file is only a *suggested* starting point. **Existing projects keep their own method — do not change it.**

## Suggested default for new projects
Use direct Postgres via `psql`, with a single per-project connection string read from that project's `server/.env`:
- **Read a query:** `psql "$SUPABASE_DB_URL" -c "select ..."`
- **Full schema dump:** `pg_dump --schema-only --schema=public "$SUPABASE_DB_URL"`
- **Run a migration (WRITE — needs confirmation, see below):** `psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f path/to/migration.sql`

Load `SUPABASE_DB_URL` from `server/.env` (e.g. `source server/.env`). On Windows/PowerShell, read the line from `server/.env` and assign it to the env var before calling psql.

Why this is the default: it is standard, supports full read/write, and `pg_dump` gives complete schema. The Postgres wire protocol also survives network setups that interfere with HTTPS to the management API — see the local runbook if a project needs that workaround.

## Access scope and least-privilege
- A `SUPABASE_DB_URL` is **per-project**: its host (`db.<project-ref>.supabase.co` / pooler) and password are unique to ONE Supabase project. It grants NO access to other projects. (The account-wide credential is the Management API token — which this default deliberately avoids.)
- Within a project, the URL is an elevated, RLS-bypassing connection. To limit employee/contractor access, do it **server-side** (you cannot enforce it from files on their machine):
  - Preferred: give them a **separate dev/staging Supabase project**; keep the prod URL with you/CI.
  - Or: create a **read-only Postgres role** server-side and distribute only its connection string.
- Distribution is the control point: `.env` is gitignored, so cloning the repo grants no credentials. Revoke by rotating the password / dropping the role server-side.

## Database execution confirmation

**NEVER execute SQL against a live database without a dedicated, standalone confirmation from the user.**

This applies to `psql`, a management-API script, or any command that writes to the database. The confirmation request MUST be its own message containing ONLY the request to run the SQL — no summaries, status updates, or other content bundled with it.

**Why:** When SQL execution is mentioned alongside other content (e.g., "Here's what I did... want me to run the migration?"), a user reply like "continue", "go", or "yes" is ambiguous — it could mean "continue working" not "execute SQL against production."

**Correct flow:**
1. Complete code changes and summarize them (do NOT mention running SQL here)
2. In a **separate, standalone message**, ask ONLY: "Here's the migration to execute: `[filename]`. Can I run it?"
3. Wait for explicit confirmation specifically about the SQL execution

**Each migration is a separate authorization event** — prior approvals in the same conversation do NOT carry forward.

## Live database is the source of truth

**Migration files are a historical log, not a source of truth. Always verify against the live database.**

For ANY database-related work — reads, writes, RPC/function definitions, triggers, constraints, RLS policies, column types, defaults, indexes — query the live DB. Never base decisions on what a migration file says.

**Why:** Later migrations may replace function bodies (`CREATE OR REPLACE FUNCTION`), move tables between schemas, rename columns, drop constraints, or otherwise supersede earlier definitions. The original migration file stays unchanged on disk but no longer reflects reality. Reading a migration file and assuming it's current is a fast path to wrong answers and broken code.

**How to apply:**
- Before modifying or renaming any DB object, query the live state:
  - Functions/procedures: `SELECT prosrc FROM pg_proc WHERE proname = '<name>'`
  - Triggers: `SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid = '<schema>.<table>'::regclass`
  - Policies: `SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy WHERE polrelid = '<schema>.<table>'::regclass`
  - Constraints: query `pg_constraint` joined with `pg_class`/`pg_namespace`
  - Columns/types/defaults: `information_schema.columns` or a schema-dump script
- **Confirm WHICH TABLE a column lives on — not just that the column name exists somewhere.** After a refactor, columns get *moved* between tables (split a table, denormalize, extract a child entity). The old column name still exists in your memory and in old migration files, but selecting it from its *old* table now silently 400s: PostgREST returns `{ data: null, error: <42703 undefined column> }` with no throw, so a `if (!data) return` swallows it and the feature dies invisibly. **This has caused a multi-day production outage in practice** — a core feature silently stopped working for days because one column had moved tables (details in `supabase-projects.local.md`). For every column you select, verify its **table** against the live schema (`information_schema.columns WHERE table_name = '<table>' AND column_name = '<col>'` or a fresh `pg_dump`), and if the project memory has a **schema-drift / column-moved map**, read it first — it lists which columns relocated and to where.
- When writing a new function that's a modified version of an existing one, copy the body from `pg_proc`, not from a migration file.
- When answering a question about how something works ("what does function X do?", "what columns does table Y have?"), query the live DB — do not quote a migration file as the answer.
- Applies even to "simple" changes and even for read-only questions. No exceptions.

## Writing migration files
- Location: the project's own `supabase/migrations/` (or as the project md specifies).
- Naming: `YYYYMMDD_HHMMSS_<description>.sql`, using a **real UTC timestamp** of when you author it (`date -u +%Y%m%d_%H%M%S`) — NEVER a round-number placeholder like `120000`. Migrations apply in lexicographic filename order, so the prefix is the ordering key. Never use sequential integers for new migrations.
- **Timestamp naming is NOT automatically collision-free** (a common misconception). Parallel agents/branches that hand-pick or round timestamps collide constantly. Before committing a migration: `git fetch origin` and confirm your `YYYYMMDD_HHMMSS` prefix isn't already on `origin/main` (`git ls-tree -r --name-only origin/main -- supabase/migrations/`); if it's taken, bump to the next free real timestamp — don't reuse it. If a collision still lands, fix it by renaming the NEW file only (`git mv`), never an already-applied one. A project may also enforce this in CI (see its own instructions).
- After execution: refresh schema cache / regenerate types per the project's documented commands.
