# Audit reference: checklist, root causes, reference numbers

Read this during Stage 1. Go through **every** checklist row, not just the headline items. Normalise counts **per release** (per push to main) so they mean something. Fields in `code` come from `scripts/session_audit.py` unless marked manual.

Before suggesting how to make a routine faster, ask whether it should exist at all. Several "usual fixes" below (warm worktrees, ship scripts, number allocators, bookkeeping hooks) only matter in a worktree-per-change workflow. In a real rollout, retiring that workflow beat every speed-up made *to* it.

## Root-cause catalogue (verified in real audits; check each before claiming it)

| Symptom | Likely cause | How to verify |
|---|---|---|
| Every command pays ~0.5-1 s; `python -c pass` 400-900 ms on Windows | Antivirus scanning the interpreter on each launch (hooks are Python) | Time 5 starts; after the user adds a Defender folder exclusion for the Python install dir, re-time. Real result: ~500 ms -> ~100 ms. Reading the exclusion list needs admin. |
| DB *reads* wait minutes on a prompt | The DB hook only says "ask" for writes and **exits silently on reads**, which hands the call to the permission mode, and that asks | Read the hook: does it emit an explicit `allow` for reads? Count `unexplained` slow MCP calls |
| Reads asked as writes | The hook's write-word list includes words that also appear in reads (e.g. `REPLACE` for the `replace()` function, write words inside quoted text) | Replay real queries through the hook's classifier |
| DB writes "declined" instantly in background/non-interactive sessions | A hosted connector (e.g. the claude.ai Supabase connector) shows its **own** confirmation (MCP elicitation); with no UI it auto-declines | The other connection (e.g. a local MCP server) succeeds. Route writes through the connection without elicitation; the local hook still asks |
| Typecheck averages minutes | Fresh worktrees with cold caches, whole-workspace runs | `hours_from_over10m` vs total; scoped vs whole counts |
| Same investigation repeated by agents | Flaky tests / breakages on main with no shared record | Look for a known-issues list; check CI runs tests on main |
| Migration "renumber" commits | Parallel agents picking the same next number | `git log --oneline \| grep -i renumber` |
| A plugin you turned off is still active | A project's `.claude/settings.local.json` or `settings.json` re-enables it (local and project override user) | Check both files in **every** project |
| Startup check reports "0 GB free" on a full-ish disk | The check calls `wmic`, removed on current Windows 11 | Run the check's command by hand |
| Hook "CRITICAL .git destroyed" inside worktrees | `[ -d .git ]`, but in a worktree `.git` is a file | Run the hook inside a worktree |
| A permission rule seems ignored | Invalid rule syntax (e.g. bare `mcp__*`) is silently dropped | Compare against the documented permission-rule format |

## Full slowdown checklist

Each row: what to look for -> evidence -> the usual fix. Report every row that applies; list the rest under "Looked fine".

**Waiting on the user**
1. Questions asked one at a time (brainstorming/planning skills do this by design) -> `waits_on_user.questions` per release + median wait -> one batched multiple-choice round at plan time; proceed on the recommendation for reversible choices; stop asking a kind of question the user always answers with the default.
2. Permission prompts on routine git -> `waits_on_user.permission_prompts`, `integration_churn` -> prompt-free exact-form feature-branch rebase; one "ship" approval.
3. Agents idle while a prompt is open -> waits vs `agent_active_h` -> launch independent work (background agents/commands) BEFORE asking; batch gated steps into one approval at the end.
4. Blocked sessions waiting silently -> `phone_notifications_sent` ~0 -> Remote Control + "push when actions required" so blocked sessions reach the phone.
5. Prompts auto-refused in sessions that can't show them (hosted MCP elicitation, non-interactive sessions) -> MCP results saying "declined" -> route writes through a connection without elicitation; never retry a decline.

**Checks and reviews**

