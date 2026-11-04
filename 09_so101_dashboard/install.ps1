# SO-101 dashboard installer (Windows PowerShell 5.1+).
# Usage:  powershell -ExecutionPolicy Bypass -File install.ps1
# Installs into the framework's virtual environment (run ..\08_so101_framework\install.ps1 first).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot "..\08_so101_framework\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    throw "Framework not installed. Run: powershell -ExecutionPolicy Bypass -File ..\08_so101_framework\install.ps1"
}
& $venvPy -c "import so101" 2>$null
if ($LASTEXITCODE -ne 0) { throw "The framework's .venv has no 'so101' package. Re-run ..\08_so101_framework\install.ps1" }
& $venvPy -m pip install -e .

Write-Host "`nInstalled. Start it with:"
Write-Host "  ..\08_so101_framework\.venv\Scripts\Activate.ps1"
Write-Host "  so101-dashboard --sim      (or without --sim to connect the real arm)"
