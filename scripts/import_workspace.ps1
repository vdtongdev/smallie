# Unpack a zip made by export_workspace.ps1 into this fresh clone and set the workspace up on a new PC.
#
# Copies the untracked files (.env, uploads, local config) into the repo without touching tracked files, parks
# other worktrees' uncommitted files in .migrate\dirty\, restores the Claude home pieces and memory, runs
# `uv sync`, and restores the databases into the Docker Postgres (WSL) before starting the compose stack.
#
# Usage (from the new PC, in the fresh clone):
#   powershell -ExecutionPolicy Bypass -File scripts\import_workspace.ps1 -Zip <zip> [-SkipDb] [-SkipClaude] [-SkipSetup] [-ClaudeHome <dir>]

param(
    [Parameter(Mandatory = $true)][string]$Zip,
    [switch]$SkipDb,
    [switch]$SkipClaude,
    [switch]$SkipSetup,
    [string]$ClaudeHome = (Join-Path $env:USERPROFILE ".claude")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.IO.Compression.FileSystem

$distro = "Ubuntu"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path.TrimEnd("\")
$Zip = (Resolve-Path -LiteralPath $Zip).Path

# Folder name Claude Code uses under ~\.claude\projects for a project path (export uses the same rule).
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

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("smallie-import-" + [guid]::NewGuid().ToString("N"))
try {
    # --- 1. unpack ---
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $tmp)
    $manifest = Get-Content -LiteralPath (Join-Path $tmp "manifest.json") -Raw | ConvertFrom-Json
    Write-Host "Zip from $($manifest.sourceRepo), exported $($manifest.exportedAt)"

    # --- 2. repo/: never overwrite a tracked file ---
    $tracked = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $lines = git -C $repo -c core.quotepath=false ls-files -z
    if ($LASTEXITCODE -ne 0) { Write-Error "git ls-files failed in $repo (run this from a git clone)." }
    foreach ($t in (((@($lines) -join "`n") -split "`0") | Where-Object { $_ -ne "" })) { [void]$tracked.Add($t.Replace("/", "\")) }
    $src = Join-Path $tmp "repo"
    $copied = 0; $skippedTracked = 0
    if (Test-Path -LiteralPath $src) {
        foreach ($f in (Get-ChildItem -LiteralPath $src -Recurse -File -Force)) {
            $rel = $f.FullName.Substring($src.Length + 1)
            if ($tracked.Contains($rel)) { $skippedTracked++; continue }
            $dst = Join-Path $repo $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -LiteralPath $f.FullName -Destination $dst -Force
            $copied++
        }
    }
    Write-Host "repo: copied $copied files, left $skippedTracked tracked files alone"

    # --- 3. dirty/ ---
    $dirtySrc = Join-Path $tmp "dirty"
    if (Test-Path -LiteralPath $dirtySrc) {
        $dirtyDst = Join-Path $repo ".migrate\dirty"
        New-Item -ItemType Directory -Force -Path $dirtyDst | Out-Null
        Copy-Item -Path (Join-Path $dirtySrc "*") -Destination $dirtyDst -Recurse -Force
        $exclude = (git -C $repo rev-parse --path-format=absolute --git-path info/exclude) -join ""
        if ($LASTEXITCODE -ne 0) { Write-Error "git rev-parse --git-path failed." }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $exclude) | Out-Null
        $have = @()
        if (Test-Path -LiteralPath $exclude) { $have = Get-Content -LiteralPath $exclude }
        if ($have -notcontains ".migrate/") { Add-Content -LiteralPath $exclude -Value ".migrate/" }
        Write-Host "Uncommitted files of these branches (folder names, '/' written as '-') wait in .migrate\dirty\:"
        foreach ($d in (Get-ChildItem -LiteralPath $dirtyDst -Directory)) { Write-Host "  $($d.Name)" }
    }

    # --- 4. claude/ ---
    $csrc = Join-Path $tmp "claude"
    if (-not $SkipClaude -and (Test-Path -LiteralPath $csrc)) {
        New-Item -ItemType Directory -Force -Path $ClaudeHome | Out-Null
        $stamp = Get-Date -Format "yyyyMMddHHmmss"
        foreach ($n in @("CLAUDE.md", "settings.json")) {
            $existing = Join-Path $ClaudeHome $n
            if ((Test-Path -LiteralPath $existing) -and (Test-Path -LiteralPath (Join-Path $csrc $n))) {
                Copy-Item -LiteralPath $existing -Destination "$existing.bak-$stamp" -Force
            }
        }
        foreach ($item in (Get-ChildItem -LiteralPath $csrc -Force)) {
            if ($item.Name -eq "projects") { continue }
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $ClaudeHome $item.Name) -Recurse -Force
        }
        $newSlug = Get-ClaudeSlug $repo
        $projSrc = Join-Path $csrc ("projects\" + $manifest.sourceSlug)
        if (Test-Path -LiteralPath $projSrc) {
            $projDst = Join-Path $ClaudeHome ("projects\" + $newSlug)
            New-Item -ItemType Directory -Force -Path $projDst | Out-Null
            Copy-Item -Path (Join-Path $projSrc "*") -Destination $projDst -Recurse -Force
            if ($newSlug -ne $manifest.sourceSlug) { Write-Host "Project memory moved from slug $($manifest.sourceSlug) to $newSlug" }
        }
        # Plugin files hold absolute paths of the old profile.
        $oldUser = [string]$manifest.sourceUser
        $newUser = $env:USERPROFILE
        if ($oldUser -and $oldUser -ne $newUser) {
            $utf8 = New-Object System.Text.UTF8Encoding($false)
            foreach ($n in @("installed_plugins.json", "known_marketplaces.json")) {
                $file = Join-Path $ClaudeHome "plugins\$n"
                if (-not (Test-Path -LiteralPath $file)) { continue }
                $text = [System.IO.File]::ReadAllText($file)
                $text = $text.Replace($oldUser.Replace("\", "\\"), $newUser.Replace("\", "\\")).Replace($oldUser, $newUser)
                [System.IO.File]::WriteAllText($file, $text, $utf8)
            }
        }
    }

    # --- 5. dependencies ---
    if (-not $SkipSetup) {
        Push-Location $repo
        try { uv sync; if ($LASTEXITCODE -ne 0) { Write-Error "uv sync failed." } } finally { Pop-Location }
    }

    # --- 6. databases ---
    $dumps = @()
    if (Test-Path -LiteralPath (Join-Path $tmp "db")) { $dumps = @(Get-ChildItem -LiteralPath (Join-Path $tmp "db") -Filter "*.dump") }
    if (-not $SkipDb) {
        & (Join-Path $PSScriptRoot "dev_db.ps1")
        if ($LASTEXITCODE -ne 0) { Write-Error "dev_db.ps1 failed" }
        foreach ($d in $dumps) {
            $db = $d.BaseName
            $exists = (wsl -d $distro -u root -- docker exec smallie-db psql -U smallie -d postgres -At -c "select 1 from pg_database where datname = '$db'") -join ""
            if ($LASTEXITCODE -ne 0) { Write-Error "Checking database $db failed." }
            if ($exists -ne "1") {
                wsl -d $distro -u root -- docker exec smallie-db createdb -U smallie $db
                if ($LASTEXITCODE -ne 0) { Write-Error "createdb $db failed." }
            }
            $wslDump = Get-WslPath $d.FullName
            wsl -d $distro -u root -- sh -c "docker exec -i smallie-db pg_restore --clean --if-exists --no-owner -U smallie -d $db < '$wslDump'"
            if ($LASTEXITCODE -ne 0) { Write-Warning "pg_restore of $db reported problems (exit $LASTEXITCODE); usually harmless warnings, check the output above." }
            else { Write-Host "Restored $db" }
        }
        wsl -d $distro -u root --cd $repo -- docker compose up -d --build
        if ($LASTEXITCODE -ne 0) { Write-Error "docker compose up -d --build failed." }
    }
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "What's left:"
Write-Host "  - Log in to Claude Code (credentials are not in the zip)."
Write-Host "  - Check .env values that name the old machine (paths, hosts, ports)."
Write-Host "  - Handle .migrate\dirty\ (uncommitted work of other branches): copy files into worktrees, then delete the folder."
Write-Host "  - Delete the zip when done: it holds secrets (.env)."
