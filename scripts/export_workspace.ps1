# Pack everything git does not carry (secrets, local config, uploads, database, Claude memory) into one zip
# so the workspace can move to another Windows PC. Run import_workspace.ps1 on the new PC from a fresh clone.
#
# The zip holds: repo/ (untracked and ignored files), dirty/<branch>/ (uncommitted files of other worktrees),
# db/<name>.dump (pg_dump -Fc of each database in smallie-db), claude/ (parts of ~\.claude) and manifest.json.
# It contains secrets (.env): never upload it anywhere public.
#
# Usage (from the old PC):
#   powershell -ExecutionPolicy Bypass -File scripts\export_workspace.ps1 [-Repo <dir>] [-Out <zip>] [-IncludeTranscripts] [-SkipDb]

param(
    [string]$Repo = (Join-Path $PSScriptRoot ".."),
    [string]$Out,
    [switch]$IncludeTranscripts,
    [switch]$SkipDb
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.IO.Compression.FileSystem

$distro = "Ubuntu"
$Repo = (Resolve-Path -LiteralPath $Repo).Path.TrimEnd("\")
$claudeHome = Join-Path $env:USERPROFILE ".claude"
if (-not $Out) { $Out = Join-Path (Split-Path -Parent $Repo) ("smallie-migrate-{0}.zip" -f (Get-Date -Format "yyyyMMdd")) }
$Out = [System.IO.Path]::GetFullPath($Out)

# Folder name Claude Code uses under ~\.claude\projects for a project path (import uses the same rule).
function Get-ClaudeSlug([string]$path) {
    $s = $path -replace '[:\\/ ]', '-'
    if ($s.Length -gt 0) { $s = $s.Substring(0, 1).ToLower() + $s.Substring(1) }
    return $s
}

# Path as WSL sees it: C:\a b\c -> /mnt/c/a b/c
function Get-WslPath([string]$path) {
    $full = [System.IO.Path]::GetFullPath($path)
    return "/mnt/" + $full.Substring(0, 1).ToLower() + $full.Substring(2).Replace("\", "/")
}

# Output of a git -z command as a list of strings.
function Split-Z($lines) {
    $text = (@($lines) -join "`n")
    return @($text -split "`0" | Where-Object { $_ -ne "" })
}

function Copy-Rel([string]$srcRoot, [string]$rel, [string]$dstRoot) {
    $src = Join-Path $srcRoot $rel
    if (-not (Test-Path -LiteralPath $src)) { return }
    $dst = Join-Path $dstRoot $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    if (Test-Path -LiteralPath $src -PathType Container) {
        Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
    } else {
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
}

# --- git facts ---
$worktrees = @()
$cur = $null
foreach ($line in (git -C $Repo worktree list --porcelain)) {
    if ($line -like "worktree *") {
        $cur = [ordered]@{ path = $line.Substring(9).Replace("/", "\"); branch = $null; head = $null }
        $worktrees += , $cur
    } elseif ($line -like "HEAD *") { $cur.head = $line.Substring(5)
    } elseif ($line -like "branch *") { $cur.branch = $line.Substring(7) -replace '^refs/heads/', '' }
}
if ($LASTEXITCODE -ne 0) { Write-Error "git worktree list failed in $Repo" }
$mainPath = $worktrees[0].path
$slug = Get-ClaudeSlug $mainPath
$mainCommit = (git -C $mainPath rev-parse main) -join ""

$branches = @()
foreach ($line in (git -C $Repo for-each-ref --format="%(refname:short)|%(upstream:short)" refs/heads)) {
    $name, $up = $line -split "\|", 2
    $ahead = $null; $behind = $null
    if ($up) {
        $counts = ((git -C $Repo rev-list --left-right --count "$name...$up") -join "") -split "\s+"
        $ahead = [int]$counts[0]; $behind = [int]$counts[1]
    }
    $branches += [ordered]@{ name = $name; upstream = $up; ahead = $ahead; behind = $behind }
}

$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("smallie-export-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    # --- 1. repo/: untracked and ignored files ---
    $skip = '(^|/)(\.venv|__pycache__|\.pytest_cache|\.codegraph|\.langgraph_api)(/|$)|^\.claude/worktrees(/|$)'
    $files = @()
    $files += Split-Z (git -C $Repo -c core.quotepath=false ls-files -z --others --exclude-standard)
    $files += Split-Z (git -C $Repo -c core.quotepath=false ls-files -z --others --ignored --exclude-standard)
    $files = $files | Where-Object { $_ -notmatch $skip } | Sort-Object -Unique
    Write-Host ("repo/: {0} untracked or ignored files" -f @($files).Count)
    foreach ($f in $files) { Copy-Rel $Repo ($f.TrimEnd("/").Replace("/", "\")) (Join-Path $stage "repo") }

    # --- 2. dirty/: uncommitted files of the other worktrees ---
    $dirtySaved = @()
    foreach ($wt in $worktrees) {
        if ($wt.path -ieq $Repo) { continue }
        if (-not (Test-Path -LiteralPath $wt.path)) { continue }
        $entries = Split-Z (git -C $wt.path -c core.quotepath=false status --porcelain -uall -z)
        if (@($entries).Count -eq 0) { continue }
        $label = if ($wt.branch) { $wt.branch -replace '/', '-' } else { "detached-" + $wt.head.Substring(0, 7) }
        $dst = Join-Path $stage ("dirty\" + $label)
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        $skipNext = $false
        foreach ($e in $entries) {
            if ($skipNext) { $skipNext = $false; continue }   # old name of a rename or copy
            if ($e.Length -lt 4) { continue }
            if ($e[0] -eq "R" -or $e[0] -eq "C") { $skipNext = $true }
            Copy-Rel $wt.path $e.Substring(3).Replace("/", "\") $dst
        }
        $dirtySaved += $label
    }

    # --- 3. db/: one dump per database ---
    $dbNames = @()
    if (-not $SkipDb) {
        & (Join-Path $Repo "scripts\dev_db.ps1")
        if ($LASTEXITCODE -ne 0) { Write-Error "dev_db.ps1 failed" }
        $dbNames = @(wsl -d $distro -u root -- docker exec smallie-db psql -U smallie -d postgres -At -c "select datname from pg_database where not datistemplate and datname <> 'postgres'" | Where-Object { $_ -ne "" })
        if ($LASTEXITCODE -ne 0) { Write-Error "Listing the databases failed." }
        New-Item -ItemType Directory -Force -Path (Join-Path $stage "db") | Out-Null
        foreach ($db in $dbNames) {
            $target = Get-WslPath (Join-Path $stage "db\$db.dump")
            wsl -d $distro -u root -- sh -c "docker exec smallie-db pg_dump -U smallie -Fc $db > '$target'"
            if ($LASTEXITCODE -ne 0) { Write-Error "pg_dump of $db failed." }
            Write-Host "db/$db.dump written"
        }
    }

    # --- 4. claude/ ---
    $cdst = Join-Path $stage "claude"
    New-Item -ItemType Directory -Force -Path $cdst | Out-Null
    foreach ($n in @("CLAUDE.md", "settings.json", ".i-have-adhd-always", ".ponytail-active")) { Copy-Rel $claudeHome $n $cdst }
    foreach ($p in (Get-ChildItem -LiteralPath $claudeHome -Filter "*.ps1" -File -Force)) { Copy-Rel $claudeHome $p.Name $cdst }
    foreach ($n in @("skills", "plugins")) { Copy-Rel $claudeHome $n $cdst }
    $proj = "projects\$slug"
    if ($IncludeTranscripts) {
        Copy-Rel $claudeHome $proj $cdst
    } else {
        Copy-Rel $claudeHome "$proj\memory" $cdst
    }

    # --- 5. manifest.json ---
    $manifest = [ordered]@{
        exportedAt  = (Get-Date).ToString("o")
        sourceRepo  = $mainPath
        sourceUser  = $env:USERPROFILE
        sourceSlug  = $slug
        mainCommit  = $mainCommit
        branches    = $branches
        databases   = $dbNames
    }
    [System.IO.File]::WriteAllText((Join-Path $stage "manifest.json"), ($manifest | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))

    # --- warnings ---
    $lost = $branches | Where-Object { -not $_.upstream -or $_.ahead -gt 0 }
    if ($lost) {
        Write-Warning "Local branches that will not reach the new PC through git (no upstream, or ahead of it):"
        foreach ($b in $lost) {
            $why = if ($b.upstream) { "ahead $($b.ahead) of $($b.upstream)" } else { "no upstream" }
            Write-Host "  $($b.name): $why"
        }
    }
    if ($dirtySaved.Count -gt 0) {
        Write-Warning "Worktrees with uncommitted changes, saved under dirty/ in the zip:"
        foreach ($d in $dirtySaved) { Write-Host "  $d" }
    }

    # --- zip ---
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out) | Out-Null
    if (Test-Path -LiteralPath $Out) { Remove-Item -LiteralPath $Out -Force }
    # Built entry by entry: CreateFromDirectory on .NET Framework can write backslashes in entry names.
    $zip = [System.IO.Compression.ZipFile]::Open($Out, "Create")
    try {
        foreach ($f in (Get-ChildItem -LiteralPath $stage -Recurse -File -Force)) {
            $name = $f.FullName.Substring($stage.Length + 1).Replace("\", "/")
            [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $name, "Optimal")
        }
    } finally { $zip.Dispose() }
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}

$size = (Get-Item -LiteralPath $Out).Length
Write-Host ("Wrote {0} ({1:N1} MB)" -f $Out, ($size / 1MB))
Write-Host "This zip contains secrets (.env): do not upload it anywhere public."
