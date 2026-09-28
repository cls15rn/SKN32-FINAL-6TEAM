# modules/mobility/bus.py — 시내버스 노선·정류장 조회 계층
# 소스: processed/mobility/bus_route_v1.jsonl (18노선) · bus_stops_v1.jsonl (1,172행 · 고유 906)
# 규칙: rules.bus
#
# ★ 버스는 지하철과 판정 구조가 다르다.
#   지하철: 열차 단위 시간표 → "그 시각에 출발하는 차가 있는가" 를 직접 본다.
#   버스:   노선 단위(첫차·막차·배차) → "운행 구간 안인가 + 배차만큼 기다리는가" 로 본다.
#   그래서 대기가 [확정]이 아니라 [추정]이고, `term` 이 노선당 값 하나라 시간대별 차이를 못 담는다.
#
# ★ 승차 소요 = 구간 거리 합 ÷ 표정속도. 2026-09-10 저녁에 근거를 확보해 해제됐다.
#   거리는 `sect_dist_m` 누적(확정), 속도는 rules.bus.표정속도(추정), 환산한 소요는 추정이다.
#   값_우선순위: **노선별 실측 > 노선유형별 통계 > 근거없음**.
import json, collections
from dataclasses import dataclass, field
from pathlib import Path

from .timeutil import to_min


@dataclass
class Route:
    route_id: str
    route_nm: str
    route_type: str
    route_type_nm: str
    term_min: int
    first_min: int
    last_min: int
    crosses_midnight: bool
    raw: dict = field(default_factory=dict)


