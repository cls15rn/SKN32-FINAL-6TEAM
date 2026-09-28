# -*- coding: utf-8 -*-
"""데이터 경로 — `.env` 의 DATA_DIR 하나가 값의 정본이다.

수집 쪽 `mobility_scripts/collect/_paths.py` 와 **같은 규칙**을 쓴다. 규칙이 두 군데 적혀 있는데,
값의 출처는 여전히 `.env` 하나다. 접는 자리는 배선 때다(그때 `ACOP_DATA_DIR` 로
팀 Settings 에 정식 필드를 만들지 같이 정한다).  → `mobility_scripts/collect/_paths.py`

_paths.py 와 다른 점 둘
  · **폴더를 만들지 않는다.** 판정 엔진이 import 만으로 빈 폴더를 만드는 건 맞지 않는다
  · 저장소 루트를 `parents[n]` 으로 세지 않는다. `.git` 을 앵커로 위로 훑는다 —
    패키지 자리를 옮겨도 안 깨지고, 누가 `final_project_cs/.env` 를 놓아도 안 속는다
"""
import os
from pathlib import Path

from dotenv import load_dotenv


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / ".git").exists():
            return p
    # .git 이 없는 배포본 — 패키지에서 여섯 칸 위가 저장소 루트다(69: mobility/engine/ 로 한 칸 더 깊이)
    return here.parents[6]


REPO_ROOT = _repo_root()
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", r"/data"))
TRAVEL = DATA_DIR / "travel"
RAW_MOBILITY = TRAVEL / "raw" / "mobility"
RAW_BLOG = TRAVEL / "raw" / "blog"
PROCESSED = TRAVEL / "processed"

RULES_DIR = Path(__file__).resolve().parent / "rules"
