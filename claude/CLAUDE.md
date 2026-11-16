# Global instructions (Claude Code)

The shared rules for all my AI tools live in one file, imported here:

@~/.ai-instructions/core.md

## Claude Code specifics
- This machine is Windows: use PowerShell syntax and `py` (not `python`).
- Prefer the built-in Read / Grep / Glob / Edit tools over shell commands for reading, searching and editing files. Use Bash for running programs and git.
- Hooks in `~/.claude/hooks/` block or ask before risky git and database commands. If one blocks you, tell me what it said instead of working around it.
- A hook adds a per-agent author to each `git commit` automatically. Don't set `user.name` / `user.email` yourself.
