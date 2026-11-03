# Build native rasterizers (paint_core.dll / paint_gpu.dll).
# Source files contain UTF-8 comments, so /utf-8 is mandatory: without it MSVC parses
# them in the system codepage and reports bogus syntax errors.
param([switch]$Gpu)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$vs = & (Join-Path $pf86 'Microsoft Visual Studio\Installer\vswhere.exe') -latest -products * -property installationPath
if (-not $vs) { throw 'Visual Studio BuildTools not found (install C++ build tools)' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw ('missing vcvars64.bat: ' + $vcvars) }

function Build-One($src, $out) {
    $line = 'call "' + $vcvars + '" >nul 2>&1 && cd /d "' + $here + '" && cl /nologo /utf-8 /O2 /LD /EHsc /std:c++17 ' + $src + ' /link /OUT:' + $out
    cmd /c $line
    if ($LASTEXITCODE -ne 0) { throw ('build failed: ' + $src + ' (exit ' + $LASTEXITCODE + ')') }
    $kb = [math]::Round((Get-Item (Join-Path $here $out)).Length / 1KB, 1)
    Write-Output ('OK  ' + $out + '  ' + $kb + ' KB')
}

Build-One 'paint_core.cpp' 'paint_core.dll'
Build-One 'paint_fx.cpp' 'paint_fx.dll'
if ($Gpu) { Build-One 'paint_gpu.cpp' 'paint_gpu.dll' }
Write-Output 'done'
