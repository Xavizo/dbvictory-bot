# Run in native Windows PowerShell, without elevation by default.
param([string]$PythonCommand = "python", [switch]$SkipDashboard)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
}

Push-Location $Root
try {
    Invoke-Checked $PythonCommand @("-c", "import sys; assert sys.platform == 'win32', 'Use native Windows Python'; assert sys.version_info >= (3, 11), 'Python 3.11+ required'")
    $Python = Join-Path $Root "venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) { Invoke-Checked $PythonCommand @("-m", "venv", "venv") }
    Invoke-Checked $Python @("-m", "pip", "install", "-r", "requirements.txt")
    if (-not $SkipDashboard) {
        Push-Location (Join-Path $Root "dashboard")
        try {
            Invoke-Checked "npm.cmd" @("ci")
            Invoke-Checked "npm.cmd" @("run", "build")
        } finally { Pop-Location }
    }
    Invoke-Checked "codex" @("mcp", "add", "dbvictory-bot", "--env", "PYTHONUTF8=1", "--env", "PYTHONUNBUFFERED=1", "--", $Python, (Join-Path $Root "run_mcp.py"))
    Write-Host "Registered dbvictory-bot. Start Codex from this folder and check /mcp. No game connection was started."
} finally { Pop-Location }
