# Start the local Postgres (Docker inside WSL) and keep WSL running until the PC shuts down.
#
# WSL stops about 25 s after the last `wsl` command ends, even while Docker runs, and takes the
# database with it: DB tests are skipped, or freeze mid-run on a connection that never answers.
# A hidden `sleep infinity` session keeps WSL up; `wsl --shutdown` ends it early.
#
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1

$ErrorActionPreference = "Stop"
$distro = "Ubuntu"
$marker = "smallie-keepalive"
$project = Split-Path -Parent $PSScriptRoot

$running = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" | Where-Object { $_.CommandLine -like "*$marker*" }
if ($running) {
    Write-Host "WSL keep-alive already running."
} else {
    Start-Process wsl.exe -WindowStyle Hidden -ArgumentList "-d $distro -u root -- sh -c 'sleep infinity # $marker'"
    Write-Host "Started the WSL keep-alive (runs until the PC shuts down or 'wsl --shutdown')."
}

wsl -d $distro -u root --cd $project -- docker compose up -d db
for ($i = 0; $i -lt 30; $i++) {
    $health = (wsl -d $distro -u root -- docker inspect -f "{{.State.Health.Status}}" smallie-db) -join ""
    if ($health -eq "healthy") {
        # Init scripts only run on a fresh volume; these are idempotent (the agent servers' databases
        # and their read-only role, for volumes created before they existed).
        wsl -d $distro -u root -- docker exec smallie-db psql -U smallie -d postgres -q -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/05_agent_databases.sql
        if ($LASTEXITCODE -ne 0) { Write-Error "Creating the agent databases failed (05_agent_databases.sql)." }
        wsl -d $distro -u root -- docker exec smallie-db psql -U smallie -d smallie -q -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/06_agent_reader.sql
        if ($LASTEXITCODE -ne 0) { Write-Error "Setting up agent_reader failed (06_agent_reader.sql)." }
        Write-Host "Postgres is ready on localhost:5433."
        exit 0
    }
    Start-Sleep -Seconds 1
}
Write-Error "smallie-db did not become healthy in 30 s; check: wsl -d $distro -u root -- docker logs smallie-db"
