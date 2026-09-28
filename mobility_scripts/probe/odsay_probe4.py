"""ODsay 4차 — 버스 재현율 · 3호출 (D5: 저장 없음, 콘솔 요약만)"""
import json, os, re, sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
import requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("ODSAY_API_KEY") or sys.exit("ODSAY_API_KEY 없음")
MOB = Path(r"C:\final_project\data\travel\processed\mobility")

def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s)); s = re.sub(r"[\s·.]", "", s)
    return s[:-1] if s.endswith("역") and len(s) > 2 else s

def ars(x):
    s = re.sub(r"\D", "", str(x or ""))
    return s.zfill(5) if s else None

def jl(name):
    with open(MOB / name, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]

# ── 우리 데이터 ─────────────────────────────
st = json.loads((MOB / "station_coords.json").read_text(encoding="utf-8"))["stations"]
st_names = {norm(v["station_nm"]) for v in st.values()} | {norm(v["src_name"]) for v in st.values() if v.get("src_name")}

stops = jl("bus_stops_v2.jsonl")
route_ars = defaultdict(set)           # route_id -> {ars}
nm2rid = defaultdict(set)              # route_nm -> {route_id}
for r in stops:
    rid = str(r["route_id"]); route_ars[rid].add(ars(r["ars_id"])); nm2rid[str(r["route_nm"])].add(rid)
all_ars = set().union(*route_ars.values())
air = {str(r["route_no"]) for r in jl("airport_bus_v1.jsonl")}
print(f"우리 버스: 노선 {len(route_ars)} · 정류장 ars {len(all_ars)} · 공항버스 {len(air)}")

# ── ODsay 3호출 ─────────────────────────────
PAIRS = {
    "명동→N서울타워":   (126.986271, 37.560955, 126.9882, 37.5512),
    "서울역→인천공항T1": (126.972103, 37.55585, 126.4523433, 37.44760672),
    "성수→여의도":      (127.0557, 37.5445, 126.924318, 37.521678),
}
for label, (sx, sy, ex, ey) in PAIRS.items():
    url = f"https://api.odsay.com/v1/api/searchPubTransPathT?apiKey={quote(KEY, safe='')}"
    d = requests.get(url, params={"SX": sx, "SY": sy, "EX": ex, "EY": ey, "lang": 0}, timeout=10).json()
    if "error" in d:
        print(f"\n[{label}] ERROR {d['error']}"); continue
    paths = d["result"].get("path", [])
    c = defaultdict(int); miss_bus = set(); path_ok = 0
    for p in paths:
        ok_all = True
        for s in p.get("subPath", []):
            t = s.get("trafficType")
            if t == 1:
                ok = norm(s.get("startName")) in st_names and norm(s.get("endName")) in st_names
                c["지하철 구간"] += 1; c["지하철 재현"] += ok
            elif t == 2:
                c["버스 구간"] += 1
                sa, ea = ars(s.get("startArsID")), ars(s.get("endArsID"))
                c["ars 둘다 우리표에"] += (sa in all_ars and ea in all_ars)
                ok = False
                for ln in s.get("lane", []):
                    no, blid = str(ln.get("busNo")), str(ln.get("busLocalBlID"))
                    by_id = blid in route_ars
                    by_nm = nm2rid.get(no, set())
                    if by_id: c["노선ID 일치"] += 1
                    if by_nm: c["노선번호 일치"] += 1
                    if by_id and by_nm: c["ID·번호 같은 노선"] += (blid in by_nm)
                    rids = ({blid} if by_id else set()) | by_nm
                    if any(sa in route_ars[r] and ea in route_ars[r] for r in rids): ok = True
                    elif no in air: ok = True; c["공항버스로 인정"] += 1
                    elif not rids: miss_bus.add(no)
                c["버스 재현"] += ok
            else:
                continue
            ok_all &= ok
        path_ok += ok_all
    print(f"\n[{label}] 후보 {len(paths)} · **경로 전체 재현 {path_ok}/{len(paths)}**")
    print(f"  {dict(c)}")
    if miss_bus: print(f"  우리 표에 없는 노선 {len(miss_bus)}개: {sorted(miss_bus)[:15]}")

print("\n끝 — 이번 3호출 · 오늘 누적 11/30")