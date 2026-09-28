# mobility_scripts/collect/walk07_seoulgov_api.py — 30번 방. 서울시 두드림길 선형(둘레길 01 · 한양도성길 05, WGS1984) Open API 전량 수집.
# 채점표 전용 — 원본은 walk_courses\seoul_gov\ 에만 둔다(빼려면 폴더째 삭제). 산출 geojson 에 넣지 않는다.
# SHP 칸은 선 좌표가 아니라 "(유형,점수,minx,miny,maxx,maxy,…,길이(도),SRID,BLOB)" 요약으로 보인다 → 파싱해서 무엇이 있는지 확인.
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk07_seoulgov_api.py
import collections, json, os, sys, time
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

KEY = os.environ.get("SEOUL_OPENAPI_KEY") or sys.exit(".env 의 SEOUL_OPENAPI_KEY 없음")
OUT = RAW_MOBILITY / "walk_courses" / "seoul_gov"
OUT.mkdir(parents=True, exist_ok=True)
SERVICES = {"SdeDoDreamWay01LW": "둘레길_선형", "SdeDoDreamWay05LW": "한양도성길_선형"}

def page(svc, a, b):
    r = requests.get(f"http://openapi.seoul.go.kr:8088/{KEY}/json/{svc}/{a}/{b}/", timeout=30)
    try:
        j = r.json()
    except Exception:
        return None, f"HTTP {r.status_code} {r.text[:120]}"
    body = j.get(svc) or {}
    res = body.get("RESULT") or j.get("RESULT") or {}
    if res.get("CODE") not in ("INFO-000", None):
        return None, f"{res.get('CODE')} {res.get('MESSAGE')}"
    return body, None

def parse_shp(s):
    # 예: (4,399,126.979465417,37.612796575,126.98244257,37.616367605,0,0,0,0,0,0.00605258206525292,12,oracle.sql.BLOB@60791eec)
    v = (s or "").strip("()").split(",")
    try:
        f = [float(x) for x in v[:12]]
        return {"gtype": int(f[0]), "npts": int(f[1]), "minx": f[2], "miny": f[3], "maxx": f[4], "maxy": f[5],
                "len_deg": f[11], "srid_field": v[12] if len(v) > 12 else None, "blob": "BLOB" in s}
    except Exception:
        return {"unparsed": (s or "")[:80]}

for svc, label in SERVICES.items():
    head, err = page(svc, 1, 1)
    if err:
        print(f"!! {svc}({label}) {err}")
        continue
    n = int(head["list_total_count"])
    rows = []
    for a in range(1, n + 1, 1000):
        body, err = page(svc, a, min(n, a + 999))
        if err:
            print("  !!", a, err)
            break
        rows += body.get("row", [])
        time.sleep(0.3)
    (OUT / f"{svc}_{time.strftime('%Y%m%d')}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    ps = [parse_shp(r.get("SHP")) for r in rows]
    ok = [p for p in ps if "npts" in p]
    # 길이(도) → m 근사: 서울 위도에서 1도 ≈ 경도 88.2 km · 위도 111 km — 방향을 모르니 두 값 사이(범위로 적는다)
    L = sum(p["len_deg"] for p in ok)
    print(f"\n== {svc}({label}) 행 {len(rows)} / 총 {n} · SHP 파싱 {len(ok)}")
    print("   이름 분포:", collections.Counter(r.get("NM") for r in rows).most_common(8))
    print("   gtype:", collections.Counter(p["gtype"] for p in ok), "· BLOB 있음:", sum(p["blob"] for p in ok),
          "· 점수 합:", sum(p["npts"] for p in ok))
    print(f"   길이 합 {L:.4f}도 ≈ {L*88.2:.1f}~{L*111.0:.1f} km (방향 모름 → 범위)")
    print("   SHP 원문 예:", (rows[0].get("SHP") or "")[:140])
    print("   다른 칸:", sorted(set(k for r in rows for k in r)))
print("\n저장:", OUT)
