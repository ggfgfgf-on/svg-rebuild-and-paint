<#
  Install this skill into the DSH home that is actually in use.

    .\install.ps1                          # auto-detect the live harness home
    .\install.ps1 -HarnessHome D:\x\.dsh  # explicit harness home -> <home>\skills
    .\install.ps1 -Dest D:\my\skills       # explicit skills root (skill goes into <Dest>\svg-rebuild-and-paint)
    .\install.ps1 -SkipDeps                # do not run pip
    .\install.ps1 -SkipVerify              # do not run scripts\selfcheck.py afterwards

  Home detection order: -HarnessHome / -Dest, $env:DSH_HOME, the .dsh/.dsh-home this
  script sits inside (self-location), $env:DSH_PROFILE_DIR derived, ~/.dsh,
  ~/.dsh-home (legacy). DSH resolves skills as <home>\skills, so profiles
  (<home>\profiles\<name>) are not a separate skills target.
  Restart DSH afterwards; the skill appears as svg-rebuild-and-paint.
  重新安装不删旧目录：旧版本移到 <home>\skill-backups\<名>-<时间戳>，只保留最近 3 份。
  （备份必须放在 skills 目录之外，否则 DSH 会把 .bak 目录当成第二个同名技能加载。）
#>
param(
    [string]$Dest = "",
    [string]$HarnessHome = "",
    [switch]$SkipDeps,
    [switch]$SkipVerify
)
$ErrorActionPreference = "Stop"

# 运行期依赖：技能代码真正 import 的包（装完逐个复核）。
# 故意不复用 requirements.txt —— 依赖安装属于本地 install 的职责，不属于技能内容。
$Deps = @("numpy|numpy", "Pillow|PIL", "scipy|scipy", "potracer|potrace", "opencv-python|cv2", "imageio-ffmpeg|imageio_ffmpeg")

# 清掉以前中断留下的暂存目录（超过 6 小时的才算，避免碰到并发安装）
Get-ChildItem -LiteralPath ([System.IO.Path]::GetTempPath()) -Directory -Filter "svp-stage-*" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-6) } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

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
    & $cand @pre -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { continue }
    $PyExe = $cand
    $PyPre = $pre
    break
}

if (-not $PyExe) {
    Write-Host "X  找不到可用的 Python 3.10+（python / py -3）。" -ForegroundColor Red
    Write-Host "   请先装 Python 3.10+ 并勾选 Add to PATH，然后重跑本脚本。"
    exit 2
}
$pyVer = & $PyExe @PyPre -c 'import sys;print(sys.version.split()[0])'
Write-Host ("python: " + $PyExe + " " + $pyVer)

# --- 找一个 DSH 真正在用的 home -------------------------------------------------
# DSH 解析技能目录（packages/util/home-paths + skill-filesystem）：
#   home = $env:DSH_HOME 或 ~/.dsh（不是 ~/.dsh-home），技能目录恒为 <home>\skills；
#   profile 是 <home>\profiles\<name>，不单独承载 skills。
$homePath = ""
$homeSource = ""
if ($HarnessHome) { $homePath = $HarnessHome; $homeSource = "-HarnessHome" }
elseif ($env:DSH_HOME) { $homePath = $env:DSH_HOME; $homeSource = "env DSH_HOME" }

if (-not $homePath) {
    $probe = $PSScriptRoot
    for ($i = 0; $i -lt 4; $i++) {
        $leaf = Split-Path $probe -Leaf
        if ($leaf -eq ".dsh-home" -or $leaf -eq ".dsh") {
            if (Test-Path (Join-Path $probe "skills")) { $homePath = $probe; $homeSource = "self-location" }
            break
        }
        $parent = Split-Path $probe -Parent
        if (-not $parent -or $parent -eq $probe) { break }
        $probe = $parent
    }
}

if (-not $homePath -and $env:DSH_PROFILE_DIR) {
    $up = Split-Path (Split-Path $env:DSH_PROFILE_DIR -Parent) -Parent
    if ($up -and (Test-Path (Join-Path $up "skills"))) { $homePath = $up; $homeSource = "env DSH_PROFILE_DIR" }
}

