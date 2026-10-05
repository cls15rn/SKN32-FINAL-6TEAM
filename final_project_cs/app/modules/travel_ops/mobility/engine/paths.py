# -*- coding: utf-8 -*-
"""데이터 경로 — 이동 엔진이 읽는 자료 폴더(DATA_DIR) 한 곳.

☆`[2026-09-29 문제목록 #48]` **import 할 때 `.env` 를 읽지 않는다.** 앞 판은 이 모듈을 불러오는 순간 저장소 맨 위
  `.env` 를 통째로 `os.environ` 에 넣었다(load_dotenv) — 서버 프로세스 안에서 앱 설정(app.core.settings)과 다른 길로
  환경이 바뀌는 숨은 통로였다. 이제 값이 들어오는 길은 둘이다.
    · 서버      — 기동 때 `configure(settings.mobility_data_dir)`(출처 "settings"). 설정의 정본은 app.core.settings.
    · 명령줄·시험 — `load_cli_env()` 가 저장소 맨 위 `.env` 의 DATA_DIR 을 읽는다(출처 "cli_env"). 데이터 기기의
      판정 회귀·자기점검·pytest 가 지금처럼 돈다. `runtime.build_verifier` 는 아무도 설정하지 않았을 때만 이것을 부른다.
  아무 것도 안 했으면 출처는 "unset" 이고 경로는 존재하지 않는 `/data` 다 — 적재가 「판정기 입력이 없다」로 멈춘다.

수집 쪽 `datasets/mobility/scripts/_paths.py` 와 같은 폴더 규칙(DATA_DIR/travel/raw·processed)을 쓴다.

_paths.py 와 다른 점 둘
  · **폴더를 만들지 않는다.** 판정 엔진이 import 만으로 빈 폴더를 만드는 건 맞지 않는다
  · 저장소 루트를 `parents[n]` 으로 세지 않는다. `.git` 을 앵커로 위로 훑는다 —
    패키지 자리를 옮겨도 안 깨지고, 누가 `final_project_cs/.env` 를 놓아도 안 속는다

★ 다른 모듈은 `from .paths import PROCESSED` 를 **함수 안에서** 한다 — configure() 뒤의 값을 본다.
  모듈 맨 위에서 값을 복사해 두면 configure() 가 안 먹는다.

☆`[2026-09-29 75번 방 · 데이터를 저장소 안 datasets/ 로]` 자료 폴더 모양이 둘이 됐다.
    · 종전  DATA_DIR/travel/processed/mobility/…            (드라이브 동기화 폴더 · `.env` DATA_DIR)
    · 저장소 <repo>/datasets/mobility/processed/mobility/…   (팀장 폴더 배정 9/29 · 서버 뜰 때까지 임시 · `git add -f` 로 추적)
  준 경로의 마지막 폴더 이름이 `processed` 이면 그 자리를 PROCESSED 로 본다(끝에 travel/processed 를 붙이지 않는다).
  명령줄·시험에서 DATA_DIR 이 없으면 저장소 안 폴더에 시간표가 있을 때만 그것을 쓴다(출처 "repo_datasets") —
  서버는 여전히 configure(settings.mobility_data_dir) 뿐이다(#48 그대로 · `.env` 에 ACOP_MOBILITY_DATA_DIR=datasets/mobility/processed).
"""
import os
from pathlib import Path


def _repo_root(start=None) -> Path:
    here = Path(start or __file__).resolve()
    for p in here.parents:
        g = p / ".git"
        # ☆`[2026-09-29]` 진짜 git 저장소만 센다 — 파일(작업 폴더 연결) 이거나 HEAD 가 든 폴더. 이 기기의 final_project_cs/.git 처럼
        #   `info/` 만 든 빈 폴더(찌꺼기)에 속으면 저장소 맨 위를 final_project_cs 로 잘못 잡아 datasets/ 를 못 찾는다
        if g.is_file() or (g.is_dir() and (g / "HEAD").exists()):
            return p
    # .git 이 없는 배포본 — 패키지에서 여섯 칸 위가 저장소 루트다(69: mobility/engine/ 로 한 칸 더 깊이)
    return here.parents[6]


