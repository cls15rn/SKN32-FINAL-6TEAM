"""ODsay 3차 — 역명 일치율 재측정 · 2호출 (D5: 저장 없음, 콘솔 요약만)"""
import json, math, os, re, sys
from pathlib import Path
from urllib.parse import quote
import requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("ODSAY_API_KEY") or sys.exit("ODSAY_API_KEY 없음")
MOB = Path(r"C:\final_project\data\travel\processed\mobility")

# ── 우리 역 표 ─────────────────────────────
st = json.loads((MOB / "station_coords.json").read_text(encoding="utf-8"))["stations"]

def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s))
    s = re.sub(r"[\s·.]", "", s)
    return s[:-1] if s.endswith("역") and len(s) > 2 else s

def line_no(s):
    m = re.search(r"(\d+)\s*호선", str(s))
    return int(m.group(1)) if m else None

by_name = {}
for v in st.values():
    for n in (v["station_nm"], v.get("src_name")):
        if n: by_name.setdefault(norm(n), []).append(v)
print(f"우리 역 표: {len(st)}역 · 정규화 이름 {len(by_name)}개")

def nearest(x, y):
    best = None
    for v in st.values():
        d = math.hypot((v["lng"] - x) * 88_800, (v["lat"] - y) * 111_000)  # 서울 위도 근사(m)
        if best is None or d < best[0]: best = (d, v)
    return best

# ── 우리 버스 파일 (호출 없음) ──────────────
print("\n[우리 버스 관련 파일]")
for p in sorted(MOB.rglob("*bus*")):
    if p.suffix not in (".json", ".jsonl"):
        print(f"  {p.relative_to(MOB)}"); continue
    try:
        if p.suffix == ".jsonl":
            first = json.loads(p.open(encoding="utf-8").readline())
        else:
            d = json.loads(p.read_text(encoding="utf-8"))
            first = next(iter(d.values())) if isinstance(d, dict) else d[0]
            if isinstance(first, (dict, list)) and not isinstance(first, dict): first = first[0]
        keys = sorted(first.keys()) if isinstance(first, dict) else type(first).__name__
        print(f"  {p.relative_to(MOB)} · 첫 항목 키: {keys}")
    except Exception as e:
        print(f"  {p.relative_to(MOB)} · 읽기 실패 {e}")

# ── ODsay 2호출 ─────────────────────────────
PAIRS = {
    "홍대입구→경복궁": (126.9264939, 37.55774103, 126.973576, 37.575844),
    "성수→여의도":     (127.0557, 37.5445, 126.924318, 37.521678),
}
lane_keys_shown = set()
for label, (sx, sy, ex, ey) in PAIRS.items():
    url = f"https://api.odsay.com/v1/api/searchPubTransPathT?apiKey={quote(KEY, safe='')}"
    d = requests.get(url, params={"SX": sx, "SY": sy, "EX": ex, "EY": ey, "lang": 0}, timeout=10).json()
    if "error" in d:
        print(f"\n[{label}] ERROR {d['error']}"); continue
    c = {"이름": 0, "이름+호선": 0, "좌표만": 0, "실패": 0}
    far, ars = [], {"있음": 0, "없음": 0}
    for p in d["result"].get("path", []):
        for s in p.get("subPath", []):
            t = s.get("trafficType")
            lane = (s.get("lane") or [{}])[0]
            if t in (1, 2) and t not in lane_keys_shown:
                print(f"  [lane 키 · trafficType={t}] {sorted(lane.keys())} · 예: {lane.get('name') or lane.get('busNo')}")
                lane_keys_shown.add(t)
            if t == 2:
                for k in ("startArsID", "endArsID"):
                    ars["있음" if s.get(k) not in (None, "", "0") else "없음"] += 1
            if t != 1: continue
            ln = line_no(lane.get("name", ""))
            for nm, x, y in ((s.get("startName"), s.get("startX"), s.get("startY")),
                             (s.get("endName"), s.get("endX"), s.get("endY"))):
                hits = by_name.get(norm(nm), [])
                if hits:
                    c["이름"] += 1
                    if ln and any(line_no(h["line"]) == ln for h in hits): c["이름+호선"] += 1
                else:
                    dist, v = nearest(float(x), float(y))
                    if dist <= 300: c["좌표만"] += 1; far.append(f"{nm}→{v['station_nm']}({dist:.0f}m)")
                    else: c["실패"] += 1; far.append(f"{nm}✗({dist:.0f}m)")
    print(f"\n[{label}] 지하철 역 대조 {c}")
    if far: print(f"  이름 불일치: {far[:10]}")
    print(f"  버스 ArsID {ars}")

print("\n끝 — 이번 2호출 · 오늘 누적 8/30")