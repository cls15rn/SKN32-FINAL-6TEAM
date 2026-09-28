# mobility_scripts/collect/seoul_bus_collect.py
# 서울특별시_노선정보조회 서비스(data.go.kr 15000193 → ws.bus.go.kr) 수집
#
# 실행:
#   python mobility_scripts/collect/seoul_bus_collect.py --search 남산        # 탐색만, 저장 안 함
#   python mobility_scripts/collect/seoul_bus_collect.py --routes 01A 01B 402 # 수집
#   python mobility_scripts/collect/seoul_bus_collect.py --routes-file config/mobility/seoul_bus_targets.txt
#
# 2026-09-10 probe 로 확인한 것 (설계서 v0.2 §3-6 정정):
#  ★ 2단계가 아니라 1단계다. getBusRouteList 가 이미 term·firstBusTm·lastBusTm·기종점·운수사를
#    다 준다. getRouteInfoItem 은 호출할 필요가 없다(그 경로는 401 이 떨어진다).
#  ★ 시각은 YYYYMMDDHHMMSS 이고 자정을 넘으면 날짜가 실제로 넘어간다.
#    N15: first 20260910233000(23:30) / last 20260911033000(익일 03:30) → 27:30 으로 정규화.
#  ★ 검색은 부분일치다('721' → 721·7211·7212). 그리고 **경기·인천 노선이 섞여 온다**
#    ('01A' → 01A 와 5001A용인). 지하철에서 겪은 타 도시 혼입과 같은 함정이다.
#  ★ 빈 값이 공백 패딩으로 온다('   '). itemCount 는 0 으로 오므로 믿지 말고 len() 을 쓴다.
#  ★ 저상버스 시각(firstLowTm·lastLowTm)은 노선마다 들쭉날쭉하다(01A 는 2019년 날짜에
#    시각부가 000000, N15 는 정상). 신뢰할 수 없어 processed 에 넣지 않는다.
import argparse, collections, json, os, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
BASE = "http://ws.bus.go.kr/api/rest/busRouteInfo"
KEY = os.environ.get("DATA_GO_KR_KEY")
TODAY = datetime.now(KST).date().isoformat()
SOURCE_ID = f"seoul_bus_route@{TODAY}"

# routeType — probe 로 직접 확인한 것만 확정, 나머지는 추정이다.
ROUTE_TYPE = {
    "1": "공항", "2": "마을", "3": "간선", "4": "지선", "5": "순환",
    "6": "광역", "7": "인천", "8": "경기", "9": "폐지", "10": "투어", "15": "심야",
}
CONFIRMED_TYPES = {"1", "2", "3", "4", "5", "6", "7", "8", "10", "15"}   # 2026-09-10 실물로 본 값
NON_SEOUL_TYPES = {"7", "8"}                       # 인천·경기 면허

# busRouteId 앞 3자리가 지역 코드다. 서울은 100~124(자치구 수만큼), 인천 165, 경기 200~241.
# routeType 만으로는 안 된다 — 경기 부천013-1·광명01·하남01 이 전부 routeType 2(마을) 로 온다.
# 반대로 busRouteId 만으로도 안 된다 — '서울01출근'(107000006)은 서울 구 코드인데 화성 노선이다(routeType 8).
# 둘을 같이 걸어야 관측된 모든 행이 옳게 갈린다.
SEOUL_ID_RANGE = range(100, 125)


def mask(s):
    return re.sub(r"(?i)(serviceKey)=[^&\s'\"]+", r"\1=***", str(s))


def call(op, **params):
    params["ServiceKey"] = KEY
    params["resultType"] = "json"
    try:
        r = requests.get(f"{BASE}/{op}", params=params, timeout=30)
    except Exception as e:
        raise SystemExit(f"요청 실패 — {type(e).__name__}: {mask(e)}") from None
    try:
        body = r.json()
    except ValueError:
        raise SystemExit(f"JSON 아님 (HTTP {r.status_code}): {r.text[:300]}")
    if "status" in body and body.get("status") != 0:          # {error, message, status} 형태
        raise SystemExit(f"API 오류 {body.get('status')}: {body.get('message')}")
    head = body.get("msgHeader", {})
    cd = str(head.get("headerCd"))
    if cd == "4":            # '결과가 없습니다' — 오류가 아니라 빈 결과다
        return []
    if cd not in ("0", "None"):
        raise SystemExit(f"API 오류 {cd}: {head.get('headerMsg')}")
    items = (body.get("msgBody") or {}).get("itemList") or []
    return [{k: (v.strip() if isinstance(v, str) else v) for k, v in it.items()} for it in items]


def bus_time(s, base=None):
    """'YYYYMMDDHHMMSS' → 'HH:MM'. base 보다 날짜가 크면 24 를 더한다(익일 막차)."""
    s = (s or "").strip()
    if not re.fullmatch(r"\d{14}", s):
        return None, None
    d, hh, mm = s[:8], int(s[8:10]), int(s[10:12])
    if base and d > base:
        hh += 24 * (datetime.strptime(d, "%Y%m%d").date() - datetime.strptime(base, "%Y%m%d").date()).days
    return f"{hh:02d}:{mm:02d}", d


