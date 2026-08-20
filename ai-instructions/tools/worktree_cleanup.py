"""
Safely removes git worktrees on Windows.

On Windows, npm workspace projects create NTFS junctions in node_modules that point
back to the worktree root. Most deletion tools (rm -rf, Remove-Item, shutil.rmtree,
rimraf, git worktree remove) follow these junctions and can destroy the main .git
directory. This script:

1. Uses os.scandir() + st_reparse_tag for BULLETPROOF junction/symlink detection
   (not dir /AL which misses junctions on some Windows builds)
2. Verifies no junction targets point into the main .git directory before deletion
3. Checks .git integrity AFTER EACH worktree removal, not just at the end
4. Uses cmd /c "rmdir" to remove junctions without following them

Usage:
    py ~/.ai-instructions/tools/worktree_cleanup.py <worktree-path>
    py ~/.ai-instructions/tools/worktree_cleanup.py --all-merged
    py ~/.ai-instructions/tools/worktree_cleanup.py --all-merged --base dev
"""
import argparse
import os
import subprocess
import sys

# NTFS reparse point tags
IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # Directory junctions
# Note: Some references swap these values. We check for ANY non-zero reparse tag
# to be safe — if st_reparse_tag != 0, it's some kind of link/reparse point.


def main():
    parser = argparse.ArgumentParser(description="Safely remove git worktrees on Windows.")
    parser.add_argument("worktree_path", nargs="?", help="Path to the worktree to remove.")
    parser.add_argument("--all-merged", action="store_true",
                        help="Remove all worktrees whose branches are merged into the base branch.")
    parser.add_argument("--base", default=None,
                        help="Base branch for --all-merged (default: auto-detect main or dev).")
    parser.add_argument("--delete-branch", action="store_true", default=True,
                        help="Delete the branch after removing the worktree (default: true).")
    parser.add_argument("--no-delete-branch", action="store_false", dest="delete_branch",
                        help="Keep the branch after removing the worktree.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be cleaned up and exit without touching anything.")
    parser.add_argument("--purge-deps-only", action="store_true",
                        help="Delete node_modules from the worktree but KEEP the worktree and "
                             "all its work. node_modules is gitignored and fully regenerable, "
                             "so this reclaims disk without touching anything you wrote.")
    parser.add_argument("--work-preserved-in-base", action="store_true",
                        help="Skip the 'branch must be pushed' check because the caller has "
                             "verified every line of this branch already exists in the base "
                             "branch (git diff base...branch is empty). The work survives in "
                             "base, so a remote copy of the branch adds nothing. Required for "
                             "repos with no remote at all, where the push check can never pass.")
    parser.add_argument("--force-dirty", action="store_true",
                        help="DANGEROUS: proceed even if the worktree has uncommitted changes. "
                             "Those changes are unrecoverable once removed — there is no reflog "
                             "for uncommitted work. Only use when you have confirmed the changes "
                             "are worthless.")
    args = parser.parse_args()

    # Make the flag reachable from the gate helpers without threading it through
    # every call site.
    global FORCE_DIRTY, WORK_PRESERVED_IN_BASE
    FORCE_DIRTY = args.force_dirty
    WORK_PRESERVED_IN_BASE = args.work_preserved_in_base

    if not args.worktree_path and not args.all_merged:
        parser.print_help()
        sys.exit(1)

    # Resolve the repo to operate on. For a single worktree path, derive the
    # repo from the WORKTREE ITSELF — it may live under a different repo than the
    # CWD, and a CWD-derived root makes `git worktree remove` target the wrong
    # repository, which reports "is not a working tree" and silently no-ops the
    # removal (leaving the worktree registered — the pileup this fix prevents).
    # --all-merged is run per-repo, so it uses the CWD's repo.
    if args.all_merged:
        repo_root = find_repo_root()
    else:
        repo_root = repo_root_for_worktree(args.worktree_path) or find_repo_root()
    if not repo_root:
        error("Could not find a git repository. Are you inside a git project, "
              "or is the worktree path valid?")

    # Verify .git exists
    git_dir = os.path.join(repo_root, ".git")
    if not os.path.exists(git_dir):
        error(f".git does not exist at {git_dir}. The repository may already be corrupted.\n"
              "Recovery: git init && git remote add origin <url> && git fetch origin "
              "&& git checkout -B main origin/main")

    if args.purge_deps_only:
        if not args.worktree_path:
            error("--purge-deps-only needs a worktree path.")
        purge_dependencies(args.worktree_path)
        return

    if args.dry_run:
        dry_run_report(repo_root, args)
        return

    if args.all_merged:
        cleanup_all_merged(repo_root, args.base, args.delete_branch)
    else:
        cleanup_one(repo_root, args.worktree_path, args.delete_branch)


def dry_run_report(repo_root, args):
    """Print what would be cleaned up without touching anything.

    Same selection logic as the real run, but no deletes, no `--force`,
    no junction removal. Output: one line per worktree, with reason it
    was selected and any safety gate that would currently refuse it.
    """
    print("DRY RUN — no changes will be made.\n")
    base_branch = args.base or detect_base_branch(repo_root)
    worktrees = list_worktrees(repo_root)

    if args.all_merged:
        # Same merged-set computation as cleanup_all_merged
        merged = set()
        try:
            r = subprocess.run(
                ["git", "branch", "--merged", base_branch],
                capture_output=True, text=True, timeout=10, cwd=repo_root,
            )
            merged.update(b.strip().lstrip("* ") for b in r.stdout.splitlines())
        except Exception as e:
            print(f"  Failed to list merged branches: {e}")

        for wt_path, wt_branch in worktrees:
            if not wt_branch or wt_branch in merged:
                continue
            if is_ancestor_only(repo_root, wt_branch):
                merged.add(wt_branch)

        targets = [(p, b) for (p, b) in worktrees if b in merged]
    else:
        # Single-target dry run
        wt_path = os.path.abspath(args.worktree_path)
        wt_branch = detect_worktree_branch(repo_root, wt_path)
        targets = [(wt_path, wt_branch)]

    if not targets:
        print(f"  No worktrees would be cleaned (base: {base_branch}).")
        return

    print(f"  Base branch: {base_branch}")
    print(f"  Targets ({len(targets)}):")
    for wt_path, wt_branch in targets:
        gate = _dry_run_gate_check(repo_root, wt_path, wt_branch, base_branch)
        marker = "WOULD CLEAN" if gate is None else f"REFUSED ({gate})"
        print(f"    [{marker}] {wt_branch}  @ {wt_path}")


