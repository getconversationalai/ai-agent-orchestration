# Core instructions (shared by Claude Code, Codex and Gemini)

## How to work
- Do what was asked. Make the smallest change that fully and correctly solves it: fix the root cause, handle the edge cases that can really happen, follow the patterns already in the codebase. Don't add layers, abstractions, options or features nobody asked for. If you notice a bigger problem, mention it in one line; don't fix it unasked.
- A question is not a request to change anything. Answer it; act only when asked to.
- Read the current local files directly. To start new work on a branch, fetch and branch from the latest main first.
- If an attempt fails, undo it before trying something else. Don't stack fixes.
- Before saying something is done, run the build/typecheck/tests that cover what you changed and say what you ran and the result. If you couldn't verify something, say so plainly.
- Stop any process you started (dev servers, watchers) when the task is done.
- Fixing a bug: where practical, add a test that fails before the fix and passes after, so the bug can't quietly come back. If that isn't practical, say why.
- Tests are evidence, not a hurdle. Never edit a test just to make it pass; change one only when the intended behavior changed, and say so. Tests with mocked data or a mocked database prove the logic only; database behavior, route wiring and deploys need a check against the real thing. Don't claim "nothing broke" based on tests you rewrote in the same change.
- Supabase / PostgREST: check `error` on every database call before using `data`. A bad query (a wrong or moved column) returns `{ data: null, error }` without throwing, so `if (!data) return` silently hides the failure. This has caused multi-day production outages.

## Talking to me
- Explain in plain English what you did and why, so a non-developer can follow. Name files only where it helps me check the work (a short "Files changed" line at the end is fine).
- Match length to the task: a small change gets 1–3 sentences. A large, multi-step job ends with: **Done** (what I can now do or see), **How to check** (where to click / what to look for), **Verified** (what you ran), **Next**.
- Bugs: say what the cause was, what you changed, and how you checked it.
- When a real decision is mine to make, ask with the options, their trade-offs and your recommendation (multiple choice when it fits). Don't ask about things you can sensibly decide yourself.
- For tasks with several steps, keep a short todo list and update it as you go.
- Don't sit idle waiting on me: start any independent work before asking, batch questions into one round, and go with your recommendation on easily reversible choices. Never do that for money, pushes to main, database writes or deletions.

## Safety (hooks enforce most of this)
- Never push to, commit on, or merge into main/master without my explicit OK.
- Never commit secrets (.env files, keys, credentials).
- Never write to a live database (insert/update/delete, schema changes, migrations) without asking me first in a separate message. The live database is the source of truth, not the migration files.
- Paid, bulk or hard-to-undo actions: ask first. A question I didn't answer is not a yes.
- Renaming or removing a database column, API field or anything other code reads: do it in steps (add the new one, move every reader, then remove the old one), never in one breaking step. Search every layer for readers.

## Read the detail doc only for these risky areas
Docs are in `~/.ai-instructions/`. Read just the section you need.
- Auth, tenant data / row-level security, payments, webhooks, secrets, AI-agent inputs → `security.md`
- Any database work → `supabase-operations.md`
- Renames or schema changes → `renames-and-schema-changes.md`
- Building a new screen or restyling UI → `ui-design-standards.md`
- Writing customer-facing text, or prompts that generate it → `sounding-human.md`

## Git
- Work on a feature branch (`feat/…`, `fix/…`). Commit when a change is coherent and push it.
- Commit messages use Conventional Commits prefixes: `feat:`, `fix:`, `refactor:`, `perf:`, `chore:`, `docs:`, `test:`.

## Parallel work
- One agent: work on a branch in the normal checkout. No worktree needed.
- Several agents editing the same repo at once: each one works in its own worktree on its own branch. Only one agent (or I) merges into main.
- Use subagents only for work that is independent and substantial, and launch them together, not one after another. A subagent that edits code gets its own worktree; read-only subagents don't need one.
- Merge conflicts: never silently drop either side's changes. If both sides added different things, keep both. If both changed the same lines differently, show me both versions and let me decide.
- Name new database migrations with a timestamp (`20261124_143000_description.sql`), not a sequence number, so parallel agents can't collide, unless the project already uses a different scheme.
- On Windows, remove worktrees only with `py ~/.ai-instructions/tools/worktree_cleanup.py <path>`. `rm -rf` and `git worktree remove` can follow npm's folder links and delete `.git`.

## Project memory
- If the repo has `PROJECT_SCOPE.md` or `MEMORY.md`, read them at the start. Add to `MEMORY.md` anything a future session would otherwise have to rediscover.
