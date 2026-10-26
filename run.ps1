# ResumeMatch Coach - start the app (Windows PowerShell)
#
# One process, one port: FastAPI serves the built React UI itself, so there is
# no second dev server to keep alive. For hot reload while developing the UI,
# run `npm run dev` in frontend-react/ instead (port 5173, API proxied to 8000).
#
#   .\run.ps1          start on http://127.0.0.1:8000
#   .\run.ps1 -Check   probe the providers first, then start

param([switch]$Check)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "No virtual environment found. Creating one..." -ForegroundColor Yellow
    python -m venv .venv
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r requirements.txt --quiet
    Write-Host "Dependencies installed." -ForegroundColor Green
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - add your API keys." -ForegroundColor Yellow
    Write-Host "(The app still runs with no keys, using the local keyword fallback.)"
}

# Build the React UI if it has never been built, or if -Build was passed.
$dist = Join-Path $PSScriptRoot "frontend-react\dist\index.html"
if ($Build -or -not (Test-Path $dist)) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location (Join-Path $PSScriptRoot "frontend-react")
        if (-not (Test-Path "node_modules")) {
            Write-Host "Installing React dependencies..." -ForegroundColor Yellow
            npm install --silent
        }
        Write-Host "Building the React UI..." -ForegroundColor Yellow
        npm run build
        Pop-Location
    } else {
        Write-Host "npm not found - using the plain HTML UI instead." -ForegroundColor Yellow
    }
}

if ($Check) {
    Write-Host ""
    & $python scripts\check_providers.py
}

# Loopback only. Binding 0.0.0.0 would expose your analysis history to the
# whole local network - see DESIGN.md s17.
Write-Host ""
Write-Host "  ResumeMatch Coach  ->  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "  API docs           ->  http://127.0.0.1:8000/docs" -ForegroundColor DarkGray
Write-Host "  Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

& $python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
