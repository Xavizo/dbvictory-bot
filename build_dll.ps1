# Builds the observer DLL for one reviewed client build (needs 32-bit MinGW g++).
#   .\build_dll.ps1                         newest client (26 Nov)
#   .\build_dll.ps1 -ClientVersion 20261116 the 16/17 Nov client
param([ValidateSet('20261116', '20261126')][string]$ClientVersion = '20261126')
$builds = @{
    '20261116' = @{ Out = 'dll/dbvobserver_marker_20261117.dll'; Defines = @() }
    '20261126' = @{ Out = 'dll/dbvobserver_marker_20261126.dll'; Defines = @('-DDBV_CLIENT_20261126') }
}
$build = $builds[$ClientVersion]
$env:PATH = 'C:\mingw32\bin;' + $env:PATH  # 32-bit MinGW, matching dbvStart.exe
Push-Location $PSScriptRoot
& g++ -shared -std=c++17 -O2 -s -static -o $build.Out 'dll/marker_observer.cpp' @($build.Defines) -lkernel32 -luser32
$failed = $LASTEXITCODE -ne 0
Pop-Location
if ($failed) { throw "DLL build failed: $($build.Out)" }
Write-Host "BUILD OK: $($build.Out)" -ForegroundColor Green
