#!/usr/bin/env python3
"""parallel_speedup_report.py — did the parallel-work fixes make agents faster?

Measures the RIGHT metrics (not raw "parallelism factor", which stays ~1.0 even
when the fix works, because the biggest win is chains running INLINE instead of
as a slow sequential-subagent conveyor — that REMOVES subagents rather than
parallelizing them):

  - subagent DISPATCHES per session      -> should FALL (chains go inline)
  - subagent OVERHEAD hours per session  -> should FALL
  - peak concurrency / % ever-overlapped -> should RISE when work IS independent

Filters by session START time (--since), so you can isolate sessions that ran
UNDER the new model from the pre-fix baseline. Compares to the measured pre-fix
baseline and emails the result (reusing ~/.stale-work/notify.env, like the daily
staleness job).

Usage:
  py parallel_speedup_report.py --since 2026-08-05
  py parallel_speedup_report.py --since 2026-08-05 --email
  py parallel_speedup_report.py --json
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECTS = os.path.expanduser("~/.claude/projects")

# Pre-fix baseline, measured over the ~9 days before the Aug-4 deploys
# (concurrency probe: 25 sessions-with-subagents, 1300 dispatches, 122.1h).
BASELINE = {
    "dispatches_per_session": 52.0,   # 1300 / 25
    "overhead_h_per_session": 4.9,    # 122.1h / 25
    "avg_parallelism": 1.09,
    "pct_never_overlapped": 64.0,
}


def _parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ---- pure metric core (unit-tested) ----------------------------------------

def session_metrics(intervals):
    """intervals: list of (start_dt, end_dt) for one session's subagent dispatches."""
    if not intervals:
        return {"dispatches": 0, "overhead_s": 0.0, "parallelism": 1.0, "peak": 0}
    summ = sum((e - s).total_seconds() for s, e in intervals)
    iv = sorted(intervals)
    union = 0.0
    cs, ce = iv[0]
    for s, e in iv[1:]:
        if s <= ce:
            ce = max(ce, e)
        else:
            union += (ce - cs).total_seconds()
            cs, ce = s, e
    union += (ce - cs).total_seconds()
    events = []
    for s, e in intervals:
        events += [(s, 1), (e, -1)]
    events.sort(key=lambda z: (z[0], z[1]))
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return {"dispatches": len(intervals), "overhead_s": summ,
            "parallelism": (summ / union) if union else 1.0, "peak": peak}


def aggregate(per_session):
    """per_session: list of session_metrics dicts (sessions that dispatched >=1)."""
    n = len(per_session)
    if n == 0:
        return {"sessions_with_subagents": 0}
    disp = sum(m["dispatches"] for m in per_session)
    overhead_h = sum(m["overhead_s"] for m in per_session) / 3600.0
    never = sum(1 for m in per_session if m["peak"] <= 1)
    return {
        "sessions_with_subagents": n,
        "total_dispatches": disp,
        "total_overhead_h": overhead_h,
        "dispatches_per_session": disp / n,
        "overhead_h_per_session": overhead_h / n,
        "avg_parallelism": sum(m["parallelism"] for m in per_session) / n,
        "pct_never_overlapped": 100.0 * never / n,
    }


# ---- transcript scan (I/O) --------------------------------------------------

def scan(root, since):
    """Return per-session metrics for top-level sessions STARTED on/after `since`."""
    out = []
    files = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        files.extend(glob.glob(os.path.join(d, "*.jsonl")))
    for f in files:
        if os.path.basename(f).startswith("agent-"):
            continue  # skip sub-agent sidechain transcripts
        first = None
        pending = {}
        intervals = []
        try:
            for line in open(f, encoding="utf-8", errors="replace"):
                try:
                    x = json.loads(line)
                except Exception:
                    continue
                ts = _parse_ts(x.get("timestamp"))
                if ts and first is None:
                    first = ts
                msg = x.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for it in content:
                    if not isinstance(it, dict):
                        continue
                    if it.get("type") == "tool_use" and it.get("name") in ("Task", "Agent") and it.get("id") and ts:
                        pending[it["id"]] = ts
                    elif it.get("type") == "tool_result":
                        st = pending.pop(it.get("tool_use_id"), None)
                        if st and ts and ts > st:
                            dur = (ts - st).total_seconds()
                            if 0 < dur <= 3600:  # ignore idle-gap artifacts
                                intervals.append((st, ts))
        except Exception:
            continue
        if since and (first is None or first < since):
            continue
        if intervals:
            out.append(session_metrics(intervals))
    return out


# ---- report -----------------------------------------------------------------

def _delta(cur, base, lower_is_better=True):
    if base == 0:
        return "n/a"
    pct = 100.0 * (cur - base) / base
    good = (pct < 0) if lower_is_better else (pct > 0)
    arrow = "v" if pct < 0 else "^"
    tag = " (better)" if good else (" (worse)" if abs(pct) > 5 else "")
    return f"{arrow}{abs(pct):.0f}%{tag}"


