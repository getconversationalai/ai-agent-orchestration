#!/usr/bin/env python3
"""
Claude Code session-log audit - where the TIME actually goes (read-only).

Reads the local session transcripts (~/.claude/projects/<project-key>/*.jsonl) and
measures the workflow, not the machine:
  * waits on the user: questions (AskUserQuestion) and permission prompts on gated
    commands (git rebase/merge/cherry-pick/push to main, DB writes) - raw, capped
    at 30 min per wait, and daytime-only (fair numbers: sessions overlap and a
    wait keeps "counting" while the user sleeps)
  * agent active time and how many sessions overlap
  * checks: typecheck / tests / lint / local builds - counts, totals, and the
    outlier runs (>10 min) that usually explain most of the total
  * subagents: real durations from task-notification usage blocks (background
    agents return in ~3 s, so the tool call's own duration is meaningless),
    split into review/audit vs other
  * slow MCP calls (>10 min) classified: write that asked / read the DB hook
    mistook for a write / read batched with a gated call / error / unexplained
    (unexplained = no prompt, no error -> the permission MODE asked; see SKILL.md)
  * instruction docs and memory files opened on demand
  * context compactions (sessions that filled up)
  * per-project tool usage: MCP servers, skills, subagent types (for tool lists)

Privacy: prints only tool names, counts and durations. Never prints command text,
SQL, file contents or arguments. Nothing leaves the machine.

Usage:
  python session_audit.py [--projects-dir ~/.claude/projects] [--days 14]
                          [--project SUBSTRING] [--day-start 9 --day-end 23]
                          [--out report.json]
Standard library only (Python 3.8+).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

CAP = 1800  # seconds: count at most 30 min per wait (fair number)
SLOW = 600  # seconds: "outlier" threshold

GATED_BASH = re.compile(
    r"git\s+(rebase|merge|cherry-pick)\b(?!-)|HEAD:(refs/heads/)?(main|master)\b"
    r"|push(?:\s+-\S+)*\s+\S+\s+(main|master)\b|\bpsql\b|supabase\s+db\b"
)
CHECKS = [
    ("typecheck", re.compile(r"\btypecheck\b|tsc\s+--noEmit|\bmypy\b|\bpyright\b")),
    ("tests", re.compile(r"\bvitest\b|\bjest\b|\bpytest\b|\bgo test\b|\bcargo test\b|\bnpm (run )?test\b|\bpnpm (run )?test\b")),
    ("lint", re.compile(r"\beslint\b|next lint|\bruff\b|\bflake8\b|\bgolangci-lint\b|\bnpm run lint\b|\bpnpm (run )?lint\b")),
    ("local build", re.compile(r"next build|\bvite build\b|\bnpm run build\b|\bpnpm\b[^;&|]*\sbuild\b|\byarn\b[^;&|]*\sbuild\b|turbo run build|\bcargo build\b")),
    ("install", re.compile(r"\b(npm|pnpm|yarn) (install|i|ci)\b|pip install")),
    ("git fetch", re.compile(r"\bgit fetch\b")),
    ("git commit", re.compile(r"\bgit commit\b")),
]
SCOPED = re.compile(r"--filter|--project|exec (tsc|vitest)|vitest run \S|pytest \S|-k ")
WRITE_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|MERGE|VACUUM|REINDEX|COMMENT\s+ON|CALL|DO|COPY)\b", re.I
)
REVIEW_WORDS = re.compile(r"review|audit|verify|adversar", re.I)
NOTIFY = re.compile(
    r"<task-id>([^<]+)</task-id>[\s\S]*?<summary>Agent \"([^\"]*)\"[^<]*</summary>[\s\S]*?<duration_ms>(\d+)</duration_ms>"
)
INSTR_PATH = re.compile(r"(\.ai-instructions|[\\/]memory[\\/]|CLAUDE\.md|AGENTS\.md|GEMINI\.md)", re.I)


def ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def is_daytime(t, start, end):
    h = datetime.fromtimestamp(t).hour  # local time of this machine
    return start <= h < end


def agg():
    return {"n": 0, "secs": 0.0, "over10m": 0, "secs_over10m": 0.0}


def add(d, secs):
    d["n"] += 1
    d["secs"] += secs
    if secs > SLOW:
        d["over10m"] += 1
        d["secs_over10m"] += secs


def waits():
    return {"n": 0, "raw": 0.0, "capped": 0.0, "day_n": 0, "day_capped": 0.0, "over10m": 0, "_all": []}


def add_wait(d, secs, day):
    d["n"] += 1
    d["raw"] += secs
    d["capped"] += min(secs, CAP)
    d["_all"].append(secs)
    if secs > SLOW:
        d["over10m"] += 1
    if day:
        d["day_n"] += 1
        d["day_capped"] += min(secs, CAP)


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def hours(s):
    return round(s / 3600, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--project", default="", help="only project dirs whose name contains this")
    ap.add_argument("--day-start", type=int, default=9)
    ap.add_argument("--day-end", type=int, default=23)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    root = Path(a.projects_dir).expanduser()
    since = time.time() - a.days * 86400
    files = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        if d.is_dir() and a.project.lower() in d.name.lower():
            files += [f for f in d.glob("*.jsonl") if f.stat().st_mtime >= since]

    asks, prompts = waits(), waits()
    checks = defaultdict(agg)
    scoped = {"typecheck": Counter(), "tests": Counter()}
    agents = {"review": [], "other": []}
    seen_tasks = set()
    slow_mcp = Counter()
    instr_opens = Counter()
    compactions = 0
    active_minutes = Counter()
    per_project = defaultdict(lambda: {"sessions": 0, "mcp": Counter(), "skills": Counter(), "agents": Counter()})
    pushes_main = 0
    feat_commits = 0
    # Friction / busywork / churn / cold starts / context growth (counts only).
    hook_blocks = Counter()          # hook refusals by reason (hook text, not user data)
    busy = Counter()                 # rule-driven bookkeeping calls
    churn = Counter()                # integration git ops
    cold = Counter()                 # worktree creation, installs, dev servers
    cleanup_secs = []                # worktree removal durations
    deploy_waits = Counter()         # polling / sleeping on deploys
    big_outputs = Counter()          # tool results > 20k chars, by tool
    big_output_chars = 0
    turns_per_session = []
    small_agents = 0                 # subagents that finished in < 2 min (cold-start overhead dominates)
    paperwork = Counter()            # spec / plan / summary docs written
    notifications = 0
    insight_blocks = 0
    insight_chars = 0

    for f in files:
        proj = f.parent.name
        per_project[proj]["sessions"] += 1
        uses, batch_of, batches = {}, {}, defaultdict(list)
        my_minutes = set()
        turns = 0
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("isSidechain"):
                continue
            t = ts(j.get("timestamp"))
            msg = j.get("message") or {}
            c = msg.get("content")
            if j.get("isCompactSummary") or (isinstance(c, str) and "continued from a previous conversation" in c):
                compactions += 1
            if t and j.get("type") == "assistant":
                my_minutes.add(int(t // 60))
            if j.get("type") == "assistant":
                turns += 1
            texts = [c] if isinstance(c, str) else [b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text"]
            if j.get("type") == "assistant":
                for tx in texts:
                    for m in re.finditer(r"★ Insight[\s\S]*?(?:─{10,}`?\s*$|\Z)", tx or "", re.M):
                        insight_blocks += 1
                        insight_chars += len(m.group(0))
            for tx in texts:
                for m in NOTIFY.finditer(tx or ""):
                    if m.group(1) in seen_tasks:
                        continue
                    seen_tasks.add(m.group(1))
                    agents["review" if REVIEW_WORDS.search(m.group(2)) else "other"].append(int(m.group(3)) / 1000)
            if not isinstance(c, list):
                continue
            mid = msg.get("id")
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    uses[b.get("id")] = (b.get("name", ""), b.get("input") or {}, t)
                    if mid:
                        batch_of[b.get("id")] = mid
                        batches[mid].append((b.get("name", ""), b.get("input") or {}))
                    name = b.get("name", "")
                    mm = re.match(r"mcp__(.+?)__", name)
                    if mm:
                        per_project[proj]["mcp"][mm.group(1)] += 1
                    if name == "Skill":
                        per_project[proj]["skills"][str((b.get("input") or {}).get("skill", "?"))] += 1
                    if name in ("Agent", "Task"):
                        per_project[proj]["agents"][str((b.get("input") or {}).get("subagent_type", "general-purpose"))] += 1
                    inp0 = b.get("input") or {}
                    if name in ("TodoWrite", "TaskCreate", "TaskUpdate"):
                        busy["todo updates"] += 1
                    if name == "PushNotification":
                        notifications += 1
                    if name in ("Write", "Edit"):
                        fp = str(inp0.get("file_path", "")).replace("\\", "/").lower()
                        if re.search(r"/(specs?|plans?)/|-(spec|plan|design)\.md$", fp):
                            paperwork["spec/plan docs written or edited"] += 1
                    if name == "Bash":
                        cmd0 = str(inp0.get("command", ""))
                        if "workboard" in cmd0 and ".py" in cmd0:
                            busy["work-board commands"] += 1
                        if re.search(r"git\s+merge\s+(\S+/)?(main|master)\b", cmd0):
                            churn["merge main INTO a branch"] += 1
                        if re.search(r"git\s+rebase\b", cmd0):
                            churn["rebase"] += 1
                        if re.search(r"git\s+worktree\s+add\b|scratch_worktree", cmd0):
                            cold["worktree created / prepared"] += 1
                        if re.search(r"\b(npm|pnpm|yarn)\b[^;&|]*\b(dev|start)\b|next dev|\bvite\b(?!st)", cmd0) and "build" not in cmd0:
                            cold["dev server started"] += 1
                        if re.search(r"\bsleep\s+\d{2,}", cmd0):
                            deploy_waits["sleep >= 10s (waiting on something)"] += 1
                    if re.search(r"list_deploys|get_deploy", name):
                        deploy_waits["deploy status checks"] += 1
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                    name, inp, t0 = uses[b["tool_use_id"]]
                    body = b.get("content")
                    body_s = body if isinstance(body, str) else json.dumps(body)
                    if len(body_s) > 20000:
                        big_outputs[name if not name.startswith("mcp__") else "mcp:" + name.split("__")[1]] += 1
                        big_output_chars += len(body_s)
                    # Only genuine hook refusals: "PreToolUse:<Tool> hook error: ... BLOCKED: <reason>".
                    mb = re.search(r"PreToolUse:\w+ hook error:[^\n]*?BLOCKED:\s*([^\n.]{5,80})", body_s)
                    if mb:
                        hook_blocks[re.sub(r"[`'\"\\]", "", mb.group(1)).strip()[:70]] += 1
                    if not (t and t0):
                        continue
                    secs = max(0.0, t - t0)
                    if name == "Bash" and re.search(r"worktree_cleanup|git\s+worktree\s+remove", str(inp.get("command", ""))):
                        cleanup_secs.append(secs)
                    if name in ("Agent", "Task") and not inp.get("run_in_background") and secs < 120:
                        small_agents += 1
                    day = is_daytime(t0, a.day_start, a.day_end)
                    if name == "AskUserQuestion":
                        add_wait(asks, secs, day)
                    elif name == "Bash" and not inp.get("run_in_background"):
                        cmd = str(inp.get("command", ""))
                        gated = bool(GATED_BASH.search(cmd))
                        if gated and secs > 45:
                            add_wait(prompts, secs, day)
                        if re.search(r"HEAD:(refs/heads/)?(main|master)\b|push(?:\s+-\S+)*\s+\S+\s+(main|master)\b", cmd):
                            pushes_main += 1
                        if re.search(r"git commit[\s\S]*-m\s+[\"']?(feat|fix|perf|refactor)", cmd):
                            feat_commits += 1
                        # A gated command's duration is mostly the wait for approval -
                        # it is counted above as a permission wait, not as a check.
                        for label, rx in ([] if gated else CHECKS):
                            if rx.search(cmd):
                                add(checks[label], secs)
                                if label in scoped:
                                    scoped[label]["scoped" if SCOPED.search(cmd) else "whole"] += 1
                                break
                    elif name in ("Read", "Grep", "Glob"):
                        p = str(inp.get("file_path") or inp.get("path") or "")
                        mm = INSTR_PATH.search(p)
                        if mm:
                            instr_opens[mm.group(1).strip("\\/").lower()] += 1
                    elif re.search(r"(execute_sql|apply_migration|query)$", name) and name.startswith("mcp__"):
                        if secs <= SLOW:
                            continue
                        q = str(inp.get("query") or inp.get("sql") or "")
                        res = json.dumps(b.get("content"))[:4000]
                        if name.endswith("apply_migration") or (WRITE_SQL.search(q) and re.match(r"^\s*(begin;?\s*)?(insert|update|delete|create|alter|drop|truncate|grant|revoke)\b", q, re.I)):
                            k = "write - asked the user (expected)"
                        elif WRITE_SQL.search(q):
                            k = "read the DB hook mistook for a write"
                        elif any(n == "AskUserQuestion" or n.endswith("apply_migration") or (n == "Bash" and GATED_BASH.search(str(i.get("command", ""))))
                                 for n, i in batches.get(batch_of.get(b["tool_use_id"]), []) if i is not inp):
                            k = "read batched with a gated call"
                        elif re.search(r"error|timeout|timed out|fetch failed|ECONN", res, re.I):
                            k = "error / timeout"
                        else:
                            k = "unexplained (no hook prompt, no error -> permission mode asked)"
                        slow_mcp[k] += 1
        for m in my_minutes:
            active_minutes[m] += 1
        if turns:
            turns_per_session.append(turns)

    conc = list(active_minutes.values())

    def wsum(d):
        out = {k: v for k, v in d.items() if not k.startswith("_")}
        out["median_min"] = round(median(d["_all"]) / 60, 1)
        for k in ("raw", "capped", "day_capped"):
            out[k + "_h"] = hours(out.pop(k))
        return out

    rep = {
        "window_days": a.days,
        "sessions": len(files),
        "agent_active_h": round(len(conc) / 60, 1),
        "avg_sessions_at_once": round(sum(conc) / len(conc), 2) if conc else 0,
        "max_sessions_at_once": max(conc) if conc else 0,
        "pushes_to_main": pushes_main,
        "feature_commits": feat_commits,
        "compactions": compactions,
        "waits_on_user": {"questions": wsum(asks), "permission_prompts": wsum(prompts)},
        "checks": {k: {"runs": v["n"], "total_h": hours(v["secs"]), "runs_over10m": v["over10m"], "hours_from_over10m": hours(v["secs_over10m"])} for k, v in checks.items()},
        "scoped_vs_whole": {k: dict(v) for k, v in scoped.items()},
        "subagents": {k: {"n": len(v), "median_min": round(median(v) / 60, 1), "total_agent_h": hours(sum(v)), "over10m": sum(1 for x in v if x > SLOW)} for k, v in agents.items()},
        "slow_mcp_calls_over10m": dict(slow_mcp),
        "friction_hook_blocks": dict(hook_blocks.most_common(15)),
        "busywork": dict(busy),
        "integration_churn": dict(churn),
        "cold_starts": dict(cold),
        "worktree_cleanup": {"n": len(cleanup_secs), "median_s": round(median(cleanup_secs)), "over5m": sum(1 for x in cleanup_secs if x > 300)},
        "deploy_waiting": dict(deploy_waits),
        "big_tool_outputs_over20k_chars": {"by_tool": dict(big_outputs.most_common(10)), "total_chars": big_output_chars, "approx_tokens": big_output_chars // 4},
        "session_length_turns": {"median": median(turns_per_session), "p90": (sorted(turns_per_session)[int(len(turns_per_session) * 0.9)] if turns_per_session else 0), "over_1000": sum(1 for x in turns_per_session if x > 1000)},
        "small_foreground_subagents_under2m": small_agents,
        "paperwork": dict(paperwork),
        "phone_notifications_sent": notifications,
        "explanatory_insight_blocks": {"n": insight_blocks, "approx_tokens": insight_chars // 4},
        "instruction_and_memory_opens": dict(instr_opens),
        "per_project_tools": {
            p: {"sessions": v["sessions"], "mcp_servers": dict(v["mcp"].most_common()), "skills": dict(v["skills"].most_common()), "subagent_types": dict(v["agents"].most_common())}
            for p, v in sorted(per_project.items(), key=lambda kv: -kv[1]["sessions"]) if v["mcp"] or v["skills"] or v["agents"]
        },
    }
    text = json.dumps(rep, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
