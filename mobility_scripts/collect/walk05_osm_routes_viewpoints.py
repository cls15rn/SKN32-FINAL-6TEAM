# mobility_scripts/collect/walk05_osm_routes_viewpoints.py — 30번 방. OSM 에서 ① 서울 안 도보 루트 관계(route=hiking/foot)
# ② tourism=viewpoint 를 받는다. 둘레길 PDF 는 선형이 없어서, 선형 후보를 OSM(ODbL · 변경 허용)에서 먼저 본다.
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk05_osm_routes_viewpoints.py
# 저장: walk_courses\osm_walk_routes_seoul_raw.json · osm_viewpoints_seoul_raw.json (Overpass 원본 그대로)
import collections, json, math, re, sys, time
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

D = RAW_MOBILITY / "walk_courses"
MIRRORS = ("https://overpass.private.coffee/api/interpreter", "https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter")
AREA = "area(3602297418)->.s;"

def overpass(q, name):
    fp = D / name
    if fp.exists():
        print("  캐시:", fp.name)
        return json.loads(fp.read_text(encoding="utf-8"))
    for u in MIRRORS:
        try:
            r = requests.post(u, data={"data": q}, timeout=300, headers={"User-Agent": "ACOP-mobility/0.1 (course project)"})
            print(f"  {u.split('/')[2]} → {r.status_code} {len(r.content)//1024} KB")
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                j = r.json()
                fp.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
                print("  osm_base:", j.get("osm3s", {}).get("timestamp_osm_base"))
                return j
        except Exception as e:
            print("  실패:", u.split("/")[2], type(e).__name__)
        time.sleep(5)
    raise SystemExit("Overpass 모두 실패")

def hav(a, b):
    (y1, x1), (y2, x2) = a, b
    return 6371000 * 2 * math.asin(math.sqrt(math.sin(math.radians(y2 - y1) / 2) ** 2 +
           math.cos(math.radians(y1)) * math.cos(math.radians(y2)) * math.sin(math.radians(x2 - x1) / 2) ** 2))

# ① 루트 관계
q1 = f"[out:json][timeout:240];{AREA}rel(area.s)[type=route][route~\"^(hiking|foot|walking)$\"];out geom;"
j = overpass(q1, "osm_walk_routes_seoul_raw.json")
rels = [e for e in j["elements"] if e["type"] == "relation"]
print(f"\n① 서울 안 도보 루트 관계 {len(rels)}개")
print("   network:", collections.Counter(e.get("tags", {}).get("network") for e in rels).most_common(8))
rows = []
for e in rels:
    t = e.get("tags", {})
    m = 0.0
    for mb in e.get("members", []):
        g = mb.get("geometry") or []
        m += sum(hav((a["lat"], a["lon"]), (b["lat"], b["lon"])) for a, b in zip(g, g[1:]))
    rows.append((t.get("name", ""), t.get("ref", ""), t.get("network", ""), t.get("operator", ""), round(m / 1000, 2),
                 len(e.get("members", [])), e["id"], t.get("distance", "")))
for r in sorted(rows, key=lambda r: r[0]):
    flag = "★" if re.search(r"둘레길|두드림|한양도성|자락길|한강|지천|성곽", r[0]) else " "
    print(f"  {flag} {r[0][:34]:34s} ref={r[1]:6s} net={r[2]:5s} op={r[3][:12]:12s} km={r[4]:7.2f} (tag {r[7] or '-'}) way={r[5]:4d} id={r[6]}")

# ② viewpoint
q2 = f"[out:json][timeout:120];{AREA}nwr(area.s)[tourism=viewpoint];out center tags;"
v = overpass(q2, "osm_viewpoints_seoul_raw.json")["elements"]
print(f"\n② tourism=viewpoint {len(v)}개 · 종류 {collections.Counter(e['type'] for e in v)}")
print("   이름 있음", sum(1 for e in v if e.get("tags", {}).get("name")), "· 영어 이름", sum(1 for e in v if e.get("tags", {}).get("name:en")))
print("   함께 붙은 태그 상위:", collections.Counter(k for e in v for k in e.get("tags", {}) if k not in ("tourism", "name")).most_common(10))
for e in v[:15]:
    t = e.get("tags", {})
    c = (e.get("lat"), e.get("lon")) if e["type"] == "node" else (e.get("center", {}).get("lat"), e.get("center", {}).get("lon"))
    print("  ", e["type"][0], e["id"], t.get("name", "(이름없음)")[:24], c)
