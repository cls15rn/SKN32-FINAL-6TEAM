# mobility_scripts/collect/seoul_bus_find_routes.py
# "이 두 지점을 잇는 서울 시내버스가 무엇인가"를 코드로 찾는다.
#
# 왜 필요한가: getBusRouteList 의 strSrch 는 **노선번호만** 본다(수집 결과 v1 §3).
# "성수", "광화문" 같은 정류장명으로는 검색이 안 되므로, 노선을 먼저 전수로 세우고
# 정류장 좌표로 걸러내는 수밖에 없다. 한 번 받아 두면 이후 구간이 추가돼도 호출 없이 찾는다
# (수집 결과 v1 §8-2 가 말한 "다른 구간이 동선에 들어오면 추가 수집"을 이 파일이 대신한다).
#
# 실행 순서:
#   1) python mobility_scripts/collect/seoul_bus_find_routes.py --enumerate
#        숫자 0~9 로 부분일치 검색 → 서울 노선 전체 목록 (호출 10여 회)
#   2) python mobility_scripts/collect/seoul_bus_find_routes.py --fetch-stops
#        노선마다 정류장 전체를 받아 캐시. **중단되면 다시 실행하면 이어서 받는다.**
#        하루 쿼터 1,000 이므로 --max-calls 로 끊어 여러 날에 나눠도 된다.
#   3) python mobility_scripts/collect/seoul_bus_find_routes.py --connects 건대입구 성수 --radius 500
#        두 지점 모두를 지나는 노선을 출력 (호출 0)
#
# 이 스크립트는 processed/ 를 건드리지 않는다. 찾은 노선번호를 기존
# seoul_bus_collect.py --routes 에 넣어 정식 수집하는 것이 최종 단계다.
import argparse, json, math, os, re, sys, time
from datetime import timezone, timedelta

import requests
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
BASE = "http://ws.bus.go.kr/api/rest/busRouteInfo"
KEY = os.environ.get("DATA_GO_KR_KEY")

NON_SEOUL_TYPES = {"7", "8"}          # 인천·경기 면허
SEOUL_ID_RANGE = range(100, 125)      # busRouteId 앞 3자리 = 지역 코드
ROUTE_TYPE = {
    "1": "공항", "2": "마을", "3": "간선", "4": "지선", "5": "순환",
    "6": "광역", "7": "인천", "8": "경기", "9": "폐지", "10": "투어", "15": "심야",
}

ALL_ROUTES = RAW_MOBILITY / "seoul_bus_all_routes.json"    # 노선 목록 (전수)
ALL_STOPS = RAW_MOBILITY / "seoul_bus_all_stops.json"      # 노선별 정류장 캐시
STATION_COORDS = PROCESSED / "mobility" / "station_coords.json"

calls = 0


def mask(s):
    return re.sub(r"(?i)(serviceKey)=[^&\s'\"]+", r"\1=***", str(s))


def call(op, **params):
    """seoul_bus_collect.py 와 같은 규약 — ServiceKey(대문자 S) · resultType=json · headerCd 4 는 빈 결과."""
    global calls
    params["ServiceKey"] = KEY
    params["resultType"] = "json"
    calls += 1
    try:
        r = requests.get(f"{BASE}/{op}", params=params, timeout=30)
    except Exception as e:
        raise SystemExit(f"요청 실패 — {type(e).__name__}: {mask(e)}") from None
    try:
        body = r.json()
    except ValueError:
        raise SystemExit(f"JSON 아님 (HTTP {r.status_code}): {r.text[:300]}")
    if "status" in body and body.get("status") != 0:
        raise SystemExit(f"API 오류 {body.get('status')}: {body.get('message')}")
    head = body.get("msgHeader", {})
    cd = str(head.get("headerCd"))
    if cd == "4":
        return []
    if cd not in ("0", "None"):
        raise SystemExit(f"API 오류 {cd}: {head.get('headerMsg')}")
    items = (body.get("msgBody") or {}).get("itemList") or []
    return [{k: (v.strip() if isinstance(v, str) else v) for k, v in it.items()} for it in items]


def is_seoul(item):
    rid = item.get("busRouteId", "")
    if item.get("routeType") in NON_SEOUL_TYPES:
        return False
    return rid[:3].isdigit() and int(rid[:3]) in SEOUL_ID_RANGE


