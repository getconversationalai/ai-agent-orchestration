@echo off
REM Weekly parallel-work speedup report — Windows Task Scheduler (ParallelSpeedupWeekly, Mon 06:15).
REM Measures sessions STARTED on/after 2026-08-05 (the first clean day after the
REM Aug-4 parallel-work fixes deployed) and emails the corrected metrics
REM (overhead/session should fall; parallelism rises on independent work).
REM Uses %USERPROFILE% + the `py` launcher so it is path-independent.
set TOOLS=%USERPROFILE%\.ai-instructions\tools
set LOG=%USERPROFILE%\.stale-work\cron.log
if not exist "%USERPROFILE%\.stale-work" mkdir "%USERPROFILE%\.stale-work"
echo. >> "%LOG%"
echo ===== SPEEDUP %DATE% %TIME% ===== >> "%LOG%"
REM Write the latest report to a dedicated file (retrievable regardless of email),
REM AND attempt email (which starts working once the Gmail SA domain-wide
REM delegation is set up — the same fix the daily staleness email needs), AND
REM append to the shared cron log for history.
py "%TOOLS%\parallel_speedup_report.py" --since 2026-08-05 --email > "%USERPROFILE%\.stale-work\speedup-latest.txt" 2>&1
type "%USERPROFILE%\.stale-work\speedup-latest.txt" >> "%LOG%"
