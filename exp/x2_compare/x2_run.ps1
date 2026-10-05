# X2 - MOTIS 199건 대조 한 번에 돌리기 (PC - PowerShell 5.1). 경로 응답은 저장하지 않는다(시각 요약만 저장소 밖 compare.csv).
#   실행: cd C:\final_project\SKN32-FINAL-6TEAM ; powershell -ExecutionPolicy Bypass -File exp\x2_compare\x2_run.ps1
#   앞서: X1 의 GTFS zip · MOTIS(exp\x1_gtfs\motis · import 끝난 data 폴더)가 있어야 한다.
#   -SkipOurs : 우리 판정(--json · 약 3~5분)을 다시 돌리지 않고 앞서 만든 ours\*.json 을 쓴다.
param(
  [string]$Out   = "C:\final_project\exp\x2_compare",
  [string]$X1    = "C:\final_project\exp\x1_gtfs",
  [switch]$SkipOurs
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"; [Console]::OutputEncoding = [Text.Encoding]::UTF8
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Here = $PSScriptRoot
$Motis = "$X1\motis\motis.exe"
if (!(Test-Path $Motis)) { throw "motis.exe 가 없다: $Motis" }
New-Item -ItemType Directory -Force "$Out\ours", "$Out\motis_b" | Out-Null
"기준: " + (git -C $Repo rev-parse --short HEAD) + " (" + (git -C $Repo rev-parse --abbrev-ref HEAD) + ")"

# 1) 우리 판정 14묶음 --json (기준 판 엔진 - 읽기만)
if (!$SkipOurs) {
  # 입력은 git 의 기준 판 자료다. 저장소 `.env` 의 DATA_DIR(집 PC 는 9/12 판 시간표)이 잡히지 않게 환경변수로 덮는다
  #   (판정기 paths.load_cli_env: 환경변수가 .env 를 이긴다). 이 창 안에서만 바뀐다.
  $env:DATA_DIR = "$Repo\datasets\mobility\processed"
  "자료 폴더: $env:DATA_DIR"
  Push-Location "$Repo\final_project_cs"
  $T = "tests\unit\travel\mobility"
  $B = @(
    @("synthetic", "--timetable", "$T\mini_timetable_v2.jsonl.gz"), @("real"), @("issue"), @("alt", "--allow-router-down"),
    @("bus"), @("mixed"), @("multi"), @("car", "--gh-url", "fixture:$T\car_routes_fixture_v1.json"),
    @("bike", "--bike-fixture", "$T\bike_gh_fixture_v1.json"), @("judgment"), @("night"), @("bus_profile"),
    @("bus_noprof", "--bus-profile", "none"), @("destfill")
  )
  $sw = [Diagnostics.Stopwatch]::StartNew()
  foreach ($b in $B) {
    $n = $b[0]; $extra = @(); if ($b.Count -gt 1) { $extra = $b[1..($b.Count - 1)] }
    $o = & python -m app.modules.travel_ops.mobility.engine.verify_time --cases "$T\${n}_legs_v1.json" --check-expect --json "$Out\ours\$n.json" @extra
    if ($n -eq "real") { ($o | Select-String "^시간표|유효 출발") | ForEach-Object { "  " + $_.Line.Trim() } }
    ($o | Select-String "기대 대조") | ForEach-Object { "{0,-12} {1}" -f $n, $_.Line; if ($_.Line -notmatch "어긋남 0건") { $script:Bad = $true } }
  }
  if ($script:Bad) { Pop-Location; throw "기준 판 기대 대조가 어긋났다 - 판정기가 기준 자료(수집 2026-10-01)를 읽지 않았다. 여기서 멈춘다" }
  # synthetic 은 MOTIS 와 대려고 실 시간표로 한 번 더(기대 대조 없음)
  & python -m app.modules.travel_ops.mobility.engine.verify_time --cases "$T\synthetic_legs_v1.json" --json "$Out\ours\synthetic_real.json" | Select-Object -Last 2
  $sw.Stop(); "우리 판정 15회 실행: {0:N0}초" -f $sw.Elapsed.TotalSeconds
  Pop-Location
}

# 2) MOTIS 두 판 - A: X1 그대로(8080) - B: 종착 추정 정차를 뺀 판(8081)
python "$Here\x2_noest.py" --gtfs "$X1\out\gtfs_subway_x1.zip" --out "$Out\gtfs_noest.zip"
@"
server:
  port: 8081
timetable:
  first_day: 2026-09-01
  num_days: 90
  datasets:
    x1:
      path: $(("$Out\gtfs_noest.zip") -replace '\\','/')
"@ | Set-Content -Encoding ascii "$Out\motis_b\config.yml"
$pb = Start-Process $Motis -ArgumentList "import" -WorkingDirectory "$Out\motis_b" -PassThru -NoNewWindow -Wait
"B 판 import 끝 코드 {0}" -f $pb.ExitCode
$sa = Start-Process $Motis -ArgumentList "server" -WorkingDirectory "$X1\motis" -PassThru -NoNewWindow -RedirectStandardOutput "$Out\motis_a.log" -RedirectStandardError "$Out\motis_a.err"
$sb = Start-Process $Motis -ArgumentList "server" -WorkingDirectory "$Out\motis_b" -PassThru -NoNewWindow -RedirectStandardOutput "$Out\motis_b.log" -RedirectStandardError "$Out\motis_b.err"
Start-Sleep -Seconds 8
try {
  $sa.Refresh(); "MOTIS A 서버 메모리(질의 전): {0:N0} MB" -f ($sa.WorkingSet64 / 1MB)
  # 3) 변환 - 대조 - 요약
  python "$Here\x2_build.py"   --out $Out --lookup "$X1\out\stop_lookup.csv"
  python "$Here\x2_compare.py" --out $Out --transfers "$X1\out\gtfs\transfers.txt" --a http://127.0.0.1:8080 --b http://127.0.0.1:8081 --gtfs "$X1\out\gtfs_subway_x1.zip"
  $sa.Refresh(); "MOTIS A 서버 메모리(질의 뒤): {0:N0} MB - 최대 {1:N0} MB" -f ($sa.WorkingSet64 / 1MB), ($sa.PeakWorkingSet64 / 1MB)
  python "$Here\x2_report.py"  --out $Out
} finally {
  Stop-Process -Id $sa.Id -ErrorAction SilentlyContinue
  Stop-Process -Id $sb.Id -ErrorAction SilentlyContinue
}
