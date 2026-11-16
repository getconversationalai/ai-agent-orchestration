# Global instructions (Codex)

The shared rules for all my AI tools live in `~/.ai-instructions/core.md`. At the start of each session, read that file once and follow it. It's short.

## Codex specifics
- This machine is Windows: use PowerShell syntax and `py` (not `python`).
- Claude Code's safety hooks don't run here, so apply the "Safety" rules in core.md yourself: never push to or merge into main, never commit secrets, never write to a live database, without asking me first.
