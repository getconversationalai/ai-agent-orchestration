import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Path-pin the worktree copy (agent_state conftest inserts live tools on sys.path).
_P = Path(__file__).resolve().parent.parent / "ai-instructions" / "tools" / "parallel_speedup_report.py"
_spec = importlib.util.spec_from_file_location("speedup_under_test", _P)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

_BASE = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)


def _iv(*pairs):
    return [(_BASE + timedelta(minutes=a), _BASE + timedelta(minutes=b)) for a, b in pairs]


def test_sequential_session_is_1x_peak1():
    m = sr.session_metrics(_iv((0, 5), (5, 10), (10, 15)))
    assert m["dispatches"] == 3 and m["peak"] == 1
    assert abs(m["parallelism"] - 1.0) < 0.01


def test_overlapping_session_is_parallel():
    m = sr.session_metrics(_iv((0, 10), (2, 12)))
    assert m["peak"] == 2 and m["parallelism"] > 1.4


def test_empty_session_is_zero():
    assert sr.session_metrics([]) == {"dispatches": 0, "overhead_s": 0.0, "parallelism": 1.0, "peak": 0}


def test_aggregate_counts_and_never_overlapped():
    seq = sr.session_metrics(_iv((0, 5), (5, 10)))   # peak 1
    par = sr.session_metrics(_iv((0, 10), (2, 12)))  # peak 2
    a = sr.aggregate([seq, par])
    assert a["sessions_with_subagents"] == 2
    assert a["total_dispatches"] == 4
    assert abs(a["pct_never_overlapped"] - 50.0) < 0.01


def test_aggregate_empty():
    assert sr.aggregate([]) == {"sessions_with_subagents": 0}


def test_report_handles_no_data():
    txt = sr.format_report({"sessions_with_subagents": 0}, _BASE)
    assert "not enough data" in txt.lower()
