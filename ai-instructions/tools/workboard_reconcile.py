#!/usr/bin/env python3
"""
workboard_reconcile.py — Keep each project's WORKBOARD.json current from git.

Write-side twin of workboard_staleness_check.py (which only reports). Registers
in-flight branches missing from the board, completes entries whose branch merged
or vanished, and bumps entries whose branch has new commits. Board metadata only —
never touches worktrees, branches, or history. Applies changes exclusively via
workboard.py so that tool stays the single writer.

Usage:
  py workboard_reconcile.py                 # preview (dry-run) across all repos
  py workboard_reconcile.py --apply         # write IF armed, else preview + instructions
  py workboard_reconcile.py --arm           # create sentinel, then write (first-time enable)
  py workboard_reconcile.py --dry-run       # never writes
  py workboard_reconcile.py --summary       # 1-3 lines (SessionStart)
  py workboard_reconcile.py --json          # machine-readable plan
  py workboard_reconcile.py --repo <path>   # limit to one repo
  py workboard_reconcile.py --days 14       # registration activity window
  py workboard_reconcile.py --fast          # skip per-repo git fetch
"""
import sys, json, subprocess, argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workboard_staleness_check import run_git, find_repos, detect_base_branch, list_worktrees

WORKBOARD_PY = Path(__file__).resolve().parent / "workboard.py"
SENTINEL = Path.home() / ".stale-work" / "reconcile-initialized"

NOISE_PREFIXES = ("wip/", "claude/", "dependabot/", "renovate/")

def is_noise(name: str, base_branches: list[str]) -> bool:
    if name in base_branches:
        return True
    if name in ("HEAD", "origin/HEAD"):
        return True
    return any(name.startswith(p) for p in NOISE_PREFIXES)


def slug(branch: str) -> str:
    return branch.replace("/", "-").replace(" ", "-").lower()


def derive_feature(branch: str) -> str:
    return branch.replace("/", " ").replace("-", " ").replace("_", " ").strip()


def parse_iso(ts: str):
    if not ts:
        return None
    ts = ts.strip().replace("Z", "")
    if "+" in ts[10:]:
        ts = ts[:ts.index("+", 10)]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def read_board(repo: Path) -> dict:
    p = Path(repo) / "WORKBOARD.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"active": [], "completed": []}
    if not isinstance(data, dict):
        return {"active": [], "completed": []}
    data.setdefault("active", [])
    data.setdefault("completed", [])
    return data


def to_local_naive(iso: str):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def _branch_names(repo: Path) -> list[str]:
    out = run_git(repo, "for-each-ref", "--format=%(refname:short)",
                  "refs/heads", "refs/remotes/origin")
    names = []
    for line in out.splitlines():
        n = line.strip()
        if not n:
            continue
        if n.startswith("origin/"):
            n = n[len("origin/"):]
        names.append(n)
    return names


def _resolve_ref(repo: Path, name: str) -> str:
    """Resolve a bare branch name to an actual git ref.
    Prefer refs/heads/<name>, else fall back to refs/remotes/origin/<name>."""
    if subprocess.run(["git", "-C", str(repo), "rev-parse", f"refs/heads/{name}"],
                      capture_output=True).returncode == 0:
        return f"refs/heads/{name}"
    return f"refs/remotes/origin/{name}"


def gather_branches(repo: Path, base: str, base_branches):
    repo = Path(repo)
    all_names = set(_branch_names(repo))
    wt_by_branch = {w.get("branch"): w.get("path", "")
                    for w in list_worktrees(repo) if w.get("branch")}
    candidates = []
    for name in sorted(all_names):
        if is_noise(name, base_branches):
            continue
        # Resolve branch name to an actual ref (prefer local, fall back to remote)
        ref = _resolve_ref(repo, name)
        last_iso = run_git(repo, "log", "-1", "--format=%cI", ref)
        last = to_local_naive(last_iso)
        if last is None:
            continue
        first_iso = run_git(repo, "log", "--reverse", "--format=%cI",
                            f"{base}..{ref}").splitlines()
        first = to_local_naive(first_iso[0]) if first_iso else last
        merged = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", ref, base],
            capture_output=True).returncode == 0
        diff = run_git(repo, "diff", "--name-only", f"{base}...{ref}")
        files = [f for f in diff.splitlines() if f.strip()]
        candidates.append({
            "name": name, "last_commit": last, "first_commit": first,
            "merged": merged, "has_source_diff": bool(files),
            "files": files, "worktree": wt_by_branch.get(name, ""),
        })
    return candidates, all_names


