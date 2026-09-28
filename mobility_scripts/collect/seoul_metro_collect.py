# mobility_scripts/collect/seoul_metro_collect.py — 서울 열린데이터광장 정적 데이터 (역 목록 + 2~9호선 첫차·막차)
# 실행: 저장소 루트에서  python mobility_scripts/collect/seoul_metro_collect.py   (호출 ≈ 43회 / 일 1,000)
import json, os, time, requests
from urllib.parse import quote
from _paths import RAW_MOBILITY

KEY = os.environ["SEOUL_OPENAPI_KEY"]
BASE = f"http://openapi.seoul.go.kr:8088/{KEY}/json"


def get(path: str) -> dict:
    r = requests.get(f"{BASE}/{path}", timeout=20)
    r.raise_for_status()
    return r.json()


def save(name: str, obj) -> None:
    p = RAW_MOBILITY / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {p}  ({p.stat().st_size // 1024} KB)")


# ① 노선별 역 정보 (OA-15442) — 전 노선 역코드·외부코드·영문/중문/일문 역명
SVC_STN = "SearchSTNBySubwayLineInfo"
stations = get(f"{SVC_STN}/1/1000/")[SVC_STN]["row"]
print(f"① 역 목록 {len(stations)}건")
save("stations_all.json", stations)

# ② 호선별 첫차·막차 (OA-15492) — 서울교통공사 제공분(2~9호선). 1·4호선·공항철도는 TAGO/코레일로
SVC_FL = "SearchFirstAndLastTrainbyLineServiceNew"
LINES = ["02호선", "03호선", "05호선", "06호선", "07호선", "08호선", "09호선"]
rows_all, coverage = [], {}
for line in LINES:
    n = 0
    for updn in ("1", "2"):          # 1 상행/내선, 2 하행/외선
        for dow in ("1", "2", "3"):  # 1 평일, 2 토요일, 3 휴일
            j = get(f"{SVC_FL}/1/1000/{quote(line)}/{updn}/{dow}/")
            if SVC_FL in j:
                rows_all += j[SVC_FL]["row"]
                n += len(j[SVC_FL]["row"])
            time.sleep(0.2)
    coverage[line] = n
    print(f"② {line}: {n}건")
save("first_last_2to9.json", rows_all)
print("② 커버리지:", coverage)
