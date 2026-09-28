# PR 노트 — 이동·동선 모듈(`role-mobility` → `develop`) v1

2026-09-28 · 서유현(Mobility) · 기준 `origin/develop` `45bfb7a`(merge 반영 완료 · 충돌 0 · 9/28 저녁 재확인 「Already up to date」) · 팀장 커밋 `3c73687`(시험 이동) 포함 · **v1.1: 엔진을 팀 폴더 `travel_ops/mobility/engine/` 안으로**(팀장 요청 「세 팀 같은 모양」)

## 1. 한 줄

이동 모듈은 **값을 내는 함수 하나**(`plan()`)와 그 판정기다. 코어(팀장) 배선은 `docs/mobility/2026-09-25_Mobility_호출안내_v2.md` 대로 부르면 되고, **팀 파일 수정은 0** 이다(보호 경로 검사 `git diff --exit-code origin/develop -- .env.teamflow.example final_project_cs/scripts scripts/README.md scripts/build_competitor_doc.py final_project_cs/app/modules/travel_ops/mobility/__init__.py final_project_cs/app/modules/travel_ops/mobility/team.py final_project_cs/app/modules/travel_ops/route_uses.py final_project_cs/config` → 차이 0).

## 2. 어디에 무엇이 있나 (`code-layout.md` 9/28 기준)

| 자리 | 내용 |
|---|---|
| `final_project_cs/app/modules/travel_ops/mobility/` | **팀장 폴더 그대로**(`__init__.py` 다시 내보내기 · `team.py` 본체 · 둘 다 무수정) — 허용 방식 「폴더」. 등록 문자열 `app.modules.travel_ops.mobility:MobilityTeam` 불변 |
| `final_project_cs/app/modules/travel_ops/mobility/engine/` | 우리 판정 엔진(`verify_time.py`) · 계획 `plan.py`(`plan()` · CLI) · 후보·요금·규칙(`rules/rules_v0.3.json` — 내부 버전 v0.9.1). **팀 폴더의 하위 패키지**(`__init__.py` 문구 「하위 폴더를 더 두는 것도 자유」) · 옛 자리 `travel_ops/mobility_engine/` 는 없어짐 · `team.py` 는 엔진을 import 하지 않는다(배선은 코어 몫) |
| `final_project_cs/tests/unit/travel/mobility/` | pytest 시험 10파일 + 회귀 케이스 JSON 14묶음(팀장이 9/28 `tests/mobility/` 에서 옮김 · **CI 가 돈다**) |
| `mobility_scripts/` | 수집·빌드·자기점검·지표 스크립트(**이번 PR 에서 루트 `scripts/` 에서 옮김** — §4) · `mobility_checks/` = 기기 자료가 있어야 도는 점검·계약 검사·픽스처 생성기(팀장이 `scripts/mobility_checks/` 에 두었던 것을 같은 이유로 여기로) |
| `docs/mobility/` · `config/mobility/` · `sql/mobility*` | 설계·호출 안내·규칙 보조표·DB 스키마(참고용) |

## 3. 데이터는 git 밖이다

시간표·역 좌표·버스 프로파일 같은 원자료·가공물은 **드라이브(`DATA_DIR/travel/…`)** 에 있고 저장소에는 없다(`.env` 의 `DATA_DIR` 하나로 위치를 잡는다). 그래서

- **CI·데이터 없는 기기**: `cd final_project_cs; python -m pytest tests/unit/travel/mobility -q` → 실데이터 시험은 `skip`(사유 「시간표 없음(DATA_DIR)」), 합성 픽스처 시험만 돈다 — **73 passed · 60 skipped · error 0**.
- 데이터 있는 기기: **133 passed**.
- `.env` 가 없어도 import·`plan()` 은 돈다(라우터 주소는 규칙 파일 기본값 · 외부 키 없으면 그 기능만 근거없음/꺼짐). 단 `mobility_scripts/check_travel_min_vs_official.py` 하나는 `.env` 를 직접 읽는다(pytest 범위 밖 조사 스크립트).

## 4. 이번 PR 에서 구조만 바꾼 것 — 판정 규칙·출력 무변경

