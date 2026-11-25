# Fix reference (Stage 2)

Read this before applying fixes. Each fix is its own change. Apply only what the user approved.

## Fix catalogue, by impact in real audits

1. **Retire ceremony that shouldn't exist.** A mandatory worktree per change, work-board registration, drift checks, and brainstorm → spec → plan before coding. Retiring a whole routine (10 hooks, 9 docs) beat every speed-up made *to* it.
2. **Never idle while waiting.** Launch independent work before asking. Batch decisions into one multiple-choice round. Proceed on the recommended option for easily reversible choices, but never for money, a push to main, DB writes or deletions. Use phone notifications (Remote Control) when truly blocked. This targeted the single biggest measured loss (~150 daytime hours waiting in 2 weeks), so don't drop it in a rewrite.
3. **Settings toggles (cheapest).**
   - Set effort to `medium`. The model picker saves effort per model under `modelSettings.<model>.effortLevel`; the top-level `effortLevel` is legacy on newer models.
   - Use permission mode `bypassPermissions` plus hooks that still block or ask for the risky actions. This removes the prompt cost without losing the guardrails.
   - Replace a verbose output-style plugin with a short custom style in `~/.claude/output-styles/` (front matter: `name`, `description`, `keep-coding-instructions: true`).
4. **Fewer prompts.** Give plain DB reads an explicit `allow` in the DB hook. Batch DB writes into one plain-English approval per release.
5. **Checks scoped to the change.** Run them once per change and once after a rebase, with a timeout. Lint every change. Run the full suite in CI instead of locally. No local production build by default.
6. **Reviewer budget.** No reviews for small work. At most one plan review and one code review for risky work (money, permissions, schema, security), with a narrow brief and a ~20-minute budget.
7. **One short shared instruction file.**
   - Aim for ≤ ~60 lines of plain rules, with no ceremony. A real rewrite went from 24 KB per tool and ~300 KB of docs to one ~5 KB core file and ~70 KB of on-demand docs.
   - Claude imports it with `@~/.ai-instructions/core.md` in CLAUDE.md. Gemini imports it with `@../.ai-instructions/core.md` in GEMINI.md, or via `context.fileName`. Codex can't import, so AGENTS.md tells it to read the file once per session.
   - Express the speed/quality balance as: *"the smallest change that fully and correctly solves it: fix the root cause, handle the edge cases that can really happen, follow existing patterns; don't add what nobody asked for."*
   - Keep these:
     - verify before "done"
     - a question is not a request to act
     - undo failed attempts
     - ask before touching main, secrets, live DB writes, or paid/irreversible actions
     - plain-English reports sized to the task
   - Point to detail docs **only for risky areas** (security, DB, schema changes, UI, customer-facing text), with "read only the section you need".
   - **Before cutting a rule, check it against what Stage 1 measured.** Keep the rules that fix the measured time sinks.
8. **Keep the hard-won lessons.** Before deleting any doc, grep it for incident language ("outage", "caused", "lost", "silently", "this has") and carry those rules into the core file as one-liners. Lessons worth keeping from real setups:
   - Check `error` on every Supabase/PostgREST call; `{data:null, error}` doesn't throw.
   - Add a regression test with bug fixes.
   - Never edit a test just to make it pass. Mocked tests don't prove DB, routing or deploy behaviour.
   - Never silently drop either side of a merge conflict.
   - Use timestamp migration names when agents work in parallel.
   - On Windows, remove worktrees only via the cleanup script. npm junctions can make `rm -rf` delete `.git`.
9. **Memory tiers.** The always-loaded index holds only in-flight work and pointers. Lessons become rules in instruction files; shipped history moves to project docs. Real result: a 14 KB index cut to 1.7 KB.
10. **Per-project tool lists** (Claude Code).
    - Plugins go in a project's `enabledPlugins`, personal skills in `skillOverrides`, and claude.ai connectors are switched off with per-project `disabledMcpServers` (no allowlist exists).
    - Turn project-specific plugins (payments, a particular DB, an email provider) off at the user level and on in those projects.
    - Check every project's `settings.local.json` too: it can quietly re-enable what you turned off.
    - Per-project lists apply to the repo's main folder. Sessions in **worktrees** only get them if the file is committed, so don't switch *every* plugin off globally if agents work in worktrees.
    - Per-project `.claude/settings.json` edits stay local until committed in each repo. Cloud sessions and teammates don't get them otherwise.
    - Keep hook-based plugins (e.g. a security reviewer) and the user's chosen output style on, even though usage scans never see them.
11. **Parallel work.** Replace custom coordination systems with built-ins: `claude --worktree`, subagent `isolation: worktree`, and one rule: only one agent merges into main.
12. **Known-issues list.** Fix or quarantine flaky tests at the root cause.
13. **Antivirus exclusion** for the interpreter folder. This is the user's change to make.

## When changing safety hooks
- Changes must be strictly *additive* to existing guards. Add a test that the old guard list is a subset of the new one.
- Prefer exact allow-forms over clever parsing. Every "smart" waiver opened new bypasses in review: shell expansion, redirects, quoting, aliases via `-c alias.x=…`, and `GIT_*` env vars.
- Fail closed. Back up the live files first and get one independent review.
- Deploy, then smoke-test the live hook, including inside a worktree.
- Fix false alarms. In a worktree `.git` is a file, so use `-e`, not `-d`. Use PowerShell rather than `wmic`, which current Windows 11 has removed, and report "unknown" rather than 0.

## When applying a slimmer setup
- **Don't destroy the user's work.** These files often took real effort. Say they're recoverable (git history, backups) and offer archiving to a non-loaded folder as an alternative to deletion.
- **Retiring a hook** means removing its `settings.json` registration, the live file AND the backup copy. A registered-but-missing hook errors on every tool call, and a leftover live file comes back on the next capture.
- **Find the source of truth first.** If the setup lives in more than one repo (e.g. a private source of truth plus a public export), edit the source-of-truth repo too. Otherwise the next deploy or export quietly restores the old setup.
- **Deploy scripts usually copy without deleting**, so remove retired files from the live home folders by hand. Update any allowlist or manifest for new and removed files.
- **Keep credentials out.** A tool config holding a token (e.g. an MCP URL with a key) is excluded or redacted, never committed. Never commit machine-specific settings to a public repo.
- **Back up the old setup locally first.** Then judge the result with a fresh session (see Verify in SKILL.md).
- **Don't trust a hook's own warnings blindly.** A start-up check reported "0.0 GB free" on a disk with 78 GB free. Verify before acting.