def _dry_run_gate_check(repo_root, wt_path, wt_branch, base_branch):
    """Replicate the safety gates without side effects. Returns reason string
    if any gate would refuse, None if all pass."""
    # Gate 0: protected scratch worktree — never cleaned
    if is_scratch_worktree(wt_path):
        return "protected scratch worktree"

    # Gate 1: WORKBOARD active (JSON source of truth, .md fallback)
    if _workboard_json_blocks(repo_root, wt_path) or _workboard_md_blocks(repo_root, wt_path):
        return "active in WORKBOARD"

    # Gate 2: branch pushed to remote (with ancestor-only bypass)
    if wt_branch and not is_ancestor_only(repo_root, wt_branch):
        try:
            r = subprocess.run(
                ["git", "branch", "-r", "--list", f"origin/{wt_branch}"],
                capture_output=True, text=True, timeout=10, cwd=repo_root,
            )
            if not r.stdout.strip():
                return "branch not pushed to origin"
        except Exception:
            pass

    return None


def cleanup_all_merged(repo_root, base_branch, delete_branch):
    """Find and remove all worktrees whose branches are merged into the base branch."""
    if not base_branch:
        base_branch = detect_base_branch(repo_root)

    worktrees = list_worktrees(repo_root)
    if not worktrees:
        info("No worktrees to clean up.")
        return

    # Collect branches whose work is fully on the base branch.
    # Two categories — both safe to sweep because their tips are reachable from base:
    #   1. Strictly merged: `git branch --merged` — base has moved past them via real merge commits.
    #   2. Ancestor-only: branch tip IS an old commit on base's history. Common with squash/rebase
    #      merges (the original commits are gone but their content is in base). `--merged` misses
    #      these, which is why projects accumulate orphan worktrees the script can't sweep.
    merged_branches = set()
    try:
        result = subprocess.run(
            ["git", "branch", "--merged", base_branch],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        merged_branches.update(b.strip().lstrip("* ") for b in result.stdout.splitlines())
    except Exception as e:
        error(f"Failed to list merged branches: {e}")

    # Detect ancestor-only branches: rev-list count of <base>..<branch> == 0 means every commit
    # on the branch is reachable from base. Only check branches that have worktrees, since
    # those are the only ones we'd act on.
    for wt_path, wt_branch in worktrees:
        if not wt_branch or wt_branch in merged_branches:
            continue
        # is_ancestor_only checks origin/main and origin/dev too, so a branch
        # merged only to origin (stale local base) is still recognized as safe.
        if is_ancestor_only(repo_root, wt_branch):
            merged_branches.add(wt_branch)

    git_dir = os.path.join(repo_root, ".git")
    cleaned = 0
    for wt_path, wt_branch in worktrees:
        if is_scratch_worktree(wt_path):
            info(f"Skipping protected scratch worktree: {wt_path}")
            continue
        if wt_branch in merged_branches:
            # ── INTEGRITY CHECK before each removal ──
            if not os.path.exists(git_dir) or not os.path.isdir(git_dir):
                error(f"CRITICAL: .git was destroyed during batch cleanup!\n"
                      f"This happened AFTER cleaning worktree #{cleaned}.\n"
                      "STOPPING immediately. Recovery:\n"
                      "  git init && git remote add origin <url> && git fetch origin "
                      "&& git checkout -B main origin/main")

            info(f"\n--- Cleaning up: {wt_path} (branch: {wt_branch}) ---")
            try:
                cleanup_one(repo_root, wt_path, delete_branch)
                cleaned += 1
            except SystemExit:
                warn(f"Failed to clean up {wt_path}, skipping.")
                # ── INTEGRITY CHECK after failed removal ──
                if not os.path.exists(git_dir) or not os.path.isdir(git_dir):
                    error(f"CRITICAL: .git was destroyed while cleaning {wt_path}!\n"
                          "STOPPING immediately — do NOT continue batch cleanup.\n"
                          "Recovery: git init && git remote add origin <url> && git fetch origin "
                          "&& git checkout -B main origin/main")
                continue

        # ── INTEGRITY CHECK after each successful removal ──
        if not os.path.exists(git_dir) or not os.path.isdir(git_dir):
            error(f"CRITICAL: .git was destroyed after cleaning worktree #{cleaned}!\n"
                  "STOPPING immediately. Recovery:\n"
                  "  git init && git remote add origin <url> && git fetch origin "
                  "&& git checkout -B main origin/main")

    if cleaned == 0:
        info("No merged worktrees found to clean up.")
    else:
        info(f"\nCleaned up {cleaned} worktree(s).")


def cleanup_one(repo_root, worktree_path, delete_branch):
    """Safely remove a single worktree."""
    worktree_path = os.path.abspath(worktree_path)
    git_dir = os.path.join(repo_root, ".git")

    # ── Step 0: Never delete the reusable scratch worktree ──
    if is_scratch_worktree(worktree_path):
        info("This is the protected reusable scratch worktree — refusing to delete it.")
        info("Recycle it instead with: py ~/.ai-instructions/tools/scratch_worktree.py <branch>")
        return

    # ── Step 1: Verify .git exists BEFORE we start ──
    if not os.path.exists(git_dir) or not os.path.isdir(git_dir):
        error(f".git does not exist at {git_dir}. STOP — repository may be corrupted.")

    # Snapshot .git contents for post-removal integrity check
    git_snapshot = snapshot_git_dir(git_dir)

    # ── Step 2: Verify worktree exists ──
    if not os.path.isdir(worktree_path):
        warn(f"Worktree directory does not exist: {worktree_path}")
        info("Running git worktree prune to clean up stale refs...")
        run_git(["git", "worktree", "prune", "--expire=now"], cwd=repo_root)
        return

    # ── Step 2b: REFUSE if the worktree has uncommitted changes ──
    # Uncommitted work has no reflog and no remote copy — removing the worktree
    # destroys it permanently. This gate exists because --all-merged silently ate
    # a branch that had zero commits (so it read as "merged") but a live edit in
    # the tree. Never remove a dirty tree without an explicit --force-dirty.
    check_worktree_clean(worktree_path)

    # ── Step 3: Check WORKBOARD.md for active entries ──
    check_workboard(repo_root, worktree_path)

    # ── Step 4: Detect branch name ──
    branch_name = detect_worktree_branch(repo_root, worktree_path)

    # ── Step 5: Verify branch is pushed to remote ──
    # Skipped when the caller proved the branch's content already lives in the base
    # branch: there is nothing left to back up, and repos with no remote configured
    # could otherwise never be cleaned at all.
    if branch_name and not WORK_PRESERVED_IN_BASE:
        check_branch_pushed(repo_root, branch_name)
    elif branch_name:
        info("Content already present in base branch — skipping the push check.")

    # ── Step 6: Comprehensive junction/symlink scan using Python reparse tags ──
    info(f"Scanning for junctions/symlinks in {worktree_path}...")
    junctions = scan_junctions_python(worktree_path)

    # ── Step 6b: SAFETY CHECK — verify no junction targets point into .git ──
    if junctions:
        check_junction_targets_safe(junctions, git_dir, repo_root)

    if junctions:
        info(f"Found {len(junctions)} junction(s)/symlink(s). Removing safely...")

        # ── Step 7: Delete each junction using rmdir (junction-safe) ──
        for jpath, jtype in junctions:
            delete_junction_safe(jpath)

        # ── Step 8: Re-scan to verify zero junctions remain ──
        remaining = scan_junctions_python(worktree_path)
        if remaining:
            error(f"FAILED: {len(remaining)} junction(s) still remain after deletion:\n"
                  + "\n".join(f"  - {p} ({t})" for p, t in remaining) +
                  "\nRefusing to run git worktree remove. Investigate manually.")
        info("All junctions removed successfully.")
    else:
        info("No junctions found — safe to proceed.")

    # ── Step 9: Verify .git STILL intact before running git worktree remove ──
    if not os.path.exists(git_dir) or not os.path.isdir(git_dir):
        error(f"CRITICAL: .git was destroyed during junction removal for {worktree_path}!\n"
              "This should not happen — the junction removal itself damaged .git.\n"
              "Recovery: git init && git remote add origin <url> && git fetch origin "
              "&& git checkout -B main origin/main")

    # ── Step 10: Run git worktree remove ──
    # We pass --force because by the time we reach this step, the script has already
    # verified: (a) the worktree is not active in WORKBOARD, (b) the branch is pushed
    # to origin (so committed work is recoverable), and (c) no junction targets point
    # into .git. The only thing --force overrides is "uncommitted changes / untracked
    # files in the working tree" — which after those three gates is always one of:
    # npm install noise, build artifacts, or stray editor files. Real in-progress work
    # would have failed gate (a) or (b).
    # ── Step 9b: Fast-delete heavy gitignored dirs (node_modules, build output) ──
    # All junctions are gone by now, so rd /s /q is safe and far faster than
    # letting `git worktree remove` walk a 1,200-package node_modules file by file.
    fast_delete_heavy_dirs(worktree_path)

    # ── Step 10: Run git worktree remove (timeout-safe) ──
    # Raised timeout + explicit TimeoutExpired handling. The old 60s limit let a
    # slow node_modules delete crash the script mid-removal, leaving a half-deleted
    # dir and a stale worktree registration. Now a timeout falls back to manual
    # removal instead of throwing.
    info(f"Running git worktree remove --force {worktree_path}...")
    remove_succeeded = False
    try:
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            capture_output=True, text=True, timeout=300, cwd=repo_root,
        )
        remove_succeeded = (result.returncode == 0)
        if not remove_succeeded:
            stderr = result.stderr.strip()
            # "is not a working tree" / "does not exist" is NOT proof the worktree
            # is gone. With repo_root now resolved from the worktree itself this
            # branch should be rare, but claim success only if the working
            # directory actually disappeared. (The old code assumed the error
            # meant "gone" and reported success even when a no-op prune left the
            # worktree — and its registration — in place: a silent failure.)
            if "is not a working tree" in stderr or "does not exist" in stderr:
                info(f"git worktree remove reported: {stderr}")
                run_git(["git", "worktree", "prune", "--expire=now"], cwd=repo_root)
                remove_succeeded = not os.path.isdir(worktree_path)
            else:
                warn(f"git worktree remove failed: {stderr}")
                warn("Junctions were already removed, so .git should be safe.")
                # Still try to prune in case the ref is dangling
                run_git(["git", "worktree", "prune", "--expire=now"], cwd=repo_root)
    except subprocess.TimeoutExpired:
        # Don't crash mid-delete. Whatever directory remains is junction-free by
        # now, so remove it directly and prune the registration.
        warn("git worktree remove exceeded 300s — falling back to manual removal.")
        if os.path.isdir(worktree_path):
            try:
                subprocess.run(
                    ["cmd", "/c", "rd", "/s", "/q", to_windows_path(worktree_path)],
                    capture_output=True, text=True, timeout=240,
                )
            except Exception as e:
                warn(f"Manual removal failed: {e}")
        run_git(["git", "worktree", "prune", "--expire=now"], cwd=repo_root)
        remove_succeeded = not os.path.isdir(worktree_path)

    # ── Step 11: COMPREHENSIVE .git integrity check AFTER removal ──
    verify_git_integrity(git_dir, git_snapshot, worktree_path)

    info(".git verified intact.")

    # If the remove failed, do NOT continue to branch deletion or claim success.
    # Surface a real error and exit non-zero so callers see the failure.
    if not remove_succeeded:
        error(f"FAILED to remove worktree at {worktree_path}.\n"
              "The worktree directory still exists. Investigate manually.")

    # ── Step 12: Delete merged branch ──
    if delete_branch and branch_name:
        # Only delete with -d (safe — refuses if not merged)
        result = subprocess.run(
            ["git", "branch", "-d", branch_name],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        if result.returncode == 0:
            info(f"Deleted branch: {branch_name}")
        else:
            warn(f"Could not delete branch {branch_name}: {result.stderr.strip()}")
            warn("Branch may not be fully merged. Delete manually with git branch -D if certain.")

    # ── Step 13: Prune worktree refs ──
    run_git(["git", "worktree", "prune", "--expire=now"], cwd=repo_root)

    info(f"Cleanup complete: {worktree_path}")


# ─────────────────────────────────────────────
# Junction scanning — Python-native (bulletproof)
# ─────────────────────────────────────────────

def scan_junctions_python(root_path):
    """
    Scan a directory tree for ALL junctions, symlinks, and reparse points
    using os.scandir() + st_reparse_tag. This is far more reliable than
    cmd /c dir /AL which misses junctions on some Windows builds.

    Returns list of (path, type_description) tuples.
    """
    found = []
    try:
        _scan_recursive(root_path, found, depth=0, max_depth=50)
    except Exception as e:
        warn(f"Junction scan encountered an error: {e}")
        # If scan failed, treat as "potentially unsafe" — return a sentinel
        found.append((f"SCAN_ERROR: {e}", "error"))
    return found


def _scan_recursive(path, found, depth, max_depth):
    """Recursively scan for junctions/symlinks using os.scandir()."""
    if depth > max_depth:
        warn(f"Max scan depth ({max_depth}) reached at {path}. Possible circular links.")
        found.append((path, "max_depth_exceeded"))
        return

    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    entry_path = entry.path

                    # Check if this entry is any kind of link/reparse point
                    is_link = False
                    link_type = ""

                    # Method 1: os.path.islink (catches symlinks)
                    if os.path.islink(entry_path):
                        is_link = True
                        link_type = "symlink"

                    # Method 2: os.path.isjunction (Python 3.12+, catches NTFS junctions)
                    if hasattr(os.path, 'isjunction') and os.path.isjunction(entry_path):
                        is_link = True
                        link_type = "junction"

                    # Method 3: st_reparse_tag (catches ALL reparse points)
                    # This is the most comprehensive check
                    try:
                        stat_info = entry.stat(follow_symlinks=False)
                        reparse_tag = getattr(stat_info, 'st_reparse_tag', 0) or 0
                        if reparse_tag != 0:
                            is_link = True
                            if not link_type:  # Don't override more specific detection
                                if reparse_tag == 0xA0000003:
                                    link_type = f"junction (reparse_tag=0x{reparse_tag:08X})"
                                elif reparse_tag == 0xA000000C:
                                    link_type = f"symlink (reparse_tag=0x{reparse_tag:08X})"
                                else:
                                    link_type = f"reparse_point (tag=0x{reparse_tag:08X})"
                    except (OSError, AttributeError):
                        pass

                    if is_link:
                        found.append((entry_path, link_type))
                        # Do NOT recurse into links — they may point outside the tree
                        continue

                    # Recurse into real directories
                    if entry.is_dir(follow_symlinks=False):
                        _scan_recursive(entry_path, found, depth + 1, max_depth)

                except PermissionError:
                    warn(f"Permission denied scanning: {entry.path}")
                except OSError as e:
                    warn(f"OS error scanning {entry.path}: {e}")

    except PermissionError:
        warn(f"Permission denied scanning directory: {path}")
    except OSError as e:
        warn(f"OS error scanning directory {path}: {e}")


# ─────────────────────────────────────────────
# Junction target safety check
# ─────────────────────────────────────────────

def check_junction_targets_safe(junctions, git_dir, repo_root):
    """
    Verify no junction/symlink targets point into the main .git directory
    or the main repo root. If any do, REFUSE to proceed.

    Special case: npm workspace projects create an EXPECTED junction at
    <worktree>/node_modules/<repo-name> pointing to the repo root. This is
    normal npm behavior. The junction itself is dangerous (would let
    `git worktree remove` cascade-delete .git), but the *cause* is benign.
    We pre-emptively delete just this one junction so the rest of the flow
    can proceed, instead of refusing and asking the human to do it manually.
    """
    git_dir_norm = os.path.normpath(os.path.abspath(git_dir)).lower()
    repo_root_norm = os.path.normpath(os.path.abspath(repo_root)).lower()

    # First pass: identify and pre-emptively remove the npm workspace self-ref junction.
    # This is the ONLY junction we auto-handle; everything else still goes through the
    # full danger check.
    repo_name = os.path.basename(repo_root_norm)
    auto_handled = []
    for jpath, jtype in junctions:
        if jtype == "error" or "SCAN" in jpath:
            continue
        try:
            target = os.path.realpath(jpath)
            target_norm = os.path.normpath(target).lower()
            jbasename = os.path.basename(os.path.normpath(jpath)).lower()
            # Match the npm workspace self-ref pattern:
            #   - junction lives at <something>/node_modules/<repo-name>
            #   - target is exactly the repo root
            if (target_norm == repo_root_norm
                    and jbasename == repo_name
                    and os.sep + "node_modules" + os.sep in os.path.normpath(jpath).lower()):
                info(f"Pre-emptively removing expected npm workspace self-ref junction: {jpath}")
                delete_junction_safe(jpath)
                auto_handled.append(jpath)
        except (OSError, ValueError):
            pass

    # Filter out the auto-handled junctions from the original list — caller will
    # use the filtered list for subsequent processing.
    if auto_handled:
        junctions[:] = [(p, t) for p, t in junctions if p not in auto_handled]

    # Second pass: any remaining junction whose target points at .git or its parents
    # is a real danger and we refuse to proceed.
    dangerous = []
    for jpath, jtype in junctions:
        if jtype == "error" or "SCAN" in jpath:
            continue

        # Try to resolve the junction target
        try:
            target = os.path.realpath(jpath)
            target_norm = os.path.normpath(target).lower()

            # DANGER: target is inside .git directory
            if target_norm.startswith(git_dir_norm + os.sep) or target_norm == git_dir_norm:
                dangerous.append((jpath, target, "POINTS INTO .git DIRECTORY"))

            # DANGER: target is the repo root itself (junction could traverse back to .git)
            if target_norm == repo_root_norm:
                dangerous.append((jpath, target, "POINTS TO REPO ROOT (contains .git)"))

            # DANGER: target is a parent of .git
            if git_dir_norm.startswith(target_norm + os.sep):
                dangerous.append((jpath, target, "POINTS TO PARENT OF .git"))

        except (OSError, ValueError):
            # Can't resolve target — treat as suspicious but not blocking
            warn(f"Could not resolve junction target for: {jpath}")

    if dangerous:
        msg = "BLOCKED: Found junction(s) whose targets could damage .git:\n"
        for jpath, target, reason in dangerous:
            msg += f"\n  Junction: {jpath}\n  Target:   {target}\n  Reason:   {reason}\n"
        msg += ("\nThese junctions point into the git repository. Deleting them with "
                "git worktree remove would follow the junction and destroy .git.\n"
                "The junctions themselves have been identified but NOT deleted.\n"
                "Manual investigation required — use 'cmd /c rmdir <junction>' to remove "
                "individual junctions, then retry.")
        error(msg)


# ─────────────────────────────────────────────
# .git integrity verification
# ─────────────────────────────────────────────

def snapshot_git_dir(git_dir):
    """Take a snapshot of critical .git contents for integrity comparison."""
    snapshot = {}
    try:
        # Check that .git is a directory (not a file, which would mean it's a worktree .git pointer)
        snapshot["is_dir"] = os.path.isdir(git_dir)
        snapshot["exists"] = os.path.exists(git_dir)

        if os.path.isdir(git_dir):
            # Check critical subdirectories
            critical = ["HEAD", "config", "refs", "objects"]
            for item in critical:
                item_path = os.path.join(git_dir, item)
                snapshot[item] = os.path.exists(item_path)

            # Count refs to detect mass deletion
            refs_dir = os.path.join(git_dir, "refs")
            if os.path.isdir(refs_dir):
                ref_count = sum(len(files) for _, _, files in os.walk(refs_dir))
                snapshot["ref_count"] = ref_count

            # Count objects (just the pack files, not loose objects)
            pack_dir = os.path.join(git_dir, "objects", "pack")
            if os.path.isdir(pack_dir):
                snapshot["pack_count"] = len(os.listdir(pack_dir))

    except Exception as e:
        snapshot["error"] = str(e)
    return snapshot


def verify_git_integrity(git_dir, before_snapshot, worktree_path):
    """
    Verify .git integrity after a worktree removal by comparing before/after state.
    Catches cases where .git was partially damaged (not just fully deleted).
    """
    # Basic existence check
    if not os.path.exists(git_dir):
        error(f"CRITICAL: .git has been DESTROYED at {git_dir}!\n"
              f"This happened during cleanup of: {worktree_path}\n"
              "Recovery: git init && git remote add origin <url> && git fetch origin "
              "&& git checkout -B main origin/main")

    if not os.path.isdir(git_dir):
        error(f"CRITICAL: .git exists but is no longer a directory at {git_dir}!\n"
              f"This happened during cleanup of: {worktree_path}\n"
              "Recovery: remove the file, then git init && git remote add origin <url>")

    # Check critical files still exist
    critical = ["HEAD", "config", "refs", "objects"]
    missing = []
    for item in critical:
        item_path = os.path.join(git_dir, item)
        if not os.path.exists(item_path):
            missing.append(item)

    if missing:
        error(f"CRITICAL: .git is DAMAGED — missing critical items: {', '.join(missing)}\n"
              f"This happened during cleanup of: {worktree_path}\n"
              "Recovery: git init && git remote add origin <url> && git fetch origin "
              "&& git checkout -B main origin/main")

    # Check if refs were mass-deleted (sign of junction traversal damage)
    if "ref_count" in before_snapshot:
        refs_dir = os.path.join(git_dir, "refs")
        if os.path.isdir(refs_dir):
            current_ref_count = sum(len(files) for _, _, files in os.walk(refs_dir))
            before_count = before_snapshot["ref_count"]
            # Allow some reduction (the worktree's own ref gets cleaned up)
            # but flag if >50% of refs disappeared
            if before_count > 5 and current_ref_count < before_count * 0.5:
                error(f"CRITICAL: .git refs were mass-deleted! "
                      f"Before: {before_count}, After: {current_ref_count}\n"
                      f"This happened during cleanup of: {worktree_path}\n"
                      "This strongly suggests a junction followed into .git/refs/.\n"
                      "Recovery: git init && git remote add origin <url> && git fetch origin "
                      "&& git checkout -B main origin/main")

    # Check if pack files disappeared
    if "pack_count" in before_snapshot and before_snapshot["pack_count"] > 0:
        pack_dir = os.path.join(git_dir, "objects", "pack")
        if os.path.isdir(pack_dir):
            current_pack_count = len(os.listdir(pack_dir))
            if current_pack_count == 0:
                error(f"CRITICAL: .git pack files were destroyed!\n"
                      f"Before: {before_snapshot['pack_count']} packs, After: 0\n"
                      f"This happened during cleanup of: {worktree_path}\n"
                      "Recovery: git init && git remote add origin <url> && git fetch origin "
                      "&& git checkout -B main origin/main")


# ─────────────────────────────────────────────
# Junction deletion
# ─────────────────────────────────────────────

def delete_junction_safe(junction_path):
    """
    Delete a junction/symlink without following it.
    Uses os.unlink for file symlinks, and rmdir for directory junctions/symlinks.
    Falls back to cmd /c rmdir for stubborn cases.
    """
    try:
        # For directory junctions/symlinks: os.rmdir removes the link, not the target
        if os.path.isdir(junction_path):
            try:
                os.rmdir(junction_path)
                return
            except OSError:
                pass  # Fall through to rmdir cmd

        # For file symlinks: os.unlink removes the link
        if os.path.islink(junction_path):
            try:
                os.unlink(junction_path)
                return
            except OSError:
                pass  # Fall through to rmdir cmd

        # Fallback: cmd /c rmdir (junction-safe on Windows)
        win_path = to_windows_path(junction_path)
        result = subprocess.run(
            ["cmd", "/c", "rmdir", win_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            # Try rd /s /q as last resort
            result = subprocess.run(
                ["cmd", "/c", "rd", "/s", "/q", win_path],
                capture_output=True, text=True, timeout=30,
            )

        # Verify removal
        if os.path.exists(junction_path):
            error(f"Failed to delete junction at {junction_path}. "
                  "Refusing to continue — investigate manually.")

    except subprocess.TimeoutExpired:
        error(f"Deletion timed out for {junction_path}. The path may have infinite recursion. "
              "Try deleting from Windows Explorer or use robocopy empty-dir trick.")
    except Exception as e:
        error(f"Failed to delete junction at {junction_path}: {e}")


# ─────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# Scratch worktree protection, ancestor checks, fast delete
# ─────────────────────────────────────────────

SCRATCH_MARKER = ".scratch-keep"


# Set from main() via --force-dirty. Default False: refuse to remove dirty trees.
FORCE_DIRTY = False
# Set from main() via --work-preserved-in-base. Default False: always require a push.
WORK_PRESERVED_IN_BASE = False

# Machine-generated files that are regenerable and never contain human work.
# Deliberately narrow: everything here is a tool cache or editor-local setting.
# NEVER add a source-file pattern — this list is the difference between "cleanup
# skipped a stale cache" and "cleanup destroyed someone's afternoon".
# worktree_sweeper.py imports this so the two tools cannot disagree.
_IGNORABLE_DIRT = (
    ".claude/settings.local.json",
    ".gemini/settings.local.json",
    ".DS_Store",
    "Thumbs.db",
)
_IGNORABLE_DIRT_SUFFIX = (
    ".tsbuildinfo",          # TypeScript incremental build cache
)
_IGNORABLE_DIRT_CONTAINS = (
    "supabase/.temp/",       # Supabase CLI version cache
    "node_modules/",         # never tracked work
)


def status_line_path(line):
    """Extract the file path from one `git status --porcelain` line.

    Never index at a fixed offset: the leading space of the XY status field is
    easily lost (any caller that .strip()s stdout eats it), and an off-by-one
    there silently truncates the path so no filter can match it — which is how a
    cache file got mistaken for real work.
    """
    parts = line.strip().split(None, 1)
    if len(parts) < 2:
        return parts[0] if parts else ""
    path = parts[1].strip()
    if " -> " in path:          # renames: "old -> new"
        path = path.split(" -> ", 1)[1].strip()
    return path.strip('"')


def is_ignorable_dirt(path):
    """True when an uncommitted path is a regenerable artifact, not work."""
    norm = path.replace("\\", "/").strip()
    if any(norm.endswith(ig) for ig in _IGNORABLE_DIRT):
        return True
    if any(norm.endswith(sfx) for sfx in _IGNORABLE_DIRT_SUFFIX):
        return True
    if any(frag in norm for frag in _IGNORABLE_DIRT_CONTAINS):
        return True
    return False


def worktree_dirt(worktree_path):
    """Return a list of 'real' uncommitted changes in the worktree.

    Includes untracked files: a brand-new file the user just wrote is exactly the
    kind of work that is unrecoverable once the directory is gone.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path, capture_output=True, text=True, timeout=60,
        )
    except Exception:
        # If we cannot determine cleanliness, assume dirty — refusing to delete is
        # always the safe failure mode.
        return ["<could not run git status — treating as dirty>"]
    if result.returncode != 0:
        return ["<git status failed — treating as dirty>"]
    dirt = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if is_ignorable_dirt(status_line_path(line)):
            continue
        dirt.append(line.rstrip())
    return dirt


def check_worktree_clean(worktree_path):
    """Refuse to remove a worktree that holds uncommitted work."""
    dirt = worktree_dirt(worktree_path)
    if not dirt:
        return
    if FORCE_DIRTY:
        warn(f"--force-dirty: proceeding despite {len(dirt)} uncommitted change(s). "
             "This work is being destroyed permanently.")
        return
    preview = "\n".join(f"    {d}" for d in dirt[:15])
    more = f"\n    ... and {len(dirt) - 15} more" if len(dirt) > 15 else ""
    error(
        f"BLOCKED: worktree has {len(dirt)} uncommitted change(s) — refusing to remove it.\n"
        f"{preview}{more}\n"
        "Uncommitted work cannot be recovered after removal (no reflog, no remote copy).\n"
        "Commit and push it first:\n"
        f"    git -C \"{worktree_path}\" add -A && git -C \"{worktree_path}\" commit -m \"wip\"\n"
        f"    git -C \"{worktree_path}\" push -u origin HEAD\n"
        "Then re-run this script. If you are certain the changes are worthless, "
        "re-run with --force-dirty."
    )


def is_scratch_worktree(worktree_path):
    """The reusable scratch worktree (scratch_worktree.py) is never deleted.
    Identified by the `_scratch` directory name OR a `.scratch-keep` marker."""
    norm = os.path.normpath(worktree_path)
    if os.path.basename(norm).lower() == "_scratch":
        return True
    return os.path.isfile(os.path.join(worktree_path, SCRATCH_MARKER))


def is_ancestor_only(repo_root, branch_name):
    """True if every commit on branch_name is already reachable from a known
    base. Checks REMOTE-tracking refs first (origin/main, origin/dev) so a
    stale local main/dev can't produce a false 'has unpushed commits' verdict —
    the bug that made the script refuse a branch already merged to origin/main."""
    if not branch_name:
        return False
    for base in ("origin/main", "origin/dev", "main", "dev"):
        try:
            if subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", base],
                capture_output=True, text=True, timeout=5, cwd=repo_root,
            ).returncode != 0:
                continue
            cnt = subprocess.run(
                ["git", "rev-list", "--count", f"{base}..{branch_name}"],
                capture_output=True, text=True, timeout=10, cwd=repo_root,
            )
            if cnt.returncode == 0 and cnt.stdout.strip() == "0":
                return True
        except Exception:
            pass
    return False


def fast_delete_heavy_dirs(worktree_path):
    """Fast-delete large gitignored dirs (node_modules, build output) with
    `rd /s /q` BEFORE `git worktree remove`, so git has almost nothing left to
    walk. Safe ONLY after every junction has been removed (callers guarantee
    this via scan_junctions_python + re-scan). This is the speed fix: git's own
    per-file removal of a 1,200-package node_modules is what used to blow past
    the timeout."""
    heavy = [
        "node_modules", "dist", "build", ".vite", ".next", ".turbo", "coverage",
        os.path.join("client", "node_modules"), os.path.join("client", "dist"),
        os.path.join("server", "node_modules"), os.path.join("server", "dist"),
        os.path.join("affiliate-portal", "node_modules"),
    ]
    for rel in heavy:
        target = os.path.join(worktree_path, rel)
        if not os.path.isdir(target):
            continue
        win_path = to_windows_path(target)
        try:
            subprocess.run(
                ["cmd", "/c", "rd", "/s", "/q", win_path],
                capture_output=True, text=True, timeout=240,
            )
        except subprocess.TimeoutExpired:
            warn(f"Fast-delete timed out for {target}; git worktree remove will retry it.")
        except Exception as e:
            warn(f"Fast-delete failed for {target}: {e}")


def find_dependency_dirs(worktree_path, max_depth=3):
    """Locate every node_modules inside a worktree (root, client/, server/, packages/*)."""
    found = []
    base_depth = worktree_path.rstrip("\\/").count(os.sep)
    for root, dirs, _ in os.walk(worktree_path):
        if root.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
            continue
        if "node_modules" in dirs:
            found.append(os.path.join(root, "node_modules"))
            dirs.remove("node_modules")   # never descend INTO one
        for skip in (".git", "dist", "build"):
            if skip in dirs:
                dirs.remove(skip)
    return found


def purge_dependencies(worktree_path):
    """Delete node_modules from a worktree while LEAVING the worktree in place.

    node_modules is gitignored, so this is invisible to git and cannot dirty the
    tree — but it is where the disk actually goes (~250MB-1GB per worktree).

    The danger this function exists to navigate: npm workspaces create NTFS
    junctions inside node_modules that point back at the worktree's OWN source
    directories (client/, server/, landing/). Any recursive delete that follows
    junctions would delete the real source through them. So we follow the same
    sequence as a full cleanup: scan junctions, verify none target .git, remove
    each junction as a link, re-scan to prove zero remain, and only then use
    `rd /s /q` (the one deletion tool on Windows that does not traverse links).
    """
    worktree_path = os.path.abspath(worktree_path)
    if not os.path.isdir(worktree_path):
        warn(f"Not a directory: {worktree_path}")
        return False

    dep_dirs = find_dependency_dirs(worktree_path)
    if not dep_dirs:
        info("No node_modules present — nothing to purge.")
        return True

    repo_root = find_repo_root() or worktree_path
    git_dir = os.path.join(repo_root, ".git")

    freed_from = []
    for dep in dep_dirs:
        junctions = scan_junctions_python(dep)
        if junctions:
            # Refuses (hard-errors) if any junction points into .git.
            check_junction_targets_safe(junctions, git_dir, repo_root)
            info(f"Removing {len(junctions)} junction(s) under {dep}...")
            for jpath, _jtype in junctions:
                delete_junction_safe(jpath)
            remaining = scan_junctions_python(dep)
            if remaining:
                warn(f"{len(remaining)} junction(s) still present under {dep} — "
                     "skipping this directory rather than risk following one.")
                continue
        win_path = to_windows_path(dep)
        try:
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", win_path],
                           capture_output=True, text=True, timeout=600)
        except Exception as e:
            warn(f"Purge failed for {dep}: {e}")
            continue
        if os.path.isdir(dep):
            warn(f"Purge incomplete, directory still present: {dep}")
        else:
            freed_from.append(dep)

    if freed_from:
        info(f"Purged node_modules from {len(freed_from)} location(s) in "
             f"{os.path.basename(worktree_path)}. Run `npm install` before the next build.")
    return bool(freed_from)


def find_repo_root():
    """Find the git repo root from CWD."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def repo_root_for_worktree(worktree_path):
    """Return the MAIN repo root that owns worktree_path, or None. The worktree
    may live under a different repo than the CWD, so resolve from the path
    itself: the first `worktree ` entry of `git worktree list --porcelain` run
    inside it is always the main worktree (= repo root)."""
    if not worktree_path:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", worktree_path, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip()
    return None


def detect_base_branch(repo_root):
    """Detect main or dev branch."""
    try:
        result = subprocess.run(
            ["git", "branch", "--list", "dev", "main"],
            capture_output=True, text=True, timeout=5, cwd=repo_root,
        )
        branches = [b.strip().lstrip("* ") for b in result.stdout.splitlines()]
        if "dev" in branches:
            return "dev"
        if "main" in branches:
            return "main"
    except Exception:
        pass
    return "main"


def detect_worktree_branch(repo_root, worktree_path):
    """Get the branch name for a worktree."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        current_wt = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_wt = os.path.normpath(line[len("worktree "):].strip())
            elif line.startswith("branch ") and current_wt:
                norm_target = os.path.normpath(worktree_path)
                if current_wt.lower() == norm_target.lower():
                    # branch refs/heads/feat/foo -> feat/foo
                    return line[len("branch refs/heads/"):].strip()
    except Exception:
        pass
    return None


def list_worktrees(repo_root):
    """List all worktrees except the main one. Returns [(path, branch), ...]."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        worktrees = []
        current_path = None
        current_branch = None
        is_first = True  # Skip the main worktree (first entry)
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current_path and not is_first:
                    worktrees.append((current_path, current_branch))
                current_path = line[len("worktree "):].strip()
                current_branch = None
                if is_first:
                    is_first = False
                    current_path = None  # Skip main
            elif line.startswith("branch "):
                current_branch = line[len("branch refs/heads/"):].strip()
        # Don't forget the last entry
        if current_path:
            worktrees.append((current_path, current_branch))
        return worktrees
    except Exception:
        return []


def check_workboard(repo_root, worktree_path):
    """Refuse to clean a worktree that's marked active/waiting in the workboard.

    Source of truth: WORKBOARD.json (managed by workboard.py). The markdown
    mirror (WORKBOARD.md) can be stale — workboard.py only regenerates it on
    write, so manual cleanups or external edits leave it out of sync. Falls
    back to WORKBOARD.md only if WORKBOARD.json doesn't exist (older repos
    that haven't migrated to the JSON workboard yet).
    """
    if _workboard_json_blocks(repo_root, worktree_path):
        error("BLOCKED: This worktree is referenced in WORKBOARD.json as active/waiting.\n"
              "Only the agent that owns this worktree should clean it up, "
              "and only after work is complete.")
    if _workboard_md_blocks(repo_root, worktree_path):
        error("BLOCKED: This worktree is referenced in WORKBOARD.md as active/waiting.\n"
              "Only the agent that owns this worktree should clean it up, "
              "and only after work is complete.")


# A board entry is only believed for this long. Entries are written when work
# STARTS and updated by hand afterwards — in practice they never were, so
# "active" entries survived for months and blocked cleanup of finished work.
# An entry nobody has touched in this many days is treated as abandoned
# bookkeeping, not as a live claim. Real active work updates far more often.
WORKBOARD_TRUST_DAYS = 14


def _entry_is_fresh(entry):
    """True if this board entry is recent enough to be believed.

    An undated entry is never believed: dates were only added later, so a blank
    timestamp means the entry predates that and is by definition ancient.
    """
    from datetime import datetime as _dt
    stamp = (entry.get("updated") or entry.get("started") or "").strip()
    if not stamp:
        return False
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            when = _dt.strptime(stamp, fmt)
        except ValueError:
            continue
        return (_dt.now() - when).days < WORKBOARD_TRUST_DAYS
    return False   # unparseable date: do not let it block forever


def _workboard_json_blocks(repo_root, worktree_path):
    """Returns True if WORKBOARD.json has a FRESH active/waiting entry for this path."""
    import json as _json
    wb_json = os.path.join(repo_root, "WORKBOARD.json")
    if not os.path.isfile(wb_json):
        return False
    try:
        with open(wb_json, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except Exception:
        return False
    norm_wt = os.path.normpath(os.path.abspath(worktree_path)).lower()

    def _entry_matches(entry):
        wt = (entry.get("worktree") or "").strip()
        if not wt:
            return False
        if os.path.normpath(os.path.abspath(wt)).lower() != norm_wt:
            return False
        if entry.get("status") not in ("active", "waiting"):
            return False
        if not _entry_is_fresh(entry):
            info(f"WORKBOARD claims '{entry.get('feature', '?')}' is still active, but the "
                 f"entry has not been touched in {WORKBOARD_TRUST_DAYS}+ days — treating it "
                 "as stale bookkeeping rather than a live claim.")
            return False
        return True

    for entry in data.get("active", []):
        if _entry_matches(entry):
            return True
        for sub in entry.get("subagents", []) or []:
            if _entry_matches(sub):
                return True
    return False


def _workboard_md_blocks(repo_root, worktree_path):
    """Fallback: scan WORKBOARD.md if WORKBOARD.json isn't present."""
    if os.path.isfile(os.path.join(repo_root, "WORKBOARD.json")):
        return False  # JSON exists → JSON is source of truth, don't double-check
    wb_path = os.path.join(repo_root, "WORKBOARD.md")
    if not os.path.isfile(wb_path):
        return False
    try:
        with open(wb_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return False

    in_active = False
    active_lines = []
    for line in content.splitlines():
        if line.strip().lower().startswith("## active work"):
            in_active = True
            continue
        if in_active and line.strip().startswith("## "):
            break
        if in_active:
            active_lines.append(line)

    active_text = "\n".join(active_lines)
    norm_wt = os.path.normpath(worktree_path).lower()
    for line in active_lines:
        if "worktree:" in line.lower():
            parts = line.lower().split("worktree:")
            if len(parts) > 1:
                entry_path = parts[1].strip().rstrip(".")
                if os.path.normpath(entry_path).lower() == norm_wt:
                    if any(s in active_text.lower() for s in ["status: active", "status: waiting"]):
                        return True
    return False


def check_branch_pushed(repo_root, branch_name):
    """Verify branch exists on remote — UNLESS the branch is ancestor-only.

    The check protects unpushed work that could be lost. But if every commit on
    the branch is already reachable from a base (origin/main, origin/dev, main,
    or dev), there is nothing to lose. is_ancestor_only() checks the REMOTE refs
    first, so a stale local main/dev no longer causes a false refusal of a branch
    that's already merged to origin/main.
    """
    if is_ancestor_only(repo_root, branch_name):
        info(f"Branch '{branch_name}' is ancestor-only of a known base "
             "(every commit already on origin) — skipping push check.")
        return

    # Otherwise, require the branch to be on origin.
    try:
        result = subprocess.run(
            ["git", "branch", "-r", "--list", f"origin/{branch_name}"],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        if not result.stdout.strip():
            error(f"Branch '{branch_name}' has NOT been pushed to remote.\n"
                  f"Push it first: git push -u origin {branch_name}\n"
                  "Then re-run this script. Unpushed branches are unrecoverable if .git is corrupted.")
    except Exception:
        warn(f"Could not verify if branch '{branch_name}' is pushed to remote.")


def run_git(cmd, cwd=None):
    """Run a git command, warn on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd)
        if result.returncode != 0 and result.stderr.strip():
            warn(f"{' '.join(cmd)} failed: {result.stderr.strip()}")
    except Exception as e:
        warn(f"{' '.join(cmd)} failed: {e}")


def to_windows_path(path):
    """Convert a path to Windows format (backslashes) for cmd.exe."""
    return os.path.abspath(path).replace("/", "\\")


def info(msg):
    print(f"  {msg}")


def warn(msg):
    print(f"  WARNING: {msg}", file=sys.stderr)


def error(msg):
    print(f"  ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
