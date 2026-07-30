# ai-agent-orchestration-config

A shared instruction system and worktree-hygiene toolchain for **Claude Code, Gemini CLI, and Codex**:
routers, instruction docs (`ai-instructions/`), project templates, coordination tools, hooks, and
scrubbed settings.

**No credentials live here** (enforced by `.gitignore`, `scripts/guard-secrets.ps1`, and a pre-commit hook).

---

## What this actually does

Two halves that solve two different problems.

**1. Instructions — how agents are expected to behave.** Worktree isolation, git safety, planning and
review gates, how to report work back to you in plain English, and an explicit workboard lifecycle.
This half is pure text and works anywhere.

**2. Tooling — hygiene that does not depend on anyone remembering.** Agents create git worktrees and
routinely abandon them: sessions end, get compacted, or crash before the tidy-up step. Left alone this
compounds — one machine reached **120 worktrees and 13 GB of duplicated `node_modules`**. Instructions
alone had not prevented it, because the instruction fires at the least reliable moment (the end of a
long task) and often falls to an agent that no longer exists by then.

So the tooling assumes nobody will remember:

| | What it does | When |
|---|---|---|
| **Push** | Every branch with unpushed commits goes to origin | daily + session end |
| **Autosave** | Stray uncommitted work is committed to `wip/<branch>` and pushed | daily + session end |
| **Purge** | `node_modules` deleted from idle worktrees — worktree and work untouched | daily |
| **Remove** | Worktrees whose content already exists in the base branch | daily + session end |
| **Retire** | Long-idle worktrees; the branch survives on origin | daily |
| **Report** | Anything needing a human, at session start and by email | always |

**The ordering is the safety model.** Push first, and a worktree stops being the only copy of anything —
which is what makes deleting it safe. Two rules are absolute:

- **A worktree with uncommitted changes is never touched.** Enforced twice, independently.
- **Autosave refuses outright if any changed file looks like a credential.** A missed backup is
  recoverable; a leaked key in shared history is not.

---

## Setup

### Option A — ask Claude to do it

Paste this into Claude Code. It handles everything except the two steps Google requires a human for.

```text
Please install the AI agent orchestration system on this machine.

1. Clone https://github.com/ykstrategy/ai-agent-orchestration
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
git clone https://github.com/ykstrategy/ai-agent-orchestration
cd ai-agent-orchestration-config

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

- `claude/` `gemini/` `codex/` — per-tool router, scrubbed settings, hooks
- `ai-instructions/` — instruction docs, `templates/`, `tools/`
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
- **The workboard is machine-local.** It coordinates multiple *agents on one machine*, not multiple
  *people*. Teammates do not see each other's boards. Sharing coordination state across a team needs a
  design that does not exist yet.
- **Auto-push creates branches.** One person generates a fair number of `wip/*` branches on shared
  remotes; a team generates many more, and nothing currently prunes them from origin. Worth agreeing
  on before a wide rollout.
- **The daily job needs you logged in.** It runs on battery and catches up after the machine was off,
  but not while logged out — changing that requires storing a password.