def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def resolve_point(token, stations):
    """'성수' → 지하철역 좌표, '37.5445,127.0557' → 그대로. 역명이 여러 노선에 있으면 첫 좌표(같은 역)."""
    if re.fullmatch(r"\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*", token):
        lat, lng = [float(x) for x in token.split(",")]
        return token, lat, lng
    hits = [v for k, v in stations.items() if v.get("station_nm") == token]
    if not hits:
        hits = [v for k, v in stations.items() if token in (v.get("station_nm") or "")]
    if not hits:
        raise SystemExit(f"'{token}' 을 station_coords.json 에서 못 찾았다. 좌표(위도,경도)로 줘도 된다")
    h = hits[0]
    label = f"{h['station_nm']}역" + (f"({len(hits)}개 노선)" if len(hits) > 1 else "")
    return label, h["lat"], h["lng"]


ap = argparse.ArgumentParser()
ap.add_argument("--enumerate", action="store_true", help="서울 노선 전체 목록을 만든다(호출 10여 회)")
ap.add_argument("--fetch-stops", action="store_true", help="노선별 정류장을 받아 캐시한다(이어받기 가능)")
ap.add_argument("--max-calls", type=int, default=900, help="이번 실행에서 쓸 최대 호출 수(하루 쿼터 1,000)")
ap.add_argument("--connects", nargs="+", metavar="지점", help="이 지점들을 모두 지나는 노선을 찾는다(역명 또는 위도,경도)")
ap.add_argument("--near", nargs="+", metavar="지점", help="각 지점 근처를 지나는 노선을 따로따로 본다")
ap.add_argument("--radius", type=int, default=500, help="지점에서 정류장까지 허용 거리(m)")
ap.add_argument("--include-tour", action="store_true", help="관광버스(routeType 10)도 후보에 넣는다")
args = ap.parse_args()

if (args.enumerate or args.fetch_stops) and not KEY:
    raise SystemExit(".env 의 DATA_GO_KR_KEY 가 없다")

# ── 1) 노선 전수 목록 ────────────────────────────────────────
if args.enumerate:
    # 서울 노선번호는 전부 숫자를 하나 이상 포함한다(01A·N15·종로01 모두). 부분일치라 0~9 면 덮인다.
    found = {}
    for q in "0123456789":
        items = call("getBusRouteList", strSrch=q)
        new = 0
        for it in items:
            rid = it.get("busRouteId")
            if rid and rid not in found and is_seoul(it):
                found[rid] = it
                new += 1
        print(f"  '{q}' → 결과 {len(items):>4} · 서울 신규 {new:>3} · 누적 {len(found)}")
    ALL_ROUTES.parent.mkdir(parents=True, exist_ok=True)
    ALL_ROUTES.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n서울 노선 {len(found)}개 → {ALL_ROUTES}  (호출 {calls})")
    print("다음: --fetch-stops")
    sys.exit()

# ── 2) 노선별 정류장 캐시 (이어받기) ─────────────────────────
if args.fetch_stops:
    routes = load(ALL_ROUTES, None)
    if not routes:
        raise SystemExit("먼저 --enumerate 를 돌려라")
    cache = load(ALL_STOPS, {})
    todo = [rid for rid in routes if rid not in cache]
    print(f"노선 {len(routes)} · 캐시됨 {len(cache)} · 남음 {len(todo)} · 이번 실행 최대 {args.max_calls}")
    for i, rid in enumerate(todo, 1):
        if calls >= args.max_calls:
            print(f"\n최대 호출 수에 도달. {len(cache)}/{len(routes)} 까지 받았다. 다시 실행하면 이어받는다.")
            break
        nm = routes[rid].get("busRouteNm")
        try:
            cache[rid] = call("getStaionByRoute", busRouteId=rid)
        except SystemExit as e:
            print(f"  ✗ {nm}({rid}) {e}")
            break
        if i % 25 == 0 or i == len(todo):
            ALL_STOPS.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(todo)} · 최근 {nm} 정류장 {len(cache[rid])} · 저장")
        time.sleep(0.05)
    ALL_STOPS.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"\n캐시 {len(cache)}/{len(routes)} → {ALL_STOPS}  (호출 {calls})")
    if len(cache) < len(routes):
        print("남은 노선은 내일 다시 --fetch-stops 로 이어받으면 된다.")
    sys.exit()

# ── 3) 조회 (호출 0) ─────────────────────────────────────────
if not (args.connects or args.near):
    raise SystemExit("--enumerate / --fetch-stops / --connects / --near 중 하나가 필요하다")

routes = load(ALL_ROUTES, None)
cache = load(ALL_STOPS, None)
if not routes or not cache:
    raise SystemExit("먼저 --enumerate 와 --fetch-stops 를 돌려라")
