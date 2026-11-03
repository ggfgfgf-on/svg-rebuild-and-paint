param([int]$Threads = 1, [switch]$DryRun)
$ErrorActionPreference = "Continue"
$py     = "python"
$script = Join-Path $PSScriptRoot "planner_8layer.py"
$ref    = "D:/DSHwork/flower/rose5-full.png"
$reg    = "D:/DSHwork/flower/plan/regions2.npz"
$papers = @("03_default-paper", "02_rough-canvas", "05_paper-torchon")
$TempLimit = 95

function CpuTemp {
  try {
    $s = (Get-Counter '\Thermal Zone Information(*)\Temperature' -ErrorAction Stop).CounterSamples
    if ($s.Count -gt 0) { return [math]::Round($s[0].CookedValue - 273.15, 1) }
  } catch { }
  return $null
}

$start = CpuTemp
"start temp: $start C   threads: $Threads   dryrun: $([bool]$DryRun)"
if ($start -ne $null -and $start -gt $TempLimit) { "ABORT: already $start C"; exit 1 }

$i = 0
foreach ($paper in $papers) {
  $out = "D:/DSHwork/flower/plan/paper$i"
  $argv = @($script, $ref, $reg, $out, $paper, "--tips", "--threads", "$Threads")
  if ($DryRun) { "would run: $py " + ($argv -join " "); $i++; continue }

  $before = (Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; StartTime=(Get-Date).AddMinutes(-60)} -ErrorAction SilentlyContinue | Measure-Object).Count
  $p = Start-Process -FilePath $py -ArgumentList $argv -PassThru -NoNewWindow
  try { $p.PriorityClass = "Idle" } catch { }
  try { $p.ProcessorAffinity = [IntPtr]0x3 } catch { }
  $peak = 0
  while (-not $p.HasExited) {
    Start-Sleep -Seconds 3
    $t = CpuTemp
    if ($t -ne $null) {
      if ($t -gt $peak) { $peak = $t }
      if ($t -gt $TempLimit) { "TEMP $t C > $TempLimit, killing render"; try { $p.Kill() } catch { }; break }
    }
  }
  $p.WaitForExit()
  $after = (Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; StartTime=(Get-Date).AddMinutes(-60)} -ErrorAction SilentlyContinue | Measure-Object).Count
  "paper$i ($paper): exit=$($p.ExitCode) peakTemp=$peak C  nvlddmkmDelta=$($after - $before)"
  $i++
  if (-not $p.HasExited -or $p.ExitCode -ne 0) { "stopping series"; break }
  Start-Sleep -Seconds 45
}
if (-not $DryRun) {
  "=== metrics ==="
  & $py "D:/DSHwork/flower/plan/metrics2.py" p0 "D:/DSHwork/flower/plan/paper0.png" p1 "D:/DSHwork/flower/plan/paper1.png" p2 "D:/DSHwork/flower/plan/paper2.png"
}
