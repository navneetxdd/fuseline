# Start the Fuseline API (auto-reload, :8000) and the Vite dev UI (:5173) on Windows.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
  Write-Host "Creating virtual environment..."
  $launcher = @("py", "python", "python3") | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
  if (-not $launcher) { throw "Python 3.11+ was not found on PATH." }
  & $launcher -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}

& $VenvPython -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Fuseline needs Python 3.11 or newer (found $(& $VenvPython --version))." }

& $VenvPython -m pip install -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Installing Python dependencies failed." }

if (-not (Test-Path ".\frontend\node_modules")) {
  Push-Location frontend
  npm install
  Pop-Location
}

# Project-local adb so Acquire → Detect device works without a global Android SDK
& $VenvPython .\scripts\ensure_platform_tools.py
if ($LASTEXITCODE -ne 0) { Write-Warning "Could not install bundled platform-tools; device import may be unavailable." }

Write-Host "Starting API on http://127.0.0.1:8000 and UI on http://127.0.0.1:5173 (Ctrl+C to stop)..."
$api = Start-Process -PassThru -NoNewWindow -FilePath $VenvPython -ArgumentList @(
  "-m", "uvicorn", "app.main:app", "--reload", "--reload-dir", "backend",
  "--host", "127.0.0.1", "--port", "8000", "--app-dir", "backend"
)
Push-Location frontend
try {
  npm run dev -- --host 127.0.0.1 --port 5173
} finally {
  if ($api -and -not $api.HasExited) {
    Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
  }
  Pop-Location
}
