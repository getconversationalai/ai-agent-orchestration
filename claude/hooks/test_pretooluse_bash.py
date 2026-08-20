"""Unit tests for pretooluse_bash.fetch_precedes_in_command — the guard that
requires a `git fetch` to precede any merge/push/update-ref targeting a base
branch (main/master/dev).

This safety hook was previously UNTESTED. These tests lock in the fix for the
newline-separated and env-prefixed fetch commands that were being false-blocked
(the single most common friction event in the audit), while asserting the guard
STILL blocks a genuinely missing or mis-ordered fetch — the safety property must
not regress.

Run: py -m pytest claude/hooks/test_pretooluse_bash.py -q
"""
import importlib.util
import os

# Path-pin the import so we always exercise the LIVE hook next to this file,
# never a stale copy that may sit earlier on sys.path.
_HOOK = os.path.join(os.path.dirname(__file__), "pretooluse_bash.py")
_spec = importlib.util.spec_from_file_location("pretooluse_bash_under_test", _HOOK)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
fetch_precedes = _mod.fetch_precedes_in_command


# --- THE FIX: valid preceding fetches that used to be false-blocked ----------

def test_newline_separated_fetch_then_push_is_credited():
    cmd = "git fetch origin main\ngit push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is True


def test_newline_separated_fetch_then_merge_is_credited():
    cmd = "git fetch origin main\ngit merge origin/main"
    assert fetch_precedes(cmd, "main") is True


def test_multiline_script_with_intermediate_lines():
    cmd = "git fetch origin main\ngit status\ngit push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is True


def test_env_prefixed_fetch_is_credited():
    cmd = "MSYS_NO_PATHCONV=1 git fetch origin main && git push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is True


def test_backslash_newline_continuation_fetch_is_credited():
    cmd = "git fetch \\\norigin main\ngit push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is True


def test_bare_fetch_all_refs_newline():
    cmd = "git fetch\ngit push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is True


# --- UNCHANGED good behavior: && / pull / --all still credited ---------------

def test_ampersand_fetch_then_push_still_works():
    cmd = "git fetch origin main && git push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is True


def test_pull_counts_as_fetch():
    cmd = "git pull origin main\ngit merge origin/main"
    assert fetch_precedes(cmd, "main") is True


def test_fetch_all_flag_newline():
    cmd = "git fetch --all\ngit push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is True


def test_dev_base_branch():
    cmd = "git fetch origin dev\ngit merge origin/dev"
    assert fetch_precedes(cmd, "dev") is True


# --- SAFETY: a missing or mis-ordered fetch must STILL block -----------------

def test_no_fetch_at_all_is_blocked():
    cmd = "git push origin feat/x:main"
    assert fetch_precedes(cmd, "main") is False


def test_fetch_after_op_is_not_credited_newline():
    # push first, fetch later -> the fetch does NOT precede -> must block
    cmd = "git push origin feat/x:main\ngit fetch origin main"
    assert fetch_precedes(cmd, "main") is False


def test_fetch_after_op_with_trailing_newline_still_blocks():
    # a trailing empty segment must not shift the op out of "final segment"
    # and let a post-op fetch count as preceding
    cmd = "git push origin feat/x:main\ngit fetch origin main\n"
    assert fetch_precedes(cmd, "main") is False


def test_only_op_present_blocks():
    cmd = "git merge origin/main"
    assert fetch_precedes(cmd, "main") is False


def test_env_override_bypasses_when_set():
    os.environ["ALLOW_UNFETCHED_BASE_MERGE"] = "1"
    try:
        assert fetch_precedes("git push origin feat/x:main", "main") is True
    finally:
        os.environ.pop("ALLOW_UNFETCHED_BASE_MERGE", None)
