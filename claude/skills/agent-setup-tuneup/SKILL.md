---
name: agent-setup-tuneup
description: >-
  Audit and speed up an AI coding agent's own setup (CLAUDE.md / AGENTS.md / GEMINI.md, hooks,
  plugins, settings, instruction docs). Use when the agent feels slow, over-builds, ignores
  requests, or does a worse job than an out-of-the-box session; when asked to "audit my setup",
  "why is Claude slow", "speed up my agents", or "tune up my instructions"; and after adding
  a batch of new rules or hooks. Runs a read-only audit first and changes nothing until the
  user approves the findings.
---

# Agent setup tune-up

Out-of-the-box agents are fast and good. Setups get slow and worse as rules, hooks and plugins pile up: each mandatory step is an extra round trip, each always-loaded rule competes with the user's actual request, and contradictory rules cost reasoning on every turn. This skill finds what's costing the most and removes it while keeping the guardrails and hard-won lessons that actually prevent damage.

Two stages. **Stage 1 is read-only.** Stop after it and get approval before Stage 2.

## Stage 1: Audit (read-only)

### 1. Inventory what loads, and when
Read the live files, not a backup copy. For Claude Code that's `~/.claude/`; also check the project's `.claude/`.

| What | Where | Cost model |
|---|---|---|
| Global + project instructions | `~/.claude/CLAUDE.md`, `./CLAUDE.md`, `./.claude/CLAUDE.md`, every `@import` they pull in; `~/.codex/AGENTS.md`; `~/.gemini/GEMINI.md` | In context **every turn** |
| Settings | `~/.claude/settings.json` and project `.claude/settings.json`: `effortLevel`, `modelSettings.<model>.effortLevel`, `model`, `outputStyle`, `hooks`, `enabledPlugins` | Effort = thinking time on every turn |
| Hooks | `hooks` in each settings file | A process **per matching event** (every Bash call, every edit, every prompt) |
| Plugins | `enabledPlugins` (user level = **every project**) | Skill/agent descriptions every session; **plugin hooks always run** (e.g. SessionStart text injection) |
| Output style | `outputStyle`, `~/.claude/output-styles/` | Sent with every request |
| Docs the rules say to read | anything "read X before Y" points at | Extra tool calls + context each time it triggers |

Measure: `wc -c` of each always-loaded file; count imperatives (`grep -oiE '\b(MUST|NEVER|ALWAYS|MANDATORY|BANNED|REQUIRED)\b' | wc -l`); count hook commands per event; list enabled plugins and which ones ship hooks or MCP servers.

### 2. Classify every rule and hook
Tag each one. The first four are what make agents slow or worse:

1. **Mandatory step on every task**: a ritual that turns into tool calls (register on a board, create a worktree, fetch, update a tracker item-by-item, announce steps). *Biggest speed cost: round trips multiply.*
2. **Mandatory reading**: "read doc X before any Y", especially when one task matches several rows of an index.
3. **Over-build bias**: "robust over minimal", "architect every feature as a layer", mandatory audits or reviews on every change. *Biggest quality cost: bigger diffs, scope creep.*
4. **Contradiction or stale source**: two rules that disagree (e.g. "use the fast Read tool" vs "read code via `git show origin/main`"), or a rule that makes the agent read something other than the user's real local code. Conflicts with plugins count too (e.g. a plugin that forces brainstorming vs a rule that says skip it).
5. **Situational guardrail**: fires only in a specific situation (DB write, push to main, merge conflict, migration naming). *Cheap: keep.*
6. **Hard-won lesson**: a rule backed by a real incident ("this caused a 43h outage"). *Keep, compressed to one line.*
7. **Default behaviour**: something the agent already does untold (read before editing, follow existing patterns). *Delete.*

For hooks, also note: processes per tool call, hooks that run git/network on every call, hooks that deny with a long "playbook" (the agent follows the playbook instead of the task), hooks that nag every turn, and hooks that enforce rules you're about to retire.

### 3. Check the plugins specifically
- User-level `enabledPlugins` apply to every project. Project-specific ones (payments, a particular DB, an email provider) belong in that project's `.claude/settings.json`.
- Find plugins with **SessionStart hooks** that inject instructions (known examples: `superpowers` injects "you MUST use skills / brainstorm first"; `explanatory-output-style` injects 2–3 insights before and after code; `hookify` hooks every tool event). These cost every session whether used or not.
- Skills only load their name + description until used, and MCP tool definitions are deferred, so those are cheap. Hooks are not.

