# 이동(Mobility) 데이터 — REPORT (2026-09-29 · 75번 방 · 담당 서유현)

`processed/mobility/` 21파일 · **88.9 MB**(9/30 · 시간표 `.gz` 1.8 MB · 도로 그래프 16 MB) · 확인 시각 2026-09-30(노트북 playdata) · sha256·행수는 `processed/mobility/MANIFEST_git_v1.json`.
파일별 열 뜻·한 행 예시·재생성·등급은 `DATA_IN_GIT.md`, 안 올린 것(원자료 1.7 GB · 개인 실측 · 라우터 그래프 1.2 GB · 로그)은 `DATA_NOT_IN_GIT.md`.

## 무슨 데이터 · 출처 · 받은 날 · 행 수

| 파일 | 무엇 | 출처(기관 · 자료) | 받은 날 | 행 |
|---|---|---|---|---|
| `timetable_v1.jsonl.gz` | 수도권 지하철 시간표(24노선 · 역·요일형·출발시각·행선지) | 서울 열린데이터광장 OA-101 · 국토교통부 TAGO 지하철 API | 2026-09-09 / 09-11 · 판 2026-09-12 · 행선지 채움 09-28 | 444,915(출발 있는 행만 · 정본 463,326) |
| `timetable_v1_meta.json` | 시간표 판 메타(built_at · 중복 제거 수) | 위 스크립트 산출 | 2026-09-12 | — |
| `line_station_order_v1.json` | 노선별 역 순서·간선 소요·역간거리 | 국가철도공단 표준데이터(FR_CODE) · 서울교통공사 역간거리 CSV(240810) · 시간표 관측 | 2026-09-11 · 거리 09-25 | 24노선 · 793역 · 777간선 |
| `transfer_walk_v1.json` | 환승역 도보 거리 | 서울교통공사 환승역거리 소요시간 정보(2025-12-31) | 2026-09-10 | 213쌍 · 74역 |
| `bus_route_v1.jsonl` · `bus_stops_v1.jsonl` | 서울 시내버스 노선·정류장 순서·좌표 | 서울시 버스 정보 API(TOPIS) | 2026-09-10 | 717 · 41,820 |
| `bus_seg_profile_v1.jsonl.gz` | 버스 구간 × 요일형 × 시각대 통행시간 p10/p50/p90 | 서울 OA-21217 노선별 정류장 구간별 평균 운행시간(2026-06-01~09-13 · 100일) | 2026-09-24 | 40,776 구간 |
| `station_coords.json` | 역 좌표(노선+역명 키) · 영문명 | 국가철도공단 전체_도시철도역사정보(2026-06-30) · 서울 OA-15442 | 2026-09-27 | 793 |
| `station_exits_v1.json` | 역 출구 좌표 | OpenStreetMap(Overpass · ODbL) | 2026-09-16 · 판 09-27 | 645역 |
| `bike_stations_v1.jsonl` | 따릉이 대여소 위치·거치대 수(정적 · 실시간 조회 안 씀) | 서울 OA-21235 · OA-15493 · OA-13252 | 2026-09-19 | 2,734 |
| `congestion_v1.jsonl` · `congestion_line9_v1.jsonl` | 역·방향·요일·30분 슬롯 혼잡도(1~8호선 · 9호선) | 서울교통공사 지하철혼잡도정보(2026-06-30) · 서울 OA-22197 | 2026-09-10 · 09-21 | 65,169 · 5,776(일반만 · 정본 8,208) |
| `transfer_car_v1.json` | 환승 칸(표시 안 함 · 보관) | 국토교통부 15151816 · 서울교통공사 15098252 | 2026-09-25 | 1,017 |
| `graph/` 5 | 도로 링크 속도 프로파일 · 도로급 계수 · OSM way↔TOPIS 링크 · way 형상 · 요일형 달력 | 서울 TOPIS 속도(2025-09~2026-05) · OSM | 2026-09-19 | 365,798 · … |
| `road_graph_v2/` 3 | 서울(+고양·성남·과천·하남·구리·광명·부천·김포·영종구·공항고속도로 회랑) 차도·자전거·걸음 그래프 노드·간선 · 범위 geojson — 서버 없는 파이썬 길찾기(택시·자전거·걷기) 입력(10/5 · v1 을 대신함) | Geofabrik `south-korea-latest.osm.pbf`(OSM 2026-09-18 · ODbL) | 2026-10-05 | 419,672 · 581,193 · 11 |
| `rail_edge_track_v1.jsonl.gz` | 역간 선로 길이(공표 역간거리가 없는 간선의 요금 거리 추정) | 같은 pbf 의 철도 선로(ODbL) + 역 순서 표 + 역 좌표 | 2026-10-05 | 777 |
| `station_gap_v1.jsonl` | 공표 역간거리 표(엔진 미연결) | 공공데이터포털 국가철도공단 역간거리 CSV 17 + 김포골드라인 운영사 | 2026-10-04 | 688 |

