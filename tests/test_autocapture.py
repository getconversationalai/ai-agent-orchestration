import subprocess
import pytest
import autocapture as ac


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True)


@pytest.fixture
def worktree(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    (main / "a.txt").write_text("1")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "c0")
    wt = tmp_path / ".worktrees" / "proj" / "feat-x"
    _git(main, "worktree", "add", str(wt), "-b", "feat/x")
    (wt / "server").mkdir(parents=True, exist_ok=True)
    f = wt / "server" / "x.ts"
    f.write_text("x")
    return wt, f


# --- pure guards (no git needed) ---
def test_register_skips_without_session_id():
    fp = "C:/dev/.worktrees/feature-a/feat-x/server/x.ts"
    assert ac.handle_register({"tool_input": {"file_path": fp}}) is None


def test_register_skips_non_worktree_edit():
    assert ac.handle_register(
        {"session_id": "s", "tool_input": {"file_path": "C:/dev/feature-a/server/x.ts"}}) is None


def test_build_register_cmd_shape():
    cmd = ac.build_register_cmd("C:/wt/feat-x", "feat/x", "agent-abc")
    assert cmd[0] == "ensure-registered"
    j = " ".join(cmd)
    assert "--branch" in cmd and "feat/x" in cmd
    assert "--worktree" in cmd and "C:/wt/feat-x" in cmd
    assert "--agent-session" in cmd and "agent-abc" in cmd
    assert "--feature" in cmd and "x" in j   # derived from branch tail


# --- happy path against a REAL worktree ---
def test_register_builds_command_for_real_worktree(worktree):
    wt, f = worktree
    res = ac.handle_register({"session_id": "sess", "tool_input": {"file_path": str(f)}})
    assert res is not None
    cmd, cwd = res
    assert cmd[0] == "ensure-registered"
    assert "feat/x" in cmd
    assert "agent-" in " ".join(cmd)
    assert "feat-x" in str(cwd).replace("\\", "/")   # workboard.py runs IN the worktree


def test_read_event_returns_empty_on_garbage(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert ac.read_event() == {}


def test_build_todo_summary_counts_and_current():
    todos = [{"content": "a", "status": "completed"},
             {"content": "b build the thing", "status": "in_progress"},
             {"content": "c", "status": "pending"}]
    s = ac.build_todo_summary(todos)
    assert "1/3 done" in s and "build the thing" in s


def test_handle_todo_skips_without_todos():
    assert ac.handle_todo({"session_id": "s", "cwd": "C:/dev/.worktrees/x/feat-y",
                           "tool_input": {"todos": []}}) is None


def test_handle_todo_builds_update_progress_for_real_worktree(worktree):
    wt, f = worktree
    data = {"session_id": "sess", "cwd": str(wt),
            "tool_input": {"todos": [
                {"content": "a", "status": "completed"},
                {"content": "b", "status": "in_progress"}]}}
    res = ac.handle_todo(data)
    assert res is not None
    cmd, cwd = res
    assert cmd[0] == "update-progress"
    assert "feat/x" in cmd and "1/2 done" in " ".join(cmd)


def test_handle_commit_detects_git_commit(worktree):
    wt, f = worktree
    data = {"session_id": "sess", "cwd": str(wt),
            "tool_input": {"command": "git commit -m 'x'"}}
    res = ac.handle_commit(data)
    assert res is not None
    cmd, cwd = res
    assert cmd[0] == "touch"
    assert "feat/x" in cmd


def test_handle_commit_ignores_non_commit_bash():
    data = {"session_id": "sess", "cwd": "C:/dev/.worktrees/x/feat-y",
            "tool_input": {"command": "git status"}}
    assert ac.handle_commit(data) is None


def test_handle_commit_ignores_without_key():
    data = {"cwd": "C:/dev/.worktrees/x/feat-y", "tool_input": {"command": "git commit -m x"}}
    assert ac.handle_commit(data) is None


# --- Phase 3a: transcript resolver ---
import json


def _write_sub_transcript(tmp_path, tool_uses):
    p = tmp_path / "sub.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for tu in tool_uses:
            f.write(json.dumps({"message": {"content": [
                {"type": "tool_use", "name": tu.get("name", "Edit"), "input": tu["input"]}]}}) + "\n")
    return p


def test_resolver_finds_worktree_from_edit(tmp_path, monkeypatch):
    wt = "C:/dev/.worktrees/proj/feat/x-track-a/server/api.ts"
    tp = _write_sub_transcript(tmp_path, [{"input": {"file_path": wt}}])
    monkeypatch.setattr(ac, "_resolve", lambda p: ("C:/dev/.worktrees/proj/feat/x-track-a", "feat/x-track-a"))
    root, branch = ac._resolve_from_transcript(str(tp))
    assert root and branch == "feat/x-track-a"


def test_resolver_finds_worktree_from_git_dash_c(tmp_path, monkeypatch):
    tp = _write_sub_transcript(tmp_path, [
        {"name": "Bash", "input": {"command": "git -C C:/dev/.worktrees/proj/feat/x-track-a commit -m y"}}])
    monkeypatch.setattr(ac, "_resolve", lambda p: ("C:/dev/.worktrees/proj/feat/x-track-a", "feat/x-track-a"))
    root, branch = ac._resolve_from_transcript(str(tp))
    assert branch == "feat/x-track-a"


def test_resolver_none_for_readonly_helper(tmp_path):
    tp = _write_sub_transcript(tmp_path, [{"name": "Read", "input": {"file_path": "C:/Users/example/x.py"}}])
    assert ac._resolve_from_transcript(str(tp)) == (None, None)


def test_resolver_none_on_missing_file():
    assert ac._resolve_from_transcript("C:/nope/none.jsonl") == (None, None)


# --- Phase 3a: SubagentStop completion handler (nested via identity keys) ---
def test_complete_handler_builds_nested_register_and_complete(monkeypatch):
    monkeypatch.setattr(ac, "_resolve_from_transcript",
                        lambda p: ("C:/dev/.worktrees/proj/feat/voice/track-a", "feat/voice/track-a"))
    data = {"session_id": "S", "agent_id": "AID", "agent_transcript_path": "t.jsonl"}
    res = ac.handle_subagent_complete(data)
    assert res is not None
    cmds, cwd = res
    joined = " ".join(sum(cmds, []))
    assert "register-subagent" in joined and "complete-subagent" in joined
    key = ac.agent_key(data)
    parent = key.split(".sub", 1)[0]
    assert ".sub" in key and key in joined          # helper key used
    assert parent in joined                          # nested under parent key
    assert "--auto-cleanup" not in joined            # never requests helper worktree cleanup


def test_complete_handler_noop_for_readonly(monkeypatch):
    monkeypatch.setattr(ac, "_resolve_from_transcript", lambda p: (None, None))
    assert ac.handle_subagent_complete(
        {"session_id": "S", "agent_id": "A", "agent_transcript_path": "t"}) is None


def test_complete_handler_noop_without_agent_id():
    assert ac.handle_subagent_complete({"session_id": "S"}) is None
