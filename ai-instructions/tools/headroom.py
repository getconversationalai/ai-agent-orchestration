#!/usr/bin/env python3
"""headroom.py — recommend how many agents to fan out RIGHT NOW.

Gate 3 of the parallelization decision (see parallel-agents.md): instead of a
fixed ceiling, size a concurrent batch to the machine's live capacity. On an
idle machine you get the full static allowance; on a loaded one (many node
processes = agents already busy) the cap drops toward 1. Never exceeds the
configured static max, never returns < 1, and falls back to the static max if
measurement fails (best-effort, never blocks a decision).

Usage:
  py ~/.ai-instructions/tools/headroom.py            # prints the recommended cap
  py ~/.ai-instructions/tools/headroom.py --max 6    # override the static max
  py ~/.ai-instructions/tools/headroom.py --json     # {cap, cores, busy_units, static_max}
"""
import argparse
import json
import os
import subprocess
import sys

# Rough number of node processes a single active agent tends to spin up on this
# machine (empirical). Used to translate a raw node-proc count into "how many
# agents are already busy".
_PROCS_PER_AGENT = 6


def _cpu_cores():
    return os.cpu_count() or 4


def _node_proc_count():
    """Best-effort count of running node.exe processes (a load proxy)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq node.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return sum(1 for line in out.splitlines() if "node.exe" in line.lower())
    except Exception:
        return 0


def _busy_units():
    """Estimate of concurrent agents already working (from node-proc load)."""
    return _node_proc_count() // _PROCS_PER_AGENT


def recommended_cap(static_max=4):
    """Live-load recommended concurrency cap, clamped to [1, static_max]."""
    try:
        cores = _cpu_cores() or 4
        ceiling = max(1, cores - 2)          # leave headroom for the OS + main loop
        cap = max(1, ceiling - _busy_units())  # subtract agents already busy
        return max(1, min(cap, static_max))
    except Exception:
        return max(1, static_max)


_CONFIG = os.path.expanduser("~/.ai-instructions/parallelization.json")


def _load_config():
    """(static_max, mode) from the dedicated `parallelization.json` config file
    (front-end-drivable). Defaults to (4, 'live') if the file is absent/unreadable
    — live sizing is the speed-optimizing default."""
    try:
        p = json.load(open(_CONFIG, encoding="utf-8"))
        return int(p.get("maxConcurrentAgents", 4)), str(p.get("headroomMode", "live"))
    except Exception:
        return 4, "live"


def cap_now():
    """The concurrency cap to use right now, honoring the configured mode:
    'static' -> the configured max; 'live' -> the live machine-load recommendation."""
    static_max, mode = _load_config()
    return recommended_cap(static_max) if mode == "live" else max(1, static_max)


def main():
    ap = argparse.ArgumentParser(description="Recommend a concurrency cap (config-driven; --max forces live sizing).")
    ap.add_argument("--max", type=int, default=None, help="force live sizing against this ceiling (ignores config mode)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cap = recommended_cap(static_max=args.max) if args.max is not None else cap_now()
    if args.json:
        print(json.dumps({"cap": cap, "cores": _cpu_cores(),
                          "busy_units": _busy_units(), "static_max": args.max}))
    else:
        print(cap)


if __name__ == "__main__":
    main()