def plan_reconciliation(board, candidates, all_branch_names, now, window_days):
    actions = []
    active = board.get("active", [])
    active_branches = {e.get("branch", "") for e in active if e.get("branch")}
    window = timedelta(days=window_days)
    cand_by_name = {c["name"]: c for c in candidates}

    # REGISTER: in-flight, recent, unmerged, has a diff, not already on the board
    for c in candidates:
        if c["merged"] or not c["has_source_diff"]:
            continue
        if (now - c["last_commit"]) > window:
            continue
        if c["name"] in active_branches:
            continue
        actions.append({
            "kind": "register", "branch": c["name"],
            "feature": derive_feature(c["name"]), "id": slug(c["name"]),
            "files": c["files"], "worktree": c.get("worktree", ""),
            "started": c["first_commit"].strftime("%Y-%m-%dT%H:%M"),
            "updated": c["last_commit"].strftime("%Y-%m-%dT%H:%M"),
            "reason": "in-flight branch not on board",
        })

    # COMPLETE / BUMP over existing active entries
    for e in active:
        br = e.get("branch", "")
        if not br:
            continue
        ref = e.get("id") or e.get("feature", "")
        if br not in all_branch_names:
            actions.append({"kind": "complete", "branch": br, "feature": ref,
                            "merged": "no", "reason": "branch gone"})
            continue
        c = cand_by_name.get(br)
        if c and c["merged"]:
            actions.append({"kind": "complete", "branch": br, "feature": ref,
                            "merged": "yes", "reason": "branch merged into base"})
            continue
        if c and not c["merged"]:
            eu = parse_iso(e.get("updated", "") or e.get("started", ""))
            if eu and c["last_commit"] > eu:
                actions.append({"kind": "bump", "branch": br, "feature": ref,
                                "files": c["files"], "reason": "new commits since last update"})
    return actions


def build_command(action: dict) -> list[str]:
    """Build a workboard.py argv for one action (without the python exe prefix)."""
    k = action["kind"]
    if k == "register":
        return ["register",
                "--feature", action["feature"], "--tool", "reconcile",
                "--branch", action["branch"], "--worktree", action.get("worktree", ""),
                "--files", ",".join(action.get("files", [])),
                "--id", action.get("id", "")]
    if k == "complete":
        return ["complete", "--feature", action["feature"],
                "--merged", action.get("merged", "no"), "--no-auto-cleanup"]
    if k == "bump":
        return ["update-files", "--feature", action["feature"],
                "--files", ",".join(action.get("files", []))]
    raise ValueError(f"unknown action kind: {k}")


def should_write(apply: bool, arm: bool, sentinel_exists: bool) -> bool:
    """Determine whether to write to the board based on flags and sentinel.

    Returns True only when (apply or arm) and (sentinel_exists or arm).
    This enforces "first run is preview-only until armed."
    """
    return bool(apply or arm) and (sentinel_exists or arm)


def apply_action(repo: Path, action: dict) -> bool:
    """Run workboard.py with the command built from the action.

    Returns True on success (returncode 0), False on failure.
    Prints warning to stderr on failure.
    """
    cmd = [sys.executable, str(WORKBOARD_PY), *build_command(action)]
    result = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    if result.returncode != 0:
        first_line = (result.stderr or "").strip().splitlines()[0] if (result.stderr or "").strip() else ""
        print(f"[reconcile] WARN: workboard {action['kind']} for {action.get('branch','?')} failed (exit {result.returncode}): {first_line}", file=sys.stderr)
        return False
    return True


