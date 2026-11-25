#!/usr/bin/env python3
"""Claude Code setup speed probe. READ-ONLY.

Collects facts about a Claude Code setup and times cheap, harmless things on
this machine (process start-up, interpreter start-up, `git status`). It never
writes to any config file, never sends anything over the network, and never
prints secrets (MCP env/args are not read into the output).

By default it does NOT execute the user's hooks. `--run-hooks` runs the
PreToolUse / PostToolUse / UserPromptSubmit hooks once each with a harmless
synthetic payload; `--include-stop` adds Stop / SubagentStop hooks. Only use
those flags after the user has agreed.

Usage:
  python probe.py [--project DIR] [--runs N] [--run-hooks [--include-stop]] [--out FILE]
Output: one JSON document (stdout, or --out FILE).
Requires Python 3.8+, standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IS_WIN = os.name == "nt"
HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")

# Events whose hooks run on (nearly) every tool call / prompt / turn.
HOT_EVENTS = {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"}
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"}
# Built-in tool names seen across Claude Code versions. Used only to flag
# matchers that name no known tool (a hint, not proof: names change).
KNOWN_TOOLS = {
    "Agent", "Task", "Bash", "BashOutput", "KillShell", "KillBash", "Edit", "MultiEdit",
    "Write", "Read", "Glob", "Grep", "LS", "NotebookEdit", "NotebookRead", "WebFetch",
    "WebSearch", "TodoWrite", "Skill", "SlashCommand", "ExitPlanMode", "EnterPlanMode",
    "AskUserQuestion", "TaskStop", "TaskOutput", "Monitor", "ToolSearch", "SendMessage",
    "Workflow", "LSP", "PowerShell", "EnterWorktree", "ExitWorktree", "ListAgents",
}
HEAVY_PATTERNS = [
    (r"\btsc\b|\btypecheck\b|type-check", "TypeScript typecheck"),
    (r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(test|build|lint)\b", "package test/build/lint script"),
    (r"\bpytest\b|\bmypy\b|\bpyright\b", "Python test/type checker"),
    (r"\b(jest|vitest|mocha)\b|\bplaywright\s+test\b", "JS test runner"),
    (r"\beslint\b|\bprettier\b|\bbiome\b", "linter/formatter over files"),
    (r"\bcargo\s+(build|test|check|clippy)\b", "Rust build/check"),
    (r"\bgo\s+(build|test|vet)\b", "Go build/test"),
    (r"\bgradle\b|\bmvn\b|\bdotnet\s+(build|test)\b", "JVM/.NET build"),
    (r"\bgit\s+fetch\b|\bgit\s+pull\b", "network git call"),
    (r"\bcurl\b|\bwget\b|requests\.|urllib\.request|fetch\(", "network call"),
]
PKG_RUNNERS = ("npx", "uvx", "bunx", "pnpx")
PKG_RUNNER_PHRASES = ("pnpm dlx", "yarn dlx", "uv run", "npm exec", "pipx run")


# ---------------------------------------------------------------- helpers
def read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def read_json(p: Path):
    t = read_text(p)
    if t is None:
        return None
    try:
        return json.loads(t)
    except Exception:
        # tolerate // comments sometimes left in settings
        try:
            return json.loads(re.sub(r"^\s*//.*$", "", t, flags=re.M))
        except Exception:
            return {"__parse_error__": True}


def which(name: str) -> str | None:
    return shutil.which(name)


def time_cmd(argv, runs: int, timeout: float = 20.0, **kw):
    """Run argv `runs` times; return timing stats in ms (wall clock)."""
    samples, ok = [], True
    try:  # warm-up run, discarded: the first start of any program pays one-off disk/cache cost
        subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=timeout, **kw)
    except Exception:
        return {"ok": False}
    for _ in range(runs):
        t0 = time.perf_counter()
        try:
            subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=timeout, **kw)
        except Exception:
            ok = False
            break
        samples.append((time.perf_counter() - t0) * 1000)
    if not samples:
        return {"ok": False}
    s = sorted(samples)
    p90 = s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]
    return {"ok": ok, "runs": len(s), "median_ms": round(statistics.median(s), 1),
            "p90_ms": round(p90, 1), "min_ms": round(s[0], 1), "max_ms": round(s[-1], 1)}


def project_root(start: Path) -> Path:
    try:
        r = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:
        pass
    return start


def est_tokens(chars: int) -> int:
    return round(chars / 4)  # rough rule of thumb; reported as an estimate


# ---------------------------------------------------------------- settings / hooks
def settings_layers(root: Path):
    managed = []
    if IS_WIN:
        managed.append(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ClaudeCode" / "managed-settings.json")
    elif sys.platform == "darwin":
        managed.append(Path("/Library/Application Support/ClaudeCode/managed-settings.json"))
    else:
        managed.append(Path("/etc/claude-code/managed-settings.json"))
    cands = [("user", CLAUDE_DIR / "settings.json"), ("user-local", CLAUDE_DIR / "settings.local.json"),
             ("project", root / ".claude" / "settings.json"),
             ("project-local", root / ".claude" / "settings.local.json")]
    cands += [("managed", m) for m in managed]
    out = []
    for name, p in cands:
        if p.exists():
            out.append((name, p, read_json(p)))
    return out


def iter_hook_groups(hooks_obj, source: str, plugin_root: str | None = None):
    if not isinstance(hooks_obj, dict):
        return
    for event, groups in hooks_obj.items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            if not isinstance(g, dict):
                continue
            matcher = g.get("matcher", "")
            for h in g.get("hooks", []) or []:
                if isinstance(h, dict) and h.get("type", "command") == "command" and h.get("command"):
                    # Newer "exec form": {"command": "node", "args": [...]} runs without a shell.
                    args = h.get("args") if isinstance(h.get("args"), list) else None
                    shown = h["command"] + ("".join(f' "{a}"' for a in args) if args else "")
                    yield {"event": event, "matcher": matcher or "", "command": shown,
                           "exec": h["command"] if args else None, "args": args,
                           "if": h.get("if"), "timeout": h.get("timeout"),
                           # async / asyncRewake hooks run in the background: they do not make Claude wait
                           "async": bool(h.get("async") or h.get("asyncRewake")),
                           "source": source, "plugin_root": plugin_root}


def enabled_plugins(layers):
    enabled = {}
    for _, _, data in layers:
        if isinstance(data, dict) and isinstance(data.get("enabledPlugins"), dict):
            enabled.update(data["enabledPlugins"])
    return [k for k, v in enabled.items() if v]


def plugin_install_paths():
    reg = read_json(CLAUDE_DIR / "plugins" / "installed_plugins.json") or {}
    out = {}
    plugins = reg.get("plugins", {}) if isinstance(reg, dict) else {}
    for key, entries in plugins.items():
        if isinstance(entries, list) and entries:
            ip = entries[-1].get("installPath")
            if ip:
                out[key] = Path(ip)
        elif isinstance(entries, dict) and entries.get("installPath"):
            out[key] = Path(entries["installPath"])
    return out


def collect_hooks(layers):
    hooks = []
    for name, p, data in layers:
        if isinstance(data, dict):
            hooks += list(iter_hook_groups(data.get("hooks"), f"settings:{name}"))
    paths = plugin_install_paths()
    plugins = enabled_plugins(layers)
    for key in plugins:
        root = paths.get(key)
        if not root or not root.exists():
            continue
        hf = read_json(root / "hooks" / "hooks.json")
        if isinstance(hf, dict):
            hooks += list(iter_hook_groups(hf.get("hooks"), f"plugin:{key}", str(root)))
        pj = read_json(root / ".claude-plugin" / "plugin.json")
        if isinstance(pj, dict) and isinstance(pj.get("hooks"), dict):
            hooks += list(iter_hook_groups(pj["hooks"], f"plugin:{key}", str(root)))
    return hooks, plugins


def launcher_of(cmd: str):
    c = cmd.strip()
    low = c.lower()
    for phrase in PKG_RUNNER_PHRASES:
        if low.startswith(phrase) or f" {phrase} " in f" {low} ":
            return phrase
    try:
        first = shlex.split(c, posix=not IS_WIN)[0]
    except Exception:
        first = c.split()[0] if c.split() else ""
    base = os.path.basename(first.strip('"\'')).lower()
    base = re.sub(r"\.(exe|cmd|bat)$", "", base)
    if re.match(r"python3(\.\d+)?$", base):
        return "python3"
    return base


def expand_path_token(tok: str, hook) -> Path | None:
    tok = tok.strip('"\'')
    if hook.get("plugin_root"):
        tok = tok.replace("${CLAUDE_PLUGIN_ROOT}", hook["plugin_root"]).replace("$CLAUDE_PLUGIN_ROOT", hook["plugin_root"])
    tok = tok.replace("${CLAUDE_PROJECT_DIR}", os.getcwd()).replace("$CLAUDE_PROJECT_DIR", os.getcwd())
    tok = os.path.expanduser(os.path.expandvars(tok))
    p = Path(tok)
    return p if p.suffix and p.exists() and p.is_file() else None


def script_files(hook):
    try:
        toks = shlex.split(hook["command"], posix=not IS_WIN)
    except Exception:
        toks = hook["command"].split()
    out = []
    for t in toks:
        p = expand_path_token(t, hook)
        if p and p.suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".ps1", ".cmd", ".bat", ""}:
            out.append(p)
    return out


def matcher_scope(event: str, matcher: str) -> str:
    if event not in TOOL_EVENTS:
        return "every-event"
    m = (matcher or "").strip()
    if m in ("", "*", ".*"):
        return "every-tool-call"
    return "matched-tools"


def dead_matcher_parts(event: str, matcher: str):
    if event not in TOOL_EVENTS or matcher_scope(event, matcher) != "matched-tools":
        return []
    dead = []
    for part in matcher.split("|"):
        part = part.strip().strip("^$()")
        if not part or re.search(r"[.*+?\[\]{}\\]", part) or part.startswith("mcp__"):
            continue
        if part not in KNOWN_TOOLS:
            dead.append(part)
    return dead


def heavy_hits(text: str):
    hits = []
    for pat, label in HEAVY_PATTERNS:
        if re.search(pat, text):
            hits.append(label)
    return hits


EXEC_RE = re.compile(r"subprocess|Popen|check_output|check_call|os\.system|os\.popen|\bspawn|execSync|"
                     r"execFile|child_process|\bexec\(|Start-Process|Invoke-Expression|\$\(|`[^`]+`")
SHELL_SUFFIXES = {".sh", ".bash", ".ps1", ".cmd", ".bat", ""}


def exec_context(p: Path, text: str) -> str:
    """Only the parts of a script that plausibly START programs.

    A word like 'playwright' in a comment or a message string is not work; the
    same word next to subprocess.run(...) probably is. Shell scripts: every
    non-comment line. Other languages: +/-3 lines around a process-spawning call.
    """
    lines = text.splitlines()
    code = [("" if re.match(r"\s*(#|//|::|rem\s)", ln, re.I) else ln) for ln in lines]
    if p.suffix.lower() in SHELL_SUFFIXES:
        return "\n".join(code)
    keep = set()
    for i, ln in enumerate(code):
        if ln and EXEC_RE.search(ln) and not re.match(r"\s*(import|from)\s|\s*(const|let|var)\s.*require\(", ln):
            keep.update(range(max(0, i - 3), min(len(code), i + 4)))
    return "\n".join(code[i] for i in sorted(keep))


# ---------------------------------------------------------------- timing
LAUNCHER_NOOPS = {
    "python": [["python", "-c", "pass"], ["python", "-S", "-c", "pass"]],
    "python3": [["python3", "-c", "pass"], ["python3", "-S", "-c", "pass"]],
    "py": [["py", "-c", "pass"], ["py", "-S", "-c", "pass"]],
    "node": [["node", "-e", "0"]],
    "bun": [["bun", "-e", "0"]],
    "deno": [["deno", "eval", "0"]],
    "bash": [["bash", "-c", "true"]],
    "sh": [["sh", "-c", "true"]],
    "zsh": [["zsh", "-c", "true"]],
    "powershell": [["powershell", "-NoProfile", "-Command", "exit"], ["powershell", "-Command", "exit"]],
    "pwsh": [["pwsh", "-NoProfile", "-Command", "exit"], ["pwsh", "-Command", "exit"]],
    "cmd": [["cmd", "/c", "exit 0"]],
}


def time_launchers(launchers, runs):
    out = {}
    want = set(launchers) | {"python", "node"}  # always give a comparison point
    if IS_WIN and ("python" in want or "py" in launchers):
        want.add("py")
    for name in sorted(want):
        variants = LAUNCHER_NOOPS.get(name)
        if not variants or not which(variants[0][0]):
            continue
        res = {"path": which(variants[0][0])}
        for argv in variants:
            res[" ".join(argv)] = time_cmd([which(argv[0])] + argv[1:], runs)
        out[name] = res
    return out


def spawn_floor(runs):
    if IS_WIN:
        argv = [which("cmd") or "cmd", "/c", "exit 0"]
    else:
        argv = [which("true") or "/bin/true"]
    return {"command": " ".join(argv[-3:]), **time_cmd(argv, max(runs, 15))}


def hook_shell_wrapper(runs):
    """Claude Code runs hook commands through a shell (Git Bash on Windows)."""
    sh = which("bash") or (None if IS_WIN else which("sh"))
    if not sh:
        return None
    return {"shell": sh, **time_cmd([sh, "-c", "true"], runs)}


def git_status_time(root: Path, runs):
    if not (root / ".git").exists() or not which("git"):
        return None
    return time_cmd([which("git"), "-C", str(root), "status", "--porcelain"], min(runs, 5), timeout=60)


def synthetic_payload(hook, cwd: str):
    ev = hook["event"]
    tool = "Read"
    if matcher_scope(ev, hook["matcher"]) == "matched-tools":
        for part in hook["matcher"].split("|"):
            part = part.strip()
            if part in KNOWN_TOOLS:
                tool = part
                break
    probe_file = str(Path(tempfile.gettempdir()) / "claude-speed-audit-probe-does-not-exist.txt")
    tool_input = {"command": "echo speed-audit-probe", "description": "probe"} if tool in ("Bash", "PowerShell") \
        else {"file_path": probe_file, "pattern": "speed-audit-probe", "prompt": "probe"}
    base = {"session_id": "speed-audit-probe", "transcript_path": probe_file, "cwd": cwd,
            "hook_event_name": ev, "permission_mode": "default"}
    if ev in ("PreToolUse", "PostToolUse"):
        base.update({"tool_name": tool, "tool_input": tool_input})
        if ev == "PostToolUse":
            base["tool_response"] = {"success": True, "output": "speed-audit-probe"}
    elif ev == "UserPromptSubmit":
        base["prompt"] = "speed audit probe (synthetic, ignore)"
    elif ev in ("Stop", "SubagentStop"):
        base["stop_hook_active"] = True  # tells well-behaved Stop hooks not to loop
    return base


def run_hook_once(hook, cwd: str, timeout: float = 30.0):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = cwd
    if hook.get("plugin_root"):
        env["CLAUDE_PLUGIN_ROOT"] = hook["plugin_root"]
    sh = which("bash") or (None if IS_WIN else which("sh"))
    if hook.get("args") is not None:  # exec form: no shell, expand variables in each arg
        def ex(s):
            s = s.replace("${CLAUDE_PLUGIN_ROOT}", hook.get("plugin_root") or "").replace("${CLAUDE_PROJECT_DIR}", cwd)
            return os.path.expandvars(os.path.expanduser(s))
        argv, sh = [which(hook["exec"]) or hook["exec"]] + [ex(a) for a in hook["args"]], "exec"
    else:
        argv = [sh, "-c", hook["command"]] if sh else hook["command"]
    payload = json.dumps(synthetic_payload(hook, cwd))
    t0 = time.perf_counter()
    try:
        r = subprocess.run(argv, input=payload, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=cwd, env=env, shell=sh is None)
        return {"ms": round((time.perf_counter() - t0) * 1000, 1), "exit": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ms": timeout * 1000, "exit": None, "timed_out": True}
    except Exception as e:  # noqa: BLE001
        return {"ms": None, "error": type(e).__name__}


# ---------------------------------------------------------------- instructions
def sanitized_project_key(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


IMPORT_RE = re.compile(r"(?<![\w`])@((?:~|\.{1,2}|/|[A-Za-z]:)?[\w./\\~-]+\.\w+)")


def resolve_imports(path: Path, text: str, seen: set, depth: int, out: list):
    if depth > 5:
        return
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in IMPORT_RE.finditer(line):
            ref = os.path.expanduser(m.group(1))
            p = Path(ref) if os.path.isabs(ref) else (path.parent / ref)
            try:
                p = p.resolve()
            except Exception:
                continue
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            t = read_text(p) or ""
            out.append({"path": str(p), "kind": "@import", "chars": len(t), "lines": t.count("\n") + 1,
                        "est_tokens": est_tokens(len(t))})
            resolve_imports(p, t, seen, depth + 1, out)


def instruction_files(cwd: Path, root: Path):
    found, seen = [], set()

    def add(p: Path, kind: str):
        try:
            rp = p.resolve()
        except Exception:
            return
        if rp in seen or not rp.is_file():
            return
        seen.add(rp)
        t = read_text(rp) or ""
        entry = {"path": str(rp), "kind": kind, "chars": len(t), "lines": t.count("\n") + 1,
                 "est_tokens": est_tokens(len(t))}
        if kind == "auto-memory index":
            entry["note"] = "Claude Code loads roughly the first 200 lines of this file"
        found.append(entry)
        resolve_imports(rp, t, seen, 0, found)

    add(CLAUDE_DIR / "CLAUDE.md", "user CLAUDE.md")
    d = cwd.resolve()
    chain = [d] + list(d.parents)
    for folder in chain:
        for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"):
            add(folder / name, "project/ancestor CLAUDE.md")
        if folder == root.parent:
            break
    for key in {sanitized_project_key(cwd.resolve()), sanitized_project_key(root)}:
        add(CLAUDE_DIR / "projects" / key / "memory" / "MEMORY.md", "auto-memory index")
    rules_dir = CLAUDE_DIR / "rules"
    if rules_dir.is_dir():
        for p in sorted(rules_dir.rglob("*.md")):
            add(p, "user rules/")
    return found


# ---------------------------------------------------------------- MCP
def mcp_servers(root: Path):
    servers = []

    def add_from(obj, scope):
        if not isinstance(obj, dict):
            return
        for name, cfg in obj.items():
            if not isinstance(cfg, dict):
                continue
            kind = cfg.get("type") or ("stdio" if cfg.get("command") else "remote")
            launcher = os.path.basename(str(cfg.get("command", ""))).lower() if cfg.get("command") else None
            # deliberately NOT recording args/env/url/headers: they can hold secrets
            servers.append({"name": name, "scope": scope, "type": kind, "launcher": launcher,
                            "disabled": bool(cfg.get("disabled"))})

    cj = read_json(HOME / ".claude.json")
    if isinstance(cj, dict):
        add_from(cj.get("mcpServers"), "user")
        projects = cj.get("projects") or {}
        for key in (str(root), str(root).replace("\\", "/")):
            if isinstance(projects.get(key), dict):
                add_from(projects[key].get("mcpServers"), "local")
                break
    add_from((read_json(root / ".mcp.json") or {}).get("mcpServers") if (root / ".mcp.json").exists() else None, "project")
    return servers


# ---------------------------------------------------------------- environment
def process_counts():
    names = {"node": 0, "python": 0, "bash": 0, "claude": 0, "git": 0}
    try:
        if IS_WIN:
            r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20)
            procs = [line.split('","')[0].strip('"').lower() for line in r.stdout.splitlines() if line]
        else:
            r = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=20)
            procs = [os.path.basename(x.strip()).lower() for x in r.stdout.splitlines()]
    except Exception:
        return None
    for p in procs:
        b = re.sub(r"\.exe$", "", p)
        for n in names:
            if b == n or b.startswith(n + "3") or (n == "python" and b in ("py", "pythonw")):
                names[n] += 1
    return names


def worktree_count(root: Path):
    try:
        r = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, timeout=20)
        return sum(1 for line in r.stdout.splitlines() if line.startswith("worktree "))
    except Exception:
        return None


# ---------------------------------------------------------------- findings
def F(findings, fid, severity, title, evidence, fix, basis, saving=None):
    findings.append({"id": fid, "severity": severity, "title": title, "evidence": evidence,
                     "fix": fix, "basis": basis, "estimated_saving": saving})


def build_findings(r):
    out = []
    L = r["timing"]["launchers"]
    hooks = r["hooks"]["items"]
    hot = [h for h in hooks if h["event"] in HOT_EVENTS]

    def med(name, key):
        return ((L.get(name) or {}).get(key) or {}).get("median_ms")

    # 1. py launcher vs python
    py_hooks = [h for h in hot if h["launcher"] == "py"]
    if py_hooks and med("py", "py -c pass") and med("python", "python -c pass"):
        d = med("py", "py -c pass") - med("python", "python -c pass")
        if d > 15:
            F(out, "py-launcher", "high" if d > 40 else "medium",
              f"{len(py_hooks)} frequent hook(s) start through the Windows `py` launcher",
              f"`py` start-up {med('py','py -c pass')}ms vs `python` {med('python','python -c pass')}ms (measured, median)",
              "Call `python` (or the full path to python.exe) directly in the hook command.",
              "measured", f"~{round(d)}ms per hook run")
    # 2. python -S
    for name in ("python", "python3", "py"):
        no_s = [h for h in hot if h["launcher"] == name and not re.search(r"\s-\w*S", h["command"])]
        a, b = med(name, f"{name} -c pass"), med(name, f"{name} -S -c pass")
        if no_s and a and b and a - b > 15:
            F(out, f"{name}-no-S", "medium",
              f"{len(no_s)} frequent Python hook(s) load site-packages they may not need",
              f"`{name} -c pass` {a}ms vs `{name} -S -c pass` {b}ms (measured, median)",
              "If a hook script only uses the standard library, add `-S` to its command. "
              "Check first: `-S` skips installed packages, so a hook that imports one will break.",
              "measured", f"~{round(a - b)}ms per hook run")
    # 3. package runners
    for h in hooks:
        if h["launcher"] in PKG_RUNNERS or h["launcher"] in PKG_RUNNER_PHRASES:
            F(out, "pkg-runner-hook", "high" if h["event"] in HOT_EVENTS else "low",
              f"{h['event']} hook runs through `{h['launcher']}`",
              f"`{h['command'][:120]}` (from {h['source']})",
              "Install the tool once and call it directly (or call a local script). Package runners "
              "resolve or download the package on every run.",
              "estimated", "often 300ms-2s+ per run")
    # 4. powershell profile
    for name in ("powershell", "pwsh"):
        ph = [h for h in hooks if h["launcher"] == name and "-noprofile" not in h["command"].lower()]
        a, b = med(name, f"{name} -Command exit"), med(name, f"{name} -NoProfile -Command exit")
        if ph and a and b and a - b > 50 and a > b * 1.2:  # PowerShell start-up is noisy; demand a clear gap
            F(out, f"{name}-profile", "medium", f"{len(ph)} {name} hook(s) load the user's profile",
              f"with profile {a}ms vs -NoProfile {b}ms (measured)",
              "Add `-NoProfile` to the hook command.", "measured", f"~{round(a - b)}ms per hook run")
    # 5. heavy work in hot, blocking hooks - one finding per hook command
    grouped = {}
    for h in hot:
        if h["heavy"] and not h["async"]:
            grouped.setdefault(h["command"].strip(), []).append(h)
    for cmd, hs in grouped.items():
        broad = any(h["scope"] in ("every-tool-call", "every-event") for h in hs)
        confirmed = any(h["heavy_in_command"] for h in hs)
        sev = "high" if (broad and confirmed) else "medium" if (broad or confirmed) else "low"
        labels = sorted({x for h in hs for x in h["heavy"]})
        where = "in the hook command itself" if confirmed else \
            "near a process-launch call in its script (VERIFY by reading the script)"
        measured = [h["measured_run"]["ms"] for h in hs if (h.get("measured_run") or {}).get("ms") is not None]
        F(out, "heavy-hot-hook", sev,
          f"{'/'.join(sorted({h['event'] for h in hs}))} hook may run: {', '.join(labels)}",
          f"`{cmd[:120]}` (from {hs[0]['source']}; scope {', '.join(sorted({h['scope'] for h in hs}))}); found {where}"
          + (f"; measured run {max(measured)}ms" if measured else ""),
          "Check it is scoped to what changed, debounced, or skipped when nothing changed - or make it async / move "
          "it to a rarer event. Run the probe with --run-hooks (with the user's OK) to measure it.",
          "measured" if measured else "candidate (pattern match)", "up to seconds per turn if it runs unscoped")
    # 6. dead matchers
    for h in hooks:
        if h["dead_matcher_parts"]:
            F(out, "dead-matcher", "medium",
              f"{h['event']} matcher names tool(s) that may not exist: {', '.join(h['dead_matcher_parts'])}",
              f"matcher `{h['matcher']}` (from {h['source']})",
              "Check the current tool name (e.g. the subagent tool was renamed Task -> Agent). A hook that never "
              "fires is a check you think is running but is not.", "pattern (hint, verify)")
    # 7. duplicates
    seen = {}
    for h in hooks:
        k = (h["event"], h["matcher"], h["command"].strip(), h.get("if"))
        seen.setdefault(k, []).append(h["source"])
    for (ev, m, c, _cond), srcs in seen.items():
        if len(srcs) > 1:
            F(out, "duplicate-hook", "medium", f"Same {ev} hook registered {len(srcs)} times",
              f"`{c[:100]}` in {', '.join(srcs)}", "Keep one copy.", "observed")
    # 8. per-event fan-out
    for ev, n in r["hooks"]["hot_counts"].items():
        if n >= 6:
            F(out, "hook-fanout", "medium", f"{n} hooks are registered on {ev} (not all fire on every call)",
              "Hooks for one event run in parallel, but each is a fresh process; many at once compete for CPU.",
              "Merge small checks that share a trigger into one script.", "observed")
    # 8b. measured slow hooks (only with --run-hooks)
    for h in hot:
        mr = h.get("measured_run") or {}
        if mr.get("ms") and mr["ms"] > 300:
            F(out, "slow-hook-measured", "high" if mr["ms"] > 1000 else "medium",
              f"{h['event']} hook took {round(mr['ms'])}ms on a synthetic call",
              f"`{h['command'][:120]}` (from {h['source']}; scope {h['scope']})"
              + ("; it TIMED OUT" if mr.get("timed_out") else ""),
              "Read the script: cache results, scope the work, exit early when nothing relevant changed, or make it async.",
              "measured (one synthetic run)", f"~{round(mr['ms'])}ms each time it fires")
    # 9. spawn floor / AV suspicion
    sf = r["timing"]["spawn_floor"]
    if sf.get("median_ms"):
        jitter = sf["p90_ms"] / max(sf["median_ms"], 1)
        if sf["median_ms"] > (60 if IS_WIN else 15) or (jitter > 2.5 and sf["p90_ms"] - sf["median_ms"] > 50):
            F(out, "spawn-floor", "high",
              "Starting ANY process is slow or spiky on this machine",
              f"trivial process: median {sf['median_ms']}ms, p90 {sf['p90_ms']}ms, max {sf['max_ms']}ms (measured)",
              "Usually antivirus / endpoint scanning of each new process. Ask the user (do not change it yourself) "
              "whether they can add an exclusion for their interpreter folders (Python, Node, Git) and dev folders.",
              "measured", "applies to every hook, tool call and build step")
    # 10. Windows Store python alias
    for name in ("python", "python3"):
        p = (L.get(name) or {}).get("path") or ""
        if "windowsapps" in p.lower():
            F(out, "store-python-alias", "high", f"`{name}` resolves to the Microsoft Store alias",
              p, "Install Python properly and turn off the 'App execution aliases' for python, or call the real path.",
              "observed")
    # 11. hook shell wrapper
    sw = r["timing"].get("hook_shell")
    if sw and sw.get("median_ms") and sw["median_ms"] > 80:
        F(out, "slow-hook-shell", "medium", "The shell that wraps every hook command is slow to start",
          f"{sw['shell']} -c true: median {sw['median_ms']}ms (measured)",
          "This cost repeats per hook. Fewer, merged hooks reduce how often it is paid.", "measured",
          f"~{round(sw['median_ms'])}ms per hook run")
    # 12. instructions
    tot = r["instructions"]["total_est_tokens"]
    if tot > 8000:
        F(out, "instruction-bloat", "high" if tot > 20000 else "medium",
          f"About {tot:,} tokens of instructions load at the start of every session",
          "; ".join(f"{os.path.basename(f['path'])} ~{f['est_tokens']:,}" for f in
                    sorted(r["instructions"]["files"], key=lambda f: -f["est_tokens"])[:4]) + " (estimated: chars/4)",
          "Keep only always-true rules in always-loaded files; move procedures into docs read on demand or skills.",
          "estimated", "context on every turn; slower, costlier, and rules get followed less reliably")
    for f in r["instructions"]["files"]:
        if f["est_tokens"] > 5000:
            F(out, "big-instruction-file", "medium", f"{os.path.basename(f['path'])} alone is ~{f['est_tokens']:,} tokens",
              f["path"], "Split it: a short always-on core plus on-demand detail.", "estimated")
    # 13. MCP
    active = [s for s in r["mcp"] if not s["disabled"]]
    npx_mcp = [s for s in active if (s["launcher"] or "") in PKG_RUNNERS]
    if len(active) > 10:
        F(out, "many-mcp", "low", f"{len(active)} MCP servers configured",
          ", ".join(s["name"] for s in active[:12]),
          "Each starts at session launch and adds tool definitions. Disable the ones not used in this project.",
          "observed")
    if npx_mcp:
        F(out, "npx-mcp", "low", f"{len(npx_mcp)} MCP server(s) start through a package runner",
          ", ".join(s["name"] for s in npx_mcp), "Install them and point `command` at the installed binary.",
          "estimated", "session start-up time")
    # 14. leaked processes
    pc = r["environment"].get("process_counts") or {}
    if pc.get("node", 0) > 40 or pc.get("python", 0) > 25:
        F(out, "leaked-processes", "medium", "Many node/python processes are running",
          f"node={pc.get('node')}, python={pc.get('python')} (observed now)",
          "Often leftover dev servers, watchers or MCP servers from old sessions. Close them; they eat RAM/CPU.",
          "observed")
    # 15. git status
    gs = r["timing"].get("git_status")
    if gs and gs.get("median_ms") and gs["median_ms"] > 800:
        F(out, "slow-git-status", "medium", "`git status` is slow in this repo",
          f"median {gs['median_ms']}ms (measured)",
          "Hooks and Claude run git constantly. Try `git config core.untrackedCache true` and "
          "`git config core.fsmonitor true`, and check for huge untracked folders missing from .gitignore.",
          "measured", f"~{round(gs['median_ms'])}ms per git call")
    # 16. location
    cwd = r["project"]["root"].lower()
    if any(s in cwd for s in ("onedrive", "dropbox", "icloud", "google drive")):
        F(out, "synced-folder", "high", "The project lives in a cloud-synced folder", r["project"]["root"],
          "Sync clients scan every file write (node_modules, builds, git). Move the repo outside the synced folder.",
          "observed")
    if sys.platform.startswith("linux") and cwd.startswith("/mnt/"):
        F(out, "wsl-cross-fs", "high", "Working on a Windows drive from inside WSL", r["project"]["root"],
          "Cross-filesystem access from WSL is many times slower. Clone the repo inside the Linux home folder.",
          "observed")
    wt = r["environment"].get("worktrees")
    if wt and wt > 25:
        F(out, "many-worktrees", "low", f"{wt} git worktrees exist for this repo", "observed",
          "Remove finished ones (`git worktree prune` after deleting their folders); each holds its own deps.",
          "observed")
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: order.get(f["severity"], 3))
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--run-hooks", action="store_true")
    ap.add_argument("--include-stop", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    except Exception:
        pass

    cwd = Path(a.project).resolve()
    root = project_root(cwd)
    layers = settings_layers(root)
    raw_hooks, plugins = collect_hooks(layers)

    items = []
    for h in raw_hooks:
        scripts = script_files(h)
        script_text = "\n".join(exec_context(p, (read_text(p) or "")[:200_000]) for p in scripts)
        in_cmd = heavy_hits(h["command"])
        in_script = [x for x in heavy_hits(script_text) if x not in in_cmd]
        scope = "conditional" if h.get("if") else matcher_scope(h["event"], h["matcher"])
        items.append({**{k: v for k, v in h.items() if k != "plugin_root"},
                      "launcher": launcher_of(h["command"]), "scope": scope,
                      "scripts": [str(p) for p in scripts], "heavy": in_cmd + in_script,
                      "heavy_in_command": in_cmd, "heavy_in_script": in_script,
                      "dead_matcher_parts": dead_matcher_parts(h["event"], h["matcher"]),
                      "_plugin_root": h.get("plugin_root")})
    hot_counts = {}
    for h in items:
        if h["event"] in HOT_EVENTS:
            hot_counts[h["event"]] = hot_counts.get(h["event"], 0) + 1

    launchers = {h["launcher"] for h in items}
    timing = {"spawn_floor": spawn_floor(a.runs), "hook_shell": hook_shell_wrapper(a.runs),
              "launchers": time_launchers(launchers, a.runs), "git_status": git_status_time(root, a.runs)}

    if a.run_hooks:
        events = {"PreToolUse", "PostToolUse", "UserPromptSubmit"} | ({"Stop", "SubagentStop"} if a.include_stop else set())
        for h in items:
            if h["event"] not in events:
                continue
            if h.get("if") or h["async"]:
                # an `if` hook would not fire on the synthetic payload, and async hooks never make Claude wait
                h["measured_run"] = {"skipped": "conditional" if h.get("if") else "async"}
                continue
            h["measured_run"] = run_hook_once({**h, "plugin_root": h["_plugin_root"]}, str(root))
    for h in items:
        h.pop("_plugin_root", None)

    instr = instruction_files(cwd, root)
    report = {
        "probe_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": {"os": platform.system(), "release": platform.release(), "machine": platform.machine(),
                     "python": platform.python_version(), "cpu_count": os.cpu_count()},
        "project": {"cwd": str(cwd), "root": str(root), "is_git": (root / ".git").exists()},
        "claude_dir": str(CLAUDE_DIR),
        "settings_files": [{"layer": n, "path": str(p), "parse_error": bool(isinstance(d, dict) and d.get("__parse_error__"))}
                           for n, p, d in layers],
        "plugins_enabled": plugins,
        "hooks": {"count": len(items), "hot_counts": hot_counts, "items": items,
                  "ran_hooks": a.run_hooks, "included_stop": a.include_stop},
        "timing": timing,
        "instructions": {"files": instr, "total_est_tokens": sum(f["est_tokens"] for f in instr)},
        "mcp": mcp_servers(root),
        "environment": {"process_counts": process_counts(), "worktrees": worktree_count(root)},
    }
    report["findings"] = build_findings(report)
    text = json.dumps(report, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out} ({len(report['findings'])} findings)")
    else:
        print(text)


if __name__ == "__main__":
    main()
