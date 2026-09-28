"""ODsay 2차 확인 — lang=0 · 4쌍 · 4호출 (loadLane 없음)
D5: 응답은 저장하지 않는다. 콘솔 요약만. 결과는 개수·예/아니오만 메모.
"""
import json, os, re, sys
from pathlib import Path
from urllib.parse import quote
import requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("ODSAY_API_KEY") or sys.exit("ODSAY_API_KEY 없음")
DATA_DIR = Path(os.getenv("DATA_DIR", r"C:\final_project\data"))

# 우리 역 표 — DATA_DIR 가 data 든 data\travel 이든 찾는다
cands = [DATA_DIR / "travel/processed/mobility/station_coords.json",
         DATA_DIR / "processed/mobility/station_coords.json"]
sc_path = next((p for p in cands if p.exists()), None) or sys.exit(f"station_coords.json 못 찾음: {cands}")
raw = json.loads(sc_path.read_text(encoding="utf-8"))

def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s))
    s = re.sub(r"[\s·.]", "", s)
    return s[:-1] if s.endswith("역") and len(s) > 2 else s

NAME_KEYS = ("name", "station", "station_name", "stn_nm", "역명", "name_ko")
names = set()
if isinstance(raw, dict):
    for k, v in raw.items():
        names.add(norm(k))
        if isinstance(v, dict):
            names |= {norm(v[x]) for x in NAME_KEYS if x in v}
elif isinstance(raw, list):
    for it in raw:
        if isinstance(it, dict):
            names |= {norm(it[x]) for x in NAME_KEYS if x in it}
print(f"우리 역 표: {sc_path.name} · 이름 {len(names)}개")
if not names:
    sys.exit(f"이름 키를 못 찾음 — 첫 항목 구조: {str(raw)[:300]}")

PAIRS = {  # 공공데이터·9/14 링크 좌표 (lng, lat)
    "홍대입구→경복궁": (126.9264939, 37.55774103, 126.973576, 37.575844),
    "성수→여의도":     (127.0557, 37.5445, 126.924318, 37.521678),
    "명동→N서울타워":  (126.986271, 37.560955, 126.9882, 37.5512),
    "서울역→인천공항T1": (126.972103, 37.55585, 126.4523433, 37.44760672),
}
shown_keys = set()
for label, (sx, sy, ex, ey) in PAIRS.items():
    url = f"https://api.odsay.com/v1/api/searchPubTransPathT?apiKey={quote(KEY, safe='')}"
    d = requests.get(url, params={"SX": sx, "SY": sy, "EX": ex, "EY": ey, "lang": 0}, timeout=10).json()
    if "error" in d:
        print(f"\n[{label}] ERROR {d['error']}"); continue
    paths = d["result"].get("path", [])
    hit = miss = 0; missed = set(); buses = set(); kinds = {"지하철": 0, "버스": 0, "혼합": 0}
    for p in paths:
        types = set()
        for s in p.get("subPath", []):
            t = s.get("trafficType")
            if t == 3: continue
            types.add(t)
            if t not in shown_keys:
                print(f"  [구간 키 · trafficType={t}] {sorted(s.keys())}"); shown_keys.add(t)
            if t == 1:
                for n in (s.get("startName"), s.get("endName")):
                    if norm(n) in names: hit += 1
                    else: miss += 1; missed.add(n)
            elif t == 2:
                buses |= {l.get("busNo") for l in s.get("lane", [])}
        kinds["혼합" if types == {1, 2} else "지하철" if types == {1} else "버스"] += 1
    print(f"\n[{label}] 후보 {len(paths)} · {kinds}")
    print(f"  역명 일치 {hit} / 불일치 {miss} · 불일치 이름: {sorted(missed)[:10]}")
    print(f"  버스 노선 {len(buses)}개: {sorted(b for b in buses if b)[:15]}")

print("\n끝 — 이번 4호출 · 오늘 누적 6/30")