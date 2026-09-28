# mobility_scripts/collect/walk08_crosscheck_seoulgov.py — 30번 방. OSM 둘레길·한양도성 선 ↔ 서울시 두드림길(2022, 공공누리 4유형) 대조.
# 서울시 API 는 선 좌표가 없다(SHP = 요약: 경계상자·점수·길이(도) + BLOB). 그래서 잰 것은 세 가지뿐:
#   d_rep   : 서울시 구간 대표점(LAT/LOT)에서 가장 가까운 OSM 선까지 거리(m)
#   osm_in_box : 서울시 구간 경계상자(+30 m) 안에 들어온 OSM 선 길이(m)  ↔  seg_len : 서울시 구간 길이(도→m 범위 중앙)
#   cover   : osm_in_box / seg_len  (1 근처 = 그 상자 안에 OSM 이 같은 만큼 그려져 있음 · 상자는 겹칠 수 있어 참고용)
# 서울시 좌표는 산출에 넣지 않는다. 결과 csv 도 seoul_gov\ 안에 둔다 → 빼려면 폴더째 삭제(--no-seoulgov 빌드).
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk08_crosscheck_seoulgov.py
import csv, json, re, sys
from pathlib import Path
from pyproj import Transformer
from shapely.geometry import Point, box, shape
from shapely.ops import transform, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

D = RAW_MOBILITY / "walk_courses"
G = D / "seoul_gov"
TO_M = Transformer.from_crs(4326, 5186, always_xy=True).transform
KM_DEG = (88.2 + 111.0) / 2  # 방향 모를 때 중앙값 · 오차 ±12%

feats = json.loads((D / "osm_walk_routes_seoul_pbf.geojson").read_text(encoding="utf-8"))["features"]
def osm(pat):
    fs = [f for f in feats if re.search(pat, f["properties"]["name"] + " " + f["properties"]["name_en"])]
    return unary_union([transform(TO_M, shape(f["geometry"])) for f in fs]), [f["properties"]["name"] for f in fs]

SETS = {"SdeDoDreamWay01LW": osm(r"서울둘레길"), "SdeDoDreamWay05LW": osm(r"Seoul City Trail|한양도성|성곽")}
out = G / "walk_courses_crosscheck_seoulgov_v1.csv"
W = csv.writer(open(out, "w", newline="", encoding="utf-8-sig"))
W.writerow(["service", "SN", "NM", "gtype", "npts", "seg_len_m", "d_rep_m", "osm_in_box_m", "cover"])
for svc, (O, names) in SETS.items():
    fp = sorted(G.glob(f"{svc}_*.json"))[-1]
    rows = json.loads(fp.read_text(encoding="utf-8"))
    print(f"\n== {svc} {len(rows)}구간 ↔ OSM {len(names)}개 루트 {O.length/1000:.1f} km")
    res = []
    for r in rows:
        v = r["SHP"].strip("()").split(",")
        gt, npts, x0, y0, x1, y1, ld = int(float(v[0])), int(float(v[1])), *map(float, v[2:6]), float(v[11])
        seg = ld * KM_DEG * 1000
        p = Point(TO_M(float(r["LOT"]), float(r["LAT"])))
        bx = transform(TO_M, box(x0, y0, x1, y1)).buffer(30)
        inb = O.intersection(bx).length
        cov = inb / seg if seg > 0 else 0
        res.append((r["SN"], r["NM"], gt, npts, round(seg), round(p.distance(O)), round(inb), round(cov, 2)))
        W.writerow([svc, *res[-1]])
    d = sorted(x[5] for x in res)
    q = lambda a: d[min(len(d) - 1, int(a * len(d)))]
    tot = sum(x[4] for x in res)
    print(f"   대표점→OSM 거리: 중앙 {q(.5)} m · p90 {q(.9)} m · 최대 {d[-1]} m · 50 m 안 {sum(x<=50 for x in d)}/{len(d)} · 200 m 밖 {sum(x>200 for x in d)}")
    print(f"   서울시 길이 합 {tot/1000:.1f} km(±12%) · OSM 합 {O.length/1000:.1f} km")
    for x in sorted(res, key=lambda x: -x[5])[:12]:
        print(f"   SN={x[0]:>3} {x[1][:12]:12s} 길이{x[4]:6d} m  대표점→OSM {x[5]:5d} m  상자안 OSM {x[6]:6d} m  cover {x[7]}")
print("\n기록:", out)
