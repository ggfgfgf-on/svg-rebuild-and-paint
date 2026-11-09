$ErrorActionPreference='Stop'
$here=Split-Path -Parent $MyInvocation.MyCommand.Path
$scripts=Split-Path -Parent $here
$py=(Get-Command python).Source
$inc=& $py -c "import sysconfig; print(sysconfig.get_paths()['include'])"
$lib=& $py -c "import sys,os; print(os.path.join(sys.base_prefix,'libs'))"
$ver=& $py -c "import sys; print('python%d%d' % (sys.version_info.major, sys.version_info.minor))"
$pf86=[Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$vs=& (Join-Path $pf86 'Microsoft Visual Studio\Installer\vswhere.exe') -latest -products * -property installationPath
$vcvars=Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
$line='call "'+$vcvars+'" >nul 2>&1 && cd /d "'+$here+'" && cl /nologo /utf-8 /O2 /LD /EHsc /std:c++17 /I"'+$inc+'" plan_svg_ext.cpp /link /LIBPATH:"'+$lib+'" '+$ver+'.lib /OUT:"'+$scripts+'\plan_svg_ext.pyd"'
cmd /c $line
if($LASTEXITCODE -ne 0){ throw ('build failed: '+$LASTEXITCODE) }
Write-Output ('OK  plan_svg_ext.pyd  ' + [math]::Round((Get-Item (Join-Path $scripts 'plan_svg_ext.pyd')).Length/1KB,1) + ' KB')
