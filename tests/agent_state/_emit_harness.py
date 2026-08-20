"""Shared, framework-agnostic harness for the workboard emit golden test.

This module deliberately imports NEITHER pytest NOR agent_state.emit. It is
imported by two very different callers that must agree byte-for-byte on what
they run:

  1. the ONE-TIME golden-capture script, run against the UNMODIFIED
     workboard.py *before* Task 4's edits (the only trustworthy baseline --
     capturing after the change would prove nothing); and
  2. the pytest suite (`test_emit_isolation.py`), run against the MODIFIED
     workboard.py with the kill switch OFF.

Because the command SEQUENCE and normalise() live here, in one place both
import, the stored goldens cannot drift from what the test replays.

normalise() is the load-bearing piece the brief calls out: workboard.py writes
wall-clock timestamps and (potentially) machine-specific absolute paths, so a
raw byte compare would flake on a minute boundary and could never match across
machines. normalise() masks exactly those field VALUES in the raw JSON text --
nothing else -- preserving key order, indentation and whitespace so the compare
still catches any real structural or content change.
"""

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# The tools dir (home path, NOT this repo) holds the live workboard.py under
# test. Mirror conftest.py so this harness is importable standalone too.
TOOLS_DIR = Path.home() / ".ai-instructions" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# Fixed clock used ONLY by the golden capture (brief Step 1: "with now_iso
# monkeypatched to a fixed string"). The pytest replay runs with a LIVE clock
# and leans on normalise() to mask timestamps -- a strictly stronger check.
FROZEN_NOW = "2026-08-02T12:00"

# One command per line, in an order where every referenced plan node / subagent
# already exists when it is named. Exercises all 16 state-changing handlers:
#   register, update-status, update-files, set-overlap, update-path,
#   register-plan, add-phase, add-wave, add-task, add-node, start,
#   register-subagent, update-subagent-path, complete-subagent, done, complete.
# Every argument value is fixed and machine-independent (synthetic --worktree /
# --plan / --files), so the only volatile content is the timestamps.
SEQUENCE = [
    ["register", "--feature", "Alpha", "--tool", "claude", "--branch", "feat/alpha",
     "--worktree", "/wt/alpha", "--plan", "plans/alpha.md", "--files", "a.py,b.py",
     "--overlap", "none", "--agent-session", "agent-aaa111"],
    ["update-status", "--feature", "Alpha", "--status", "waiting", "--reason", "blocked on upstream"],
    ["update-status", "--feature", "Alpha", "--status", "active"],
    ["update-files", "--feature", "Alpha", "--files", "a.py,c.py"],
    ["set-overlap", "--feature", "Alpha", "--overlap", "touches shared/util.py"],
    ["update-path", "--feature", "Alpha", "--path", "Phase 1 / Wave 1.1"],
    ["register-plan", "--feature", "Alpha", "--plan-title", "Alpha Plan", "--plan-path", "plans/alpha.md"],
    ["add-phase", "--feature", "Alpha", "--title", "Phase 1"],
    ["add-wave", "--feature", "Alpha", "--phase", "Phase 1", "--title", "Wave 1.1"],
    ["add-task", "--feature", "Alpha", "--phase", "Phase 1", "--wave", "Wave 1.1", "--title", "Task 1"],
    ["add-node", "--feature", "Alpha", "--parent", "Phase 1 / Wave 1.1", "--title", "Node X", "--level", "node"],
    ["start", "--feature", "Alpha", "--at", "Phase 1 / Wave 1.1 / Task 1"],
    ["register-subagent", "--parent", "Alpha", "--feature", "SubA",
     "--task", "Phase 1 / Wave 1.1 / Task 1", "--branch", "feat/suba", "--worktree", "/wt/suba", "--files", "d.py"],
    ["update-subagent-path", "--parent", "Alpha", "--sub", "alpha/suba", "--path", "Phase 1 / Wave 1.1"],
    ["complete-subagent", "--parent", "Alpha", "--sub", "alpha/suba"],
    ["done", "--feature", "Alpha", "--at", "Phase 1 / Wave 1.1 / Task 1"],
    ["complete", "--feature", "Alpha", "--merged", "yes", "--no-auto-cleanup"],
]

# Field VALUES masked by normalise(). Timestamps (wall-clock, minute-precision)
# and the two machine-specific path fields the brief names explicitly.
_TS_KEYS = ("started", "updated", "completed", "created")
_PATH_KEYS = ("worktree", "plan_path")


def normalise(text):
    """Mask clock-/machine-dependent field values in raw JSON text.

    Operates on the raw text (not a parsed+reserialised structure) so JSON key
    order, indentation and the trailing newline are all still part of the
    compared bytes -- only the masked values are removed from the comparison.
    """
    if text is None:
        return None
    for key in _TS_KEYS:
        text = re.sub(rf'("{key}": )"[^"]*"', r'\1"<TS>"', text)
    for key in _PATH_KEYS:
        text = re.sub(rf'("{key}": )"[^"]*"', r'\1"<PATH>"', text)
    return text


def _read(root, name):
    path = Path(root) / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def run_workboard(argv):
    """Run ``workboard.main()`` IN-PROCESS with ``argv``; return the exit code.

    In-process (not a subprocess) is required: the isolation tests monkeypatch
    ``agent_state.emit._append`` and ``workboard.now_iso``, which only affect
    the running interpreter. A normal return is exit 0; a ``SystemExit`` (an
    error handler or argparse) surfaces its code.
    """
    import workboard
    saved_argv = sys.argv
    sys.argv = ["workboard.py", *argv]
    try:
        workboard.main()
        return 0
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = saved_argv


def replay(freeze_clock):
    """Run SEQUENCE in the CURRENT working directory (must be a git repo root),
    returning the normalised (WORKBOARD.json, PROGRESS.json) text after each
    command as a list of ``{"wb":..., "prog":...}`` dicts.

    Always forces the kill switch OFF -- both the golden capture and the
    switch-off byte test run with emission disabled. ``freeze_clock`` pins
    ``workboard.now_iso`` to FROZEN_NOW (used by the capture); the pytest replay
    passes False and relies on normalise() instead.
    """
    import workboard
    root = Path.cwd()
    assert workboard.find_project_root().resolve() == root.resolve(), (
        f"find_project_root() {workboard.find_project_root()} is not the "
        f"scratch repo {root} -- refusing to run (would clobber a real repo)"
    )
    os.environ.pop("AGENT_STATE_EMIT", None)
    saved_now = workboard.now_iso
    if freeze_clock:
        workboard.now_iso = lambda: FROZEN_NOW
    try:
        snapshots = []
        for argv in SEQUENCE:
            rc = run_workboard(argv)
            assert rc == 0, f"command exited {rc!r}: {argv}"
            snapshots.append({
                "wb": normalise(_read(root, "WORKBOARD.json")),
                "prog": normalise(_read(root, "PROGRESS.json")),
            })
        return snapshots
    finally:
        workboard.now_iso = saved_now


def capture_in_tempdir(freeze_clock):
    """Create a throwaway git repo, chdir into it, run ``replay`` there, and
    clean up. Used by the one-time golden-capture script (which has no pytest
    ``tmp_path`` fixture of its own)."""
    tmp = Path(tempfile.mkdtemp(prefix="emit_golden_"))
    saved_cwd = Path.cwd()
    try:
        (tmp / ".git").mkdir(parents=True, exist_ok=True)
        os.chdir(tmp)
        return replay(freeze_clock)
    finally:
        os.chdir(saved_cwd)
        shutil.rmtree(tmp, ignore_errors=True)
