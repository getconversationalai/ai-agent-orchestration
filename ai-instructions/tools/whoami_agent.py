"""
Prints this Claude Code session's own agent-<id>, matching the identity
pretooluse_bash.py already injects as the git commit author. Lets an agent
pass the SAME id to `workboard.py register --agent-session <id>` that its
own commits will carry, so the live board and finished git history use one
consistent identifier.

Reads CLAUDE_CODE_SESSION_ID from the environment — verified (2026-08-03)
to be a real, populated env var, and to be IDENTICAL between a parent
session and any sub-agent it spawns via the Agent tool (confirmed live: a
sub-agent's CLAUDE_CODE_SESSION_ID matched its parent's exactly). This is
consistent with the hook's own session_id field, which sub-agents share
with their parent.

LIMITATION: the hook additionally reads `agent_id` from its own stdin JSON
payload to append a `.sub<hash>` suffix distinguishing a sub-agent's commits
from its parent's — but agent_id has NO environment-variable equivalent, so
this script (invoked as a plain Bash command, not a hook) cannot see it and
cannot reproduce that suffix. A sub-agent running this script gets its
PARENT's agent-<id> — accurate for "which top-level task owns this," not
"which specific sub-agent." The finer distinction still exists in git log
(the hook sees agent_id and applies the .sub<hash> suffix there); it's just
not available to this standalone lookup.
"""
import os
import sys
import hashlib


def main():
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        print("ERROR: CLAUDE_CODE_SESSION_ID is not set in this environment "
              "(not running inside Claude Code, or an older version without it).",
              file=sys.stderr)
        sys.exit(1)
    short = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:6]
    print(f"agent-{short}")


if __name__ == "__main__":
    main()
