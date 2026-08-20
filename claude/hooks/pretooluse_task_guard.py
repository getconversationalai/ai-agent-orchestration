#!/usr/bin/env python3
"""PreToolUse:Task/Agent — WARN-only parallelization reminder.

A non-blocking nudge at the moment a helper is dispatched: confirm the dispatch
matches the parallelization decision. It NEVER denies a dispatch and always
exits 0 — it only surfaces a one-line reminder on stderr.

(It can't reliably tell a bad sequential loop from a legit solo dispatch at the
hook level — the helper-per-message shape looks identical one-at-a-time — so
this is deliberately an advisory reminder, not a smart detector.)
"""
import sys
import json


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # never block on unexpected input
    if data.get("tool_name") not in ("Task", "Agent"):
        sys.exit(0)
    print(
        "Parallelization: helper dispatch — is it INDEPENDENT + SUBSTANTIAL and part of a "
        "CONCURRENT BATCH? If it's one-at-a-time, run it INLINE instead. "
        "Cap: py ~/.ai-instructions/tools/headroom.py",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
