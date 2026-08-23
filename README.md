# ai-agent-orchestration

A shared workflow that makes AI coding agents — Claude Code, Gemini CLI, and Codex — build better software, faster.

It gives your agents the instincts of a great engineering team. They think an idea through and plan it before writing anything, and a second pass reviews that plan for problems before a line of code exists. When a job is big enough, they split it across parallel sub-agents and work the pieces at the same time instead of one after another — each in its own isolated workspace — then merge it back together without tripping over each other. They know when to build something to last and when it's fine to move fast. And they check that what they built actually works: running the tests and exercising the real system, not just trusting that it looks finished.

It also knows when to slow down. Small changes move quickly; anything that touches money, security, or your users' data gets a full review and test pass, automatically, without you having to ask.

It cleans up after itself, too. Every change is committed and pushed as it goes, so work is never lost; half-finished experiments and stale branches don't pile up; and you can hand your agents real independence and step away.

---

## What it does

**It plans before it builds.** Every feature starts as a plan, and the plan gets checked for problems before any code is written — the way you'd review a pull request, except the mistakes are still cheap to fix. Riskier work (anything touching payments, login, or the database) gets a dedicated reviewer whose only job is to poke holes; small changes skip the ceremony. The plan describes what to build and how to prove it works, and treats specific file names and snippets as sketches to re-check against the real code, so a plan from last week doesn't fall apart when someone moves a file.

**It works in parallel — when that actually helps.** When a job splits into independent pieces, agents build them at the same time, each in its own isolated workspace, then merge the results in an order picked to avoid conflicts. When the pieces depend on each other, it does them in sequence — running parallel workers on chained work is slower, not faster, and the system knows the difference. The number running at once adjusts to how busy your machine is.

**It builds things to last.** Agents reuse what's already there instead of reinventing it, structure new work so it's easy to extend, and make failures loud instead of letting them slip by unnoticed. They know when to build something properly and when a quick version is the right call. Changes that ripple across a system — renaming things, changing a database column, reshaping an API — happen in safe, reversible stages so nothing breaks halfway. Interfaces are designed for real people on real devices, phone and desktop, not a generic template.

**It proves the work is done.** "Done" means verified, not "looks done." Agents run the right tests for what changed, and check anything only a live system can confirm against the running app rather than a stand-in. Every bug fix comes with a test that locks it so it can't quietly return. After a big change, they walk the actual screens a user would touch and confirm the front end and back end still line up — nothing missing, nothing stale, nothing wired to the wrong place.

**It knows when to be careful.** Two dials run through everything: how much process a change gets scales to its size, and how much scrutiny it gets scales to its risk. A typo ships in seconds. Anything touching money, security, user data, or your live database gets a full review-and-test pass, automatically. The careful path is never skipped to save time, and the fast path is never buried in process it doesn't need.

**It never loses your work.** All of it runs on a safety net that assumes nobody will remember to clean up. Work is committed and pushed as it goes, so there's always a backup. Finished workspaces are cleared, abandoned ones are swept up on a schedule, and leftover files are reclaimed so your disk doesn't fill. Uncommitted work is never touched, and anything that looks like a password or key is never allowed into a backup. Point your agents at your repos, let them work, and walk away.

**It reports back in plain English.** When something's done, you get a plain-language summary of what changed and how to see it yourself — not a wall of file names and commit IDs.

---

## Setup

### Option A — ask Claude to do it

Paste this into Claude Code. It handles everything except the two steps Google requires a human for.

```text
Please install the AI agent orchestration system on this machine.

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