6. Typecheck slow -> `checks.typecheck.hours_from_over10m`, `scoped_vs_whole` -> scoped to the touched package, timeout, once per change + once after rebase.
7. Full test suite locally -> `checks.tests`, `scoped_vs_whole.tests` -> scoped while working; full suite in CI.
8. Lint rarely run but the deploy fails on lint -> `checks.lint` vs typecheck -> lint changed files every change (seconds).
9. Redundant local production builds -> `checks."local build"` -> drop unless build config / dependencies changed; the host and CI build anyway.
10. Too many / too long independent reviews -> `subagents.review` per release, median -> none for small work; one plan + one code review for risky work; narrow brief; time budget.
11. Re-investigating known failures (flaky tests, other agents' breakages) -> repeated test runs, no known-issues doc, CI without tests -> known-issues list; CI runs tests on main; fix or quarantine flakes at the root cause.

**Merging and deploying**

12. Merging main into feature branches repeatedly -> `integration_churn` -> rebase only; short-lived branches.
13. Main moves mid-merge -> repeated rebase+recheck loops before a push -> one "ship" step that retries automatically; land promptly.
14. Waiting on deploys -> `deploy_waiting` -> don't poll; background-wait and do other work; batch releases.
15. Migration-number collisions -> renumber commits -> timestamp migration names (or an allocator).
16. Main working tree drifting behind origin (stale reads, merge refusals) -> manual: `git -C <repo> rev-list --count HEAD..origin/main` -> fetch before new work; keep the main tree clean.

**Cold starts and housekeeping**

17. Fresh worktree per task (reinstall, cold caches) -> `cold_starts."worktree created"`, `checks.install` -> single-agent work in the normal checkout; worktrees only for parallel agents.
18. Dev server cold start (first page compile can take ~3 min) -> `cold_starts."dev server started"` -> keep one dev server warm per repo during a session.
19. Slow worktree deletion on Windows (node_modules) -> `worktree_cleanup.over5m` -> run cleanup in the background; exclude dev folders from antivirus (the user's call).
20. Rule-driven bookkeeping (work-board/todo updates, mid-task status notes) -> `busywork` per release -> drop the rule, or automate via a hook; keep only start/finish.
21. Paperwork: spec + plan + summaries for medium work -> `paperwork` -> spec only when the agent needs it; the user gets a plain-English chat summary.

**Friction from safety rules**

22. Hooks blocking legitimate commands -> `friction_hook_blocks` (each block = a retry) -> for the top reasons, either teach the rule in the instructions (so agents write it right first time) or narrow the hook. Example: a "no piping into tail" rule blocked 126 commands in 2 weeks.
23. Hooks that deny with a long remediation "playbook" -> read deny messages -> the agent follows the playbook instead of the task; shorten to one line.
24. Fix -> review -> fix loops on safety code -> many review rounds on one file -> prefer exact allow-forms over clever parsing; additive changes only; set a round limit.

**Context size**

25. Always-loaded context (instructions, memory index, tool names, MCP instructions, skill list, duplicate integrations) -> manual sizes + `per_project_tools` -> short core file; memory tiers; per-project tool lists; one connection per service.
26. Context growing inside sessions -> `big_tool_outputs_over20k_chars`, `session_length_turns.p90`, `compactions` -> read file slices; send big output to a file and read back only what matters; big searches via a subagent; one session per feature.
27. Small foreground subagents (cold start costs more than the task) -> `small_foreground_subagents_under2m` -> do small or sequential work inline; delegate only independent, substantial work, in parallel.
28. Output style adding text every reply -> `explanatory_insight_blocks` -> fine if the user wants it; mention the cost, let them choose.
29. Plugins injecting text at every session start (SessionStart hooks) -> probe `hooks.items` with `source: plugin:*` on `SessionStart` -> disable, or keep only where wanted.

**Machine**

30. Antivirus scanning the interpreter -> probe `launchers` / `spawn_floor` -> folder exclusion (the user's change). Real: ~500 ms -> ~100 ms per Python start.

## Reference numbers (typical ranges, not guarantees)

| Thing | Usually fine | Worth a look |
|---|---|---|
| Start a trivial process | Windows 10-30ms; macOS/Linux 1-5ms | median above that, or p90 > 2.5x median |
| `python -c pass` | 20-50ms | > 80ms (or the Windows `py` launcher adding 20ms+) |
| `node -e 0` | 40-80ms | > 150ms |
| `bash -c true` (Git Bash on Windows) | 20-60ms | > 80ms |
| `powershell` without `-NoProfile` | - | profile adds 100ms-1s+ |
| `npx`/`uvx` package runner per call | - | often 300ms-2s+ |
| `git status` | < 300ms | > 800ms |
| Always-loaded instructions | < 8k tokens (a good slim setup is ~1.5k) | > 20k tokens |
| Questions / permission prompts per release | 0-2 | > 5 (each waits a median ~6-9 min when the user is around) |
| Daytime hours idle on the user vs hours working | < 25% | ~100% (seen in a real audit) |
| Independent reviews per release | 0-1 | > 2 |
| Typecheck runs > 10 min | none | any (cold cache / whole workspace / hung) |
| Effort level on every turn | medium | high / xhigh / max as the default |

Hooks registered on the same event run **in parallel**, so the wait for one event is roughly the slowest matching hook, not the sum. But each is a fresh process competing for CPU, so many small hooks still cost something.

## Manual fallback (no Python)

Collect the same facts by hand:
- Settings: `~/.claude/settings.json`, `~/.claude/settings.local.json`, `<project>/.claude/settings.json`, `<project>/.claude/settings.local.json`; enabled plugins' hooks in `<plugin installPath>/hooks/hooks.json` (paths in `~/.claude/plugins/installed_plugins.json`).
- Instructions: `~/.claude/CLAUDE.md`, every `CLAUDE.md` / `CLAUDE.local.md` from the project folder up to the repo root, files they `@import`, and `~/.claude/projects/<project path with non-letters replaced by '-'>/memory/MEMORY.md`.
- Timing: run each 5+ times and take the middle value.
  - macOS/Linux: `time (for i in 1 2 3 4 5; do /bin/true; done)`, same for `python3 -c pass`, `node -e 0`.
  - Windows PowerShell: `1..5 | % { (Measure-Command { cmd /c exit 0 }).TotalMilliseconds }`, same for `python -c pass`, `node -e 0`, `bash -c true`.
