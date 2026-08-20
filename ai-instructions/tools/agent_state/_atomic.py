"""agent_state._atomic -- shared bounded-retry wrapper around os.replace.

`os.replace(src, dst)` is the atomic-rename primitive every write path in
this package builds on (`events._atomic_write_json`, `spool._write_file`,
`spool.dead_letter`, `emit._machine_id`): write to a temp file, then
`os.replace` it into place, so a crash mid-write leaves either the whole
old file or the whole new one, never a torn partial write.

On Windows that primitive is not as reliable as it looks. `os.replace`
occasionally raises `PermissionError` when Windows Defender, the search
indexer, or another short-lived process briefly has the source or
destination file open at the exact instant of the rename:

    WinError 5  (ERROR_ACCESS_DENIED)     -- something briefly has the
                                              file open for read/write.
    WinError 32 (ERROR_SHARING_VIOLATION) -- something has the file open
                                              with a conflicting share mode.

Both are transient -- the lock holder is normally gone within a
millisecond or two. Without a retry, one unlucky rename becomes a real
failure that propagates straight into whatever this package's own
never-raise barriers exist to contain (`spool.append` degrading to a
no-op, `emit_safely` swallowing it, or `events.next_gap_seq` raising
`GapSeqError`). This module exists so those barriers stop having to
absorb a failure mode that a few milliseconds of retry would avoid
entirely -- it is a robustness layer BENEATH those contracts, not a
replacement for them: every caller's existing failure behaviour on a
genuine (non-transient, or retry-exhausted) failure is unchanged.

`safe_replace(src, dst)` is a drop-in replacement for `os.replace(src,
dst)`: same two-argument call, same return value (``None``), identical
behaviour for everything that is NOT one of the two transient codes
above.

Trap this module avoids
------------------------
Retrying blindly on ANY `OSError` would silently paper over real bugs --
a cross-device rename, a missing parent directory, a permissions problem
that will never clear on its own. Those must fail immediately, exactly as
`os.replace` already does, so they surface as real errors instead of a
few extra milliseconds of latency followed by the same failure. Only the
two known-transient Windows lock codes are retried; every other
`OSError` -- and any non-OSError exception entirely -- re-raises on the
very first attempt, no retry.

Retry policy
------------
Up to `_MAX_ATTEMPTS` (5) attempts total (1 initial + up to 4 retries),
sleeping between failed attempts starting at `_INITIAL_DELAY_SECONDS`
(2ms) and doubling each time (2ms, 4ms, 8ms, 16ms) -- worst case ~30ms of
total sleep across a whole `safe_replace` call: long enough to clear the
lock windows this is meant to survive, short enough that no caller or
test notices the difference. On the 5th consecutive transient failure,
the most recent exception is re-raised rather than swallowed -- this
module hardens the primitive, it does not change any caller's failure
contract.

On non-Windows platforms `OSError` instances never carry a `winerror`
attribute, so `_is_transient_lock_error` is always False there and
`safe_replace` behaves exactly like a direct `os.replace` call -- no
retries, no behaviour change.
"""

import os
import time

# The two Windows error codes this module treats as transient and worth
# retrying. Anything else re-raises immediately -- see "Trap this module
# avoids" above.
#   5  = ERROR_ACCESS_DENIED     -- e.g. Defender/indexer briefly holds a
#                                   read handle on src or dst.
#   32 = ERROR_SHARING_VIOLATION -- e.g. another process has the file open
#                                   with a conflicting share mode.
_TRANSIENT_WINERRORS = (5, 32)

_MAX_ATTEMPTS = 5
_INITIAL_DELAY_SECONDS = 0.002  # 2ms; doubles on each subsequent retry


def _is_transient_lock_error(exc: OSError) -> bool:
    """True iff `exc` is one of the two known-transient Windows lock errors.

    `winerror` only exists on `OSError` instances raised on Windows; on any
    other platform `getattr(exc, "winerror", None)` is always None, so this
    is always False there -- see the module docstring's platform note.
    """
    return getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS


def safe_replace(src, dst) -> None:
    """`os.replace(src, dst)` with bounded retry on transient Windows locks.

    Behaves exactly like `os.replace` except: when a failure is one of the
    two transient lock errors documented in this module's docstring, retries
    up to `_MAX_ATTEMPTS` times total with a short escalating sleep between
    attempts before giving up. Any other `OSError` (e.g. cross-device, a
    missing parent directory) -- or any non-OSError exception -- re-raises
    immediately on the first attempt; it is never retried. On exhausting all
    `_MAX_ATTEMPTS` attempts, the most recent exception is re-raised (never
    swallowed into some other error type).
    """
    delay = _INITIAL_DELAY_SECONDS
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if attempt >= _MAX_ATTEMPTS or not _is_transient_lock_error(exc):
                raise
            time.sleep(delay)
            delay *= 2
