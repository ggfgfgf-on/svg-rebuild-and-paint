# 编译原生光栅器（paint_core.dll / paint_gpu.dll）
# 注意：源码含中文注释，必须带 /utf-8；否则 MSVC 按系统代码页解析会报一堆莫名其妙语法错误。
param([switch]$Gpu)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$vs = & (Join-Path $pf86 'Microsoft Visual Studio\Installer\vswhere.exe') -latest -products * -property installationPath
if (-not $vs) { throw '找不到 Visual Studio / BuildTools（需 C++ 生成工具）' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "缺少 vcvars64.bat: $vcvars" }

function Build-One($src, $out) {
    $line = 'call "' + $vcvars + '" >nul 2>&1 && cd /d "' + $here + '" && cl /nologo /utf-8 /O2 /LD /EHsc /std:c++17 ' + $src + ' /link /OUT:' + $out
    cmd /c $line
    if ($LASTEXITCODE -ne 0) { throw "编译失败: $src (exit $LASTEXITCODE)" }
    $kb = [math]::Round((Get-Item (Join-Path $here $out)).Length / 1KB, 1)
    Write-Output ('OK  ' + $out + '  ' + $kb + ' KB')
}

Build-One 'paint_core.cpp' 'paint_core.dll'
if ($Gpu) { Build-One 'paint_gpu.cpp' 'paint_gpu.dll' }
Write-Output 'done'
