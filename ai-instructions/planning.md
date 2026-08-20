---
name: planning
description: How to write implementation plans (precision tiers — intent is authoritative, locations/code are illustrative) and the plan-review gate run before execution
when_to_read: before writing an implementation plan, AND before executing one
sections:
  - Ceremony by change size (tiers)
  - Precision Tiers
  - Authoring Rules
  - Declaring execution structure (tracks)
  - Executing by structure
  - Plan-Review Gate
  - Reviewer Checklist
---
# Implementation Planning

> Navigation: read this frontmatter + section list, then `grep` the `##` heading you need and read only that section. Do not read the whole doc.

> This doc OVERRIDES the superpowers `writing-plans` / `subagent-driven-development` skills where they conflict (user instructions win). The skills' "no vague placeholders, show real code, frequent commits, TDD" discipline still holds — this only changes how *authoritative* exact paths/line-numbers/code are, and adds a mandatory review of the plan itself.

> **Execution-mode override (supersedes superpowers defaults).** The superpowers `writing-plans` / `subagent-driven-development` skills default to executing a plan as one-subagent-per-task **in sequence** (implement task → review → next task → …). That default is SUPERSEDED: choose execution mode by the decision model in `parallel-agents.md` (`## The parallelization decision`) — a dependency-chained plan runs **inline** (`executing-plans`), only genuinely independent tracks fan out as a **concurrent batch**, and one-at-a-time subagent dispatch of implementation work is **banned**. (This is separate from the plan-review reviewer subagent below, which is a rigor dispatch, not an implementation loop. The full recipe is below — see **Executing by structure**.)

> **Reporting override (user-facing).** The `subagent-driven-development` / `executing-plans` skills define a terse status contract for the *controller* (`Status: DONE`, commit SHAs, one-line test summary, concerns, report-file path) and verbose implementer write-ups (full diffs, line numbers, exit codes, "byte-identical" notes). Those are for agent↔agent coordination and the report FILE only — **never surface them to the user.** Whatever reaches the user for any finished task / step / sub-agent is the plain-English relay (see `multi-agent-coordination.md` → "Relaying sub-agent work to the user"): outcomes in words a non-technical reader understands, with the same banned-items list as the completion summary (no diffs, line numbers, file/function names, commit SHAs, test-command output, exit codes, or status-contract labels).

> **Plan-summary override (user-facing).** When you post a plan's key points in chat (per the global "after writing a spec or plan file" rule), write them **in plain English** — the goal, what the feature will let the user *do*, the handful of real decisions, and what's still open. NOT the plan's raw task list: no table/file/function names, code identifiers, or task labels (e.g. "Migration: `learned_sender_categories` table", "`resolveInboundCategory` (learned-map-first)"). The technical breakdown belongs IN the plan file; chat gets the plain-English version a non-technical stakeholder can follow.

## Ceremony by change size (tiers)

Scale the PAPERWORK to the change; **NEVER** scale safety or verification by size. Classify every change before starting:

- **Trivial** — 1-2 files, no meaningful logic change (typo, copy/comment/doc edit, a config value, a version bump). Touches NO risk area.
- **Small** — one focused concern, roughly ≤5 files, a clear approach. Touches NO risk area.
- **Large / Risky** — a feature or multi-concern change, **OR ANY change (however small) that touches** auth, tenant isolation, money/billing, DB migrations/schema, webhooks, a public API contract, or security/secrets.

**Risk overrides size** — a one-line change to any risk area is Large-tier. **When unsure which tier, go up one.** The tiers only ever remove *paperwork* from small, safe work; they can never remove a safety check.

What scales by tier:

