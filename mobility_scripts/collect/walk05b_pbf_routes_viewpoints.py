# mobility_scripts/collect/walk05b_pbf_routes_viewpoints.py — 30번 방. Overpass 가 504 라 로컬 PBF(2026-09-18 기준)에서 직접 뽑는다.
# ① 도보 루트 관계(type=route, route=hiking/foot/walking) 중 서울 경계에 닿는 것 ② tourism=viewpoint(서울 안).
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk05b_pbf_routes_viewpoints.py     (PBF 2회 읽기 · 3~6분 · 메모리 1~3 GB)
# 저장: walk_courses\osm_walk_routes_seoul_pbf.geojson · osm_viewpoints_seoul_pbf.geojson (ODbL · 내부용)
import collections, json, math, re, sys, time
from pathlib import Path
import osmium
from shapely.geometry import LineString, MultiLineString, Point, mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

D = RAW_MOBILITY / "walk_courses"
PBF = RAW_MOBILITY / "osm" / "south-korea-latest.osm.pbf"
SEOUL = shape(json.loads((D / "seoul_boundary_osm.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"])
BX = SEOUL.buffer(0.02).bounds  # 서울 +약 2 km 상자
KMX, KMY = 88.2, 111.0          # 서울 위도 근사 km/도

def km(g):
    xs = g.geoms if hasattr(g, "geoms") else [g]
    t = 0.0
    for l in xs:
        c = list(l.coords)
        t += sum(math.hypot((b[0] - a[0]) * KMX, (b[1] - a[1]) * KMY) for a, b in zip(c, c[1:]))
    return t

t0 = time.time()

class Rel(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rels = {}
    def relation(self, r):
        if r.tags.get("type") == "route" and r.tags.get("route") in ("hiking", "foot", "walking"):
            self.rels[r.id] = {"tags": dict(r.tags), "ways": [m.ref for m in r.members if m.type == "w"],
                               "subrels": [m.ref for m in r.members if m.type == "r"]}

h1 = Rel()
h1.apply_file(str(PBF))
need = {w for v in h1.rels.values() for w in v["ways"]}
print(f"pass1 {time.time()-t0:.0f}s · 전국 도보 루트 관계 {len(h1.rels)} · 필요한 way {len(need)}")

class Geo(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways, self.vp = {}, []
    def _vp(self, kind, oid, tags, x, y):
        if BX[0] <= x <= BX[2] and BX[1] <= y <= BX[3]:
            self.vp.append((kind, oid, dict(tags), x, y))
    def node(self, n):
        if n.tags.get("tourism") == "viewpoint" and n.location.valid():
            self._vp("node", n.id, n.tags, n.location.lon, n.location.lat)
    def way(self, w):
        vp = w.tags.get("tourism") == "viewpoint"
        if w.id not in need and not vp:
            return
        try:
            c = [(n.lon, n.lat) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if w.id in need and len(c) >= 2:
            self.ways[w.id] = c
        if vp and c:
            self._vp("way", w.id, w.tags, sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c))

h2 = Geo()
h2.apply_file(str(PBF), locations=True, idx="flex_mem")
print(f"pass2 {time.time()-t0:.0f}s · way 좌표 {len(h2.ways)} · viewpoint(상자 안) {len(h2.vp)}")

# ① 서울에 닿는 루트
feats, rows = [], []
for rid, v in h1.rels.items():
    ls = [LineString(h2.ways[w]) for w in v["ways"] if w in h2.ways]
    if not ls:
        continue
    g = MultiLineString(ls)
    if not g.intersects(SEOUL):
        continue
    gin = g.intersection(SEOUL)
    t = v["tags"]
    p = {"osm_rel": rid, "name": t.get("name", ""), "name_en": t.get("name:en", ""), "ref": t.get("ref", ""),
         "network": t.get("network", ""), "operator": t.get("operator", ""), "route": t.get("route"),
         "distance_tag": t.get("distance", ""), "km_total": round(km(g), 2), "km_in_seoul": round(km(gin), 2),
         "ways": len(v["ways"]), "ways_missing": len(v["ways"]) - len(ls), "subrels": len(v["subrels"])}
    feats.append({"type": "Feature", "properties": p, "geometry": mapping(g)})
    rows.append(p)
out1 = D / "osm_walk_routes_seoul_pbf.geojson"
out1.write_text(json.dumps({"type": "FeatureCollection", "source": f"OSM PBF {time.strftime('%Y-%m-%d', time.localtime(PBF.stat().st_mtime))} · ODbL", "features": feats}, ensure_ascii=False), encoding="utf-8")
print(f"\n① 서울에 닿는 도보 루트 {len(rows)}개 · 서울 안 합 {sum(r['km_in_seoul'] for r in rows):.1f} km")
print("   network:", collections.Counter(r["network"] for r in rows).most_common(8))
for r in sorted(rows, key=lambda r: r["name"]):
    f = "★" if re.search(r"둘레길|두드림|한양도성|자락길|한강|지천|성곽|Seoul Trail", r["name"] + r["name_en"]) else " "
    print(f"  {f} {r['name'][:30]:30s} ref={r['ref'][:5]:5s} net={r['network'][:4]:4s} 전체={r['km_total']:6.1f} 서울안={r['km_in_seoul']:6.1f} "
          f"(tag {r['distance_tag'] or '-'}) way={r['ways']}{'(결손'+str(r['ways_missing'])+')' if r['ways_missing'] else ''} sub={r['subrels']} id={r['osm_rel']}")

# ② viewpoint (서울 경계 안)
vp = [x for x in h2.vp if SEOUL.contains(Point(x[3], x[4]))]
out2 = D / "osm_viewpoints_seoul_pbf.geojson"
out2.write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"osm_type": k, "osm_id": i, **t}, "geometry": {"type": "Point", "coordinates": [x, y]}}
    for k, i, t, x, y in vp]}, ensure_ascii=False), encoding="utf-8")
print(f"\n② viewpoint 서울 안 {len(vp)} (node {sum(1 for x in vp if x[0]=='node')} · way {sum(1 for x in vp if x[0]=='way')})"
      f" · 이름 {sum(1 for x in vp if x[2].get('name'))} · 영어이름 {sum(1 for x in vp if x[2].get('name:en'))}")
print("   함께 붙은 태그:", collections.Counter(k for x in vp for k in x[2] if k not in ("tourism", "name")).most_common(10))
for x in [x for x in vp if x[2].get("name")][:15]:
    print("  ", x[0][0], x[1], x[2]["name"][:24], round(x[4], 5), round(x[3], 5))
print(f"\n{time.time()-t0:.0f}s · 기록: {out1.name} · {out2.name}")
