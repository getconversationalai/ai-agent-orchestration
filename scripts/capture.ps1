# capture.ps1 - copy home -> repo per sync-manifest.txt (excludes secrets/caches)
#
# Guard: every recursively-synced directory is validated against sync-allowlist.txt
# BEFORE anything is copied. A file matching no allow pattern aborts the whole
# capture. This repo holds GLOBAL agent config only - project/session files (memory
# dumps, wrap-ups, notes) belong in their project's repo and must never be swept in.
#   Fix a violation by deleting the stray, or by adding an allowlist line if the
#   file genuinely belongs to this repo.
#
# Validation covers BOTH sides: the live home dirs (what a capture would sweep
# in) and the repo dirs (what a manual commit may have dropped in). The git
# pre-commit hook runs -ValidateOnly, so a stray cannot even be committed.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less scripts
# in the ANSI codepage, so UTF-8 punctuation (em-dashes etc.) breaks parsing.
#
# Switches:
#   -WhatIf        validate + print planned copies and orphans; change nothing
#   -ValidateOnly  validate and exit (used by the pre-commit hook)
#   -Prune         delete repo-side files whose live counterpart is gone
#                  (default: orphans are only reported)
param([switch]$WhatIf, [switch]$ValidateOnly, [switch]$Prune)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $repoRoot 'sync-manifest.txt'
$allowFile = Join-Path $repoRoot 'sync-allowlist.txt'
$xf = @('*.pyc','*.bak','settings.local.json','auth.json','oauth_creds.json','google_accounts.json','*.sqlite','*.sqlite-shm','*.sqlite-wal','history.jsonl','*.log','installation_id')
$xd = @('__pycache__','logs','sessions','tmp','.git')

function Test-Excluded([string]$rel) {
  # same scope as robocopy /XD /XF so validation, copy, and orphan checks agree
  $segs = $rel -split '/'
  if ($segs.Count -gt 1) {
    foreach ($d in $xd) { if ($segs[0..($segs.Count - 2)] -contains $d) { return $true } }
  }
  foreach ($f in $xf) { if ($segs[-1] -like $f) { return $true } }
  return $false
}

function Convert-GlobToRegex([string]$glob) {
  # * matches within one path segment, ** crosses segments; matching is anchored
  $ph = [string][char]1
  $rx = [regex]::Escape($glob)
  $rx = $rx.Replace('\*\*', $ph).Replace('\*', '[^/]*').Replace($ph, '.*').Replace('\?', '[^/]')
  return "^$rx$"
}

$entries = @()
foreach ($line in Get-Content $manifest) {
  $t = $line.Trim()
  if (-not $t -or $t.StartsWith('#')) { continue }
  $parts = $t -split '\|', 2
  $entries += [pscustomobject]@{
    RepoRel  = $parts[0].Trim()
    HomePath = ($parts[1].Trim()) -replace '^~', $HOME
  }
}
$dirEntries = @($entries | Where-Object { $_.RepoRel.EndsWith('/') })

if (-not (Test-Path $allowFile)) {
  throw "sync-allowlist.txt is missing - every recursively-synced directory must declare what belongs in it"
}
$allow = @{}
foreach ($line in Get-Content $allowFile) {
  $t = $line.Trim()
  if (-not $t -or $t.StartsWith('#')) { continue }
  $parts = $t -split '\|', 2
  if ($parts.Count -lt 2) { throw "sync-allowlist.txt: bad line (want '<dir> | <pattern>'): $t" }
  $dir = $parts[0].Trim()
  if (-not $allow.ContainsKey($dir)) { $allow[$dir] = @() }
  $allow[$dir] += Convert-GlobToRegex $parts[1].Trim()
}

# both directions must agree: no allowlist entries for unknown dirs (typos),
# and no synced dir without a declared policy (nothing syncs unvetted)
foreach ($dir in $allow.Keys) {
  if (-not ($dirEntries | Where-Object { $_.RepoRel -eq $dir })) {
    throw "sync-allowlist.txt covers '$dir', which is not a directory entry in sync-manifest.txt"
  }
}
foreach ($e in $dirEntries) {
  if (-not $allow.ContainsKey($e.RepoRel)) {
    throw "sync-manifest.txt syncs '$($e.RepoRel)' recursively but sync-allowlist.txt has no entries for it - declare what belongs there ('<dir> | **' only for a deliberately unrestricted dir)"
  }
}