def is_seoul(item):
    rid = item.get("busRouteId", "")
    if item.get("routeType") in NON_SEOUL_TYPES:
        return False
    return rid[:3].isdigit() and int(rid[:3]) in SEOUL_ID_RANGE


ap = argparse.ArgumentParser()
ap.add_argument("--search", help="검색만 하고 결과를 출력한다(저장 없음)")
ap.add_argument("--routes", nargs="+", default=[], help="노선번호 정확 일치로 수집")
ap.add_argument("--routes-file", help="노선번호 목록 파일(한 줄에 하나, # 은 주석)")
args = ap.parse_args()

if not KEY:
    raise SystemExit(".env 의 DATA_GO_KR_KEY 가 없다")

# ── 탐색 모드 ────────────────────────────────────────────────
if args.search:
    for it in call("getBusRouteList", strSrch=args.search):
        f, base = bus_time(it.get("firstBusTm"))
        l, _ = bus_time(it.get("lastBusTm"), base)
        rt = it.get("routeType", "")
        mark = "" if is_seoul(it) else "  ← 서울 아님(제외 대상)"
        tm = (it.get("term") or "").strip()
        tm = "배차없음" if tm == "0" else f"배차 {tm:>3}분"      # 0 = 예약제·출퇴근 전용
        print(f"{it['busRouteNm']:<12} id={it['busRouteId']} "
              f"{ROUTE_TYPE.get(rt, '?'+rt):<4} {tm} "
              f"{f}~{l}  {it.get('stStationNm')}→{it.get('edStationNm')}{mark}")
    sys.exit()

targets = list(args.routes)
if args.routes_file:
    for line in Path(args.routes_file).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            targets.append(line)
if not targets:
    raise SystemExit("--routes 또는 --routes-file 또는 --search 중 하나가 필요하다")

# ── 수집 ─────────────────────────────────────────────────────
RAW_ROUTES = RAW_MOBILITY / "seoul_bus_routes.json"
RAW_STOPS = RAW_MOBILITY / "seoul_bus_stops.json"
OUT_ROUTE = PROCESSED / "mobility" / "bus_route_v1.jsonl"
OUT_STOPS = PROCESSED / "mobility" / "bus_stops_v1.jsonl"
OUT_COORDS = PROCESSED / "mobility" / "bus_stop_coords.json"
REPORT = PROCESSED / "mobility" / "bus_route_v1_report.md"
OUT_ROUTE.parent.mkdir(parents=True, exist_ok=True)

raw_routes, raw_stops = {}, {}
routes_out, stops_out = [], []
coords = {}
missing, excluded, unknown_type = [], [], collections.Counter()

for nm in targets:
    found = [it for it in call("getBusRouteList", strSrch=nm)]
    exact = [it for it in found if it.get("busRouteNm") == nm]
    seoul = [it for it in exact if is_seoul(it)]
    for it in exact:
        if not is_seoul(it):
            excluded.append((nm, it.get("busRouteNm"), it.get("busRouteId"), it.get("routeType")))
    if not seoul:
        missing.append((nm, [i.get("busRouteNm") for i in found][:6]))
        print(f"  ✗ {nm}: 정확 일치하는 서울 노선 없음 (검색 결과 {[i.get('busRouteNm') for i in found][:6]})")
        continue
    it = seoul[0]
    rid = it["busRouteId"]
    raw_routes[rid] = it

    rt = it.get("routeType", "")
    if rt not in CONFIRMED_TYPES:
        unknown_type[rt] += 1
    first, base = bus_time(it.get("firstBusTm"))
    last, last_d = bus_time(it.get("lastBusTm"), base)
    term_raw = (it.get("term") or "").strip()
    term = int(term_raw) if term_raw.isdigit() else None
    if term == 0:
        term = None
    routes_out.append({
        "route_id": rid, "route_nm": it.get("busRouteNm"),
        "route_type": rt,
        "route_type_nm": ROUTE_TYPE.get(rt),
        "route_type_grade": "확정" if rt in CONFIRMED_TYPES else "추정",
        "corp_nm": re.sub(r"\s+", " ", it.get("corpNm", "")),
        "st_station_nm": it.get("stStationNm"), "ed_station_nm": it.get("edStationNm"),
        "length_km": float(it["length"]) if it.get("length") else None,
        # term=0 은 배차가 0분이 아니라 배차 개념이 없다는 뜻이다(예약제·출퇴근 전용 노선).
        # 0 을 그대로 두면 "대기 0분"이 되어 판정이 낙관 쪽으로 틀어진다.
        "term_min": term,
        "first_time": first, "last_time": last,
        "crosses_midnight": bool(last and int(last[:2]) >= 24),
        # 첫차·막차는 기록이므로 확정. 대기는 배차 평균이라 추정 (rules.bus)
        "grade_service_window": "확정" if first and last else "근거없음",
        "grade_wait": "추정" if term else "근거없음",
        "source": "seoul_bus_route", "source_id": SOURCE_ID,
        "fetched_at": TODAY, "fetched_at_precision": "day",
    })

    stops = call("getStaionByRoute", busRouteId=rid)
    raw_stops[rid] = stops
    for s in stops:
        lat = float(s["gpsY"]) if s.get("gpsY") else None
        lng = float(s["gpsX"]) if s.get("gpsX") else None
        ars = s.get("arsId")
        stops_out.append({
            "route_id": rid, "route_nm": it.get("busRouteNm"),
            "seq": int(s["seq"]) if (s.get("seq") or "").isdigit() else None,
            "station_id": s.get("station"), "ars_id": ars,
            "station_nm": s.get("stationNm"),
            "lat": lat, "lng": lng,
            "direction": s.get("direction"),
            "sect_dist_m": int(s["fullSectDist"]) if (s.get("fullSectDist") or "").isdigit() else None,
            "transfer_yn": s.get("transYn"),
            "source": "seoul_bus_route", "source_id": SOURCE_ID,
            "fetched_at": TODAY, "fetched_at_precision": "day",
        })
        # 정류장 좌표는 노선 간 공유되므로 arsId 로 중복 제거. station_coords.json 과 같은 모양.
        if ars and lat and lng:
            key = f"버스|{ars}"
            coords.setdefault(key, {
                "station_key": key, "line": "버스", "station_nm": s.get("stationNm"),
                "station_nm_en": None, "station_cd": ars, "lat": lat, "lng": lng,
                "operator": None, "src_name": s.get("stationNm"), "src_line": "서울시내버스",
                "station_id": s.get("station"), "routes": [],
            })
            coords[key]["routes"].append(it.get("busRouteNm"))
    print(f"  ✓ {nm}: id={rid} {ROUTE_TYPE.get(rt, '?')} "
          f"{f'배차 {term}분' if term else '배차없음'} {first}~{last} · 정류장 {len(stops)}")