class BusRoutes:
    def __init__(self, routes, stops):
        self.by_id, self.by_nm = {}, {}
        for r in routes:
            R = Route(r["route_id"], r["route_nm"], r.get("route_type"), r.get("route_type_nm"),
                      r.get("term_min"), to_min(r.get("first_time")), to_min(r.get("last_time")),
                      bool(r.get("crosses_midnight")), r)
            self.by_id[R.route_id] = R
            self.by_nm.setdefault(R.route_nm, R)
        self.stops = collections.defaultdict(list)          # route_id → [row] (seq 순)
        for s in stops:
            self.stops[s["route_id"]].append(s)
        for v in self.stops.values():
            v.sort(key=lambda s: s["seq"])
        self.fetched_at = routes[0].get("fetched_at") if routes else None
        self.source_id = routes[0].get("source_id") if routes else "seoul_bus_route"

    @classmethod
    def load(cls, route_path=None, stop_path=None):
        if route_path is None or stop_path is None:
            from .paths import PROCESSED
            base = PROCESSED / "mobility"
            route_path = route_path or base / "bus_route_v1.jsonl"
            stop_path = stop_path or base / "bus_stops_v1.jsonl"
        rp, sp = Path(route_path), Path(stop_path)
        if not rp.exists() or not sp.exists():
            return None
        rd = [json.loads(l) for l in rp.read_text(encoding="utf-8").splitlines() if l.strip()]
        sd = [json.loads(l) for l in sp.read_text(encoding="utf-8").splitlines() if l.strip()]
        return cls(rd, sd)

    def route(self, name):
        return self.by_nm.get(str(name)) or self.by_id.get(str(name))

    def find_stops(self, route_id, name):
        """정류장명으로 그 노선의 행을 찾는다. 정확일치 우선, 없으면 부분일치.

        같은 이름이 여러 seq 에 나오는 노선이 있다(2016 의 성수사거리는 31·104).
        왕복 노선이라 방향이 다르므로 **하나로 좁히지 않고 전부 돌려준다** — 방향 판정이 고른다.
        """
        rows = self.stops.get(route_id, [])
        exact = [s for s in rows if s["station_nm"] == name]
        if exact:
            return exact
        return [s for s in rows if name in s["station_nm"]]

    def distance_m(self, route_id, a_seq, b_seq):
        """a → b 구간 거리(m). `sect_dist_m` 은 **직전 정류장에서 이 정류장까지**의 거리라
        seq 가 a 보다 크고 b 이하인 행만 더한다(첫 행은 0 이다)."""
        tot = 0
        for s in self.stops.get(route_id, []):
            if a_seq < s["seq"] <= b_seq:
                d = s.get("sect_dist_m")
                if d is None:
                    return None
                tot += d
        return tot

    @staticmethod
    def _m(lat1, lng1, lat2, lng2):
        import math
        dy = (lat2 - lat1) * 111_320
        dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
        return math.hypot(dx, dy)

    def shorter_pair(self, route_id, a, b, within_m):
        """요청한 두 정류장 근처의 **다른 짝**으로 훨씬 짧은 구간이 되는지.

        왕복 노선은 같은 정류장이 두 번 나온다. 2016 은 성수역1번출구(32)→건대입구역사거리(107)가
        75정거장인데 길 건너 짝(성수역4번출구 103 → 건대입구역6번출구 105)은 2정거장이다.
        판정을 바꾸지는 않는다 — 요청한 정류장에서의 판정은 맞다. 더 짧은 길이 있다고 알릴 뿐이다.
        """
        rows = self.stops.get(route_id, [])
        near = lambda s, t: (s.get("lat") and t.get("lat")
                             and self._m(s["lat"], s["lng"], t["lat"], t["lng"]) <= within_m)
        A = [s for s in rows if near(s, a)]
        B = [s for s in rows if near(s, b)]
        best = None
        for x in A:
            for y in B:
                if x["seq"] < y["seq"] and (best is None or y["seq"] - x["seq"] < best[2]):
                    best = (x, y, y["seq"] - x["seq"])
        return best

    def stops_near(self, lat, lng, within_m):
        """좌표 근처의 정류장 행. (거리, row) 로 가까운 순."""
        from .geo import meters
        out = []
        for rows in self.stops.values():
            for s in rows:
                if s.get("lat") is None:
                    continue
                d = meters(lat, lng, s["lat"], s["lng"])
                if d <= within_m:
                    out.append((d, s))
        out.sort(key=lambda x: x[0])
        return out

    def routes_between(self, a_lat, a_lng, b_lat, b_lng, within_m):
        """두 좌표를 **한 노선으로** 잇는 후보. (route, 타는 정류장, 내리는 정류장, 정거장 수, 도보 m).

        ★ seq 증가만 보면 안 된다 — 목적지에 가지 않는 노선이 잡힌다(시연 v0.1 의 성동10).
          여기서는 **양쪽 정류장이 목적지 반경 안에 있어야** 하므로 그 사례가 구조적으로 걸러진다.
        """
        A = self.stops_near(a_lat, a_lng, within_m)
        B = self.stops_near(b_lat, b_lng, within_m)
        bybus = {}
        for da, x in A:
            for db, y in B:
                if x["route_id"] != y["route_id"] or x["seq"] >= y["seq"]:
                    continue
                r = self.by_id.get(x["route_id"])
                if r is None:
                    continue
                span = y["seq"] - x["seq"]
                cur = bybus.get(r.route_nm)
                if cur is None or span < cur[3]:
                    bybus[r.route_nm] = (r, x, y, span, round(da), round(db))
        return sorted(bybus.values(), key=lambda t: t[3])

    def segment(self, route_id, from_nm, to_nm):
        """from → to 로 **순서가 증가하는** 구간을 찾는다. 없으면 None.

        ★ seq 증가만 보면 안 된다. 목적지에 실제로 가지 않는 노선이 후보로 잡힌 사례가 있다
          (시연 시나리오 v0.1 — 성동10 이 성수역에 가지 않는데 잡혔다).
          여기서는 **양쪽 정류장명이 그 노선에 다 있어야** 하므로 그 사례는 걸러진다.
        """
        A, B = self.find_stops(route_id, from_nm), self.find_stops(route_id, to_nm)
        if not A or not B:
            return None
        best = None
        for a in A:
            for b in B:
                if a["seq"] < b["seq"]:
                    span = b["seq"] - a["seq"]
                    if best is None or span < best[2]:
                        best = (a, b, span)
        return best
