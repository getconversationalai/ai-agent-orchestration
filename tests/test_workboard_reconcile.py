from datetime import datetime, timedelta
from pathlib import Path
import pytest
import workboard_reconcile as wr

BASES = ["dev", "main", "master"]

def test_is_noise_flags_base_and_automation_and_wip():
    assert wr.is_noise("main", BASES) is True
    assert wr.is_noise("dev", BASES) is True
    assert wr.is_noise("origin/HEAD", BASES) is True
    assert wr.is_noise("wip/fix/usage-agg", BASES) is True
    assert wr.is_noise("claude/sync-local-main", BASES) is True

def test_is_noise_allows_real_work_branches():
    assert wr.is_noise("feat/ai-safety-layer", BASES) is False
    assert wr.is_noise("fix/kb-document-understanding", BASES) is False

def test_is_noise_flags_bot_branches():
    assert wr.is_noise("dependabot/github_actions/actions/checkout-7", BASES) is True
    assert wr.is_noise("renovate/lodash", BASES) is True


NOW = datetime(2026, 7, 31, 12, 0)

def _cand(name, age_days=1, merged=False, diff=True, files=("server/x.ts",)):
    return {"name": name,
            "last_commit": NOW - timedelta(days=age_days),
            "first_commit": NOW - timedelta(days=age_days + 3),
            "merged": merged, "has_source_diff": diff, "files": list(files),
            "worktree": ""}

def test_registers_inflight_branch_absent_from_board():
    board = {"active": [], "completed": []}
    cands = [_cand("feat/ai-safety-layer")]
    actions = wr.plan_reconciliation(board, cands, {"feat/ai-safety-layer"}, NOW, 14)
    reg = [a for a in actions if a["kind"] == "register"]
    assert len(reg) == 1
    assert reg[0]["branch"] == "feat/ai-safety-layer"
    assert reg[0]["feature"] == "feat ai safety layer"
    assert reg[0]["files"] == ["server/x.ts"]

def test_derive_feature_is_collision_safe_across_prefixes():
    # "feat/foo" and "fix/foo" used to both derive "foo" (collision); now they
    # keep the full branch path so distinct branches yield distinct names.
    assert wr.derive_feature("feat/foo") != wr.derive_feature("fix/foo")
    assert wr.derive_feature("feat/ai-safety-layer") == "feat ai safety layer"

def test_skips_merged_old_and_already_registered():
    board = {"active": [{"branch": "feat/known", "updated": "2026-07-31T00:00",
                         "feature": "known", "id": "known"}], "completed": []}
    cands = [_cand("feat/merged", merged=True),
             _cand("feat/old", age_days=30),
             _cand("feat/nodiff", diff=False),
             _cand("feat/known")]
    actions = wr.plan_reconciliation(board, cands,
              {"feat/merged", "feat/old", "feat/nodiff", "feat/known"}, NOW, 14)
    assert [a for a in actions if a["kind"] == "register"] == []

def test_completes_merged_and_gone_active_entries():
    board = {"active": [
        {"branch": "feat/done", "updated": "2026-06-01T00:00", "feature": "done", "id": "done"},
        {"branch": "feat/vanished", "updated": "2026-06-01T00:00", "feature": "van", "id": "van"},
    ], "completed": []}
    cands = [_cand("feat/done", merged=True)]
    actions = wr.plan_reconciliation(board, cands, {"feat/done"}, NOW, 14)
    comps = {a["branch"]: a for a in actions if a["kind"] == "complete"}
    assert comps["feat/done"]["merged"] == "yes"
    assert comps["feat/vanished"]["merged"] == "no"   # gone

def test_bumps_active_entry_whose_branch_moved():
    board = {"active": [{"branch": "feat/live", "updated": "2026-07-20T00:00",
                         "feature": "live", "id": "live"}], "completed": []}
    cands = [_cand("feat/live", age_days=1, files=("server/new.ts",))]
    actions = wr.plan_reconciliation(board, cands, {"feat/live"}, NOW, 14)
    bump = [a for a in actions if a["kind"] == "bump"]
    assert len(bump) == 1 and bump[0]["files"] == ["server/new.ts"]

def test_idempotent_when_updated_is_current():
    board = {"active": [{"branch": "feat/live", "updated": "2026-07-31T00:00",
                         "feature": "live", "id": "live"}], "completed": []}
    cands = [_cand("feat/live", age_days=1)]   # last_commit 2026-07-30 < updated 2026-07-31
    actions = wr.plan_reconciliation(board, cands, {"feat/live"}, NOW, 14)
    assert actions == []


def test_parse_iso_all_formats():
    assert wr.parse_iso("2026-07-29T02:32:00") == datetime(2026, 7, 29, 2, 32, 0)
    assert wr.parse_iso("2026-07-29T02:32") == datetime(2026, 7, 29, 2, 32)
    assert wr.parse_iso("2026-07-29T02:32:00+03:00") == datetime(2026, 7, 29, 2, 32, 0)
    assert wr.parse_iso("2026-07-29T02:32:00Z") == datetime(2026, 7, 29, 2, 32, 0)
    assert wr.parse_iso("") is None
    assert wr.parse_iso("not-a-date") is None

def test_parse_iso_space_separated_and_date_only_formats():
    assert wr.parse_iso("2026-07-29 02:32:00") == datetime(2026, 7, 29, 2, 32, 0)
    assert wr.parse_iso("2026-07-29 02:32") == datetime(2026, 7, 29, 2, 32)
    assert wr.parse_iso("2026-07-29") == datetime(2026, 7, 29, 0, 0)


