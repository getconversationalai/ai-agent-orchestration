#!/usr/bin/env python3
"""
worktree_sweeper.py — Make worktrees clean themselves up.

The problem this exists for: cleanup only ever happened when an agent remembered
to call it. Agents forget, sessions crash, parents compact before merging their
subagents' branches — so worktrees accumulated to 120 while WORKBOARD.json still
claimed March work was "active".

The insight that makes automatic cleanup safe: every cleanup refusal was
"branch has unpushed commits". Push every branch first and a worktree stops being
a graveyard and becomes a rebuildable cache — the work lives on origin forever,
so removing the directory costs nothing and is undone with one command.

Three layers, cheapest-risk first:

  1. PUSH    every branch with unpushed commits -> origin.
             Nothing can be lost after this runs. Never removes anything.
  2. REMOVE  worktrees whose work is genuinely in the base branch
             (fast-forward merged OR squash-merged, detected by content).
             This is "cleanup when done", finally automatic.
  3. RETIRE  worktrees that are pushed + clean + idle >= --idle-days.
             Directory and local branch go; the branch remains on origin.
             Restore with:  git worktree add <path> <branch>

INVARIANT: a worktree with uncommitted changes is never touched by any layer.
Enforced twice — here, and again inside worktree_cleanup.py.

Usage:
  py worktree_sweeper.py                 # dry run — prints the plan, changes nothing
  py worktree_sweeper.py --apply         # actually do it
  py worktree_sweeper.py --apply --no-retire      # layers 1+2 only
  py worktree_sweeper.py --repo /path/to/repo   # one repo
  py worktree_sweeper.py --idle-days 30
  py worktree_sweeper.py --here          # only the worktree you are standing in
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CLEANUP = Path.home() / ".ai-instructions" / "tools" / "worktree_cleanup.py"

# Machine-specific settings (project roots, thresholds, report-vs-apply) live in
# ~/.agent-orchestration/config.json so these scripts stay portable.
sys.path.insert(0, str(CLEANUP.parent))
try:
    import orchestration_config as ocfg
except Exception:
    ocfg = None

# Share ONE definition of "which uncommitted files are ignorable" with the cleanup
# script. Two copies would inevitably drift, and a disagreement here means either
# nothing ever gets cleaned or something real gets destroyed.
sys.path.insert(0, str(CLEANUP.parent))
try:
    from worktree_cleanup import is_ignorable_dirt, status_line_path
except Exception:  # pragma: no cover - fail closed: treat everything as real work
    def is_ignorable_dirt(path):
        return False

    def status_line_path(line):
        return line.strip()


def git(repo, *args, timeout=120):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def find_repos():
    """Repositories under the CONFIGURED roots. Never auto-discovered — see
    orchestration_config for why guessing here is dangerous."""
    if ocfg is None:
        return []
    repos = ocfg.find_repos()
    if not repos:
        print("No repositories found. Set your project roots with: "
              "py ~/.ai-instructions/tools/orchestration_setup.py",
              file=sys.stderr)
    return repos


def detect_base(repo):
    for base in ("dev", "main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}")[0] == 0:
            return base
    return "main"


def list_worktrees(repo):
    _, out, _ = git(repo, "worktree", "list", "--porcelain")
    entries, cur = [], {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {"path": line[9:].strip(), "branch": None}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "").strip()
    if cur:
        entries.append(cur)
    out_list = []
    for e in entries[1:]:  # entries[0] is the main working tree
        p = e["path"].replace("\\", "/").rstrip("/")
        if ocfg and ocfg.is_protected(p):
            continue  # scratch and baselines are deliberately permanent
        out_list.append(e)
    return out_list


def dirt(worktree_path):
    """Real uncommitted changes (settings drift ignored). Unknown => treat as dirty."""
    if not Path(worktree_path).is_dir():
        return []
    code, out, _ = git(worktree_path, "status", "--porcelain", timeout=90)
    if code != 0:
        return ["<git status failed — treated as dirty>"]
    keep = []
    for line in out.splitlines():
        if not line.strip():
            continue
        if is_ignorable_dirt(status_line_path(line)):
            continue
        keep.append(line.rstrip())
    return keep


def idle_days(repo, wt, branch):
    """Days since the last sign of life: newest of last commit and index mtime.

    The linked worktree's index file is touched by any git activity in that tree,
    so it catches work that was never committed. One stat call — cheap enough to
    run across a hundred worktrees.
    """
    stamps = []
    _, iso, _ = git(repo, "log", "-1", "--format=%cI", branch)
    if iso:
        try:
            stamps.append(datetime.fromisoformat(iso).astimezone(timezone.utc))
        except ValueError:
            pass
    name = Path(wt["path"]).name
    idx = Path(repo) / ".git" / "worktrees" / name / "index"
    if idx.exists():
        stamps.append(datetime.fromtimestamp(idx.stat().st_mtime, timezone.utc))
    if not stamps:
        return 9999
    return (datetime.now(timezone.utc) - max(stamps)).days


def content_in_base(repo, branch, base):
    """True if every line of this branch already exists in the base branch.

    Covers both fast-forward merges (ancestor) and squash merges, which rewrite
    commits so ancestry checks alone report a merged branch as unmerged.
    """
    ref = f"origin/{base}" if git(repo, "rev-parse", "--verify", "--quiet",
                                 f"refs/remotes/origin/{base}")[0] == 0 else base
    code, out, _ = git(repo, "diff", f"{ref}...{branch}", "--stat")
    if code != 0:
        return False
    return out.strip() == ""


def unpushed_count(repo, branch):
    """Commits on this branch not on its remote. -1 when there is no remote copy."""
    code, upstream, _ = git(repo, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
    if code == 0 and upstream:
        _, n, _ = git(repo, "rev-list", "--count", f"{upstream}..{branch}")
        return int(n) if n.isdigit() else 0
    code, _, _ = git(repo, "rev-parse", "--verify", "--quiet",
                     f"refs/remotes/origin/{branch}")
    if code == 0:
        _, n, _ = git(repo, "rev-list", "--count", f"origin/{branch}..{branch}")
        return int(n) if n.isdigit() else 0
    return -1  # never pushed


def has_remote(repo):
    code, out, _ = git(repo, "remote")
    return code == 0 and bool(out.strip())


def has_dependencies(worktree_path, max_depth=3):
    """True if this worktree carries a node_modules worth reclaiming.

    Searched rather than guessed from a fixed list of folder names: monorepo
    layouts differ per project, and a hardcoded list silently reclaims nothing
    for anyone whose repo is arranged differently.
    """
    base = Path(worktree_path)
    if not base.is_dir():
        return False
    root_depth = str(base).rstrip("\/").count(os.sep)
    for cur, dirs, _ in os.walk(base):
        if cur.count(os.sep) - root_depth >= max_depth:
            dirs[:] = []
            continue
        if "node_modules" in dirs:
            return True
        for skip in (".git", "dist", "build", ".next", ".turbo"):
            if skip in dirs:
                dirs.remove(skip)
    return False


def run_purge(repo, worktree_path):
    """Delete node_modules via the cleanup script's junction-safe path.

    Never reimplement this deletion here: npm workspaces put NTFS junctions inside
    node_modules pointing at the worktree's own source dirs, and any delete that
    follows them destroys real source.
    """
    try:
        r = subprocess.run(
            [sys.executable, str(CLEANUP), str(worktree_path), "--purge-deps-only"],
            capture_output=True, text=True, timeout=900, cwd=str(repo))
    except Exception as e:
        return False, str(e)
    if has_dependencies(worktree_path):
        tail = (r.stdout + r.stderr).strip().splitlines()
        return False, tail[-1] if tail else "node_modules still present"
    return True, ""


def run_autosave(repo, worktree_path):
    """Commit uncommitted work to wip/<branch> and push it.

    Delegates to wip_autosave.py, which refuses if anything looks like a
    credential — auto-committing a secret is not recoverable from shared history.
    """
    tool = CLEANUP.parent / "wip_autosave.py"
    try:
        r = subprocess.run([sys.executable, str(tool), str(worktree_path), "--apply"],
                           capture_output=True, text=True, timeout=600, cwd=str(repo))
    except Exception as e:
        return False, str(e)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        first = out.splitlines()[0] if out else f"exit {r.returncode}"
        return False, first
    return True, ""


def run_cleanup(repo, worktree_path, keep_branch=False, preserved_in_base=False):
    """Invoke the cleanup script FROM the owning repo.

    cwd matters: worktree_cleanup.py resolves the repository by walking up from
    its working directory. Run it from anywhere else (this sweeper runs from the
    user's home, which is itself a git repo) and it operates on the wrong
    repository — `git worktree remove` answers "is not a working tree", the
    script falls back to prune, and reports success while the directory survives.
    """
    cmd = [sys.executable, str(CLEANUP), str(worktree_path)]
    if keep_branch:
        cmd.append("--no-delete-branch")
    if preserved_in_base:
        # We verified `git diff base...branch` is empty, so the push gate is moot.
        cmd.append("--work-preserved-in-base")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                           cwd=str(repo))
    except Exception as e:
        return False, str(e)
    # Trust the filesystem, not the exit code.
    if Path(worktree_path).is_dir():
        tail = (r.stdout + r.stderr).strip().splitlines()
        detail = tail[-1] if tail else "no output"
        return False, f"directory still present after cleanup — {detail}"
    return True, ""


def sweep_repo(repo, args, log):
    base = detect_base(repo)
    git(repo, "fetch", "origin", base, timeout=180)
    worktrees = list_worktrees(repo)
    if not worktrees:
        return

    for wt in worktrees:
        path, branch = wt["path"], wt["branch"]
        if args.here and Path(path).resolve() != Path(args.here).resolve():
            continue
        label = f"{repo.name} :: {branch or '(detached)'}"

        # ---- Layer 0: reclaim disk without touching any work ----
        # node_modules is gitignored and fully regenerable, so this is safe even
        # on worktrees we will never remove (dirty ones, detached ones). Those are
        # permanent residents, which makes their dependencies pure waste.
        # list_worktrees() already excludes _scratch, which keeps deps on purpose.
        if not args.no_purge:
            age0 = idle_days(repo, wt, branch) if branch else 9999
            if age0 >= args.purge_days and has_dependencies(path):
                if args.apply:
                    ok, why = run_purge(repo, path)
                    log("PURGED " if ok else "FAILED ",
                        f"{label} — node_modules removed, idle {age0}d"
                        if ok else f"{label} — purge failed: {why}")
                else:
                    log("WOULD-PURGE", f"{label} — node_modules, idle {age0}d "
                                       f"(worktree and work untouched)")

        d = dirt(path)
        if d:
            age_d = idle_days(repo, wt, branch) if branch else 9999
            if args.autosave and branch and age_d >= args.autosave_days:
                if not args.apply:
                    log("WOULD-SAVE", f"{label} — {len(d)} change(s) -> wip/{branch}, idle {age_d}d")
                    continue
                ok, why = run_autosave(repo, path)
                if not ok:
                    log("SKIP   ", f"{label} — {len(d)} uncommitted; autosave declined: {why}")
                    continue
                log("SAVED  ", f"{label} — {len(d)} change(s) saved to wip/{branch} and pushed")
                # Re-read: the tree should now be clean and the branch is wip/<branch>.
                d = dirt(path)
                _, branch, _ = git(path, "rev-parse", "--abbrev-ref", "HEAD")
                label = f"{repo.name} :: {branch}"
            if d:
                log("SKIP   ", f"{label} — {len(d)} uncommitted change(s), never touched")
                continue
        if not branch:
            log("SKIP   ", f"{label} — detached HEAD, needs a human")
            continue

        # ---- Layer 2 first: if the work is already in base, it needs no backup ----
        # Checked before pushing so finished branches don't litter origin with
        # remote branches whose content main already has.
        if content_in_base(repo, branch, base):
            if args.apply:
                ok, why = run_cleanup(repo, path, preserved_in_base=True)
                log("REMOVED" if ok else "FAILED ",
                    f"{label} — merged into {base}" + ("" if ok else f" ({why})"))
            else:
                log("WOULD-REMOVE", f"{label} — content already in {base}")
            continue

        # ---- Layer 1: push, so nothing can ever be lost ----
        n_unpushed = unpushed_count(repo, branch)
        if n_unpushed != 0:
            what = ("never pushed" if n_unpushed < 0
                    else f"{n_unpushed} unpushed commit(s)")
            if args.apply:
                code, _, err = git(repo, "push", "-u", "origin", branch, timeout=300)
                if code == 0:
                    log("PUSHED ", f"{label} — {what} now safe on origin")
                else:
                    tail_err = err.splitlines()[-1] if err else "unknown error"
                    log("FAILED ", f"{label} — push failed: {tail_err}")
                    continue  # never retire work we could not back up
            else:
                log("WOULD-PUSH", f"{label} — {what}")

        # ---- Layer 3: retire long-idle work (safe: it lives on origin) ----
        if args.no_retire:
            continue
        if not has_remote(repo):
            # Retirement trades the local copy for the one on origin. No remote,
            # no trade — leave it alone.
            log("KEEP   ", f"{label} — idle but repo has no remote; nothing to fall back on")
            continue
        age = idle_days(repo, wt, branch)
        if age >= args.idle_days:
            if args.apply:
                ok, why = run_cleanup(repo, path, keep_branch=True)
                if ok:
                    log("RETIRED", f"{label} — idle {age}d, on origin; restore: "
                                   f"git -C {repo} worktree add <path> {branch}")
                else:
                    log("FAILED ", f"{label} — retire failed: {why}")
            else:
                log("WOULD-RETIRE", f"{label} — idle {age}d (branch kept on origin)")


def main():
    ap = argparse.ArgumentParser(
        description="Push, then clean up finished and long-idle worktrees.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually make changes. Without this it is a dry run.")
    ap.add_argument("--idle-days", type=int, default=14,
                    help="Retire pushed+clean worktrees idle this long (default 14).")
    ap.add_argument("--purge-days", type=int, default=7,
                    help="Purge node_modules from worktrees idle this long (default 7). "
                         "The worktree and all its work stay; only regenerable "
                         "dependencies are removed.")
    ap.add_argument("--autosave", action="store_true",
                    help="Commit long-idle uncommitted work to wip/<branch> and push it, "
                         "so abandoned worktrees stop being unbackupable permanent residents. "
                         "Refuses on anything that looks like a credential.")
    ap.add_argument("--autosave-days", type=int, default=7,
                    help="Only autosave worktrees idle at least this long (default 7).")
    ap.add_argument("--no-purge", action="store_true",
                    help="Never purge node_modules.")
    ap.add_argument("--no-retire", action="store_true",
                    help="Run layers 1-2 only; never retire idle worktrees.")
    ap.add_argument("--repo", default=None, help="Limit to one repo path.")
    ap.add_argument("--here", default=None, nargs="?", const=".",
                    help="Only consider the worktree containing this path "
                         "(bare --here means the current directory). Used by the "
                         "session-end hook.")
    ap.add_argument("--quiet-if-clean", action="store_true",
                    help="Print nothing when there is nothing to report.")
    args = ap.parse_args()

    lines = []

    def log(tag, msg):
        lines.append(f"  {tag.strip():<12} {msg}")

    if args.here:
        # Resolve to the worktree ROOT — the hook's cwd is often a subdirectory,
        # which would never match a worktree path.
        code, top, _ = git(args.here, "rev-parse", "--show-toplevel")
        if code != 0:
            if not args.quiet_if_clean:
                print("Not inside a git worktree — nothing to sweep.")
            return
        args.here = top
        # Scope to that worktree's own repo so we don't walk all of C:/dev.
        _, common, _ = git(top, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common:
            args.repo = args.repo or str(Path(common).parent)

    repos = [Path(args.repo)] if args.repo else find_repos()
    for repo in repos:
        try:
            sweep_repo(repo, args, log)
        except Exception as e:
            log("ERROR  ", f"{repo.name} — {e}")

    if not lines:
        if not args.quiet_if_clean:
            print("Worktree sweep: nothing to do — all worktrees are clean, "
                  "pushed, and either active or already gone.")
        return

    header = ("Worktree sweep — APPLIED" if args.apply
              else "Worktree sweep — DRY RUN (re-run with --apply to make these changes)")
    print(header)
    print("\n".join(lines))
    if not args.apply:
        print("\n  Nothing above was changed.")


if __name__ == "__main__":
    main()
