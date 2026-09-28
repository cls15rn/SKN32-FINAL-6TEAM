# mobility_scripts/

이동·동선 모듈(Mobility)의 **수집·빌드·자기점검·지표 스크립트**. 2026-09-28 에 루트 `scripts/` 에서 옮겼다 —
팀 `final_project_cs/scripts/` 와 패키지 이름 `scripts` 가 겹쳐 경로 순서에 따라 한쪽만 잡히던 문제를 없애기 위해서다.
루트 `scripts/` 는 팀 것(`README.md` · `build_competitor_doc.py`)만 남는다.

- `collect/` — 공공데이터 수집·가공(시간표·역 좌표·버스·환승 칸 등). 출력은 전부 **git 밖** `DATA_DIR/travel/…`(`.env`).
- `probe/` — 외부 API 응답 모양 조사(응답 원문은 저장하지 않는다).
- `mobility_checks/` — 기기 자료(DATA_DIR)가 있어야 도는 점검·계약 검사·픽스처 생성기(`check_*.py` · `contract/` · `make_*.py`). pytest 가 아니라 스크립트로 돈다(팀장 9/28 분리 · 원래 `scripts/mobility_checks/`).
- 루트 — 자기점검(`selfcheck_mobility.py`) · 판정 로그·지표(`judgment_log_run.py` · `judgment_metrics_report.py` · `mobility_metrics.py`) · 규칙 검사(`rules_check.py`) · 데모(`demo_render.py`) 등.

실행은 저장소 루트에서 `python -m mobility_scripts.<이름>` 또는 `python mobility_scripts/<이름>.py` (PYTHONPATH 불필요 — 각 스크립트가 `final_project_cs` 를 스스로 올린다).
판정 엔진 본체는 `final_project_cs/app/modules/travel_ops/mobility/engine/`, pytest 시험은 `final_project_cs/tests/unit/travel/mobility/`(팀장 9/28 이동 · CI 가 돈다).
