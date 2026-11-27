# Build the ready-to-run player package from the committed HEAD.
# Used by .github/workflows/release.yml; also runs locally on Windows.
#
# The package bundles a relocatable Python in venv\Scripts (so every existing
# venv\Scripts\python.exe path keeps working), Node.js in runtime\node, the
# built panel with Electron, the observer DLLs and DBVictory Launcher.exe.
#
#   -PythonDir  existing relocatable Python to copy (default: `uv python install`)
#   -NodeDir    existing Node.js folder to copy (default: download the version on PATH)
#   -MinGwBin   folder with 32-bit g++.exe for the DLLs
param(
    [string]$OutDir = "build\release",
    [string]$Repo = "Xavizo/tibia-ots-mcp-bot",
    [string]$PythonVersion = "3.12",
    [string]$PythonDir,
    [string]$NodeDir,
    [Parameter(Mandatory=$true)][string]$MinGwBin
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    # Windows PowerShell 5.1 turns any stderr line of a native tool into an error; judge by exit code.
    $ErrorActionPreference = "Continue"
    & $Command @Arguments 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$Command $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

if (git status --porcelain --untracked-files=no) { throw "Commit or stash changes first: the package is built from HEAD." }
$Revision = (git rev-parse HEAD).Trim()
$HostPython = (Get-Command python).Source
$Version = (& $HostPython -I -c "import sys; sys.path.insert(0, r'$Root'); from pathlib import Path; from dbv_revision import version_for; print(version_for(Path(r'$Root'), '$Revision') or '')").Trim()
if ($Version -notmatch '^v\d+\.\d+\.\d+$') { throw "Cannot derive the version of $Revision (needs full Git history)." }
Write-Host "Building DBVictory Bot $Version ($($Revision.Substring(0,7)))"

if (-not [IO.Path]::IsPathRooted($OutDir)) { $OutDir = Join-Path $Root $OutDir }
$Out = [IO.Path]::GetFullPath($OutDir)
$Work = Join-Path $Out "work"
$Stage = Join-Path $Out "DBVictory-Bot"
function Remove-Tree([string]$Path) {
    # node_modules goes past 260 characters, which Remove-Item in Windows PowerShell 5.1 cannot delete.
    if (Test-Path $Path) { cmd /c "rd /s /q `"\\?\$Path`""; if (Test-Path $Path) { throw "Cannot remove $Path" } }
}
foreach ($dir in $Stage, $Work) { Remove-Tree $dir }
New-Item -ItemType Directory -Force $Stage, $Work | Out-Null

# 1. Program files exactly as committed (no .git, no local data).
$source = Join-Path $Work "source.zip"
Invoke-Checked git @("archive", "--format=zip", "-o", $source, "HEAD")
Invoke-Checked tar @("-xf", $source, "-C", $Stage)
foreach ($dev in ".github", ".claude", ".mcp.json") {
    $p = Join-Path $Stage $dev
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

# 2. Python with the bot's dependencies.
$Scripts = Join-Path $Stage "venv\Scripts"
if (-not $PythonDir) {
    Invoke-Checked uv @("python", "install", $PythonVersion, "--install-dir", (Join-Path $Work "python"))
    $PythonDir = (Get-ChildItem (Join-Path $Work "python") -Directory | Where-Object { Test-Path (Join-Path $_.FullName "python.exe") } | Select-Object -First 1).FullName
    if (-not $PythonDir) { throw "uv did not install Python." }
}
Copy-Item -Recurse $PythonDir $Scripts
Get-ChildItem $Scripts -Recurse -Filter EXTERNALLY-MANAGED | Remove-Item -Force
$Python = Join-Path $Scripts "python.exe"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Invoke-Checked uv @("pip", "install", "--python", $Python, "--break-system-packages", "-r", (Join-Path $Stage "requirements.txt"))
} else {
    & $Python -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) { Invoke-Checked $Python @("-m", "ensurepip") }
    Invoke-Checked $Python @("-m", "pip", "install", "--no-warn-script-location", "-r", (Join-Path $Stage "requirements.txt"))
}
Invoke-Checked $Python @("-c", "import mcp, Crypto, websockets, pymem; print('Python OK')")

# 3. Node.js for the panel.
$NodeTarget = Join-Path $Stage "runtime\node"
if (-not $NodeDir) {
    $nodeVersion = (node --version).Trim()
    $name = "node-$nodeVersion-win-x64"
    $zip = Join-Path $Work "$name.zip"
    Invoke-WebRequest "https://nodejs.org/dist/$nodeVersion/$name.zip" -OutFile $zip
    $sums = (Invoke-WebRequest "https://nodejs.org/dist/$nodeVersion/SHASUMS256.txt" -UseBasicParsing).Content
    $expected = ($sums -split "`n" | Where-Object { $_ -match "  $name\.zip$" }) -replace '\s.*$', ''
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $expected.Trim()) { throw "Node.js checksum mismatch" }
    Invoke-Checked tar @("-xf", $zip, "-C", $Work)
    $NodeDir = Join-Path $Work $name
}
New-Item -ItemType Directory -Force (Split-Path $NodeTarget) | Out-Null
Copy-Item -Recurse $NodeDir $NodeTarget

# 4. Panel and Electron, built with the bundled Node.
$env:PATH = "$NodeTarget;$env:PATH"
$env:NEXT_TELEMETRY_DISABLED = "1"
Push-Location (Join-Path $Stage "dashboard")
try {
    Invoke-Checked (Join-Path $NodeTarget "npm.cmd") @("ci", "--no-audit", "--no-fund")
    Invoke-Checked (Join-Path $NodeTarget "npm.cmd") @("run", "build")
} finally { Pop-Location }
$cache = Join-Path $Stage "dashboard\.next\cache"
if (Test-Path $cache) { Remove-Item -Recurse -Force $cache }

# 5. Observer DLLs for every reviewed client build.
$env:PATH = "$MinGwBin;$env:PATH"
$ErrorActionPreference = "Continue"  # compilers write warnings to stderr; both scripts throw on failure
foreach ($client in "legacy", "20261116", "20261126") {
    & (Join-Path $Stage "build_dll.ps1") -ClientVersion $client 2>&1 | ForEach-Object { "$_" }
}
Set-Location $Root

# 6. Launcher.
& (Join-Path $Stage "launcher\build_desktop.ps1") -OutputFile (Join-Path $Stage "DBVictory Launcher.exe") 2>&1 | ForEach-Object { "$_" }
$ErrorActionPreference = "Stop"
foreach ($built in "dll\dbvbot.dll", "dll\dbvobserver_marker_20261117.dll", "dll\dbvobserver_marker_20261126.dll", "DBVictory Launcher.exe") {
    if (-not (Test-Path (Join-Path $Stage $built))) { throw "Build did not produce $built" }
}

# 7. Version file, checksum manifest, zip and release description.
Invoke-Checked $HostPython @("-I", (Join-Path $Root "tools\release_package.py"), $Stage, "--version", $Version,
    "--revision", $Revision, "--repo", $Repo, "--out", $Out)
Remove-Tree $Work
$outputs = $env:GITHUB_OUTPUT
if (-not $outputs) { $outputs = Join-Path $Out "outputs.txt" }
"version=$Version" | Out-File -Append -Encoding utf8 -FilePath $outputs
