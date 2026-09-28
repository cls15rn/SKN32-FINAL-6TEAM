# mobility_scripts/collect/build_bus_all_v1.py
# 서울 시내버스 21노선 → **717노선 전수**. API 호출 0회.
#
# 왜 호출이 0인가: seoul_bus_find_routes.py 의 --enumerate / --fetch-stops 가 이미
#   raw/mobility/seoul_bus_all_routes.json  (717 노선 · 첫차·막차·배차·기종점)
#   raw/mobility/seoul_bus_all_stops.json   (717 노선 전부의 정류장)
# 를 받아 뒀다. seoul_bus_collect.py --routes 로 717개를 돌리면 호출이 1,400여 회라
# 하루 쿼터 1,000 을 넘는다. **이미 받은 것을 쓰는 게 맞다.**
#
# 실행:
#   python mobility_scripts/collect/build_bus_all_v1.py                 # 만들어 보고 *_v2 로 쓴다(기본)
#   python mobility_scripts/collect/build_bus_all_v1.py --dry-run       # 쓰지 않고 숫자만 본다
#   python mobility_scripts/collect/build_bus_all_v1.py --replace-v1    # v1 을 덮어쓴다(되돌릴 수 없다)
#
# ★ 정규화 규칙은 seoul_bus_collect.py 를 **그대로** 옮겼다. 한 줄도 바꾸지 않는다 —
#   바꾸면 기존 21노선의 값이 달라지고, 그러면 회귀가 왜 움직였는지 구분이 안 된다.
#   [4] 검사가 그것을 확인한다.
#
# ★ fetched_at 은 **오늘이 아니라 캐시를 받은 날**이다. 값을 오늘 것으로 적으면
#   확인 시각을 위조하는 것이다. 캐시 안의 firstBusTm/lastBusTm 날짜에서 뽑는다.
#
# ★ 45번 방(2026-09-24 · GPT 대조 반영) — 운행 구간 정규화를 normalize_window() 하나로 모았다.
#   **seoul_bus_collect.py 와 여기서 갈라진다**(그쪽은 21노선 시절 규칙 그대로).
#   · 시간 기준일(time_base_date)과 확인 시각(fetched_at)은 **다른 값**이다. 기준일은 캐시 날짜 최빈값으로
#     **추정**하고, 모든 날짜가 {기준일, 기준일+1} 안에 있는지 검사한다 — 밖이면 중단(자정 끼고 수집한 혼합 캐시).
#     기준일보다 이른 날짜도 중단(종전 bus_time 은 음수 차이를 조용히 무시했다).
#   · 첫차·막차 모두 기준일과의 날짜 차이만큼 +24h (N26 원문 첫차 익일 00:00 → 24:00).
#   · 첫차·막차가 **둘 다 24시 전**인데 막차 < 첫차면 막차 +24h 로 **추정 보정**(심야A21 막차 같은 날 03:40 → 27:40).
#     원문 날짜 결함인지 다른 원인인지는 이것만으로 확정할 수 없다 → grade_service_window 「추정」.
#     보정 뒤에도 첫차 > 막차면 운행 구간을 해석할 수 없다 → None · 근거없음.
#   · 첫차 = 막차 이고 시각부 000000 → **운행 구간을 신뢰성 있게 해석할 수 없음** → None · 근거없음(8772).
#     「시각이 없다」고 단정하지 않는다 — 단일 출발·24시간 운행·결측 표현을 이 값만으로 못 가른다.
#   · service_days 필드 신설 — 'daily'(기본 · 추정 · 종전 동작) 또는 None(미상 → 판정기 no_data).
#     'weekday' 는 **두지 않는다** — 운행일/달력일·공휴일 의미를 확정하기 전에는 쓰는 노선도 판정 분기도 없다.
#   · [4] 는 v1 대조에서 **예상 값(EXPECTED_VALUES: 이전 → 이후)** 과 새 필드 값까지 검사한다. 어긋나면 쓰지 않는다.
import argparse, collections, json, re, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY, PROCESSED                      # noqa: E402

