# mobility_scripts/collect/walk09_route_gap_tags.py — 30번 방. walk06 에서 보행망과 어긋난 둘레길 16·5·12코스의 원인을 PBF 태그로 본다.
# 대조군 10코스(GH/선 0.999) 를 같이 본다. GH 는 쓰지 않는다(PBF 3회 읽기 · 3~4분).
# 보는 것: 구성 way 의 highway·foot·access·sidewalk 등 길이 합 · GH foot 가 막을 만한 way(의심) · way 위 barrier/access 노드
# 실행: C:\final_project\.venv\Scripts\python mobility_scripts\collect\walk09_route_gap_tags.py
# 저장: walk_courses\walk09_gap_ways_<날짜>.csv
import collections, csv, math, sys, time
from pathlib import Path
import osmium

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

PBF = str(RAW_MOBILITY / "osm" / "south-korea-latest.osm.pbf")
D = RAW_MOBILITY / "walk_courses"
RELS = {17506050: "16코스(4.57)", 17496414: "5코스(1.39)", 17538299: "12코스(1.34)", 17538301: "10코스(대조 0.999)"}
KEYS = ("highway", "foot", "access", "sidewalk", "footway", "informal", "trail_visibility", "sac_scale", "surface",
        "tunnel", "bridge", "area", "motor_vehicle", "vehicle", "name", "construction", "disused", "abandoned")
FOOT_OK = {"footway", "path", "pedestrian", "steps", "track", "living_street", "residential", "service", "unclassified",
           "tertiary", "secondary", "primary", "road", "cycleway", "bridleway", "corridor", "platform",
           "tertiary_link", "secondary_link", "primary_link"}
t0 = time.time()

class P1(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.m = {}
    def relation(s, r):
        if r.id in RELS:
            s.m[r.id] = [(m.ref, m.role) for m in r.members if m.type == "w"]
p1 = P1(); p1.apply_file(PBF)
need = {w for v in p1.m.values() for w, _ in v}
print(f"pass1 {time.time()-t0:.0f}s · 관계 {len(p1.m)}/{len(RELS)} · way {len(need)}")

class P2(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.w = {}
    def way(s, w):
        if w.id in need:
            try:
                c = [(n.lon, n.lat) for n in w.nodes]
            except osmium.InvalidLocationError:
                c = []
            L = sum(6371000 * 2 * math.asin(math.sqrt(math.sin(math.radians(b[1] - a[1]) / 2) ** 2 + math.cos(math.radians(a[1])) *
                    math.cos(math.radians(b[1])) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2)) for a, b in zip(c, c[1:]))
            s.w[w.id] = {"tags": {k: w.tags.get(k) for k in KEYS if w.tags.get(k)}, "len": L, "nodes": [n.ref for n in w.nodes]}
p2 = P2(); p2.apply_file(PBF, locations=True, idx="flex_mem")
nodes = {n for v in p2.w.values() for n in v["nodes"]}
print(f"pass2 {time.time()-t0:.0f}s · way 태그 {len(p2.w)} · 노드 {len(nodes)}")

class P3(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.n = {}
    def node(s, n):
        if n.id in nodes and (n.tags.get("barrier") or n.tags.get("access") or n.tags.get("foot")):
            s.n[n.id] = {k: n.tags.get(k) for k in ("barrier", "access", "foot", "name") if n.tags.get(k)}
p3 = P3(); p3.apply_file(PBF)
print(f"pass3 {time.time()-t0:.0f}s · 표시 노드 {len(p3.n)}")

def suspect(t):
    why = []
    hw = t.get("highway")
    if not hw:
        why.append("highway 없음")
    elif hw not in FOOT_OK:
        why.append(f"highway={hw}")
    if t.get("foot") in ("no", "private", "use_sidepath", "discouraged"):
        why.append(f"foot={t['foot']}")
    if t.get("access") in ("no", "private", "customers") and t.get("foot") not in ("yes", "designated", "permissive"):
        why.append(f"access={t['access']}")
    if t.get("area") == "yes":
        why.append("area")
    return why

out = D / f"walk09_gap_ways_{time.strftime('%Y%m%d')}.csv"
W = csv.writer(open(out, "w", newline="", encoding="utf-8-sig"))
W.writerow(["rel", "course", "way", "len_m", "suspect", "barrier_nodes", *KEYS])
for rid, lab in RELS.items():
    ms = p1.m.get(rid, [])
    tot = sum(p2.w[w]["len"] for w, _ in ms if w in p2.w)
    by_hw, by_acc = collections.Counter(), collections.Counter()
    sus, bar = [], []
    for w, role in ms:
        v = p2.w.get(w)
        if not v:
            sus.append((w, 0, ["PBF 에 없음"])); continue
        t = v["tags"]
        by_hw[t.get("highway", "(없음)")] += v["len"]
        by_acc[f"foot={t.get('foot','-')}/access={t.get('access','-')}"] += v["len"]
        why = suspect(t)
        bn = [f"{n}:{p3.n[n]}" for n in v["nodes"] if n in p3.n]
        bar += [(w, b) for b in bn]
        if why:
            sus.append((w, v["len"], why))
        W.writerow([rid, lab, w, round(v["len"]), ";".join(why), " | ".join(bn), *[t.get(k, "") for k in KEYS]])
    print(f"\n== {lab} rel {rid} · way {len(ms)} · {tot/1000:.2f} km")
    print("   highway별 m:", {k: round(v) for k, v in by_hw.most_common()})
    print("   foot/access별 m:", {k: round(v) for k, v in by_acc.most_common(6)})
    print(f"   의심 way {len(sus)}개 · {sum(s[1] for s in sus)/1000:.2f} km")
    for s in sorted(sus, key=lambda s: -s[1])[:10]:
        print(f"     w{s[0]} {s[1]:6.0f} m  {', '.join(s[2])}  {p2.w.get(s[0], {}).get('tags', {}).get('name', '')}")
    print(f"   barrier/access 노드 {len(bar)}:", [b for _, b in bar][:8])
print(f"\n{time.time()-t0:.0f}s · 기록: {out}")
