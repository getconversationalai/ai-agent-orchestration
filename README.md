# ai-agent-orchestration

One short set of instructions, a few safety hooks, and a nightly cleanup job that make AI coding agents — Claude Code, Gemini CLI, and Codex — safer to leave alone, without slowing them down.

Out of the box, these agents are already fast and good. Most "make the AI better" systems bury them in process: plan reviews, checklists, coordination boards, a dozen docs to read before every edit. That makes them slower and, oddly, worse — every extra rule competes with the task you actually asked for. This repo takes the opposite approach: keep the agent's natural speed, and add only the guardrails that prevent real damage.

---

## What it does

**One set of rules for every agent.** A single ~35-line file (`ai-instructions/core.md`) is shared by Claude Code, Gemini, and Codex, so there's nothing to keep in sync by hand. It asks for the smallest change that correctly solves the problem, verification before calling anything done, and plain-English reports.

**Detail only where the risk is.** Short reference docs cover the areas where mistakes are expensive — security, database work, renames and schema changes, UI design, and customer-facing writing. Agents read just the section they need, only when they're working in that area.

**Guardrails that cost almost nothing.** Fast hooks block or ask before the dangerous things: pushing or merging to main, committing secrets, deleting `.git`, rebases, and writes to a live database. Everything else runs at full speed.

**Parallel work without the paperwork.** A single agent just works on a branch. When several agents work on the same repo at once, each gets its own git worktree (built into Claude Code) and only one merges into main.

**It never loses your work.** Unfinished work is saved to a `wip/<branch>` branch when a session ends, and a nightly job tidies up abandoned worktrees and reclaims disk space. Uncommitted work is never touched, and anything that looks like a password or key is never allowed into a backup.

**It reports back in plain English.** You get a short summary of what changed and why, sized to the job — a sentence for a small fix, and "done / how to check / verified / next" for a big one.

---

## Setup

### Option A — ask Claude to do it

Paste this into Claude Code. It handles everything except the two steps Google requires a human for.

```text
Please install the AI agent orchestration setup on this machine.

1. Clone https://github.com/getconversationalai/ai-agent-orchestration
   into my projects folder (ask me where that is if you can't tell).
2. Run the repo's scripts/install-hooks.ps1, then scripts/deploy.ps1 -WhatIf,
   show me the preview, and once I approve it run scripts/deploy.ps1.
   (On macOS or Linux, copy the files per sync-manifest.txt instead —
   the deploy scripts are PowerShell.)
3. Run: py ~/.ai-instructions/tools/orchestration_setup.py
   Ask me which folder contains my git repos. Leave mode as "report".
4. Run: py ~/.ai-instructions/tools/orchestration_setup.py --schedule
5. Show me: py ~/.ai-instructions/tools/worktree_sweeper.py
   — this is a dry run. Walk me through what it would do and why.

Do NOT switch it to apply mode. I want to watch it in report mode first.

Optional, only if I say I want the daily email: run
  py ~/.ai-instructions/tools/orchestration_setup.py --setup-email <my address>
and tell me exactly what I have to do by hand afterwards.
```

### Option B — do it yourself

```bash
git clone https://github.com/getconversationalai/ai-agent-orchestration
cd ai-agent-orchestration

./scripts/install-hooks.ps1
./scripts/deploy.ps1 -WhatIf      # preview
./scripts/deploy.ps1              # copies instructions + tools into your home dir

py ~/.ai-instructions/tools/orchestration_setup.py              # asks where your repos are
py ~/.ai-instructions/tools/orchestration_setup.py --schedule   # daily 6am sweep
py ~/.ai-instructions/tools/worktree_sweeper.py                 # dry run — changes nothing
```

You start in **report mode**: everything describes what it *would* do and changes nothing. Watch it for
a few days. When the reports look right:

```bash
py ~/.ai-instructions/tools/orchestration_setup.py --mode apply
```

Nothing here is one-way — set `--mode report` at any time to stop it acting.

### Optional: the daily email

Entirely optional. Without it you still get the session-start summary and a local log.

```bash
py ~/.ai-instructions/tools/orchestration_setup.py --setup-email you@company.com
```

This creates a GCP project, a service account, and a key. **Two steps need a human and cannot be
automated:**

1. `gcloud auth login` needs a browser.
2. Authorising the service account to send mail needs a Workspace admin in `admin.google.com`
   (Security → Access and data control → API controls → Domain-wide delegation). Google exposes no
   API for this. The setup script prints the exact Client ID and scope to paste in.

No Workspace admin? Skip it. Nothing else depends on email.

---

## Configuration

Per-machine settings live in `~/.agent-orchestration/config.json` — deliberately **outside** the synced
directories, so your paths are never published to the team.

| Key | Meaning | Default |
|---|---|---|
| `project_roots` | Folders containing your repos. **Nothing outside these is ever touched.** | none — setup asks |
| `mode` | `report` (describe only) or `apply` (act) | `report` |
| `purge_deps_days` | Idle days before `node_modules` is reclaimed | 7 |
| `autosave_days` | Idle days before stray work is saved to `wip/<branch>` | 7 |
| `retire_days` | Idle days before a worktree is removed (branch stays on origin) | 14 |
| `protected_paths` | Never touched (substring match) | `_scratch`, `_baseline` |
| `notify_email` | Daily report recipient; blank disables email | blank |

Project roots are **never auto-discovered.** These tools delete things, and a home directory is often
itself a git repository — a tool that goes looking for repos will eventually find one nobody meant to
give it.

---

## Layout

- `ai-instructions/core.md` — the shared rules every agent follows
- `claude/` `gemini/` `codex/` — a few tool-specific lines that import the core rules, plus settings, hooks and (for Claude) a custom output style
- `ai-instructions/` — the risk-area reference docs, `templates/`, and `tools/` (the nightly cleanup job)
- `sync-manifest.txt` — maps repo paths ↔ home paths
- `sync-allowlist.txt` — what may live in each synced dir. `capture.ps1` and the pre-commit hook abort
  on anything unlisted, so project or session files can never be swept in.
  **A new instruction doc or hook needs its line here in the same commit.**
- `scripts/` — `deploy.ps1`, `capture.ps1`, `guard-secrets.ps1`, `install-hooks.ps1`

## Contributing changes back

```bash
./scripts/capture.ps1     # pull live edits into the repo (aborts on unlisted files)
git add -A && git commit -m "..."   # secret scan runs automatically
git push
```

Edited from the repo side instead? `./scripts/deploy.ps1` pushes it back out to your live files.

---

## Known limits

- **`deploy.ps1` / `capture.ps1` are PowerShell.** On macOS/Linux, copy per `sync-manifest.txt` by hand.
  The Python tooling itself is cross-platform; the Windows-specific NTFS junction handling simply does
  not apply elsewhere.
- **Auto-push creates branches.** One person generates a fair number of `wip/*` branches on shared
  remotes; a team generates many more, and nothing currently prunes them from origin. Worth agreeing
  on before a wide rollout.
- **The daily job needs you logged in.** It runs on battery and catches up after the machine was off,
  but not while logged out — changing that requires storing a password.
