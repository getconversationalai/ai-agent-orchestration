import subprocess
import sys
import json
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "claude" / "hooks" / "pretooluse_task_guard.py"


def _run(stdin_text):
    return subprocess.run([sys.executable, str(GUARD)], input=stdin_text,
                          capture_output=True, text=True)


def test_warns_on_task_dispatch_but_never_blocks():
    r = _run(json.dumps({"tool_name": "Task", "tool_input": {"description": "x"}}))
    assert r.returncode == 0                            # WARN-only: never blocks
    assert "arallelization" in (r.stderr + r.stdout)    # reminder surfaced


def test_warns_on_agent_tool_name():
    r = _run(json.dumps({"tool_name": "Agent", "tool_input": {}}))
    assert r.returncode == 0
    assert "arallelization" in (r.stderr + r.stdout)


def test_silent_on_non_dispatch():
    r = _run(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x"}}))
    assert r.returncode == 0
    assert "arallelization" not in (r.stderr + r.stdout)


def test_never_blocks_on_garbage():
    r = _run("not json")
    assert r.returncode == 0
