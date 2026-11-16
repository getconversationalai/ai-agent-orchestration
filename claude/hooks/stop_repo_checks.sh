#!/bin/bash
# Consolidated Stop hook — replaces four separate Stop hooks that each spawned a
# process and ran their own git calls (three independent `git diff` scans per
# turn). This runs ONE `git rev-parse` + ONE name-only diff and short-circuits
# on the common case (turn ended with no code changes), so the expensive
# unified diff only runs when there is actually something to scan.
#
# Preserves ALL prior behaviors:
#   1. .git-destroyed CRITICAL guard (safety — runs first, always)
#   2. uncommitted-code-on-main/master warning
#   (WORKBOARD.md warning removed: worktree/workboard routine retired)
#   4. debug-code (console.log / debugger) scan of changed lines

GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$GIT_ROOT" ] && exit 0

# 1. Safety guard: .git must exist. Runs before any short-circuit.
if [ ! -d "$GIT_ROOT/.git" ]; then
    echo "🚨 CRITICAL: .git directory has been DESTROYED at $GIT_ROOT/.git! STOP ALL WORK IMMEDIATELY. Recovery: git init && git remote add origin <url> && git fetch origin && git checkout -B main origin/main"
    exit 0
fi

# One cheap scan: are there changed code files at all?
CODE_GLOBS=('*.ts' '*.tsx' '*.js' '*.jsx' '*.py' '*.go' '*.rs' '*.css' '*.json' '*.mjs' '*.cjs')
CHANGED=$(git diff --name-only -- "${CODE_GLOBS[@]}" 2>/dev/null)

# Common case: nothing changed -> nothing else to check. Exit fast.
[ -z "$CHANGED" ] && exit 0

# 2. Uncommitted code on main/master.
BRANCH=$(git branch --show-current 2>/dev/null)
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
    echo "⚠️ You have uncommitted code changes on $BRANCH. Create a feature branch."
fi

# 4. Debug code left in changed lines (only reached when code changed).
DEBUG_LINES=$(git diff --unified=0 -- '*.ts' '*.tsx' '*.js' '*.jsx' '*.py' '*.mjs' '*.cjs' 2>/dev/null \
    | grep -E '^\+' \
    | grep -v '^\+\+\+' \
    | grep -iE '(console\.log\(|^\+\s*debugger\s*;?\s*$)' \
    | head -5)
if [ -n "$DEBUG_LINES" ]; then
    echo "⚠️ Debug code detected in your changes:"
    echo "$DEBUG_LINES" | sed 's/^\+/  /'
    echo ""
    echo "Remove console.log() and debugger statements before committing."
fi
