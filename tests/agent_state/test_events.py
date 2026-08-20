"""Tests for agent_state.events -- event envelope + crash-safe per-machine
sequence counter (Task 2).

Covers the brief's four exact cases (unique event_ids, entity_updated as the
conflict key rather than gap_seq, the transcript-contents invariant, and
next_gap_seq's crash-safety after a truncated seq file), plus:

- a cross-PROCESS durability test for next_gap_seq. The brief explicitly
  flags that a same-process test can pass on an in-memory high-water mark
  that isn't actually durable -- this spawns a fresh `py` process per call
  so the only thing that can make values persist between calls is the file
  on disk, not Python state.
- a threaded concurrency test proving the lock actually serialises
  concurrent callers (the brief's "under a lock" requirement) rather than
  merely looking safe.
- coverage for build_event's other documented behaviours (full field set,
  defensive payload copy, entity_updated defaulting to None, tz-aware UTC
  client_time, repo/kind/machine_id passthrough) and next_gap_seq's harder
  corruption edge cases (missing state dir, primary file deleted outright,
  both files unreadable).

Design note this file locks in: build_event() has exactly the brief's 4-arg
signature (kind, repo_identity, payload, machine_id) with no state_dir
parameter, yet must return a real `gap_seq` -- so build_event() resolves it
internally via next_gap_seq(default_state_dir()). default_state_dir() falls
back to the real, machine-local ~/.agent-orchestration/agent_state/ (the
same directory family Task 8 uses for the refresh token and config.json)
when AGENT_STATE_DIR is unset. Every test in this file therefore gets an
autouse fixture pointing AGENT_STATE_DIR at a throwaway tmp_path directory,
so no test run ever touches the real machine's directory.
"""
import json
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from agent_state.events import (
    GapSeqError,
    build_event,
    default_state_dir,
    next_gap_seq,
)

TOOLS_DIR = Path.home() / ".ai-instructions" / "tools"


@pytest.fixture(autouse=True)
def _isolated_agent_state_dir(tmp_path, monkeypatch):
    """Redirect every test's default state dir into tmp_path.

    build_event() calls next_gap_seq(default_state_dir()), and
    default_state_dir() falls back to the real
    ~/.agent-orchestration/agent_state/ when AGENT_STATE_DIR is unset.
    Tests must never write there -- point it at a fresh throwaway
    directory for every test instead.
    """
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "agent_state_default"))


# ---------------------------------------------------------------------------
# build_event -- the brief's exact cases
# ---------------------------------------------------------------------------

def test_unique_event_ids():
    assert build_event("register", {}, {}, "m1")["event_id"] != \
           build_event("register", {}, {}, "m1")["event_id"]


def test_conflict_key_is_entity_updated_not_seq():
    ev = build_event("update-status", {}, {"updated": "2026-08-03T01:00"}, "m1")
    assert ev["entity_updated"] == "2026-08-03T01:00"


def test_payload_never_carries_transcript_contents():
    # A real, dirty payload: a legitimate transcript_path reference sitting
    # alongside an (accidental or malicious) transcript_contents key that
    # actually carries chat text. build_event must ACTIVELY strip the
    # contents key -- this is a passthrough function otherwise, so feeding
    # an already-clean payload here would prove nothing (that was the
    # review finding against the original version of this test).
    ev = build_event(
        "register", {},
        {
            "transcript_path": "/x/t.jsonl",
            "transcript_contents": "user: how do I reset my password?",
        },
        "m1",
    )
    assert "transcript_contents" not in ev["payload"]
    assert ev["payload"]["transcript_path"] == "/x/t.jsonl"
    assert all("transcript" not in k or k.endswith("path") for k in ev["payload"])


def test_transcript_ish_content_keys_are_dropped_regardless_of_exact_name():
    ev = build_event(
        "register", {},
        {
            "transcript_path": "/x/t.jsonl",
            "transcript_body": "full text here",
            "TRANSCRIPT_CONTENTS": "full text here too",
            "notes": "kept: not transcript-related at all",
        },
        "m1",
    )
    assert ev["payload"] == {
        "transcript_path": "/x/t.jsonl",
        "notes": "kept: not transcript-related at all",
    }


def test_payload_with_only_transcript_path_is_left_untouched():
    ev = build_event("register", {}, {"transcript_path": "/x/t.jsonl"}, "m1")
    assert ev["payload"] == {"transcript_path": "/x/t.jsonl"}