### 4. Report, ranked, then stop
Rank findings by **how often it fires × how much it costs**, worst first. For each: file:line, what it makes the agent do, why that's slower and/or worse, and the recommended fix. Then list what you'd **keep** (guardrails and hard-won lessons). End by asking which fixes to apply. Change nothing in this stage.

Don't overstate: you're reading files, not timing real sessions. Say so, and point to any timing logs the setup has.

## Stage 2: Fix (after approval)

Work cheapest-first, commit as you go, and keep everything reversible.

1. **Settings toggles.** Effort to `medium` (note: the model picker saves effort per model under `modelSettings`; the top-level `effortLevel` is legacy on newer models). Disable plugins that inject text every session. Move project-specific plugins to project settings. Replace a verbose output-style plugin with a short custom style in `~/.claude/output-styles/` (front matter: `name`, `description`, `keep-coding-instructions: true`).
2. **One short shared instruction file.** Aim for ≤ ~60 lines, plain rules, no ceremony.
   - Claude: `@~/.ai-instructions/core.md` in CLAUDE.md.
   - Gemini: `@../.ai-instructions/core.md` in GEMINI.md (or `context.fileName`).
   - Codex can't import: AGENTS.md tells it to read the file once per session.
   - Express the speed/quality balance as: *"the smallest change that fully and correctly solves it: fix the root cause, handle the edge cases that can really happen, follow existing patterns; don't add what nobody asked for."*
   - Keep: verification before "done", question ≠ request to act, undo failed attempts, safety asks (main, secrets, live DB writes, paid/irreversible actions), plain-English reports sized to the task.
   - Point to detail docs **only for risky areas** (security, DB, schema changes, UI, customer-facing text), and say "read only the section you need".
3. **Keep the hard-won lessons.** Before deleting any doc, grep it for incident language ("outage", "caused", "lost", "silently") and carry those rules into the core file as one-liners. Example lessons worth keeping: check `error` on every Supabase/PostgREST call (`{data:null, error}` doesn't throw); regression test for bug fixes; never edit a test just to pass; mocked tests don't prove DB/routing/deploy behaviour; never silently drop either side of a merge conflict; timestamp migration names for parallel agents.
4. **Hooks.** Keep fast safety gates (regex checks are cheap). Remove hooks that enforce retired routines, inject reminders every prompt, or nag every turn. Fix false alarms (e.g. `[ -d .git ]` is wrong in a worktree, where `.git` is a file: use `-e`).
5. **Docs.** Retire docs nothing points to anymore. Fix links in the docs you keep. Trim oversized ones to their essentials.
6. **Parallel work.** Replace custom coordination systems with built-ins: `claude --worktree`, subagent `isolation: worktree`, and one rule ("only one agent merges into main").

**Don't destroy the user's work.** These files often took real effort. Before deleting, say it's recoverable (git history, backups) and offer archiving to a non-loaded folder as an alternative.

**Mind the sync setup.** Find the source of truth before editing: a private config repo with a public export, live home files with `capture`/`deploy` scripts, etc.
- Edit the source of truth, or the next export/deploy will undo your work.
- Deploy scripts usually copy without deleting, and capture scripts copy live files back. So deleted files must be removed from the live home folders too.
- Update any allowlist/manifest for new and removed files.
- Never commit machine-specific settings or credentials to a public repo.

## Verify
- Tests, JSON parse of every settings file, `bash -n` on changed shell hooks.
- Run hooks once in the situations they target (including inside a worktree).
- **Fresh-session self-check.** A new session, not an old one that still remembers the previous rules. Paste:
  > Don't change anything. Report: (1) quote the first rule in your loaded instructions; do you see any rules about a workboard, mandatory worktrees, reading from origin/main, or brainstorm→spec→plan before coding? (2) active output style; (3) any skills from superpowers or hookify? (4) run `echo test`: did any hook output appear? (5) which plugins does this project's `.claude/settings.json` enable?
- **Real-task test.** In another fresh session, give 2–3 ordinary tasks without mentioning testing: a question, a small bug fix, a small UI change. Compare time, steps before the first edit, change size and summary length with the old setup.
