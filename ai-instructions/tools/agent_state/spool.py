"""agent_state.spool -- durable local spool: one file per event (Task 3).

`Spool` is the client-side durability layer between "an event happened"
and "the server has confirmed receipt of it". Every event is written as
its OWN file, atomically (write-temp-then-rename), rather than appended
into one shared JSONL file. That one design choice is what makes the rest
of the contract simple:

  - Concurrent appenders never corrupt each other -- each writes a
    uniquely-named file and never touches any other file.
  - `ack(event_ids)` needs no rewrite of anything and no lock: it just
    unlinks the specific files whose event_id was confirmed. There is no
    "read the whole queue, remove one entry, write it all back" step for
    a concurrent writer to race against.
  - `read_batch(n)` is a directory listing + sort, not a parse-and-slice
    of one growing file.

Trap this module avoids (the reason this exists instead of a v1 JSONL
design): a single shared JSONL file requires read-modify-write to ack
anything, which loses events under concurrent writers -- one appender's
write can land in the gap between another caller's read and rewrite.
One-file-per-event sidesteps that whole class of bug.

Filename scheme -- the single most important detail in this module
------------------------------------------------------------------
Every event is stored as:

    f"{time.time_ns():020d}-{event_id}.json"

in `spool_dir`. This MUST be `time.time_ns()`, never `client_time` (or any
other ISO-8601 string). An ISO timestamp contains colons
(e.g. "2026-08-03T12:00:00+00:00"), and colons are illegal in Windows
filenames -- `open()` raises `OSError [Errno 22]` on them. Combined with
`append`'s never-raise contract (below), a colon in the filename would
make the write fail, `append` would swallow that failure exactly as
designed, and the event would silently vanish forever -- on every single
real event, on every Windows machine, always. `time.time_ns()` is a plain
integer with none of those characters, zero-padded to 20 digits so that a
plain lexical (string) sort of filenames equals chronological order --
no filename parsing is needed anywhere a batch of events must come out
oldest-first.

Invariants
----------
- One file per event (see above).
- `append` NEVER raises to the caller. Any failure -- an unwritable
  spool_dir, a full disk, a monkeypatched `_write_file` in tests, a
  garbage `event` argument -- degrades to a no-op. Telemetry that crashes
  the caller's real command is a worse outcome than telemetry that is
  quietly lost, per this whole package's founding rule (see
  agent_state/__init__.py's kill-switch docstring for the same
  philosophy applied at the package level). The failure is still printed
  to stderr, so it is not silently invisible to someone looking -- only
  non-fatal to the caller. That stderr write is itself routed through
  `_safe_warn`, which can never raise either (see below) -- a broken or
  redirected stderr must not be able to turn a logging statement into the
  exact caller-visible exception this invariant exists to prevent.
- Dropping (via the size cap) is *recorded*, never silent:
  `dropped_count()` increments every time the cap forces the oldest event
  out.
- A refused event is *dead-lettered, not deleted*
  (`dead_letter(event_id, reason)` moves the file to a `dead/` subdir).
  A hostile or buggy server rejecting events must not be able to make the
  client silently discard its own queue -- the data survives, just
  outside the active queue, with the reason recorded alongside it.

Concurrency
-----------
`append` is safe to call from multiple threads (and multiple processes)
against the same `spool_dir` with no external locking, because each call
writes to a path derived from its own `(time.time_ns(), event_id)` pair --
a collision would require two calls to share both values, and `event_id`
is expected to be unique per event (a fresh UUID4 from `build_event`).
`ack` and `dead_letter` likewise need no lock: unlinking (or renaming) a
specific, already-fully-written file is a single atomic filesystem
operation. The one piece of mutable in-process state this module keeps is
the dropped-event counter, which a lock protects from lost increments
when the size cap is hit by concurrent callers within one process. This
counter is in-memory only (not persisted across a process restart) --
unlike `events.next_gap_seq`'s on-disk counter, nothing in this task's
contract requires `dropped_count()` to survive a restart.
"""

import json
import sys
import threading
import time
import uuid
from pathlib import Path

from agent_state._atomic import safe_replace

