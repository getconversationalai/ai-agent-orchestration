"""Tests for agent_state.spool -- durable one-file-per-event local spool
(Task 3).

Covers the brief's five exact cases (append/read/ack roundtrip, real
concurrent appenders losing nothing, the size cap dropping oldest and
recording it, dead-letter moving rather than deleting, and append
degrading to a no-op on a simulated I/O failure), plus the single most
important regression guard in this task:

test_append_persists_a_REAL_event_with_iso_timestamp appends the actual
output of `build_event()`, whose `client_time` field is a real ISO-8601
string containing colons. Every other test in this file uses a fake
`client_time` like "t1" -- which would NOT catch a bug where the spool
filename was built from `client_time` instead of `time.time_ns()`, since
colons in a Windows filename raise `OSError [Errno 22]` and, per
`append`'s never-raise contract, that failure would silently degrade to
a no-op. This test is the only one that would fail if that regression
were reintroduced.

Test isolation: build_event() internally calls
next_gap_seq(default_state_dir()), which falls back to the real
~/.agent-orchestration/agent_state/ unless AGENT_STATE_DIR is overridden.
The autouse fixture that redirects AGENT_STATE_DIR into a tmp_path is
defined once in conftest.py (hoisted there from test_events.py's
Task-2-era copy, since this file needs it too) rather than repeated here.

Fix round 1 (review findings) added:
- test_cap_enforcement_does_not_count_a_file_already_removed_by_ack --
  Finding 1: dropped_count() must not count a file a concurrent ack()
  already removed as a cap eviction.
- test_safe_warn_never_raises_even_when_stderr_is_broken and
  test_append_degrades_even_when_stderr_itself_is_broken -- Finding 2:
  the diagnostic stderr writes append() relies on must themselves never
  raise, even if stderr is closed/broken.
- test_cap_drops_oldest_and_records_it and
  test_dead_letter_moves_not_deletes were both tightened (minor findings)
  to assert on the SPECIFIC surviving/moved event, not just counts.
"""
import json
import sys
import threading

from agent_state.events import build_event
from agent_state.spool import Spool, _safe_warn


def test_append_read_ack_roundtrip(tmp_path):
    s = Spool(tmp_path, cap=100)
    s.append({"event_id": "a", "client_time": "t1"})
    assert [e["event_id"] for e in s.read_batch(10)] == ["a"]
    s.ack(["a"])
    assert s.read_batch(10) == []