def format_report(summ, since):
    if summ.get("sessions_with_subagents", 0) == 0:
        return (f"PARALLEL-WORK SPEEDUP REPORT\nWindow: sessions started >= {since.date() if since else 'all'}\n\n"
                "No post-fix sessions dispatched subagents yet — not enough data. "
                "Re-check once more sessions have run under the new model.")
    L = []
    L.append("PARALLEL-WORK SPEEDUP REPORT")
    L.append(f"Window: sessions STARTED on/after {since.date() if since else 'all-time'}")
    L.append(f"Sessions that dispatched subagents: {summ['sessions_with_subagents']}")
    L.append("")
    L.append("The real scoreboard (vs pre-fix baseline):")
    L.append(f"  subagent overhead / session : {summ['overhead_h_per_session']:.2f}h   "
             f"(baseline {BASELINE['overhead_h_per_session']:.2f}h)  {_delta(summ['overhead_h_per_session'], BASELINE['overhead_h_per_session'])}")
    L.append(f"  dispatches / session        : {summ['dispatches_per_session']:.1f}    "
             f"(baseline {BASELINE['dispatches_per_session']:.0f})    {_delta(summ['dispatches_per_session'], BASELINE['dispatches_per_session'])}")
    L.append("")
    L.append("Parallelism (rises only when work is genuinely independent):")
    L.append(f"  avg parallelism factor      : {summ['avg_parallelism']:.2f}x  (baseline {BASELINE['avg_parallelism']:.2f}x)  {_delta(summ['avg_parallelism'], BASELINE['avg_parallelism'], lower_is_better=False)}")
    L.append(f"  sessions never overlapping  : {summ['pct_never_overlapped']:.0f}%   (baseline {BASELINE['pct_never_overlapped']:.0f}%)  {_delta(summ['pct_never_overlapped'], BASELINE['pct_never_overlapped'])}")
    L.append("")
    L.append("Read it thus: the fix mainly turns slow sequential-subagent chains into")
    L.append("INLINE work, so OVERHEAD/SESSION falling is the primary win; parallelism")
    L.append("rising is the secondary win on genuinely-independent work.")
    if summ['sessions_with_subagents'] < 10:
        L.append(f"\n(Only {summ['sessions_with_subagents']} sessions — treat as a first read, not proof.)")
    return "\n".join(L)


# ---- email (self-contained; mirrors the daily staleness sender) -------------

def _load_creds():
    creds = {}
    p = Path.home() / ".stale-work" / "notify.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def send_email(subject, body):
    creds = _load_creds()
    to_addr = creds.get("NOTIFY_EMAIL")
    if not to_addr:
        print("NOTE: no NOTIFY_EMAIL in ~/.stale-work/notify.env — email not sent.", file=sys.stderr)
        return False
    sa_key = creds.get("GMAIL_SA_KEY")
    if sa_key and Path(sa_key).exists():
        try:
            import base64, urllib.request
            from email.mime.text import MIMEText
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request as GRequest
            send_as = creds.get("SEND_AS") or creds.get("FROM_EMAIL") or to_addr
            sa = service_account.Credentials.from_service_account_file(
                sa_key, scopes=["https://www.googleapis.com/auth/gmail.send"], subject=send_as)
            sa.refresh(GRequest())
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = subject, send_as, to_addr
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            req = urllib.request.Request(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                data=json.dumps({"raw": raw}).encode(), method="POST",
                headers={"Authorization": f"Bearer {sa.token}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                if 200 <= r.status < 300:
                    print(f"Emailed report to {to_addr} (Gmail).")
                    return True
        except Exception as e:
            print(f"Gmail send failed: {e}", file=sys.stderr)
    if creds.get("RESEND_API_KEY"):
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=json.dumps({"from": creds.get("FROM_EMAIL", "onboarding@resend.dev"),
                                 "to": [to_addr], "subject": subject, "text": body}).encode(),
                method="POST",
                headers={"Authorization": f"Bearer {creds['RESEND_API_KEY']}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if 200 <= r.status < 300:
                    print(f"Emailed report to {to_addr} (Resend).")
                    return True
        except Exception as e:
            print(f"Resend send failed: {e}", file=sys.stderr)
    if creds.get("SMTP_HOST") and creds.get("SMTP_USER") and creds.get("SMTP_PASS"):
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = subject, creds["SMTP_USER"], to_addr
            with smtplib.SMTP(creds["SMTP_HOST"], int(creds.get("SMTP_PORT", 587)), timeout=20) as s:
                s.starttls(); s.login(creds["SMTP_USER"], creds["SMTP_PASS"]); s.send_message(msg)
            print(f"Emailed report to {to_addr} (SMTP).")
            return True
        except Exception as e:
            print(f"SMTP send failed: {e}", file=sys.stderr)
    print("NOTE: no working email transport configured in notify.env.", file=sys.stderr)
    return False


def main():
    ap = argparse.ArgumentParser(description="Parallel-work speedup report (corrected metrics).")
    ap.add_argument("--since", help="YYYY-MM-DD (UTC): only sessions STARTED on/after this date")
    ap.add_argument("--email", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--projects-dir", default=PROJECTS)
    args = ap.parse_args()
    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    summ = aggregate(scan(os.path.expanduser(args.projects_dir), since))
    if args.json:
        print(json.dumps(summ, indent=2, default=str))
        return
    report = format_report(summ, since)
    print(report)
    if args.email:
        n = summ.get("sessions_with_subagents", 0)
        send_email(f"[dev] Parallel-work speedup — {n} sessions", report)


if __name__ == "__main__":
    main()
