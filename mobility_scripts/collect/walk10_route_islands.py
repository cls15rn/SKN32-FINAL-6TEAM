# mobility_scripts/collect/walk10_route_islands.py — 30번 방. walk09 에서 태그 원인(foot=no 등)이 안 나왔다 →
# 두 번째 가설 = 「섬」: 코스 way 가 주변 보행망과 노드를 공유하지 않아 작은 연결 요소가 되고, GraphHopper 가
# 작은 연결 요소를 지운다(prepare.min_network_size 기본 200). 셋째 = access=permit(GH 는 통행 제한으로 본다).
# 코스 주변(경계상자 +1.5 km) 보행 가능 way 로 연결 요소를 만들고, 코스 way 마다 속한 요소 크기를 잰다.
# 실행: C:\final_project\.venv\Scripts\python mobility_scripts\collect\walk10_route_islands.py    (PBF 2회 · 2~3분)
# 저장: walk_courses\walk10_route_islands_<날짜>.csv
import collections, csv, math, sys, time
from pathlib import Path
import osmium

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

PBF = str(RAW_MOBILITY / "osm" / "south-korea-latest.osm.pbf")
D = RAW_MOBILITY / "walk_courses"
RELS = {17506050: "16코스(4.57)", 17496414: "5코스(1.39)", 17538299: "12코스(1.34)", 17538301: "10코스(대조)"}
NO_FOOT = {"motorway", "motorway_link", "trunk", "trunk_link", "construction", "proposed", "abandoned", "platform", "raceway", "bus_guideway"}
RESTRICT = {"no", "private", "permit", "restricted", "military", "emergency"}
BUF = 0.015
t0 = time.time()

def hav(a, b):
    return 6371000 * 2 * math.asin(math.sqrt(math.sin(math.radians(b[1] - a[1]) / 2) ** 2 + math.cos(math.radians(a[1])) *
           math.cos(math.radians(b[1])) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2))

class P1(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.m = {}
    def relation(s, r):
        if r.id in RELS:
            s.m[r.id] = [m.ref for m in r.members if m.type == "w"]
p1 = P1(); p1.apply_file(PBF)
route_w = {w: rid for rid, ws in p1.m.items() for w in ws}

class P2(osmium.SimpleHandler):
    """코스 way 좌표로 상자를 만들고, 상자 안 보행 가능 way 를 모두 모은다(한 번에 하려면 좌표가 먼저 필요 → 전국 상자 후보를 넉넉히)."""
    def __init__(s):
        super().__init__(); s.ways = {}
    def way(s, w):
        hw = w.tags.get("highway")
        if not hw and w.id not in route_w:
            return
        try:
            c = [(n.lon, n.lat) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        if not c or not (126.7 <= c[0][0] <= 127.3 and 37.4 <= c[0][1] <= 37.75):
            return
        s.ways[w.id] = {"hw": hw, "foot": w.tags.get("foot"), "access": w.tags.get("access"), "sac": w.tags.get("sac_scale"),
                        "nodes": [n.ref for n in w.nodes], "c": c, "len": sum(hav(a, b) for a, b in zip(c, c[1:]))}
p2 = P2(); p2.apply_file(PBF, locations=True, idx="flex_mem")
print(f"PBF {time.time()-t0:.0f}s · 서울 상자 highway way {len(p2.ways)}")

def walkable(v):
    if v["hw"] in NO_FOOT or not v["hw"]:
        return False, "highway"
    if v["foot"] in ("no", "private", "use_sidepath"):
        return False, f"foot={v['foot']}"
    if v["access"] in RESTRICT and v["foot"] not in ("yes", "designated", "permissive"):
        return False, f"access={v['access']}"
    return True, ""

out = D / f"walk10_route_islands_{time.strftime('%Y%m%d')}.csv"
W = csv.writer(open(out, "w", newline="", encoding="utf-8-sig"))
W.writerow(["rel", "course", "way", "len_m", "highway", "foot", "access", "sac_scale", "walkable", "block_reason", "comp_edges", "comp_km", "shares_node_with_non_route"])
for rid, lab in RELS.items():
    ws = [w for w in p1.m.get(rid, []) if w in p2.ways]
    if not ws:
        print(f"\n== {lab} · 서울 상자 안 way 없음 — 건너뜀"); continue
    xs = [p[0] for w in ws for p in p2.ways[w]["c"]]; ys = [p[1] for w in ws for p in p2.ways[w]["c"]]
    bx = (min(xs) - BUF, min(ys) - BUF, max(xs) + BUF, max(ys) + BUF)
    near = {i: v for i, v in p2.ways.items() if any(bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3] for x, y in v["c"][::max(1, len(v["c"]) // 4)])}
    ok = {i: v for i, v in near.items() if walkable(v)[0]}
    # union-find over nodes of walkable ways
    par = {}
    def f(a):
        while par.setdefault(a, a) != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for v in ok.values():
        for a, b in zip(v["nodes"], v["nodes"][1:]):
            ra, rb = f(a), f(b)
            if ra != rb:
                par[ra] = rb
    comp_e, comp_m = collections.Counter(), collections.Counter()
    for v in ok.values():
        r = f(v["nodes"][0]); comp_e[r] += len(v["nodes"]) - 1; comp_m[r] += v["len"]
    node_ways = collections.defaultdict(set)
    for i, v in near.items():
        for n in v["nodes"]:
            node_ways[n].add(i)
    big = comp_e.most_common(1)[0][0] if comp_e else None
    tot = blocked = island = 0.0
    rows = []
    for w in ws:
        v = p2.ways[w]; wk, why = walkable(v); tot += v["len"]
        r = f(v["nodes"][0]) if wk else None
        ce, cm = (comp_e[r], comp_m[r] / 1000) if wk else (0, 0)
        shares = any(len(node_ways[n] - set(p1.m[rid])) > 0 for n in v["nodes"])
        if not wk:
            blocked += v["len"]
        elif r != big and ce < 200:
            island += v["len"]
        rows.append((w, v, wk, why, ce, cm, shares))
        W.writerow([rid, lab, w, round(v["len"]), v["hw"], v["foot"] or "", v["access"] or "", v["sac"] or "", wk, why, ce, round(cm, 2), shares])
    print(f"\n== {lab} · way {len(ws)} · {tot/1000:.2f} km · 주변 보행 가능 way {len(ok)} · 최대 요소 {comp_m[big]/1000:.0f} km")
    print(f"   통행 제한 {blocked:.0f} m · 작은 섬(<200 edge) {island:.0f} m · 비코스 way 와 노드 공유 안 함 {sum(r[1]['len'] for r in rows if not r[6]):.0f} m")
    print("   sac_scale:", collections.Counter(r[1]["sac"] or "-" for r in rows).most_common())
    for w, v, wk, why, ce, cm, sh in sorted(rows, key=lambda r: (r[2] and r[4] >= 200, -r[1]["len"]))[:8]:
        print(f"     w{w} {v['len']:6.0f} m {v['hw']:10s} {('막힘 ' + why) if not wk else f'요소 {ce} edge / {cm:.1f} km'}  {'' if sh else '← 주변과 노드 공유 없음'}")
print(f"\n{time.time()-t0:.0f}s · 기록: {out}")
