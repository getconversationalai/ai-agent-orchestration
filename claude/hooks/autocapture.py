#!/usr/bin/env python3
"""autocapture.py — best-effort workboard auto-capture.

Wired to five Claude hook events; dispatches on argv[1]
(register | todo | subagent-register | subagent-complete | commit).

Contract: it NEVER blocks or fails an agent's tool call. Every path swallows
its own errors and the process always exits 0. Board writes go through
workboard.py, whose auto-capture verbs drop on lock contention. The git-derived
reconciler backstops anything dropped here.
"""
import sys
import json
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ident import agent_key

WB = Path.home() / ".ai-instructions" / "tools" / "workboard.py"


def read_event():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _git(cwd, *args):
    try:
        return subprocess.run(["git", "-C", str(cwd), *args],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _in_worktree(path):
    return "/.worktrees/" in str(path).replace("\\", "/").lower()


def _resolve(path_in_worktree):
    """(worktree_root, branch) for a path inside a /.worktrees/ tree, else (None, None)."""
    if not _in_worktree(path_in_worktree):
        return None, None
    start = Path(path_in_worktree)
    start = start if start.is_dir() else start.parent
    root = _git(start, "rev-parse", "--show-toplevel")
    if not root:
        return None, None
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        return None, None
    return root, branch


def _resolve_from_transcript(transcript_path):
    """Scan a finished helper's own transcript for a worktree it touched, and
    return (worktree_root, branch) or (None, None). Best-effort: any parse
    failure or a read-only helper (no worktree edit/command) yields (None, None)."""
    try:
        p = Path(transcript_path)
        if not p.exists():
            return None, None
        cand = None
        for line in p.open(encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_use":
                    continue
                inp = c.get("input") or {}
                fp = inp.get("file_path") or ""
                if fp and _in_worktree(fp):
                    cand = fp
                cmd = inp.get("command") or ""
                m = re.search(r'git\s+-C\s+["\']?([^"\'\s]+)', cmd)
                if m and _in_worktree(m.group(1)):
                    cand = m.group(1)
        return _resolve(cand) if cand else (None, None)
    except Exception:
        return None, None


def wb_call(args_list, cwd):
    # Run workboard.py IN the target worktree so it resolves that repo's board
    # (find_project_root walks up from cwd), not the hook process's cwd.
    try:
        subprocess.run([sys.executable, str(WB), *args_list],
                       cwd=str(cwd), capture_output=True, text=True, timeout=3)
    except Exception:
        pass  # best-effort: a board write must never surface to the agent


def build_register_cmd(root, branch, key):
    feature = branch.split("/", 1)[-1].replace("-", " ")
    return ["ensure-registered", "--branch", branch, "--worktree", root,
            "--agent-session", key, "--feature", feature]


def handle_register(data):
    key = agent_key(data)
    if not key:
        return None
    fp = data.get("tool_input", {}).get("file_path", "")
    if not fp or not _in_worktree(fp):
        return None
    root, branch = _resolve(fp)
    if not root:
        return None
    return build_register_cmd(root, branch, key), root


def build_todo_summary(todos):
    done = sum(1 for t in todos if t.get("status") == "completed")
    cur = next((t.get("content", "") for t in todos if t.get("status") == "in_progress"), "")
    # ASCII separator only: this string is passed through subprocess argv, and a
    # non-ASCII dash gets mangled by the Windows console code page.
    return f"{done}/{len(todos)} done" + (f" - {cur[:60]}" if cur else "")


def handle_todo(data):
    key = agent_key(data)
    todos = data.get("tool_input", {}).get("todos", [])
    if not key or not todos:
        return None
    root, branch = _resolve(data.get("cwd", ""))
    if not root:
        return None
    return (["update-progress", "--branch", branch, "--agent-session", key,
             "--path", build_todo_summary(todos)], root)


_COMMIT_RE = re.compile(r"\bgit\s+(?:-[cC]\s+\S+\s+|--\S+\s+)*commit(?![\w-])")


def handle_commit(data):
    key = agent_key(data)
    cmd = data.get("tool_input", {}).get("command", "")
    if not key or not _COMMIT_RE.search(cmd):
        return None
    root, branch = _resolve(data.get("cwd", ""))
    if not root:
        return None
    return ["touch", "--branch", branch, "--agent-session", key], root


def handle_debug(data):
    # TEMPORARY: log the raw sub-agent hook event so the exact Task/SubagentStop
    # payload shape can be verified before the nesting/auto-complete is built.
    # Returns None (never writes the board).
    try:
        log = Path.home() / ".stale-work" / "subagent-payloads.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "keys": sorted(data.keys()),
            "session_id": data.get("session_id"),
            "agent_id": data.get("agent_id"),
            "cwd": data.get("cwd"),
            "hook_event_name": data.get("hook_event_name"),
            "tool_name": data.get("tool_name"),
            "tool_input_keys": sorted((data.get("tool_input") or {}).keys()),
        }
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    return None


def handle_subagent_complete(data):
    # On SubagentStop: resolve the finished helper's worktree/branch from its OWN
    # transcript, then register + complete its board entry nested under the parent
    # (parent key = helper key minus the .sub suffix; both matched by agent-session).
    # Best-effort, never blocks. complete-subagent does NOT clean the helper's
    # worktree (the parent still merges the track up) -- confirmed vs workboard.py.
    key = agent_key(data)                       # agent-<6>.sub<4>
    if not key or ".sub" not in key:
        return None                             # not a sub-agent event
    parent_key = key.split(".sub", 1)[0]        # agent-<6>  (nesting derives from the id)
    root, branch = _resolve_from_transcript(data.get("agent_transcript_path", ""))
    if not root:
        return None                             # read-only helper: nothing to track
    feature = branch.split("/", 1)[-1].replace("-", " ")
    cmds = [
        ["register-subagent", "--parent", parent_key, "--feature", feature,
         "--branch", branch, "--worktree", root, "--agent-session", key],
        ["complete-subagent", "--parent", parent_key, "--sub", key],
    ]
    return cmds, root


DISPATCH = {"register": handle_register, "todo": handle_todo,
            "commit": handle_commit, "debug-subagent": handle_debug,
            "subagent-complete": handle_subagent_complete}


def main(argv):
    handler = DISPATCH.get(argv[1] if len(argv) > 1 else "")
    if handler:
        try:
            result = handler(read_event())
            if result:
                cmds, cwd = result
                # A handler returns either one command (list of str) or several
                # (list of lists). Normalize to a list of commands and run each.
                for cmd in (cmds if cmds and isinstance(cmds[0], list) else [cmds]):
                    wb_call(cmd, cwd)
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv)
