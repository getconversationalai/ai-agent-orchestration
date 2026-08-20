#!/usr/bin/env python3
"""
log_event.py — Opt-in semantic timing markers.

The transcripts already record every tool call and hook with timing, so
timing_report.py can reconstruct where wall-clock goes WITHOUT any runtime
logging. The one thing transcripts do NOT capture is *named* work spans that
are not a single tool call -- e.g. "plan-review gate", "phase-1 gate",
"TDD cycle for task 3". This helper lets you bracket those, cheaply and only
when you choose to.

Deliberately NOT wired into any PreToolUse/PostToolUse hook: doing so would add
a subprocess to every tool call and make the very latency we are trying to cut
worse. Call it explicitly, only around spans you care about.

Usage:
  py ~/.ai-instructions/tools/log_event.py start plan-review-gate
  ...work...
  py ~/.ai-instructions/tools/log_event.py end   plan-review-gate

  # one-shot marker (no paired end):
  py ~/.ai-instructions/tools/log_event.py mark phase-1-complete

Appends one JSON line to ~/.claude/logs/events.jsonl:
  {"ts": "...ISO...", "kind": "start|end|mark", "label": "...", "note": "..."}

timing_report.py --events folds these into the report (pairs start/end by
label, reports each span's duration).
"""
import json
import os
import sys
from datetime import datetime, timezone

LOG_DIR = os.path.expanduser("~/.claude/logs")
LOG_FILE = os.path.join(LOG_DIR, "events.jsonl")


def main():
    if len(sys.argv) < 3:
        print("usage: log_event.py <start|end|mark> <label> [note...]", file=sys.stderr)
        sys.exit(2)
    kind = sys.argv[1].lower()
    if kind not in ("start", "end", "mark"):
        print("kind must be start|end|mark", file=sys.stderr)
        sys.exit(2)
    label = sys.argv[2]
    note = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""

    os.makedirs(LOG_DIR, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "label": label,
        "note": note,
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