if (-not $homePath) {
    $official = Join-Path $HOME ".dsh"
    $legacy = Join-Path $HOME ".dsh-home"
    if (Test-Path (Join-Path $official "skills")) { $homePath = $official; $homeSource = "default ~/.dsh" }
    elseif (Test-Path (Join-Path $legacy "skills")) { $homePath = $legacy; $homeSource = "legacy ~/.dsh-home" }
    else { $homePath = $official; $homeSource = "new ~/.dsh" }
}

if (-not $Dest) { $Dest = Join-Path $homePath "skills" }
Write-Host ("home: " + $homePath + "  (" + $homeSource + ")")

# 一台机器可能有多份 home（多 checkout / 旧 ~/.dsh-home）：把已装过的那几份报出来，
# 否则装进非活动 home 后 DSH 里看不到，看起来像"装失败"。
$dup = @()
$here = [System.IO.Path]::GetFullPath($homePath).TrimEnd('\')
foreach ($h in @($env:DSH_HOME, (Join-Path $HOME ".dsh"), (Join-Path $HOME ".dsh-home"))) {
    if (-not $h) { continue }
    if ([System.IO.Path]::GetFullPath($h).TrimEnd('\') -eq $here) { continue }
    $cand = Join-Path (Join-Path $h "skills") "svg-rebuild-and-paint"
    if (Test-Path $cand) { $dup += $cand }
}
if ($dup.Count -gt 0) {
    Write-Host ("!  另外发现已安装: " + ($dup -join "; ")) -ForegroundColor Yellow
    Write-Host "   要用那一份: -HarnessHome <它的 home>；要清掉: 直接删该目录。"
}
$target = Join-Path $Dest "svg-rebuild-and-paint"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# 先把源目录复制到临时暂存区，再换入目标。这样"从已安装的技能目录里跑 install.ps1"也安全：
# 旧目标被移进备份后，正在运行的 $PSScriptRoot 已经不存在，直接 Copy-Item 会失败。
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("svp-stage-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
Copy-Item -LiteralPath $PSScriptRoot -Destination $stage -Recurse
foreach ($pat in @(".git", "__pycache__", "_kf", ".vs", ".idea")) {
    Get-ChildItem -LiteralPath $stage -Recurse -Directory -Filter $pat -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($ext in @("*.pyc", "*.pyo")) {
    Get-ChildItem -LiteralPath $stage -Recurse -File -Filter $ext -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

$swapped = $false
if (Test-Path $target) {
    # 备份必须落在 skills 之外：skills 下任何含 SKILL.md 的目录都会被当成第二个同名技能加载。
    $bakRoot = Join-Path (Split-Path $Dest -Parent) "skill-backups"
    New-Item -ItemType Directory -Force -Path $bakRoot | Out-Null
    $bak = Join-Path $bakRoot ("svg-rebuild-and-paint-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    try {
        Move-Item -LiteralPath $target -Destination $bak
        Write-Host ("旧版本已备份 -> " + $bak)
        $swapped = $true
    } catch {
        # 目录被某个进程的 cwd 占着（典型：用户就站在技能目录里跑 .\install.ps1），
        # Windows 不允许重命名这种目录：改为复制备份 + 原地刷新。
        Copy-Item -LiteralPath $target -Destination $bak -Recurse
        Write-Host ("旧版本已备份(复制, 目录被占用) -> " + $bak)
        Get-ChildItem -LiteralPath $target -Force | Remove-Item -Recurse -Force
    }
    Get-ChildItem -LiteralPath $bakRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -Skip 3 |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
if ($swapped -or -not (Test-Path $target)) {
    Move-Item -LiteralPath $stage -Destination $target
} else {
    Copy-Item -Path (Join-Path $stage "*") -Destination $target -Recurse -Force
    Remove-Item -LiteralPath $stage -Recurse -Force
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