REPO_ROOT = _repo_root()
RULES_DIR = Path(__file__).resolve().parent / "rules"
REPO_DATASETS = REPO_ROOT / "datasets" / "mobility" / "processed"   # 75: 저장소 안 자료 폴더(팀장 배정) — PROCESSED 자리
UNSET_DIR = Path("/data")          # 설정하지 않았을 때의 자리 — 있을 리 없는 경로라 적재가 멈춘다

SOURCE = "unset"
DATA_DIR = TRAVEL = RAW_MOBILITY = RAW_BLOG = PROCESSED = None


def _layout(data_dir, source):
    global SOURCE, DATA_DIR, TRAVEL, RAW_MOBILITY, RAW_BLOG, PROCESSED
    DATA_DIR = Path(data_dir)
    if DATA_DIR.name == "processed":
        # 75: 「processed 폴더」를 바로 받았다(저장소 datasets/mobility/processed 등) — 그 자리가 PROCESSED
        PROCESSED = DATA_DIR
        TRAVEL = DATA_DIR.parent
        RAW_MOBILITY = TRAVEL / "raw"
        RAW_BLOG = TRAVEL / "raw" / "blog"
    else:
        TRAVEL = DATA_DIR / "travel"
        RAW_MOBILITY = TRAVEL / "raw" / "mobility"
        RAW_BLOG = TRAVEL / "raw" / "blog"
        PROCESSED = TRAVEL / "processed"
    SOURCE = source


_layout(UNSET_DIR, "unset")


def configure(data_dir, source="settings"):
    """자료 폴더를 정한다(서버 기동 때). 빈 값은 받지 않는다 — 조용히 옛 자리를 쓰지 않는다."""
    if not data_dir:
        raise ValueError("이동 자료 폴더(mobility_data_dir)가 비었다")
    # ☆`[2026-09-29 자료 폴더 통일]` 상대 경로(`datasets/mobility/processed`)는 **저장소 맨 위 기준**으로 푼다 — 그냥 두면
    #   서버를 띄운 폴더(final_project_cs/ 등)에 따라 다른 곳을 봐 자료 확인이 실패하고 서버가 안 뜬다
    given = Path(data_dir)
    _layout(given if given.is_absolute() else REPO_ROOT / given, source)


def disable():
    """서버가 계산기를 끈다(설정 mobility_data_dir 비움). 이 뒤로는 명령줄 관례(.env)로도 켜지지 않는다."""
    _layout(UNSET_DIR, "disabled")


def load_cli_env():
    """명령줄 도구·시험 전용 — 저장소 맨 위 `.env` 를 읽고(dotenv) DATA_DIR 이 있으면 그 자리로.
    서버는 이것을 부르지 않는다(configure 로 설정 값을 넘긴다). 이미 환경변수가 있으면 그것이 이긴다(dotenv 규칙)."""
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    if os.environ.get("DATA_DIR"):
        _layout(os.environ["DATA_DIR"], "cli_env")
    elif timetable_file(REPO_DATASETS / "mobility").exists():
        _layout(REPO_DATASETS, "repo_datasets")     # 75: DATA_DIR 없으면 저장소 안 자료(pull 만 하면 시험이 돈다)
    return SOURCE


def timetable_file(mob_dir):
    """실 시간표 파일 — `timetable_v1.jsonl.gz` 가 있으면 그것, 없으면 `timetable_v1.jsonl`(73 후속 · 3-4).
    판정기(Timetable.load)는 확장자로 gzip 여부를 정한다. 둘 다 없으면 텍스트 경로(없는 파일)를 돌려 적재가 멈추게 한다."""
    gz = Path(mob_dir) / "timetable_v1.jsonl.gz"
    return gz if gz.exists() else Path(mob_dir) / "timetable_v1.jsonl"


def cli_processed():
    """명령줄 도구·점검 스크립트용 — 아무도 자료 폴더를 정하지 않았으면(출처 "unset") `.env` 를 읽고 PROCESSED 를 준다.

    ☆`[2026-09-29 문제목록 #48 뒤따름]` import 때 .env 를 안 읽게 바꾼 뒤, `from ...paths import PROCESSED` 를 바로 쓰던
    점검 스크립트(자전거 단위·28번 지나감·행선지 채우기·지표·자기점검)가 있을 리 없는 `/data` 를 보고
    「입력이 없다」로 멈췄다. 서버가 켜거나 끈 뒤(출처 settings·disabled)에는 건드리지 않는다."""
    if SOURCE == "unset":
        load_cli_env()
    return PROCESSED
