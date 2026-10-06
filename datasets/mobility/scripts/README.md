# datasets/mobility/scripts — 이동(Mobility) 데이터 갱신 스크립트

판정기가 읽는 파일(`../processed/mobility/`)을 **원자료가 새로 나왔을 때 다시 만드는** 코드만 둔다(2026-09-30 · 82).
이미 끝난 조사·탐침·일회성 보정은 넣지 않았다(저장소 밖 백업). 자동 실행은 아직 없다 — 필요할 때 사람이 순서대로 돌린다.

- 실행은 **저장소 루트에서** `python datasets/mobility/scripts/<이름>.py` (하위 폴더 없음 · 서로 `_paths.py` 를 옆에서 부른다).
- 산출은 **정본** `DATA_DIR\travel\processed\mobility\`(드라이브 동기화 폴더 · `.env` 의 `DATA_DIR`) — git 폴더에 바로 쓰지 않는다.
  git 에 올리는 줄인 판은 마지막에 `reduce_75.py` 가 정본에서 만든다. **`.env` 에 `DATA_DIR` 을 꼭 둔다** — 없거나 저장소 안
  `datasets/` 를 가리키면 쓰는 스크립트는 첫 쓰기 전에 멈춘다(`_paths.ensure_dirs` · `fill_timetable_dest_v1.py` 는 옆에 `.gz` 가 있어도 멈춘다).
  상대경로 `DATA_DIR` 은 저장소 루트 기준으로 푼다.
- `graph_` 5개는 예외 — 작업 폴더를 cwd 로 두고 돌린다(각 파일 머리말). 최상위에서 바로 읽고 쓰므로 import 하거나 `--help` 로 점검하지 않는다.
- 키는 팀 양식 그대로 `final_project_cs/.env.apikeys` 에(양식 `.env.apikeys.example` · `_paths.api_key()` 가 앱 설정과 같은 순서로 읽는다): 공공데이터포털 — 양식 규칙대로 서비스 칸이 비면 공통 키 `ACOP_DATA_GO_KR_KEY`: `ACOP_TAGO_API_KEY`(TAGO 지하철 15098554) · `ACOP_HOLIDAY_API_KEY`(천문연 특일 15012690) · 서울 버스노선 15000193 은 공통 키(양식의 `ACOP_SEOUL_BUS_API_KEY` 칸은 `settings.py` 필드가 생길 때까지 주석 — 풀리면 그 칸이 먼저) · 각각 활용신청 · `ACOP_SEOUL_OPENAPI_KEY`(서울 열린데이터광장 일반 인증키 — OA-101 · OA-15442 · OA-15492). 맨 위 `.env` 는 `DATA_DIR` 만(91 · 2026-10-01).
- 패키지: 이동 자료 기기 목록 `final_project_cs/requirements-mobility.txt`(팀 서버 목록과 판이 달라 **따로 만든 가상환경**에 깐다 · 팀장 9/30 정리) — `graph_03b_profile.py` 만 쓰는 `holidays` 는 그때 따로.

## 파일

| 판정기가 읽는 파일 | 수집(API) | 전처리 | 원자료(API 아님 = 수동 다운로드) |
|---|---|---|---|
| `timetable_v1.jsonl` · `_meta.json` | `seoul_metro_collect` → `tago_subway_collect` → `tago_fix_unmatched` → `tago_subway_collect`(이어받기) → `tago_retry_empty` · `seoul_fill_holiday` · `seoul_fill_gaps`(build 뒤 meta 필요) | `build_timetable_v1` → `fill_timetable_dest_v1` | — |
| `line_station_order_v1.json` | — | `build_line_station_order_v1`(시간표 뒤) | 서울교통공사 역간거리 CSV |
| `transfer_walk_v1.json` | — | `build_transfer_walk_v1` | 서울교통공사 환승역거리 CSV(OA-13290 · 연 1회) |
| `bus_route_v1.jsonl` · `bus_stops_v1.jsonl` | `seoul_bus_find_routes --enumerate` → `--fetch-stops` | `build_bus_all_v1`(`--replace-v1` 로 정본 교체) | — |
| `station_coords.json` | — | `station_coords_build` + 보정표 `station_coord_fix.json` · `station_nm_en_fix.json` | 전국도시철도역사정보 xlsx(15013205 · 연 1회) |
| `station_exits_v1.json` | — | `build_station_exits_v1`(역 좌표 뒤) | OSM Overpass 역 출구 json |
| `bike_stations_v1.jsonl` | — | `build_bike_stations_v1` | 따릉이 대여소 마스터 ∩ 실시간 ID json(`raw\mobility\bike\_check\`) |
| `bus_seg_profile_v1.jsonl.gz` | — | `build_bus_seg_profile_v1`(버스 노선 뒤) | 서울 OA-21217 구간 운행시간 zip |
| `congestion_v1.jsonl` · `congestion_line9_v1.jsonl` | — | `congestion_build` · `congestion_build --line9` | 서울교통공사 혼잡도 CSV · 9호선 xlsx(OA-22197) |
| `graph/` 5파일(도로 구간별 시간대 속도) | — | `graph_01_geom` → `graph_02a_extract_car_ways` → `graph_02b_match` → `graph_03a_convert_xlsx`(달마다) → `graph_03b_profile fix34` — **작업 폴더를 cwd 로** 돌린다(각 파일 머리말) | TOPIS 속도 xlsx 12개월 · 서울 클립 OSM pbf |
| `road_graph_v2/`(서버 없는 길찾기 도로망 — 차도·자전거·걸음) | — | `build_road_graph_v2 --src <pbf> --out <폴더>`(팀장 · `build_road_graph_v1` 을 불러 쓴다 — v1 스크립트·검사 `check_road_graph_v1` 는 그래서 남긴다 · 빌드 때 `osmium`·`shapely`) | Geofabrik south-korea pbf |
| `rail_edge_track_v1.jsonl.gz`(역간 선로 길이 — 요금 거리 추정) | — | `build_rail_edge_distance_v1`(팀장 · pbf 를 `datasets/mobility/raw/osm/` 에서 읽는다 · 역 순서 표·역 좌표 뒤) | 같은 pbf |
| `station_gap_v1.jsonl`(공표 역간거리 표 · 엔진 미연결) | — | `build_station_gap_v1 --raw … --ref … --out …` | 국가철도공단 역간거리 CSV 17(`raw\mobility\station_gap\`) |
| `express_marks_v1.json`(9호선 급행 편 표시 · 105) | — | `build_express_marks_v1 [--dir …] [--stops-checked-at …]` | `timetable_v1` + `line_station_order_v1`(시간표를 다시 만들면 이것도 다시 · 정차역은 스크립트 안 공표 값) |
| 엔진 `rules/holidays_<시작>_<끝>.json` | `holiday_collect --years …`(연 1회 · 결과는 git 의 엔진 규칙 폴더) | — | — |

검사: `consistency_check`(시간표↔혼잡도↔첫막차↔좌표 접합부 · 옆의 `check_station_names` 를 부른다 · 갱신 뒤 필수).
그 밖: `inventory_75.py`(정본 인벤토리) · `reduce_75.py`(정본 → `../processed/mobility/` 줄인 판 + `MANIFEST_git_v1.json` · 파일을 더할 때는 `FILES` 표에 한 줄).

## 갱신 순서

1. 원자료 받기 — 위 표의 수집(API) 또는 수동 다운로드를 `DATA_DIR\travel\raw\mobility\` 에.
2. 전처리 — 의존 순서: **시간표 → 노선 역순서 → 역 좌표 → 출구·혼잡도·환승 도보** / **버스 노선 → 버스 구간 프로파일** / 따릉이 · graph · 도로망은 따로.
3. `python datasets/mobility/scripts/consistency_check.py` — 새 결함이 없는지.
4. `python datasets/mobility/scripts/reduce_75.py --src <DATA_DIR>\travel\processed\mobility --clean` — 정본 → git 줄인 판 + MANIFEST(sha256·md5·행수) · 기본이 9/30 채택안(시간표 `.gz` · 출발없음·9호선 급행 행 제외) · `--src` 가 저장소 안이면 멈춘다.
5. 회귀: `pytest final_project_cs/tests/unit/travel/mobility` 게이트 + 전체층(`-m "mobility_full and not live"`) — 판정이 바뀌었으면 기대값을 사유와 함께 고친다.
6. 새 파일은 `git add -f`(팀 `.gitignore` 가 `processed/**` 를 막는다 · 추적 중인 파일은 보통 `git add`).

공휴일 표가 바뀌면(`holiday_collect`) 엔진 규칙 폴더 파일이 바뀌므로 회귀 기대값(요일형)을 같이 본다.
