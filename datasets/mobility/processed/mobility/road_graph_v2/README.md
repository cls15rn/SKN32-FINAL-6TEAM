# road_graph_v2 — 서울(+인접·공항) 차도·자전거·걸음 그래프 파일 (2026-10-05 · 팀장 스크립트 그대로 만듦)

서버 없이 파이썬에서 택시·자동차 · 자전거 · **걷기** 경로를 계산하기 위한 **지도 데이터**. 경로는 저장하지 않는다.
`road_graph_v1`(차도·자전거만)을 대신한다 — v1 3파일은 저장소에서 내렸다(v2 가 v1 의 칸을 모두 싣고 걸음 칸을 더한 판이다).

- 원자료: Geofabrik `south-korea-latest.osm.pbf` · md5 `b4aac9966079cb88204b1d8df495e33c` · OSM 기준 시각 2026-09-18T20:21:10Z ·
  **ODbL 1.0 (© OpenStreetMap contributors)** — v1 과 같은 원자료다.
- 만드는 법: `python datasets/mobility/scripts/build_road_graph_v2.py --src <pbf> --out <이 폴더>` (약 4분 · 메모리 약 2 GB ·
  빌드 때만 `osmium`·`shapely`·`numpy` — 실행(길찾기)은 표준 라이브러리만). `build_road_graph_v1.py` 를 불러 쓴다(같은 범위·같은 도구).
- 읽는 코드: `final_project_cs/app/modules/travel_ops/mobility/engine/graph_router.py`(팀장 · A* · 프로파일 car · bike · foot).
- 등급: **확정(OSM 공표 태그)** — 형상·일방통행·접근 태그. 걸음 길은 「태그 해석」이다. 소요는 이 파일에 없다
  (택시 = TOPIS 프로파일 · 걷기 = 거리 ÷ 보행 속도 · 자전거 = 거리 ÷ 고정 속도 → 모두 추정). 회전 제약·횡단 대기·계단 속도는 없다.

## 파일
| 파일 | 행 | 크기(gz) | md5 |
|---|---:|---:|---|
| `nodes.jsonl.gz` | 419,672 | 4.54 MB | `50dbcf7e95400b4d518901de4f46e549` |
| `edges.jsonl.gz` | 581,193 | 18.32 MB | `c28daad6e8080b444eeee2784c8c2e74` |
| `region_v1.geojson` | 11 도형 | 0.09 MB | `94335aee0dc3681cbb3700f4e8e47ab5`(v1 과 같은 파일) |

간선 581,193 = 차량 359,592 · 자전거 404,711 · **걸음 532,732(걸음 전용 166,007 · 계단 5,542)** — `build_report.json`.

## 열 뜻
`nodes` · `edges` 의 v1 칸(`id` `lat` `lon` `r` / `u` `v` `way` `sa` `sb` `len` `ow` `hw` `ms` `car` `bike` `bow` `g` `main` `bmain`)은 v1 과 같다.
v2 가 `edges` 에 더한 칸:

| 열 | 뜻 |
|---|---|
| `foot` | 1 = 걸어서 다닐 수 있다(아래 규칙 · 양방향 — 걸음은 일방통행을 보지 않는다) |
| `st` | 1 = 계단(`highway=steps`) — 길찾기는 경로를 고를 때만 1.5배로 치고 거리는 실제 길이 |
| `fmain` | 1 = 걸음 **최대 연결 성분**. 길찾기는 여기에만 출발·도착을 붙인다 |

걸음 규칙(스크립트 머리말과 같다): primary~service 차도는 걷는다 · motorway·trunk 는 `foot=yes/designated` 일 때만 ·
`footway·path·pedestrian·steps·corridor·track` 은 걷는다 · cycleway 는 `foot=yes/designated/permissive` 일 때만 ·
`foot=no`·`access=no/private`(걸음 허용 태그가 없을 때)·`area=yes` 는 뺀다.

## 알아 둘 것(팀장 판 확인 방 · 2026-10-04 실측)
- 도보 20구간(지하철 출구 → 관광지 · 카카오 도보 최단거리 ±25%): **v2 12/20**(v1 10/20 · 직선 × 1.4 는 16/20). 길찾기의 값은 평균이
  아니라 **하천·철도 건너편**이다(직선으로는 「걸어서 n분」이던 곳이 실제 돌아가는 거리로 나온다).
- 보도가 OSM 에 없는 곳은 길게 나온다(시청→대한문 2.47배 · 동대문→흥인지문 2.10배 · 이촌→국립중앙박물관 1.94배).
- v1 로는 걷기를 재지 않는다 — 플래너가 이 판(`MANIFEST.json` 의 `version: road_graph_v2`)일 때만 걷기를 길로 잰다.
