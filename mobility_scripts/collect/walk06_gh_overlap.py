# mobility_scripts/collect/walk06_gh_overlap.py — 30번 방. 「13번 보행망과 겹침」 검수.
# OSM 도보 루트(walk05b 산출) 선 위에 250 m 마다 경유점을 찍어 로컬 GraphHopper foot 로 잇고,
#   ratio = GH 거리 / 코스 선 길이  (1.0 에 가까우면 우리 보행망이 그 길을 그대로 따라간다)
#   snap = 경유점이 보행망에 붙을 때 옮겨진 거리(m)  (크면 보행망에 그 길이 없다)
# 을 잰다. GH 응답은 저장하지 않는다(숫자만 csv). 서버: graph\gh 에서 .\gh_build.ps1 (localhost:8989).
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk06_gh_overlap.py [--all]   (기본: 둘레길·한양도성 계열만)
import csv, json, math, re, sys, time
from pathlib import Path
import requests
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import linemerge, transform, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

D = RAW_MOBILITY / "walk_courses"
GH = "http://localhost:8989"
STEP, CHUNK = 250.0, 40
TO_M = Transformer.from_crs(4326, 5186, always_xy=True).transform
TO_LL = Transformer.from_crs(5186, 4326, always_xy=True).transform
PICK = r"서울둘레길|Seoul City Trail|한양도성|성곽"

def hav(a, b):
    (x1, y1), (x2, y2) = a, b
    return 6371000 * 2 * math.asin(math.sqrt(math.sin(math.radians(y2 - y1) / 2) ** 2 +
           math.cos(math.radians(y1)) * math.cos(math.radians(y2)) * math.sin(math.radians(x2 - x1) / 2) ** 2))

def gh_leg(pts):
    r = requests.post(GH + "/route", json={"points": pts, "profile": "foot", "points_encoded": False,
                                           "instructions": False, "calc_points": False}, timeout=60)
    j = r.json()
    if "paths" not in j:
        return None, None, j.get("message", str(r.status_code))[:80]
    p = j["paths"][0]
    snapped = p.get("snapped_waypoints", {}).get("coordinates", [])
    snaps = [hav(a, b) for a, b in zip(pts, snapped)] if len(snapped) == len(pts) else []
    return p["distance"], snaps, None

try:
    info = requests.get(GH + "/info", timeout=3).json()
    print("GH", info.get("version"), [p["name"] for p in info.get("profiles", [])])
except Exception as e:
    raise SystemExit(f"GH 서버 없음({type(e).__name__}) — graph\\gh 에서 .\\gh_build.ps1 먼저")

feats = json.loads((D / "osm_walk_routes_seoul_pbf.geojson").read_text(encoding="utf-8"))["features"]
if "--all" not in sys.argv:
    feats = [f for f in feats if re.search(PICK, f["properties"]["name"] + f["properties"]["name_en"])]
out = D / f"walk06_gh_overlap_{time.strftime('%Y%m%d')}.csv"
W = csv.writer(open(out, "w", newline="", encoding="utf-8-sig"))
W.writerow(["osm_rel", "name", "part", "part_len_m", "gh_m", "ratio", "n_pts", "snap_med_m", "snap_p90_m", "snap_max_m", "err"])
tot = []
for f in feats:
    p = f["properties"]
    g = unary_union(transform(TO_M, shape(f["geometry"])))
    mer = linemerge(g)
    parts = list(mer.geoms) if hasattr(mer, "geoms") else [mer]
    for k, ln in enumerate(sorted(parts, key=lambda l: -l.length)):
        if ln.length < 200:
            continue
        n = max(3, int(ln.length // STEP) + 1)  # 닫힌 고리도 3점 이상
        pts = [list(TO_LL(*ln.interpolate(i * ln.length / (n - 1)).coords[0])) for i in range(n)]
        dist, snaps, err = 0.0, [], None
        for s in range(0, n - 1, CHUNK - 1):
            seg = pts[s:s + CHUNK]
            d, sn, e = gh_leg(seg)
            if e:
                err = e
                break
            dist += d
            snaps += sn if s == 0 else sn[1:]
            time.sleep(0.05)
        sn = sorted(snaps)
        q = lambda a: round(sn[min(len(sn) - 1, int(a * len(sn)))], 1) if sn else ""
        ratio = round(dist / ln.length, 3) if not err else ""
        W.writerow([p["osm_rel"], p["name"], k, round(ln.length), round(dist) if not err else "", ratio, n, q(0.5), q(0.9), round(sn[-1], 1) if sn else "", err or ""])
        tot.append((p["name"], k, round(ln.length), ratio, q(0.9), err))
        print(f"  {p['name'][:28]:28s} 조각{k} {ln.length/1000:5.2f} km  GH/선={ratio!s:6s} snap p90={q(0.9)!s:5s} m {err or ''}")
ok = [t for t in tot if t[3] != "" and 0.9 <= t[3] <= 1.15]
print(f"\n조각 {len(tot)} · GH/선 0.90~1.15 안 {len(ok)} · 오류 {sum(1 for t in tot if t[5])}\n기록: {out}")
