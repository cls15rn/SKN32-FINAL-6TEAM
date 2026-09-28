# mobility_scripts/collect/walk02_tour_courses_dump.py — 30번 방. TourAPI 코스(contentTypeId=25) 전국 1,069건 목록을 받아
# 서울 필터가 왜 0인지(지역 필드가 비었나 / 코드가 바뀌었나) 목록 자체로 판정한다.
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk02_tour_courses_dump.py
# 저장: raw\mobility\walk_courses\tourapi_c25_list_<날짜>.jsonl (목록 필드만 · 약 11회 호출) + 분포 요약 출력
import collections, json, os, re, sys, time, urllib.parse
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

K = urllib.parse.unquote(os.environ["DATA_GO_KR_KEY"])
URL = "https://apis.data.go.kr/B551011/KorService2/areaBasedList2"
BASE = {"serviceKey": K, "MobileOS": "ETC", "MobileApp": "ACOP", "_type": "json", "contentTypeId": 25, "numOfRows": 100, "arrange": "C"}
KEEP = ("contentid", "title", "addr1", "addr2", "areacode", "sigungucode", "lDongRegnCd", "lDongSignguCd",
        "cat1", "cat2", "cat3", "lclsSystm1", "lclsSystm2", "lclsSystm3", "mapx", "mapy", "modifiedtime", "cpyrhtDivCd")

rows, page, total = [], 1, None
while True:
    r = requests.get(URL, params={**BASE, "pageNo": page}, timeout=30)
    b = r.json()["response"]["body"]
    total = b.get("totalCount")
    it = (b.get("items") or {})
    it = it.get("item", []) if isinstance(it, dict) else []
    if isinstance(it, dict):
        it = [it]
    rows += [{k: i.get(k) for k in KEEP} | {"_other_keys": sorted(set(i) - set(KEEP))} for i in it]
    print(f"page {page}: +{len(it)} (누적 {len(rows)}/{total})")
    if not it or len(rows) >= (total or 0):
        break
    page += 1
    time.sleep(0.3)

out = RAW_MOBILITY / "walk_courses" / f"tourapi_c25_list_{time.strftime('%Y%m%d')}.jsonl"
with open(out, "w", encoding="utf-8") as f:
    for x in rows:
        f.write(json.dumps(x, ensure_ascii=False) + "\n")

def c(k, n=12):
    return collections.Counter(str(x.get(k)) for x in rows).most_common(n)

print("\n총", len(rows), "건 · 목록 외 키:", sorted({k for x in rows for k in x["_other_keys"]}))
for k in ("areacode", "lDongRegnCd", "cat1", "cat2", "cat3", "lclsSystm1", "lclsSystm2", "lclsSystm3", "cpyrhtDivCd"):
    print(f"  {k:12s}", c(k))
seoul = [x for x in rows if re.search(r"서울", (x.get("addr1") or "") + (x.get("title") or ""))
         or (x.get("mapx") and x.get("mapy") and 126.76 <= float(x["mapx"]) <= 127.19 and 37.42 <= float(x["mapy"]) <= 37.70)]
print(f"\n서울(주소·제목 '서울' 또는 좌표 상자 안) {len(seoul)}건")
for x in seoul[:40]:
    print(" ", x["contentid"], x["title"], "|", x.get("addr1"), "|", x.get("areacode"), x.get("lDongRegnCd"), x.get("cat2"), x.get("lclsSystm2"), x.get("mapx"), x.get("mapy"))
print("\n기록:", out)
