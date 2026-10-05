# MOTIS 적재 시간·메모리 + 간단 질의 3건 (X1 · PC) — 경로 응답은 화면에만 찍고 저장하지 않는다.
#   먼저: https://github.com/motis-project/motis/releases 에서 motis-windows.zip 을 받아
#         C:\final_project\exp\x1_gtfs\motis\ 에 풀어 둔다(motis.exe 가 그 폴더에 있게).
#   실행: powershell -ExecutionPolicy Bypass -File exp\x1_gtfs\motis_load.ps1
# ※ 이 스크립트의 MOTIS 명령·설정 키는 클라우드에서 돌려 보지 못했다(릴리스 내려받기 차단). 판이 다르면 `motis.exe --help` 로 맞춘다.
param(
  [string]$Dir  = "C:\final_project\exp\x1_gtfs\motis",
  [string]$Gtfs = "C:\final_project\exp\x1_gtfs\out\gtfs_subway_x1.zip",
  [string]$Date = "2026-10-14",
  [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
if (!(Test-Path "$Dir\motis.exe")) { throw "motis.exe 가 없다: $Dir" }
if (!(Test-Path $Gtfs)) { throw "GTFS zip 이 없다: $Gtfs — build_gtfs.py 를 먼저 돌린다" }
Set-Location $Dir
& .\motis.exe --help | Select-Object -First 3

# 시간표만 싣는다(지도·도보 길찾기·지오코딩 없음). 회귀 199건의 날짜(9/10~10/18)를 덮게 9/1 부터 90일.
@"
server:
  port: $Port
timetable:
  first_day: 2026-09-01
  num_days: 90
  datasets:
    x1:
      path: $($Gtfs -replace '\\','/')
"@ | Set-Content -Encoding ascii config.yml

function Run-Measured([string]$argline) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $p = Start-Process .\motis.exe -ArgumentList $argline -PassThru -NoNewWindow
  $peak = 0
  while (!$p.HasExited) { $p.Refresh(); if ($p.PeakWorkingSet64 -gt $peak) { $peak = $p.PeakWorkingSet64 }; Start-Sleep -Milliseconds 200 }
  $sw.Stop()
  "{0}: {1:N1}초 · 최대 메모리 {2:N0} MB · 끝 코드 {3}" -f $argline, $sw.Elapsed.TotalSeconds, ($peak / 1MB), $p.ExitCode
}
Run-Measured "import"
"data 폴더 크기: {0:N1} MB" -f ((Get-ChildItem data -Recurse | Measure-Object Length -Sum).Sum / 1MB)

$srv = Start-Process .\motis.exe -ArgumentList "server" -PassThru -NoNewWindow
Start-Sleep -Seconds 8
$srv.Refresh(); "server 메모리: {0:N0} MB" -f ($srv.WorkingSet64 / 1MB)
# 09:00 KST = 00:00 UTC. 정류장 id = 데이터셋 이름(x1) + _ + stop_id
$q = @(
  @("강남→홍대입구",         "x1_L2_222",   "x1_L2_239"),
  @("서울역→인천공항T1",     "x1_AREX_A01", "x1_AREX_A10"),
  @("노원→사당",             "x1_L7_713",   "x1_L2_226")
)
foreach ($x in $q) {
  $u = "http://localhost:$Port/api/v1/plan?fromPlace=$($x[1])&toPlace=$($x[2])&time=${Date}T00:00:00Z"
  try {
    $sw = [Diagnostics.Stopwatch]::StartNew(); $r = Invoke-RestMethod $u; $sw.Stop()
    $it = $r.itineraries | Select-Object -First 1
    "{0}: 응답 {1:N0}ms · 여정 {2}개 · 첫 여정 {3} → {4} · 환승 {5}" -f $x[0], $sw.ElapsedMilliseconds, @($r.itineraries).Count, $it.startTime, $it.endTime, $it.transfers
  } catch { "{0}: 실패 — {1}" -f $x[0], $_.Exception.Message }
}
Stop-Process -Id $srv.Id
