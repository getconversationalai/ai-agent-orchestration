import importlib.util
from pathlib import Path

# Load the WORKTREE copy explicitly. tests/agent_state/conftest.py inserts the
# LIVE ~/.ai-instructions/tools on sys.path[0] during collection, which would
# otherwise shadow this module with the deployed copy — so `import headroom`
# could test live code instead of the code under test. Path-loading pins it.
_HEADROOM = Path(__file__).resolve().parent.parent / "ai-instructions" / "tools" / "headroom.py"
_spec = importlib.util.spec_from_file_location("headroom_under_test", _HEADROOM)
headroom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(headroom)


def test_idle_machine_uses_static_max(monkeypatch):
    monkeypatch.setattr(headroom, "_cpu_cores", lambda: 16)
    monkeypatch.setattr(headroom, "_busy_units", lambda: 0)
    assert headroom.recommended_cap(static_max=4) == 4  # idle -> full static allowance


def test_loaded_machine_drops_to_floor(monkeypatch):
    monkeypatch.setattr(headroom, "_cpu_cores", lambda: 8)
    monkeypatch.setattr(headroom, "_busy_units", lambda: 10)
    assert headroom.recommended_cap(static_max=6) == 1  # never below 1


def test_more_load_never_raises_cap(monkeypatch):
    monkeypatch.setattr(headroom, "_cpu_cores", lambda: 16)
    monkeypatch.setattr(headroom, "_busy_units", lambda: 4)
    loaded = headroom.recommended_cap(static_max=8)
    monkeypatch.setattr(headroom, "_busy_units", lambda: 0)
    idle = headroom.recommended_cap(static_max=8)
    assert 1 <= loaded <= idle <= 8


def test_never_exceeds_static_max(monkeypatch):
    monkeypatch.setattr(headroom, "_cpu_cores", lambda: 64)
    monkeypatch.setattr(headroom, "_busy_units", lambda: 0)
    assert headroom.recommended_cap(static_max=3) == 3


def test_robust_falls_back_to_static_max_on_error(monkeypatch):
    def boom():
        raise RuntimeError("measurement failed")
    monkeypatch.setattr(headroom, "_cpu_cores", boom)
    assert headroom.recommended_cap(static_max=5) == 5


def test_cap_now_static_mode_skips_live_measure(monkeypatch):
    monkeypatch.setattr(headroom, "_load_config", lambda: (5, "static"))
    assert headroom.cap_now() == 5


def test_cap_now_live_mode_uses_headroom(monkeypatch):
    monkeypatch.setattr(headroom, "_load_config", lambda: (8, "live"))
    monkeypatch.setattr(headroom, "_cpu_cores", lambda: 8)
    monkeypatch.setattr(headroom, "_busy_units", lambda: 3)
    assert headroom.cap_now() == 3   # ceiling 6 - 3 busy = 3


def test_load_config_defaults_when_unreadable(monkeypatch):
    monkeypatch.setattr(headroom, "_CONFIG", "C:/nope/none.json")
    assert headroom._load_config() == (4, "live")  # live sizing is the default
