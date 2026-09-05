# Sentinel-G — full stack startup
# Usage: .\start.ps1
# Starts Neo4j (Docker), FastAPI backend, and Vite frontend.

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot

# ── helpers ───────────────────────────────────────────────────────────────────
function Kill-Port($port) {
    $pid = (netstat -ano | Select-String ":$port " | Select-String "LISTENING" |
            ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -First 1)
    if ($pid) {
        taskkill /F /PID $pid 2>$null | Out-Null
    }
}

# ── 1. Docker check ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[ 1/6 ] Checking Docker Desktop..." -ForegroundColor Cyan
$dockerOk = $false
try { docker info 2>$null | Out-Null; $dockerOk = $true } catch {}
if (-not $dockerOk) {
    Write-Host "ERROR: Docker Desktop is not running." -ForegroundColor Red
    Write-Host "       Start it from the taskbar, wait for the whale icon to stop" -ForegroundColor Red
    Write-Host "       animating, then run .\start.ps1 again." -ForegroundColor Red
    exit 1
}
Write-Host "       Docker is running." -ForegroundColor Green

# ── 2. Start Neo4j ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[ 2/6 ] Starting Neo4j container..." -ForegroundColor Cyan
docker compose -f "$ROOT\infra\docker-compose.dev.yml" up -d
if ($LASTEXITCODE -ne 0) { Write-Host "docker compose failed." -ForegroundColor Red; exit 1 }

# ── 3. Wait for Bolt ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[ 3/6 ] Waiting for Neo4j to be ready..." -ForegroundColor Cyan
Write-Host "        First run: ~10 min (GDS plugin download + load)." -ForegroundColor DarkGray
Write-Host "        Subsequent runs: ~30 s (plugins cached in .neo4j-plugins/)." -ForegroundColor DarkGray

$maxWait = 720  # 12 minutes
$waited  = 0
$ready   = $false
while ($waited -lt $maxWait) {
    $count = docker exec sentinel-g-neo4j sh -c `
        "grep -c 'Started\.' /var/lib/neo4j/logs/neo4j.log 2>/dev/null || echo 0" 2>$null
    if ([int]($count -replace '[^0-9]','') -ge 1) { $ready = $true; break }
    Start-Sleep -Seconds 10
    $waited += 10
    Write-Host "        ...still loading ($waited s elapsed)" -ForegroundColor DarkGray
}

if (-not $ready) {
    Write-Host "ERROR: Neo4j did not start within 12 minutes." -ForegroundColor Red
    Write-Host "       Run: docker logs sentinel-g-neo4j" -ForegroundColor Red
    exit 1
}
Write-Host "        Neo4j is ready." -ForegroundColor Green

# ── 4. Seed demo users ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[ 4/6 ] Seeding demo users (idempotent)..." -ForegroundColor Cyan
& "$ROOT\.venv\Scripts\python.exe" "$ROOT\scripts\seed_users.py"

# ── 5. Backend ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[ 5/6 ] Starting FastAPI backend on port 8000..." -ForegroundColor Cyan
Kill-Port 8000
$backendCmd = "Set-Location '$ROOT'; " +
    "Write-Host 'Sentinel-G Backend' -ForegroundColor Green; " +
    ".\.venv\Scripts\uvicorn backend.app.main:app " +
    "--host 0.0.0.0 --port 8000 --reload --env-file .env.local"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd

# Give uvicorn a few seconds to bind before starting the frontend
Start-Sleep -Seconds 5

# ── 6. Frontend ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[ 6/6 ] Starting Vite frontend on port 5173..." -ForegroundColor Cyan
Kill-Port 5173
$frontendCmd = "Set-Location '$ROOT\frontend'; " +
    "Write-Host 'Sentinel-G Frontend' -ForegroundColor Green; " +
    "npm run dev -- --host"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Sentinel-G is running" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Frontend   -> http://localhost:5173" -ForegroundColor Cyan
Write-Host "  Backend    -> http://localhost:8000" -ForegroundColor Cyan
Write-Host "  API docs   -> http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "  Neo4j UI   -> http://localhost:7474" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Demo logins  (password for all: Sentinel@1)" -ForegroundColor Yellow
Write-Host "    priya@demo.in      credit_officer" -ForegroundColor Yellow
Write-Host "    rajan@demo.in      investigator" -ForegroundColor Yellow
Write-Host "    deepa@demo.in      auditor" -ForegroundColor Yellow
Write-Host "    amir@demo.in       admin" -ForegroundColor Yellow
Write-Host ""
Write-Host "  To stop Neo4j:" -ForegroundColor DarkGray
Write-Host "    docker compose -f infra\docker-compose.dev.yml down" -ForegroundColor DarkGray
Write-Host ""
