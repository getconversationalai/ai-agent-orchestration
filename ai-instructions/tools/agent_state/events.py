"""agent_state.events -- event envelope + crash-safe per-machine sequence.

`build_event` packages one state change as a dict with a stable identity
(`event_id`) and the field the server uses to resolve conflicts
(`entity_updated`) -- see the plan's Global Constraints
(docs/superpowers/plans/2026-08-02-agent-state-store.md) for the full
conflict-resolution rule this feeds. `next_gap_seq` hands out the
per-machine monotonic counter recorded alongside it.

Conflict key vs. gap counter
----------------------------
`entity_updated` is copied verbatim from the entity's own `updated` field
(as written by workboard.py's `now_iso()` -- naive-local, minute
precision) and is the ONLY thing that decides which of two conflicting
changes wins. `gap_seq` is a per-machine monotonic counter used
exclusively for (a) the server noticing a missing event and (b) breaking
ties between two events from the SAME machine whose `entity_updated`
values are equal (the same-minute-same-machine case `now_iso()`'s
precision makes possible). It is NEVER used for cross-machine ordering.

Where gap_seq comes from
-------------------------
The contract fixes build_event's signature at exactly
`(kind, repo_identity, payload, machine_id)` -- no `state_dir` parameter --
yet every built event must carry a real `gap_seq`. This module resolves
that by having `build_event` call `next_gap_seq(default_state_dir())`
internally. `default_state_dir()` defaults to
`~/.agent-orchestration/agent_state/` -- the same machine-local,
never-synced directory family `orchestration_config.py` already uses for
`config.json` and that Task 8 uses for the refresh token -- and is
overridable via the `AGENT_STATE_DIR` environment variable (mirroring the
existing `AGENT_ORCHESTRATION_ROOTS` override precedent), which is also
what lets Task 11 simulate two distinct "machines" on one physical box.
Callers that need a fully self-contained, side-effect-free event for
testing should set `AGENT_STATE_DIR` to a throwaway directory first (see
test_events.py's autouse fixture).

Crash-safety trap this module avoids
-------------------------------------
An earlier draft's gap_seq counter lived in a single JSON file with no
redundancy: if that file was ever found truncated (power loss, disk
corruption, or anything else that mangles the file outside this module's
own write path -- which is already atomic and therefore never itself
produces a torn file), the counter silently reset to 1, after which the
server would reject every subsequent event as stale forever (gap_seq only
ever increases per machine). This version keeps a second,
independently-written high-water-mark file alongside the primary one;
recovery uses whichever of the two still parses, so a single corrupted
file never rolls the counter backwards. Only if BOTH files exist but
neither parses -- i.e. there is demonstrably no recoverable value, not
just a fresh/never-used directory -- does `next_gap_seq` refuse outright
(raise `GapSeqError`) rather than guess "0" and silently corrupt the
sequence, per the "losing telemetry is acceptable, corrupting the
sequence is not" rule. Callers that need this to never break their own
command (the emitter, Task 4) are expected to catch broadly around the
whole build_event() call, exactly as they must already do for any other
emission failure.

Transcript-contents guard (hard security invariant)
-----------------------------------------------------
"Transcripts are referenced by path/metadata only, never contents" is a
Global Constraint, not a suggestion -- a payload accidentally or
maliciously carrying a transcript's actual text must never leave this
machine. A review of the first version of this module found that
guarantee was NOT enforced: `build_event` did pure passthrough, and its
own covering test fed an already-clean payload and asserted it stayed
clean, which is true of any passthrough function and proves nothing.

`build_event` now actively drops any payload key that is "transcript-ish"
AND does not merely reference a path. Precise rule, applied
case-insensitively to each top-level payload key:

    drop the key iff "transcript" appears in the key name
                  AND the key name does NOT end in "path"

So `transcript_path` / `chat_transcript_path` survive untouched;
`transcript_contents`, `transcript_body`, `TRANSCRIPT_CONTENTS` (any
transcript-ish key that isn't a path reference) are removed before the
event is ever built. Keys unrelated to transcripts entirely (e.g.
`notes`, `feature`) are never touched by this rule, regardless of
whether they happen to end in `_contents`/`_body` -- the guard is scoped
to transcript-ish keys specifically, not a blanket ban on those suffixes.
This is dropped, never raised: per the never-break-workboard rule this is
telemetry, so a caller passing a bad key must still get a working event
back (minus the offending field), not an exception. A dropped key is
still surfaced (a `stderr` warning), so it is not silently discarded.
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_state._atomic import safe_replace


# ---------------------------------------------------------------------------
# Cross-platform advisory file locking -- mirrors workboard.py's _lock /
# _unlock exactly (msvcrt on Windows, fcntl elsewhere) so next_gap_seq is
# safe against two concurrent processes racing for the same seq value. Not
# imported from workboard.py: this package must not depend on the CLI
# script, only mirror its proven pattern.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import msvcrt

    def _lock(f) -> None:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class GapSeqError(RuntimeError):
    """Raised when persisted gap_seq state is corrupt and unrecoverable.

    Only raised when BOTH the primary counter file and its redundant
    high-water-mark copy exist but neither parses to a valid value -- the
    one case where returning a number would mean silently resetting a real
    machine's sequence backwards. Per the "losing telemetry is acceptable,
    corrupting the sequence is not" rule, callers (the emitter) should
    treat this the same as any other emission failure: swallow it broadly,
    never let it break the caller's own command.
    """


STATE_DIR_ENV = "AGENT_STATE_DIR"

_SEQ_FILENAME = "seq.json"
_HWM_FILENAME = "seq.hwm.json"
_LOCK_FILENAME = "seq.lock"


def default_state_dir() -> Path:
    """Resolve the per-machine local state directory for gap_seq bookkeeping.

    Defaults to ``~/.agent-orchestration/agent_state`` -- deliberately
    OUTSIDE ``~/.ai-instructions/`` (which is captured into the shared
    config repo by capture.ps1; a runtime state file living there would
    both leak machine-local state into the shared backup and, being a
    ``.json`` file, would not match that tree's `*.py`/`**` allowlist and
    would abort capture entirely -- see Task 0). Overridable via the
    ``AGENT_STATE_DIR`` environment variable, mirroring
    ``orchestration_config.py``'s existing ``AGENT_ORCHESTRATION_ROOTS``
    override, so tests and Task 11's two-simulated-machines-on-one-box
    scenario can point separate "machines" at separate directories.

    Pure path computation -- does not create the directory; callers that
    need it to exist (`next_gap_seq`) create it themselves.
    """
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".agent-orchestration" / "agent_state"


def _acquire_lock(lock_path: Path, timeout: float = 10.0):
    """Open and exclusively lock `lock_path`, blocking up to `timeout`s.

    Raises TimeoutError if the lock can't be acquired in time (e.g. another
    process is wedged) rather than hanging forever or silently proceeding
    unlocked -- an unlocked read-increment-write is exactly how two
    concurrent processes end up handing out the same gap_seq value.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lf = open(lock_path, "w")
    start = time.monotonic()
    while True:
        try:
            _lock(lf)
            return lf
        except OSError:
            if time.monotonic() - start > timeout:
                lf.close()
                raise TimeoutError(
                    f"could not acquire gap_seq lock at {lock_path} "
                    f"within {timeout}s"
                )
            time.sleep(0.05)


