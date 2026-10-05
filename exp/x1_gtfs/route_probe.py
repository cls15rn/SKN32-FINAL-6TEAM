#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""만든 GTFS 가 「길찾기에 쓸 수 있는 모양」인지 보는 작은 탐침. (X1 · 2026-10-04)

MOTIS 를 대신하는 것이 아니다 — MOTIS 적재 전에, 형식(참조 끊김)과 간단 질의 3건이 답이 나오는지를 파이썬만으로 확인한다.
방법은 연결 훑기(Connection Scan): 그 날짜에 도는 편의 (출발→다음 정차) 조각을 시각순으로 한 번 훑는다.
환승은 transfers.txt 의 쌍만 쓴다(같은 정류장에서 갈아타기는 0초). 경로는 화면에만 찍고 저장하지 않는다.

  python exp/x1_gtfs/route_probe.py                                  # 기본 3건 · 2026-10-14(수) 09:00 출발
  python exp/x1_gtfs/route_probe.py --date 2026-10-17 --time 22:30 --from 02호선:강남 --to 02호선:홍대입구
"""
import argparse
import collections
import csv
import datetime
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next((p for p in HERE.parents if (p / "datasets" / "mobility").is_dir()), HERE.parents[1])
DEFAULT = [("02호선:강남", "02호선:홍대입구"), ("공항철도:서울역", "공항철도:인천공항1터미널"), ("07호선:노원", "02호선:사당")]


def rd(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def sec(s):
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(x)


def hms(t):
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def fmt_check(gt):
    """형식 — 필수 파일·참조 끊김."""
    need = ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt", "calendar.txt", "calendar_dates.txt",
            "transfers.txt", "feed_info.txt"]
    miss = [n for n in need if not (gt / n).exists()]
    stops = {r["stop_id"] for r in rd(gt / "stops.txt")}
    routes = {r["route_id"] for r in rd(gt / "routes.txt")}
    svc = {r["service_id"] for r in rd(gt / "calendar.txt")}
    trips = rd(gt / "trips.txt")
    tid = {t["trip_id"] for t in trips}
    bad = collections.Counter()
    for t in trips:
        bad["trip→route"] += t["route_id"] not in routes
        bad["trip→service"] += t["service_id"] not in svc
    for r in rd(gt / "stop_times.txt"):
        bad["stop_time→trip"] += r["trip_id"] not in tid
        bad["stop_time→stop"] += r["stop_id"] not in stops
    for r in rd(gt / "calendar_dates.txt"):
        bad["calendar_dates→service"] += r["service_id"] not in svc
    for r in rd(gt / "transfers.txt"):
        bad["transfer→stop"] += (r["from_stop_id"] not in stops) + (r["to_stop_id"] not in stops)
    for r in rd(gt / "stops.txt"):
        bad["좌표 범위 밖"] += not (33 < float(r["stop_lat"]) < 39 and 124 < float(r["stop_lon"]) < 131)
    print(f"  형식: 없는 파일 {miss or 0} · 참조 끊김 {dict((k, v) for k, v in bad.items() if v) or 0}")
    return not miss and not any(bad.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO.parent / "exp" / "x1_gtfs" / "out"))
    ap.add_argument("--date", default="2026-10-14")
    ap.add_argument("--time", default="09:00")
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--to")
    a = ap.parse_args()
    gt = Path(a.out) / "gtfs"
    ok = fmt_check(gt)

    d = datetime.date.fromisoformat(a.date)
    ymd = d.strftime("%Y%m%d")
    wk = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][d.weekday()]
    act = {c["service_id"] for c in rd(gt / "calendar.txt") if c[wk] == "1" and c["start_date"] <= ymd <= c["end_date"]}
    for r in rd(gt / "calendar_dates.txt"):
        if r["date"] == ymd:
            act = act | {r["service_id"]} if r["exception_type"] == "1" else act - {r["service_id"]}
    stops = {r["stop_id"]: r for r in rd(gt / "stops.txt")}
    key = {(r["stop_desc"], r["stop_name"]): r["stop_id"] for r in stops.values()}
    trip = {t["trip_id"]: t for t in rd(gt / "trips.txt") if t["service_id"] in act}
    st = collections.defaultdict(list)
    for r in rd(gt / "stop_times.txt"):
        if r["trip_id"] in trip:
            st[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], sec(r["departure_time"])))
    conns = []
    for t, v in st.items():
        v.sort()
        conns += [(x[2], y[2], x[1], y[1], t) for x, y in zip(v, v[1:])]
    conns.sort()
    foot = collections.defaultdict(list)
    for r in rd(gt / "transfers.txt"):
        foot[r["from_stop_id"]].append((r["to_stop_id"], int(r["min_transfer_time"])))
    print(f"  {a.date}({wk}) 켜진 운행 {sorted(act)} · 편 {len(trip):,} · 연결 {len(conns):,} · 환승 쌍 {sum(len(v) for v in foot.values())}")

    def query(frm, to, t0):
        src, dst = key.get(tuple(frm.split(":"))), key.get(tuple(to.split(":")))
        if not src or not dst:
            print(f"  {frm} → {to}: 정류장을 못 찾음")
            return False
        best = {src: (t0, None)}
        for s2, w in foot.get(src, []):
            best[s2] = (t0 + w, ("walk", src, t0))
        on = {}                                            # 편 → 그 편에 처음 탄 연결
        for dep, arr, s, n, t in conns:
            if dep < t0:
                continue
            if t in on or (s in best and best[s][0] <= dep):
                on.setdefault(t, (s, dep))
                if arr < best.get(n, (10 ** 9,))[0]:
                    best[n] = (arr, ("ride", t, on[t][0], on[t][1]))
                    for s2, w in foot.get(n, []):
                        if arr + w < best.get(s2, (10 ** 9,))[0]:
                            best[s2] = (arr + w, ("walk", n, arr))
        if dst not in best:
            print(f"  {frm} → {to}: {a.time} 뒤 도착 없음")
            return False
        legs, cur = [], dst
        while best[cur][1] is not None:
            arr, how = best[cur]
            if how[0] == "ride":
                legs.append(f"{stops[how[2]]['stop_desc']} {stops[how[2]]['stop_name']} {hms(how[3])} → {stops[cur]['stop_name']} {hms(arr)}"
                            f" ({trip[how[1]]['trip_headsign']}행 {how[1]})")
                cur = how[2]
            else:
                legs.append(f"환승 도보 {stops[how[1]]['stop_desc']}→{stops[cur]['stop_desc']} {(arr - how[2]) // 60}분{(arr - how[2]) % 60}초")
                cur = how[1]
        print(f"  {frm} → {to}: 도착 {hms(best[dst][0])} · " + " | ".join(reversed(legs)))
        return True

    t0 = sec(a.time + ":00")
    qs = [(a.frm, a.to)] if a.frm and a.to else DEFAULT
    n_ok = sum(query(f, t, t0) for f, t in qs)
    print(f"  질의 {n_ok}/{len(qs)} 응답")
    sys.exit(0 if ok and n_ok == len(qs) else 1)


if __name__ == "__main__":
    main()
