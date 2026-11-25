---
name: agent-setup-tuneup
description: >-
  Audit and speed up an AI coding agent's own setup (Claude Code, Codex, Gemini): instruction files,
  hooks, plugins, settings, and the workflow measured from session logs. Use when the agent feels
  slow, over-builds, or does a worse job than an out-of-the-box session; when asked to "audit my
  setup", "why is Claude slow", "speed up my agents", "are my hooks slow", or "tune up my
  instructions"; and after adding a batch of rules or hooks. Stage 1 is a read-only, measured,
  ranked report; Stage 2 applies only the fixes the user approves. Works on Windows, macOS, Linux, WSL.
---

# Agent setup tune-up

Out-of-the-box agents are fast and good. Setups get slow and worse as rules, hooks and plugins pile up:
- each mandatory step is an extra round trip
- each always-loaded rule competes with the user's request
- contradictions cost reasoning on every turn
- and the biggest loss is usually the workflow itself: sessions sitting idle waiting on the user

This skill measures where the time goes, removes what costs the most, and keeps the guardrails and hard-won lessons that prevent real damage.

You are auditing **this user's** setup. Every finding must come from what you observe or measure here. Never assume their stack, OS or habits.

## Ground rules (tell the user in one or two sentences before starting)
- **Stage 1 is read-only.** Read settings, hooks and instruction files, and time a few harmless commands. Change nothing until the user approves specific fixes.
- **Nothing leaves the machine.** Don't send files or probe output to any external service. The scripts never read MCP server args/env, which can hold secrets.
- **Honest numbers.** Label every number **measured** (timed here), **estimated** (calculated or a rule of thumb), **observed** (seen in a file or process list) or **candidate** (a pattern a human must confirm).
- **Antivirus and security software is theirs to change.** Recommend; never change it.
- **Don't invent problems.** If the setup is already lean, say so plainly and keep the report short. A clean bill of health is a valid result.
- **Shared files affect teammates.** A project's committed `.claude/settings.json`, `CLAUDE.md` or `.mcp.json` applies to everyone on that repo. Recommend personal changes in personal files (`~/.claude/`, `.claude/settings.local.json`), and flag any fix to a shared file as "affects your team: agree it first".

## Stage 1: Audit (read-only)

### 1. Run the probe (machine + config)
`scripts/probe.py` in this skill's folder uses the Python 3.8+ standard library only. Find a Python: `python3`, `python`, then `py`.
```bash
<python> "<skill folder>/scripts/probe.py" --project "<project dir>" --out "<temp dir>/probe.json"
```
It takes ~10-30 s. Use its `findings` (pre-sorted) as a starting list. It also reports:
- `timing`: the spawn floor, the hook shell, interpreter start-ups and `git status`
- `hooks.items`: every hook from every settings layer and enabled plugin
- `instructions.files`: what loads every session, with estimated tokens
- `mcp`, `plugins_enabled` and `environment`

No Python? Use the manual fallback in `reference/checklist.md`.

**Optional, ask first:** "Want me to also time your actual hooks? I'd run each tool-call and prompt hook once with a harmless fake input. Hooks that end a turn can run tests, so those only run if you say so separately."
- Yes → re-run with `--run-hooks`.
- Separate yes → also add `--include-stop`.

### 2. Measure the workflow from session logs (usually the biggest finding)
Machine latency is seconds; the workflow is hours. In one real 2-week audit, sessions sat ~150 daytime hours waiting on the user, as long as they spent working.
```bash
<python> "<skill folder>/scripts/session_audit.py" --days 14 [--project <substring>] --out "<temp>/sessions.json"
```
It is read-only and prints only tool names, counts and durations. Interpret it like this:
- **Waits on the user.** Report the **capped** and **daytime-capped** numbers, never the raw total, because sessions overlap and waits count overnight. Give counts per release (`pushes_to_main`, approximate) and the median wait.
- **Permission prompts.** Five or more rebase/merge prompts per release means branches merge main repeatedly instead of rebasing once.
- **Checks.** Look at `hours_from_over10m`: a few slow outliers usually explain most of the total. `scoped_vs_whole` shows whether checks are scoped to what changed.
- **Subagents.** More than one review per release is over-reviewing for ordinary work.
- **Slow MCP calls.** The "unexplained" bucket means the permission mode asked. See the root-cause catalogue.
- **Per-project tools.** These can't see hook-based or style plugins, so "unused in the scan" never means "unused".

### 3. Inventory and classify the instructions (judgement, not measurement)
Read every always-loaded file:
- global and project CLAUDE.md / AGENTS.md / GEMINI.md, plus every `@import`
- the memory index
- the output style
- any doc a rule says to "read before X"

Check settings at **every layer**: user, user-local, project `settings.json`, **and each project's `settings.local.json`**. Local and project override user, so a plugin turned off globally, an effort level, or `permissions.defaultMode` can be quietly switched back per project. Check every project, not just one. Flag invalid permission rules (e.g. a bare `mcp__*`), which are silently ignored.

