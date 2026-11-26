param([ValidateSet('legacy', '20261116', '20261126')][string]$ClientVersion = 'legacy')
$env:PATH = "C:\mingw32\bin;$env:PATH"
Set-Location $PSScriptRoot
$source = 'dll/dbvbot.cpp'
$output = 'dll/dbvbot.dll'
$defines = @()
if ($ClientVersion -ne 'legacy') {
    $source = 'dll/marker_observer.cpp'
    $output = 'dll/dbvobserver_marker_20261117.dll'
}
if ($ClientVersion -eq '20261126') {
    $output = 'dll/dbvobserver_marker_20261126.dll'
    $defines = @('-DDBV_CLIENT_20261126')
}
& g++ -shared -o $output $source -lkernel32 -luser32 -static -s -O2 -std=c++17 @defines
if ($LASTEXITCODE -ne 0) { throw "DLL build failed: $output" }
Write-Host "BUILD OK: $output" -ForegroundColor Green
