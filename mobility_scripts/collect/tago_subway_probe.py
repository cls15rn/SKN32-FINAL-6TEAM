# mobility_scripts/collect/tago_subway_probe.py — TAGO 지하철정보: 1·4호선·공항철도 시간표 있는지 확인
import os, json, requests
from _paths import RAW_MOBILITY  # noqa: F401 — import 자체가 .env(load_dotenv)를 읽는다 · 아래 DATA_GO_KR_KEY 가 그 값(56)

KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "https://apis.data.go.kr/1613000/SubwayInfo"
COMMON = {"serviceKey": KEY, "_type": "json", "numOfRows": 100, "pageNo": 1}

def call(op, **p):
    r = requests.get(f"{BASE}/{op}", params={**COMMON, **p}, timeout=20)
    if r.status_code != 200:
        print("HTTP", r.status_code, r.text[:500])   # 에러 본문 확인
        r.raise_for_status()
    return r.json()["response"]["body"]

# ① 키워드로 역 ID 찾기 — 노선별로 ID가 따로 나옴
body = call("GetKwrdFndSubwaySttnList", subwayStationName="서울역")
items = body["items"]["item"]
for it in items:
    print(it["subwayRouteName"], it["subwayStationId"], it["subwayStationName"])

# ② 역별 시간표 — dailyTypeCode 01 평일/02 토/03 일·공휴일, upDownTypeCode U 상행/D 하행
for it in items:
    b = call("GetSubwaySttnAcctoSchdulList", subwayStationId=it["subwayStationId"],
             dailyTypeCode="01", upDownTypeCode="U")
    n = b.get("totalCount", 0)
    print(f"{it['subwayRouteName']:12} {it['subwayStationId']}  시간표 {n}건")
    if n and "item" in b["items"]:
        print("   예:", {k: b["items"]["item"][0][k] for k in list(b["items"]["item"][0])[:6]})


print(json.dumps(b["items"]["item"][0], ensure_ascii=False))