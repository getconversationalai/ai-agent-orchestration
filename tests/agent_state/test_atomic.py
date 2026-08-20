"""Tests for agent_state._atomic -- shared bounded-retry wrapper around
os.replace for Windows' two known-transient lock errors (hardening fix,
post-Phase-A).

Background: `tests/agent_state/test_events.py::
test_gap_seq_genuinely_concurrent_processes_never_collide` was intermittently
flaky on Windows -- `os.replace` inside `events._atomic_write_json`
occasionally raised `PermissionError [WinError 5]` (or `[WinError 32]`) when
Windows Defender / the search indexer briefly held the temp or target file
during a burst of concurrent-process file I/O (see Task 4's report and
progress.md's Task 4 entry). The failure was already contained -- the
emitter's never-raise barrier degrades it to a dropped event -- but the
primitive itself should not fail in the first place when the lock clears a
few milliseconds later, and the concurrent test should stop flaking.

Covers:
  - safe_replace's own contract in isolation (transient errors retried and
    eventually succeed with the exact expected call count; non-transient
    OSError and non-OSError exceptions re-raise on the very first attempt,
    no retry; exhausting the retry budget re-raises the last error, not a
    swallowed/generic one).
  - that each of the four real call sites this fix touches
    (events._atomic_write_json via next_gap_seq, spool._write_file via
    Spool.append, spool.dead_letter, emit._machine_id) is actually WIRED to
    safe_replace, not just that safe_replace itself works in isolation -- a
    missed call site would pass every safe_replace-only test below and
    still leave that one path exactly as flaky as before this fix.

All monkeypatching targets `agent_state._atomic.os.replace` specifically --
that is the exact name `safe_replace` calls, so patching it there affects
every caller regardless of which module imported the `safe_replace` name.
"""
import json
import os

import pytest

from agent_state import _atomic
from agent_state._atomic import safe_replace


def _transient_lock_error(winerror: int) -> OSError:
    """Build an OSError carrying a specific `winerror`, as Windows does.

    A real Windows PermissionError gets `winerror` set by the interpreter
    when the underlying syscall fails; there is no public constructor
    keyword for it, so tests set the attribute directly after construction
    -- exactly what `getattr(exc, "winerror", None)` reads at runtime.
    """
    exc = PermissionError(f"simulated transient lock (winerror={winerror})")
    exc.winerror = winerror
    return exc


# ---------------------------------------------------------------------------
# safe_replace -- own contract, in isolation
# ---------------------------------------------------------------------------

def test_safe_replace_moves_file_like_os_replace(tmp_path):
    """No injected failure at all -- real os.replace, unpatched. Proves
    basic correctness independent of the retry machinery."""
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello")

    safe_replace(src, dst)

    assert not src.exists()
    assert dst.read_text() == "hello"


@pytest.mark.parametrize("winerror", [5, 32])
def test_safe_replace_retries_transient_winerror_then_succeeds(tmp_path, monkeypatch, winerror):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("payload")

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(s, d):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise _transient_lock_error(winerror)
        return real_replace(s, d)

    monkeypatch.setattr(_atomic.os, "replace", flaky_replace)

    safe_replace(src, dst)

    assert calls["n"] == 4  # 3 injected failures + 1 real success
    assert not src.exists()
    assert dst.read_text() == "payload"


def test_safe_replace_reraises_non_transient_oserror_without_retry(tmp_path, monkeypatch):
    calls = {"n": 0}

    def bad_replace(s, d):
        calls["n"] += 1
        raise _transient_lock_error(18)  # ERROR_NOT_SAME_DEVICE -- not in (5, 32)

    monkeypatch.setattr(_atomic.os, "replace", bad_replace)

    with pytest.raises(OSError) as excinfo:
        safe_replace(tmp_path / "a", tmp_path / "b")

    assert excinfo.value.winerror == 18
    assert calls["n"] == 1  # no retry at all


