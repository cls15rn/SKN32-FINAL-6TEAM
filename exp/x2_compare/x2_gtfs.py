# -*- coding: utf-8 -*-
"""X2 — GTFS 를 MOTIS 없이 직접 읽어 「고정 경로」를 초 단위로 통과시킨다 (GPT 대조 2026-10-05 · 1·2·7번).

쓰임 둘
  · MOTIS 가 같은 경로를 못 준 것이 「그 운행일에 편이 없다」인지 「응답 후보에 안 나왔다」인지 가른다
  · MOTIS(분 단위 응답)와 같은 GTFS 를 초 단위로 읽은 값이 같은지 본다
이것은 **변환 GTFS 와 MOTIS 응답의 정합성** 확인이다 — 원천 열차 복원(X1 잇기)의 정확도 증거가 아니다.
한 편(trip) 안에서만 탄다(역에서 편을 갈아 잇지 않는다 — 성수·응암 이어 타기는 여기서 「없음」으로 나온다).
"""
import bisect, collections, csv, datetime as dt, io, zipfile


def sec(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


class Gtfs:
    def __init__(self, zip_path):
        z = zipfile.ZipFile(zip_path)
        rd = lambda n: csv.DictReader(io.StringIO(z.read(n).decode("utf-8-sig")))
        self.svc = {r["trip_id"]: r["service_id"] for r in rd("trips.txt")}
        self.cal = list(rd("calendar.txt"))
        self.exc = collections.defaultdict(dict)
        for r in rd("calendar_dates.txt"):
            self.exc[r["date"]][r["service_id"]] = r["exception_type"]
        self.trip = collections.defaultdict(list)          # trip → [(seq, stop, sec)]
        for r in rd("stop_times.txt"):
            self.trip[r["trip_id"]].append((int(r["stop_sequence"]), "x1_" + r["stop_id"], sec(r["departure_time"])))
        self.at = collections.defaultdict(list)            # (service, stop) → [(sec, trip, idx)]
        for t, s in self.trip.items():
            s.sort()
            for i, (_, st, x) in enumerate(s):
                self.at[(self.svc[t], st)].append((x, t, i))
        for v in self.at.values():
            v.sort()

    def active(self, date):
        d = dt.date.fromisoformat(date)
        key = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][d.weekday()]
        ymd = d.strftime("%Y%m%d")
        s = {r["service_id"] for r in self.cal if r[key] == "1" and r["start_date"] <= ymd <= r["end_date"]}
        for sid, e in self.exc.get(ymd, {}).items():
            (s.add if e == "1" else s.discard)(sid)
        return s

    def leg(self, date, frm, to, t_sec, arrive_by=False):
        """한 편으로 frm→to. 앞으로: t_sec 이후 출발 중 도착이 가장 이른 것(동률이면 늦게 떠나는 것).
        뒤로: t_sec 까지 도착 중 출발이 가장 늦은 것. 돌려줌 (출발 초, 도착 초) 또는 None. 그 운행일의 편만 본다."""
        best = None
        for sid in self.active(date):
            rows = self.at.get((sid, frm), [])
            lo = 0 if arrive_by else bisect.bisect_left(rows, (t_sec, "", -1))
            for x, t, i in rows[lo:]:
                if arrive_by and x > t_sec: break
                for _, st, y in self.trip[t][i + 1:]:
                    if st == to:
                        if arrive_by:
                            if y <= t_sec and (best is None or x > best[0]): best = (x, y)
                        elif best is None or (y, -x) < (best[1], -best[0]): best = (x, y)
                        break
        return best

    def chain(self, date, stops, t_min, xfer_min, arrive_by=False):
        """구간열 통과. xfer_min[i] = i번째 환승(분). 돌려줌 {dep, arr}(초) 또는 None."""
        n = len(stops)
        t, first, last = t_min * 60, None, None
        for i in (range(n - 1, -1, -1) if arrive_by else range(n)):
            h = self.leg(date, stops[i][0], stops[i][1], t, arrive_by)
            if not h: return None
            if arrive_by:
                last = h[1] if last is None else last; first = h[0]
                if i > 0: t = h[0] - xfer_min[i - 1] * 60
            else:
                first = h[0] if first is None else first; last = h[1]
                if i < n - 1: t = h[1] + xfer_min[i] * 60
        return {"dep": first, "arr": last}
