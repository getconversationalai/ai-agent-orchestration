"""Pytest configuration for the agent_state test suite.

The package under test is NOT part of this repo. It lives at the LIVE path
~/.ai-instructions/tools/agent_state/ (real global tooling that ships via
scripts/capture.ps1, never edited here in the repo copy). This conftest
puts that package's parent directory on sys.path ahead of collection so
tests in this directory can simply `import agent_state`, mirroring how the
tools themselves resolve sibling imports (see e.g. orchestration_setup.py's
sys.path.insert(0, str(Path(__file__).resolve().parent)) pattern).
"""
from pathlib import Path
import sys

import pytest

TOOLS_DIR = Path.home() / ".ai-instructions" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


@pytest.fixture(autouse=True)
def _isolated_agent_state_dir(tmp_path, monkeypatch):
    """Redirect every test's default agent_state dir into tmp_path.

    build_event() calls next_gap_seq(default_state_dir()), and
    default_state_dir() falls back to the real
    ~/.agent-orchestration/agent_state/ when AGENT_STATE_DIR is unset. No
    test in this directory may write there -- point it at a fresh
    throwaway directory for every test instead.

    Hoisted here from test_events.py's Task-2-era identical fixture
    because test_spool.py (Task 3) also indirectly triggers
    default_state_dir() -- its real-event regression test calls
    build_event(). test_events.py keeps its own copy of this fixture
    (same name, same body); a module-level fixture shadows a conftest
    fixture of the same name for tests in that module, so the two never
    both run -- this is a harmless, deliberate duplicate, not a conflict.
    """
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "agent_state_default"))
