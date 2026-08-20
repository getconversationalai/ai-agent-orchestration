"""agent_state.emit -- the workboard.py event emitter (Task 4).

`emit_safely(kind, payload)` is the ONLY entry point `workboard.py` calls. It
is a *barrier*, not just a helper:

  - It is gated on the ``AGENT_STATE_EMIT`` kill switch (off by default, via
    ``agent_state.emit_enabled``). With the switch off it returns immediately,
    having touched nothing -- this is what makes wiring it into the live,
    shared ``workboard.py`` provably safe: every command's on-disk JSON and
    stdout are byte-for-byte unchanged when emission is off.
  - It wraps everything in ``except BaseException`` -- deliberately broader
    than ``except Exception``. A ``KeyboardInterrupt`` or ``SystemExit`` raised
    *inside telemetry* must not abort a workboard command that has ALREADY
    written its files and released its lock. Losing an event is acceptable;
    corrupting or aborting the command that produced it is not (the same rule
    the spool and events modules are built on).
  - It ALWAYS returns ``None``.

The real work lives in ``_append``: resolve the repo identity and this
machine's id, build the event envelope, and append it to the durable local
spool. ``_append`` is a module-level function precisely so the tests can
monkeypatch it to raise every failure mode (OSError, ValueError,
TimeoutError, Exception, KeyboardInterrupt) and assert the barrier swallows
each one.

Where things are resolved
-------------------------
Nothing machine-specific is passed in by ``workboard.py`` -- the caller hands
over only ``(kind, payload)``. ``_append`` resolves the rest itself so the
call site in the shared script stays a single trivial line:

  * repo identity  -- ``identity.resolve_identity`` on the repo root walked up
    from cwd (mirrors ``workboard.find_project_root`` without importing it).
  * machine id     -- a UUID4 minted once and persisted under the same
    machine-local, ``AGENT_STATE_DIR``-overridable directory family
    ``events.default_state_dir`` uses. Two "machines" simulated on one box
    (Task 11) point ``AGENT_STATE_DIR`` at two directories and therefore get
    two distinct machine ids -- exactly what the cross-machine tiebreak needs.
  * spool          -- a ``spool/`` subdir of that same state directory.

All three honour ``AGENT_STATE_DIR``, so the test suite's autouse fixture
(which redirects it into ``tmp_path``) fully isolates emission -- no test can
write to the developer's real spool or machine-id file.
"""

import json
import sys
import uuid
from pathlib import Path

from agent_state import emit_enabled
from agent_state._atomic import safe_replace
from agent_state.events import build_event, default_state_dir
from agent_state.identity import resolve_identity
from agent_state.spool import Spool

_SPOOL_DIRNAME = "spool"
_MACHINE_FILENAME = "machine.json"


# ---------------------------------------------------------------------------
# Resolution helpers (only ever run when the switch is on, from inside the
# emit_safely barrier -- so they are free to do real I/O and even raise).
# ---------------------------------------------------------------------------
def _project_root() -> Path:
    """Repo root (nearest ancestor containing ``.git``) walking up from cwd.

    Mirrors ``workboard.find_project_root`` but does NOT import workboard --
    this package must never depend on the CLI script it is wired into. Falls
    back to cwd when no ``.git`` is found, exactly as workboard does.
    """
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    return cwd


def _machine_id() -> str:
    """Return this machine's stable id, minting + persisting one on first use.

    Stored as ``machine.json`` under ``default_state_dir()`` so it (a) survives
    across runs and (b) is distinct per ``AGENT_STATE_DIR`` -- the mechanism
    Task 11 uses to simulate two machines on one box. Persistence is
    best-effort: if the file can't be written, an ephemeral id is used for
    this run rather than raising (the surrounding barrier would swallow a
    raise anyway, but an ephemeral id keeps the event well-formed).
    """
    state_dir = default_state_dir()
    path = state_dir / _MACHINE_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mid = data.get("machine_id")
        if isinstance(mid, str) and mid:
            return mid
    except (OSError, ValueError):
        pass  # absent, unreadable, or malformed -- fall through and mint one

    mid = str(uuid.uuid4())
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"machine_id": mid}), encoding="utf-8")
        safe_replace(tmp, path)
    except OSError:
        pass  # couldn't persist -- use the ephemeral id for this run only
    return mid


def _spool() -> Spool:
    """A ``Spool`` rooted at ``default_state_dir()/spool``.

    Constructed fresh per call (not cached) so a mid-process change to
    ``AGENT_STATE_DIR`` -- which the tests rely on -- is always honoured.
    """
    return Spool(default_state_dir() / _SPOOL_DIRNAME)


def _append(kind, payload) -> None:
    """Build one event and append it to the durable local spool.

    Called ONLY when the kill switch is on and ONLY from inside
    ``emit_safely``'s ``BaseException`` barrier, so it may freely do I/O and
    raise -- the barrier absorbs anything it throws. Tests replace this whole
    function to inject failures; keep the real emission logic here so those
    injections exercise the barrier rather than the internals.
    """
    identity = resolve_identity(_project_root())
    event = build_event(kind, identity, payload, _machine_id())
    _spool().append(event)


def _log_failure(kind, exc) -> None:
    """Report a swallowed emission failure to stderr -- but NEVER raise.

    Mirrors ``spool._safe_warn``: a broken/redirected stderr (dead pipe, no
    console, ``| head`` closing early) must not turn the act of logging a
    failure into a NEW exception that escapes the barrier. Catches
    ``BaseException`` for exactly that reason.
    """
    try:
        print(
            f"WARNING: agent_state.emit swallowed a {kind!r} emission "
            f"failure -- command already succeeded: {exc!r}",
            file=sys.stderr,
        )
    except BaseException:
        pass


def emit_safely(kind, payload):
    """Emit one event, or nothing. NEVER raises; ALWAYS returns ``None``.

    The single barrier every workboard command relies on. When the kill
    switch is off it is a no-op (nothing resolved, nothing written, no
    output) -- the byte-identical guarantee. When on, it delegates to
    ``_append`` and swallows any ``BaseException`` that results, logging it
    locally so the failure is visible without ever propagating to the caller.
    """
    try:
        if not emit_enabled():
            return None
        _append(kind, payload)
    except BaseException as exc:
        _log_failure(kind, exc)
    return None
