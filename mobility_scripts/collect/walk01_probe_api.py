# mobility_scripts/collect/walk01_probe_api.py — 30번 방. TourAPI 도보코스 0건이 진짜 0인지, 두루누비 403이 신청 문제인지 가른다.
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk01_probe_api.py
# 저장: 건수·분류 분포만(raw\mobility\walk_courses\walk01_probe_<시각>.json). 키 값은 안 찍음.
import collections, json, os, re, sys, time, urllib.parse
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

K = urllib.parse.unquote(os.environ["DATA_GO_KR_KEY"])
BASE = {"serviceKey": K, "MobileOS": "ETC", "MobileApp": "ACOP", "_type": "json", "pageNo": 1}
TOUR = "https://apis.data.go.kr/B551011/KorService2/areaBasedList2"
OUT = {}

def call(url, **p):
    r = requests.get(url, params={**BASE, "numOfRows": 1, **p}, timeout=15)
    try:
        j = r.json()["response"]
        return r.status_code, j["header"]["resultCode"], j["body"].get("totalCount"), j["body"]
    except Exception:
        return r.status_code, None, None, re.sub(r"serviceKey=[^&\"<]+", "***", r.text[:200])

# ① TourAPI 코스(25) — 조건을 하나씩 좁혀 어디서 0이 되는지
for name, p in [
    ("c25_all_korea", {"contentTypeId": 25}),
    ("c25_area1", {"contentTypeId": 25, "areaCode": 1}),
    ("c25_ldong11", {"contentTypeId": 25, "lDongRegnCd": 11}),
    ("c25_area1_C01", {"contentTypeId": 25, "areaCode": 1, "cat1": "C01"}),
    ("c25_area1_C01_C0115", {"contentTypeId": 25, "areaCode": 1, "cat1": "C01", "cat2": "C0115"}),
    ("c25_all_C01_C0115", {"contentTypeId": 25, "cat1": "C01", "cat2": "C0115"}),
]:
    s, c, t, _ = call(TOUR, **p)
    OUT[name] = {"status": s, "code": c, "total": t}
    print(f"{name:22s} status={s} code={c} total={t}")
    time.sleep(0.3)

# ② 서울 코스 전체 1쪽(100행)에서 분류 값 분포 — cat2 / lclsSystm 어느 쪽이 채워져 있나
r = requests.get(TOUR, params={**BASE, "numOfRows": 100, "contentTypeId": 25, "areaCode": 1}, timeout=20)
items = (((r.json().get("response") or {}).get("body") or {}).get("items") or {})
items = items.get("item", []) if isinstance(items, dict) else []
dist = {k: collections.Counter(str(i.get(k)) for i in items).most_common(10) for k in ("cat1", "cat2", "cat3", "lclsSystm1", "lclsSystm2", "lclsSystm3")}
OUT["seoul_c25_page1"] = {"n": len(items), "dist": dist, "titles": [i.get("title") for i in items[:15]]}
print("\n서울 코스 1쪽", len(items), "행")
for k, v in dist.items():
    print(f"  {k:11s}", v)
print("  제목 예:", OUT["seoul_c25_page1"]["titles"])

# ③ 두루누비 — 403 이면 활용신청/승인 전
for op in ("courseList", "routeList"):
    s, c, t, b = call(f"https://apis.data.go.kr/B551011/Durunubi/{op}")
    OUT["durunubi_" + op] = {"status": s, "code": c, "total": t}
    print(f"durunubi_{op:10s} status={s} code={c} total={t}", "" if c else b)

fp = RAW_MOBILITY / "walk_courses" / f"walk01_probe_{time.strftime('%Y%m%d_%H%M')}.json"
fp.write_text(json.dumps(OUT, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n기록:", fp)