- 루트 `scripts/`(우리) 와 `final_project_cs/scripts/`(팀)가 **둘 다 정규 패키지 `scripts`** 여서 경로 순서에 따라 한쪽만 잡히던 문제 → 우리 것을 **`mobility_scripts/`** 로 옮겼다(`git mv` · 29파일 + `collect/` 45 + `probe/` 4 + `mobility_checks/`). 루트 `scripts/` 에는 팀 파일(`README.md` · `build_competitor_doc.py`)만 남아 네임스페이스 패키지가 된다 — 우리 코드는 `scripts` 를 import 하지 않는다. 저장소 루트에서 `python -m mobility_scripts.<이름>` 이 PYTHONPATH 없이 돈다.
- 시험 위치·pytest 입구 함수는 팀장 커밋 `3c73687` 대로(우리가 같은 날 같은 수정을 했으나 팀장 판을 택함). 우리 쪽 추가: `test_bus_window_norm` 경로만 `mobility_scripts/collect`.
- 9/20 첫 커밋에 잘못 들어간 파일 5개 제거(루트 `__init__.py` · 빈 `modules/` 패키지 · 옛 메모 md 2) · `.env.teamflow.example` 은 develop 판으로 되돌림.
- 골든 1곳 교체: 팀 `route_uses.py` 가 버스 노선명 검사를 넓혀(`01A`·`702A` 등 59노선 수용) 예시 출력의 **요약 한 줄**만 바뀜(`before_prev_end 41 · uses_format 2` → `before_prev_end 43`) · 이동 항목·경로 값 동일 · 새 uses 전부 팀 검사 통과.
- 엔진 5파일은 주석의 경로 문자열만.
- **(v1.1 · 9/28 저녁) `mobility_engine/` → `mobility/engine/`** — 팀장 요청(세 팀이 `travel_ops/<팀>/` 폴더 한 곳에서 같은 모양으로). 23파일 `git mv`(rename 100%·유사도 유지) + 경로 문자열 치환 **160곳/50파일**(엔진 7 · 시험 12 · `mobility_scripts/` 28 · docs 3 — import · `-m` 명령 · `Path` 조각 · 주석) + 엔진 안 실제 코드 2줄: `options.py` `from ..route_uses` → `from ...route_uses`(한 칸 깊어짐), `paths.py` `.git` 없는 배포본 fallback `parents[5]` → `[6]`(정상 경로는 `.git` 앵커라 무관). 팀 파일 수정 **0**. `test_team_tool_discipline`(금지 import 이름의 AST 검사 — `app.infrastructure`·`psycopg`·`openai`·`app.presentation`·`app.application`)가 훑는 엔진 파일 21 은 자리만 바뀌고 그대로 통과한다(41 passed). 이 검사는 import 이름만 보므로 「인프라 접근 0」의 증명이 아니다 — 엔진의 바깥 접점은 `paths.py` 의 `.env` 읽기(`dotenv`)와 `options.py` 의 팀 공용 `route_uses` 뿐이다. 이관 전후 23쌍은 `scratch\_69\verify_69_pairs.py` 로 「승인한 치환 + 코드 2줄 + 독스트링」 외 바이트 차이 0 을 확인. 실행 명령은 `app.modules.travel_ops.mobility.engine.<모듈>` 로 바뀜(§6).

## 5. 숫자(기기 노트북 `playdata` · 2026-09-28 · GraphHopper 없음 · `mobility/engine/` 자리에서 재측정)

- 판정 회귀 **14묶음 171건 어긋남 0**(라우터 없는 기기라 alt 4건 SKIP — 종전과 같음)
- pytest(cs) 133 / 데이터 없음 73+60skip · 팀 배치 검사 `test_team_layout`·`test_team_tool_discipline` **41 passed**(우리 `mobility/engine/` 21파일 포함)
- 계약·점검 스크립트: 어댑터 30 · 런타임 26 · fold 0실패 · 자기점검 불변식 13 · bike 0실패 · passes 6 · 규칙표 통과
- 자기점검 탐침 **15,792**(불가 6,155 · 성립 8,089 · 판단불가 1,269 · 상한 279) · 치명 0
- `ruff check .`(develop CI 관문 F·E9) **0건**
- `plan()` 예시 골든 2파일 재생성 → 이관 전과 **SAME** · `final_project_cs` 만 sys.path 에 두고 `import app.modules.travel_ops.mobility` · `…mobility.engine.runtime` 통과 · 데이터 없는 기기에서 `build_verifier()` 는 종전대로 `RuntimeError: 판정기 입력이 없다` 로 안내

## 6. 기기에서 회귀를 다시 돌리는 법

```powershell
cd C:\...\SKN32-FINAL-6TEAM
# pytest (CI 와 같은 모양 · 데이터 없어도 됨)
cd final_project_cs; python -m pytest tests/unit/travel/mobility -q; cd ..
# 판정 회귀 한 묶음 (데이터 필요)
$env:PYTHONPATH = "final_project_cs"
python -m app.modules.travel_ops.mobility.engine.verify_time --cases final_project_cs/tests/unit/travel/mobility/real_legs_v1.json --check-expect
# 자기점검 (데이터 필요 · 느림)
python -m mobility_scripts.selfcheck_mobility --seeds "final_project_cs/tests/unit/travel/mobility/*_legs_v1.json"
# 팀 파일을 건드리지 않았는지
git diff --exit-code origin/develop -- .env.teamflow.example final_project_cs/scripts scripts/README.md scripts/build_competitor_doc.py final_project_cs/app/modules/travel_ops/mobility/__init__.py final_project_cs/app/modules/travel_ops/mobility/team.py final_project_cs/app/modules/travel_ops/route_uses.py final_project_cs/config
```
전체 묶음은 `scratch\_69\s4_fix_run.ps1`(git 밖 · 담당자 기기 · (3)~(11) 구간).

