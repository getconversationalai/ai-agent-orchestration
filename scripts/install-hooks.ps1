# install-hooks.ps1 - install guard-secrets + sync-allowlist validation as the
# git pre-commit hook. Hooks live in the MAIN clone's .git/hooks (shared by all
# worktrees) - run this script from the main clone, not a worktree.
$repoRoot = Split-Path -Parent $PSScriptRoot
$hookDir = Join-Path $repoRoot '.git/hooks'
if (-not (Test-Path -PathType Container (Join-Path $repoRoot '.git'))) {
  throw "run install-hooks.ps1 from the main clone (.git here is a worktree pointer file, not the hooks home)"
}
New-Item -ItemType Directory -Force -Path $hookDir | Out-Null
$hook = @'
#!/bin/sh
powershell -NoProfile -File scripts/guard-secrets.ps1 || {
  echo "Commit blocked by guard-secrets.ps1"; exit 1; }
powershell -NoProfile -File scripts/capture.ps1 -ValidateOnly || {
  echo "Commit blocked: non-allowlisted file in a synced dir (see sync-allowlist.txt)"; exit 1; }
'@
Set-Content -Path (Join-Path $hookDir 'pre-commit') -Value $hook -NoNewline -Encoding ASCII
Write-Host "pre-commit hook installed."