ROUTE_TYPE = {"1": "공항", "2": "마을", "3": "간선", "4": "지선", "5": "순환",
              "6": "광역", "7": "인천", "8": "경기", "9": "폐지", "10": "투어", "15": "심야"}
CONFIRMED_TYPES = {"1", "2", "3", "4", "5", "6", "7", "8", "10", "15"}
NON_SEOUL_TYPES = {"7", "8"}
SEOUL_ID_RANGE = range(100, 125)

# 운행 요일 — route_id → (값, 등급, 근거). 없는 노선은 SERVICE_DAYS_DEFAULT.  값: 'daily' · None(미상)
SERVICE_DAYS = {
    "101000005": (None, "근거없음",
                  "심야A21 — 서울시 2023-12-04 공식 게시물(mediahub.seoul.go.kr/archives/2009697)은 당시 "
                  "월~금 23:30~익일 05:10 운행을 말한다(과거 weekday 근거). 현행 적용 여부는 미확인 — "
                  "현재 API 시각(23:00~03:40 · 배차 140)이 그 게시물과 다르고, 2026-03-20 올빼미버스 공지에는 A21 이 없다"),
}
SERVICE_DAYS_DEFAULT = ("daily", "추정", None)   # 노선 API 에 요일 칸이 없다 — 종전 동작(매일)이 기본값
SERVICE_DAYS_VALUES = {"daily", None}

# [4] v1 대조 — (route_id, 필드) → (허용되는 이전 값들, 반드시 나와야 할 이후 값).
#   이전 값은 45 이전 판과 45 판 둘 다 허용한다(재생성해도 통과해야 하므로).
EXPECTED_VALUES = {
    ("100100586", "first_time"): ({"00:00", "24:00"}, "24:00"),            # N26
    ("100100586", "last_time"): ({"03:25", "27:25"}, "27:25"),
    ("100100586", "crosses_midnight"): ({False, True}, True),
    ("101000005", "last_time"): ({"03:40", "27:40"}, "27:40"),             # 심야A21 — 추정 보정
    ("101000005", "crosses_midnight"): ({False, True}, True),
    ("101000005", "grade_service_window"): ({"확정", "추정"}, "추정"),
    ("100100525", "first_time"): ({"00:00", None}, None),                   # 8772 — 해석 불가
    ("100100525", "last_time"): ({"00:00", None}, None),
    ("100100525", "grade_service_window"): ({"확정", "근거없음"}, "근거없음"),
}
NEW_FIELDS = {"service_days", "service_days_grade", "service_days_basis", "time_base_date", "window_note"}


def bus_time(s, base=None):
    """'YYYYMMDDHHMMSS' → 'HH:MM'. base 보다 날짜가 크면 24 를 더한다(익일 막차)."""
    s = (s or "").strip()
    if not re.fullmatch(r"\d{14}", s):
        return None, None
    d, hh, mm = s[:8], int(s[8:10]), int(s[10:12])
    if base and d > base:
        hh += 24 * (datetime.strptime(d, "%Y%m%d").date() - datetime.strptime(base, "%Y%m%d").date()).days
    return f"{hh:02d}:{mm:02d}", d


def _hm(m):
    return None if m is None else f"{m // 60:02d}:{m % 60:02d}"


