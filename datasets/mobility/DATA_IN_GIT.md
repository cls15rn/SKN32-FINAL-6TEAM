# 이동 모듈 — git 에 올리는 데이터 (DATA_IN_GIT)

작성 서유현 · 2026-09-29 첫 판 · 2026-09-30 갱신(차도 그래프 추가 · 시간표 `.gz` · 필요 없는 행 제외 · 스크립트 자리) · **2026-10-01 시간표 재수집**(89 · 수집일 10/1 · 노선 역 순서 재생성) · 확인 기기 노트북 playdata
정본(드라이브) `DATA_DIR\travel\processed\mobility\` → 줄인 판 **저장소 `datasets/mobility/processed/mobility/`**(팀장 배정 9/29 · 「서버가 뜰 때까지 임시 · develop 까지」)
안 올린 것은 옆 `DATA_NOT_IN_GIT.md` · 갱신 스크립트는 `scripts/README.md`.

> 팀 `.gitignore` 는 `datasets/**/processed/**` 를 막지만 손대지 않고 **`git add -f`** 로 추적한다(전부 공공데이터 · 개인정보 0 · 이 폴더 README 「이동 팀」 절).
> 파일당 50 MB 미만(GitHub 경고선) · 합계 89.0 MB.

## 0. 요약

| | 값 |
|---|---|
| 판정기·라우터가 읽는 파일 | **21파일** = 지하철·버스·혼잡도 등 13 + `graph/` 5 + `road_graph_v1/` 3 (+ 엔진 안 규칙 2: `rules_v0.3.json` · `holidays_2026_2027.json`) |
| 그 21파일 정본 크기 | **279.9 MB** |
| 줄인 판(git) | **89.0 MB** |
| 줄인 것 | 시간표 — 판정기가 읽는 **8열만** · 출발 시각 없는 **18,873행 제외**(10/1 판) · **`.gz`**(텍스트 73.6 MB → 1.8 MB) / 9호선 혼잡도 — **급행 2,432행 제외**(8,208 → 5,776) / 나머지 19파일은 정본 그대로 |
| 50 MB 넘는 파일 | 없음(최대 `congestion_v1.jsonl` 32.2 MB) |
| 중복 확인(sha256 동일) | `bus_route_v2 = v1` · `bus_stops_v2 = v1` · `bus_stop_coords_v2 = bus_stop_coords` → v1 만 올림(v2 는 정본에서도 백업으로 옮김 · 9/30) |
| 검증 | 줄인 판으로 이동 회귀 전체 · pytest 게이트 — 정본과 같은 값(§7) |

## 1. 누가 무엇을 읽나 (엔진 `final_project_cs/app/modules/travel_ops/mobility/engine/`)

경로의 정본은 `runtime.py default_paths()` 와 `verify_time.py build_verifier_for_cases()` — 둘 다 `paths.PROCESSED / "mobility"` 아래 같은 이름. 시간표는 `paths.timetable_file()` 이 `.gz` 를 먼저 찾는다.

| 파일 | 읽는 코드 | 없으면 |
|---|---|---|
| `timetable_v1.jsonl.gz` | `verify_time.Timetable.load`(`paths.timetable_file`) | RuntimeError(필수) |
| `timetable_v1_meta.json` | `runtime.py`(built_at = 판 표시 · `tago_fetched_at`·`seoul_fetched_at` = 오래됨 기준 · 89) | 행의 `fetched_at` 로 대신 |
| `line_station_order_v1.json` | `line_order.LineOrder` · `candidates` | 필수 |
| `transfer_walk_v1.json` | `transfer_walk.TransferWalk` | 필수 |
| `bus_route_v1.jsonl` · `bus_stops_v1.jsonl` | `bus.BusRoutes` · `geo` | 필수 |
| `station_coords.json` | `geo.StationCoords` | 필수 |
| `station_exits_v1.json` | `exits.StationExits` | 역 좌표로 대신(경고) |
| `bike_stations_v1.jsonl` | `bike.BikeStations` | 자전거 근거없음 |
| `bus_seg_profile_v1.jsonl.gz` | `bus_profile.BusSegProfile`(gzip) | 표정속도 모델 |
| `congestion_v1.jsonl` · `congestion_line9_v1.jsonl` | `congestion.Congestion` | 가산 없음(근거없음) |
| `transfer_car_v1.json` | `options.TransferCar`(표시 안 함) | None |
| `graph/` 5파일 | `car.CarGraph`(택시·자동차 소요) | 택시·자동차 근거없음 |
| `road_graph_v1/` 3파일 | 서버 없는 파이썬 라우터(택시·자동차·자전거 경로 · 연결 작업 중) | 지금은 영향 없음 |

시험: `final_project_cs/tests/unit/travel/mobility/test_regression_cases.py` 가 위 경로를 `paths.PROCESSED` 로 읽는다. `.env` 에 `DATA_DIR` 이 없으면 이 저장소 폴더를 자동으로 쓴다(§5).

## 2. 파일별

공통: 경로 = 정본 `processed\mobility\<파일>` → git `datasets/mobility/processed/mobility/<파일>`(`mobility/` 한 겹은 엔진 경로 규칙 `PROCESSED / "mobility"` 때문). 생성 스크립트는 전부 `datasets/mobility/scripts/`(이름만 적음 · 순서는 `scripts/README.md`). 갱신: 원자료 받기 → 스크립트 → 정본에 쓰기 → `reduce_75.py --src <정본> --clean` → 회귀. 등급은 파일 안 `grade` 칸·`_report.md` 기준.

### B-1 `timetable_v1.jsonl.gz` — 지하철 시간표

| | |
|---|---|
| 행수 | 정본 472,541 → **453,668**(출발 시각 없는 18,873행 제외 · 10/1 판) |
| 크기 | 정본 191.8 MB → 텍스트 73.6 MB → **`.gz` 1.8 MB** |
| 남긴 열 8 | `line`(노선 24종 `01호선`…) · `station_nm`(역명) · `day_type`(`weekday`/`saturday`/`holiday`) · `dep_time`(`HH:MM:SS` · 24시 넘김 유지) · `dir`(`U`/`D` 참고용) · `dest_nm`(행선지 — 방향의 정본) · `dest_inferred`(`chain_v1` = 행선지를 앞뒤 열차로 채운 값 · 등급 추정) · `fetched_at`(`2026-10-01` TAGO·서울 — 89 재수집) |
| 뺀 열 12 | `station_key` `station_cd` `station_nm_en` `arr_time` `orig_nm` `train_no` `express` `source` `source_station_id` `fetched_at_precision` `dest_basis` `dest_hops` — 판정기가 읽지 않음(`Timetable.load` 가 위 8열만 읽는다) |
| 뺀 행 | `dep_time` 이 null 인 18,873행(종착역 도착만 있는 행) — 판정에 오르지 않는다. 역 이름 집합은 같은 역의 다른 행으로 그대로 채워진다(회귀로 확인 · §7) |
| 한 행 | `{"line":"01호선","station_nm":"가능","day_type":"weekday","dep_time":"06:58:00","dir":"U","dest_nm":"동두천","dest_inferred":null,"fetched_at":"2026-09-09"}` |
| 원자료 | 서울 열린데이터광장 OA-101 + 국토교통부 TAGO 지하철 API |
| 생성 | `build_timetable_v1.py` → `fill_timetable_dest_v1.py`(행선지 빈칸 채움 · **정본 비압축 파일에서만** 돈다 — 옆에 `.gz` 가 있으면 멈춤) |
| 등급 | 시각 확정 · 행선지 확정(원천) / 추정(`dest_inferred=chain_v1`) / 근거없음(`dest_nm` null) |
| 줄인 방법 | `reduce_75.py` — 행 순서 그대로 · 8열 · `separators=(",",":")` · `ensure_ascii=False` · gzip |

### A `timetable_v1_meta.json` · 8.7 KB
시간표 판 메타(`built_at 2026-10-01T10:38:34+09:00` · `tago_fetched_at`·`seoul_fetched_at` 2026-10-01 · 원 행수 · 중복 제거 수 · 소스별 행수 · `cross_source_conflicts_kept` 30 · `gaps` 24노선 중 빈 곳 남은 2 · `dest_fill`). `runtime.py` 가 `built_at` 을 판정 이력 `timetable_built_at` 에 남기고, **오래됨은 수집일(두 원천 중 오래된 것)로 잰다**(89 · 앞 판은 `built_at` — 옛 원자료를 다시 빌드만 해도 신선해 보였다). 생성 `build_timetable_v1.py`. 등급 —.

### A `line_station_order_v1.json` · 786.1 KB(10/1 새 시간표로 재생성 · 10/2 80: 경춘선 광운대를 상봉 갈래로 · 소수 편 관측 · 시간표 없는 역 건너 관측)
`lines`(24노선) → `stations[]`(`station_nm` `station_key` `station_cd` `fr_code` `fr_order` `is_spur` `has_timetable`) · `edges[]`(`a` `b` `travel_min` `travel_min_source` `travel_min_grade` `distance_m` `grade`) · `dir_label.reliable` · `is_loop` · `direction` · `dest_alias`. 777간선 · 793역 · 간선 등급 확정 665 / 추정 110 / 근거없음 2(10/2 · 남은 근거없음 = 경의선 운천–임진강 · 수인분당선 청량리–왕십리) · 추정 세부 `추정:소수편관측`(하루 5~19편 구간 · 편이 전부 일대일로 짝지어지고 시차 폭 60초 안) · `추정:건너관측`(시간표 없는 역 양옆 — 지나가는 것만 말한다 · 소요는 두 간선을 함께 지날 때만). 원자료 국가철도공단 FR_CODE + 시간표 관측 + 서울교통공사 역간거리 CSV(270간선 `distance_m`). 생성 `build_line_station_order_v1.py`. 한 항목: `{"a":"소요산","b":"청산","travel_min":3,"grade":"확정",…}`.

### A `transfer_walk_v1.json` · 58.0 KB
`pairs`(213 · 키 `역|노선|노선` · `distance_m` `walk_min` `src_min`) · `stations`(74 · 역별 최장) · `grade{distance_m:확정, walk_min:추정(1.04 m/s)}` · `license 공공누리 1`. 원자료 `서울교통공사_환승역거리 소요시간 정보_20251231.csv`. 생성 `build_transfer_walk_v1.py`. 한 항목 `{"station_nm":"서울역","from_line":"01호선","to_line":"04호선","distance_m":159.0,"walk_min":2.5,"src_min":2.2,"src_speed_mps":1.2}`.

### A `bus_route_v1.jsonl` · 470.0 KB · 717행
열 24: `route_id` `route_nm` `route_type`(1~15) `route_type_nm`(간선·지선·마을·심야·공항·광역·순환·투어) `route_type_grade` `corp_nm` `st/ed_station_nm` `length_km` `term_min`(배차 · 6 null) `first_time` `last_time`(24시 넘김 유지) `crosses_midnight` `grade_service_window`(확정/추정/근거없음) `grade_wait` `window_note` `time_base_date`(2026-09-10) `service_days`(716 daily) `service_days_grade` `service_days_basis` `source` `source_id` `fetched_at` `fetched_at_precision`. `bus.py` 는 행 전체를 `Route(..., r)` 로 보관하므로 **열 제거 안 함**. 원자료 서울시 버스 API(`raw\mobility\seoul_bus_all_routes.json`). 생성 `build_bus_all_v1.py`(`normalize_window`). 등급 첫차·막차 확정 · 배차 추정.

### A `bus_stops_v1.jsonl` · 15.6 MB · 41,820행
열 15: `route_id` `route_nm` `seq` `station_id` `ars_id` `station_nm` `lat` `lng` `direction` `sect_dist_m` `transfer_yn` `source` `source_id` `fetched_at` `fetched_at_precision`. `bus.py` 는 행 dict 를 그대로 `stops[route_id]` 에 넣고 `seq`·`station_nm`·`lat`·`lng`·`sect_dist_m`·`station_id` 를 쓴다(bus_profile 도 `station_id`·`seq`) — 50 MB 아래라 **그대로**. 원자료 `seoul_bus_all_stops.json`(19.7 MB). 생성 `build_bus_all_v1.py`. 등급 확정(좌표 · 순서).

### A `station_coords.json` · 528.2 KB
`stations`(793 · 키 **노선|역명** · `lat` `lng` `station_nm_en` `station_cd` `operator` `coord_source` `coord_join` `name_source` `fetched_at 2026-06-25`) + `source` `src_file` `data_basis_date` `built_at 2026-09-27` `count`. 좌표 없는 역 6(신길온천·GTX-A 3·서해구청). 원자료 국가철도공단 `전체_도시철도역사정보_20260630.xlsx` + OA-15442 `stations_all.json` + 보정표 `scripts/station_coord_fix.json` · `station_nm_en_fix.json`. 생성 `station_coords_build.py`. 등급 확정(표준데이터) · 영문명 보정 39역.

### A `station_exits_v1.json` · 804.8 KB
`exits`(645역명 → `[{osm_id lat lng ref desc desc_en wheelchair attrib dist_to_station_m}]`) · `grade 추정`(OSM 비공식 · 커버 99.4%) · `license ODbL` · `radius_m 300` · `built_at 2026-09-27`. 원자료 OSM Overpass `raw\mobility\osm\osm_subway_entrances_sudogwon_raw.json`. 생성 `build_station_exits_v1.py`.

### A `bike_stations_v1.jsonl` · 800.4 KB · 2,734행
열 11: `stationId` `no` `name` `lat` `lon` `rack` `mode`(QR/LCD) `gu` `checked_at 2026-09-19T21:55` `source_id` `grade{position:확정, mode:추정|근거없음}`. 실시간 거치 수는 없음(판정 시 `bikeList` 조회). 원자료 서울 OA-21235/15493/13252(`raw\mobility\bike\`). 생성 `build_bike_stations_v1.py`.

### A `bus_seg_profile_v1.jsonl.gz` · 9.2 MB · 40,776행 + `_meta` 1행(MANIFEST 행수 40,777) (정본이 gz)
열 9: `route_id` `route_nm` `from_id` `to_id` `from_seq` `to_seq` `dist_m` `weekday{n[24] p10[24] p50[24] p90[24]}` `holiday{…}`. 717노선 · 구간 40,776/41,103 · 100일(2026-06-01~09-13). `bus_profile.py` 가 gzip 으로 읽는다(이미 gz — 이 파일은 `.gz` 그대로 올린다). 원자료 서울 OA-21217 zip 9(`raw\mobility\bus_speed\` 422 MB · 원행 8,743,191). 생성 `build_bus_seg_profile_v1.py`. 등급 확정(5일 이상 관측 셀 1,457,091) · 1~4일 셀 18,130.

### A `congestion_v1.jsonl` · 32.2 MB · 65,169행
열 20: `station_key` `station_cd` `station_nm` `station_nm_en` `line`(1~8호선) `branch` `src_station_nm` `src_station_cd` `dir` `dir_raw` `day_type`(weekday/saturday/sunday) `slot`(30분 39칸) `congestion`(% · null 3,693=근거없음) `grade` `reason`(after_last_train 2,028 · terminus_direction 1,599 · no_train_in_window 66) `source*` `data_basis_date 2026-06-30` `fetched_at`. `congestion.py` 는 행을 통째로 `by_key` 에 두고 `lookup` 이 행을 돌려주므로 **열 제거 안 함**(50 MB 아래). 「판정에 쓰는 시간대만」 행 필터는 행 삭제라 하지 않음. 원자료 `서울교통공사_지하철혼잡도정보_20260630.csv`. 생성 `congestion_build.py`.

### B-2 `congestion_line9_v1.jsonl` · 3.0 → 2.1 MB · 8,208 → **5,776행**
위(`congestion_v1`)와 같은 키 + `service`(local/express) · `day_type` weekday/holiday 2종. **급행 2,432행을 뺐다** — `congestion.py` 가 `service=local` 행만 쓰고 express 는 읽고 버린다. 열은 그대로. 원자료 서울 OA-22197 xlsx(`raw\mobility\congestion_line9\line9_congestion_2026.xlsx` 정본). 생성 `congestion_build.py --line9`. 등급: 원천 xlsx 의 확정/근거없음 구분 그대로(정본 8,208행 기준 확정 7,799 · 근거없음 409 · `congestion_line9_v1_report.md` 도 정본 기준).

### A `transfer_car_v1.json` · 988.8 KB
`entries`(1,017 · 키 `환승역|노선|직전역|환승노선|둘째역` · `positions[{car door car_door}]` · `src` `src_sm`) · `missing_pairs` 79 · `merge_disagreements` 223. **표시 안 함(display=False)** — 코드는 읽되 안내에 안 씀. 올리는 이유: `options.TransferCar.load` 가 경로를 찾으므로 있으면 로드·없으면 None 으로 갈리지 않게. 원자료 국토부 15151816 + 서울교통공사 15098252(`raw\mobility\car_position\`). 생성 스크립트는 저장소에 넣지 않았다(표시 안 하는 파일이라 갱신하지 않음 · 드라이브 백업에만).

### A `graph/` 5개 (택시·자동차 소요 계산 · 13.6 MB)
| 파일 | 크기·행 | 열 | 등급 |
|---|---|---|---|
| `topis_class_factor_v1.json` | 7.9 KB | 도로급×요일형×시간대 평균속도·계수 · `osm_class_map` | 추정(약함) |
| `topis_link_profile_v1.jsonl.gz` | 5.0 MB · 365,798행 | `link_id` `daytype`(평일/토요일/휴일) `hour` `mean_kmh` `n_days` `std` `p10` `p90` — `car.py` 는 앞 4개만 씀(50 MB 아래 · 그대로) | 추정 |
| `osm_way_seg_topis_link_v1.csv` | 1.9 MB · 67,458행 | `osm_way_id` `seg_idx` `dir` `link_id` `n_pts` | — |
| `osm_way_geom_v1.csv` | 1.7 MB · 8,664행 | `osm_way_id` `highway` `name` `pts` | 확정(형상) |
| `daytype_calendar_v1.csv` | 8.0 KB · 365행 | `date` `daytype` `is_holiday` | — |
원자료 TOPIS 속도 xlsx 12개(`raw\mobility\topis\` 465 MB) + OSM pbf. 생성 `graph_01_geom.py → graph_02a_extract_car_ways.py → graph_02b_match.py → graph_03a_convert_xlsx.py → graph_03b_profile.py`(작업 폴더를 cwd 로 · 각 파일 머리말). **라우터(GraphHopper jar · pbf · graph-cache 1,240 MB)는 C** — 없으면 택시·자동차 근거없음(회귀 CAR-* 는 전체층 · `allow_router_down`).

### `road_graph_v1/` 3파일 (서울+인접·공항 차도·자전거 그래프 · 15.96 MB · 9/30 추가)

서버(GraphHopper) 없이 파이썬에서 택시·자동차(·자전거) 경로를 계산하기 위한 지도 데이터. **소요는 이 파일에 없다**(TOPIS 속도 프로파일로 계산). 경로는 저장하지 않는다.

| 파일 | 행 | 크기 | md5 |
|---|---:|---:|---|
| `nodes.jsonl.gz` | 280,065 | 3.24 MB | `1742f2eece86fbe6c8195e3224c0b72c` |
| `edges.jsonl.gz` | 375,651 | 12.63 MB | `5fef4bf787b91ddcbed1c5ec4a1eb49b` |
| `region_v1.geojson` | 11 도형 | 0.09 MB | `94335aee0dc3681cbb3700f4e8e47ab5` |

- nodes 열: `id`(OSM node) · `lat` `lon`(WGS84 · 소수 7자리) · `r`(1 = 범위 안 · 0 = 3 km 여백)
- edges 열: `u` `v`(끝 노드) · `way`(OSM way — `graph/osm_way_seg_topis_link_v1.csv` 의 `osm_way_id`) · `sa` `sb`(way 노드 순번 구간) · `len`(m) · `ow`(차량 일방 0/1/-1) · `hw`(OSM highway) · `ms`(maxspeed · 없으면 null) · `car` · `bike` · `bow`(자전거 일방) · `g`(형상 encoded polyline 1e-6) · `main`(차량 최대 강연결 성분) · `bmain`(자전거 최대 약연결 성분)
- 범위: 서울 ∪ 고양·성남·과천·하남·구리·광명·부천·김포 ∪ 영종(인천공항) ∪ 공항고속도로 회랑 · 3 km 여백
- 원자료 Geofabrik `south-korea-latest.osm.pbf`(ODbL 1.0 · © OpenStreetMap contributors · OSM 기준 2026-09-18) · 생성 `build_road_graph_v1.py` · 검수 `check_road_graph_v1.py` → `check_report.json` · 같은 pbf 면 md5 재현
- 등급 확정(OSM 공표 형상·일방·접근 태그) · 회전 제약은 싣지 않음 → 이걸로 낸 소요는 추정
- 상세(범위 relation id · 차량/자전거 규칙 · 검수 수치)는 폴더 안 `README.md`

### 부속 문서 (코드 안 읽음 · 같이 올림)
`timetable_v1_coverage.md` `timetable_v1_destfill_report.md` `line_station_order_v1_report.md` `transfer_walk_v1_report.md` `bus_route_v1_report.md` `station_coords_report.md` `bike_stations_v1_report.md` `bus_seg_profile_v1_report.md` `congestion_v1_report.md` `congestion_line9_v1_report.md` `transfer_car_v1_report.md` `graph/README.md` · `road_graph_v1/README.md` `build_report.json` `check_report.json` `MANIFEST.json` — 파일별 커버리지·등급 근거(정본 기준 숫자).

## 3. 크기표 전/후 (2026-09-30)

| 파일 | 분류 | 정본 | git | 행(git) |
|---|---|---:|---:|---:|
| `timetable_v1.jsonl.gz` | B(열·행 제외 · gz) | 191.8 MB | **1.8 MB**(텍스트 73.6 MB) | 453,668 |
| `congestion_v1.jsonl` | A | 32.2 MB | 32.2 MB | 65,169 |
| `bus_stops_v1.jsonl` | A | 15.6 MB | 15.6 MB | 41,820 |
| `road_graph_v1/edges.jsonl.gz` | A | 12.0 MB | 12.0 MB | 375,651 |
| `bus_seg_profile_v1.jsonl.gz` | A | 9.2 MB | 9.2 MB | 40,777 |
| `graph/topis_link_profile_v1.jsonl.gz` | A | 5.0 MB | 5.0 MB | 365,798 |
| `road_graph_v1/nodes.jsonl.gz` | A | 3.1 MB | 3.1 MB | 280,065 |
| `congestion_line9_v1.jsonl` | B(급행 행 제외) | 3.0 MB | **2.1 MB** | 5,776 |
| `graph/osm_way_seg_topis_link_v1.csv` | A | 1.9 MB | 1.9 MB | 67,458 |
| `graph/osm_way_geom_v1.csv` | A | 1.7 MB | 1.7 MB | 8,664 |
| `transfer_car_v1.json` | A | 988.8 KB | 〃 | |
| `station_exits_v1.json` | A | 804.8 KB | 〃 | |
| `bike_stations_v1.jsonl` | A | 800.4 KB | 〃 | 2,734 |
| `line_station_order_v1.json` | A | 761.6 KB | 〃 | |
| `station_coords.json` | A | 528.2 KB | 〃 | |
| `bus_route_v1.jsonl` | A | 470.0 KB | 〃 | 717 |
| `road_graph_v1/region_v1.geojson` | A | 88.6 KB | 〃 | |
| `transfer_walk_v1.json` | A | 58.0 KB | 〃 | |
| `graph/daytype_calendar_v1.csv` | A | 8.0 KB | 〃 | 365 |
| `graph/topis_class_factor_v1.json` | A | 7.9 KB | 〃 | |
| `timetable_v1_meta.json` | A | 8.7 KB | 〃 | |
| **합계 21** | | **279.9 MB** | **89.0 MB** | |

(첫 커밋 9/29 는 시간표 텍스트 75.1 MB · 행 제외 없이 148 MB 였다 — 히스토리에 남아 있다.)

## 4. 줄인 방법 · 하지 않은 것

- 열 제거는 **시간표만** — 다른 파일은 loader 가 행 dict 를 통째로 들고 있어 열을 빼면 동작이 바뀔 수 있다.
- 행 제외는 **판정기가 읽고 버리는 행만**: 시간표 출발 없음 18,411(9/30 판 · 10/1 판 18,873) · 9호선 급행 2,432. 뺀 판으로 이동 회귀 전체와 pytest 전체층을 돌려 정본과 같은 값을 확인하고 채택(9/29~30).
- `.gz` 는 시간표만 새로 만들었다(`bus_seg_profile` · `topis_link_profile` · `road_graph_v1` 은 정본이 원래 gz).
- 「판정에 쓰는 시간대만」 같은 행 필터는 하지 않았다 — 기준이 바뀌면 판정이 달라진다.
- 안 올린 것(원자료 · 개인 실측 · 라우터 캐시 · 로그 · 중복 사본) → `DATA_NOT_IN_GIT.md`.

## 5. 받는 법 — pull 만 하면 된다

`engine/paths.py`: 명령줄·시험은 `.env` 에 `DATA_DIR` 이 없으면 이 저장소 폴더(`datasets/mobility/processed`)를 자동으로 쓴다(출처 `repo_datasets`). 준 경로의 마지막 폴더가 `processed` 면 그 자리를 그대로 자료 폴더로 본다. 서버는 앱 설정 `mobility_data_dir`(`.env` `ACOP_MOBILITY_DATA_DIR`) — 비어 있으면 이동 계산기가 꺼진다. 드라이브 정본을 쓰는 기기는 `DATA_DIR` 이 이긴다.

## 6. MANIFEST

`processed/mobility/MANIFEST_git_v1.json` — 파일마다 `path` `class` `source_raw` `regen_script` `read_by` `src_bytes` `src_sha256` `bytes` `sha256` `rows` `checked_at` + `totals` + 시간표 `reduce{rows_in rows_out columns_kept columns_dropped}` · 9호선 행 제외 기록 · `road_graph_v1/MANIFEST.json` 과 md5 대조 결과.

## 7. 검증 (2026-09-30 · 노트북 · develop `1ce09a6` 위)

- `reduce_75.py` 재생성: 21파일 · road_graph md5 3개 일치 · `--src` 가 저장소 안이면 멈춤(rc=2) 확인 · 키 패턴 0건
- 저장소 데이터로 `pytest tests/unit/travel/mobility -q`: **210 passed · 1 skipped**(`test_check_scripts.py` 의 정본 텍스트 시간표 전용 시험 — 자료 기기에서만 돈다)
- 기기 회귀(저장소 데이터): 점검 스크립트 · 판정 로그 19줄 기대 어긋남 0 · 스크립트 `--help` 점검 → **전부 기대값**
- 행 제외 채택 전(9/29~30): 정본 대 줄인 판 회귀 게이트·전체 비교 어긋남 0 · pytest 전체층 185
