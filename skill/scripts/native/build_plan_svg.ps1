$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$vs = & (Join-Path $pf86 'Microsoft Visual Studio\Installer\vswhere.exe') -latest -products * -property installationPath
if (-not $vs) { throw 'Visual Studio BuildTools not found' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
$line = 'call "' + $vcvars + '" >nul 2>&1 && cd /d "' + $here + '" && cl /nologo /utf-8 /O2 /LD /EHsc /std:c++17 /Ithird_party /Ithird_party\rapidjson\include /Ithird_party\lcms2\include /Ithird_party\clipper2\include plan_svg_core.cpp third_party\pugixml.cpp third_party\lcms2\src\*.c third_party\clipper2\src\*.cpp /link xmllite.lib shlwapi.lib ole32.lib /OUT:plan_svg_core.dll'
cmd /c $line
if ($LASTEXITCODE -ne 0) { throw ('build failed: ' + $LASTEXITCODE) }
$kb = [math]::Round((Get-Item (Join-Path $here 'plan_svg_core.dll')).Length / 1KB, 1)
Write-Output ('OK  plan_svg_core.dll  ' + $kb + ' KB')