_SUFFIX = ".json"
_DEAD_DIRNAME = "dead"


def _make_filename(event_id) -> str:
    """Build the one-file-per-event filename for `event_id`.

    MUST use time.time_ns() -- see the module docstring's "Filename
    scheme" section. Never client_time or any other ISO string here.
    """
    return f"{time.time_ns():020d}-{event_id}{_SUFFIX}"


def _split_filename(name):
    """Return the event_id encoded in a spool filename, or None.

    Filenames are `f"{ts:020d}-{event_id}.json"` -- a fixed-width 20-digit
    timestamp, a literal "-", then the event_id, then ".json". The
    timestamp's fixed width lets this parse unambiguously even though
    `event_id` (a UUID4 in practice) itself contains dashes.
    """
    if not name.endswith(_SUFFIX):
        return None
    stem = name[: -len(_SUFFIX)]
    if len(stem) < 22 or stem[20] != "-":  # 20-digit ts + "-" + >=1 char id
        return None
    if not stem[:20].isdigit():
        return None
    return stem[21:]


def _safe_warn(message: str) -> None:
    """Print `message` to stderr -- but NEVER raise, not even if stderr
    itself is broken.

    Every diagnostic write in this module goes through this function
    rather than calling `print(..., file=sys.stderr)` directly (review
    finding: a closed or redirected stderr -- a dead pipe, a service
    context with no console, `| head` closing early -- can make the write
    itself raise, and an unguarded `print` in an `except` block would let
    that propagate straight out of `append`, violating its never-raise
    contract via the very code that exists to report a DIFFERENT failure).
    Catches `BaseException`, not just `Exception`, deliberately: logging a
    failure must never be able to produce a new, different failure in the
    operation it is reporting on -- mirroring the intended `emit_safely`
    pattern (Task 4).
    """
    try:
        print(message, file=sys.stderr)
    except BaseException:
        pass