def test_complete_and_bump_reference_by_stable_id():
    # Entry has id="feat-x" (stable) but feature="human name" (user-facing)
    # Complete and bump actions must reference by id, not feature name
    board = {"active": [
        {"branch": "feat/merged", "updated": "2026-06-01T00:00",
         "feature": "human friendly name", "id": "feat-merged"},
        {"branch": "feat/updated", "updated": "2026-07-20T00:00",
         "feature": "another human name", "id": "feat-updated"},
    ], "completed": []}
    cands = [
        _cand("feat/merged", merged=True),
        _cand("feat/updated", age_days=1, files=("new.ts",)),
    ]
    actions = wr.plan_reconciliation(board, cands, {"feat/merged", "feat/updated"}, NOW, 14)

    # Verify complete action references by id
    comps = {a["branch"]: a for a in actions if a["kind"] == "complete"}
    assert comps["feat/merged"]["feature"] == "feat-merged"  # Must be id, not "human friendly name"

    # Verify bump action references by id
    bumps = {a["branch"]: a for a in actions if a["kind"] == "bump"}
    assert bumps["feat/updated"]["feature"] == "feat-updated"  # Must be id, not "another human name"


def test_build_command_register_includes_required_flags():
    a = {"kind": "register", "branch": "feat/x", "feature": "x", "id": "feat-x",
         "files": ["a.ts", "b.ts"], "worktree": "/wt/x",
         "started": "2026-07-01T00:00", "updated": "2026-07-30T00:00"}
    cmd = wr.build_command(a)
    assert cmd[0] == "register"
    assert "--feature" in cmd and "x" in cmd
    assert "--tool" in cmd and "reconcile" in cmd
    assert "--branch" in cmd and "feat/x" in cmd
    assert "--worktree" in cmd
    assert "a.ts,b.ts" in cmd

def test_build_command_complete_is_never_auto_cleanup():
    a = {"kind": "complete", "branch": "feat/x", "feature": "feat-x", "merged": "yes"}
    cmd = wr.build_command(a)
    assert cmd[0] == "complete"
    assert "--no-auto-cleanup" in cmd
    assert "--merged" in cmd and "yes" in cmd

def test_build_command_bump_uses_update_files():
    a = {"kind": "bump", "branch": "feat/x", "feature": "feat-x", "files": ["a.ts"]}
    cmd = wr.build_command(a)
    assert cmd[0] == "update-files"
    assert "a.ts" in cmd

def test_should_write_requires_sentinel_or_arm():
    assert wr.should_write(apply=True, arm=False, sentinel_exists=True) is True
    assert wr.should_write(apply=True, arm=True, sentinel_exists=False) is True
    assert wr.should_write(apply=True, arm=False, sentinel_exists=False) is False
    assert wr.should_write(apply=False, arm=False, sentinel_exists=True) is False


def test_arm_dry_run_does_not_create_sentinel(monkeypatch, tmp_path):
    """--arm --dry-run must be a true no-op: no sentinel, no writes."""
    sentinel = tmp_path / "reconcile-initialized"
    monkeypatch.setattr(wr, "SENTINEL", sentinel)
    monkeypatch.setattr(wr, "find_repos", lambda: [])

    wr.main(["--arm", "--dry-run"])
    assert not sentinel.exists()

    wr.main(["--arm"])
    assert sentinel.exists()


def test_apply_action_returns_false_on_failure(monkeypatch, capsys):
    """Test that apply_action returns False when workboard.py fails."""
    class _FakeResult:
        returncode = 1
        stderr = "boom"

    monkeypatch.setattr(wr.subprocess, "run", lambda *a, **k: _FakeResult())
    result = wr.apply_action(Path("."), {"kind": "complete", "branch": "feat/x", "feature": "feat-x", "merged": "yes"})
    assert result is False

    captured = capsys.readouterr()
    assert "failed" in captured.err
    assert "feat/x" in captured.err


def test_apply_action_returns_true_on_success(monkeypatch):
    """Test that apply_action returns True when workboard.py succeeds."""
    class _FakeResult:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(wr.subprocess, "run", lambda *a, **k: _FakeResult())
    result = wr.apply_action(Path("."), {"kind": "complete", "branch": "feat/x", "feature": "feat-x", "merged": "yes"})
    assert result is True


def test_build_command_bogus_kind_raises_valueerror():
    """Test that build_command raises ValueError for unknown action kind."""
    with pytest.raises(ValueError, match="unknown action kind"):
        wr.build_command({"kind": "bogus"})


def test_format_summary_counts_per_repo():
    results = {"feature-a": [
        {"kind": "register"}, {"kind": "register"}, {"kind": "complete"}, {"kind": "bump"}]}
    line = wr.format_summary(results)
    assert "feature-a" in line
    assert "+2 registered" in line

def test_format_summary_all_current():
    assert "current" in wr.format_summary({"feature-a": []}).lower()


def test_summary_status_unarmed_preview():
    s = wr.summary_status(False, 0, 0, unarmed=True)
    assert "PREVIEW" in s
    assert "not armed" in s

def test_summary_status_applied_no_failures():
    assert wr.summary_status(True, 5, 0, unarmed=False) == " (applied 5)"

def test_summary_status_applied_with_failures():
    s = wr.summary_status(True, 5, 2, unarmed=False)
    assert "2 FAILED" in s

def test_summary_status_no_write_not_unarmed():
    assert wr.summary_status(False, 0, 0, unarmed=False) == ""