def _release_lock(lf) -> None:
    try:
        _unlock(lf)
    finally:
        lf.close()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write `data` to `path` atomically: temp file + safe_replace.

    Mirrors workboard.py's `_write_json` (write-temp-then-replace) so a
    crash mid-write leaves either the complete old file or the complete
    new one, never a torn partial write. Uses `safe_replace` (see
    `agent_state._atomic`) rather than a bare `os.replace` so a transient
    Windows file-lock (Defender/indexer) is retried instead of failing
    the call outright.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    safe_replace(tmp, path)


def _read_seq_value(path: Path):
    """Read a `{"seq": N}` file, reporting both its value and its state.

    Returns (state, value):
      - ("absent", None)  -- file does not exist at all
      - ("corrupt", None) -- file exists but its content doesn't parse to
                             a valid non-negative int (truncated, garbled,
                             wrong shape, ...)
      - ("ok", N)         -- file exists and holds a valid int N

    The absent/corrupt distinction is what lets `next_gap_seq` tell "this
    is a legitimate first-ever call" apart from "the counter file was
    destroyed" -- both look like "no usable value" but only the former is
    safe to treat as a starting point of 0.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "corrupt", None

    if not raw or not raw.strip():
        return "corrupt", None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "corrupt", None
    if not isinstance(data, dict):
        return "corrupt", None
    seq = data.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        return "corrupt", None
    return "ok", seq


def next_gap_seq(state_dir) -> int:
    """Return the next per-machine monotonic gap_seq value for `state_dir`.

    Atomic + crash-safe:
      - The read-increment-write cycle runs under an exclusive lock (a
        `seq.lock` file in `state_dir`), so two concurrent callers --
        threads or separate processes -- never hand out the same value.
      - Every write goes through `_atomic_write_json` (temp file +
        `safe_replace`), so a crash mid-write never leaves a torn file,
        and a transient Windows file-lock is retried rather than failing
        the call outright.
      - A redundant high-water-mark file (`seq.hwm.json`) is written
        alongside the primary counter file (`seq.json`) on every
        successful call. If the primary is later found truncated or
        corrupt, recovery falls back to the high-water mark rather than
        resetting to 1. Only if BOTH files exist but neither parses --
        meaning there is truly no recoverable state, not just a fresh
        directory -- does this raise `GapSeqError` instead of guessing.

    `state_dir` should be a directory stable for one machine (e.g. under
    `~/.agent-orchestration/`); the counter is monotonic per directory,
    not global across machines.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    seq_path = state_dir / _SEQ_FILENAME
    hwm_path = state_dir / _HWM_FILENAME
    lock_path = state_dir / _LOCK_FILENAME

    lf = _acquire_lock(lock_path)
    try:
        primary_state, primary_val = _read_seq_value(seq_path)
        hwm_state, hwm_val = _read_seq_value(hwm_path)

        candidates = [v for v in (primary_val, hwm_val) if v is not None]
        if candidates:
            current = max(candidates)
        elif primary_state == "corrupt" or hwm_state == "corrupt":
            # At least one counter file exists but is unreadable, and
            # neither offers a valid value to recover from. Treating this
            # as "0" would silently roll the sequence back to 1 -- the
            # exact v1 failure. Refuse instead of guessing.
            raise GapSeqError(
                f"gap_seq state at {state_dir} is corrupt in both "
                f"{_SEQ_FILENAME} and {_HWM_FILENAME} with no recoverable "
                "value; refusing to reset the counter."
            )
        else:
            current = 0  # both genuinely absent: legitimate first-ever call

        next_value = current + 1
        _atomic_write_json(seq_path, {"seq": next_value})
        _atomic_write_json(hwm_path, {"seq": next_value})
        return next_value
    finally:
        _release_lock(lf)


