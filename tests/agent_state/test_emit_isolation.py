"""Task 4 isolation tests: the emitter must never be able to break workboard.py.

Three guarantees, in priority order:

  1. ``emit_safely`` NEVER raises -- not for OSError, ValueError, TimeoutError,
     a plain Exception, a KeyboardInterrupt, or a SystemExit raised inside
     telemetry (the ``except BaseException`` barrier). A command that has
     already written its files must not be aborted by its own telemetry.
  2. A real workboard command whose emission fails still succeeds (exit 0).
  3. With the kill switch OFF (the default), every command's WORKBOARD.json /
     PROGRESS.json is byte-identical to the pre-Task-4 baseline captured from
     the unmodified workboard.py (values of clock/machine fields masked by
     ``_emit_harness.normalise`` -- see that module).

A fourth test proves the ON path actually works: the resolved entity id
(``entry["id"]`` / ``sub_id`` / ``_resolve_plan_id``) reaches the spooled event.

The autouse fixture in conftest.py redirects AGENT_STATE_DIR into tmp_path, so
the spool, machine-id and gap-seq files are fully isolated -- no test touches
the developer's real agent-state directory.
"""

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

import _emit_harness as H
from agent_state import emit

GOLDENS_PATH = Path(__file__).with_name("emit_golden_snapshots.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def scratch_repo(tmp_path, monkeypatch):
    """A throwaway git repo the workboard writes into, chdir'd as cwd.

    Creates a ``.git`` dir and asserts ``find_project_root()`` resolves INSIDE
    ``tmp_path`` before any command runs -- without this guard, an in-process
    workboard command would discover the developer's REAL repo above the temp
    dir and clobber live coordination state.
    """
    import workboard

    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    resolved = workboard.find_project_root().resolve()
    assert resolved == tmp_path.resolve(), (
        f"find_project_root() resolved to {resolved}, OUTSIDE the scratch repo "
        f"{tmp_path} -- aborting so we never write into a real repo."
    )
    monkeypatch.delenv("AGENT_STATE_EMIT", raising=False)
    return tmp_path


@pytest.fixture
def golden():
    data = json.loads(GOLDENS_PATH.read_text(encoding="utf-8"))
    assert data["sequence_len"] == len(H.SEQUENCE), (
        "golden sequence length != harness SEQUENCE length -- the goldens are "
        "stale relative to the command sequence; recapture is NOT the fix here "
        "(recapture must run against the original workboard.py)."
    )
    assert len(data["stdout"]) == len(H.SEQUENCE), (
        "stdout golden length != harness SEQUENCE length"
    )
    return data


# ---------------------------------------------------------------------------
# 1. emit_safely never raises -- the BaseException barrier
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "failure", [OSError, ValueError, TimeoutError, Exception, KeyboardInterrupt, SystemExit]
)
def test_emit_never_raises(monkeypatch, failure):
    monkeypatch.setenv("AGENT_STATE_EMIT", "1")
    monkeypatch.setattr(emit, "_append", lambda *a, **k: (_ for _ in ()).throw(failure()))
    # Must not propagate `failure`; must return None.
    assert emit.emit_safely("register", {}) is None


def test_emit_off_is_noop_and_never_touches_append(monkeypatch):
    """With the switch off, emit_safely must not even call _append."""
    monkeypatch.delenv("AGENT_STATE_EMIT", raising=False)
    calls = []
    monkeypatch.setattr(emit, "_append", lambda *a, **k: calls.append((a, k)))
    assert emit.emit_safely("register", {"id": "x"}) is None
    assert calls == [], "emission ran while the kill switch was off"


