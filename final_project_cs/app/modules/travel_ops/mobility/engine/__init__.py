# -*- coding: utf-8 -*-
"""이동·동선 판정 엔진 — 자기완결 패키지.

안에서는 상대 import 만 쓴다. 밖으로 나가는 의존은 둘이다(69 GPT 대조 #2 정정 — 종전 「하나뿐」은 거짓):
  ① `paths.py` 가 저장소 루트의 `.env` 에서 `DATA_DIR` 을 읽는다(파일 읽기지 import 가 아니다 · 환경변수가 있으면 그게 우선).
  ② `options.py` 가 팀 공용 `travel_ops/route_uses.py` 를 `from ...route_uses` 로 든다(후보 uses 형식 검사 · 팀장 소유).
그래서 이 패키지는 `final_project_cs/` 만 sys.path 에 있으면 돈다 —
저장소 루트가 sys.path 에 없어도 된다. 별칭 패키지로 파일째 드는 방식(`budget_probe_measure_v1.py`)은
② 때문에 `options`·`plan` 까지는 못 든다(`car`·`paths` 만) — 정식 경로와 별칭을 한 프로세스에서 섞지 않는다.

★ 31번 방 2차(2026-09-21) — app/infrastructure/travel/mobility → app/modules/travel_ops/mobility/engine.
  infrastructure/travel 은 여러 팀이 같이 쓰는 바깥 피드(캐시·호출 제한·주기 폴링) 층이라
  우리가 필요할 때 부르는 엔진과 결이 달랐다. 세 팀이 travel_ops/<이름>_engine/ 으로 통일했다.
  이름이 mobility 가 아닌 건 옆의 팀장 mobility.py 와 부딪히지 않게 하려는 것이다.
  대가 하나 — 이 자리에서 import 하면 travel_ops/__init__.py 가 여섯 팀 모듈을 먼저 불러온다.
  팀 venv 가 있어야 하고, 다른 팀 import 가 깨지면 여기도 같이 멈춘다(반대도 마찬가지).

  from app.modules.travel_ops.mobility.engine.runtime import get_verifier

★ 69번 방(2026-09-28) — mobility_engine/ → mobility/engine/. 팀장이 세 팀을 travel_ops/<팀>/ 폴더로
  통일하며(develop 45bfb7a · `mobility/__init__.py`+`team.py`) 「이 팀의 파일은 전부 이 폴더 안에」로 바뀌어
  엔진을 팀 폴더의 하위 패키지로 넣었다. 팀장 파일(`__init__.py`·`team.py`)은 손대지 않았고 판정 규칙·출력은 그대로다.
  위 두 문단의 「옆의 팀장 mobility.py」·「mobility_engine」은 옛 기록이다.

★ 판정을 돌리려면 시간표가 필요하다. `.env` 의 `DATA_DIR` 이 없거나 그 아래
  `travel/processed/mobility/` 가 비어 있으면 `build_verifier()` 가
  `RuntimeError: 판정기 입력이 없다` 로 멈춘다 — 코드가 깨진 게 아니라 데이터가 없는 것이다.
  시간표(195MB)는 git 밖이다.
"""