RAW_ROUTES.write_text(json.dumps(raw_routes, ensure_ascii=False, indent=1), encoding="utf-8")
RAW_STOPS.write_text(json.dumps(raw_stops, ensure_ascii=False, indent=1), encoding="utf-8")
with OUT_ROUTE.open("w", encoding="utf-8") as g:
    for r in routes_out:
        g.write(json.dumps(r, ensure_ascii=False) + "\n")
with OUT_STOPS.open("w", encoding="utf-8") as g:
    for r in stops_out:
        g.write(json.dumps(r, ensure_ascii=False) + "\n")
OUT_COORDS.write_text(json.dumps({
    "source": "seoul_bus_route", "source_id": SOURCE_ID, "built_at": datetime.now(KST).isoformat(timespec="seconds"),
    "count": len(coords), "stops": coords,
}, ensure_ascii=False, indent=1), encoding="utf-8")

night = [r["route_nm"] for r in routes_out if r["crosses_midnight"]]
lines = [
    "# 서울 시내버스 v1", "",
    f"생성 {datetime.now(KST).isoformat(timespec='seconds')} · source_id `{SOURCE_ID}`",
    f"노선 {len(routes_out)}/{len(targets)} · 정류장 행 {len(stops_out):,} · 좌표 있는 정류장 {len(coords):,}", "",
    "| 노선 | 유형 | 배차(분) | 첫차 | 막차 | 정류장 | 운수사 |", "|---|---|---|---|---|---|---|",
]
cnt = collections.Counter(s["route_nm"] for s in stops_out)
for r in routes_out:
    lines.append(f"| {r['route_nm']} | {r['route_type_nm'] or '?'} | {r['term_min']} | "
                 f"{r['first_time']} | {r['last_time']} | {cnt[r['route_nm']]} | {r['corp_nm']} |")
lines += ["", "## 읽는 법", "",
          "- 막차 시각이 24 를 넘으면 익일이다(N15 → 27:30). API 가 날짜를 실제로 넘겨 준다.",
          "- **첫차·막차는 확정, 대기는 추정**이다. `term` 은 노선당 값 하나라 시간대별 차이를 담지 못한다(rules.bus).",
          f"- 자정 넘는 노선: {night or '없음'}",
          f"- 서울 아님으로 제외: {excluded or '없음'}",
          f"- 못 찾은 노선: {missing or '없음'}",
          f"- probe 로 확인 못 한 routeType: {dict(unknown_type) or '없음'} (ROUTE_TYPE 표를 보강한다)",
          "- 저상버스 시각(firstLowTm·lastLowTm)은 노선마다 날짜가 뒤죽박죽이라 넣지 않았다 — raw 에만 있다.",
          ]
REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"\n노선 {len(routes_out)} · 정류장 {len(stops_out):,} · 좌표 {len(coords):,}")
print(f"→ {OUT_ROUTE}\n→ {OUT_STOPS}\n→ {OUT_COORDS}\n→ {REPORT}")
