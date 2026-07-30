#!/usr/bin/env python3
"""
orchestration_config.py — per-machine settings for the worktree tooling.

Everything machine-specific lives HERE, not baked into the tools, so the same
scripts work for anyone. The config file deliberately sits OUTSIDE
~/.ai-instructions/ because that whole directory is captured into the shared
config repo — a machine's own paths must never be published to the team.

    ~/.agent-orchestration/config.json

Nothing is auto-discovered by default. That is a safety decision, not laziness:
these tools push branches, delete worktrees and purge dependencies, and a home
directory is often itself a git repo. A tool that goes hunting for repositories
will eventually find one nobody meant to hand it.

Defaults for a NEW install are deliberately timid — report only, change nothing —
so a newcomer can watch it for a week before granting it the power to delete.
"""

import json
import os
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".agent-orchestration"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    # Where your git repositories live. No default guess — setup asks.
    "project_roots": [],
    # Directory names never treated as a project, even if they contain .git.
    "skip_dirs": [".worktrees", ".claude", ".remember", ".env-backups", "node_modules"],
    # "report"  = describe what would happen, change nothing (default for new installs)
    # "apply"   = actually push / clean / purge
    "mode": "report",
    # Days idle before each action becomes eligible.
    "purge_deps_days": 7,     # delete node_modules (worktree and work untouched)
    "autosave_days": 7,       # commit stray work to wip/<branch> and push
    "retire_days": 14,        # remove worktree; branch survives on origin
    # Branch names to treat as the integration branch, most preferred first.
    "base_branches": ["dev", "main", "master"],
    # Worktree paths matching these are never touched (substring match).
    "protected_paths": ["_scratch", "_baseline"],
    # Optional: email for the daily report. Empty = no email, everything else works.
    "notify_email": "",
}


def config_path():
    return CONFIG_FILE


def load():
    """Return the effective config. Missing file => safe defaults (report mode)."""
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_FILE.is_file():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"WARNING: could not read {CONFIG_FILE} ({e}); using safe defaults.",
              file=sys.stderr)
    # Environment override, mostly for CI and for testing against a scratch tree.
    env_roots = os.environ.get("AGENT_ORCHESTRATION_ROOTS")
    if env_roots:
        cfg["project_roots"] = [p for p in env_roots.split(os.pathsep) if p]
    return cfg


def save(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return CONFIG_FILE


def project_roots(cfg=None):
    cfg = cfg or load()
    return [Path(p) for p in cfg.get("project_roots", []) if p]


def find_repos(cfg=None):
    """Every git repository directly inside the configured roots.

    One level deep on purpose: a root holds projects, and descending further
    would sweep vendored checkouts and worktrees back into scope.
    """
    cfg = cfg or load()
    skip = set(cfg.get("skip_dirs", []))
    repos = []
    for root in project_roots(cfg):
        if not root.exists():
            print(f"WARNING: configured project root does not exist: {root}",
                  file=sys.stderr)
            continue
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name not in skip and (d / ".git").exists():
                repos.append(d)
    return repos


def is_protected(worktree_path, cfg=None):
    cfg = cfg or load()
    norm = str(worktree_path).replace("\\", "/")
    return any(tok in norm for tok in cfg.get("protected_paths", []))


def describe():
    """Human-readable summary — used by setup and by --show-config."""
    cfg = load()
    lines = [f"config file : {CONFIG_FILE}"
             + ("" if CONFIG_FILE.is_file() else "   (not created yet — using defaults)")]
    roots = cfg.get("project_roots") or []
    lines.append("project roots: " + (", ".join(roots) if roots
                                      else "NONE SET — tools will find no repositories"))
    lines.append(f"mode         : {cfg['mode']}"
                 + ("   (nothing will be changed)" if cfg["mode"] != "apply" else ""))
    lines.append(f"thresholds   : purge deps {cfg['purge_deps_days']}d, "
                 f"autosave {cfg['autosave_days']}d, retire {cfg['retire_days']}d")
    lines.append(f"notify email : {cfg.get('notify_email') or '(none — email disabled)'}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
