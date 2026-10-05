# Mobility(이동) 팀 데이터셋 폴더

Mobility(이동) 팀이 쓰는 데이터를 모으는 자리다(2026-09-29 신설).

## 폴더

| 폴더 | 무엇을 두나 | git 에 올라가나 |
|---|---|---|
| `raw/` | 받은 그대로의 원본(API 응답, 내려받은 파일, 엑셀) | **안 올라간다** |
| `processed/` | 정리·가공한 결과(CSV·JSONL 등) | **안 올라간다** |
| `scripts/` | 원본을 받아 오고 가공하는 코드(`.py`·`.json`·`.txt` 등) | 올라간다 |
| `README.md` · `REPORT.md` | 무슨 데이터인지, 어디서 받았고 어디에 쓰는지 | 올라간다 |

★**데이터 파일 자체(CSV·TSV·JSONL·XLSX·Parquet·DB·압축본)는 이 폴더 어디에 두어도 git 에 올라가지 않는다.**
공개 저장소라서다 — 09-14 에 실제 주문 기록 조각(가린 이름·연락처 끝자리·주소)이 섞여 올라간 일이 있었다.
데이터는 각자 PC 나 팀 드라이브로 나누고, 여기에는 **다시 만드는 방법(scripts/)과 설명(REPORT.md)** 을 올린다.

## REPORT.md 에 적을 것

- 무슨 데이터인가 · 출처(기관·API·주소) · 받은 날짜 · 행 수
- 이용 조건(출처 표시 등) — 관광공사 자료는 ⓒ한국관광공사 표시를 유지한다
- 어떤 코드가 이 데이터를 읽는가(`final_project_cs/...` 경로)
- 개인정보가 섞일 수 있는지, 섞였다면 어떻게 걸렀는지

## 올리면 안 되는 것 (위 규칙이 막지만 이름을 바꾸면 샐 수 있다)

- 로그인 세션·쿠키·브라우저 프로필, API 키(`.env`) — 키는 `.env.apikeys` 같은 git 이 무시하는 파일에만 둔다
- 사람 이름·연락처·주소가 든 원본

---

## 이동(Mobility) 팀 — 이 폴더를 실제로 어떻게 쓰나 (2026-09-29 · 75번 방)

**판정기·라우터가 읽는 가공 데이터 21파일(89 MB · 9/30)을 `processed/mobility/` 에 두고 git 에 올린다** — 팀장 배정(9/29 · 「서버가 뜰 때까지 임시 · develop 까지」).
위 규칙과 어긋나는 점은 하나뿐이고 근거는 이렇다.

- 전부 **공공데이터**(서울 열린데이터광장 · 국토교통부 TAGO · 국가철도공단 · 서울교통공사 · OSM) — 개인정보·키·사용자 입력 **없음**. 원자료·개인 실측(담당자 출퇴근 기록·티머니 태그)은 여기 없고 드라이브에만(`DATA_NOT_IN_GIT.md`).
- 팀 `.gitignore` 는 손대지 않았다. `processed/**` 가 막혀 있으므로 **`git add -f`** 로 추적한다 — 한번 추적된 파일은 이후 변경도 보통 `git add` 로 잡힌다. **새 파일**을 더할 때만 다시 `-f`.
- 서버(`app.core.settings.mobility_data_dir`)가 자기 자료 폴더를 갖게 되면 이 폴더의 데이터는 지운다(히스토리엔 남는다 — 그래서 첫 커밋을 줄인 판 148 MB 로 작게 했다).

| 자리 | 무엇 | git |
|---|---|---|
| `processed/mobility/` | 판정기 입력 23파일(시간표는 판정기가 읽는 8열만 · **`.gz`** · 나머지 정본 그대로) + 파일별 `_report.md` 12 + `MANIFEST_git_v1.json`(sha256·md5·행수·원자료·확인 시각) | **올림(`-f`)** |
| `processed/mobility/graph/` | 택시·자동차 소요 계산 자료 5파일(TOPIS 속도 프로파일 등) | 올림 |
| `processed/mobility/road_graph_v2/` | 서울(+인접·공항) 차도·자전거·**걸음** 그래프(노드 419,672 · 간선 581,193 · 22.95 MB gz · OSM ODbL · © OpenStreetMap contributors) — 서버 없는 파이썬 길찾기(택시·자전거·걷기) 입력(10/5 — v1 15.96 MB 를 대신함 · 만드는 스크립트는 팀장 `build_road_graph_v2.py`) | 올림 |
| `processed/mobility/rail_edge_track_v1.jsonl.gz` · `station_gap_v1.jsonl` | 요금 거리 — OSM 선로 길이 추정(777간선 · 추정 · 팀장) · 공표 역간거리 표(688간선 · 엔진 미연결) | 올림(10/5) |
| `processed/inventory_75.json` · `size_table_75.md` | 정본 폴더 인벤토리(88파일 열·행·sha) · 전/후 크기표 | 안 올림(ignore) |
| `raw/` | 비움 — 원자료 1.7 GB 는 드라이브 `data\travel\raw\mobility\` | 안 올림 |
| `scripts/` | `inventory_75.py`(인벤토리) · `reduce_75.py`(정본 → 이 폴더 줄인 판 + MANIFEST · 기본이 gz·행 삭제 채택안 · **파일을 더할 때는 이 스크립트의 `FILES` 표에 한 줄** — 지금 조사 중인 데이터도 같은 길) · 갱신용 수집·전처리 32개(82 · 파일표·갱신 순서는 `scripts/README.md`) | 올림 |
| `REPORT.md` | 데이터 설명(팀장 양식) | 올림 |
| `DATA_IN_GIT.md` | 올린 21파일 상세 — 읽는 코드 · 열 뜻 · 한 행 예시 · 원자료 · 생성 스크립트 · 등급 · 줄인 방법 · 검증 | 올림 |
| `DATA_NOT_IN_GIT.md` | 안 올린 것 — 원자료 · 개인 실측 · 라우터 캐시 · 로그 · 줄인 판에서 뺀 열·행 · 이유 · 드라이브 위치 | 올림 |

**읽는 코드**: `final_project_cs/app/modules/travel_ops/mobility/engine/paths.py` — 명령줄·시험은 `.env` 의 `DATA_DIR` 이 없으면 이 폴더(`datasets/mobility/processed`)를 자동으로 쓴다(pull 만 하면 `pytest tests/unit/travel/mobility` 회귀 게이트가 skip 없이 돈다). 서버는 `.env` 에 `ACOP_MOBILITY_DATA_DIR=datasets/mobility/processed`(마지막 폴더 이름이 `processed` 면 그 자리를 그대로 자료 폴더로 본다).