_TRANSCRIPT_MARKER = "transcript"
_PATH_SUFFIX = "path"


def _drop_transcript_contents(payload: dict) -> dict:
    """Return a copy of `payload` with transcript-content-bearing keys removed.

    Rule (see the module docstring, "Transcript-contents guard"): a key is
    dropped iff "transcript" (case-insensitive) appears anywhere in its
    name AND the name does not end in "path". Keys with no "transcript" in
    their name are never touched by this function, no matter what else
    they're named.

    Non-fatal by design: filters, never raises. A dropped key is still
    reported (stderr warning) so removal is not silent, without ever
    risking the caller's command on what is, ultimately, best-effort
    telemetry.
    """
    cleaned = {}
    for key, value in payload.items():
        key_lower = key.lower() if isinstance(key, str) else str(key).lower()
        if _TRANSCRIPT_MARKER in key_lower and not key_lower.endswith(_PATH_SUFFIX):
            print(
                f"WARNING: agent_state.events dropped payload key {key!r} -- "
                "transcripts are referenced by path only, never contents.",
                file=sys.stderr,
            )
            continue
        cleaned[key] = value
    return cleaned


def build_event(kind, repo_identity, payload, machine_id) -> dict:
    """Build one event envelope.

    `event_id` is a fresh UUID4 and doubles as the idempotency key. A
    retry of the SAME logical change should reuse the original `event_id`
    rather than calling this again -- this function always mints a new
    one; callers that retry are responsible for holding onto the first
    result and re-sending it, not for calling `build_event` twice.

    `entity_updated` is copied verbatim from `payload["updated"]` (absent
    -> None). This, not `gap_seq`, is what the server uses to resolve
    conflicting concurrent edits. `payload["updated"]` is written by
    workboard.py's `now_iso()` -- naive-local, minute precision -- and
    this function does not reinterpret or convert it, so client and server
    compare the exact same basis.

    `client_time` is this call's own UTC, timezone-aware wall-clock
    reading -- advisory only (for humans debugging latency), never used
    for conflict resolution.

    `gap_seq` is this machine's next monotonic value from
    `next_gap_seq(default_state_dir())` -- see the module docstring
    ("Where gap_seq comes from") for why this is resolved internally
    rather than taken as a parameter.

    `payload` is shallow-copied (so a caller mutating its own dict after
    the fact can't retroactively change an already-built event) and then
    run through `_drop_transcript_contents` -- see the module docstring
    ("Transcript-contents guard") for the exact rule. `repo_identity`
    (when a dict) is likewise shallow-copied, unfiltered.
    """
    payload = dict(payload) if payload else {}
    payload = _drop_transcript_contents(payload)
    repo = dict(repo_identity) if isinstance(repo_identity, dict) else repo_identity

    return {
        "event_id": str(uuid.uuid4()),
        "kind": kind,
        "repo": repo,
        "payload": payload,
        "machine_id": machine_id,
        "entity_updated": payload.get("updated"),
        "client_time": datetime.now(timezone.utc).isoformat(),
        "gap_seq": next_gap_seq(default_state_dir()),
    }
