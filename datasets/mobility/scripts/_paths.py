# datasets/mobility/scripts/_paths.py — 수집·전처리 스크립트 공용 경로. .env 의 DATA_DIR 기준
# 70: import 는 값만 정한다(부작용 없음). 폴더는 쓰는 스크립트가 쓰기 직전에 ensure_dirs() 로 만든다 —
#     읽기만 하는 곳(점검)은 부르지 않는다. DATA_DIR 미설정 기본값은 저장소 루트 기준 data/ (→ data/travel) ·
#     절대경로 /data 금지(.env 없는 CI 에서 import 시점 mkdir 이 PermissionError 로 수집을 죽였다).
# 91: API 키는 팀 양식 이름(ACOP_*)으로 final_project_cs/.env·.env.apikeys 에서 읽는다 — api_key(). 맨 위 .env 는 DATA_DIR 만.
# 82: 자리가 datasets/mobility/scripts/ 로 바뀌었다. 저장소 루트는 parents[n] 으로 세지 않고 final_project_cs/app 이 있는
#     조상을 위로 찾는다. 산출은 여전히 **정본** DATA_DIR/travel/processed/mobility/ — git 에 올리는 줄인 판
#     (datasets/mobility/processed/mobility/)은 reduce_75.py 가 정본에서 만든다(README 「갱신 순서」).
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / "final_project_cs" / "app").is_dir()), None)   # SKN32-FINAL-6TEAM/
if REPO_ROOT is None:                      # 82 GPT 1: datasets/ 만 떼어 온 사본은 지원하지 않는다 — 조용히 틀린 자리 대신 이유를 말하고 멈춘다
    raise SystemExit(f"저장소 루트(final_project_cs/app 이 있는 폴더)를 못 찾았다: {Path(__file__).resolve()} — 저장소 전체에서 실행한다")
load_dotenv(REPO_ROOT / ".env")

_env_data_dir = os.environ.get("DATA_DIR", "").strip()
DATA_DIR_SET = bool(_env_data_dir)         # 82 GPT 2: 쓰기는 DATA_DIR 을 정해야만 한다(ensure_dirs)
DATA_DIR = Path(_env_data_dir) if _env_data_dir else REPO_ROOT / "data"
if not DATA_DIR.is_absolute():             # 82 GPT 1: 상대경로는 실행 cwd 가 아니라 저장소 루트 기준으로 푼다
    DATA_DIR = REPO_ROOT / DATA_DIR
TRAVEL = DATA_DIR / "travel"
RAW_MOBILITY = TRAVEL / "raw" / "mobility"
RAW_BLOG = TRAVEL / "raw" / "blog"
PROCESSED = TRAVEL / "processed"

# 91: 키 이름은 팀 양식(final_project_cs/.env.apikeys.example) 그대로. 앱 설정(app.core.settings)과 같은 순서로 읽는다 —
#     환경변수 → final_project_cs/.env → final_project_cs/.env.apikeys(뒤 파일이 이긴다). 옛 이름(DATA_GO_KR_KEY 등)은 안 본다.
CS_ROOT = REPO_ROOT / "final_project_cs"
#     공공데이터포털은 양식 규칙대로 「서비스별 칸 → 비면 공통 키」(앱 Settings.data_go_kr_key(override) 와 같음).
DATA_GO_KR = "ACOP_DATA_GO_KR_KEY"                      # 공공데이터포털 공통 키 — 서비스마다 활용신청
KEY_NAMES = {"data_go_kr": (DATA_GO_KR,),
             "subway_alert": ("ACOP_SUBWAY_ALERT_API_KEY", DATA_GO_KR),  # 서울교통공사 지하철알림정보 15144070
             "seoul_bus": ("ACOP_SEOUL_BUS_API_KEY", DATA_GO_KR),        # 서울특별시 노선정보조회 15000193
             "tago": ("ACOP_TAGO_API_KEY", DATA_GO_KR),  # TAGO 지하철정보 15098554
             "holiday": ("ACOP_HOLIDAY_API_KEY", DATA_GO_KR),  # 천문연 특일 15012690
             "seoul": ("ACOP_SEOUL_OPENAPI_KEY",)}       # 서울 열린데이터광장 일반 인증키


def api_key(kind: str) -> str:
    """수집 스크립트용 키 — 없으면 이유를 말하고 멈춘다(호출 전). 공공데이터포털 키는 앱과 같이 unquote 한다."""
    from urllib.parse import unquote

    from dotenv import dotenv_values
    names = KEY_NAMES[kind]
    merged: dict = {}
    for f in (CS_ROOT / ".env", CS_ROOT / ".env.apikeys"):
        if f.exists():
            merged.update(dotenv_values(f))
    v = ""
    for name in names:
        v = (os.environ.get(name) or merged.get(name) or "").strip()
        if v:
            break
    if not v:
        raise SystemExit(f"{' / '.join(names)} 이 비어 있다 — final_project_cs/.env.apikeys 에 채운다(양식 .env.apikeys.example)")
    return unquote(v) if kind != "seoul" else v


def ensure_dirs() -> None:
    """산출 폴더 셋을 만든다 — 수집 스크립트가 쓰기 직전에 한 번 부른다(import 시점엔 만들지 않는다).

    82 GPT 2: 정본 갱신 도구는 **정본 자리를 명시했을 때만** 쓴다 — `.env`(또는 환경)에 DATA_DIR 이 없거나,
    그 자리가 저장소 안 git 데이터 폴더(datasets/)면 첫 쓰기 전에 멈춘다(읽기용 기본값을 쓰기에 재사용하지 않는다)."""
    if not DATA_DIR_SET:
        raise SystemExit("DATA_DIR 이 없다 — 정본 자리(드라이브 동기화 폴더)를 .env 의 DATA_DIR 에 적고 다시 돌린다(쓰기 전 멈춤)")
    if DATA_DIR.resolve().is_relative_to((REPO_ROOT / "datasets").resolve()):
        raise SystemExit(f"DATA_DIR 이 저장소 안 datasets/ 다({DATA_DIR}) — git 데이터는 reduce_75.py 로만 만든다(쓰기 전 멈춤)")
    for p in (RAW_MOBILITY, RAW_BLOG, PROCESSED):
        p.mkdir(parents=True, exist_ok=True)
