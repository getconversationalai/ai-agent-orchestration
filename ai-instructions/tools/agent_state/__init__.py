"""agent_state -- server-backed agent state store (client-side package).

Lives at ~/.ai-instructions/tools/agent_state/, alongside the other global
CLI tools in this tree (workboard.py, scratch_worktree.py, etc.), and is
captured into the ai-agent-orchestration-config repo by scripts/capture.ps1
(see that repo's sync-allowlist.txt for the entries that let this package
sync out).

Kill switch
-----------
Every piece of emission behaviour this package adds -- and everything that
reads what it emits -- is gated by the AGENT_STATE_EMIT environment
variable. This module is the single source of truth for that gate: the
emitter, the drainer, and any other caller MUST go through `emit_enabled()`
below rather than reading the environment variable directly, so the on/off
contract can never drift between call sites.

Contract:
    unset, or "0"  -> OFF (default)
    "1"            -> ON
    anything else  -> OFF (fail closed)

OFF is the default for the whole build. With it OFF, nothing in this
package changes the behaviour of any existing tool -- workboard.py in
particular is provably unchanged when AGENT_STATE_EMIT is unset or "0".
"""

import os

ENV_VAR = "AGENT_STATE_EMIT"
_ON_VALUE = "1"


def emit_enabled() -> bool:
    """Return True if agent_state emission is switched on.

    Reads AGENT_STATE_EMIT fresh on every call (never cached), so tests and
    callers can flip it via os.environ/monkeypatch and see the change take
    effect immediately. Only the exact value "1" enables emission --
    unset, "0", or any other value is OFF.
    """
    return os.environ.get(ENV_VAR, "0").strip() == _ON_VALUE
