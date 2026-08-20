#!/usr/bin/env python3
"""
timing_report.py — Retroactive timing analysis of Claude Code sessions.

Reads the transcript JSONL files Claude Code already writes and derives, per
tool call, how long it took (tool_result timestamp - tool_use timestamp),
matched by tool_use id. No instrumentation, no runtime cost: it mines data that
is already on disk for every past session.

What it answers:
  - Where does wall-clock time actually go? (tools, Bash sub-categories,
    subagent dispatches, hooks)
  - Which Bash commands are slow? (tests / typecheck / lint / build / git ...)
  - How much do subagents cost, and where does time go *inside* them?
  - How much overhead do the PreToolUse/PostToolUse hooks add?

Usage:
  py ~/.ai-instructions/tools/timing_report.py                 # all sessions
  py ~/.ai-instructions/tools/timing_report.py --session latest
  py ~/.ai-instructions/tools/timing_report.py --since 2026-08-01
  py ~/.ai-instructions/tools/timing_report.py --top 25
  py ~/.ai-instructions/tools/timing_report.py --json          # machine-readable
  py ~/.ai-instructions/tools/timing_report.py --projects-dir <path>

A "duration" for a Task/Agent tool call is the FULL subagent wall time (it
already includes everything the subagent did). To avoid double counting, the
report separates the main chain from subagent-internal (sidechain) work.
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

# Windows consoles default to cp1252 and choke on non-ASCII; force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Tools that block on the human, not on the agent. Their "duration" is
# user think/answer time and must never be counted as agent slowness.
INTERACTIVE_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


# ---------- helpers ----------

def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_dur(seconds):
    if seconds is None:
        return "-"
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{int(m)}m{int(s):02d}s"
    h, m = divmod(int(m), 60)
    return f"{h}h{m:02d}m"


def pct(values, p):
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def median(values):
    return pct(values, 50)


# Bash command classifier. Order matters: first match wins.
BASH_BUCKETS = [
    ("test",      re.compile(r"\b(npm (run )?test|vitest|jest|pytest|go test|cargo test|playwright test|npm t)\b", re.I)),
    ("typecheck", re.compile(r"\b(tsc|tsc --noemit|tsc -p|type-?check|mypy|pyright)\b", re.I)),
    ("lint",      re.compile(r"\b(eslint|prettier|ruff|flake8|black|npm run lint|biome)\b", re.I)),
    ("build",     re.compile(r"\b(npm run build|vite build|next build|webpack|rollup|tsup|cargo build|go build|make\b)\b", re.I)),
    ("install",   re.compile(r"\b(npm install|npm ci|npm i |pnpm install|yarn install|pip install|cargo fetch)\b", re.I)),
    ("git",       re.compile(r"^\s*git\b|\bgit -C\b", re.I)),
    ("worktree",  re.compile(r"worktree_cleanup|scratch_worktree|worktree add|workboard\.py", re.I)),
    ("search",    re.compile(r"\b(grep|rg|ripgrep|find |fd |glob)\b", re.I)),
    ("fileio",    re.compile(r"\b(cat|sed|awk|head|tail|wc|ls|cp|mv|mkdir|echo)\b", re.I)),
]


def classify_bash(cmd):
    if not cmd:
        return "other"
    for name, rx in BASH_BUCKETS:
        if rx.search(cmd):
            return name
    return "other"


# Classify a subagent Task call by its declared type/description.
def classify_task(inp):
    if not isinstance(inp, dict):
        return "subagent:unknown"
    t = (inp.get("subagent_type") or "").strip().lower()
    if t:
        return f"subagent:{t}"
    desc = (inp.get("description") or inp.get("prompt") or "").lower()
    for key in ("review", "test", "explore", "plan", "debug", "architect", "simplif"):
        if key in desc:
            return f"subagent:{key}"
    return "subagent:general"


# ---------- core parse ----------

def iter_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def analyze_file(path, since=None):
    """Return dict of aggregates for one transcript file."""
    # id -> (start_ts, name, input, is_sidechain)
    pending = {}
    calls = []          # completed (name, dur_s, is_sidechain, input, start_ts)
    hooks = []          # (label, durationMs)
    session_first = None
    session_last = None
    session_id = os.path.splitext(os.path.basename(path))[0]

    for d in iter_lines(path):
        ts = parse_ts(d.get("timestamp"))
        # Apply the --since window uniformly to every event (calls AND hooks
        # AND wall-clock), so a windowed report is internally consistent.
        if since and ts and ts < since:
            continue
        if ts:
            if session_first is None or ts < session_first:
                session_first = ts
            if session_last is None or ts > session_last:
                session_last = ts

        # hook durations live on attachment entries
        att = d.get("attachment")
        if isinstance(att, dict) and "durationMs" in att:
            label = (att.get("hookName") or att.get("hookEvent")
                     or (att.get("command") or "")[:60] or "hook")
            try:
                hooks.append((label, float(att.get("durationMs") or 0)))
            except Exception:
                pass

        is_side = bool(d.get("isSidechain"))
        msg = d.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                cid = c.get("id")
                if cid:
                    pending[cid] = (ts, c.get("name"), c.get("input"), is_side)
            elif c.get("type") == "tool_result":
                cid = c.get("tool_use_id")
                start = pending.pop(cid, None)
                if start and start[0] and ts:
                    dur = (ts - start[0]).total_seconds()
                    if dur < 0:
                        continue
                    calls.append((start[1], dur, start[3], start[2], start[0]))

    if since:
        calls = [c for c in calls if c[4] and c[4] >= since]

    return {
        "session_id": session_id,
        "path": path,
        "first": session_first,
        "last": session_last,
        "calls": calls,
        "hooks": hooks,
    }


def summarize(files_data, top_n=15, cap_s=600):
    main_by_tool = defaultdict(list)     # tool -> [durations] (main chain, non-Task)
    interactive = defaultdict(list)      # tool -> [durations] (user-input wait)
    task_by_type = defaultdict(list)     # subagent:type -> [durations]
    side_by_tool = defaultdict(list)     # tool -> [durations] (inside subagents)
    bash_main = defaultdict(list)        # bucket -> [durations] (main chain)
    bash_side = defaultdict(list)        # bucket -> [durations] (sidechain)
    hook_by_label = defaultdict(list)    # label -> [ms]
    all_ops = []                         # (dur, tool, side, snippet, session)
    excluded_n = 0                       # ops longer than cap: assumed idle-gap
    excluded_s = 0.0
    sessions = []

    for fd in files_data:
        # Skip sessions with no in-window activity (empty after --since filter).
        if not fd["calls"] and not fd["hooks"] and not fd["first"]:
            continue
        wall = None
        if fd["first"] and fd["last"]:
            wall = (fd["last"] - fd["first"]).total_seconds()
        sessions.append((fd["session_id"], wall, len(fd["calls"])))

        for (name, dur, is_side, inp, start_ts) in fd["calls"]:
            # A single tool call longer than the cap is almost certainly an
            # idle gap (user walked away mid-call, session paused) rather than
            # real execution time. Exclude from totals; count it separately.
            if name not in INTERACTIVE_TOOLS and dur > cap_s:
                excluded_n += 1
                excluded_s += dur
                continue
            snippet = ""
            if isinstance(inp, dict):
                snippet = (inp.get("command") or inp.get("description")
                           or inp.get("file_path") or inp.get("pattern")
                           or inp.get("description") or "")
            snippet = str(snippet).replace("\n", " ")[:80]
            # Interactive tools are human-wait; keep them out of "slowest ops".
            if name not in INTERACTIVE_TOOLS:
                all_ops.append((dur, name, is_side, snippet, fd["session_id"]))

            if name in INTERACTIVE_TOOLS:
                interactive[name].append(dur)
                continue  # user think-time, not agent time

            if name in ("Task", "Agent"):
                task_by_type[classify_task(inp)].append(dur)
                continue  # Task duration already includes its sidechain; don't also bucket it

            if is_side:
                side_by_tool[name].append(dur)
                if name == "Bash":
                    bash_side[classify_bash(isinstance(inp, dict) and inp.get("command") or "")].append(dur)
            else:
                main_by_tool[name].append(dur)
                if name == "Bash":
                    bash_main[classify_bash(isinstance(inp, dict) and inp.get("command") or "")].append(dur)

        for (label, ms) in fd["hooks"]:
            hook_by_label[label].append(ms)

    all_ops.sort(reverse=True, key=lambda x: x[0])
    return {
        "sessions": sessions,
        "excluded_n": excluded_n,
        "excluded_s": excluded_s,
        "interactive": interactive,
        "main_by_tool": main_by_tool,
        "task_by_type": task_by_type,
        "side_by_tool": side_by_tool,
        "bash_main": bash_main,
        "bash_side": bash_side,
        "hook_by_label": hook_by_label,
        "top_ops": all_ops[:top_n],
    }


# ---------- render ----------

def _row(label, vals):
    tot = sum(vals)
    return (label, len(vals), tot, median(vals) or 0, pct(vals, 90) or 0, max(vals) if vals else 0)


def _print_table(title, rows, sort_idx=2):
    if not rows:
        return
    rows = sorted(rows, key=lambda r: r[sort_idx], reverse=True)
    print(f"\n{title}")
    print(f"  {'category':<26}{'n':>5}{'total':>11}{'median':>9}{'p90':>9}{'max':>9}")
    print("  " + "-" * 68)
    for (label, n, tot, med, p90, mx) in rows:
        print(f"  {label[:26]:<26}{n:>5}{fmt_dur(tot):>11}{fmt_dur(med):>9}{fmt_dur(p90):>9}{fmt_dur(mx):>9}")
    print(f"  {'TOTAL':<26}{sum(r[1] for r in rows):>5}{fmt_dur(sum(r[2] for r in rows)):>11}")


def render_text(summ):
    sessions = summ["sessions"]
    print("=" * 72)
    print("CLAUDE CODE TIMING REPORT")
    print("=" * 72)
    print(f"\nSessions analyzed: {len(sessions)}")
    total_wall = sum(w for (_, w, _) in sessions if w)
    total_calls = sum(c for (_, _, c) in sessions)
    print(f"Total session wall-clock: {fmt_dur(total_wall)}   |   tool calls: {total_calls}")

    interactive_total = sum(sum(v) for v in summ["interactive"].values())
    if interactive_total:
        print(f"User-input wait (AskUserQuestion/plan approval, NOT agent time): {fmt_dur(interactive_total)}")
    if summ.get("excluded_n"):
        print(f"Excluded {summ['excluded_n']} idle-gap ops (single call > cap; {fmt_dur(summ['excluded_s'])} "
              f"of wall time that was really the session sitting idle, not the tool running)")
    print("NOTE: trust median/p90 over total+max; totals still carry some idle inflation below the cap.")

    _print_table("MAIN-CHAIN TOOL TIME (what the primary agent ran directly)",
                 [_row(k, v) for k, v in summ["main_by_tool"].items()])

    _print_table("BASH BY CATEGORY (main chain) -- the real work buckets",
                 [_row(k, v) for k, v in summ["bash_main"].items()])

    _print_table("SUBAGENT DISPATCHES (Task wall time -- includes all their internal work)",
                 [_row(k, v) for k, v in summ["task_by_type"].items()])

    _print_table("INSIDE SUBAGENTS -- bash by category (where subagent time goes)",
                 [_row(k, v) for k, v in summ["bash_side"].items()])

    _print_table("HOOK OVERHEAD (per-call subprocess cost; ms summed to seconds)",
                 [_row(k, [x / 1000.0 for x in v]) for k, v in summ["hook_by_label"].items()])

    if summ.get("spans"):
        render_events(summ["spans"])

    print("\nTOP SLOWEST INDIVIDUAL OPERATIONS (agent work only; human-wait excluded)")
    print(f"  {'dur':>9}  {'tool':<10}{'loc':<5}snippet")
    print("  " + "-" * 68)
    for (dur, name, side, snip, sess) in summ["top_ops"]:
        loc = "sub" if side else "main"
        print(f"  {fmt_dur(dur):>9}  {name[:10]:<10}{loc:<5}{snip}")
    print()


EVENTS_FILE = os.path.expanduser("~/.claude/logs/events.jsonl")


def read_event_spans(since=None):
    """Pair start/end markers from log_event.py into named spans."""
    if not os.path.exists(EVENTS_FILE):
        return [], []
    open_stacks = defaultdict(list)   # label -> [start_ts]
    spans = []                        # (label, dur_s, note)
    marks = []                        # (label, ts, note)
    for d in iter_lines(EVENTS_FILE):
        ts = parse_ts(d.get("ts"))
        if since and ts and ts < since:
            continue
        kind, label = d.get("kind"), d.get("label")
        if kind == "start":
            open_stacks[label].append(ts)
        elif kind == "end":
            if open_stacks[label]:
                start = open_stacks[label].pop()
                if start and ts:
                    spans.append((label, (ts - start).total_seconds(), d.get("note", "")))
        elif kind == "mark":
            marks.append((label, ts, d.get("note", "")))
    return spans, marks


def render_events(spans):
    if not spans:
        return
    by_label = defaultdict(list)
    for (label, dur, note) in spans:
        by_label[label].append(dur)
    _print_table("NAMED SPANS (from log_event.py -- your semantic gate markers)",
                 [_row(k, v) for k, v in by_label.items()])


def main():
    ap = argparse.ArgumentParser(description="Retroactive timing analysis of Claude Code sessions.")
    ap.add_argument("--projects-dir", default=DEFAULT_PROJECTS_DIR)
    ap.add_argument("--session", help="'latest', a session id, or substring of the filename")
    ap.add_argument("--since", help="YYYY-MM-DD (UTC): only operations starting on/after this date")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--cap-seconds", type=float, default=600,
                    help="Single ops longer than this are treated as idle gaps and excluded (default 600).")
    ap.add_argument("--no-events", action="store_true", help="Skip the log_event.py named-span section.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = os.path.expanduser(args.projects_dir)
    files = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        files.extend(glob.glob(os.path.join(d, "*.jsonl")))
    files = sorted(files, key=lambda p: os.path.getmtime(p))

    if not files:
        print(f"No transcripts found under {root}", file=sys.stderr)
        sys.exit(1)

    if args.session:
        if args.session == "latest":
            files = files[-1:]
        else:
            files = [f for f in files if args.session in f]
            if not files:
                print(f"No session matched '{args.session}'", file=sys.stderr)
                sys.exit(1)

    since = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print("--since must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)

    files_data = [analyze_file(f, since=since) for f in files]
    summ = summarize(files_data, top_n=args.top, cap_s=args.cap_seconds)

    if not args.no_events:
        spans, _marks = read_event_spans(since=since)
        summ["spans"] = spans

    if args.json:
        out = {
            "sessions": [{"id": s, "wall_s": w, "calls": c} for (s, w, c) in summ["sessions"]],
            "main_by_tool": {k: {"n": len(v), "total_s": sum(v), "median_s": median(v), "p90_s": pct(v, 90)}
                             for k, v in summ["main_by_tool"].items()},
            "bash_main": {k: {"n": len(v), "total_s": sum(v)} for k, v in summ["bash_main"].items()},
            "task_by_type": {k: {"n": len(v), "total_s": sum(v), "median_s": median(v)}
                             for k, v in summ["task_by_type"].items()},
            "bash_side": {k: {"n": len(v), "total_s": sum(v)} for k, v in summ["bash_side"].items()},
            "hooks": {k: {"n": len(v), "total_ms": sum(v), "mean_ms": sum(v) / len(v)}
                      for k, v in summ["hook_by_label"].items()},
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        render_text(summ)


if __name__ == "__main__":
    main()
