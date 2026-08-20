"""Tests for agent_state.identity -- repo identity resolver (Task 1).

Covers normalise_remote() using the brief's exact parametrized cases
(including the file:// regression that broke v1: it returned "file/tmp/x"
for "file:///tmp/x" instead of None), plus resolve_identity()'s "never
raises" contract across missing remotes, absent repos, missing git, and
credential-bearing URLs.
"""
import subprocess

import pytest

from agent_state.identity import normalise_remote, resolve_identity


# ---------------------------------------------------------------------------
# normalise_remote
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://github.com/Org/Repo.git",        "github.com/org/repo"),
    ("git@github.com:Org/Repo.git",            "github.com/org/repo"),
    ("ssh://git@github.com/Org/Repo.git",      "github.com/org/repo"),
    ("https://user:token@github.com/Org/Repo", "github.com/org/repo"),
])
def test_forms_normalise_to_one_key(raw, expected):
    assert normalise_remote(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "not a url", "file:///tmp/x", "/local/path"])
def test_non_git_remotes_return_none(raw):
    assert normalise_remote(raw) is None   # file:/// is the v1 regression


def test_credential_token_never_appears_in_key():
    key = normalise_remote("https://user:s3cr3t-token@github.com/Org/Repo")
    assert key == "github.com/org/repo"
    assert "s3cr3t-token" not in key
    assert "user" not in key


def test_git_scheme_is_rejected_like_file_scheme():
    # Not explicitly in the brief's table, but the reference implementation's
    # comment calls out git:// alongside file:// as a rejected scheme --
    # lock that in explicitly.
    assert normalise_remote("git://github.com/Org/Repo.git") is None


# ---------------------------------------------------------------------------
# resolve_identity
# ---------------------------------------------------------------------------

def _init_repo(path, remote_url=None):
    """Create a real git repo at `path`, optionally with an origin remote."""
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True,
    )
    if remote_url is not None:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", remote_url],
            check=True,
        )
    return path


def test_resolve_identity_returns_expected_keys(tmp_path):
    repo = _init_repo(tmp_path, "https://github.com/Org/Repo.git")
    result = resolve_identity(repo)
    assert set(result.keys()) == {"github_repo_id", "remote_key", "host", "owner", "name"}


def test_resolve_identity_with_https_remote(tmp_path):
    repo = _init_repo(tmp_path, "https://github.com/Org/Repo.git")
    result = resolve_identity(repo)
    assert result == {
        "github_repo_id": None,
        "remote_key": "github.com/org/repo",
        "host": "github.com",
        "owner": "org",
        "name": "repo",
    }


def test_resolve_identity_with_ssh_remote_matches_https_key(tmp_path):
    repo = _init_repo(tmp_path, "git@github.com:Org/Repo.git")
    result = resolve_identity(repo)
    assert result["remote_key"] == "github.com/org/repo"
    assert result["github_repo_id"] is None


def test_resolve_identity_credential_bearing_remote_does_not_leak_token(tmp_path):
    repo = _init_repo(tmp_path, "https://user:s3cr3t-token@github.com/Org/Repo.git")
    result = resolve_identity(repo)
    assert result["remote_key"] == "github.com/org/repo"
    assert "s3cr3t-token" not in result["remote_key"]
    assert "s3cr3t-token" not in repr(result)


def test_resolve_identity_no_remote_returns_all_none(tmp_path):
    repo = _init_repo(tmp_path, remote_url=None)
    result = resolve_identity(repo)
    assert result == {
        "github_repo_id": None,
        "remote_key": None,
        "host": None,
        "owner": None,
        "name": None,
    }


def test_resolve_identity_rejects_file_scheme_remote(tmp_path):
    repo = _init_repo(tmp_path, "file:///tmp/x")
    result = resolve_identity(repo)
    assert result["remote_key"] is None
    assert result["github_repo_id"] is None


def test_resolve_identity_absent_repo_returns_all_none_without_raising(tmp_path):
    not_a_repo = tmp_path / "no-such-git-repo"
    not_a_repo.mkdir()
    result = resolve_identity(not_a_repo)
    assert result == {
        "github_repo_id": None,
        "remote_key": None,
        "host": None,
        "owner": None,
        "name": None,
    }


def test_resolve_identity_nonexistent_path_does_not_raise():
    result = resolve_identity("Z:/definitely/does/not/exist/anywhere")
    assert result["remote_key"] is None
    assert result["github_repo_id"] is None


def test_resolve_identity_missing_git_returns_all_none_without_raising(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path, "https://github.com/Org/Repo.git")
    monkeypatch.setattr("agent_state.identity.shutil.which", lambda _: None)
    result = resolve_identity(repo)
    assert result == {
        "github_repo_id": None,
        "remote_key": None,
        "host": None,
        "owner": None,
        "name": None,
    }


def test_resolve_identity_never_raises_on_garbage_input():
    # repo_path is not even a path-like value.
    result = resolve_identity(12345)
    assert result["remote_key"] is None
    assert result["github_repo_id"] is None
