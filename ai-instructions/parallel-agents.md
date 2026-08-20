---
name: parallel-agents
description: The canonical decision for WHETHER to parallelize any unit of work — the three-gate test (independent? substantial? headroom?), when to fan out as a concurrent batch vs. run inline, and why sequential subagent dispatch is banned
when_to_read: before dispatching ANY subagent, or when deciding whether to split any work across agents; CLAUDE.md carries the compact always-on version and points here for the full model
sections:
  - The parallelization decision
  - Why (the two facts that make this true)
  - Finding the parallel axis
---
# Parallel Work — The Decision

> Navigation: this is the canonical home for *whether* to parallelize any unit of work. `CLAUDE.md` carries the compact always-on version; this doc carries the full model. Worktree/merge mechanics live in `worktree-operations.md`; coordination machinery (WORKBOARD, register-subagent, merge ordering) in `multi-agent-coordination.md`.

## The parallelization decision

At the moment ANY unit of work *could* be split across agents, apply three gates IN ORDER:

1. **Independent?** Do the pieces avoid consuming each other's output and avoid writing the same files? A dependency chain (schema → API → UI → tests) FAILS — it cannot be parallelized. Only genuinely separate pieces pass.
2. **Substantial?** Is each piece's real work larger than an agent's cold-start (spin-up + full context re-load)? Speed-first ⇒ a LOW bar: only trivially tiny pieces (a one-line edit, a lookup) fail.
3. **Headroom?** Fan out to at most the cap from `py ~/.ai-instructions/tools/headroom.py` — in `live` mode it sizes to CURRENT machine load (more when idle, fewer when busy); in `static` mode it is the fixed `maxConcurrentAgents`. Set in `~/.ai-instructions/parallelization.json` (see `CLAUDE.md`).

| Situation | Action |
|---|---|
| Fails gate 1 (chained) | **Inline, one warm agent** — no subagent |
| Passes 1, fails 2 (tiny) | **Inline** |
| Passes 1 + 2, more pieces than headroom | **Concurrent batch**, sized to the limit; remainder queued |
| One task too big for a single context window | **Solo subagent for isolation** (the only no-parallelism dispatch allowed) |

**BANNED: one-at-a-time (sequential) subagent dispatch.** If work is not a concurrent batch and not context-window isolation, do it INLINE. Dispatching one subagent per task in sequence pays full overhead for zero parallelism — it is never faster than inline and usually slower.

> **Scope — this governs dispatching *implementation/work* for speed.** Independent *review or verification* (an adversarial plan reviewer, a code reviewer on risky logic, the plan-review gate) is a separate **rigor** decision about correctness-via-fresh-perspective, governed by the risk tiering in `multi-agent-coordination.md` (`## Rigor, by risk`) — NOT by this test. A single independent reviewer is always allowed where the risk tier calls for one; it is not the banned sequential-implementation loop.

> **Once you've decided:** HOW to execute (a chain inline vs. independent tracks as a concurrent batch) is the recipe in `planning.md` → "Executing by structure". This doc decides *whether*; that recipe decides *how*.

## Why (the two facts that make this true)

- **Parallel lowers wall-clock but raises total token cost.** Fifteen agents each re-load context; you don't *wait* for fifteen loads (they overlap), but you *pay* for them. We accept this because we optimize for speed.
- **Overhead is per-agent and only cheap when it overlaps.** Cold-start + context reload is worth paying when it runs concurrently (true parallel) AND the task dwarfs it (substantial). Sequential dispatch is the case where the overhead stacks with nothing to offset it — that is the pattern this model exists to kill.

## Finding the parallel axis

Independence is usually found ACROSS units (features, files, modules, review angles), rarely WITHIN one unit's steps. "Build features A, B, C" that don't touch each other → 3 parallel tracks; but each feature's own schema → API → UI → tests stays a sequential chain. Structure plans into independent *tracks* first; if the work is one dependency chain, it does not parallelize — do not fake it with subagents.