stations = (load(STATION_COORDS, {}) or {}).get("stations", {})

points = [resolve_point(t, stations) for t in (args.connects or args.near)]


def stops_near(stops, lat, lng, radius):
    """반경 안 정류장을 **전부** 돌려준다. 왕복 노선은 같은 지점이 두 방향에 다 있다."""
    out = []
    for s in stops:
        try:
            d = haversine_m(lat, lng, float(s["gpsY"]), float(s["gpsX"]))
        except (TypeError, ValueError, KeyError):
            continue
        if d <= radius:
            seq = int(s["seq"]) if str(s.get("seq") or "").isdigit() else None
            out.append({"nm": s.get("stationNm"), "ars": s.get("arsId"),
                        "d": d, "seq": seq, "dir": s.get("direction")})
    return sorted(out, key=lambda x: (x["seq"] is None, x["seq"]))


hit = []
for label, lat, lng in points:
    near = {}
    for rid, stops in cache.items():
        if not args.include_tour and routes.get(rid, {}).get("routeType") == "10":
            continue
        found = stops_near(stops, lat, lng, args.radius)
        if found:
            near[rid] = found
    hit.append((label, near))
    print(f"{label} 반경 {args.radius}m — 노선 {len(near)}개")

if args.near:
    for label, near in hit:
        print(f"\n■ {label}")
        for rid, ss in sorted(near.items(), key=lambda kv: kv[1][0]["d"]):
            r = routes[rid]
            spots = " / ".join(f"{x['nm']}({x['ars']}) seq{x['seq']} {x['d']:.0f}m" for x in ss[:3])
            print(f"  {r['busRouteNm']:<10} {ROUTE_TYPE.get(r.get('routeType'), '?'):<3} {spots}")
    sys.exit()


def ordered_chain(seq_lists):
    """지점 순서대로 seq 가 증가하는 조합을 찾는다. 찾으면 각 지점에서 고른 정류장, 없으면 None.
    왕복 노선에서 '타면 그 방향으로 간다'가 성립하는지를 이걸로 가른다."""
    best = None
    def walk(i, prev_seq, chosen):
        nonlocal best
        if i == len(seq_lists):
            span = chosen[-1]["seq"] - chosen[0]["seq"]
            if best is None or span < best[0]:
                best = (span, list(chosen))
            return
        for x in seq_lists[i]:
            if x["seq"] is None:
                continue
            if prev_seq is None or x["seq"] > prev_seq:
                chosen.append(x)
                walk(i + 1, x["seq"], chosen)
                chosen.pop()
    walk(0, None, [])
    return best


common = set.intersection(*[set(n) for _, n in hit]) if hit else set()
rows, wrong_dir = [], []
for rid in common:
    r = routes[rid]
    chain = ordered_chain([hit[i][1][rid] for i in range(len(hit))])
    term = (r.get("term") or "").strip()
    term_s = "배차없음" if term in ("", "0") else f"{term}분"
    if chain is None:
        wrong_dir.append(r["busRouteNm"])
        continue
    span, picks = chain
    rows.append((r["busRouteNm"], ROUTE_TYPE.get(r.get("routeType"), "?"), term_s, span,
                 " → ".join(f"{x['nm']}(seq{x['seq']}, {x['d']:.0f}m)" for x in picks),
                 f"{r.get('stStationNm')}~{r.get('edStationNm')}"))

print(f"\n■ 순서대로 지나는 노선 {len(rows)}개 — 반경 {args.radius}m, seq 증가 확인")
for nm, ty, tm, span, path, ends in sorted(rows, key=lambda x: x[3]):
    warn = "  ※ 배차 근거없음" if tm == "배차없음" else ""
    print(f"  {nm:<10} {ty:<3} {tm:<7} {span:>3}정거장  {path}   [{ends}]{warn}")
if wrong_dir:
    print(f"\n  방향이 반대라 제외: {', '.join(sorted(wrong_dir))}")
if not rows:
    print("  없다. --radius 를 늘리거나 지점을 좌표로 직접 주자.")
else:
    print("\n다음: 아래 번호를 기존 수집기에 넣는다 (기존 7개를 함께 줘야 덮어쓰기로 사라지지 않는다)")
    print("  python mobility_scripts/collect/seoul_bus_collect.py --routes 01A 01B 종로01 종로02 종로03 종로09 종로11 "
          + " ".join(nm for nm, *_ in sorted(rows, key=lambda x: x[3])))
