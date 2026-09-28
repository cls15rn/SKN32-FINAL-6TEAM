# -*- coding: utf-8 -*-
"""자동차·택시 구간 — 도로망 그래프(GraphHopper) 위 시각별 소요 + 택시 요금. 21번 방(2026-09-20).

18번 방 `processed/mobility/graph/graph_time.py` 를 모듈로 옮긴 것이다(계산 규칙은 그대로).

  경로 선택은 정적(GraphHopper, maxspeed 주입 v2) · 소요는 동적(TOPIS 링크×요일형×시간대 프로파일).
  응답 edge 마다 세그먼트→링크→프로파일(그 시각) 로 소요를 다시 계산하고 진행하면서 시각을 넘긴다.
  ★ 경로는 저장하지 않는다 — 좌표열·edge 목록은 이 함수 밖으로 나가지 않는다. 나가는 것은
    거리·소요·커버 m/%·링크 요약(link_id, m)뿐이다(저장소 설계 3차 변경점 §2·§3).
  ★ 캐시 없음 — 같은 요청도 매번 라우터에 묻는다(시간표 상주 원칙과 같은 이유: 낡은 값을 조용히 내주지 않는다).

등급(18번 §3)
  edge 프로파일 있음 = topis(추정) · 간선(motorway~tertiary)인데 없음 = class(추정, 도로급 계수 — 약함) ·
  골목 = default(근거없음, 고정 속도). 구간 등급 = 골목 비율이 rules car.coverage.unknown_default_pct 를 넘으면
  근거없음, 아니면 추정. class/default 비율이 경계값을 넘으면 경고 MOB_W_CAR_SPEED_CLASS / MOB_W_CAR_SPEED_DEFAULT.
  3단 등급(확정/추정/근거없음)만 쓴다 — 「추정(약함)」은 등급이 아니라 경고로 드러낸다(스키마 grade enum 3종).

택시 요금 — rules taxi.fare 산식 그대로(15번 방). mobility_scripts/rules_check.py 의 taxi_fare 와 같은 식이고
  규칙 파일의 검산_예시 8건이 둘의 공통 정답이다(final_project_cs/tests/unit/travel/mobility/car_legs_v1.json 이 이 모듈로 다시 검산한다).
  병산: 프로파일 속도가 전환속도(15.72 km/h) 미만인 edge 는 시간요금(slow_s), 이상인 edge 는 거리요금(distance_m − slow_m).
  정차·신호 대기·호출료는 없다 → 요금은 **하한**.
  시계외 할증은 서울 경계 폴리곤이 없어 판정하지 않는다(out_of_city=None → 미적용, 하한 방향).
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import math
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

# 골목 정적 속도(근거없음 · 담당자 결정 2026-09-20) — build_topis_pbf.py 의 골목 주입값과 같아야 한다
DEFAULT_KMH = {"unclassified": 20, "residential": 15, "living_street": 6, "service": 10, "road": 15, "track": 10}
CLASS_MAP = {"motorway": "도시고속도로", "motorway_link": "도시고속도로", "trunk": "도시고속도로",
             "trunk_link": "도시고속도로", "primary": "주간선도로", "primary_link": "주간선도로",
             "secondary": "보조간선도로", "secondary_link": "보조간선도로",
             "tertiary": "기타도로", "tertiary_link": "기타도로"}
SOURCE_ID = "topis_link_profile_v1@2025-09~2026-05"
GRAPH_SOURCE_ID = "osm_road_graph@2026-09-18"


class RouterDown(Exception):
    """라우터에 닿지 못했다 — 소요·요금을 지어내지 않고 근거없음으로 낸다."""


def hav(a, b):
    R = 6371000.0
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(d))


def daytype_kr(d, holidays):
    """date → 프로파일 요일형. 토요일은 시간표(TAGO)와 달리 따로 있다. 공휴일은 config holidays(A10)가 정본 —
    `holidays` 패키지는 2026-07-17 제헌절을 공휴일로 잘못 넣는다(18번)."""
    if d.isoformat() in holidays or d.weekday() == 6:
        return "휴일"
    return "토요일" if d.weekday() == 5 else "평일"


# ── 그래프 자료 ────────────────────────────────────────────────────────────
class CarGraph:
    """프로파일·도로급 계수·세그먼트→링크 표·way 형상. 프로세스당 한 번 올린다(약 2초)."""

    def __init__(self, graph_dir, holidays=None):
        self.dir = Path(graph_dir)
        self.holidays = set(holidays or ())
        self.prof = {}
        pf = self.dir / "topis_link_profile_v1.jsonl"
        pf = pf if pf.exists() else self.dir / "topis_link_profile_v1.jsonl.gz"
        op = gzip.open if str(pf).endswith(".gz") else open
        with op(pf, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                self.prof[(r["link_id"], r["daytype"], r["hour"])] = r["mean_kmh"]
        cf = json.loads((self.dir / "topis_class_factor_v1.json").read_text(encoding="utf-8"))
        self.cf = cf["classes"]
        self.profile_source = cf.get("source")
        self.seg = defaultdict(dict)          # way → {(seg_idx, dir): link}
        with open(self.dir / "osm_way_seg_topis_link_v1.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.seg[int(r["osm_way_id"])][(int(r["seg_idx"]), r["dir"])] = r["link_id"]
        self.wayinfo = {}                     # way → (highway, pts)
        with open(self.dir / "osm_way_geom_v1.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.wayinfo[int(r["osm_way_id"])] = (
                    r["highway"], [tuple(map(float, p.split(","))) for p in r["pts"].split(" ")])
        # 요일형 달력(학습 기간) — config holidays 가 없는 날짜의 보조. 둘 다 있으면 config 가 이긴다.
        self.cal_hol = set()
        cal = self.dir / "daytype_calendar_v1.csv"
        if cal.exists():
            with open(cal, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r["daytype"] == "휴일" and r["is_holiday"] == "True":
                        self.cal_hol.add(f"{r['date'][:4]}-{r['date'][4:6]}-{r['date'][6:]}")

    @classmethod
    def load(cls, graph_dir=None, holidays=None):
        if graph_dir is None:
            from .paths import PROCESSED
            graph_dir = PROCESSED / "mobility" / "graph"
        p = Path(graph_dir)
        if not (p / "topis_class_factor_v1.json").exists():
            return None
        return cls(p, holidays)

    def daytype(self, t):
        return daytype_kr(t.date(), self.holidays | self.cal_hol)

    def _way_dir_and_segs(self, way, sub):
        hw, pts = self.wayinfo[way]
        cum = [0.0]
        for i in range(len(pts) - 1):
            cum.append(cum[-1] + hav(pts[i], pts[i + 1]))

        def pos(p):
            best = (1e18, 0, 0.0)
            for i in range(len(pts) - 1):
                (ax, ay), (bx, by) = pts[i], pts[i + 1]
                px, py = p
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = hav(p, (ax + t * dx, ay + t * dy))
                if d < best[0]:
                    best = (d, i, cum[i] + t * (cum[i + 1] - cum[i]))
            return best

        ps = [pos(p) for p in sub]
        adv = sum(ps[i + 1][2] - ps[i][2] for i in range(len(ps) - 1))
        return hw, adv >= 0, [q[1] for q in ps]

    def compute(self, route, depart, slow_threshold_kmh=None):
        """GraphHopper /route 응답 → 시각별 소요. 반환값에 좌표·edge 목록은 없다."""
        path = route["paths"][0]
        pts = path["points"]["coordinates"]
        det = path["details"]["osm_way_id"]
        rc_at = [None] * len(pts)
        for a, b, c in path["details"].get("road_class", []):
            for i in range(a, b):
                rc_at[i] = c
        t = depart
        cover = {"topis": 0.0, "class": 0.0, "default": 0.0}
        links = defaultdict(float)
        slow_s = slow_m = 0.0
        n_edges = 0
        for a, b, way in det:
            sub = pts[a:b + 1]
            if len(sub) < 2:
                continue
            if way in self.wayinfo:
                hw, fwd, segidx = self._way_dir_and_segs(way, sub)
                d = "fwd" if fwd else "bwd"
            else:
                hw, segidx, d = None, [None] * len(sub), "fwd"
            segtab = self.seg.get(way, {})
            for i in range(len(sub) - 1):
                L = hav(sub[i], sub[i + 1])
                dtp, h = self.daytype(t), t.hour
                hw_i = rc_at[a + i] or hw
                link = None
                if segidx[i] is not None:
                    for k in (0, 1, -1, 2, -2, 3, -3):        # 10 m 표본에 안 걸린 짧은 세그먼트는 이웃으로 보간
                        link = segtab.get((segidx[i] + k, d))
                        if link:
                            break
                v = self.prof.get((link, dtp, h)) if link else None
                if v:
                    grade = "topis"
                    links[link] += L
                elif hw_i in CLASS_MAP:
                    v = self.cf[CLASS_MAP[hw_i]]["mean_kmh"][dtp][h]
                    grade = "class"
                else:
                    v = DEFAULT_KMH.get(hw_i, 20)
                    grade = "default"
                sec = L / max(v, 3.0) * 3.6
                if slow_threshold_kmh is not None and v < slow_threshold_kmh:
                    slow_s += sec                       # 병산 — 이 edge 는 시간요금(거리요금 대신)
                    slow_m += L
                t = t + dt.timedelta(seconds=sec)
                cover[grade] += L
                n_edges += 1
        tot = sum(cover.values())
        top = sorted(links.items(), key=lambda kv: -kv[1])[:20]
        return {"depart": depart.isoformat(timespec="minutes"), "day_type": self.daytype(depart),
                "hour_start": depart.hour,
                "distance_m": round(tot, 1), "gh_distance_m": path.get("distance"),
                "gh_time_s": round(path["time"] / 1000) if path.get("time") else None,
                "topis_time_s": round((t - depart).total_seconds()),
                "arrive": t.isoformat(timespec="minutes"),
                "slow_s": round(slow_s), "slow_m": round(slow_m, 1), "n_edges": n_edges,
                "coverage_m": {k: round(v, 1) for k, v in cover.items()},
                "coverage_pct": {k: round(v / max(tot, 1) * 100, 1) for k, v in cover.items()},
                "links": [{"link_id": k, "m": round(m, 1)} for k, m in top],
                "n_links": len(links)}


# ── 라우터 ─────────────────────────────────────────────────────────────────
class GraphHopperClient:
    """노트북/집 PC 상주 GraphHopper 11.0. 캐시 없음."""

    def __init__(self, url="http://localhost:8989", timeout_s=30):
        self.url = url.rstrip("/")
        self.timeout = timeout_s
        self.calls = 0

    def route(self, s, e, profile="car", via=None):
        body = {"points": [list(s)] + [list(v) for v in (via or [])] + [list(e)], "profile": profile,
                "points_encoded": False, "details": ["osm_way_id", "road_class"],
                "instructions": False, "calc_points": True}
        req = urllib.request.Request(self.url + "/route", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                res = json.load(r)
        except urllib.error.HTTPError as ex:
            msg = ex.read().decode(errors="replace")[:200]
            raise RouterDown(f"GraphHopper {ex.code}: {msg}")
        except (urllib.error.URLError, OSError, ValueError) as ex:
            raise RouterDown(f"GraphHopper 에 닿지 못했다 ({self.url}): {ex}")
        if "paths" not in res:
            raise RouterDown(f"GraphHopper 응답에 paths 가 없다: {str(res)[:120]}")
        return res

    def info(self):
        try:
            with urllib.request.urlopen(self.url + "/info", timeout=5) as r:
                return json.load(r)
        except (urllib.error.URLError, OSError, ValueError):
            return None


class FixtureRouter:
    """시험용 — 합성 경로 파일에서 꺼낸다. 키 = 'lng,lat|lng,lat'(소수 4자리) 또는 케이스 id.
    실제 API 응답을 담는 자리가 아니다: 18번의 합성 시험(TOPIS 링크 체인)과 같은 종류의 픽스처만 둔다."""

    def __init__(self, path):
        self.doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self.routes = self.doc["routes"]
        self.calls = 0

    @staticmethod
    def key(s, e):
        return f"{s[0]:.4f},{s[1]:.4f}|{e[0]:.4f},{e[1]:.4f}"

    def route(self, s, e, profile="car", via=None):
        self.calls += 1
        r = self.routes.get(self.key(s, e))
        if r is None:
            raise RouterDown(f"픽스처에 경로가 없다: {self.key(s, e)}")
        if r.get("down"):
            raise RouterDown("픽스처가 라우터 다운을 흉내낸다")
        return r

    def info(self):
        return {"version": "fixture"}


class NoRouter:
    """라우터를 쓰지 않기로 한 실행(--gh-url none). 매번 RouterDown."""
    calls = 0

    def route(self, *a, **k):
        raise RouterDown("라우터 없이 실행 중(--gh-url none)")

    def info(self):
        return None


def make_router(spec):
    if not spec or spec == "none":
        return NoRouter()
    if spec.startswith("fixture:"):
        return FixtureRouter(spec[len("fixture:"):])
    return GraphHopperClient(spec)


# ── 택시 요금 (rules taxi.fare · 15번 방) ───────────────────────────────────
def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def taxi_rate(fare, kind, hhmm):
    """승차 시각 기준 심야 할증률 하나. 구간 [from, to), 자정 넘김(23:00~02:00) 처리."""
    t = _hm(hhmm) % (24 * 60)
    for seg in fare[kind]["night_surcharge"]["value"]:
        a, b = _hm(seg["from"]), _hm(seg["to"])
        if (a <= t < b) if a < b else (t >= a or t < b):
            return seg["rate"]
    return 0.0


def taxi_fare(fare, kind, dist_m, slow_s, hhmm, out_of_city=False):
    """규칙 taxi.fare.산식 그대로 — 전 구간 거리요금 + 저속 시간요금(단위 올림), 할증 후 총액은 step 최근접."""
    k = fare[kind]
    base = k["base_fare_won"]["value"]
    extra_m = max(0, dist_m - k["base_dist_m"]["value"])
    dist_won = math.ceil(extra_m / k["dist_unit_m"]["value"]) * k["dist_unit_won"]["value"]
    time_won = math.ceil((slow_s or 0) / k["time_unit_s"]["value"]) * k["time_unit_won"]["value"]
    meter = base + dist_won + time_won
    rate = taxi_rate(fare, kind, hhmm) + (k["out_of_city_rate"]["value"] if out_of_city else 0.0)
    rate = min(rate, k["max_combined_rate"]["value"])
    step = k["fare_step_won"]["value"]
    return int(math.floor(meter * (1 + rate) / step + 0.5) * step)


# ── 구간 판정 서비스 ───────────────────────────────────────────────────────
class CarService:
    """그래프 + 라우터 + 규칙. Verifier 가 자동차/택시 구간과 택시 대안에 쓴다."""

    def __init__(self, graph, router, rules):
        self.g, self.router, self.R = graph, router, rules
        self.C = rules["car"]
        self.F = rules["taxi"]["fare"]

    def in_airport_box(self, pt):
        b = self.C["공항_상자"]["value"]
        return b["lng"][0] <= pt[0] <= b["lng"][1] and b["lat"][0] <= pt[1] <= b["lat"][1]

    def leg(self, s, e, depart, taxi=False, kind="중형"):
        """s, e = (lng, lat) · depart = datetime. 반환: dict(경로 없음). 라우터가 없으면 RouterDown."""
        thr = self.F[kind]["time_speed_threshold_kmh"]["value"] if taxi else None
        route = self.router.route(s, e)
        r = self.g.compute(route, depart, slow_threshold_kmh=thr)
        del route                                   # 경로는 여기서 끝난다
        cov = r["coverage_pct"]
        warn = []
        if cov["class"] >= self.C["coverage"]["warn_class_pct"]["value"]:
            warn.append(("MOB_W_CAR_SPEED_CLASS", {"pct": cov["class"]}))
        if cov["default"] >= self.C["coverage"]["warn_default_pct"]["value"]:
            warn.append(("MOB_W_CAR_SPEED_DEFAULT", {"pct": cov["default"]}))
        grade = "근거없음" if cov["default"] > self.C["coverage"]["unknown_default_pct"]["value"] else "추정"
        out = {"v": 1, "kind": "car_leg", "mode": "taxi" if taxi else "car", "grade": grade,
               "distance_m": r["distance_m"], "topis_time_s": r["topis_time_s"],
               "gh_time_s": r["gh_time_s"],
               "depart": r["depart"], "arrive": r["arrive"], "day_type": r["day_type"],
               "hour_start": r["hour_start"],
               "coverage_m": r["coverage_m"], "coverage_pct": cov, "n_edges": r["n_edges"],
               "links": r["links"], "n_links": r["n_links"],
               "source_id": [SOURCE_ID, GRAPH_SOURCE_ID], "warn": warn}
        if taxi:
            hhmm = f"{depart.hour:02d}:{depart.minute:02d}"
            toll = 0
            toll_basis = None
            if self.in_airport_box(s) or self.in_airport_box(e):
                toll = self.F["공항"]["통행료_인천공항고속도로_won"]["value"]
                toll_basis = "공항 방면 — 인천공항고속도로 통행료 상한(어느 다리를 타는지는 미판정)"
            # 병산: 전환속도 미만 edge 는 시간요금, 이상 edge 는 거리요금(15번 산식의 '전 구간 거리 + 저속 시간' 근사보다 한 발 정확).
            fare = taxi_fare(self.F, kind, r["distance_m"] - r["slow_m"], r["slow_s"], hhmm, out_of_city=False)
            out.update({"fare_kind": kind, "fare_won": fare + toll, "meter_won": fare, "toll_won": toll,
                        "toll_basis": toll_basis, "slow_s": r["slow_s"], "slow_m": r["slow_m"],
                        "night_rate": taxi_rate(self.F, kind, hhmm), "out_of_city": None,
                        "fare_basis": "호출료·정차·시계외 미포함 하한"})
        return out