# ---- validation pass: any stray on either side => nothing is copied ----
$violations = @()
foreach ($e in $dirEntries) {
  $roots = @(
    @{ Side = 'live'; Root = $e.HomePath },
    @{ Side = 'repo'; Root = (Join-Path $repoRoot $e.RepoRel) }
  )
  foreach ($r in $roots) {
    if (-not (Test-Path -LiteralPath $r.Root)) { continue }
    $base = (Get-Item -LiteralPath $r.Root).FullName
    foreach ($f in Get-ChildItem -LiteralPath $r.Root -Recurse -File -Force) {
      $rel = $f.FullName.Substring($base.Length).TrimStart('\','/') -replace '\\','/'
      if (Test-Excluded $rel) { continue }
      $ok = $false
      foreach ($rx in $allow[$e.RepoRel]) { if ($rel -match $rx) { $ok = $true; break } }
      if (-not $ok) { $violations += [pscustomobject]@{ Side = $r.Side; Rel = "$($e.RepoRel)$rel"; Path = $f.FullName } }
    }
  }
}
if ($violations.Count -gt 0) {
  Write-Host ""
  Write-Host "[capture] BLOCKED - file(s) not in sync-allowlist.txt; NOTHING was copied:" -ForegroundColor Red
  foreach ($v in $violations) { Write-Host ("  [{0}] {1}   ({2})" -f $v.Side, $v.Rel, $v.Path) -ForegroundColor Red }
  Write-Host "  This repo backs up GLOBAL agent config only. Project/session files belong in their project's repo."
  Write-Host "  -> stray [live]: delete it from the live path above"
  Write-Host "  -> stray [repo]: git rm it"
  Write-Host "  -> legitimate:   add a '<dir> | <pattern>' line to sync-allowlist.txt, then re-run"
  throw "capture aborted: $($violations.Count) non-allowlisted file(s)"
}
if ($ValidateOnly) { Write-Host "[capture] validation OK - all synced files are allowlisted."; exit 0 }

# ---- copy pass ----
foreach ($e in $entries) {
  $dst = Join-Path $repoRoot $e.RepoRel
  if ($e.RepoRel.EndsWith('/')) {
    if ($WhatIf) { Write-Host "[capture] DIR  $($e.HomePath) -> $dst"; continue }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    robocopy $e.HomePath $dst /E /XD $xd /XF $xf /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE) for $($e.HomePath)" }
  } else {
    if (-not (Test-Path $e.HomePath)) { Write-Host "[capture] SKIP (missing) $($e.HomePath)"; continue }
    if ($WhatIf) { Write-Host "[capture] FILE $($e.HomePath) -> $dst"; continue }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item -Force $e.HomePath $dst
  }
}

# ---- orphan pass: repo-side files whose live counterpart is gone ----
# robocopy /E never deletes, so live-side removals linger in the repo forever
# unless surfaced here. Reported by default; -Prune removes them (git shows the diff).
$orphans = @()
foreach ($e in $dirEntries) {
  $dst = Join-Path $repoRoot $e.RepoRel
  if (-not (Test-Path -LiteralPath $dst)) { continue }
  $base = (Get-Item -LiteralPath $dst).FullName
  foreach ($f in Get-ChildItem -LiteralPath $dst -Recurse -File -Force) {
    $rel = $f.FullName.Substring($base.Length).TrimStart('\','/') -replace '\\','/'
    if (Test-Excluded $rel) { continue }
    $live = Join-Path $e.HomePath ($rel -replace '/','\')
    if (-not (Test-Path -LiteralPath $live)) { $orphans += [pscustomobject]@{ Repo = $f.FullName; Rel = "$($e.RepoRel)$rel" } }
  }
}
if ($orphans.Count -gt 0) {
  Write-Host "[capture] orphans - in the repo but no longer in the live home dir:" -ForegroundColor Yellow
  foreach ($o in $orphans) {
    if ($Prune -and -not $WhatIf) {
      Remove-Item -LiteralPath $o.Repo -Force
      Write-Host "  pruned: $($o.Rel)" -ForegroundColor Yellow
    } else {
      Write-Host "  $($o.Rel)   (re-run with -Prune to delete, then commit)" -ForegroundColor Yellow
    }
  }
}
Write-Host "capture complete."
