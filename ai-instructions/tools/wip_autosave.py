#!/usr/bin/env python3
"""
wip_autosave.py — Never lose half-finished work sitting in a worktree.

Worktrees accumulated uncommitted changes that sat for months: unbacked-up,
and (correctly) un-removable by any cleanup, so they became permanent residents.

This commits that work to a PARALLEL branch — `wip/<branch>` — and pushes it.
The real feature branch is never touched, so its history stays clean and
reviewable. The worktree ends up on the wip branch with a clean tree, which
also makes it eligible for normal cleanup later.

SAFETY — this runs unattended, so it refuses rather than guesses:
  * Nothing is committed if any changed file looks like a credential. Committing
    secrets is unrecoverable in shared history, so a suspicious file aborts the
    whole save and reports loudly. Better a missed backup than a leaked key.
  * .gitignore is honoured (`git add -A` respects it), so build output and
    node_modules never enter a commit.
  * Never runs on the main working tree, and never on main/master/dev.

Usage:
  py wip_autosave.py <worktree-path>          # dry run: report what it would do
  py wip_autosave.py <worktree-path> --apply
  py wip_autosave.py --here --apply           # the worktree containing cwd
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROTECTED_BRANCHES = {"main", "master", "dev", "develop"}

# Filenames that carry credentials often enough that auto-committing one is
# never acceptable. Matched on the full relative path, case-insensitively.
SECRET_PATH_PATTERNS = [
    r"(^|/)\.env$", r"(^|/)\.env\.(?!example$|sample$|template$)[^/]+$",
    r"(^|/)secrets?\.(json|ya?ml|toml|ini|txt)$",
    r"(^|/)credentials?\.(json|ya?ml|toml|ini)$",
    r"(^|/)service-account.*\.json$",
    r"\.(pem|key|p12|pfx|keystore|jks)$",
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$",
    r"(^|/)\.npmrc$", r"(^|/)\.pypirc$", r"(^|/)\.netrc$",
    r"(^|/)sa-key\.json$",
]

# High-signal credential material. Deliberately narrow — a false positive only
# costs a skipped backup, but these patterns rarely appear by accident.
SECRET_CONTENT_PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    (r"\bsk-[A-Za-z0-9]{20,}", "OpenAI-style secret key"),
    (r"\bsk_live_[A-Za-z0-9]{10,}", "Stripe live secret key"),
    (r"\brk_live_[A-Za-z0-9]{10,}", "Stripe restricted live key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub personal access token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained token"),
    (r"\bxox[abprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bre_[A-Za-z0-9]{20,}", "Resend API key"),
    (r"\bAIza[0-9A-Za-z_\-]{30,}", "Google API key"),
    (r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.", "JWT"),
]

MAX_SCAN_BYTES = 512 * 1024   # skip content-scanning anything larger


def git(cwd, *args, timeout=120):
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def status_paths(worktree):
    """Changed paths (modified, added, untracked) — gitignored files never appear."""
    code, out, _ = git(worktree, "status", "--porcelain")
    if code != 0:
        return None
    paths = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        p = parts[1].strip().strip('"')
        if " -> " in p:
            p = p.split(" -> ", 1)[1].strip()
        paths.append(p.replace("\\", "/"))
    return paths


def scan_for_secrets(worktree, paths):
    """Return a list of (path, reason). Empty means safe to commit."""
    hits = []
    for rel in paths:
        low = rel.lower()
        for pat in SECRET_PATH_PATTERNS:
            if re.search(pat, low):
                hits.append((rel, "filename looks like a credential file"))
                break
        else:
            full = Path(worktree) / rel
            try:
                if not full.is_file() or full.stat().st_size > MAX_SCAN_BYTES:
                    continue
                text = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for pat, label in SECRET_CONTENT_PATTERNS:
                if re.search(pat, text):
                    hits.append((rel, f"contains what looks like a {label}"))
                    break
    return hits


def autosave(worktree, apply_changes):
    worktree = os.path.abspath(worktree)

    code, top, _ = git(worktree, "rev-parse", "--show-toplevel")
    if code != 0:
        print("Not a git worktree — nothing to do.")
        return 0
    worktree = top

    # Refuse on the main working tree: its uncommitted state is often the user's
    # own in-progress work and is not ours to commit.
    _, common, _ = git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    _, gitdir, _ = git(worktree, "rev-parse", "--path-format=absolute", "--git-dir")
    if common and gitdir and Path(common).resolve() == Path(gitdir).resolve():
        print("Main working tree — skipping (only linked worktrees are auto-saved).")
        return 0

    code, branch, _ = git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or not branch or branch == "HEAD":
        print("Detached HEAD — skipping, a human should decide what this is.")
        return 0
    if branch in PROTECTED_BRANCHES or branch.startswith("wip/"):
        print(f"On '{branch}' — skipping.")
        return 0

    paths = status_paths(worktree)
    if paths is None:
        print("Could not read worktree status — skipping.")
        return 1
    if not paths:
        return 0  # clean: nothing to save, stay silent

    secrets = scan_for_secrets(worktree, paths)
    if secrets:
        print(f"WIP autosave SKIPPED for {branch}: {len(secrets)} file(s) look like secrets.")
        for rel, why in secrets[:10]:
            print(f"  - {rel}: {why}")
        print("  Nothing was committed. Commit deliberately, or add these to .gitignore.")
        return 2

    wip_branch = f"wip/{branch}"
    if not apply_changes:
        print(f"WOULD SAVE {len(paths)} change(s) from '{branch}' to '{wip_branch}' and push.")
        for p in paths[:10]:
            print(f"  - {p}")
        if len(paths) > 10:
            print(f"  ... and {len(paths) - 10} more")
        return 0

    # Move onto the parallel branch first: uncommitted changes come with us, so
    # the commit lands on wip/<branch> and the real branch is never rewritten.
    code, _, err = git(worktree, "checkout", "-B", wip_branch)
    if code != 0:
        print(f"Could not switch to {wip_branch}: {err.splitlines()[-1:] }")
        return 1

    git(worktree, "add", "-A")
    msg = (f"chore(wip): auto-saved from {branch}\n\n"
           f"Saved automatically because this worktree held uncommitted work.\n"
           f"The original branch '{branch}' was not modified.\n"
           f"To resume: git checkout {branch} && git cherry-pick {wip_branch}")
    code, _, err = git(worktree, "commit", "-m", msg)
    if code != 0:
        print(f"Nothing committed ({err.splitlines()[-1:] }).")
        return 1

    code, _, err = git(worktree, "push", "-u", "origin", wip_branch, timeout=300)
    if code != 0:
        print(f"Saved locally to {wip_branch}, but push failed: {err.splitlines()[-1:]}")
        return 1

    print(f"WIP autosave: {len(paths)} change(s) saved to {wip_branch} and pushed. "
          f"'{branch}' untouched.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Auto-save uncommitted worktree work to wip/<branch>.")
    ap.add_argument("worktree_path", nargs="?", default=None)
    ap.add_argument("--here", action="store_true", help="Use the worktree containing cwd.")
    ap.add_argument("--apply", action="store_true", help="Actually commit and push.")
    args = ap.parse_args()
    target = args.worktree_path or (os.getcwd() if args.here else None)
    if not target:
        ap.print_help()
        return 1
    return autosave(target, args.apply)


if __name__ == "__main__":
    sys.exit(main())