def normalize_window(first_raw, last_raw, base_d):
    """원문 첫차·막차('YYYYMMDDHHMMSS') → (first 'HH:MM'|None, last, grade, note).

    base_d: 시간 기준일 'YYYYMMDD'. 정수 분으로 비교한다. 규칙은 파일 머리 주석(45번 방).
    날짜가 {base_d, base_d+1} 밖이면 ValueError — 혼합 캐시를 조용히 정규화하지 않는다.
    """
    from datetime import timedelta
    b = datetime.strptime(base_d, "%Y%m%d").date()
    ok_dates = {b, b + timedelta(days=1)}

    def mins(raw):
        raw = (raw or "").strip()
        if not re.fullmatch(r"\d{14}", raw):
            return None
        d = datetime.strptime(raw[:8], "%Y%m%d").date()
        if d not in ok_dates:
            raise ValueError(f"날짜 {raw[:8]} 가 기준일 {base_d}·+1 밖이다 — 혼합 캐시로 보고 중단한다")
        return (d - b).days * 1440 + int(raw[8:10]) * 60 + int(raw[10:12])

    ft, lt = (first_raw or "").strip(), (last_raw or "").strip()
    f, l = mins(ft), mins(lt)
    if f is None or l is None:
        return _hm(f), _hm(l), "근거없음", "첫차 또는 막차 원문이 없다"
    if ft == lt and ft.endswith("000000"):
        return None, None, "근거없음", (f"첫차=막차={ft}(시각부 000000) — 운행 구간을 신뢰성 있게 해석할 수 없음 "
                                      f"(단일 출발·24시간 운행·결측을 이 값만으로 못 가른다)")
    grade, note = "확정", None
    if l < f and f < 1440 and l < 1440:
        note = f"원문 첫차 {_hm(f)} > 막차 {_hm(l)}(같은 날) — 막차 +24h 로 추정 보정"
        l += 1440
        grade = "추정"
    if f > l:
        return None, None, "근거없음", f"첫차 {_hm(f)} > 막차 {_hm(l)} — 운행 구간을 해석할 수 없음"
    return _hm(f), _hm(l), grade, note


def is_seoul(item):
    rid = item.get("busRouteId", "")
    if item.get("routeType") in NON_SEOUL_TYPES:
        return False
    return rid[:3].isdigit() and int(rid[:3]) in SEOUL_ID_RANGE


