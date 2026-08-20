---
name: hooks-reference
description: Descriptive reference of global and project-level hooks (what blocks/warns/reminds)
when_to_read: when asked about hooks, when a hook fires unexpectedly, or when configuring project hooks
sections:
  - Global hooks
  - Project-level hooks
---
# Hooks Reference
> Navigation: read this frontmatter + section list, then `grep` the `##` heading you need and read only that section. Do not read the whole doc.

## Global hooks

### Global Hooks (apply to ALL projects via `~/.claude/settings.json`)

**PreToolUse — Blocks and confirmations:**

| Hook | Matcher | What it does |
|------|---------|-------------|
| `pretooluse_bash.py` | Bash | **Blocks:** `python`/`python3` (use `py`), `git push` to main/master, `git add` on sensitive files, ALL `git worktree remove` (must use cleanup script), ALL `rm -rf` on worktree paths including node_modules (must use cleanup script), merges in main working tree, ANY mutating `git stash` (bare/push/pop/apply/drop/clear) in a repo with linked worktrees — shared stash stack; `stash list`/`show` still allowed. **Asks:** `git push` to main, `git commit` on main/master, `git merge`, `git update-ref` to main/dev, `git branch -d` on unpushed branches, `git stash drop`/`clear` in single-worktree repos, `git rebase`/`cherry-pick`, non-read SQL (`execute_sql.py`, `psql`, or `supabase db` writes; SELECT / dump / diff stay silent). Also checks WORKBOARD.md for active agents before merges to main. **Rewrites (via `updatedInput`):** every `git commit` gets a per-agent-session author identity injected (`-c user.name="Developer (Claude agent-<id>)" -c user.email="you+agent-<id>@example.com"`, `<id>` = short hash of the hook payload's `session_id`, `.sub<hash>` appended when `agent_id` is present i.e. inside a sub-agent) plus an `Agent-Session:`/`Agent-Task:` trailer appended to the last `-m` message. Survives rebase (rebase preserves AUTHOR, only committer changes) — `git shortlog -sne --all` / `git log --author=agent-<id>` separate concurrent agents' work after landing on main. Only this hook may rewrite Bash `updatedInput`; `pretooluse_git_drift_check.py` (the other Bash-matched hook) never does, so there's no "last writer wins" conflict. Heredoc/`-F`-sourced commit messages get the `-c` identity flags but not the trailer (rewriting an arbitrary heredoc safely isn't attempted). |
| `pretooluse_bash_confirm.py` | — | **Disabled (no-op).** Deprecated 2026-07 and unregistered in `settings.json`; the file is kept only as a fail-safe no-op. `pretooluse_bash.py` above is the sole Bash confirmation gate (single-confirm; no second prompt). |
| `pretooluse_code.py` | Edit, Write | Blocks hardcoded Stripe secret keys, AWS access keys (`AKIA...`), `eval()`, `debugger` statements. |
| `pretooluse_main_worktree_guard.py` | Edit, Write | **Denies** edits to code files in the main working tree (all code changes must happen in worktrees), returning a worktree-migration playbook as the deny reason. Allows non-code files (WORKBOARD.md, MEMORY.md, plans), files under the OS temp dir (session scratchpads), the HOME dotfiles repo (global config), and any target outside the CWD's repo. |

**PostToolUse — Reminders (printed after tool completes):**
| Hook | What it does |
|------|-------------|
| `posttooluse_write.py` | After writing a SQL migration file, reminds to refresh schema cache |
| `posttooluse_bash.py` | After `git worktree add`, reminds to install dependencies (`npm install`) |

**Stop — Warnings (printed after every response):**
- **Branch guard** — Warns if you have uncommitted code changes directly on main/master. Create a feature branch.
- **WORKBOARD check** — Warns if code files changed but no `WORKBOARD.md` exists. Reminds to create one for multi-agent coordination.
- **Debug code scan** — Scans `git diff` for `console.log` and `debugger` left in changed lines. Warns if found.