# ---------------------------------------------------------------------------
# build_event -- additional coverage for the rest of the documented contract
# ---------------------------------------------------------------------------

def test_event_id_is_a_real_uuid4():
    ev = build_event("register", {}, {}, "m1")
    parsed = uuid.UUID(ev["event_id"])
    assert parsed.version == 4
    assert str(parsed) == ev["event_id"]


def test_build_event_returns_exactly_the_contract_fields():
    ev = build_event("register", {}, {}, "m1")
    assert set(ev.keys()) == {
        "event_id", "kind", "repo", "payload", "machine_id",
        "entity_updated", "client_time", "gap_seq",
    }


def test_kind_repo_and_machine_id_pass_through():
    repo_identity = {
        "github_repo_id": None, "remote_key": "github.com/org/repo",
        "host": "github.com", "owner": "org", "name": "repo",
    }
    ev = build_event("complete", repo_identity, {}, "machine-42")
    assert ev["kind"] == "complete"
    assert ev["repo"] == repo_identity
    assert ev["machine_id"] == "machine-42"


def test_entity_updated_is_none_when_payload_has_no_updated_field():
    ev = build_event("register", {}, {"feature": "x"}, "m1")
    assert ev["entity_updated"] is None


def test_entity_updated_is_none_for_empty_payload():
    ev = build_event("register", {}, {}, "m1")
    assert ev["entity_updated"] is None


def test_payload_is_defensively_copied_not_aliased():
    payload = {"updated": "2026-08-03T01:00"}
    ev = build_event("update-status", {}, payload, "m1")
    payload["updated"] = "MUTATED-AFTER-THE-FACT"
    assert ev["payload"]["updated"] == "2026-08-03T01:00"
    assert ev["entity_updated"] == "2026-08-03T01:00"


def test_client_time_is_utc_and_timezone_aware():
    ev = build_event("register", {}, {}, "m1")
    parsed = datetime.fromisoformat(ev["client_time"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_gap_seq_increments_across_build_event_calls():
    first = build_event("register", {}, {}, "m1")["gap_seq"]
    second = build_event("register", {}, {}, "m1")["gap_seq"]
    assert isinstance(first, int) and isinstance(second, int)
    assert second == first + 1


# ---------------------------------------------------------------------------
# next_gap_seq -- the brief's exact crash-safety case
# ---------------------------------------------------------------------------

def test_gap_seq_atomic_and_crash_safe(tmp_path):
    assert next_gap_seq(tmp_path) == 1
    assert next_gap_seq(tmp_path) == 2
    # simulate truncation: counter must NOT reset to 1 (v1 finding)
    (tmp_path / "seq.json").write_text("")   # corrupt
    assert next_gap_seq(tmp_path) >= 3        # refuses to go backwards


# ---------------------------------------------------------------------------
# next_gap_seq -- cross-process durability. The brief: "also add a
# cross-PROCESS test (subprocess) -- a same-process test can pass on an
# in-memory high-water mark that isn't actually durable."
# ---------------------------------------------------------------------------

def _next_gap_seq_in_fresh_process(state_dir) -> int:
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(TOOLS_DIR)!r}); "
        "from agent_state.events import next_gap_seq; "
        f"print(next_gap_seq({str(state_dir)!r}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    return int(result.stdout.strip())


def test_gap_seq_atomic_and_crash_safe_across_processes(tmp_path):
    state_dir = tmp_path / "cross_process"
    assert _next_gap_seq_in_fresh_process(state_dir) == 1
    assert _next_gap_seq_in_fresh_process(state_dir) == 2
    # Corrupt the primary file from OUTSIDE any process that wrote it. There
    # is zero shared Python state between these subprocess calls, so
    # recovering to >= 3 here (rather than resetting to 1) proves the
    # high-water mark is genuinely persisted on disk.
    (state_dir / "seq.json").write_text("")
    assert _next_gap_seq_in_fresh_process(state_dir) >= 3


# ---------------------------------------------------------------------------
# next_gap_seq -- concurrency ("under a lock so two concurrent processes
# never get the same seq")
# ---------------------------------------------------------------------------

def test_next_gap_seq_concurrent_callers_never_collide(tmp_path):
    results = []
    results_lock = threading.Lock()

    def worker():
        value = next_gap_seq(tmp_path)
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == list(range(1, 21))


