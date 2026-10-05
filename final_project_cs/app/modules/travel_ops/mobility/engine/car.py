# -*- coding: utf-8 -*-
"""자동차·택시 구간 — 차도 그래프 경로 위 시각별 소요 + 택시 요금. 21번 방(2026-09-20).

18번 방 `processed/mobility/graph/graph_time.py` 를 모듈로 옮긴 것이다(계산 규칙은 그대로).

  경로 선택은 정적(파이썬 길찾기 graph_router · 도로급 고정 속도) · 소요는 동적(TOPIS 링크×요일형×시간대 프로파일).
  ☆99(2026-10-04) 경로 서버(GraphHopper) 호출을 지웠다 — 경로는 저장소 안 도로 그래프 파일로만 낸다.
  ☆101(2026-10-05 · 합치기) 길찾기는 팀장 `graph_router.GraphRouter` 하나다(우리 `road_router.py` 는 내렸다 — 같은 그래프를
    두 번 읽지 않는다). 서버 클라이언트(`GraphHopperClient`)와 `make_router` 의 서버 가지는 팀장 판에도 남기지 않는다(본인 10/5).
    응답 모양(`paths[0].points.coordinates` · `details.osm_way_id` · `details.road_class`)과 출력 칸 이름
    (`gh_time_s` · `gh_distance_m` — 라우터가 준 정적 값 · 파이썬 라우터는 시간을 안 줘 None)은 스키마·골든이 걸려 그대로 둔다.
  응답 edge 마다 세그먼트→링크→프로파일(그 시각) 로 소요를 다시 계산하고 진행하면서 시각을 넘긴다.
  ★ 경로는 저장하지 않는다 — 좌표열·edge 목록은 이 함수 밖으로 나가지 않는다. 나가는 것은
    거리·소요·커버 m/%·링크 요약(link_id, m)뿐이다(저장소 설계 3차 변경점 §2·§3).
  ★ 경로 캐시 없음 — 같은 요청도 매번 다시 계산한다(시간표 상주 원칙과 같은 이유: 낡은 값을 조용히 내주지 않는다).

등급(18번 §3)
  edge 프로파일 있음 = topis(추정) · 간선(motorway~tertiary)인데 없음 = class(추정, 도로급 계수 — 약함) ·
  골목 = default(근거없음, 고정 속도). 구간 등급 = 골목 비율이 rules car.coverage.unknown_default_pct 를 넘으면
  근거없음, 아니면 추정. class/default 비율이 경계값을 넘으면 경고 MOB_W_CAR_SPEED_CLASS / MOB_W_CAR_SPEED_DEFAULT.
  3단 등급(확정/추정/근거없음)만 쓴다 — 「추정(약함)」은 등급이 아니라 경고로 드러낸다(스키마 grade enum 3종).

택시 요금 — rules taxi.fare 산식 그대로(15번 방). rules_check.py(저장소 밖 점검 · 82)의 taxi_fare 와 같은 식이고
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
from collections import defaultdict
from pathlib import Path

# 골목 정적 속도(근거없음 · 담당자 결정 2026-09-20) — build_topis_pbf.py 의 골목 주입값과 같아야 한다
DEFAULT_KMH = {"unclassified": 20, "residential": 15, "living_street": 6, "service": 10, "road": 15, "track": 10}
CLASS_MAP = {"motorway": "도시고속도로", "motorway_link": "도시고속도로", "trunk": "도시고속도로",
             "trunk_link": "도시고속도로", "primary": "주간선도로", "primary_link": "주간선도로",
             "secondary": "보조간선도로", "secondary_link": "보조간선도로",
             "tertiary": "기타도로", "tertiary_link": "기타도로"}
SOURCE_ID = "topis_link_profile_v1@2025-09~2026-05"
GRAPH_SOURCE_ID = "osm_road_graph@2026-09-18"   # 라우터가 제 출처(source_id)를 말하지 않을 때의 표기(합성 경로 픽스처) — 골든이 이 값을 잠근다


class RouterDown(Exception):
    """라우터에 닿지 못했다 — 소요·요금을 지어내지 않고 근거없음으로 낸다.

    code(77-2): 왜 못 냈는지의 갈래 — `router_down`(기본 · 라우터 없음) · `out_of_area`(도로 그래프 범위 밖) ·
      `no_path`(이을 길 없음) · `depart_unconfirmed`(경로는 있는데 출발 시각을 못 정함). 수단별 후보의 택시 칸이 이유를 가르는 데 쓴다.
      ☆101 — 팀장 길찾기(graph_router)는 갈래를 문장 머리(「out_of_area: …」·「no_path: …」)로 낸다. 코드를 따로 안 주면 그
        머리를 읽어 채운다(길찾기 파일은 고치지 않는다). 우리 옛 라우터의 `no_snap`(200 m 안 차도 없음)은 없어졌다 — 팀장
        길찾기는 1,200 m 까지 붙이고 그 밖은 `out_of_area` 다."""

    _HEADS = ("out_of_area", "no_path")

    def __init__(self, msg="", code="router_down"):
        super().__init__(msg)
        if code == "router_down":
            head = str(msg).split(":", 1)[0].strip()
            if head in self._HEADS:
                code = head
        self.code = code


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
        self.p10 = {}                         # 77-2: 느린 쪽 10% 속도(km/h) — 택시 칸 최악 소요(worst)에만 쓴다
        pf = self.dir / "topis_link_profile_v1.jsonl"
        pf = pf if pf.exists() else self.dir / "topis_link_profile_v1.jsonl.gz"
        op = gzip.open if str(pf).endswith(".gz") else open
        with op(pf, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                self.prof[(r["link_id"], r["daytype"], r["hour"])] = r["mean_kmh"]
                if r.get("p10"):
                    self.p10[(r["link_id"], r["daytype"], r["hour"])] = r["p10"]
        cf = json.loads((self.dir / "topis_class_factor_v1.json").read_text(encoding="utf-8"))
        self.cf = cf["classes"]
        self.profile_source = cf.get("source")
        self.seg = defaultdict(dict)          # way → {(seg_idx, dir): link}
        with open(self.dir / "osm_way_seg_topis_link_v1.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.seg[int(r["osm_way_id"])][(int(r["seg_idx"]), r["dir"])] = r["link_id"]
        self.wayinfo = {}                     # way → (highway, pts)
        self.wayname = {}                     # way → OSM 도로명(77-2 · 택시 칸 uses `도로:<이름>` · 간선 way 만 있다)
        with open(self.dir / "osm_way_geom_v1.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.wayinfo[int(r["osm_way_id"])] = (
                    r["highway"], [tuple(map(float, p.split(","))) for p in r["pts"].split(" ")])
                if (r.get("name") or "").strip():
                    self.wayname[int(r["osm_way_id"])] = r["name"].strip()
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

    def static_kmh(self, way, seg_idx, d, hw):
        """경로 **선택**용 정적 속도(km/h) — 18번 방 maxspeed 주입 v2(build_topis_pbf.py)와 같은 잣대.
        TOPIS 링크 = 평일 07~20시 평균 · 링크 없는 간선급 = 도로급 평일 07~20시 평균 · 골목 = DEFAULT_KMH.
        77 파이썬 라우터의 시간 가중에 쓴다. 소요(compute)는 여전히 그 시각의 프로파일로 따로 낸다."""
        segtab = self.seg.get(way)
        link = None
        if segtab:
            for k in (0, 1, -1, 2, -2, 3, -3):          # compute 와 같은 이웃 보간
                link = segtab.get((seg_idx + k, d))
                if link:
                    break
        cache = self.__dict__.setdefault("_static_cache", {})
        if link:
            v = cache.get(link)
            if v is None:
                vals = [self.prof[(link, "평일", h)] for h in range(7, 21) if (link, "평일", h) in self.prof]
                v = cache[link] = (sum(vals) / len(vals)) if vals else 0.0
            if v:
                return v
        if hw in CLASS_MAP:
            key = ("class", CLASS_MAP[hw])
            v = cache.get(key)
            if v is None:
                arr = self.cf[CLASS_MAP[hw]]["mean_kmh"]["평일"]
                v = cache[key] = sum(arr[h] for h in range(7, 21)) / 14
            return v
        return DEFAULT_KMH.get(hw, 20)

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

    def prepare(self, route):
        """라우터 응답(`/route` 모양) → 세그먼트 열(길이 m · TOPIS 링크 · 도로급) + 도로명별 길이. 출발 시각과 무관한 부분만.
        77-2: 도착 목표에 맞춘 출발 탐색(arrive_by)이 같은 경로를 여러 시각으로 다시 셈하므로 형상 맞추기를 한 번만 한다.
        ★좌표는 여기서 길이·링크로 바뀌고 버려진다 — 돌려주는 값에 좌표·way 열은 없다."""
        path = route["paths"][0]
        pts = path["points"]["coordinates"]
        det = path["details"]["osm_way_id"]
        rc_at = [None] * len(pts)
        for a, b, c in path["details"].get("road_class", []):
            for i in range(a, b):
                rc_at[i] = c
        segs = []
        roads = defaultdict(float)
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
                link = None
                if segidx[i] is not None:
                    for k in (0, 1, -1, 2, -2, 3, -3):        # 10 m 표본에 안 걸린 짧은 세그먼트는 이웃으로 보간
                        link = segtab.get((segidx[i] + k, d))
                        if link:
                            break
                segs.append((L, link, rc_at[a + i] or hw))
            nm = getattr(self, "wayname", {}).get(way)
            if nm:
                roads[nm] += sum(hav(sub[i], sub[i + 1]) for i in range(len(sub) - 1))
        return {"segs": segs, "gh_distance_m": path.get("distance"),
                "gh_time_s": round(path["time"] / 1000) if path.get("time") else None,
                "roads": [{"name": k, "m": round(m, 1)} for k, m in sorted(roads.items(), key=lambda kv: -kv[1])[:8]]}

    def compute(self, route, depart, slow_threshold_kmh=None, worst=False):
        """라우터 응답(`/route` 모양) → 시각별 소요. 반환값에 좌표·edge 목록은 없다. (= prepare + run)"""
        return self.run(self.prepare(route), depart, slow_threshold_kmh=slow_threshold_kmh, worst=worst)

    def run(self, prep, depart, slow_threshold_kmh=None, worst=False):
        """prepare() 결과 + 출발 시각 → 시각별 소요. 진행하면서 시각을 넘긴다(시간대 칸이 바뀐다).

        worst(77-2): True 면 TOPIS 링크 속도를 평균 대신 **느린 쪽 10%(p10)** 로 셈한다 — 「느린 쪽 추정」이지 보장된
          상한이 아니다(링크마다의 p10 을 한꺼번에 적용한 합 · 경로 전체의 분위수가 아니다). p10 이 없는 링크와 링크 없는
          간선급·골목은 평균·고정값 그대로다(더 느린 값의 근거가 없다) — 적용된 길이 비율을 `p10_pct` 로 같이 낸다.
          판정 경로(leg)는 쓰지 않는다."""
        t = depart
        cover = {"topis": 0.0, "class": 0.0, "default": 0.0}
        links = defaultdict(float)
        prof = getattr(self, "p10", None) if worst else None
        slow_s = slow_m = p10_m = 0.0
        n_edges = 0
        for L, link, hw_i in prep["segs"]:
            dtp, h = self.daytype(t), t.hour
            v = self.prof.get((link, dtp, h)) if link else None
            if v:
                grade = "topis"
                links[link] += L
                if prof:
                    v10 = prof.get((link, dtp, h))
                    if v10:
                        v = v10
                        p10_m += L
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
                "distance_m": round(tot, 1), "gh_distance_m": prep["gh_distance_m"],
                "gh_time_s": prep["gh_time_s"],
                "topis_time_s": round((t - depart).total_seconds()),
                "arrive": t.isoformat(timespec="minutes"),
                "slow_s": round(slow_s), "slow_m": round(slow_m, 1), "n_edges": n_edges,
                "coverage_m": {k: round(v, 1) for k, v in cover.items()},
                "coverage_pct": {k: round(v / max(tot, 1) * 100, 1) for k, v in cover.items()},
                "links": [{"link_id": k, "m": round(m, 1)} for k, m in top],
                "n_links": len(links),
                "p10_pct": round(p10_m / max(tot, 1) * 100, 1) if worst else None,
                "roads": prep["roads"]}

    def floor_s(self, prep, day_types):
        """이 경로가 그 요일형들에서 **어느 시각에 떠나도 이보다 빨리는 못 가는** 초 — 세그먼트마다 run() 이 쓸 수 있는
        속도(24시간 × 요일형의 링크 평균 · 도로급 평균 · 골목 고정값) 중 가장 빠른 것으로 셈한다.
        arrive_by 가 탐색을 시작할 가장 늦은 출발을 정한다 — 이보다 늦게 떠나면 어떤 시간대 조합으로도 못 닿는다."""
        tot = 0.0
        for L, link, hw_i in prep["segs"]:
            cands = []
            for dtp in day_types:
                if link:
                    cands += [v for h in range(24) if (v := self.prof.get((link, dtp, h)))]
                if hw_i in CLASS_MAP:
                    cands += list(self.cf[CLASS_MAP[hw_i]]["mean_kmh"][dtp])
            if hw_i not in CLASS_MAP:
                cands.append(DEFAULT_KMH.get(hw_i, 20))
            tot += L / max(max(cands), 3.0) * 3.6
        return tot


# ── 라우터 ────────────────────────────────────────────────────────────────
#   실제 경로 계산은 graph_router.GraphRouter(저장소 안 도로 그래프 파일 · 서버 없음) 하나다. ☆99(2026-10-04) 경로 서버
#   클라이언트와 「파이썬 라우터 → 서버」 둘째 단을 지웠다 — 라우터가 못 내면 바로 근거없음(RouterDown)이다.
#   시험·명령줄 대역(FixtureRouter · NoRouter)은 car_fixtures.py(팀장 #59) — 옛 이름은 파일 끝 __getattr__ 가 이어 준다.
def make_router(spec):
    """명령줄·시험용 — "none"/빈 값 = 라우터 없음 · "fixture:<파일>" = 합성 경로 대역 · "local" = 자료 폴더의 도로 그래프 ·
    "local:<폴더>" = 그 폴더. ☆101 서버 주소(http…)는 받지 않는다 — 경로 서버를 부르는 코드가 없다(99 · 본인 10/5)."""
    if not spec or spec == "none":
        from .car_fixtures import NoRouter
        return NoRouter()
    if spec.startswith("fixture:"):
        from .car_fixtures import FixtureRouter
        return FixtureRouter(spec[len("fixture:"):])
    if spec == "local" or spec.startswith("local:"):
        from .graph_router import GraphRouter
        return GraphRouter(spec[len("local:"):]) if spec.startswith("local:") else GraphRouter.default()
    raise ValueError(f"라우터 지정을 모른다: {spec!r} — none · fixture:<파일> · local · local:<폴더> 만 받는다(경로 서버 주소는 쓰지 않는다)")


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
    """그래프 + 라우터 + 규칙. Verifier 가 자동차/택시 구간과 택시 대안에 쓴다.

    ☆77(2026-09-30) · 99(2026-10-04) · 101(2026-10-05) 경로는 **`router`(graph_router.GraphRouter · 시험은 FixtureRouter) 하나**에
      묻는다. 못 내면 RouterDown(근거없음) — 다른 서버로 넘기지 않는다. 소요는 `CarGraph` 가 낸다 — 판정·요금·등급 규칙은
      그대로다. 파이썬 길찾기는 회전 제약이 없어 등급은 추정을 넘지 않는다(원래 이 구간 등급은 추정/근거없음 둘뿐). 어느
      그래프였는지는 `source_id` 둘째 칸으로 남긴다(라우터가 말하는 값 · 없으면 GRAPH_SOURCE_ID).
    ☆101 — 인자 순서는 팀장 판(`CarService(graph, router, rules)`)으로 맞췄다. router 가 None 이면 근거없음.
    """

    def __init__(self, graph, router, rules):
        self.g, self.router, self.R = graph, router, rules
        self.C = rules["car"]
        self.F = rules["taxi"]["fare"]

    def _route(self, s, e):
        """(응답, 그래프 출처 id). 라우터가 없거나 못 내면 RouterDown."""
        if self.router is None:
            raise RouterDown("도로 경로 계산 없이 실행 중(도로 그래프 없음 · --road-graph none)")
        return self.router.route(s, e), getattr(self.router, "source_id", None) or GRAPH_SOURCE_ID

    def in_airport_box(self, pt):
        b = self.C["공항_상자"]["value"]
        return b["lng"][0] <= pt[0] <= b["lng"][1] and b["lat"][0] <= pt[1] <= b["lat"][1]

    def leg(self, s, e, depart, taxi=False, kind="중형"):
        """s, e = (lng, lat) · depart = datetime. 반환: dict(경로 없음). 라우터가 없으면 RouterDown."""
        route, graph_src = self._route(s, e)
        prep = self.g.prepare(route)
        del route                                   # 경로는 여기서 끝난다
        out, _r = self._summary(prep, graph_src, s, e, depart, taxi, kind)
        return out

    SCAN_MAX_MIN = 240          # arrive_by 가 뒤로 훑는 분 수 상한 — 넘으면 「출발 시각 미확인」(경로 없음이 아니다)

    def arrive_by(self, s, e, arrive, taxi=True, kind="중형"):
        """(77-2 · 수단별 후보의 택시 칸) `arrive`(datetime)까지 닿는 **가장 늦은 출발**을 분 단위로 찾는다.

        경로는 한 번만 묻고(정적 선택) 형상 맞추기도 한 번(prepare) — 출발 시각만 바꿔 소요를 다시 셈한다(run).
        성립 = 출발 + 느린 쪽 소요(분 올림) ≤ arrive. 느린 쪽 소요 = max(p10 속도로 셈한 값, 평균으로 셈한 값) + 차도까지 걷는 시간.
        탐색: 어떤 시각에도 그보다 빨리 못 가는 하한(floor_s)으로 「가장 늦을 수 있는 출발」을 잡고, 거기서 **1분씩 앞으로 당기며
          처음 성립하는 분**을 고른다 → 시간대 경계에서 소요가 뛰어도 그 뒤의 성립 구간을 놓치지 않는다(GPT 77-2 #1).
          SCAN_MAX_MIN 분 안에 못 찾으면 RouterDown(code="depart_unconfirmed") — 경로는 있는데 출발을 못 정한 것(#2).
        차도까지 걷는 시간: 장소 좌표 ↔ 스냅점 이격(양끝 합) × 우회계수 ÷ 보행속도(정류장↔역 환승과 같은 규칙 값) — 택시가 문 앞에
          서지 못하는 만큼을 소요에 넣는다(#3). 합성 픽스처 응답에는 이격이 없어 0. 도로 소요·요금은 **출발지 쪽 걷는 시간 뒤**의 시각으로
          셈한다(GPT 97 #4 — 걷는 사이 시간대가 바뀌는 경우).
        호출·승차 대기는 넣지 않는다(근거 없음 — rules car.택시_대기).
        반환 = leg() 와 같은 dict + depart_dt · worst_time_s(걷기 포함) · access_m · access_s · p10_pct · roads."""
        route, graph_src = self._route(s, e)
        prep = self.g.prepare(route)
        snap = route["paths"][0].get("snap_m") or []
        del route                                   # 경로(좌표)는 여기서 끝난다
        access_m = float(snap[0] + snap[-1]) if len(snap) >= 2 else 0.0
        per_m = (self.R["transfer"]["stop_station_walk"]["detour_factor"]["value"]
                 / self.R["measured_baseline"]["kakao_walk_speed_mps"]["value"])
        access_s = access_m * per_m if access_m else 0.0
        # (GPT 97 #4) 차는 **장소 출발 + 출발지에서 차도까지 걷는 시간**에 달리기 시작한다 — 도로 소요·요금의 기준 시각을 그만큼
        #   옮긴다(앞 판은 장소 출발 시각의 속도로 셈해, 걷는 사이 시간대가 바뀌면 느린 시간대를 놓쳤다).
        lead = dt.timedelta(seconds=math.ceil(float(snap[0]) * per_m)) if len(snap) >= 2 and snap[0] else dt.timedelta(0)
        day_types = {self.g.daytype(arrive), self.g.daytype(arrive - dt.timedelta(days=1))}
        floor = self.g.floor_s(prep, day_types) + access_s
        dep = (arrive - dt.timedelta(minutes=math.ceil(floor / 60))).replace(second=0, microsecond=0)
        for _ in range(self.SCAN_MAX_MIN + 1):
            w = self.g.run(prep, dep + lead, worst=True)
            m = self.g.run(prep, dep + lead)
            need = max(w["topis_time_s"], m["topis_time_s"]) + access_s
            if dep + dt.timedelta(minutes=math.ceil(need / 60)) <= arrive:
                break
            dep -= dt.timedelta(minutes=1)
        else:
            raise RouterDown(f"도착 목표에 맞는 출발 시각을 {self.SCAN_MAX_MIN}분 안에서 찾지 못했다(경로는 있다)",
                             code="depart_unconfirmed")
        out, _r = self._summary(prep, graph_src, s, e, dep + lead, taxi, kind)
        out.update({"depart_dt": dep, "worst_time_s": int(math.ceil(need)), "access_m": round(access_m, 1),
                    "access_s": int(math.ceil(access_s)), "p10_pct": w["p10_pct"], "roads": prep["roads"]})
        return out

    def _summary(self, prep, graph_src, s, e, depart, taxi, kind):
        """prepare() 결과 + 출발 시각 → 구간 요약(등급·경고·요금). leg()·arrive_by() 가 같이 쓴다 — 규칙은 한 곳."""
        thr = self.F[kind]["time_speed_threshold_kmh"]["value"] if taxi else None
        r = self.g.run(prep, depart, slow_threshold_kmh=thr)
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
               "source_id": [SOURCE_ID, graph_src], "warn": warn}
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
        return out, r


# ☆`[2026-10-04 문제목록 #59]` 시험·명령줄에서만 쓰는 라우터(FixtureRouter · NoRouter)는 car_fixtures.py 로 옮겼다 — 서비스 경로(CarGraph ·
#   CarService)만 이 파일에 남는다. 옛 이름은 첫 접근 때 새 파일에서 이어 준다.
def __getattr__(name):
    if name in ("FixtureRouter", "NoRouter"):
        from . import car_fixtures
        return getattr(car_fixtures, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