## 이용 조건

- 서울 열린데이터광장 · 공공데이터포털 · 국가철도공단 · 서울교통공사: **공공누리 제1유형(출처 표시)** — 각 파일 안 `source`·`source_id`·`license` 칸으로 표시.
- OSM 유래 4곳(`station_exits_v1.json` · `graph/osm_way_geom_v1.csv` · `road_graph_v2/` · `rail_edge_track_v1.jsonl.gz`): **ODbL 1.0 © OpenStreetMap contributors** — 파일·MANIFEST 안에 표기. 파생 DB 공개 시 같은 조건.
- 관광공사 자료: 이 폴더엔 없음.
- 경로 API(카카오·ODsay) 응답: **저장하지 않는다**(이동 모듈 규칙) — 여기 없음.

## 어떤 코드가 읽나

- `final_project_cs/app/modules/travel_ops/mobility/engine/runtime.py default_paths()` · `verify_time.py build_verifier_for_cases()` · `car.py CarGraph` · `options.py TransferCar` — 경로는 `engine/paths.py`(`PROCESSED / "mobility"`).
- 시험: `final_project_cs/tests/unit/travel/mobility/test_regression_cases.py`(회귀 게이트 21 · 전체층 156) · `test_plan_*`.
- `road_graph_v2/` 는 `engine/graph_router.py`(서버 없는 파이썬 길찾기 — 택시·자전거·걷기)가 읽는다. 열 뜻은 `road_graph_v2/README.md`. `rail_edge_track_v1.jsonl.gz` 는 `engine/options.py`(요금 거리 추정) · `station_gap_v1.jsonl` 은 아직 읽는 코드가 없다.
- 규칙 파일 2(`engine/rules/rules_v0.3.json` · `holidays_2026_2027.json`)는 코드 옆.

## 개인정보

- **없음** — 사용자 입력·식별자·좌표 원값 수집 없음. 담당자 본인 출퇴근 실측·티머니 태그(평가용)는 이 폴더에 넣지 않았다(드라이브 `ground_truth\`). `bus_route_v1.jsonl` 의 운수사 전화번호는 API 가 주는 공개 사업자 정보.
- 커밋 전 키 패턴(서비스키·API 키·티켓 토큰 접두어) grep 0건(2026-09-30 · 이 줄이 스스로 걸리지 않게 패턴 글자는 적지 않음).

## 다시 만드는 법

정본(드라이브 `DATA_DIR\travel\processed\mobility\`)을 `datasets/mobility/scripts/*.py` 로 만들고(파일별 스크립트·갱신 순서는 `scripts/README.md`), 저장소 루트에서
`python datasets/mobility/scripts/reduce_75.py --src <DATA_DIR>\travel\processed\mobility --clean` 가 이 폴더의 줄인 판과 MANIFEST 를 다시 쓴다(시간표 8열 · 출발없음 18,411행·9호선 급행 2,432행 제외 · 50 MB 넘는 파일은 `.gz`). 갱신 뒤 `pytest tests/unit/travel/mobility` 회귀 게이트로 값 불변 확인. 도로 그래프는 `datasets/mobility/scripts/build_road_graph_v1.py`(약 3.5분 · 같은 pbf 면 md5 재현).
