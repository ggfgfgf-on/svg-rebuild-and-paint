<#
  Install this skill into a DSH skills directory.

    .\install.ps1                       # -> $env:DSH_HOME\skills  (or ~/.dsh-home\skills)
    .\install.ps1 -Dest D:\my\skills    # explicit skills root (skill goes into <Dest>\svg-rebuild-and-paint)
    .\install.ps1 -SkipDeps             # do not run pip
    .\install.ps1 -SkipVerify           # do not run scripts\selfcheck.py afterwards

  Restart DSH afterwards; the skill appears as svg-rebuild-and-paint.
  重新安装不删旧目录：旧版本重命名为 <target>.bak-<时间戳>，只保留最近 3 份。
#>
param(
    [string]$Dest = "",
    [switch]$SkipDeps,
    [switch]$SkipVerify
)
$ErrorActionPreference = "Stop"

# 运行期依赖：技能代码真正 import 的包（装完逐个复核）。
# 故意不复用 requirements.txt —— 依赖安装属于本地 install 的职责，不属于技能内容。
$Deps = @("numpy|numpy", "Pillow|PIL", "scipy|scipy", "potracer|potrace", "opencv-python|cv2", "imageio-ffmpeg|imageio_ffmpeg")

$PyExe = $null
$PyPre = @()
foreach ($cand in @("python", "py")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    if ($cmd.Source -and $cmd.Source.Contains("WindowsApps")) { continue }
    $pre = @()
    if ($cand -eq "py") { $pre = @("-3") }
    & $cand @pre -c "import sys" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { continue }
    $ver = & $cand @pre -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $ver) { continue }
    if ([int]$ver -lt 310) { continue }
    $PyExe = $cand
    $PyPre = $pre
    break
}

if (-not $PyExe) {
    Write-Host "X  找不到可用的 Python 3.10+（python / py -3）。" -ForegroundColor Red
    Write-Host "   请先装 Python 3.10+ 并勾选 Add to PATH，然后重跑本脚本。"
    exit 2
}
$pyVer = & $PyExe @PyPre -c "import sys;print("%d.%d.%d" % sys.version_info[:3])"
Write-Host ("python: " + $PyExe + " " + $pyVer)

if (-not $Dest) {
    $base = $env:DSH_HOME
    if (-not $base) { $base = Join-Path $HOME ".dsh-home" }
    $Dest = Join-Path $base "skills"
}
$target = Join-Path $Dest "svg-rebuild-and-paint"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

if (Test-Path $target) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $bak = $target + ".bak-" + $stamp
    Move-Item -LiteralPath $target -Destination $bak
    Write-Host ("旧版本已备份 -> " + $bak)
    Get-ChildItem -LiteralPath $Dest -Directory -Filter "svg-rebuild-and-paint.bak-*" |
        Sort-Object Name -Descending | Select-Object -Skip 3 |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

Copy-Item -LiteralPath $PSScriptRoot -Destination $target -Recurse
foreach ($pat in @(".git", "__pycache__", "_kf", ".vs", ".idea")) {
    Get-ChildItem -LiteralPath $target -Recurse -Directory -Filter $pat -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($ext in @("*.pyc", "*.pyo")) {
    Get-ChildItem -LiteralPath $target -Recurse -File -Filter $ext -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
Write-Host ("installed -> " + $target)

if (-not $SkipDeps) {
    $missing = @()
    foreach ($d in $Deps) {
        $mod = $d.Split("|")[1]
        & $PyExe @PyPre -c ("import " + $mod) 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { $missing += $d.Split("|")[0] }
    }
    if ($missing.Count -eq 0) {
        Write-Host "deps: already satisfied"
    } else {
        Write-Host ("installing: " + ($missing -join ", "))
        & $PyExe @PyPre -m pip install --disable-pip-version-check -q @missing
        if ($LASTEXITCODE -ne 0) { Write-Warning ("pip 安装失败。手动执行：" + $PyExe + " -m pip install " + ($missing -join " ")) }
    }
}

$bad = @()
foreach ($d in $Deps) {
    $mod = $d.Split("|")[1]
    & $PyExe @PyPre -c ("import " + $mod) 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { $bad += $d.Split("|")[0] }
}
if ($bad.Count -gt 0) {
    Write-Host ("X  依赖仍缺失: " + ($bad -join ", ")) -ForegroundColor Red
    Write-Host ("   手动: " + $PyExe + " -m pip install " + ($bad -join " "))
    exit 3
}
Write-Host "deps: ok"

$verifyOk = $true
if (-not $SkipVerify) {
    Write-Host ""
    Write-Host "selfcheck ..."
    $sc = Join-Path $target "scripts\selfcheck.py"
    & $PyExe @PyPre $sc
    if ($LASTEXITCODE -ne 0) { $verifyOk = $false }
}

Write-Host ""
if ($verifyOk) {
    Write-Host "OK  安装完成。重启 DSH 后技能生效（svg-rebuild-and-paint）。"
} else {
    Write-Host "!   安装完成，但自检未通过：看上面哪条 FAIL。" -ForegroundColor Yellow
}
Write-Host "Try it:"
Write-Host ("  " + $PyExe + " `"" + $target + "\scripts\selfcheck.py`"")
Write-Host ("  " + $PyExe + " `"" + $target + "\scripts\auto.py`" ref.png out.html")
if ($verifyOk) { exit 0 } else { exit 1 }