def _base_branches():
    try:
        import orchestration_config as ocfg
        cfg = ocfg.load_config()
        return cfg.get("base_branches", ["dev", "main", "master"])
    except Exception:
        return ["dev", "main", "master"]


def reconcile_repo(repo: Path, base_branches: list, now: datetime,
                    window_days: int, fetch: bool) -> list:
    """Compute the action plan for one repo (no writes)."""
    repo = Path(repo)
    if fetch:
        run_git(repo, "fetch", "origin", "--prune", timeout=60)
    # NOTE (--fast tradeoff): when fetch=False we skip this fetch+prune, so a branch
    # deleted on origin whose stale origin/<name> ref still lingers locally may not
    # be detected as "gone" until the next full (non-fast) run — e.g. the daily 6am
    # job, which fetches with --prune and clears out dangling remote-tracking refs.
    base = detect_base_branch(repo)
    candidates, all_names = gather_branches(repo, base, base_branches)
    board = read_board(repo)
    return plan_reconciliation(board, candidates, all_names, now, window_days)


def format_summary(results: dict) -> str:
    """One-line-per-repo human summary, e.g. for SessionStart hooks."""
    parts = []
    for name, actions in results.items():
        if not actions:
            continue
        c = {"register": 0, "complete": 0, "bump": 0}
        for a in actions:
            c[a["kind"]] = c.get(a["kind"], 0) + 1
        parts.append(f"{name}: +{c['register']} registered, "
                     f"{c['complete']} completed, {c['bump']} bumped")
    return "  ".join(parts) if parts else "workboards current with git"


def summary_status(write: bool, applied: int, failed: int, unarmed: bool) -> str:
    """Write-status suffix for --summary/--json output.

    Makes it unambiguous whether a run actually wrote anything: PREVIEW when
    blocked only for lack of arming, an "(applied N[, M FAILED])" tag when
    writes were attempted, or empty when no writes were requested at all.
    """
    if unarmed:
        return " (PREVIEW — not armed; run with --arm once to enable writes)"
    if write:
        s = f" (applied {applied}"
        if failed:
            s += f", {failed} FAILED"
        return s + ")"
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile WORKBOARD.json from git.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--arm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fast", action="store_true", help="skip git fetch")
    ap.add_argument("--repo", default="", help="limit to one repo path")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args(argv)

    write = should_write(args.apply, args.arm, SENTINEL.exists()) and not args.dry_run
    unarmed = args.apply and not write and not args.dry_run and not SENTINEL.exists()
    if args.arm and not args.dry_run and not SENTINEL.exists():
        SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        SENTINEL.write_text("armed\n", encoding="utf-8")

    bases = _base_branches()
    now = datetime.now()
    repos = [Path(args.repo)] if args.repo else find_repos()

    results, applied, failed = {}, 0, 0
    for repo in repos:
        plan = reconcile_repo(repo, bases, now, args.days, fetch=not args.fast)
        results[Path(repo).name] = plan
        if write:
            for action in plan:
                ok = apply_action(repo, action)
                if ok:
                    applied += 1
                else:
                    failed += 1

    if args.json:
        print(json.dumps({"preview": (not write), "applied": applied, "failed": failed,
                          "repos": results}, default=str))
    elif args.summary:
        print(format_summary(results) + summary_status(write, applied, failed, unarmed))
    else:
        for name, plan in results.items():
            if not plan:
                continue
            print(f"\n{name}:")
            for a in plan:
                print(f"  {a['kind'].upper():9} {a['branch']:42} {a['reason']}")
        total = sum(len(v) for v in results.values())
        if write:
            verb = f"applied {applied}"
            if failed:
                verb += f", {failed} FAILED"
        else:
            verb = f"would change {total} (preview)"
        print(f"\n{verb}.")
        if unarmed:
            print("First run is preview-only. Review above, then re-run with --arm to enable writes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