def cache_date(routes):
    """캐시를 받은 날 = 노선 시각 문자열의 날짜 최빈값. 오늘 날짜를 쓰지 않는다."""
    c = collections.Counter()
    for it in routes.values():
        for k in ("firstBusTm", "lastBusTm"):
            v = (it.get(k) or "").strip()
            if re.fullmatch(r"\d{14}", v):
                c[v[:8]] += 1
    if not c:
        raise SystemExit("캐시에서 날짜를 못 뽑았다 — fetched_at 을 오늘로 적지 않는다. 중단한다.")
    d = c.most_common(1)[0][0]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 숫자만 낸다")
    ap.add_argument("--replace-v1", action="store_true", help="*_v1 을 덮어쓴다(되돌릴 수 없다)")
    a = ap.parse_args()
    suffix = "v1" if a.replace_v1 else "v2"

    src_routes = RAW_MOBILITY / "seoul_bus_all_routes.json"
    src_stops = RAW_MOBILITY / "seoul_bus_all_stops.json"
    for p in (src_routes, src_stops):
        if not p.exists():
            raise SystemExit(f"없다: {p}\n  먼저 seoul_bus_find_routes.py --enumerate / --fetch-stops 를 돌린다.")
    all_routes = json.loads(src_routes.read_text(encoding="utf-8"))
    all_stops = json.loads(src_stops.read_text(encoding="utf-8"))

    fetched = cache_date(all_routes)
    base_d = fetched.replace("-", "")     # 시간 기준일 — 캐시 날짜 최빈값(추정). fetched_at 과 뜻이 다르다
    try:
        import os
        mt = datetime.fromtimestamp(os.path.getmtime(src_routes)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        mt = "?"
    print(f"    시간 기준일 {fetched} (날짜 최빈값 · 추정) · 캐시 파일 시각 {mt}")
    source_id = f"seoul_bus_route@{fetched}"
    print(f"[0] 캐시 {len(all_routes)}노선 · 정류장 캐시 {len(all_stops)}노선 · 확인 시각 {fetched}")

    routes_out, stops_out, coords = [], [], {}
    notes = []
    excluded, no_stops, unknown_type = [], [], collections.Counter()

    for rid, it in all_routes.items():
        if not is_seoul(it):
            excluded.append((it.get("busRouteNm"), rid, it.get("routeType")))
            continue
        rt = it.get("routeType", "")
        if rt not in CONFIRMED_TYPES:
            unknown_type[rt] += 1
        try:
            first, last, win_grade, win_note = normalize_window(it.get("firstBusTm"), it.get("lastBusTm"), base_d)
        except ValueError as e:
            raise SystemExit(f"{it.get('busRouteNm')}({rid}): {e}")
        if win_note:
            notes.append(f"{it.get('busRouteNm')}: {win_note}")
        sd, sd_grade, sd_basis = SERVICE_DAYS.get(rid, SERVICE_DAYS_DEFAULT)
        term_raw = (it.get("term") or "").strip()
        term = int(term_raw) if term_raw.isdigit() else None
        if term == 0:
            term = None          # 배차 0분이 아니라 배차 개념이 없다는 뜻이다
        routes_out.append({
            "route_id": rid, "route_nm": it.get("busRouteNm"),
            "route_type": rt,
            "route_type_nm": ROUTE_TYPE.get(rt),
            "route_type_grade": "확정" if rt in CONFIRMED_TYPES else "추정",
            "corp_nm": re.sub(r"\s+", " ", it.get("corpNm", "")),
            "st_station_nm": it.get("stStationNm"), "ed_station_nm": it.get("edStationNm"),
            "length_km": float(it["length"]) if it.get("length") else None,
            "term_min": term,
            "first_time": first, "last_time": last,
            "crosses_midnight": bool(last and int(last[:2]) >= 24),
            "grade_service_window": win_grade,
            "grade_wait": "추정" if term else "근거없음",
            "window_note": win_note, "time_base_date": fetched,
            "service_days": sd, "service_days_grade": sd_grade, "service_days_basis": sd_basis,
            "source": "seoul_bus_route", "source_id": source_id,
            "fetched_at": fetched, "fetched_at_precision": "day",
        })

        stops = all_stops.get(rid) or []
        if not stops:
            no_stops.append((it.get("busRouteNm"), rid))
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
                "source": "seoul_bus_route", "source_id": source_id,
                "fetched_at": fetched, "fetched_at_precision": "day",
            })
            if ars and lat and lng:
                key = f"버스|{ars}"
                coords.setdefault(key, {
                    "station_key": key, "line": "버스", "station_nm": s.get("stationNm"),
                    "station_nm_en": None, "station_cd": ars, "lat": lat, "lng": lng,
                    "operator": None, "src_name": s.get("stationNm"), "src_line": "서울시내버스",
                    "station_id": s.get("station"), "routes": [],
                })
                coords[key]["routes"].append(it.get("busRouteNm"))

    by_type = collections.Counter(r["route_type_nm"] for r in routes_out)
    print(f"[1] 서울 노선 {len(routes_out)} · 제외(인천·경기 면허) {len(excluded)}")
    print(f"    유형: {dict(by_type)}")
    print(f"[2] 정류장 {len(stops_out)}행 · 고유 정류장(ars) {len(coords)}")
    if no_stops:
        print(f"    ! 정류장 캐시가 없는 노선 {len(no_stops)}개: {[n for n, _ in no_stops[:8]]}")
    print(f"[3] 배차 없음(term_min=NULL) {sum(1 for r in routes_out if r['term_min'] is None)} · "
          f"자정 넘김 {sum(1 for r in routes_out if r['crosses_midnight'])} · "
          f"막차 미상 {sum(1 for r in routes_out if not r['last_time'])}")
    if notes:
        print(f"    운행 구간 주석 {len(notes)}: " + " | ".join(notes))
    sdc = collections.Counter(str(r["service_days"]) for r in routes_out)
    print(f"    운행 요일 {dict(sdc)}")
    bad_sd = [r["route_nm"] for r in routes_out
              if r["service_days"] not in SERVICE_DAYS_VALUES
              or (r["service_days"], r["service_days_grade"], r["service_days_basis"])
              != SERVICE_DAYS.get(r["route_id"], SERVICE_DAYS_DEFAULT)]
    if bad_sd:
        raise SystemExit(f"    ★ service_days 값이 표와 다르다: {bad_sd[:5]}")
    if unknown_type:
        print(f"    ! 처음 보는 routeType {dict(unknown_type)} → route_type_grade=추정")

    # [4] ★ 기존 값이 예상한 것 말고는 한 글자도 안 바뀌었는가 (45: 예상 값 EXPECTED_VALUES)
    old = PROCESSED / "mobility" / "bus_route_v1.jsonl"
    if old.exists():
        prev = {json.loads(l)["route_id"]: json.loads(l) for l in old.open(encoding="utf-8")}
        now = {r["route_id"]: r for r in routes_out}
        missing = sorted(set(prev) - set(now))
        added = sorted(set(now) - set(prev))
        diff, changed = [], []
        for rid, o in prev.items():
            n = now.get(rid)
            if not n:
                continue
            # fetched_at·source_id 는 캐시 날짜라 달라질 수 있다. 그 둘만 빼고 비교한다.
            ks = set(o) | set(n)
            bad = {}
            for k in ks - {"fetched_at", "source_id"} - NEW_FIELDS:
                ov, nv = o.get(k), n.get(k)
                exp = EXPECTED_VALUES.get((rid, k))
                if exp:
                    before_ok, after = exp
                    if ov not in before_ok or nv != after:
                        bad[k] = (ov, nv, f"예상 {sorted(map(str, before_ok))}→{after}")
                    elif ov != nv:
                        changed.append((o.get("route_nm"), k, ov, nv))
                elif ov != nv:
                    bad[k] = (ov, nv)
            if bad:
                diff.append((rid, o.get("route_nm"), bad))

        unexpected_missing = [key for key in EXPECTED_VALUES if key[0] not in now]
        print(f"[4] v1 대조 — 사라진 노선 {len(missing)} · 새 노선 {len(added)} · 예상대로 바뀐 값 {len(changed)} · "
              f"예상 밖 {len(diff)}노선")
        for nm, k, ov, nv in changed:
            print(f"    = {nm} {k}: {ov} → {nv}")
        for rid, nm, d in diff[:5]:
            print(f"    ! {nm}({rid}) {d}")
        if missing:
            print(f"    ! 사라짐: {missing}")
        if added:
            print(f"    ! 새 노선: {added[:5]}")
        if unexpected_missing:
            print(f"    ! 예상 값 대상 노선이 없다: {unexpected_missing}")
        if missing or diff or added or unexpected_missing:
            print("    ★ 확장이 기존 값을 바꿨다. 쓰지 않는다.")
            if not a.dry_run:
                raise SystemExit(2)

    if a.dry_run:
        print("[5] --dry-run — 아무것도 쓰지 않았다")
        return

    outs = {
        PROCESSED / "mobility" / f"bus_route_{suffix}.jsonl":
            "\n".join(json.dumps(r, ensure_ascii=False) for r in routes_out) + "\n",
        PROCESSED / "mobility" / f"bus_stops_{suffix}.jsonl":
            "\n".join(json.dumps(s, ensure_ascii=False) for s in stops_out) + "\n",
        PROCESSED / "mobility" / ("bus_stop_coords.json" if a.replace_v1 else "bus_stop_coords_v2.json"):
            json.dumps(coords, ensure_ascii=False, indent=1),
    }
    for p, text in outs.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"[5] {p.name}  {len(text):,} bytes")
    print(f"\n  다음: 회귀를 새 파일로 한 번 돌려 본다(v1 은 그대로 둔 채)\n"
          f"  $env:PYTHONPATH=\"final_project_cs\"\n"
          f"  python -m app.modules.travel_ops.mobility.engine.verify_time --cases final_project_cs/tests/unit/travel/mobility/bus_legs_v1.json --check-expect \\\n"
          f"      --bus-route data/travel/processed/mobility/bus_route_{suffix}.jsonl \\\n"
          f"      --bus-stops data/travel/processed/mobility/bus_stops_{suffix}.jsonl")


if __name__ == "__main__":
    main()
