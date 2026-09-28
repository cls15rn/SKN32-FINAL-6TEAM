# mobility_scripts/collect/_paths.py — 수집 스크립트 공용 경로. .env의 DATA_DIR 기준
import os
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]      # SKN32-FINAL-6TEAM/
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", r"/data"))
TRAVEL = DATA_DIR / "travel"
RAW_MOBILITY = TRAVEL / "raw" / "mobility"
RAW_BLOG = TRAVEL / "raw" / "blog"
PROCESSED = TRAVEL / "processed"

for p in (RAW_MOBILITY, RAW_BLOG, PROCESSED):
    p.mkdir(parents=True, exist_ok=True)
