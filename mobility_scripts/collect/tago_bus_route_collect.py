# mobility_scripts/collect/tago_bus_route_collect.py — TAGO 버스노선정보: 첫차·막차·배차간격
# 실행: 저장소 루트에서  python mobility_scripts/collect/tago_bus_route_collect.py --city 23        (인천)
#                        python mobility_scripts/collect/tago_bus_route_collect.py --list-cities     (도시코드 확인)
# 선행: 공공데이터포털 "국토교통부_(TAGO)_버스노선정보"(15098529) 활용신청 — 자동승인, 10,000/일.
#       지하철과 같은 기관코드 1613000, 같은 DATA_GO_KR_KEY.
#
# ★서울특별시는 TAGO 도시코드 목록(138개)에 없다 — 2026-09-09 확인(mobility_scripts/probe_tago_bus.py).
#   서울시는 자체 API(서울특별시_노선정보조회, data.go.kr 15000193 / ws.bus.go.kr)를 따로 운영한다.
#   따라서 이 스크립트는 인천·경기 등 TAGO 제공 도시용이고, 서울 시내버스는 별도 소스로 간다.
#
# 판정에서의 위치: 지하철은 열차 단위 시간표라 "그 시각에 출발하는 차가 있는가"를 직접 본다.
#   버스는 노선 단위(첫차·막차·배차간격)라 "운행 구간 안인가 + 배차만큼 대기"로 본다.
#   첫차·막차는 확정, 대기 시간은 배차 평균이라 항상 추정이다(rules_v0.2.json bus 절).
import os, json, time, argparse
import requests
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY

KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService"
SOURCE = "data.go.kr/1613000/BusRouteInfoInqireService"
KST = timezone(timedelta(hours=9))

ap = argparse.ArgumentParser()
ap.add_argument("--city", help="도시코드 (인천 23, 수원 31010 …). 서울은 없다")
ap.add_argument("--list-cities", action="store_true", help="도시코드 목록만 출력")
ap.add_argument("--no-interval", action="store_true", help="배차간격 조회를 건너뛴다(노선당 1회 호출 절약)")
args = ap.parse_args()


def call(op, **p):
    for attempt in range(5):
        r = requests.get(f"{BASE}/{op}", params={"serviceKey": KEY, "_type": "json",
                                                 "numOfRows": 1000, "pageNo": 1, **p}, timeout=30)
        if r.status_code == 200:
            try:
                return r.json()["response"]["body"]
            except (KeyError, ValueError):
                print("  응답 형식 이상:", r.text[:200]); return {}
        if "PER_SECOND" in r.text or r.status_code >= 500:
            time.sleep(2 + attempt * 2); continue
        print("  HTTP", r.status_code, r.text[:200]); return {}
    return {}


def items_of(body):
    it = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
    return [it] if isinstance(it, dict) else list(it)


cities = items_of(call("getCtyCodeList"))
if args.list_cities or not args.city:
    print(f"도시코드 {len(cities)}개")
    for c in cities:
        print(f"  {str(c.get('citycode')):>6}  {c.get('cityname')}")
    print("\n※ 서울특별시는 목록에 없다. 서울 시내버스는 data.go.kr 15000193(서울특별시_노선정보조회)로 간다.")
    raise SystemExit(0)

name = next((c["cityname"] for c in cities if str(c.get("citycode")) == str(args.city)), "?")
if name == "?":
    raise SystemExit(f"도시코드 {args.city} 를 목록에서 못 찾았다. --list-cities 로 확인.")
OUT = RAW_MOBILITY / f"tago_bus_routes_{args.city}.json"


def hhmm(v):
    """'0500'(문자열) 과 2000(int) 이 섞여 온다 → '05:00'."""
    s = str(v).strip() if v not in (None, "") else ""
    s = s.zfill(4) if s.isdigit() else s
    return f"{s[:2]}:{s[2:4]}" if len(s) == 4 and s.isdigit() else None


rows, page = [], 1
while True:
    body = call("getRouteNoList", cityCode=args.city, pageNo=page)
    got = items_of(body)
    rows += got
    total = int(body.get("totalCount") or 0)
    print(f"  page {page}: {len(got)}건 (누적 {len(rows)}/{total})")
    if not got or len(rows) >= total:
        break
    page += 1
    time.sleep(0.15)

# 배차간격은 노선정보항목에만 있다 — 노선당 1회
interval = {}
if not args.no_interval:
    print(f"배차간격 조회 {len(rows)}건")
    for i, r in enumerate(rows, 1):
        rid = r.get("routeid")
        if not rid:
            continue
        got = items_of(call("getRouteInfoIem", cityCode=args.city, routeId=rid))
        if got:
            interval[rid] = got[0].get("intervaltime")
        if i % 100 == 0:
            print(f"  … {i}/{len(rows)}")
        time.sleep(0.12)

fetched = datetime.now(KST).isoformat(timespec="seconds")
out = []
for r in rows:
    rid = r.get("routeid")
    out.append({
        "route_id": rid, "route_no": str(r.get("routeno", "")), "route_type": r.get("routetp"),
        "start_node": r.get("startnodenm"), "end_node": r.get("endnodenm"),
        "first": hhmm(r.get("startvehicletime")), "last": hhmm(r.get("endvehicletime")),
        "interval_min": interval.get(rid),
        "city_code": str(args.city), "city_name": name,
        "source": SOURCE, "fetched_at": fetched, "fetched_at_precision": "second",
        "grade": {"service_window": "확정", "wait": "추정"},   # 첫차·막차는 기록, 대기는 배차 평균
    })
OUT.write_text(json.dumps({"source": SOURCE, "city_code": str(args.city), "city_name": name,
                           "fetched_at": fetched, "count": len(out),
                           "with_interval": sum(1 for x in out if x["interval_min"]),
                           "routes": out}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n{name} 노선 {len(out)}개 → {OUT}")
print(f"  첫차·막차 있는 노선 {sum(1 for x in out if x['first'] and x['last'])}개")
print(f"  배차간격 있는 노선 {sum(1 for x in out if x['interval_min'])}개")
if out:
    print("  예:", json.dumps(out[0], ensure_ascii=False)[:220])
