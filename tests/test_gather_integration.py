import subprocess, json
from pathlib import Path
import workboard_reconcile as wr

def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True,
                   capture_output=True, text=True)

def _make_repo(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1\n"); _git(repo, "add", "."); _git(repo, "commit", "-m", "c0")
    # unmerged feature branch with a real diff
    _git(repo, "checkout", "-b", "feat/live")
    (repo / "b.txt").write_text("2\n"); _git(repo, "add", "."); _git(repo, "commit", "-m", "c1")
    # merged branch
    _git(repo, "checkout", "main"); _git(repo, "checkout", "-b", "feat/merged")
    (repo / "c.txt").write_text("3\n"); _git(repo, "add", "."); _git(repo, "commit", "-m", "c2")
    _git(repo, "checkout", "main"); _git(repo, "merge", "--no-ff", "feat/merged", "-m", "m")
    _git(repo, "checkout", "feat/live")
    return repo

def test_gather_classifies_merged_and_unmerged(tmp_path):
    repo = _make_repo(tmp_path)
    cands, names = wr.gather_branches(repo, "main", ["main"])
    by = {c["name"]: c for c in cands}
    assert by["feat/live"]["merged"] is False
    assert by["feat/live"]["has_source_diff"] is True
    assert "b.txt" in by["feat/live"]["files"]
    assert by["feat/merged"]["merged"] is True
    assert "feat/live" in names and "feat/merged" in names
    assert "main" not in [c["name"] for c in cands]   # base filtered from candidates

def test_read_board_missing_returns_empty(tmp_path):
    repo = tmp_path / "empty"; repo.mkdir()
    assert wr.read_board(repo) == {"active": [], "completed": []}

def test_read_board_handles_invalid_json_shapes(tmp_path):
    repo = tmp_path / "invalid"; repo.mkdir()
    # Test with JSON array
    (repo / "WORKBOARD.json").write_text("[]")
    assert wr.read_board(repo) == {"active": [], "completed": []}
    # Test with JSON null
    (repo / "WORKBOARD.json").write_text("null")
    assert wr.read_board(repo) == {"active": [], "completed": []}

def test_gather_includes_remote_only_merged_branches(tmp_path):
    # Create a bare remote repo
    bare = tmp_path / "bare.git"; bare.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=bare, check=True, capture_output=True)

    # Create a working repo and push a branch
    repo = tmp_path / "work"; repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1\n"); _git(repo, "add", "."); _git(repo, "commit", "-m", "c0")

    # Add remote and push main
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "main")

    # Create feature branch
    _git(repo, "checkout", "-b", "feat/remote-only")
    (repo / "b.txt").write_text("2\n"); _git(repo, "add", "."); _git(repo, "commit", "-m", "c1")
    _git(repo, "push", "-u", "origin", "feat/remote-only")

    # Merge into main
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "feat/remote-only", "-m", "merge")
    _git(repo, "push", "origin", "main")

    # Delete the LOCAL ref (keep remote)
    _git(repo, "branch", "-D", "feat/remote-only")
    # Verify local is gone, remote exists
    out = subprocess.run(["git", "-C", str(repo), "branch", "-a"], capture_output=True, text=True)
    assert "remotes/origin/feat/remote-only" in out.stdout

    # gather_branches should still find and classify it as merged
    cands, names = wr.gather_branches(repo, "main", ["main"])
    by = {c["name"]: c for c in cands}
    assert "feat/remote-only" in names, f"Branch not in all_branch_names. Found: {names}"
    assert "feat/remote-only" in by, f"Branch dropped from candidates. Found: {list(by.keys())}"
    assert by["feat/remote-only"]["merged"] is True, "Remote-only merged branch should be marked merged"
