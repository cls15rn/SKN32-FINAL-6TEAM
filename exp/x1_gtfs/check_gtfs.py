#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""보존 검사 5 — 만든 GTFS 를 원천 시간표와 다시 대조한다. (X1 · 2026-10-04)

build_gtfs.py 의 중간 값은 쓰지 않는다. 읽는 것은 산출 파일(gtfs/*.txt · row_map · orphan_rows · est_stops)과 원천뿐이다.

원천에 열차 번호가 없어 「편 수 · 열차 식별자」 검사를 행 단위로 다시 읽었다(X1 ⓪ · 본인 결정 10/4):
  ① 행 수      원천 행 = 편에 들어간 행 + 홑행(목록) · 들어간 행은 역·시각이 원천과 같다 · 그 밖의 stop_time 은 종착 추정뿐
  ② 식별자     원천 행 ↔ (trip_id, stop_sequence) 1:1 · trip_id 중복 없음 · 편마다 정차 2개 이상 · 시각이 뒤로 가지 않음
  ③ 운행일     편 안의 행은 요일형이 하나 · 2026~2027 모든 날짜에서 GTFS 가 켜는 요일형 = 판정기 규칙(day_type_of)이 고르는 요일형
  ④ 행선지     행선지가 적힌 행은 그 편의 끝 역이 그 행선지다(조각·순환·표기 어긋남은 따로 센다 · 다른 역에서 끝나면 실패)
  ⑤ 통과역     원천에 없는 정차는 편의 마지막 1개(종착 추정)뿐 · 편 중간에 끼운 정차 0

  python exp/x1_gtfs/check_gtfs.py            # 기본 경로
끝 코드: 5/5 면 0, 아니면 1.
"""
import argparse
import collections
import csv
import datetime
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next((p for p in HERE.parents if (p / "datasets" / "mobility").is_dir()), HERE.parents[1])
M = REPO / "datasets" / "mobility" / "processed" / "mobility"
RULES = REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules"


def rd(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def sec(s):
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timetable", default=str(M / "timetable_v1.jsonl.gz"))
    ap.add_argument("--order", default=str(M / "line_station_order_v1.json"))
    ap.add_argument("--holidays", default=str(RULES / "holidays_2026_2027.json"))
    ap.add_argument("--daytype-csv", default=str(M / "graph" / "daytype_calendar_v1.csv"))
    ap.add_argument("--out", default=str(REPO.parent / "exp" / "x1_gtfs" / "out"))
    a = ap.parse_args()
    out, gt = Path(a.out), Path(a.out) / "gtfs"

    rows = [json.loads(ln) for ln in gzip.open(a.timetable, "rt", encoding="utf-8") if ln.strip()]
    order = json.loads(Path(a.order).read_text(encoding="utf-8"))
    alias = order.get("dest_alias") or {}
    N = len(rows)
    stops = {r["stop_id"]: r for r in rd(gt / "stops.txt")}
    trips = rd(gt / "trips.txt")
    routes = {r["route_id"]: r["route_short_name"] for r in rd(gt / "routes.txt")}
    st = collections.defaultdict(list)
    n_st = 0
    for r in rd(gt / "stop_times.txt"):
        st[r["trip_id"]].append(r)
        n_st += 1
    for v in st.values():
        v.sort(key=lambda r: int(r["stop_sequence"]))
    with gzip.open(out / "row_map.csv.gz", "rt", encoding="utf-8") as f:
        rmap = list(csv.DictReader(f))
    orphans = rd(out / "orphan_rows.csv")
    est = rd(out / "est_stops.csv")
    trip = {t["trip_id"]: t for t in trips}
    res, notes = {}, {}

    # ① 행 수
    bad = []
    mapped = {}
    for m in rmap:
        i = int(m["row_no"]) - 1
        key = (m["trip_id"], int(m["stop_sequence"]))
        if i in mapped:
            bad.append(("행이 두 번", i + 1))
        mapped[i] = key
    by_key = {(t, int(r["stop_sequence"])): r for t, v in st.items() for r in v}
    for i, key in mapped.items():
        r, g = rows[i], by_key.get(key)
        if g is None:
            bad.append(("stop_time 없음", i + 1))
            continue
        s = stops[g["stop_id"]]
        if (s["stop_name"], s["stop_desc"]) != (r["station_nm"], r["line"]) or g["departure_time"] != r["dep_time"] \
                or g["arrival_time"] != r["dep_time"]:
            bad.append(("역·시각 다름", i + 1))
    orow = {int(o["row_no"]) - 1 for o in orphans}
    if orow & set(mapped):
        bad.append(("홑행이 편에도 있음", len(orow & set(mapped))))
    miss = N - len(mapped) - len(orow)
    extra = n_st - len(mapped)
    ok1 = not bad and miss == 0 and extra == len(est)
    res["① 행 수"] = ok1
    notes["① 행 수"] = (f"원천 {N:,} = 편에 든 행 {len(mapped):,} + 홑행 {len(orow):,} (빠진 행 {miss}) · "
                      f"stop_times {n_st:,} = 원천 행 {len(mapped):,} + 종착 추정 {extra:,} (est_stops {len(est):,}) · 어긋남 {len(bad)}")

    # ② 식별자 1:1
    back = collections.Counter(mapped.values())
    dup_key = sum(1 for v in back.values() if v > 1)
    dup_trip = len(trips) - len(trip)
    short = [t for t, v in st.items() if len(v) < 2]
    backward = 0
    seq_bad = 0
    for t, v in st.items():
        if [int(r["stop_sequence"]) for r in v] != list(range(1, len(v) + 1)):
            seq_bad += 1
        tt = [sec(r["departure_time"]) for r in v]
        backward += sum(1 for x, y in zip(tt, tt[1:]) if y < x)
    no_trip = set(st) ^ set(trip)
    ok2 = not (dup_key or dup_trip or short or backward or seq_bad or no_trip)
    res["② 식별자 1:1"] = ok2
    notes["② 식별자 1:1"] = (f"편 {len(trip):,} · 한 자리에 두 행 {dup_key} · trip_id 중복 {dup_trip} · 정차 1개 편 {len(short)} · "
                           f"시각 역행 {backward} · 순번 끊김 {seq_bad} · trips↔stop_times 불일치 {len(no_trip)}")

    # ③ 운행일
    by_trip_rows = collections.defaultdict(list)
    for i, (t, k) in mapped.items():
        by_trip_rows[t].append(i)
    has_sat = {r["line"] for r in rows if r["day_type"] == "saturday"}
    mixed = wrong = 0
    for t, idx in by_trip_rows.items():
        days = {rows[i]["day_type"] for i in idx}
        lines = {rows[i]["line"] for i in idx}
        if len(days) != 1 or len(lines) != 1:
            mixed += 1
            continue
        day, ln = days.pop(), lines.pop()
        want = "WD" if day == "weekday" else "SAT" if day == "saturday" else ("SUNHOL" if ln in has_sat else "SATSUNHOL")
        if trip[t]["service_id"] != want or routes[trip[t]["route_id"]] != ln:
            wrong += 1
    hol = json.loads(Path(a.holidays).read_text(encoding="utf-8"))["holidays"]
    cal = {r["service_id"]: r for r in rd(gt / "calendar.txt")}
    exc = collections.defaultdict(dict)
    for r in rd(gt / "calendar_dates.txt"):
        exc[r["date"]][r["service_id"]] = r["exception_type"]
    wk = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    d0 = datetime.date(int(cal["WD"]["start_date"][:4]), 1, 1)
    d1 = datetime.date(int(cal["WD"]["end_date"][:4]), 12, 31)
    day_bad, n_days, d = [], 0, d0
    while d <= d1:
        ymd = d.strftime("%Y%m%d")
        act = {s for s, c in cal.items() if c[wk[d.weekday()]] == "1" and c["start_date"] <= ymd <= c["end_date"]}
        for s, e in exc.get(ymd, {}).items():
            act = act | {s} if e == "1" else act - {s}
        eng = "holiday" if (d.weekday() >= 5 or d.isoformat() in hol) else "weekday"      # 판정기 timeutil.day_type_of 와 같은 규칙
        # 판정기 요일형 → 켜져야 할 운행: 평일 = WD · 휴일 = 토요일 표 없는 노선 SATSUNHOL, 있는 노선은 토요일(공휴일 아님) SAT · 그 밖 SUNHOL
        if eng == "weekday":
            want = {"WD"}
        elif d.weekday() == 5 and d.isoformat() not in hol:
            want = {"SATSUNHOL", "SAT"}
        else:
            want = {"SATSUNHOL", "SUNHOL"}
        if act != want:
            day_bad.append(ymd)
        n_days += 1
        d += datetime.timedelta(1)
    csv_bad = n_csv = 0
    if Path(a.daytype_csv).exists():                         # 34 의 달력(2025-09~2026-08)과 겹치는 날 — 참고 대조
        for r in rd(a.daytype_csv):
            dd = datetime.date(int(r["date"][:4]), int(r["date"][4:6]), int(r["date"][6:]))
            if not d0 <= dd <= d1:
                continue
            n_csv += 1
            eng = "holiday" if (dd.weekday() >= 5 or dd.isoformat() in hol) else "weekday"
            csv_bad += (("weekday" if r["daytype"] == "평일" else "holiday") != eng)
    ok3 = not (mixed or wrong or day_bad)
    res["③ 운행일"] = ok3
    notes["③ 운행일"] = (f"요일형 섞인 편 {mixed} · 운행 표가 다른 편 {wrong} · 날짜 {n_days}일 중 판정기 요일형과 다른 날 {len(day_bad)} · "
                       f"daytype_calendar_v1 과 겹치는 {n_csv}일 중 다른 날 {csv_bad} · 토요일 표 따로 있는 노선 {sorted(has_sat)}")

    # ④ 행선지
    loops = {ln for ln, L in order["lines"].items() if L.get("is_loop")}
    cat = collections.Counter()
    bad4 = []
    defect = collections.Counter()
    last_stop = {t: stops[v[-1]["stop_id"]]["stop_name"] for t, v in st.items()}
    visits = {t: [stops[r["stop_id"]]["stop_name"] for r in v] for t, v in st.items()}
    est_trip = {e["trip_id"] for e in est}
    adj = {}
    for ln, L in order["lines"].items():
        g = collections.defaultdict(set)
        for e in L["edges"]:
            g[e["a"]].add(e["b"])
            g[e["b"]].add(e["a"])
        adj[ln] = g
    pcache = {}

    def toward(ln, s, d):
        """s 에서 d 로 가는 최단 경로의 역 집합(s 제외)."""
        key = (ln, s, d)
        if key not in pcache:
            prev, q = {s: None}, [s]
            for u in q:
                if u == d:
                    break
                for v in adj[ln][u]:
                    if v not in prev:
                        prev[v] = u
                        q.append(v)
            path, u = set(), d if d in prev else None
            while u is not None and u != s:
                path.add(u)
                u = prev[u]
            pcache[key] = path
        return pcache[key]

    for i, (t, k) in mapped.items():
        r = rows[i]
        d_ = r.get("dest_nm")
        if not d_:
            cat["행선지 없음(원천)"] += 1
            continue
        d_ = alias.get(r["line"], {}).get(d_, d_)
        if last_stop[t] == d_:
            cat["끝 역 = 행선지"] += 1
        elif d_ in visits[t][k:]:
            cat["행선지를 지나 계속 감(순환·관문)" if r["line"] in loops else "행선지를 지나 계속 감"] += 1
            if r["line"] not in loops:
                cat["  └ 가지 노선(옛 종착 표기 — 8호선 구리 등)"] += 1
        elif d_ in visits[t][:k] or (r["line"] not in loops and d_ != r["station_nm"] and (
                any(x not in toward(r["line"], r["station_nm"], d_) for x in visits[t][k:]) or
                (k == len(visits[t]) and k >= 2 and visits[t][k - 2] in toward(r["line"], r["station_nm"], d_)))):
            cat["행선지와 다른 쪽으로 감(시발역·옛 표기 — 원천 결함)"] += 1          # 그 뒤 정차가 행선지 가는 길 밖이다
            defect[(r["line"], r["day_type"], r["dir"], r.get("dest_nm"))] += 1
        elif t not in est_trip and trip[t]["trip_headsign"] in (r.get("dest_nm"), d_):
            cat["조각 — 행선지에 못 닿고 끊김"] += 1
        else:
            cat["다른 역에서 끝남"] += 1
            if len(bad4) < 2000:
                bad4.append((r["line"], r["day_type"], r["station_nm"], r["dep_time"], r["dir"], r.get("dest_nm"), t, last_stop[t]))
    res["④ 행선지"] = cat["다른 역에서 끝남"] == 0
    notes["④ 행선지"] = " · ".join(f"{k.strip()} {v:,}" for k, v in cat.most_common())

    # ⑤ 통과역 없음
    est_key = {(e["trip_id"]) for e in est}
    mid_extra = last_extra = 0
    for t, v in st.items():
        for r in v:
            key = (t, int(r["stop_sequence"]))
            if key not in back:
                if int(r["stop_sequence"]) == len(v) and t in est_key and r.get("timepoint") == "0":
                    last_extra += 1
                else:
                    mid_extra += 1
    ok5 = mid_extra == 0 and last_extra == len(est)
    res["⑤ 통과역 없음"] = ok5
    notes["⑤ 통과역 없음"] = (f"원천에 없는 정차 {last_extra + mid_extra:,} = 편 마지막 종착 추정 {last_extra:,} + 편 중간 {mid_extra} · "
                          f"종착 추정 종류 {dict(collections.Counter(e['kind'] for e in est))}")

    print()
    for k in res:
        print(f"  [{'통과' if res[k] else '실패'}] {k} — {notes[k]}")
    n_ok = sum(res.values())
    print(f"\n  보존 검사 {n_ok}/5")
    rep = {"checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "pass": n_ok,
           "result": {k: bool(v) for k, v in res.items()}, "notes": notes, "dest_categories": dict(cat),
           "dest_mismatch_sample": bad4[:200],
           "dest_label_defect": [list(k) + [v] for k, v in defect.most_common()]}
    (out / "check_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    if defect:
        print("  ④ 원천 표기 결함으로 본 행(노선·요일형·dir·적힌 행선지):", ", ".join(f"{k[0]} {k[1]} {k[2]} {k[3]} {v:,}" for k, v in defect.most_common(12)))
    if bad4:
        c = collections.Counter((b[0], b[1], b[5], b[7]) for b in bad4)
        print("  ④ 다른 역에서 끝남 상위:", c.most_common(15))
    sys.exit(0 if n_ok == 5 else 1)


if __name__ == "__main__":
    main()
