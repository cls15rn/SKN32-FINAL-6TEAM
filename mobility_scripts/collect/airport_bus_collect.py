# mobility_scripts/collect/airport_bus_collect.py — 인천공항 공항버스(리무진) 노선·시간표 수집
# 실행: 저장소 루트에서  python mobility_scripts/collect/airport_bus_collect.py
# 선행: 공공데이터포털 "인천국제공항공사_버스정보"(15095045) 활용신청 — 자동승인, 개발계정 1,000/일.
#       DATA_GO_KR_KEY 를 그대로 쓴다(같은 포털 계정 키).
# 왜 이 소스인가: 인바운드 1순위 고객은 인천공항으로 들어온다. 공항↔시내 구간은 공항철도(TAGO)와
#   공항버스 둘뿐인데 버스 쪽이 미확보였다. 이 API 는 노선별 첫차·막차와 평일/주말 시간표를 함께 준다.
# 한계: 인천공항 출·도착 노선만. 김포공항은 별도(§ 소싱 문서 3-5).
import os, json, time, requests
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY

KEY = os.environ["DATA_GO_KR_KEY"]
URL = "https://apis.data.go.kr/B551177/BusInformation/getBusInfo"
SOURCE = "data.go.kr/B551177/BusInformation"
KST = timezone(timedelta(hours=9))
OUT = RAW_MOBILITY / "incheon_airport_bus.json"

rows, page = [], 1
while True:
    r = requests.get(URL, params={"serviceKey": KEY, "type": "json", "numOfRows": 100, "pageNo": page}, timeout=30)
    if r.status_code != 200:
        print("HTTP", r.status_code, r.text[:300]); break
    try:
        body = r.json()["response"]["body"]
    except (KeyError, ValueError):
        print("응답 형식 이상:", r.text[:300]); break
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    rows += items
    total = int(body.get("totalCount") or 0)
    print(f"  page {page}: {len(items)}건 (누적 {len(rows)}/{total})")
    if not items or len(rows) >= total:
        break
    page += 1
    time.sleep(0.2)

doc = {"source": SOURCE, "endpoint": URL, "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
       "count": len(rows), "items": rows}
OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"노선 {len(rows)}개 → {OUT}")
if rows:
    print("필드:", sorted(rows[0]))
    print("예:", {k: rows[0][k] for k in list(rows[0])[:8]})
