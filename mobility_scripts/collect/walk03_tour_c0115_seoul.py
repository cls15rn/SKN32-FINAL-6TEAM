# mobility_scripts/collect/walk03_tour_c0115_seoul.py — 30번 방. TourAPI 코스 목록(walk02 산출)에서
# (1) 서울 경계(OSM rel 2297418) 안 출발점 코스를 유형별로 세고 (2) 도보코스(lclsSystm2=C0115)만 상세를 받는다.
# 상세 = detailIntro2(총거리·소요) + detailInfo2(경유 POI 순서) + 각 POI 좌표(detailCommon2). 선은 만들지 않는다(점 순서만).
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk03_tour_c0115_seoul.py
# 저장: walk_courses\seoul_boundary_osm.geojson · tourapi_c0115_seoul_<날짜>.jsonl
import collections, json, os, re, sys, time, urllib.parse
from pathlib import Path
import requests
from shapely.geometry import LineString, Point, mapping
from shapely.ops import polygonize, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

D = RAW_MOBILITY / "walk_courses"
DAY = time.strftime("%Y%m%d")
TYPES = {"C0112": "가족", "C0113": "나홀로", "C0114": "힐링", "C0115": "도보", "C0116": "캠핑", "C0117": "맛"}

# ① 서울 경계 — 한 번 만들어 두고 재사용. Overpass(미러 3곳) → 실패하면 로컬 PBF(osmium, 2~4분)
bf = D / "seoul_boundary_osm.geojson"
RID = 2297418

def _poly(lines):
    return unary_union(list(polygonize(unary_union(lines))))

def from_overpass():
    q = f"[out:json][timeout:90];rel({RID});out geom;"
    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                "https://overpass.private.coffee/api/interpreter"):
        try:
            r = requests.post(url, data={"data": q}, timeout=120, headers={"User-Agent": "ACOP-mobility/0.1 (course project)"})
            print(f"  overpass {url.split('/')[2]} → {r.status_code} {r.headers.get('content-type', '')[:30]}")
            if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                continue
            j = r.json()
            rel = j["elements"][0]
            lines = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]]) for m in rel["members"]
                     if m["type"] == "way" and m.get("role") in ("outer", "") and "geometry" in m]
            return _poly(lines), {"via": url.split("/")[2], "osm_base": j.get("osm3s", {}).get("timestamp_osm_base")}
        except Exception as e:
            print("  overpass 실패:", type(e).__name__)
    return None, None

def from_pbf():
    import osmium
    pbf = RAW_MOBILITY / "osm" / "south-korea-latest.osm.pbf"
    print("  PBF 에서 경계 조립:", pbf)
    wids = set()
    for o in osmium.FileProcessor(str(pbf), osmium.osm.RELATION):
        if o.id == RID:
            wids = {m.ref for m in o.members if m.type == "w" and m.role in ("outer", "")}
            break
    print("  outer way", len(wids))
    lines = []
    fp = osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY).with_locations("flex_mem")
    for o in fp:
        if o.is_way() and o.id in wids:
            lines.append(LineString([(n.lon, n.lat) for n in o.nodes]))
    return _poly(lines), {"via": "local_pbf", "pbf_mtime": time.strftime("%Y-%m-%d", time.localtime(pbf.stat().st_mtime))}

if not bf.exists():
    poly, meta = from_overpass()
    if poly is None or poly.is_empty:
        poly, meta = from_pbf()
    if poly.is_empty:
        raise SystemExit("서울 경계 조립 실패")
    bf.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature",
        "properties": {"source": f"OSM relation {RID}", "license": "ODbL", **meta},
        "geometry": mapping(poly)}]}), encoding="utf-8")
    print("  경계 저장:", bf, meta)
from shapely.geometry import shape  # noqa: E402
SEOUL = shape(json.loads(bf.read_text(encoding="utf-8"))["features"][0]["geometry"])
print("서울 경계 면적 km²(근사):", round(SEOUL.area * 111.0 * 88.2, 1))