Tag every rule. The first five make agents slow or worse:
1. **Mandatory step on every task.** A ritual that becomes tool calls: register on a board, create a worktree, fetch, update a tracker item by item, announce steps. *Biggest speed cost.*
2. **Mandatory reading.** "Read doc X before any Y", especially when one task matches several rows of an index.
3. **Over-build or over-verify bias.** "Robust over minimal", "architect every feature as a layer", a full build or suite after every edit, mandatory audits or reviews on every change. *Biggest quality cost.*
4. **Contradiction, stale source, or doc-vs-reality.** Two rules that fight; a rule that reads something other than the user's local code (e.g. `git show origin/main`); a rule claiming a hook does something no hook does; conflicts with plugins (e.g. one that forces brainstorming).
5. **Waiting behaviour.** Questions one at a time, idling while a prompt is open, proceeding without batching.
6. **Situational guardrail.** Fires only in a specific situation: DB write, push to main, merge conflict, migration naming. *Cheap: keep.*
7. **Hard-won lesson.** Backed by a real incident ("this caused a 43h outage"). *Keep, compressed to one line.*
8. **Default behaviour.** The agent already does it untold (read before editing, follow patterns). *Delete.*

Ask before speeding a routine up whether it should exist at all.

Check plugins for **SessionStart hooks** that inject text every session; these cost whether used or not. Known examples:
- `superpowers`: "you MUST use skills / brainstorm first"
- `explanatory-output-style`: 2-3 insights before and after code
- `hookify`: hooks every tool event

Skill descriptions and deferred MCP tool names are cheap by comparison.

### 4. Verify every candidate before reporting it
The scripts pattern-match; you confirm.
- **`heavy-hot-hook`:** open the script. Is it already scoped, debounced or cached?
- **`dead-matcher`:** compare against the tool names you actually have (e.g. `Task` became `Agent`). A hook that never fires is a check the user *thinks* runs.
- **`spawn-floor`:** a spiky p90 with a normal median suggests antivirus. Re-run if borderline.
- **`leaked-processes`:** list what they are before blaming them.

Then go through **every row** of `reference/checklist.md` and its root-cause catalogue.

### 5. Report, ranked, then stop
Write it in plain English for someone who may not know what a "hook" is, and explain terms the first time they appear. Rank by **how often it fires × how much it costs**. Use this structure:
```
# Agent setup audit - <date>, <OS>
## The short version          (2-3 sentences: overall health, the single biggest win)
## Where the time goes        (from session logs: working vs waiting hours (capped), per-release counts, outliers)
## Biggest wins               (max 3: what's happening / evidence + label / exact fix / saving with the maths)
## Other findings             (same shape, high -> low, with file:line)
## Questions about your rules (quote the line; ask if it's intended. Their rules may be deliberate)
## Keep                       (guardrails and hard-won lessons worth keeping)
## Looked fine                (short)
## Not checked                (what the audit couldn't see)
```
Make savings concrete and labelled: *"~40 ms × ~150 tool calls ≈ 6 s per session (estimated, assumes 150 calls)."* Offer to save the report as Markdown. **Then stop and ask which fixes to apply.**

## Stage 2: Fix (only what the user approved)
Read `reference/fixes.md` first. Work cheapest-first, commit as you go, keep everything reversible. In order:
1. **Settings toggles:** effort, permission mode, plugins that inject text, project-specific plugins moved to project settings, output style.
2. **Instructions:** one short shared instruction file. Before cutting a rule, check it against what Stage 1 measured.
3. **Hard-won lessons:** carry them over before deleting any doc.
4. **Hooks:** keep fast safety gates; remove enforcers of retired routines, every-prompt reminders and every-turn nags; fix false alarms.
5. **Docs:** retire unreferenced docs, fix links in kept ones, trim oversized ones.
6. **Workflow:** never idle while waiting, scope checks, budget reviews, replace parallel-work machinery with built-ins.

Before deleting anything:
- Offer to archive rather than delete; the files often took real effort.
- Find the source-of-truth repo (private config vs public export; live files vs capture/deploy scripts) and edit that.
- Remove retired files from the live folders too.
- Never commit credentials or machine-specific settings to a public repo.

## Verify
- Run the tests, JSON-parse every settings file, run `bash -n` on changed shell hooks, and run each changed hook once in the situation it targets (including inside a worktree).
- **Fresh-session self-check.** Use a brand-new session in the right tool (Claude Code, not Codex) that doesn't remember the old rules. Paste:
  > Don't change anything. Report: (1) quote the first rule in your loaded instructions; do you see any rules about a workboard, mandatory worktrees, reading from origin/main, or brainstorm→spec→plan before coding? (2) active output style; (3) any skills from superpowers or hookify? (4) run `echo test`: did any hook output appear? (5) which plugins do this project's `.claude/settings.json` and `settings.local.json` enable?
- **Real-task test.** In another fresh session, give 2-3 ordinary tasks without mentioning testing: a question, a small bug fix, a small UI or wording change.
  - Compare with before: time, steps before the first edit, change size, summary length, interruptions and cost.
  - Re-run `session_audit.py` after a week to confirm the waiting time actually dropped.