def test_safe_replace_reraises_non_oserror_exception_without_retry(tmp_path, monkeypatch):
    calls = {"n": 0}

    def bad_replace(s, d):
        calls["n"] += 1
        raise ValueError("not a filesystem error at all")

    monkeypatch.setattr(_atomic.os, "replace", bad_replace)

    with pytest.raises(ValueError):
        safe_replace(tmp_path / "a", tmp_path / "b")

    assert calls["n"] == 1


def test_safe_replace_reraises_missing_source_without_retry(tmp_path):
    """Real (unpatched) os.replace on a nonexistent source -- a genuine,
    non-transient failure with no injected mock at all."""
    missing_src = tmp_path / "does-not-exist.txt"
    dst = tmp_path / "dst.txt"

    with pytest.raises(FileNotFoundError):
        safe_replace(missing_src, dst)


def test_safe_replace_reraises_last_error_after_exhausting_retries(tmp_path, monkeypatch):
    """Every attempt fails transiently -- the retry budget is genuinely
    bounded (not an infinite/very-long retry loop), and the LAST error is
    what escapes, never swallowed into some other exception type."""
    calls = {"n": 0}

    def always_flaky(s, d):
        calls["n"] += 1
        raise _transient_lock_error(5)

    monkeypatch.setattr(_atomic.os, "replace", always_flaky)

    with pytest.raises(PermissionError) as excinfo:
        safe_replace(tmp_path / "a", tmp_path / "b")

    assert excinfo.value.winerror == 5
    assert calls["n"] == _atomic._MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Wiring proof -- each real call site actually routes through safe_replace
# ---------------------------------------------------------------------------

def test_next_gap_seq_call_site_survives_transient_replace_lock_error(tmp_path, monkeypatch):
    from agent_state.events import next_gap_seq

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(s, d):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _transient_lock_error(32)
        return real_replace(s, d)

    monkeypatch.setattr(_atomic.os, "replace", flaky_replace)

    assert next_gap_seq(tmp_path) == 1
    assert calls["n"] >= 3  # at least 2 injected failures + 1 real success


def test_spool_append_call_site_survives_transient_replace_lock_error(tmp_path, monkeypatch):
    from agent_state.spool import Spool

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(s, d):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _transient_lock_error(5)
        return real_replace(s, d)

    monkeypatch.setattr(_atomic.os, "replace", flaky_replace)

    spool = Spool(tmp_path, cap=100)
    spool.append({"event_id": "a", "client_time": "t1"})

    # Before this fix: a transient lock here made append() degrade to a
    # silent no-op (its documented never-raise contract). After: the write
    # survives the transient lock and the event is NOT dropped.
    assert [e["event_id"] for e in spool.read_batch(10)] == ["a"]
    assert calls["n"] >= 3


def test_spool_dead_letter_call_site_survives_transient_replace_lock_error(tmp_path, monkeypatch):
    from agent_state.spool import Spool

    spool = Spool(tmp_path, cap=100)
    spool.append({"event_id": "a", "client_time": "t1"})

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(s, d):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _transient_lock_error(32)
        return real_replace(s, d)

    monkeypatch.setattr(_atomic.os, "replace", flaky_replace)

    spool.dead_letter("a", "unconnected repo")

    dead_dir = tmp_path / "dead"
    event_files = [p for p in dead_dir.glob("*.json") if not p.name.endswith(".reason.json")]
    assert len(event_files) == 1
    assert calls["n"] >= 3


def test_machine_id_call_site_survives_transient_replace_lock_error(tmp_path, monkeypatch):
    from agent_state import emit as emit_module
    from agent_state.events import default_state_dir

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(s, d):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _transient_lock_error(5)
        return real_replace(s, d)

    monkeypatch.setattr(_atomic.os, "replace", flaky_replace)

    mid = emit_module._machine_id()

    # Before this fix: a transient lock here made persistence fail,
    # falling back to an ephemeral (unpersisted) id for this call only.
    # After: the id is actually written to machine.json.
    persisted = json.loads((default_state_dir() / "machine.json").read_text(encoding="utf-8"))
    assert persisted["machine_id"] == mid
    assert calls["n"] >= 3
