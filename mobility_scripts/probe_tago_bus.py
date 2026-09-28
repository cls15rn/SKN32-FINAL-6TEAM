# mobility_scripts/probe_tago_bus.py — TAGO 버스노선정보: 서울이 있는지, 어느 오퍼레이션에 첫차·막차·배차가 있는지 확인
# 실행: 저장소 루트에서  python mobility_scripts/probe_tago_bus.py
# 왜: getRouteNoList(cityCode=11) 가 0건이었다. 원인 후보 셋을 한 번에 가른다.
#   ① 서울이 TAGO 에 없다(서울시는 자체 API 를 쓴다) ② 도시코드가 11 이 아니다 ③ 오퍼레이션·필수 파라미터가 다르다
# 호출 ≈ 10회.
import os, json, requests
from pathlib import Path
from dotenv import load_dotenv

# mobility_scripts/ 루트에 있어 mobility_scripts/collect/_paths.py 를 못 쓴다 — .env 를 직접 읽는다
REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
RAW_MOBILITY = Path(os.environ.get("DATA_DIR") or (REPO.parent / "data")) / "travel" / "raw" / "mobility"
RAW_MOBILITY.mkdir(parents=True, exist_ok=True)

KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService"
OUT = RAW_MOBILITY / "tago_bus_probe.json"


def call(op, **p):
    r = requests.get(f"{BASE}/{op}", params={"serviceKey": KEY, "_type": "json",
                                             "numOfRows": 100, "pageNo": 1, **p}, timeout=30)
    if r.status_code != 200:
        return {"_http": r.status_code, "_text": r.text[:300]}
    try:
        return r.json()["response"]["body"]
    except (KeyError, ValueError):
        return {"_raw": r.text[:400]}


def items(body):
    it = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
    return [it] if isinstance(it, dict) else list(it)


result = {}

# ① 도시코드 목록 — 서울이 있나
print("① 도시코드 목록")
body = call("getCtyCodeList")
cities = items(body)
result["cities"] = cities
if not cities:
    print("   응답:", json.dumps(body, ensure_ascii=False)[:400])
else:
    print(f"   {len(cities)}개")
    seoul = [c for c in cities if "서울" in str(c.get("cityname", ""))]
    print("   서울:", seoul or "★ 목록에 없음 — TAGO 버스는 서울을 제공하지 않는다")
    print("   수도권 예:", [c for c in cities if str(c.get("citycode", "")).startswith(("11", "23", "31"))][:8])
    print("   앞 5개:", cities[:5])

# ② 도시코드별로 노선번호 목록이 나오는지 (서울 후보 + 대조군)
print("\n② getRouteNoList 시험")
codes = [c.get("citycode") for c in cities if "서울" in str(c.get("cityname", ""))] or ["11", "1100"]
codes += ["23", "31010"]                      # 인천, 수원 — 대조군
for cc in codes[:4]:
    body = call("getRouteNoList", cityCode=cc)
    n = body.get("totalCount", 0) if isinstance(body, dict) else 0
    got = items(body)
    print(f"   cityCode={cc}: totalCount={n}, 예시={got[0] if got else body if not got else ''}")
    result.setdefault("routeNoList", {})[str(cc)] = {"total": n, "sample": got[0] if got else None}

# ③ 첫차·막차·배차가 어느 오퍼레이션에 있는지 — 노선 하나로 확인
print("\n③ getRouteInfoIem (노선정보항목) — 첫차·막차·배차 필드 확인")
probe_cc, probe_id = None, None
for cc, v in (result.get("routeNoList") or {}).items():
    if v["sample"]:
        probe_cc = cc; probe_id = v["sample"].get("routeid") or v["sample"].get("routeId"); break
if probe_id:
    body = call("getRouteInfoIem", cityCode=probe_cc, routeId=probe_id)
    got = items(body)
    print(f"   cityCode={probe_cc} routeId={probe_id}")
    print("   필드:", sorted(got[0]) if got else "없음")
    print("   값:", json.dumps(got[0], ensure_ascii=False)[:400] if got else json.dumps(body, ensure_ascii=False)[:300])
    result["routeInfo"] = got[0] if got else body
else:
    print("   노선 ID 를 못 구해 건너뜀")

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n기록 → {OUT}")
print("서울이 목록에 없으면: 서울 시내버스는 서울 열린데이터광장(ws.bus.go.kr / OA-15067 노선정보)으로 간다.")