## 7. 알려진 한계(밝혀 둔다)

- **요금 커버 56%** — 지하철 요금은 탄 간선 전부 거리가 확정된 쌍(관광지 30역 쌍 432 중 243)에서만 값이 나온다. 10 km 넘는 쌍·9호선 등 거리 모르는 노선·버스 섞인 환승은 `fare_krw` 없음 → 코어 재계획이 그 후보를 「요금 미상」으로 떨어뜨리는 것은 정상 동작.
- **관광 구간 실측 0** — 실측 정답(26여정)은 담당자 통근·생활 동선(742·040·4319·3·7호선)이다. 여유 적중률 11/26·상한 초과 0 은 「이 노선들에서」다.
- 자전거는 추천하지 않는다 — `modes=["bike","walk"]` 로 여행자가 고른 때만 후보로 실린다.
- 환승 칸 안내는 뺐다(공공 원천 두 곳이 43% 서로 달라 안 믿는다) · 하차 칸은 API 계단·엘리베이터 칸만(POI 확정 뒤).
- GraphHopper(자동차·자전거 라우터)는 기기별 로컬 — 없는 기기는 해당 판정이 근거없음으로 나온다.
- `requirements-mobility.txt` 는 **수집·분석 스크립트용 별도 환경**이다 — 팀 `final_project_cs/requirements.txt` 와 고정 버전이 다르다(numpy 2.2.1↔1.26.4 · scipy · scikit-learn · `pywin32`). 팀 env 에 같이 설치하지 않는다. 엔진·시험은 팀 requirements 만으로 돈다.
- `pytest tests/unit/travel` 전체는 팀 `test_planner.py` 가 CI 환경변수(`ACOP_*`) 없이는 수집 단계에서 멈춘다 — 우리 폴더만 돌릴 때는 무관.
- **엔진 import 순서**(9/28 이관 뒤): `app.modules.travel_ops.mobility.engine.*` 를 부르면 팀장 `mobility/__init__.py` → `team.py` → 코어 계약이 먼저 올라온다(옛 자리도 `travel_ops/__init__.py` 가 여섯 팀을 먼저 불렀으니 「팀 venv 필요」 조건은 같다). `team.py` 가 나중에 엔진을 import 하게 배선할 때는 새 프로세스에서 「engine 먼저 · team 먼저 · 코어 먼저」 세 순서를 한 번씩 돌려 순환이 없는지 본다(GPT 대조).
- **축소 배포**(`final_project_cs` 내용만 복사 · `.git` 없음): `paths.py` 는 `.git` 앵커 → 없으면 `parents[6]`(= 전체 저장소 배치의 루트). 컨테이너에 `.env` 를 그 자리에 둘 수 없으면 **환경변수 `DATA_DIR` 주입이 계약**이다(`load_dotenv` 는 이미 있는 환경변수를 덮지 않는다). Docker 관문(main)은 이 PR 범위 밖.
- `mobility_scripts/budget_probe_measure_v1.py` 는 엔진을 별칭 패키지(`mobility_engine`)로 파일째 든다 — `car`·`paths` 까지만 되고(`options` 는 팀 공용 `route_uses` 상대 import 때문에 불가) 정식 경로와 한 프로세스에서 섞지 않는다.

## 8. 코어 쪽에 이미 보낸 것

`팀장전달_모음.md`(12항목 · 통지 7 · 질문 2 · 제안 3) — 이 PR 과 별도. 코어 계약에 **새 키 0**(기존 칸 `starts_at`·`ends_at`·`eta_min`·`uses`·`label`·`planned` 만). 이 PR 로 새로 알릴 것 둘: ① `scripts/mobility_checks/` → `mobility_scripts/mobility_checks/`(루트 `scripts` 는 팀 것만 · `code-layout.md` 의 예시 경로와 다름) ② 엔진이 `mobility_engine/` 에서 `mobility/engine/` 으로 들어갔다 — `code-layout.md` 의 「파일 + 엔진 `mobility.py` + `mobility_engine/`」 예시 줄은 이제 실물이 없다(폴더 방식 하나로 통일).
