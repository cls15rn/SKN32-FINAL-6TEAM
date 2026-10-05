# exp/x1_gtfs — 지하철 시간표 → GTFS (실험 X1 · 2026-10-04)

실험 브랜치 `exp-mobility`(포크 · `ae88ba1` 분기 · 머지 금지) 전용. 발표 코드는 읽기만 한다.
산출은 전부 저장소 밖 `C:\final_project\exp\x1_gtfs\out\` 에 생긴다.

| 파일 | 하는 일 |
|---|---|
| `chain.py` | 열차 잇기 — 역별 출발 행(열차 번호 없음)을 편으로 묶는다 |
| `build_gtfs.py` | 입력(기준 판 blob 확인) → 잇기 → GTFS 9파일 + zip + 행 대응표 · 홑행 · 종착 추정 · 역↔stop_id 표 |
| `check_gtfs.py` | 보존 검사 5 (산출과 원천만 읽어 다시 대조 · 5/5 면 끝 코드 0) |
| `link_check.py` | 잇기 검수 — 한 편에 행선지 둘 · 이상한 시차 · 추월 · 시작·끝 역 |
| `mask_test.py` | 가림 시험 — 행선지를 가리고 시각만으로 이은 짝이 같은 비율 |
| `route_probe.py` | 형식(참조 끊김) + 간단 질의 3건을 파이썬으로 (MOTIS 대신이 아님) |
| `motis_load.ps1` | MOTIS 적재 시간·메모리 측정 + 질의 3건 (PC) |
| `stop_coord_fill.csv` | 좌표 없는 5역의 근사 좌표(실험용 · 근거없음) |

```powershell
cd C:\final_project\SKN32-FINAL-6TEAM
python exp\x1_gtfs\build_gtfs.py
python exp\x1_gtfs\check_gtfs.py
python exp\x1_gtfs\link_check.py
python exp\x1_gtfs\mask_test.py
python exp\x1_gtfs\route_probe.py
```

## 정한 것 (이유)

- **편은 잇기로 만든다** — 원천에 열차 번호가 없다. 출발 시각은 원천 그대로, 묶음만 추론. 판정기의 「편 잇기」(92번 방 · 두 역 사이)와 같은 생각을 전 구간에 쓴 것.
- **원천에 없는 정차는 종착역 도착 하나뿐** — 종착역에는 출발 행이 없어서 「직전 역 출발 + 간선 시차」로 더한다(`timepoint=0` · `est_stops.csv`).
- **못 이은 행은 버리지 않는다** — 2행 이상이면 짧은 편(조각). 1행짜리만 GTFS 에 못 넣어 `orphan_rows.csv` 로 뺀다.
- **행선지가 dir 과 어긋나면 dir 을 믿는다** — 원천이 시발역·옛 종착역을 행선지로 준 행(우이신설 토요일 · 1호선 「제물포」 · 8호선 「구리」).
- **공휴일은 판정기와 같은 표**(`holidays_2026_2027.json`) — `daytype_calendar_v1.csv` 는 2026-08-31 에서 끝나 회귀 날짜(9~10월)를 못 덮는다. 겹치는 243일은 같다.
- **환승은 거리표 213쌍만** — 표에 없는 환승역 50곳은 넣지 않았다(지어내지 않음).
- **버스 없음.**

OSM 가공물 없음(좌표는 국가철도공단 표준데이터). 경로 응답은 저장하지 않는다.