| | Trivial | Small | Large / Risky |
|---|---|---|---|
| **Design / plan** | Skip — state intent in one line in chat, then do it | Brief inline plan (a few sentences in chat); **NO separate spec+plan files** | Full brainstorm → spec → plan → plan-review gate |
| **Completion summary** | 1-2 plain-English lines | Terse 5-section (or a few lines if it isn't a formal phase) | Full 5-section |
| **Workboard mid-flight updates** | none (just register + complete) | none needed (register + complete) | Full: `update-status`/`update-path`/`update-files` as scope changes, esp. parallel work |

**This OVERRIDES the superpowers `brainstorming` HARD-GATE** (which demands a design for every project regardless of simplicity) **for Trivial and Small changes**: no separate design/spec/plan file is required — a brief statement of intent in chat suffices. The full HARD-GATE (spec + plan + user approval) still applies to Large/Risky work.

What NEVER scales down — applies at **every** tier:
- **Worktree isolation** for any code change (cheap via the scratch worktree — `scratch_worktree.py`).
- **Commit + push; never leave work uncommitted.**
- **Workboard register + complete** on EVERY change, no exceptions. This is *protective*, not bureaucratic — an unregistered worktree gets swept and its uncommitted work destroyed. It's one cheap command each.
- **Verification scoped to the change** (`code-quality.md`) and **safety rigor scaled to RISK** (the plan-review gate; tenant-isolation / swallowed-error / migration checks). Size never lowers these — *risk* sets them.

## Precision Tiers

A plan has two kinds of content. Be exact about the first; treat the second as disposable.

**Authoritative — state precisely, the executor must honor:**
- The goal and the behavioral contract (what's true when done).
- Interfaces / function signatures / data shapes / API contracts.
- Invariants — especially tenant isolation (`company_id` scoping), security, and error-surfacing.
- The test assertions and the verification gates (build/typecheck/test commands + expected results).

**Illustrative — "as of writing," MUST be re-verified at execution:**
- Exact file paths, line numbers, and line ranges. **Code drifts; line numbers rot.** Plans say *what to find* and *how to find it* (by symbol/content), never "edit line 412."
- Code snippets. A snippet is a **reference implementation, not a transcription target.** The executor re-derives it against the live code: confirm the real symbol names, signatures, imports, and that it compiles — adapt anything that has drifted. Never paste a plan's snippet unchecked, and never assume the plan's code is correct (see the Reviewer Checklist — bugs get authored *into* plans).

Rule of thumb: if a detail would silently break when someone renames a file or refactors a function next week, it's illustrative — mark it so and tell the executor to re-locate/re-verify.

## Authoring Rules

- Lead each task with the **authoritative** part (contract + invariants + test), then provide illustrative code/paths under a "locate by content; re-verify" instruction.
- For every file a task touches, instruct: *find it by content/symbol (grep/Glob), read it, confirm the snippet still matches the current API, then edit.* Do not embed absolute line numbers as edit anchors.
- Keep the skills' bans on genuinely vague placeholders ("add error handling", "TBD"): illustrative ≠ vague. Show the intended code — just don't treat it as gospel.
- Do not over-specify the unknowable: enum values, column names, and signatures that the executor will read from the live schema/code should be stated as "confirm against live" rather than hard-asserted from memory.

## Declaring execution structure (tracks)

Every multi-task plan declares an `## Execution structure` section (near the top, after the header) that groups its tasks into **tracks**. A *track* is a sequential chain of tasks run by ONE worker inline. This makes real the rule in `parallel-agents.md` — "structure plans into independent tracks first."

```
## Execution structure
- Track A (independent): Tasks 1–3      # a chain; runs inline in its own worktree
- Track B (independent): Tasks 4–5
- Track C (after A, B):  Tasks 6–7      # integration; runs once A and B merge up
```

Rules:
- Two tasks share a track ONLY if one consumes the other's output (see the task's Interfaces block) or they touch the same files. Tasks that do neither belong in **separate, independent tracks**.
- State inter-track dependencies explicitly (`after A, B`). Independent tracks carry no `after`.
- A plan that is one dependency chain declares a **single track** — and runs entirely inline. Do not invent parallelism the dependencies don't support.

## Executing by structure (supersedes the SDD serial loop)

Read the plan's `## Execution structure` and run it thus:

1. **One track / a pure chain → INLINE** (`executing-plans`): one warm worker does the tasks in order. **Inline work pays ZERO parallel tax** — no per-task worktree, no `register-subagent`, no report round-trip, no merge-up. Just do it in the current session. (The parallel machinery is engaged ONLY when you actually fan out — see `multi-agent-coordination.md`.)
2. **Multiple independent tracks → CONCURRENT BATCH:** dispatch them in a SINGLE message (true parallelism), up to `maxConcurrentAgents` (4) at a time; **each track runs in its OWN worktree** off the parent feature branch; extra tracks run in the next **wave** as slots free.
3. **Dependent tracks** (`after …`) run only once their prerequisite tracks have merged up.
4. **Merge up two-level:** track branch → parent feature branch → main.
5. **Review once per WAVE** per the risk tier (`multi-agent-coordination.md § Rigor, by risk`) — NOT per task.
6. **Board:** the parent reports each track (`register-subagent` / `complete-subagent`) manually for now; do **NOT** arm the git→board reconciler during batch churn. (Automatic batch tracking is Phase 3.)

Sizing: `dispatch = min(remaining independent tracks, maxConcurrentAgents)`; iterate in waves until done.

**Landmines — never violate:** one worktree per track (never two workers on one branch/worktree — they collapse to one board entry and stomp each other); keep the reconciler disarmed during batches (its noise filter doesn't exclude track branches); change no registration payloads (the emitter-isolation golden test pins them); a batch-completion path must decide worktree cleanup deliberately (`complete` auto-cleans).

## Plan-Review Gate

**Before executing ANY implementation plan, run a plan-review pass.** A plan is reviewed like code — because bugs get written *into* plans and then faithfully copied (real cases: a hardcoded production host and a missing `company_id` filter both shipped verbatim from plan snippets).

- **Lightweight (every plan):** the plan's author re-reads it against the Reviewer Checklist below and fixes findings inline.
- **Full adversarial (mandatory when the plan touches auth, tenant data, outbound URLs, DB migrations, mechanical renames/sweeps over DB-coupled or API-contract strings, money, or webhooks):** dispatch a **fresh reviewer subagent** whose only job is to find defects in the plan's own snippets and assumptions. It does not rubber-stamp.
- **Gate:** do not start execution while any Critical/High plan-finding is unresolved. Fixing the plan before coding is far cheaper than fixing shipped code.

This is in addition to (not a replacement for) reviewing the resulting code after execution.

## Reviewer Checklist

Run each item against the plan's OWN snippets and assumptions:

- **Tenant isolation:** does every DB read/write snippet scope `.eq('company_id', req.companyId)` (or scope through a verified parent for `company_id`-less tables)? Is any company/user id read from `req.body`/`query`/`params` instead of `requireAuth`?
- **Outbound URLs:** any hardcoded host literal? URLs must be built from validated env (frontend host vs API host), never a baked-in domain.
- **Error surfacing:** any DB/client call whose `.error` is ignored — i.e. a path that could fail silently (HTTP 200 with 0 rows, swallowed exception)? Errors must be checked and surfaced.
- **DB constraints:** any `ON CONFLICT`/upsert that could target a *partial* unique index (illegal — use pre-filter + plain insert)? After any rename, does EVERY conflict-target list and RPC parameter key match the LIVE catalog (pg_indexes / pg_get_function_arguments) — not the swept source text? Any `DROP+ADD CHECK`/constraint migration that could clobber values, or a schema change with readers not re-grepped (expand-contract)?
- **Backfills:** for any `UPDATE … SET new = old`, are the affected tables' triggers inventoried (updated_at stampers!) and is there a snapshot-or-disable-trigger step BEFORE the write? A backfill plan without a trigger inventory is incomplete.
- **Route/middleware:** public/unauthenticated routes mounted before blanket auth? CORS allows the expected origins?
- **Stale references:** are file paths/line numbers/code marked illustrative, with a "locate by content + re-verify signatures" instruction — not absolute-line edits?
- **Build/verify gates:** does the plan name the *real* gates (the typechecking build, the live/integration check), not just a bundler that skips type errors? Is there a live/runtime verification step for anything mocked-only tests can't see (route mounting, CORS, real DB constraint semantics, deployed host resolution)?
- **Missing call sites:** if the plan adds a helper, does it enumerate EVERY place that must call it (a unit test of a helper can't catch an un-wired call site)?