**SubagentStop — Warnings (printed after each subagent completes):**
- **Worktree check** — Detects worktrees not referenced as active/waiting in `WORKBOARD.md` and suggests cleanup. Skips worktrees that have active entries in WORKBOARD.md.
- **WORKBOARD reminder** — Reminds to move completed subagent work to the Completed section.

### Companion tool: `whoami_agent.py`

Not a hook — a standalone lookup an agent runs to learn its OWN `agent-<id>` before its first commit exists (e.g. to pass to `workboard.py register --agent-session`). Computes the identical hash `pretooluse_bash.py` uses (`sha1($CLAUDE_CODE_SESSION_ID)[:6]`), read from the `CLAUDE_CODE_SESSION_ID` env var (confirmed populated in every Claude Code Bash subprocess, including sub-agents).

```bash
py ~/.ai-instructions/tools/whoami_agent.py
# -> agent-5a9c3e
```

**Limitation:** `CLAUDE_CODE_SESSION_ID` is IDENTICAL between a parent session and any sub-agent it spawns (verified live) — there is no environment variable exposing the sub-agent-distinguishing id (`agent_id` in the hook's stdin payload has no env-var equivalent). So a sub-agent running this script gets its PARENT's `agent-<id>`, not its own finer `.sub<hash>` suffix. That finer distinction still exists in git history (the hook sees `agent_id` and applies the suffix to commits it makes) — it's just not derivable standalone. For WORKBOARD.md purposes this is fine: sub-agents register under the same `agent-<id>` as their parent, since the board already nests sub-agents under their parent entry.

## Project-level hooks

### Project-Level Hooks (set per-project in `.claude/settings.json` or `.claude/settings.local.json`)

These vary by project and must be configured separately:
- **Build/typecheck** — Should run on Stop. Command depends on the project (e.g., `npx tsc --noEmit`, `npm run build`).
- **Lint** — Should run on Stop. Command depends on the project (e.g., `npm run lint`).
- **Hub file guard** — Optional SubagentStop hook that checks if a subagent modified hub files listed in `PROJECT_SCOPE.md`.

If a project has no project-level hooks configured, you MUST run build and lint checks manually after writing code (see `~/.ai-instructions/code-quality.md`).

### A check hook must never LIE — timeouts and honest exit codes

Any hook that runs a build/typecheck/test/lint has TWO ways to silently report
success while the check actually failed. Both were found live and both are
forbidden:

1. **Missing `timeout` → the check gets killed and read as a pass.** A cold
   `tsc`/`vitest`/`build` routinely takes 60-180s (more under concurrent-build
   contention). The hook framework's default timeout is far shorter, so it kills
   the check mid-run and the turn proceeds as if it passed. EVERY check hook MUST
   set an explicit, generous `timeout` (e.g. `"timeout": 600`) sized to a COLD
   run, not a warm one.
2. **Piping the check through `tail`/`head`/`grep` → the pipeline exit code is the
   filter's (always 0), so real failures are swallowed.** This is the worst kind:
   the check runs, finds errors, and STILL exits 0 — it has never once caught a
   failure. NEVER write `npx tsc --noEmit ... | tail -20`. If you must trim
   output, preserve the real status:
   - `set -o pipefail` at the top of the `bash -c` body, **or**
   - check `${PIPESTATUS[0]}` after the pipe, **or**
   - capture to a var and print a slice, exiting on the captured code.
   On a genuine timeout, emit an explicit "check did NOT complete — not verified"
   line and exit non-zero; a check that cannot finish is a failed check, not a
   passed one.

Also **scope the check to the current worktree**, not a loop over every worktree
in `git worktree list` — fanning `tsc`/`build` across all worktrees on every Stop
is both the concurrent-build contention that causes the timeouts above and
redundant re-verification of unrelated work (see the verification-scoping rule in
`~/.ai-instructions/code-quality.md`).
