import json, subprocess, sys
from pathlib import Path
import pytest

WB = Path(__file__).resolve().parent.parent / "ai-instructions" / "tools" / "workboard.py"


@pytest.fixture
def repo(tmp_path):
    # workboard.py resolves its board to the nearest ancestor containing .git.
    # Make tmp_path a real git repo so it writes HERE, never escaping up to the
    # home repo. (Regression: without this, boards leaked into ~/WORKBOARD.json.)
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    return tmp_path


def _run(repo, *args):
    return subprocess.run([sys.executable, str(WB), *args], cwd=str(repo),
                          capture_output=True, text=True)


def _board(repo):
    p = repo / "WORKBOARD.json"
    return json.loads(p.read_text()) if p.exists() else {"active": [], "completed": []}


def test_nest_and_complete_subagent_by_identity_key(repo):
    # Parent auto-registers (top-level) keyed on its agent-session.
    _run(repo, "ensure-registered", "--branch", "feat/voice", "--worktree", str(repo),
         "--agent-session", "agent-parent1", "--feature", "voice")
    # A helper (parallel track) nests under the parent, addressed by the PARENT'S
    # agent-session key (all we can derive at SubagentStop).
    r = _run(repo, "register-subagent", "--parent", "agent-parent1",
             "--feature", "track a", "--branch", "feat/voice/track-a",
             "--worktree", str(repo), "--agent-session", "agent-parent1.sub9f2")
    assert r.returncode == 0, r.stderr
    b = _board(repo)
    parent = next(e for e in b["active"] if e.get("agent_session") == "agent-parent1")
    assert any(s.get("agent_session") == "agent-parent1.sub9f2" for s in parent.get("subagents", []))
    # Complete the helper, addressed by ITS OWN agent-session key.
    r2 = _run(repo, "complete-subagent", "--parent", "agent-parent1", "--sub", "agent-parent1.sub9f2")
    assert r2.returncode == 0, r2.stderr
    parent2 = next(e for e in _board(repo)["active"] if e.get("agent_session") == "agent-parent1")
    assert not parent2.get("subagents"), "helper should be removed from active subagents on complete"


def test_ensure_registered_creates_then_is_idempotent(repo):
    _run(repo, "ensure-registered", "--branch", "feat/x",
         "--worktree", str(repo), "--agent-session", "agent-abc123", "--feature", "x")
    b1 = _board(repo)
    assert len([e for e in b1["active"] if e["branch"] == "feat/x"]) == 1
    _run(repo, "ensure-registered", "--branch", "feat/x",
         "--worktree", str(repo), "--agent-session", "agent-abc123", "--feature", "x")
    b2 = _board(repo)
    assert len([e for e in b2["active"] if e["branch"] == "feat/x"]) == 1


def test_ensure_registered_records_agent_session_and_last_active(repo):
    _run(repo, "ensure-registered", "--branch", "feat/y",
         "--worktree", str(repo), "--agent-session", "agent-def456", "--feature", "y")
    e = [e for e in _board(repo)["active"] if e["branch"] == "feat/y"][0]
    assert e["agent_session"] == "agent-def456"
    assert e.get("last_active")


def test_ensure_registered_distinct_agents_same_branch_are_separate(repo):
    _run(repo, "ensure-registered", "--branch", "feat/shared",
         "--worktree", str(repo), "--agent-session", "agent-one", "--feature", "shared")
    _run(repo, "ensure-registered", "--branch", "feat/shared",
         "--worktree", str(repo), "--agent-session", "agent-two", "--feature", "shared")
    entries = [e for e in _board(repo)["active"] if e["branch"] == "feat/shared"]
    assert len(entries) == 2  # keyed on (branch, agent_session)


def test_touch_bumps_last_active_without_creating(repo):
    _run(repo, "touch", "--branch", "feat/absent", "--agent-session", "agent-x")
    assert [e for e in _board(repo)["active"] if e["branch"] == "feat/absent"] == []


def test_touch_updates_existing_last_active(repo):
    _run(repo, "ensure-registered", "--branch", "feat/z",
         "--worktree", str(repo), "--agent-session", "agent-z", "--feature", "z")
    before = [e for e in _board(repo)["active"] if e["branch"] == "feat/z"][0]["last_active"]
    r = _run(repo, "touch", "--branch", "feat/z", "--agent-session", "agent-z")
    assert r.returncode == 0
    entry = [e for e in _board(repo)["active"] if e["branch"] == "feat/z"][0]
    assert entry.get("last_active") >= before


def test_update_progress_sets_current_path_on_matching_entry(repo):
    _run(repo, "ensure-registered", "--branch", "feat/p",
         "--worktree", str(repo), "--agent-session", "agent-p", "--feature", "p")
    r = _run(repo, "update-progress", "--branch", "feat/p", "--agent-session", "agent-p",
             "--path", "2/5 done - building")
    assert r.returncode == 0
    e = [e for e in _board(repo)["active"] if e["branch"] == "feat/p"][0]
    assert e["current_path"] == "2/5 done - building"
    assert e.get("last_active")


def test_update_progress_noop_when_absent(repo):
    r = _run(repo, "update-progress", "--branch", "feat/none", "--agent-session", "a", "--path", "x")
    assert r.returncode == 0
    assert [e for e in _board(repo)["active"] if e["branch"] == "feat/none"] == []
