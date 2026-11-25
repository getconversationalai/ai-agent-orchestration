# agent-setup-tuneup

Makes Claude Code faster by finding what in **your** setup slows it down, or makes it do a worse job, and fixing only what you approve.

Setups get slow as rules, hooks and plugins pile up:
- each "always do X first" step adds round trips
- each always-loaded rule competes with your actual request
- the biggest loss is usually time sessions spend waiting on you

This plugin measures that and gives you a ranked, plain-English report.

## Install (once)
In Claude Code:
```
/plugin marketplace add getconversationalai/ai-agent-orchestration
/plugin install agent-setup-tuneup@agent-orchestration
```
Updates arrive through `/plugin marketplace update agent-orchestration`.

## Use
In any Claude Code session, say something like:
- "Audit my Claude setup for speed"
- "Why is Claude slow for me?"
- "Are my hooks slowing things down?"

**Stage 1 is read-only.** Claude:
- reads your settings, hooks and instruction files
- times a few harmless commands
- reads your local session logs (counts and durations only)

Then it reports the biggest wins first and **stops**. Nothing changes until you say which fixes to apply.

**Stage 2** applies only the fixes you pick, cheapest first, and keeps everything reversible.

**Afterwards:** start a fresh session and give it a normal task to feel the difference.

## Privacy
- Everything runs on your machine. Nothing is uploaded.
- The scripts print only tool names, counts and timings, never your commands, code, SQL or MCP credentials.
- The scripts need Python 3.8+ (standard library only). Without Python, Claude falls back to checking by hand.

## Good to know
- Fixes to a project's **shared** files (committed `.claude/settings.json`, `CLAUDE.md`, `.mcp.json`) affect everyone on that repo. The report flags those so you can agree on them first.
- Antivirus exclusions and other machine-security changes are only ever recommended, never made.
- Works on Windows, macOS, Linux and WSL.