def test_concurrent_appenders_lose_nothing(tmp_path):
    s = Spool(tmp_path, cap=1000)

    def w(i):
        s.append({"event_id": str(i), "client_time": f"t{i}"})

    ts = [threading.Thread(target=w, args=(i,)) for i in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(s.read_batch(1000)) == 50


def test_cap_drops_oldest_and_records_it(tmp_path):
    s = Spool(tmp_path, cap=2)
    for i in "abc":
        s.append({"event_id": i, "client_time": f"t{i}"})
    remaining = [e["event_id"] for e in s.read_batch(10)]
    # Not just "2 remain" -- the SPECIFIC oldest ("a") must be the one
    # evicted. A newest-first or random eviction policy would still
    # satisfy len(...) == 2 but fail this.
    assert remaining == ["b", "c"]
    assert s.dropped_count() >= 1


def test_dead_letter_moves_not_deletes(tmp_path):
    s = Spool(tmp_path, cap=100)
    s.append({"event_id": "a", "client_time": "t1"})
    s.dead_letter("a", "unconnected repo")
    assert s.read_batch(10) == []

    dead_dir = tmp_path / "dead"
    # The SPECIFIC moved event file must be present under dead/ -- not
    # merely "dead/ is non-empty", which would also pass if e.g. only a
    # reason sidecar were written and the actual event were lost.
    event_files = [p for p in dead_dir.glob("*.json") if not p.name.endswith(".reason.json")]
    assert len(event_files) == 1
    assert json.loads(event_files[0].read_text(encoding="utf-8"))["event_id"] == "a"

    # Bonus (not part of the minor-finding ask, but cheap while here):
    # the reason sidecar is also the specific, correct one for this event.
    reason_files = list(dead_dir.glob("*.reason.json"))
    assert len(reason_files) == 1
    assert json.loads(reason_files[0].read_text(encoding="utf-8")) == {
        "event_id": "a",
        "reason": "unconnected repo",
    }


def test_append_degrades_when_spool_unwritable(tmp_path, monkeypatch):
    s = Spool(tmp_path, cap=100)
    monkeypatch.setattr(s, "_write_file", lambda *a, **k: (_ for _ in ()).throw(OSError))
    s.append({"event_id": "a", "client_time": "t1"})  # must not raise


def test_cap_enforcement_does_not_count_a_file_already_removed_by_ack(tmp_path, monkeypatch):
    # Finding 1 regression: an earlier version of _enforce_cap incremented
    # dropped_count() unconditionally after the unlink try/except, even
    # when unlink() failed because a concurrent ack() had already removed
    # the same file. That over-counts a legitimately-ack'd event as a cap
    # drop. This reproduces the exact race deterministically: take a
    # snapshot of the live files (what _enforce_cap's own _live_files()
    # call would see), let a real ack() remove one of them for real, then
    # force _enforce_cap to act on the now-stale snapshot -- so its own
    # unlink() attempt hits a file that's already gone, exactly as would
    # happen if a concurrent ack() won a real race in production.
    s = Spool(tmp_path, cap=100)  # high cap: these two appends must not evict anything
    s.append({"event_id": "a", "client_time": "t1"})
    s.append({"event_id": "b", "client_time": "t2"})
    stale_snapshot = s._live_files()
    assert len(stale_snapshot) == 2

    s.ack(["a"])  # a concurrent ack wins the race and removes "a" for real, right now
    assert [e["event_id"] for e in s.read_batch(10)] == ["b"]

    s.cap = 1  # now force cap enforcement to believe an eviction is needed
    monkeypatch.setattr(s, "_live_files", lambda: stale_snapshot)  # the stale, pre-ack listing
    s._enforce_cap()

    assert s.dropped_count() == 0  # "a" was ack'd, not cap-evicted -- must not be counted
    assert [e["event_id"] for e in s.read_batch(10)] == ["b"]


class _ExplodingStderr:
    """Stand-in for sys.stderr whose write() always raises -- simulates a
    closed pipe, a dead console, or a service context with no stderr."""

    def write(self, *_a, **_k):
        raise OSError("stderr is broken")

    def flush(self, *_a, **_k):
        pass


def test_safe_warn_never_raises_even_when_stderr_is_broken(monkeypatch):
    # Finding 2 regression, direct unit test of the shared helper every
    # diagnostic write in the module routes through.
    monkeypatch.setattr(sys, "stderr", _ExplodingStderr())
    _safe_warn("this must not raise even though stderr is broken")  # must not raise


def test_append_degrades_even_when_stderr_itself_is_broken(tmp_path, monkeypatch):
    # Finding 2 regression, end-to-end: append's except-blocks used to log
    # via a bare print(..., file=sys.stderr) with no guard of their own.
    # If stderr itself is broken, that write raises and append propagates
    # it -- violating the never-raise contract via the logging path
    # itself, not the original failure it was trying to report.
    s = Spool(tmp_path, cap=100)
    monkeypatch.setattr(s, "_write_file", lambda *a, **k: (_ for _ in ()).throw(OSError))
    monkeypatch.setattr(sys, "stderr", _ExplodingStderr())
    s.append({"event_id": "a", "client_time": "t1"})  # must not raise, even now


def test_append_persists_a_REAL_event_with_iso_timestamp(tmp_path):
    # Guards the v2 regression: real build_event() output has colons in
    # client_time. Every other test in this file uses "t1" and would hide
    # a total-loss bug on Windows -- see module docstring.
    s = Spool(tmp_path, cap=100)
    ev = build_event("register", {}, {}, "m1")  # client_time is ISO with colons
    s.append(ev)
    assert [e["event_id"] for e in s.read_batch(10)] == [ev["event_id"]]
