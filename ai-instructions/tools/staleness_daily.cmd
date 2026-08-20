@echo off
REM Daily worktree hygiene — Windows Task Scheduler (StaleWorkDailyCheck, 06:00).
REM
REM Sweep, in order of increasing commitment:
REM   purge   node_modules from worktrees idle 7+ days (work untouched)
REM   save    uncommitted work idle 7+ days to wip/<branch> (refuses on secrets)
REM   remove  worktrees whose content already lives in the base branch
REM   retire  pushed+clean worktrees idle 14+ days (branch stays on origin)
REM Then email whatever still needs a human decision.
REM
REM No absolute user paths: %USERPROFILE% keeps this working for whoever installs
REM it, and `py` is the Windows launcher (always on PATH when Python is installed).
set RUNTIME=%USERPROFILE%\.stale-work
set TOOLS=%USERPROFILE%\.ai-instructions\tools
set LOG=%RUNTIME%\cron.log
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
echo. >> "%LOG%"
echo ===== %DATE% %TIME% ===== >> "%LOG%"
py "%TOOLS%\worktree_sweeper.py" --apply --autosave >> "%LOG%" 2>&1
REM Reconcile each repo's board from git (register in-flight branches, complete merged/gone,
REM refresh timestamps) before the staleness report reads it. Inert until armed
REM (~/.stale-work/reconcile-initialized); --apply only previews until then.
py "%TOOLS%\workboard_reconcile.py" --apply >> "%LOG%" 2>&1
py "%TOOLS%\workboard_staleness_check.py" --email --days 7 --no-cache >> "%LOG%" 2>&1
