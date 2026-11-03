# SO-101 framework installer (Windows PowerShell 5.1+).
# Usage:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1) Python 3.10-3.12 (3.12 tested). Prefer the py launcher.
$py = $null
foreach ($v in "3.12", "3.11", "3.10") {
    try { & py -$v -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
}
if (-not $py) { throw "Python 3.10-3.12 not found. Install Python 3.12 from python.org (tick 'Add to PATH')." }
Write-Host "Using Python $($py -join ' ')"

# 2) Virtual environment inside the framework folder
if (-not (Test-Path .venv)) { & $py[0] $py[1] -m venv .venv }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt
& $venvPy -m pip install -e .

# 3) Health check (no motion). Plug in the arm (USB + power) and the camera first.
Write-Host "`nRunning: so101 doctor`n"
& (Join-Path $PSScriptRoot ".venv\Scripts\so101.exe") doctor
Write-Host "`nActivate later with:  .\.venv\Scripts\Activate.ps1"