# ---------------------------------------------------------------------------
# next_gap_seq -- GENUINELY overlapping multi-PROCESS concurrency.
#
# test_next_gap_seq_concurrent_callers_never_collide (above) proves the
# lock serialises concurrent THREADS. test_gap_seq_atomic_and_crash_safe_
# across_processes proves durability using subprocess.run -- but run()
# blocks until each child exits before the next one is even started, so
# those calls are strictly sequential and never actually contend for the
# lock. The deployment reality is multiple independent agent PROCESSES on
# one machine racing for the same seq.json. This test launches several
# child processes with Popen (which does NOT block), so they are genuinely
# running at the same time, each hammering next_gap_seq in a tight loop
# against the same state_dir, and only then collects and checks all the
# numbers they were handed out.
# ---------------------------------------------------------------------------

_CONCURRENT_PROCESSES = 3
_ITERATIONS_PER_PROCESS = 60


def _spawn_gap_seq_hammer(state_dir, iterations) -> subprocess.Popen:
    """Start (not wait for) a child process that calls next_gap_seq
    `iterations` times against `state_dir`, printing one value per line.
    """
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(TOOLS_DIR)!r}); "
        "from agent_state.events import next_gap_seq; "
        "[print(next_gap_seq(" + repr(str(state_dir)) + ")) for _ in "
        f"range({iterations})]"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_gap_seq_genuinely_concurrent_processes_never_collide(tmp_path):
    state_dir = tmp_path / "genuinely_concurrent"

    # Launch ALL children back-to-back with Popen (non-blocking) BEFORE
    # waiting on any of them, so their execution windows genuinely overlap
    # in wall-clock time -- this is what actually exercises lock contention
    # across process boundaries, unlike a run()-per-call loop.
    processes = [
        _spawn_gap_seq_hammer(state_dir, _ITERATIONS_PER_PROCESS)
        for _ in range(_CONCURRENT_PROCESSES)
    ]

    all_values = []
    for proc in processes:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"child failed: {err}"
        values = [int(line) for line in out.split()]
        assert len(values) == _ITERATIONS_PER_PROCESS
        all_values.extend(values)

    total = _CONCURRENT_PROCESSES * _ITERATIONS_PER_PROCESS
    # No two processes were ever handed the same number...
    assert len(all_values) == len(set(all_values)), (
        f"collision detected across {_CONCURRENT_PROCESSES} concurrent "
        f"processes: {sorted(all_values)}"
    )
    # ...and nothing was skipped either -- exactly the contiguous run
    # 1..total, however the individual values were interleaved between
    # processes.
    assert sorted(all_values) == list(range(1, total + 1))


# ---------------------------------------------------------------------------
# next_gap_seq -- harder corruption edge cases
# ---------------------------------------------------------------------------

def test_next_gap_seq_creates_missing_state_dir(tmp_path):
    state_dir = tmp_path / "does" / "not" / "exist" / "yet"
    assert not state_dir.exists()
    assert next_gap_seq(state_dir) == 1


def test_next_gap_seq_recovers_when_primary_file_deleted_entirely(tmp_path):
    assert next_gap_seq(tmp_path) == 1
    assert next_gap_seq(tmp_path) == 2
    (tmp_path / "seq.json").unlink()   # gone outright, not just truncated
    assert next_gap_seq(tmp_path) >= 3


def test_next_gap_seq_refuses_when_both_files_are_corrupt(tmp_path):
    assert next_gap_seq(tmp_path) == 1
    (tmp_path / "seq.json").write_text("")
    (tmp_path / "seq.hwm.json").write_text("not json at all")
    with pytest.raises(GapSeqError):
        next_gap_seq(tmp_path)


def test_next_gap_seq_writes_are_atomic_no_leftover_tmp_files(tmp_path):
    next_gap_seq(tmp_path)
    assert not (tmp_path / "seq.json.tmp").exists()
    assert not (tmp_path / "seq.hwm.json.tmp").exists()
    assert json.loads((tmp_path / "seq.json").read_text()) == {"seq": 1}


# ---------------------------------------------------------------------------
# default_state_dir
# ---------------------------------------------------------------------------

def test_default_state_dir_honours_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "custom"))
    assert default_state_dir() == tmp_path / "custom"


def test_default_state_dir_falls_back_to_agent_orchestration_home(monkeypatch):
    monkeypatch.delenv("AGENT_STATE_DIR", raising=False)
    assert default_state_dir() == Path.home() / ".agent-orchestration" / "agent_state"