rows = [json.loads(l) for l in open(sorted(D.glob("tourapi_c25_list_*.jsonl"))[-1], encoding="utf-8")]
def inside(x):
    try:
        return SEOUL.contains(Point(float(x["mapx"]), float(x["mapy"])))
    except Exception:
        return False
seoul = [x for x in rows if inside(x)]
print(f"전국 {len(rows)} · 서울 경계 안 출발점 {len(seoul)}")
print("  유형별(전국):", {TYPES.get(k, k): v for k, v in collections.Counter(x["lclsSystm2"] for x in rows).most_common()})
print("  유형별(서울):", {TYPES.get(k, k): v for k, v in collections.Counter(x["lclsSystm2"] for x in seoul).most_common()})
walk = [x for x in seoul if x["lclsSystm2"] == "C0115"]
print("서울 도보코스(C0115):", len(walk))

# ② 상세
K = urllib.parse.unquote(os.environ["DATA_GO_KR_KEY"])
B = "https://apis.data.go.kr/B551011/KorService2/"
BASE = {"serviceKey": K, "MobileOS": "ETC", "MobileApp": "ACOP", "_type": "json", "numOfRows": 50, "pageNo": 1}
NCALL = 0
def items(op, **p):
    global NCALL
    NCALL += 1
    time.sleep(0.2)
    r = requests.get(B + op, params={**BASE, **p}, timeout=20)
    try:
        it = (r.json()["response"]["body"].get("items") or {})
    except Exception:
        return None, re.sub(r"serviceKey=[^&\"<]+", "***", r.text[:200])
    it = it.get("item", []) if isinstance(it, dict) else []
    return ([it] if isinstance(it, dict) else it), None

POI = {}
def poi_xy(cid):
    if cid not in POI:
        it, err = items("detailCommon2", contentId=cid)
        POI[cid] = (it[0].get("mapx"), it[0].get("mapy"), it[0].get("contenttypeid")) if it else (None, None, err)
    return POI[cid]

out = D / f"tourapi_c0115_seoul_{DAY}.jsonl"
with open(out, "w", encoding="utf-8") as f:
    for x in walk:
        intro, e1 = items("detailIntro2", contentId=x["contentid"], contentTypeId=25)
        info, e2 = items("detailInfo2", contentId=x["contentid"], contentTypeId=25)
        i0 = (intro or [{}])[0]
        pts = []
        for s in sorted(info or [], key=lambda s: int(s.get("subnum") or 0)):
            mx, my, ct = poi_xy(s.get("subcontentid")) if s.get("subcontentid") else (None, None, None)
            pts.append({"subnum": s.get("subnum"), "name": s.get("subname"), "subcontentid": s.get("subcontentid"),
                        "mapx": mx, "mapy": my, "contenttypeid": ct,
                        "in_seoul": bool(mx and my and SEOUL.contains(Point(float(mx), float(my))))})
        rec = {"contentid": x["contentid"], "title": x["title"], "lclsSystm3": x["lclsSystm3"], "cpyrhtDivCd": x.get("cpyrhtDivCd"),
               "start": [x["mapx"], x["mapy"]], "distance": i0.get("distance"), "taketime": i0.get("taketime"),
               "schedule": i0.get("schedule"), "theme": i0.get("theme"), "points": pts,
               "src": f"tourapi_KorService2@{DAY}", "err": [e for e in (e1, e2) if e]}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  {x['contentid']} {x['title'][:24]:24s} 거리={rec['distance']!s:10s} 소요={rec['taketime']!s:10s} 점={len(pts)} 좌표있음={sum(1 for p in pts if p['mapx'])} 서울밖={sum(1 for p in pts if p['mapx'] and not p['in_seoul'])}")
print(f"\n호출 {NCALL}회 · 기록: {out}")
