param([Parameter(Mandatory=$true)][string]$OutputFile)
$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
$target = [IO.Path]::GetFullPath($OutputFile)
$icon = Join-Path $PSScriptRoot 'desktop.ico'
& $compiler /nologo /target:winexe /optimize+ /langversion:5 /codepage:65001 /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$target" (Join-Path $PSScriptRoot 'Desktop.cs')
if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($target)) build failed (csc)." }
