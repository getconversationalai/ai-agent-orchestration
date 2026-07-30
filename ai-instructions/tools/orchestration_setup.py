#!/usr/bin/env python3
"""
orchestration_setup.py — set this system up on a new machine.

Run it interactively, or drive it with flags (which is how Claude runs it).

    py orchestration_setup.py                       # asks questions
    py orchestration_setup.py --roots C:/dev --mode report
    py orchestration_setup.py --schedule            # register the daily job
    py orchestration_setup.py --setup-email you@company.com   # GCP + Gmail sending
    py orchestration_setup.py --status              # what is configured right now

New installs default to REPORT mode: the tools describe what they would do and
change nothing. That is deliberate. This system pushes branches, deletes
worktrees and purges dependencies — nobody should grant that to a tool they have
watched for zero days. Switch on with `--mode apply` once the reports look right.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orchestration_config as ocfg   # noqa: E402

TOOLS = Path(__file__).resolve().parent
IS_WINDOWS = platform.system() == "Windows"
TASK_NAME = "StaleWorkDailyCheck"


def say(msg=""):
    print(msg)


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kw)
    except Exception as e:
        class R:
            returncode, stdout, stderr = 1, "", str(e)
        return R()


# --------------------------------------------------------------------------
# 1. configuration
# --------------------------------------------------------------------------
def configure(roots, mode, interactive):
    cfg = ocfg.load()

    if roots:
        cfg["project_roots"] = roots
    elif interactive and not cfg["project_roots"]:
        say("Where do your git repositories live?")
        say("  This is the folder CONTAINING your projects, e.g. C:/dev or ~/code.")
        say("  Nothing outside these folders is ever touched.")
        raw = input("  Path(s), comma separated: ").strip()
        cfg["project_roots"] = [p.strip() for p in raw.split(",") if p.strip()]

    if mode:
        cfg["mode"] = mode

    cfg["project_roots"] = [str(Path(os.path.expanduser(p))).replace("\\", "/")
                            for p in cfg["project_roots"]]

    missing = [p for p in cfg["project_roots"] if not Path(p).is_dir()]
    for p in missing:
        say(f"  WARNING: {p} does not exist — no repositories will be found there.")

    path = ocfg.save(cfg)
    say(f"Saved {path}")
    found = ocfg.find_repos(cfg)
    say(f"Found {len(found)} repositor{'y' if len(found) == 1 else 'ies'}.")
    if not found:
        say("  Nothing to manage yet. Check the paths above.")
    return cfg


# --------------------------------------------------------------------------
# 2. the daily job
# --------------------------------------------------------------------------
def daily_command():
    py = shutil.which("py") if IS_WINDOWS else (shutil.which("python3") or sys.executable)
    return py or sys.executable


def schedule_daily(hour=6, minute=0):
    """Register the once-a-day sweep. Windows Task Scheduler or cron."""
    runtime = Path.home() / ".stale-work"
    runtime.mkdir(exist_ok=True)
    py = daily_command()

    if IS_WINDOWS:
        cmd_file = runtime / "daily.cmd"
        cmd_file.write_text(
            "@echo off\r\n"
            f"set LOG={runtime}\\cron.log\r\n"
            "echo. >> %LOG%\r\n"
            "echo ===== %DATE% %TIME% ===== >> %LOG%\r\n"
            f'"{py}" "{TOOLS}\\worktree_sweeper.py" --apply --autosave >> %LOG% 2>&1\r\n'
            f'"{py}" "{TOOLS}\\workboard_staleness_check.py" --email --days 7 '
            f"--no-cache >> %LOG% 2>&1\r\n",
            encoding="utf-8")
        r = run(["schtasks", "/Create", "/F", "/SC", "DAILY",
                 "/ST", f"{hour:02d}:{minute:02d}", "/TN", TASK_NAME,
                 "/TR", str(cmd_file)])
        if r.returncode != 0:
            say(f"Could not register the task: {r.stderr.strip()[:200]}")
            return False
        # Without these it silently never runs on an unplugged laptop, and a
        # missed run is never made up.
        ps = ("$s=(Get-ScheduledTask -TaskName '%s').Settings;"
              "$s.DisallowStartIfOnBatteries=$false;$s.StopIfGoingOnBatteries=$false;"
              "$s.StartWhenAvailable=$true;"
              "Set-ScheduledTask -TaskName '%s' -Settings $s | Out-Null" % (TASK_NAME, TASK_NAME))
        run(["powershell", "-NoProfile", "-Command", ps])
        say(f"Daily sweep registered for {hour:02d}:{minute:02d} (runs on battery; "
            "catches up if the machine was off).")
        say(f"  Log: {runtime / 'cron.log'}")
        return True

    # macOS / Linux
    line = (f"{minute} {hour} * * * {py} {TOOLS}/worktree_sweeper.py --apply --autosave "
            f">> {runtime}/cron.log 2>&1 && {py} {TOOLS}/workboard_staleness_check.py "
            f"--email --days 7 --no-cache >> {runtime}/cron.log 2>&1")
    existing = run(["crontab", "-l"]).stdout
    if "worktree_sweeper.py" in existing:
        say("A daily sweep is already in your crontab — leaving it alone.")
        return True
    new = (existing.rstrip() + "\n" + line + "\n").lstrip("\n")
    p = subprocess.run(["crontab", "-"], input=new, text=True, capture_output=True)
    if p.returncode != 0:
        say(f"Could not update crontab: {p.stderr.strip()[:200]}")
        say(f"  Add this line manually:\n    {line}")
        return False
    say(f"Daily sweep added to crontab at {hour:02d}:{minute:02d}.")
    return True


# --------------------------------------------------------------------------
# 3. email (entirely optional)
# --------------------------------------------------------------------------
def setup_email(address, project_id=None):
    """Create a Google service account that can email you the daily report.

    Everything here is optional — without it the system still sweeps, still
    reports at session start, and still writes its log. You just do not get mail.
    """
    if not shutil.which("gcloud"):
        say("gcloud is not installed. Install the Google Cloud CLI, then re-run:")
        say(f"  py {Path(__file__).name} --setup-email {address}")
        return False

    who = run(["gcloud", "auth", "list", "--filter=status:ACTIVE",
               "--format=value(account)"]).stdout.strip()
    if not who:
        say("gcloud is installed but not signed in. A browser is required for this —")
        say("  run:  gcloud auth login")
        say(f"  then: py {Path(__file__).name} --setup-email {address}")
        return False
    say(f"Signed in to gcloud as {who}")

    project = project_id or f"agent-orch-{address.split('@')[0][:12].replace('.', '-')}"
    say(f"Using GCP project: {project}")
    r = run(["gcloud", "projects", "describe", project])
    if r.returncode != 0:
        say("  creating it...")
        r = run(["gcloud", "projects", "create", project, "--name=Agent Orchestration"])
        if r.returncode != 0:
            say(f"  could not create project: {r.stderr.strip()[:200]}")
            say("  (a billing account or org policy may be required)")
            return False

    run(["gcloud", "services", "enable", "gmail.googleapis.com", f"--project={project}"])
    sa = f"agent-orch-notifier@{project}.iam.gserviceaccount.com"
    if run(["gcloud", "iam", "service-accounts", "describe", sa,
            f"--project={project}"]).returncode != 0:
        run(["gcloud", "iam", "service-accounts", "create", "agent-orch-notifier",
             "--display-name=Agent Orchestration Notifier", f"--project={project}"])

    runtime = Path.home() / ".stale-work"
    runtime.mkdir(exist_ok=True)
    key = runtime / "sa-key.json"
    if not key.exists():
        r = run(["gcloud", "iam", "service-accounts", "keys", "create", str(key),
                 f"--iam-account={sa}", f"--project={project}"])
        if r.returncode != 0:
            say(f"  could not create a key: {r.stderr.strip()[:200]}")
            return False

    client_id = run(["gcloud", "iam", "service-accounts", "describe", sa,
                     f"--project={project}", "--format=value(uniqueId)"]).stdout.strip()

    (runtime / "notify.env").write_text(
        "# Written by orchestration_setup.py. Never commit this file.\n"
        f"NOTIFY_EMAIL={address}\n"
        f"GMAIL_SA_KEY={key}\n"
        f"SEND_AS={address}\n", encoding="utf-8")

    cfg = ocfg.load(); cfg["notify_email"] = address; ocfg.save(cfg)

    say("")
    say("Service account ready. ONE STEP REMAINS, and only a human can do it —")
    say("Google exposes no API for authorising a service account to send as a user.")
    say("")
    say("  1. Open  admin.google.com  (you need Workspace admin on this domain)")
    say("  2. Security > Access and data control > API controls")
    say("  3. Domain-wide delegation > Add new")
    say(f"     Client ID:    {client_id}")
    say("     OAuth scope:  https://www.googleapis.com/auth/gmail.send")
    say("  4. Save, wait a few minutes, then test:")
    say(f"     py {TOOLS}\\workboard_staleness_check.py --email")
    say("")
    say("No Workspace admin? Skip email entirely — everything else works without it.")
    return True


# --------------------------------------------------------------------------
def status():
    say(ocfg.describe())
    say("")
    if IS_WINDOWS:
        r = run(["schtasks", "/Query", "/TN", TASK_NAME])
        say("daily job    : " + ("registered" if r.returncode == 0 else "NOT registered"))
    else:
        out = run(["crontab", "-l"]).stdout
        say("daily job    : " + ("registered" if "worktree_sweeper" in out else "NOT registered"))
    creds = Path.home() / ".stale-work" / "notify.env"
    say("email        : " + ("configured" if creds.is_file() else "not configured (optional)"))


def main():
    ap = argparse.ArgumentParser(description="Set up the worktree orchestration system.")
    ap.add_argument("--roots", nargs="*", help="Folder(s) containing your git repos.")
    ap.add_argument("--mode", choices=["report", "apply"],
                    help="report = describe only (default for new installs); apply = act.")
    ap.add_argument("--schedule", action="store_true", help="Register the daily sweep.")
    ap.add_argument("--hour", type=int, default=6)
    ap.add_argument("--setup-email", metavar="ADDRESS", help="Configure the daily email.")
    ap.add_argument("--gcp-project", default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--non-interactive", action="store_true")
    args = ap.parse_args()

    if args.status:
        status(); return 0

    interactive = not args.non_interactive and sys.stdin.isatty()
    did_something = False

    if args.roots or args.mode or interactive:
        configure(args.roots, args.mode, interactive); did_something = True
    if args.schedule:
        say(""); schedule_daily(args.hour); did_something = True
    if args.setup_email:
        say(""); setup_email(args.setup_email, args.gcp_project); did_something = True

    if not did_something:
        ap.print_help(); return 1

    say(""); say("Next:")
    say("  py " + str(TOOLS / "worktree_sweeper.py") + "        # see what it would do")
    say("  py " + str(TOOLS / "orchestration_setup.py") + " --status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