class Spool:
    """A durable, crash-safe, append-only local event queue.

    One file per event under `spool_dir` (see module docstring). `cap`
    bounds how many *live* (not yet ack'd, not dead-lettered) events are
    kept -- once exceeded, the oldest are evicted and counted in
    `dropped_count()`.
    """

    def __init__(self, spool_dir, cap: int = 10_000):
        self.spool_dir = Path(spool_dir)
        self.cap = cap
        self._dropped_count = 0
        self._cap_lock = threading.Lock()
        self.spool_dir.mkdir(parents=True, exist_ok=True)

    # -- internal helpers -------------------------------------------------

    def _write_file(self, path: Path, data: bytes) -> None:
        """Write `data` to `path` atomically: temp file + safe_replace.

        Split out from `append` purely so tests can monkeypatch this one
        method to simulate an I/O failure without needing a real
        unwritable filesystem -- see
        test_append_degrades_when_spool_unwritable. Uses `safe_replace`
        (see `agent_state._atomic`) rather than a bare `os.replace` so a
        transient Windows file-lock (Defender/indexer) is retried instead
        of `append` needlessly falling back to its no-op degrade path.
        """
        tmp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
        with open(tmp_path, "wb") as f:
            f.write(data)
        safe_replace(tmp_path, path)

    def _live_files(self):
        """This spool's live (non-dead) event files, oldest first.

        Plain lexical sort on filename -- correct because every filename
        starts with a fixed-width, zero-padded nanosecond timestamp (see
        module docstring). Only direct children of spool_dir match, so
        the dead/ subdirectory and any in-flight *.tmp-* file are never
        included (neither matches the "*.json" glob's exact suffix).
        """
        return sorted(self.spool_dir.glob(f"*{_SUFFIX}"), key=lambda p: p.name)

    def _enforce_cap(self) -> None:
        """Evict oldest live files down to `self.cap`, recording each drop.

        Locked so concurrent callers within one process never double
        count or double evict the same file. Cross-process cap races are
        not covered here (unlike events.next_gap_seq's file-locked
        counter) -- not part of this task's tested contract.

        Only a file THIS call itself successfully unlinks counts as a
        drop. The increment is deliberately inside the `try`, after
        `unlink()` succeeds -- not unconditionally after the try/except
        as a whole. `_live_files()` above is a snapshot; if a concurrent
        `ack()` legitimately removes the same oldest file in the window
        between that snapshot and this loop's own `unlink()` call, the
        unlink raises FileNotFoundError (a subclass of OSError) for a
        file that was never actually evicted by the cap -- it was ack'd.
        Counting that as a drop would be a lie (review finding: an
        earlier version incremented unconditionally after the try/except,
        over-counting exactly this race -- see
        test_cap_enforcement_does_not_count_a_file_already_removed_by_ack).
        """
        with self._cap_lock:
            files = self._live_files()
            overflow = len(files) - self.cap
            for path in files[: max(overflow, 0)]:
                try:
                    path.unlink()
                except OSError:
                    continue  # already gone (e.g. concurrently ack'd) -- not a genuine cap drop
                self._dropped_count += 1

    # -- public contract ---------------------------------------------------

    def append(self, event: dict) -> None:
        """Durably record `event` as its own file. NEVER raises.

        On any failure -- unwritable spool_dir, a monkeypatched
        `_write_file`, a malformed `event` -- this degrades to a no-op.
        The failure is still printed to stderr so it is not invisible,
        but it never propagates to the caller: telemetry must not be able
        to break whatever real command triggered it.
        """
        try:
            event_id = event.get("event_id") if isinstance(event, dict) else None
            filename = _make_filename(event_id)
            data = json.dumps(event, ensure_ascii=False).encode("utf-8")
            self.spool_dir.mkdir(parents=True, exist_ok=True)
            self._write_file(self.spool_dir / filename, data)
        except Exception as exc:
            _safe_warn(
                "WARNING: agent_state.spool failed to append event -- "
                f"degrading to no-op: {exc!r}"
            )
            return

        try:
            self._enforce_cap()
        except Exception as exc:
            _safe_warn(f"WARNING: agent_state.spool cap enforcement failed: {exc!r}")

    def read_batch(self, n: int) -> list:
        """Return up to `n` oldest live events, as parsed dicts.

        A file that fails to read or parse (e.g. truncated by something
        outside this module's own atomic write path) is skipped rather
        than raising, so one bad file cannot block the rest of the batch.
        """
        events = []
        for path in self._live_files()[:n]:
            try:
                events.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                _safe_warn(
                    f"WARNING: agent_state.spool skipped unreadable spool "
                    f"file {path.name!r}: {exc!r}"
                )
        return events

    def ack(self, event_ids) -> None:
        """Unlink the files for `event_ids` -- the server confirmed receipt.

        No lock: unlinking a specific, fully-written file is already an
        atomic OS operation, and nothing else in this class rewrites a
        shared structure it could race against. IDs not currently present
        are silently ignored -- ack is idempotent.
        """
        wanted = {str(i) for i in event_ids}
        for path in self._live_files():
            if _split_filename(path.name) in wanted:
                try:
                    path.unlink()
                except OSError:
                    pass  # already gone -- fine, that's the goal anyway

    def dead_letter(self, event_id, reason) -> None:
        """Move the file for `event_id` into a `dead/` subdir; record `reason`.

        Moves, never deletes -- per the module docstring, a server that
        refuses an event must not be able to make the client silently
        discard its queue. `reason` is written to a sidecar
        `<same-stem>.reason.json` file next to the moved event so it is
        recoverable for debugging, without altering the original event's
        bytes.
        """
        target_id = str(event_id)
        for path in self._live_files():
            if _split_filename(path.name) != target_id:
                continue
            dead_dir = self.spool_dir / _DEAD_DIRNAME
            dead_dir.mkdir(parents=True, exist_ok=True)
            dest = dead_dir / path.name
            safe_replace(path, dest)
            reason_path = dead_dir / f"{dest.stem}.reason.json"
            try:
                reason_path.write_text(
                    json.dumps(
                        {"event_id": target_id, "reason": str(reason)},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass  # best-effort -- the event itself is already safe
            return

    def dropped_count(self) -> int:
        """Cumulative events this instance has evicted via the size cap.

        In-memory only -- resets if the process restarts (see module
        docstring's Concurrency section).
        """
        return self._dropped_count
