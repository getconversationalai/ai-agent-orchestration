#!/usr/bin/env python3
"""
workboard_staleness_check.py — Detect abandoned work across your configured repos.

Project roots come from ~/.agent-orchestration/config.json (never auto-discovered).

Cross-references three real signals per repo (WORKBOARD.json is treated as a claim,
not truth — this tool exists because boards go stale):
  1. WORKBOARD.json active entries: age of `updated` timestamp
  2. git worktrees: last commit date, merged-into-base status, unpushed commits
  3. worktree dirt: uncommitted changes sitting in the tree (skipped in --fast)

Classification per finding:
  FINISHED_UNCLEANED — branch merged into base, worktree clean → safe to auto-clean
  ABANDONED          — no activity for --days days, but has unmerged/unpushed work
                       → needs a human decision (continue vs archive), emailed
  AT_RISK            — unpushed commits exist (any age) → data-loss risk if deleted
  AUTOSAVED_WIP      — sitting on a wip/ branch: work was auto-committed here, so the
                       tree looks clean but is NOT empty. Reported so nobody concludes
                       their work vanished and starts over.
  STALE_BOARD_ENTRY  — WORKBOARD active entry older than --days with no matching
                       live worktree activity

Usage:
  py workboard_staleness_check.py                  # human-readable report
  py workboard_staleness_check.py --summary        # 1-3 lines for SessionStart hook
  py workboard_staleness_check.py --json           # machine-readable
  py workboard_staleness_check.py --email          # send report to NOTIFY_EMAIL if
                                                   # findings exist (needs creds file)
  py workboard_staleness_check.py --days 14        # staleness threshold (default 7)

Email credentials file (~/.stale-work/notify.env — kept OUTSIDE ~/.ai-instructions
so the config-repo capture script never commits it), KEY=VALUE lines:
  NOTIFY_EMAIL=you@example.com
  RESEND_API_KEY=re_...            # preferred: Resend HTTP API
  # or SMTP:
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=you@gmail.com
  SMTP_PASS=<gmail app password>
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Project roots come from ~/.agent-orchestration/config.json so this script is
# not tied to one person's disk layout.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import orchestration_config as ocfg
except Exception:
    ocfg = None
# Runtime artifacts live OUTSIDE ~/.ai-instructions — that dir is swept wholesale
# into the ai-agent-orchestration-config GitHub repo by capture.ps1, and the creds
# file must never be committed.
RUNTIME_DIR = Path.home() / ".stale-work"
CREDS_FILE = RUNTIME_DIR / "notify.env"


def run_git(repo: Path, *args, timeout=15) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def find_repos() -> list[Path]:
    """Repositories under the CONFIGURED roots — never auto-discovered."""
    return ocfg.find_repos() if ocfg else []


def detect_base_branch(repo: Path) -> str:
    for base in ("dev", "main", "master"):
        if run_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}"):
            return base
    return "main"


def list_worktrees(repo: Path) -> list[dict]:
    out = run_git(repo, "worktree", "list", "--porcelain")
    entries, cur = [], {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {"path": line[len("worktree "):].strip()}
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].replace("refs/heads/", "").strip()
        elif line == "detached":
            cur["branch"] = None
    if cur:
        entries.append(cur)
    # First entry is the main working tree; skip it and protected scratch slots.
    result = []
    for e in entries[1:]:
        p = e["path"].replace("\\", "/")
        if p.rstrip("/").endswith("/_scratch") or "/_baseline" in p:
            continue
        result.append(e)
    return result


def analyze_worktree(repo: Path, base: str, wt: dict, days: int, fast: bool) -> dict | None:
    branch = wt.get("branch")
    path = wt["path"]
    if not branch:
        return {"kind": "UNCLEAR", "repo": repo.name, "branch": "(detached)",
                "path": path, "detail": "detached HEAD — manual look needed"}

    last_commit_iso = run_git(repo, "log", "-1", "--format=%cI", branch)
    if not last_commit_iso:
        return {"kind": "UNCLEAR", "repo": repo.name, "branch": branch,
                "path": path, "detail": "cannot read branch tip — possibly broken worktree"}
    last_commit = datetime.fromisoformat(last_commit_iso)
    age_days = (datetime.now(timezone.utc) - last_commit.astimezone(timezone.utc)).days

    merged = run_git(repo, "merge-base", "--is-ancestor", branch, base) == "" and \
        subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", branch, base],
                       capture_output=True).returncode == 0

    upstream = run_git(repo, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
    if upstream:
        unpushed = run_git(repo, "rev-list", "--count", f"{upstream}..{branch}") or "0"
    else:
        unpushed = run_git(repo, "rev-list", "--count", f"{base}..{branch}") or "?"
    unpushed_n = int(unpushed) if unpushed.isdigit() else -1
    has_unpushed = (unpushed_n > 0) or (not upstream and unpushed_n != 0)

    dirty = ""
    if not fast and Path(path).exists():
        porcelain = run_git(Path(path), "status", "--porcelain", timeout=20)
        real = [ln for ln in porcelain.splitlines()
                if ".claude/settings.local.json" not in ln]
        dirty = f"{len(real)} uncommitted file(s)" if real else ""

    if branch.startswith("wip/"):
        # Surfaced deliberately: the worktree looks clean and its board entry still
        # names the original branch, so an agent can easily conclude its work
        # vanished and start over. Say plainly where the work went.
        return {"repo": repo.name, "branch": branch, "path": path,
                "last_commit": last_commit_iso[:10], "age_days": age_days,
                "merged": False, "unpushed": 0, "dirty": "",
                "kind": "AUTOSAVED_WIP",
                "detail": f"work was auto-saved here from '{branch[4:]}'; "
                          f"restore with: git checkout {branch[4:]} && "
                          f"git cherry-pick {branch}"}

    info = {
        "repo": repo.name, "branch": branch, "path": path,
        "last_commit": last_commit_iso[:10], "age_days": age_days,
        "merged": merged, "unpushed": unpushed_n if unpushed_n >= 0 else "no-upstream",
        "dirty": dirty,
    }

    if merged and not dirty:
        if age_days >= days:
            return {**info, "kind": "FINISHED_UNCLEANED",
                    "detail": f"merged into {base}, clean, idle {age_days}d — safe to clean"}
        return None
    if has_unpushed and age_days >= days:
        return {**info, "kind": "ABANDONED",
                "detail": f"unpushed/unmerged work, idle {age_days}d — decide: continue or archive"}
    if has_unpushed:
        return {**info, "kind": "AT_RISK",
                "detail": f"unpushed commits ({unpushed}) — push for backup"}
    if age_days >= days:
        return {**info, "kind": "ABANDONED",
                "detail": f"pushed but unmerged, idle {age_days}d — decide: merge, continue, or archive"}
    return None


def analyze_workboard(repo: Path, days: int) -> list[dict]:
    wb_path = repo / "WORKBOARD.json"
    if not wb_path.exists():
        return []
    try:
        wb = json.loads(wb_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    findings = []
    cutoff = datetime.now() - timedelta(days=days)
    for e in wb.get("active", []):
        ts = e.get("updated") or e.get("started") or ""
        try:
            when = datetime.strptime(ts, "%Y-%m-%dT%H:%M")
        except ValueError:
            when = None
        if when is None or when < cutoff:
            age = f"{(datetime.now() - when).days}d" if when else "undated"
            findings.append({
                "kind": "STALE_BOARD_ENTRY", "repo": repo.name,
                "branch": e.get("branch", "?"), "path": e.get("worktree", ""),
                "feature": e.get("feature", "?"), "plan": e.get("plan_path", ""),
                "age_days": (datetime.now() - when).days if when else 9999,
                "detail": f"WORKBOARD entry '{e.get('feature','?')}' untouched for {age}",
            })
    return findings


CACHE_FILE = RUNTIME_DIR / "cache.json"
CACHE_MAX_AGE_HOURS = 6


def collect(days: int, fast: bool) -> list[dict]:
    jobs = []
    findings = []
    for repo in find_repos():
        base = detect_base_branch(repo)
        for wt in list_worktrees(repo):
            jobs.append((repo, base, wt))
        findings.extend(analyze_workboard(repo, days))
    with ThreadPoolExecutor(max_workers=8) as pool:
        for f in pool.map(lambda j: analyze_worktree(j[0], j[1], j[2], days, fast), jobs):
            if f:
                findings.append(f)
    order = {"AT_RISK": 0, "AUTOSAVED_WIP": 1, "ABANDONED": 2,
             "STALE_BOARD_ENTRY": 3, "FINISHED_UNCLEANED": 4, "UNCLEAR": 5}
    findings.sort(key=lambda f: (order.get(f["kind"], 9), -f.get("age_days", 0)))
    try:
        RUNTIME_DIR.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "days": days,
             "findings": findings}), encoding="utf-8")
    except Exception:
        pass
    return findings


def cached_findings(days: int) -> list[dict] | None:
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(cache["ts"])
        if cache.get("days") != days:
            return None
        if datetime.now(timezone.utc) - ts > timedelta(hours=CACHE_MAX_AGE_HOURS):
            return None
        return cache["findings"]
    except Exception:
        return None


def render_report(findings: list[dict]) -> str:
    if not findings:
        return "No stale or at-risk work found. All tracked work is fresh."
    lines = [f"Stale-work report — {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    by_kind = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    titles = {
        "AT_RISK": "AT RISK — unpushed commits (push these before anything else)",
        "AUTOSAVED_WIP": "AUTO-SAVED — work was moved to a wip/ branch; the worktree "
                         "looks clean but is NOT empty",
        "ABANDONED": "ABANDONED — idle with real work; continue or archive?",
        "STALE_BOARD_ENTRY": "STALE WORKBOARD ENTRIES — board says active, evidence says no",
        "FINISHED_UNCLEANED": "FINISHED BUT NOT CLEANED — merged + clean, safe to remove",
        "UNCLEAR": "UNCLEAR — broken/odd state, manual look",
    }
    for kind in ["AT_RISK", "AUTOSAVED_WIP", "ABANDONED", "STALE_BOARD_ENTRY",
                 "FINISHED_UNCLEANED", "UNCLEAR"]:
        if kind not in by_kind:
            continue
        lines.append(f"## {titles[kind]} ({len(by_kind[kind])})")
        for f in by_kind[kind]:
            feat = f" [{f['feature']}]" if f.get("feature") else ""
            lines.append(f"- {f['repo']} :: {f['branch']}{feat} — {f['detail']}")
            if f.get("path"):
                lines.append(f"    worktree: {f['path']}")
            if f.get("plan"):
                lines.append(f"    plan: {f['plan']}")
        lines.append("")
    lines.append("Cleanup:  py ~/.ai-instructions/tools/worktree_cleanup.py <worktree-path>")
    lines.append("Board fix: py ~/.ai-instructions/tools/workboard.py complete --feature \"<name>\"")
    return "\n".join(lines)


def load_creds() -> dict:
    creds = {}
    if CREDS_FILE.exists():
        for line in CREDS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def send_email(report: str, findings: list[dict]) -> bool:
    creds = load_creds()
    to_addr = creds.get("NOTIFY_EMAIL")
    if not to_addr:
        print(f"NOTE: no NOTIFY_EMAIL in {CREDS_FILE} — email not sent.",
              file=sys.stderr)
        return False
    n_risk = sum(1 for f in findings if f["kind"] == "AT_RISK")
    n_aband = sum(1 for f in findings if f["kind"] == "ABANDONED")
    subject = f"[dev] Stale work: {n_aband} abandoned, {n_risk} at-risk"

    sa_key = creds.get("GMAIL_SA_KEY")
    if sa_key and Path(sa_key).exists():
        # Service account + domain-wide delegation: the SA impersonates
        # SEND_AS and calls Gmail directly. No browser, no token expiry.
        import base64
        import urllib.request
        from email.mime.text import MIMEText
        send_as = creds.get("SEND_AS") or creds.get("FROM_EMAIL") or to_addr
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request as GRequest
            sa_creds = service_account.Credentials.from_service_account_file(
                sa_key, scopes=["https://www.googleapis.com/auth/gmail.send"],
                subject=send_as)
            sa_creds.refresh(GRequest())
            token = sa_creds.token
        except Exception as e:
            print(f"Service-account auth failed: {e}\n"
                  "If this says 'unauthorized_client', the domain-wide delegation "
                  "step in admin.google.com is not done yet — see "
                  "~/.stale-work/notify.env for the client ID and scope.",
                  file=sys.stderr)
            return False
        msg = MIMEText(report, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, send_as, to_addr
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=json.dumps({"raw": raw}).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                ok = 200 <= resp.status < 300
                print(f"Email sent via Gmail API (service account) to {to_addr}."
                      if ok else f"Gmail API returned {resp.status}.")
                return ok
        except Exception as e:
            print(f"Gmail API send failed: {e}", file=sys.stderr)
            return False

    if creds.get("RESEND_API_KEY"):
        import urllib.request
        payload = json.dumps({
            "from": creds.get("FROM_EMAIL", "onboarding@resend.dev"),
            "to": [to_addr], "subject": subject,
            "text": report,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=payload, method="POST",
            headers={"Authorization": f"Bearer {creds['RESEND_API_KEY']}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ok = 200 <= resp.status < 300
                print(f"Email sent via Resend to {to_addr}." if ok
                      else f"Resend returned {resp.status}.")
                return ok
        except Exception as e:
            print(f"Resend send failed: {e}", file=sys.stderr)
            return False

    if creds.get("SMTP_HOST") and creds.get("SMTP_USER") and creds.get("SMTP_PASS"):
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(report, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, creds["SMTP_USER"], to_addr
        try:
            with smtplib.SMTP(creds["SMTP_HOST"], int(creds.get("SMTP_PORT", 587)),
                              timeout=20) as s:
                s.starttls()
                s.login(creds["SMTP_USER"], creds["SMTP_PASS"])
                s.send_message(msg)
            print(f"Email sent via SMTP to {to_addr}.")
            return True
        except Exception as e:
            print(f"SMTP send failed: {e}", file=sys.stderr)
            return False

    print("NOTE: creds file has NOTIFY_EMAIL but no RESEND_API_KEY or SMTP_* — "
          "email not sent.", file=sys.stderr)
    return False


def main():
    ap = argparse.ArgumentParser(description="Detect abandoned work across C:/dev repos.")
    ap.add_argument("--days", type=int, default=7, help="staleness threshold in days")
    ap.add_argument("--summary", action="store_true", help="1-3 lines for SessionStart hook")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--email", action="store_true", help="send report if findings exist")
    ap.add_argument("--fast", action="store_true",
                    help="skip per-worktree dirty checks (for hooks)")
    ap.add_argument("--no-cache", action="store_true",
                    help="force a fresh scan even if a recent cache exists")
    args = ap.parse_args()

    findings = None
    if args.summary and not args.no_cache:
        findings = cached_findings(args.days)
    if findings is None:
        findings = collect(args.days, args.fast or args.summary)

    if args.as_json:
        print(json.dumps(findings, indent=2))
        return

    if args.summary:
        if not findings:
            print("[stale-work] all clear")
            return
        counts = {}
        for f in findings:
            counts[f["kind"]] = counts.get(f["kind"], 0) + 1
        parts = [f"{v} {k.lower().replace('_', '-')}" for k, v in counts.items()]
        print(f"[stale-work] {', '.join(parts)} "
              f"(threshold {args.days}d)")
        print("  detail: py ~/.ai-instructions/tools/workboard_staleness_check.py")
        return

    report = render_report(findings)
    print(report)
    if args.email and findings:
        send_email(report, findings)


if __name__ == "__main__":
    main()
