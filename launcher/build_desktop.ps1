param([Parameter(Mandatory=$true)][string]$OutputFile)
$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
$desktop = Join-Path $PSScriptRoot 'desktop'
$embedded = foreach ($name in 'main.js','bridge.js','host.js','index.html','effects.js','render.js','style.css','hero.webp') {
    '/resource:' + (Join-Path $desktop $name) + ',DBVictory.' + $name
}
$target = [IO.Path]::GetFullPath($OutputFile)
$icon = Join-Path $PSScriptRoot 'desktop.ico'
& $compiler /nologo /target:winexe /optimize+ /langversion:5 /codepage:65001 /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$target" @embedded (Join-Path $PSScriptRoot 'Desktop.cs')
if ($LASTEXITCODE -ne 0) { throw 'Kompilacja DBVictory Launcher.exe nie powiodła się.' }