# ---------------------------------------------------------------------------
# 2. A command whose emission fails still succeeds
# ---------------------------------------------------------------------------
def test_command_succeeds_when_emission_fails(monkeypatch, scratch_repo):
    monkeypatch.setenv("AGENT_STATE_EMIT", "1")
    monkeypatch.setattr(emit, "_append", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    rc = H.run_workboard(
        ["register", "--feature", "X", "--tool", "claude",
         "--branch", "feat/x", "--worktree", str(scratch_repo)]
    )
    assert rc == 0
    # The files were still written correctly despite the emission failure.
    wb = json.loads((scratch_repo / "WORKBOARD.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in wb["active"]] == ["x"]


# ---------------------------------------------------------------------------
# 3. Switch OFF -> byte-identical to the pre-Task-4 baseline
# ---------------------------------------------------------------------------
def test_switch_off_is_byte_identical(scratch_repo, golden):
    # replay() runs the shared SEQUENCE with the switch OFF and a LIVE clock;
    # normalise() masks the timestamps so the compare is machine/clock-agnostic.
    produced = H.replay(freeze_clock=False)
    assert len(produced) == len(golden["snapshots"])
    for i, (got, want) in enumerate(zip(produced, golden["snapshots"])):
        cmd = " ".join(H.SEQUENCE[i])
        assert got["wb"] == want["wb"], f"WORKBOARD.json diverged after: {cmd}"
        assert got["prog"] == want["prog"], f"PROGRESS.json diverged after: {cmd}"


# ---------------------------------------------------------------------------
# 4. Switch ON -> the resolved id reaches the spooled event
# ---------------------------------------------------------------------------
def test_switch_on_emits_events_with_resolved_ids(monkeypatch, scratch_repo):
    monkeypatch.setenv("AGENT_STATE_EMIT", "1")
    # Avoid a real git subprocess against the empty .git dir; identity content
    # is irrelevant to what this test asserts (the resolved entity id).
    monkeypatch.setattr(
        emit, "resolve_identity",
        lambda p: {"github_repo_id": None, "remote_key": None,
                   "host": None, "owner": None, "name": None},
    )

    for argv in [
        ["register", "--feature", "Alpha", "--tool", "claude",
         "--branch", "feat/alpha", "--worktree", "/wt/alpha"],
        ["register-subagent", "--parent", "Alpha", "--feature", "SubA"],
        ["register-plan", "--feature", "Alpha", "--plan-title", "Alpha Plan"],
        ["start", "--feature", "Alpha", "--at", ""],
    ]:
        assert H.run_workboard(argv) == 0

    events = emit._spool().read_batch(100)
    by_kind = {}
    for ev in events:
        by_kind.setdefault(ev["kind"], []).append(ev)

    # register -> resolved entry id "alpha"
    assert by_kind["register"][0]["payload"]["id"] == "alpha"
    # register-subagent -> resolved sub_id "alpha/suba" + parent link
    assert by_kind["register-subagent"][0]["payload"]["id"] == "alpha/suba"
    assert by_kind["register-subagent"][0]["payload"]["parent_id"] == "alpha"
    # register-plan / start -> resolved plan id via _resolve_plan_id
    assert by_kind["register-plan"][0]["payload"]["id"] == "alpha"
    assert by_kind["start"][0]["payload"]["id"] == "alpha"
    # entity_updated is carried from payload["updated"] for the work item
    assert by_kind["register"][0]["entity_updated"] is not None


# ===========================================================================
# Regression guards (fix round 1) — three load-bearing invariants that were
# correct but previously had no committed automated guard.
# ===========================================================================

# --- GUARD 1 (constraint 6): migration materialisation emits ONE snapshot -----
# A pre-JSON legacy WORKBOARD.md (old format: no "(id)" before the em-dash, which
# is what migrate_legacy_markdown's regex parses).
LEGACY_MD = (
    "# Workboard\n\n## Active Work\n"
    "- **Legacy Feature** — claude — Started 2026-01-01. Status: active.\n"
    "  Branch: feat/legacy. Worktree: /wt/legacy.\n\n"
    "## Completed\n_(none)_\n"
)


def test_migration_snapshot_emitted_once_on_first_write(monkeypatch, scratch_repo):
    """Legacy WORKBOARD.md, no JSON: the first command that triggers
    migrate_legacy_markdown materialises the JSON AND emits exactly one snapshot
    event (covering the `show`/`refresh-markdown` paths that write outside
    `transaction`); a second run does NOT re-emit, since materialisation only
    happens on the first JSON write."""
    monkeypatch.setenv("AGENT_STATE_EMIT", "1")
    monkeypatch.setattr(
        emit, "resolve_identity",
        lambda p: {"github_repo_id": None, "remote_key": None,
                   "host": None, "owner": None, "name": None},
    )
    (scratch_repo / "WORKBOARD.md").write_text(LEGACY_MD, encoding="utf-8")
    assert not (scratch_repo / "WORKBOARD.json").exists()

    # `show` writes outside `transaction`; it must still emit the snapshot.
    assert H.run_workboard(["show"]) == 0
    assert (scratch_repo / "WORKBOARD.json").exists(), "migration should have materialised JSON"

    snaps = [e for e in emit._spool().read_batch(100) if e["kind"] == "snapshot"]
    assert len(snaps) == 1, f"expected exactly one snapshot, got {len(snaps)}"
    assert snaps[0]["payload"]["active"][0]["feature"] == "Legacy Feature"

    # Second run: JSON now exists -> migrate is a no-op -> no new snapshot.
    assert H.run_workboard(["show"]) == 0
    snaps2 = [e for e in emit._spool().read_batch(100) if e["kind"] == "snapshot"]
    assert len(snaps2) == 1, f"snapshot re-emitted on second run: got {len(snaps2)}"


# --- GUARD 2 (constraint 1): STDOUT byte-identical to the pre-change baseline --
def test_switch_off_stdout_is_byte_identical(scratch_repo, golden):
    """The file-bytes golden test covers WORKBOARD/PROGRESS only. Command stdout
    carries no timestamps or machine paths, so it is compared verbatim against
    stdout captured from the UNMODIFIED workboard.py (a subprocess run, stored in
    the goldens). The switch is off (scratch_repo delenv'd it) and emit_safely
    prints nothing on the off path, so any divergence is a real stdout
    regression."""
    os.environ.pop("AGENT_STATE_EMIT", None)
    produced = []
    for argv in H.SEQUENCE:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = H.run_workboard(argv)
        assert rc == 0, f"command exited {rc!r}: {argv}"
        produced.append(buf.getvalue().replace("\r\n", "\n").replace("\r", "\n"))
    assert len(produced) == len(golden["stdout"])  # no silent zip truncation
    for i, (got, want) in enumerate(zip(produced, golden["stdout"])):
        assert got == want, (
            f"stdout diverged (switch off) after: {' '.join(H.SEQUENCE[i])}\n"
            f"  got : {got!r}\n  want: {want!r}"
        )


# --- GUARD 3 (constraint 3): emission happens AFTER the lock is released -------
def _workboard_lock_is_free(workboard):
    """True iff the WORKBOARD lock can be acquired right now (non-blocking).

    Uses workboard's own _lock/_unlock (msvcrt LK_NBLCK on Windows, fcntl
    LOCK_EX|LOCK_NB elsewhere) on a SECOND handle. These byte-range locks conflict
    even within the same process (verified on Windows; fcntl locks per open file
    description do likewise), so a HELD lock makes this return False and a RELEASED
    lock makes it return True — exactly the signal that distinguishes "emitting
    inside the lock" from "emitting after it"."""
    lf = open(workboard.lock_path(), "w")
    try:
        workboard._lock(lf)
        workboard._unlock(lf)
        return True
    except OSError:
        return False
    finally:
        lf.close()


def test_emission_runs_after_lock_released(monkeypatch, scratch_repo):
    """Prove emission runs in main()'s post-transaction slot, AFTER release_lock,
    never inside the locked region. During emission the probe re-acquires the
    WORKBOARD lock; success means the lock is free at that instant."""
    import workboard
    probe = {}

    def probing_append(kind, payload):
        probe["lock_free_during_emit"] = _workboard_lock_is_free(workboard)

    monkeypatch.setenv("AGENT_STATE_EMIT", "1")
    monkeypatch.setattr(emit, "_append", probing_append)

    # Self-check: the probe is not a tautology. While the lock is genuinely HELD
    # it must report not-free; if this fails the mechanism is unreliable here and
    # the post-lock assertion below would be meaningless.
    held = workboard.acquire_lock()
    try:
        assert _workboard_lock_is_free(workboard) is False, (
            "probe reported the lock free while it was held — mechanism unreliable"
        )
    finally:
        workboard.release_lock(held)

    assert H.run_workboard(
        ["register", "--feature", "Alpha", "--tool", "claude",
         "--branch", "feat/a", "--worktree", "/wt/a"]
    ) == 0
    assert probe.get("lock_free_during_emit") is True, (
        "emission ran while the WORKBOARD lock was still held — it must run "
        "AFTER release_lock, in main()'s post-transaction slot."
    )
