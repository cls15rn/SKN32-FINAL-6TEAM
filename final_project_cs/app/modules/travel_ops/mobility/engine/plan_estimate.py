# -*- coding: utf-8 -*-
"""계획용 이동 추정(P1) — 48번 방(2026-09-25).

    from app.modules.travel_ops.mobility.engine.plan_estimate import estimate
    r = estimate("명동", {"name": "성수 쇼룸", "lat": 37.5445, "lon": 127.056}, "2026-09-29", "오전")

    python -m app.modules.travel_ops.mobility.engine.plan_estimate --from 명동 --to "성수 쇼룸@37.5445,127.056" \\
        --date 2026-09-29 --slot 오전

입력: 출발지·도착지(역명 또는 {name, lat, lon}) · 날짜(**운행일** — 45 계약) · 시간대(오전/오후/저녁/밤)
출력: 날짜 유형(우리 달력) · 소요 p10 / 중앙값 / p90 · 39 최악 소요(이름 그대로) · 출퇴근·막차 주의 · 쓴 경로.
      **여유(@)·slack 은 없다** — 계획용이라 예정값과 범위만(27번 48 첫 메시지).

어떻게 (새 판정이 아니다)
  자기점검(selfcheck)의 「시각 흔들기」를 **구간 하나**에 쓴다 — 시간대 창 안에서 출발 시각을 step 분씩 옮겨 가며
  판정기(verify_case · multi 후보)를 그대로 부르고, 표본마다 **밖 판으로 성립하는 후보 중 예정 소요가 가장 짧은 것**을 고른다.
  판정기·규칙은 한 줄도 안 고친다. 39 의 `eta_min`(예정)·`eta_worst_min`(최악) 위에 얹는다.

범위의 뜻 (◆밀도-1 — p95 는 만들지 않는다 · 가진 것을 이름 그대로)
  p50 = 표본 예정 소요(`eta_min`)의 중앙값
  p10 = 표본 하한 소요의 10 분위 · p90 = 표본 상한 소요의 90 분위
        하한·상한은 지하철·도보면 예정 소요 그대로(시간표 확정 — 흔들림은 출발 시각에서만 온다),
        **버스 한 구간 후보면 41 구간 프로파일의 p10·p90 누적을 같은 승차 시각에서** 다시 센 값(버스 범위는 p10/p50/p90 그대로).
  worst_max = 표본 39 최악 소요(`eta_worst_min` · 버퍼 제외)의 최댓값 — 판정기가 쓰는 값을 이름 그대로 낸다. 범위에 안 섞는다.
  ★ 이 분포는 **「출발 시각에 따른 변동」(+ 버스는 날짜별 시간대 평균의 분위)**이지 「날마다의 변동」이 아니다.
    분위는 최근순위(nearest-rank) — 보간하지 않는다(분 단위 정수 · 표본에 있는 값만 낸다).
  ★ (GPT 7) p10/p90 은 **출발 표본별 하한·상한 추정치의 분위**다 — 전체 이동시간 확률분포의 분위가 아니고,
    [p10, p90] 은 80% 예측구간·도착 확률이 아니다. 버스 대기는 예정(best) 승차 시각에 고정한다(대기 변동 없음).
    구간별 분위를 누적한 값에 다시 표본 분위를 씌운 것이라 두 번 근사다. 출력 `eta.basis` 에 같은 말을 싣는다.

시간대 창(운행일 분) — 결정 2
  오전 06:00~12:00 · 오후 12:00~18:00 · 저녁 18:00~22:00 · 밤 22:00~28:00(다음 날 04:00 = 운행일 끝)
  밤 시작 = 서울 택시 심야할증 시작(rules taxi 22:00 · 확정) · 밤 끝 = 운행일 끝(timeutil 04:00).
  06시·정오·18시는 시계 관례다(근거 자료 없음) — 창은 판정이 아니라 **표본을 뽑는 범위**라 결과에 창과 표본 수를 같이 낸다.
  04:00~06:00(새벽)은 네 창 밖이다 — 04:00 에서 열면 지하철 첫차 전 버스 표본이 오전 분포를 끌어올린다
  (사당→강남 평일 오전: 04:00 창 p90 44분 · 06:00 창은 지하철만). 새벽이 필요하면 window=(분, 분)으로 준다.

주의(판정 아님 · 데이터에서만)
  commute_crowding  평일 창 안 지하철 승차역 재차율이 rules congestion.levels 「혼잡」(≥80) 이상인 표본이 있다(33 데이터)
  bus_peak          창 안 버스 승차 중앙값이 같은 요일형 하루 중 가장 빠른 시간대보다 **정책 버퍼 이상** 느리다
                    (버퍼가 흡수한다고 본 폭을 넘는다는 뜻 — 새 상수 없이 rules buffer.by_stage 를 쓴다)
  last_service      창에서 가장 많이 쓴 경로가 뒤쪽 출발에서 막차로 불가(after_last)가 된다 — 그 경로의 마지막 성립 표본 시각과
                    그 뒤를 다른 경로가 잇는지(심야버스 등) · 아무 경로도 없으면 그렇게 말한다. 경계 다음 표본에서
                    예정은 되고 최악만 막차를 놓치면(버스 막차 근처) near_last=true 로 같이 낸다.
                    경계 코드는 그 경로 하나를 legs 케이스로 다시 판정해 받는다 — multi 는 불가 버스 직행을 코드 없이 접는다
  near_last         예정(best)은 되는데 최악(worst)이 막차를 놓치는 표본이 있다(MOB_W_BEST_OK_WORST_FAIL)
  first_service     창에서 가장 많이 쓴 경로가 앞쪽 출발에서 첫차 전(before_first)이다 — 처음 성립 시각
  service_gap       배차 공백(service_gap)으로 불가인 표본이 있다
  no_service        창 안 성립 표본이 하나도 없다 — 이유 코드를 표본 수와 같이(모두 막차 뒤·모두 첫차 전일 때만 그렇게 단정)
  calendar_unknown  날짜가 공휴일 표가 덮는 해 밖이다 — 공휴일을 평일로 볼 수 있다

경계 시각 (GPT 3)
  첫차·막차의 성립 경계는 표본 간격(5분)이 아니라 **1분 단위로 다시 판정**해 낸다 — 경계 표본 사이 분을 그 경로 하나로 훑는다.
  `last_ok`/`first_ok` = 1분 단위 확인값 · `*_sample` = 창 표본에서 본 값.
  `none_from` 은 창 끝까지 **이어서** 성립 경로가 없을 때만 낸다. 중간에 비었다가 다시 잇는 시각은 `gaps`(GPT 2).

수단 (결정 8)
  기본은 지하철·버스·도보. 자전거는 modes 에 "bike" 를 줄 때만 — 기본에서 빼는 이유는 위 __init__ 주석(라우터 HTTP 비용 ·
  시각대 변동 없음 · 대여 가능 여부 실시간 근거없음). 택시·자동차는 계획용 추정 밖(판정기 runtime 에 자동차 서비스가 없다).

판정기 상태 (GPT 1)
  판정기 싱글턴을 **건드리지 않는다** — 추정은 얕은 복사본(`copy.copy`)에서 역산(lfd)만 끄고 돈다. 표·시간표·캐시는 공유하고,
  건마다 갈아 끼우는 상태(`_case_date`·`disr`·`_leg_cache`·`lfd_capped`)는 verify_case 가 재할당하므로 복사본에만 남는다.
  종전 판(`6669a28`)은 공유 판정기의 `lfd_enabled` 를 껐다 켰다 — 동시 실행이면 영구히 꺼질 수 있었다.
  ※ 판정기 자체의 동시 실행 안전성(같은 객체로 verify_case 를 두 스레드가 동시에)은 이 방 밖이다.
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import math
import sys
from datetime import date as _date, timedelta
from pathlib import Path

from .geo import same_station
from .timeutil import fmt_min, day_type_of, MIN_DAY
from .verify_time import leg_mode

ESTIMATE_VERSION = "estimate-v1.1"
SLOTS = {"오전": (6 * 60, 12 * 60), "오후": (12 * 60, 18 * 60), "저녁": (18 * 60, 22 * 60), "밤": (22 * 60, 28 * 60)}
SLOT_ALIASES = {"morning": "오전", "afternoon": "오후", "evening": "저녁", "night": "밤"}
DEFAULT_STEP_MIN = 5
DEFAULT_MODES = ("subway", "bus", "walk")      # 결정 8 — 자전거는 기본 밖(요청하면 넣는다)
_WD = "월화수목금토일"


# ── 달력 ────────────────────────────────────────────────────────────────
def _holiday_names():
    from .paths import RULES_DIR
    d = json.loads((RULES_DIR / "holidays_2026_2027.json").read_text(encoding="utf-8"))
    return d["holidays"], d.get("years") or []


def day_info(d, holidays=None):
    """운행일 → 우리 달력의 날짜 유형. holidays: {'YYYY-MM-DD': 이름} (없으면 rules 의 공휴일 표).

    kind           평일 · 토요일 · 일요일 · 공휴일(토·일과 겹쳐도 공휴일 — 혼잡도 규칙과 같은 순서)
    timetable      시간표·버스 프로파일 요일축 weekday/holiday(timeutil.day_type_of — 판정기와 같은 함수)
    congestion     혼잡도 요일 키(1~8호선 3종 · 공휴일은 sunday) — congestion.Congestion.day_key 와 같은 규칙
    in_calendar    공휴일 표가 덮는 해인가 — 밖이면 공휴일을 모른다(평일로 잘못 볼 수 있다)
    """
    years = None
    if holidays is None:
        holidays, years = _holiday_names()
    iso = d.isoformat()
    name = holidays.get(iso)
    wd = d.weekday()
    kind = "공휴일" if name else ("토요일" if wd == 5 else "일요일" if wd == 6 else "평일")
    tt = day_type_of(d, set(holidays))
    cg = "weekday" if tt == "weekday" else ("saturday" if (wd == 5 and not name) else "sunday")
    out = {"date": iso, "weekday": _WD[wd], "kind": kind, "timetable": tt, "congestion": cg}
    if name:
        out["holiday_name"] = name
    if years is not None:
        out["in_calendar"] = d.year in years
    return out


# ── 분위 ────────────────────────────────────────────────────────────────
def pct(values, q):
    """최근순위 분위 — 정렬한 값의 ceil(q·n) 번째(1-기준). 보간하지 않는다. 빈 목록이면 None."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    k = max(1, math.ceil(q * len(xs)))
    return xs[k - 1]


def slot_window(slot):
    s = SLOT_ALIASES.get(slot, slot)
    if s not in SLOTS:
        raise ValueError(f"시간대는 {'/'.join(SLOTS)} 중 하나다: {slot!r}")
    return s, SLOTS[s]


# ── 본체 ─────────────────────────────────────────────────────────────────

def _multi_of(A, B):
    """출발지·도착지 점 둘 → verify_multi 의 multi 칸. 동명이역이면 노선군을 같이(55 ④ · plan._multi 와 같은 모양)."""
    m = {"from": A["station"], "to": B["station"]}
    if A.get("lines"):
        m["from_lines"] = list(A["lines"])
    if B.get("lines"):
        m["to_lines"] = list(B["lines"])
    return m

class Estimator:
    def __init__(self, runtime, *, stage="planning", modes=None):
        from .plan import Planner
        self.rt = runtime
        # GPT 1 — 공유 판정기를 바꾸지 않는다. 복사본에서만 역산을 끈다(표본마다 최대 600회 재판정 방지 · 판정 결과는 같다)
        self.v = copy.copy(runtime._v)
        self.v.lfd_enabled = False
        self.modes = set(modes) if modes else set(DEFAULT_MODES)
        # 결정 8 — 자전거를 안 볼 때는 복사본에서 따릉이 대여소 표를 뗀다 → multi 가 자전거 후보를 만들지 않는다.
        #   라우터(GraphHopper)가 떠 있으면 자전거 후보가 표본마다 HTTP 경로 탐색을 불러 창 하나가 수십 분이 된다
        #   (노트북 2026-09-25 · reg48 가 1시간 넘게 돎). 자전거 소요는 시각대에 따라 안 변하고 대여 가능 여부가
        #   실시간이라(근거없음) 계획용 범위에 넣을 값이 아니다.
        if "bike" not in self.modes:
            self.v.bk = None
        self.P = Planner(runtime, stage=stage, modes=modes)      # 장소↔역 도보 식은 32 와 같은 것을 쓴다
        self.stage = stage

    # 출발지·도착지 → (표시 이름, 역명 또는 None, 장소→역 도보 분, 좌표 dict 또는 None)
    def _point(self, x, wlim):
        sc = self.v.sc
        if isinstance(x, str):
            if sc is not None and getattr(sc, "is_ambiguous", None) and sc.is_ambiguous(x):
                # 55 ④ — 동명이역은 역명만으로 고르지 않는다(양평 = 양평군 · 영등포구)
                raise ValueError(f"역 이름이 두 역을 가리킨다: {x!r}({' / '.join(sc.ambiguous_lines(x))}) — "
                                 f"장소 {{name, lat, lon}} 로 준다")
            if sc is None or x not in sc.by_name:
                raise ValueError(f"역 이름을 모른다: {x!r} — 장소면 {{name, lat, lon}} 로 준다")
            rec = sc.by_name[x]
            return {"name": x, "station": x, "walk": 0,
                    "place": {"name": x, "lat": rec.get("lat"), "lon": rec.get("lng")}}
        p = dict(x)
        if "lon" not in p and "lng" in p:
            p["lon"] = p["lng"]
        if p.get("lat") is None or p.get("lon") is None:
            return {"name": p.get("name"), "station": None, "walk": None, "place": None}
        near = self.P._near_station(p, wlim)
        if near is None:
            return {"name": p.get("name"), "station": None, "walk": None, "place": p}
        return {"name": p.get("name"), "station": near[0], "walk": self.P._walk(near[1]), "place": p,
                "lines": near[2] if len(near) > 2 else None}

    def _dayf(self, d, day_type):
        """버스 프로파일 요일형 — 판정기 _bus_day_types 와 같은 규칙(자정 뒤 연장 구간은 다음 날 요일형도 본다)."""
        nxt = day_type_of(d + timedelta(days=1), self.v.holidays)

        def f(t):
            if t >= MIN_DAY and nxt != day_type:
                return (day_type, nxt)
            return (day_type,)
        return f

    def _bus_range(self, cand, d, day_type):
        """버스 한 구간 후보 → (하한 가감, 상한 가감) 분 또는 None(온전한 프로파일이 아님).
        같은 승차 시각(예정 통과의 승차)에서 구간 프로파일 p10·p50·p90 을 진입 시각대로 누적한다(41 과 같은 누적).
        예정 승차(ride_min)가 p50 프로파일로만 셈해졌을 때만 — 대체 구간·부분·보정이 섞이면 범위를 안 낸다."""
        v = self.v
        legs = cand["legs"]
        if [leg_mode(x) for x in legs] != ["bus"] or v.bus_prof is None:
            return None
        lr = [x for x in cand["legs_result"] if x.ride_src is not None]
        if len(lr) != 1 or lr[0].ride_src != "profile" or lr[0].depart_min is None:
            return None
        L = lr[0]
        r = v.bus.route(str(legs[0]["route"]))
        seg = v.bus.segment(r.route_id, legs[0]["from"], legs[0]["to"]) if r else None
        if seg is None:
            return None
        a, b, _ = seg
        stops = v.bus.stops[r.route_id]
        md = v.rv("bus", "구간_프로파일", "min_days")
        dayf = self._dayf(d, day_type)
        got = {}
        for q in ("p10", "p50", "p90"):
            w = v.bus_prof.walk(r.route_id, stops, a["seq"], b["seq"], L.depart_min, dayf, q, md,
                                lambda _d: None, pick=max)
            if not w.complete:
                return None
            got[q] = math.ceil(w.minutes)
        if got["p50"] != math.ceil(L.ride_min):
            return None           # 판정기 예정 승차와 같은 값이 아니면(축 어긋남) 범위를 안 낸다 — 모르면 뺀다
        return got["p10"] - got["p50"], got["p90"] - got["p50"], {"route": r.route_nm, "route_id": r.route_id,
                                                                  "a": a, "b": b, "stops": stops}

    def _bus_slow(self, info, d, day_type, board_mins):
        """창 안 버스 승차 중앙값(p50) − 같은 요일형 하루 중 가장 빠른 시간대의 p50 승차(분). 온전한 칸만."""
        v = self.v
        md = v.rv("bus", "구간_프로파일", "min_days")
        dayf = self._dayf(d, day_type)

        def ride(t):
            w = v.bus_prof.walk(info["route_id"], info["stops"], info["a"]["seq"], info["b"]["seq"], t, dayf,
                                "p50", md, lambda _d: None, pick=max)
            return w.minutes if w.complete else None
        day = [ride(h * 60) for h in range(5, 24)]          # 05~23시 정각 승차 — 첫차 전 칸은 날 수가 적어 빠진다
        day = [x for x in day if x is not None]
        slot = [ride(t) for t in board_mins]
        slot = [x for x in slot if x is not None]
        if not day or not slot:
            return None
        return round(pct(slot, 0.5) - min(day), 1)

    def _dir_of(self, line, frm, to, day_type, dep_min, arr_min):
        """지하철 구간의 방향 — 판정기가 고른 편성과 **출발 분·도착 분이 둘 다 같은** 후보의 dir. 혼잡도 조회용.
        (GPT 4) 같은 분에 방향이 다른 편성이 있어도 도착 분으로 가른다. 그래도 방향이 하나로 안 정해지면 None — 혼잡도를 안 본다."""
        cands, _, _ = self.v.candidates(line, frm, to, day_type)
        dirs = set()
        for dep, pv, _full in cands:
            if dep.min != dep_min:
                continue
            ride = self.v.lo.travel_min_on_path(line, pv.path, to)
            if ride is not None and arr_min is not None and dep.min + math.ceil(ride) == arr_min:
                dirs.add(dep.dir)
        return dirs.pop() if len(dirs) == 1 else None

    def _crowd(self, cand, d, day_type, is_hol):
        """후보 지하철 구간마다 승차역 재차율(판정기와 같은 자리 — 승차역·승차 슬롯). (값, 등급, 노선, 역, 슬롯) 최대."""
        v = self.v
        if not v.cg_data:
            return None
        L = v.R["congestion"]["levels"]
        best = None
        for leg, lr in zip(cand["legs"], [x for x in cand["legs_result"] if not x.label.startswith("환승")]):
            if leg_mode(leg) != "subway" or lr.depart_min is None:
                continue
            dr = self._dir_of(leg["line"], leg["from"], leg["to"], day_type, lr.depart_min, lr.arrive_min)
            if dr is None:
                continue
            hit = v.cg_data.lookup(leg["line"], leg["from"], dr, day_type, d, lr.depart_min, L, is_holiday=is_hol)
            if hit and (best is None or hit[0] > best[0]):
                best = (hit[0], hit[1], leg["line"], leg["from"], fmt_min((lr.depart_min // 30) * 30))
        return best

    def estimate(self, frm, to, date, slot, *, party=None, first_visit=True, step=DEFAULT_STEP_MIN,
                 window=None, samples=None):
        """→ dict. samples 에 리스트를 주면 표본마다 내부 값을 적는다(시험·대조용 — 밖으로 안 나간다)."""
        v = self.v
        party = dict(party or {})
        d = _date.fromisoformat(str(date))
        info = day_info(d)
        slot_nm, (lo, hi) = slot_window(slot)
        if window:
            lo, hi = window
        # GPT 6 — 창·간격이 틀리면 표본 0건이 「이동 불성립」으로 새지 않게 멈춘다(운행일 04:00~28:00 안)
        if not (isinstance(step, int) and step > 0):
            raise ValueError(f"step 은 양의 정수(분)다: {step!r}")
        if not (isinstance(lo, int) and isinstance(hi, int) and 4 * 60 <= lo < hi <= 28 * 60):
            raise ValueError(f"창은 운행일 04:00~28:00 안의 [시작, 끝) 분이다: {lo!r}~{hi!r}")
        wlim = v._walk_limit(party)
        A, B = self._point(frm, wlim), self._point(to, wlim)
        base = {"from": A["name"], "to": B["name"], "slot": slot_nm, "day": info,
                "window": {"from": fmt_min(lo), "to": fmt_min(hi), "step_min": step}}
        buf = v.rv("buffer", "by_stage", self.stage)

        # 도보 직행 — 두 장소 직선이 도보 상한 안이면 표본마다 같은 후보(32 와 같은 식)
        walk_eta = None
        if A["place"] and B["place"] and "walk" in self.modes:
            from .geo import meters
            dm = meters(A["place"]["lat"], A["place"]["lon"], B["place"]["lat"], B["place"]["lon"])
            if dm <= wlim:
                walk_eta = max(1, self.P._walk(dm))
        same = (A["station"] is not None and
                same_station(A["station"], A.get("lines"), B["station"], B.get("lines")))   # 55 GPT #2 — 물리적 역 기준
        transit = A["station"] is not None and B["station"] is not None and not same
        if not transit and walk_eta is None:
            why = ("두 곳의 가장 가까운 역이 같고 도보 상한 밖이다" if same
                   else "도보 상한 안에 지하철역이 없다(" + (A["name"] if A["station"] is None else B["name"]) + ")")
            return {**base, "verdict": "no_data", "reason": why}

        is_hol = "holiday_name" in info
        tt_day = info["timetable"]
        pts = list(range(lo, hi, step))
        rows, ref = [], {}
        for t in pts:
            row = {"t": t, "choice": None, "codes": [], "near_last": False, "by": {}}
            opts = []
            if walk_eta is not None:
                row["by"]["도보"] = "ok"
                opts.append({"key": "도보", "uses": [], "eta": walk_eta, "lo": walk_eta, "hi": walk_eta,
                             "worst": walk_eta, "transfers": 0, "range": "walk"})
            if transit:
                r = v.verify_case({"id": f"est/{fmt_min(t)}", "date": d.isoformat(), "stage": self.stage,
                                   "depart_at": t + A["walk"], "multi": _multi_of(A, B),
                                   "party": party, "first_visit": first_visit})
                for c in r.candidates or []:
                    if any(leg_mode(x) not in self.modes for x in c["legs"]):
                        continue
                    o = c.get("out") or {}
                    if any(w.get("code") == "MOB_W_BEST_OK_WORST_FAIL" for w in c.get("warnings") or []):
                        row["near_last"] = True
                    if o.get("verdict") != "feasible" or o.get("eta_min") is None:
                        row["codes"].append(o.get("code") or "no_data")
                        row["by"][c["label"]] = o.get("code") or "no_data"
                        continue
                    row["by"][c["label"]] = "ok"
                    ref.setdefault(c["label"], (c["legs"], c["walk_in_min"]))
                    eta = A["walk"] + o["eta_min"] + B["walk"]
                    wst = A["walk"] + (o.get("eta_worst_min") if o.get("eta_worst_min") is not None
                                       else o["eta_min"]) + B["walk"]
                    rg = self._bus_range(c, d, tt_day)
                    is_bus = any(leg_mode(x) == "bus" for x in c["legs"])
                    opt = {"key": c["label"], "uses": _uses(c["legs"]), "eta": eta, "worst": wst,
                           "transfers": c.get("transfers") or 0, "cand": c,
                           "lo": eta + (rg[0] if rg else 0), "hi": eta + (rg[1] if rg else 0),
                           "range": "bus_profile" if rg else ("bus_no_profile" if is_bus else "timetable"),
                           "_bus": rg[2] if rg else None}
                    opts.append(opt)
            if opts:
                ch = min(opts, key=lambda o: (o["eta"], o["transfers"], o["key"]))
                row.update(choice=ch["key"], uses=ch["uses"], eta=ch["eta"], lo=ch["lo"], hi=ch["hi"],
                           worst=ch["worst"], range=ch["range"])
                if ch.get("cand") is not None:
                    row["crowd"] = self._crowd(ch["cand"], d, tt_day, is_hol)
                    if ch.get("_bus"):
                        row["_bus"] = ch["_bus"]
                        row["board"] = next(x.depart_min for x in ch["cand"]["legs_result"] if x.ride_src)
                    row["legs"] = ch["cand"]["legs"]
            rows.append(row)

        def probe(label, t):
            """그 경로 하나를 legs 케이스로 t 출발에 다시 판정 — 첫차·막차 경계의 이유 코드를 받는다.
            multi 는 불가 버스 직행 후보를 bus_rejected 로 접어(코드 없음) 경계가 안 보이기 때문이다."""
            legs, win = ref[label]
            rr = v.verify_case({"id": f"est/probe/{fmt_min(t)}", "date": d.isoformat(), "stage": self.stage,
                                "depart_at": t + A["walk"] + win, "legs": legs, "party": party,
                                "first_visit": first_visit, "no_alternatives": True})
            return (rr.out or {}).get("verdict"), (rr.out or {}).get("code"), rr.verdict_best
        ok = [x for x in rows if x["choice"] is not None]
        cautions = _cautions(rows, ok, buf, self, d, tt_day, probe if ref else None)
        if info.get("in_calendar") is False:
            cautions.append({"code": "calendar_unknown", "text": f"{d.year}년은 공휴일 표 밖이다 — 공휴일을 평일로 볼 수 있다"})

        base["window"]["n"] = len(rows)
        base["window"]["n_feasible"] = len(ok)
        if samples is not None:
            samples.extend({k: val for k, val in x.items() if not k.startswith("_")} for x in rows)
        if not ok:
            codes = collections.Counter(c for x in rows for c in x["codes"])
            return {**base, "verdict": "infeasible", "code": (codes.most_common(1)[0][0] if codes else "no_data"),
                    "reason": "창 안의 어느 출발 시각에도 성립하는 후보가 없다", "cautions": cautions,
                    "basis": self._basis()}
        used = collections.defaultdict(list)
        for x in ok:
            used[x["choice"]].append(x)
        routes = sorted(({"label": k, "uses": xs[0]["uses"], "n": len(xs),
                          "eta_p50_min": pct([x["eta"] for x in xs], 0.5), "range": xs[0]["range"]}
                         for k, xs in used.items()), key=lambda z: (-z["n"], z["eta_p50_min"]))
        eta = {"p10_min": pct([x["lo"] for x in ok], 0.1), "p50_min": pct([x["eta"] for x in ok], 0.5),
               "p90_min": pct([x["hi"] for x in ok], 0.9), "worst_max_min": max(x["worst"] for x in ok),
               "basis": "출발 시각 흔들기(창 안 step 분) — p10/p90 은 출발 표본별 하한·상한 추정치의 분위다. "
                        "하한·상한은 지하철·도보는 시간표 예정값, 버스 한 구간은 구간 프로파일 p10/p90 누적(날짜별 시간대 평균의 분위 · "
                        "대기는 예정 승차에 고정). 전체 이동시간 확률분포의 분위가 아니며 [p10, p90] 은 80% 예측구간·도착 확률이 아니다. "
                        "날마다의 변동이 아니다. worst_max 는 39 최악 소요(버퍼 제외)의 최댓값"}
        if any(x["range"] == "bus_no_profile" for x in ok):
            eta["note"] = "버스 표본 일부는 온전한 구간 프로파일이 아니라 범위를 예정값으로 두었다"
        return {**base, "verdict": "feasible", "eta": eta, "routes": routes, "cautions": cautions,
                "basis": self._basis()}

    def _basis(self):
        bp = self.v.bus_prof
        return {"timetable_built_at": self.rt.timetable_built_at, "rules_version": self.rt.rules_version,
                "bus_profile_built_at": bp.built_at if bp is not None else None,
                "estimate_version": ESTIMATE_VERSION}


def _uses(legs):
    from .plan import uses_of
    return uses_of(legs)


def _runs(ts, step):
    """정렬된 분 목록 → 연속 구간 [(시작, 끝)] (step 간격으로 이어진 것끼리)."""
    out = []
    for t in ts:
        if out and t - out[-1][1] == step:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [tuple(x) for x in out]


def _cautions(rows, ok, buf, est, d, tt_day, probe):
    """주의 목록. probe(경로, 분) → (밖 판정, 이유 코드, 예정 판정) — 그 경로 하나를 legs 케이스로 다시 판정한다.
    rows 는 창 표본(시각 순) — {t, choice, codes, near_last, by, crowd?, _bus?, board?}."""
    out = []
    step = rows[1]["t"] - rows[0]["t"] if len(rows) > 1 else 1
    prim = None
    if ok:
        cnt = collections.Counter(x["choice"] for x in ok)
        prim = max(cnt, key=lambda k: (cnt[k], k))
    if prim is not None and prim != "도보" and probe is not None:
        pok = [x["t"] for x in rows if x["by"].get(prim) == "ok"]
        first_s, last_s = pok[0], pok[-1]
        early = [x for x in rows if x["t"] < first_s]
        late = [x for x in rows if x["t"] > last_s]
        if early:
            verdict, code, _vb = probe(prim, early[-1]["t"])
            if verdict != "feasible" and code == "before_first":
                # GPT 3 — 경계를 1분 단위로: 앞 표본(불가) 다음 분부터 첫 성립 표본 앞까지 훑어 처음 성립 분
                first = first_s
                for t in range(early[-1]["t"] + 1, first_s):
                    if probe(prim, t)[0] == "feasible":
                        first = t
                        break
                carry = sorted({x["choice"] for x in early if x["choice"] is not None})
                out.append({"code": "first_service", "route": prim, "first_ok": fmt_min(first),
                            "first_ok_sample": fmt_min(first_s), "carried_by": carry,
                            "text": f"{prim} 은 {fmt_min(first)} 출발부터 된다(그 전은 첫차 전 · 1분 단위 확인)"
                                    + (" — 그 전은 " + ", ".join(carry) if carry else " — 그 전은 대중교통이 없다")})
        if late:
            verdict, code, vb = probe(prim, late[0]["t"])
            near = verdict != "feasible" and vb == "feasible"
            if verdict != "feasible" and (code == "after_last" or near):
                last = last_s
                for t in range(last_s + 1, late[0]["t"]):
                    if probe(prim, t)[0] == "feasible":
                        last = t
                carry = sorted({x["choice"] for x in late if x["choice"] is not None})
                # GPT 2 — none_from 은 창 끝까지 이어서 비는 꼬리만. 중간에 비었다가 다시 잇는 구간은 gaps
                none_ts = [x["t"] for x in late if x["choice"] is None]
                runs = _runs(none_ts, step)
                tail = runs[-1] if runs and runs[-1][1] == rows[-1]["t"] else None
                gaps = [r for r in runs if r != tail]
                item = {"code": "last_service", "route": prim, "last_ok": fmt_min(last), "last_ok_sample": fmt_min(last_s),
                        "carried_by": carry, "none_from": fmt_min(tail[0]) if tail else None, "near_last": near,
                        "gaps": [[fmt_min(a), fmt_min(b)] for a, b in gaps]}
                txt = (f"{prim} 은 {fmt_min(last)} 출발이 마지막이다(1분 단위 확인 · "
                       + ("그 뒤는 예정대로면 타지만 늦어지면 막차를 놓친다)" if near else "그 뒤는 막차가 끊긴다)"))
                if carry:
                    txt += " — 그 뒤는 " + ", ".join(carry)
                if gaps:
                    txt += " · 잇는 경로가 없는 표본: " + ", ".join(f"{fmt_min(a)}~{fmt_min(b)}" for a, b in gaps)
                if tail:
                    txt += f" · {fmt_min(tail[0])} 부터 창 끝까지 성립하는 경로가 없다"
                item["text"] = txt
                out.append(item)
    elif not ok and rows:
        # GPT 3 — 전체 단정은 모든 표본의 근거가 같을 때만
        per = [set(x["codes"]) for x in rows]
        if all(c == {"after_last"} for c in per):
            out.append({"code": "last_service", "route": None, "last_ok": None, "text": "창 안 모든 출발이 막차 이후다"})
        elif all(c == {"before_first"} for c in per):
            out.append({"code": "first_service", "route": None, "first_ok": None, "text": "창 안 모든 출발이 첫차 전이다"})
        else:
            cnt = collections.Counter(c for x in rows for c in (x["codes"] or ["no_candidate"]))
            out.append({"code": "no_service", "codes": dict(cnt),
                        "text": "창 안 성립 표본이 없다 — " + " · ".join(f"{k} {v}" for k, v in cnt.most_common())})
    nl = [x["t"] for x in rows if x["near_last"]]
    if nl:
        out.append({"code": "near_last", "at": [fmt_min(nl[0]), fmt_min(nl[-1])],
                    "text": f"{fmt_min(nl[0])}~{fmt_min(nl[-1])} 출발은 예정대로면 되지만 늦어지면 막차를 놓치는 후보가 있다"})
    gap = [x["t"] for x in rows if "service_gap" in x["codes"] and x["choice"] is None]
    if gap:
        out.append({"code": "service_gap", "at": [fmt_min(gap[0]), fmt_min(gap[-1])],
                    "text": "배차 공백으로 성립하지 않는 출발이 있다"})
    # 출퇴근 — 평일만(재차율). 주말·공휴일 재차율은 표본에 남기되(samples.crowd) 주의로 올리지 않는다
    if tt_day == "weekday":
        L = est.v.R["congestion"]["levels"]["혼잡"]["gte"]
        cr = [x["crowd"] for x in ok if x.get("crowd") and x["crowd"][0] >= L]
        if cr:
            top = max(cr, key=lambda c: c[0])
            out.append({"code": "commute_crowding", "pct": top[0], "level": top[1], "line": top[2], "station": top[3],
                        "slot": top[4], "n": len(cr),
                        "text": f"평일 이 시간대 {top[2]} {top[3]} 승차 재차율 {top[0]}%({top[1]}) — 붐비는 시간이다"})
    bus_rows = [x for x in ok if x.get("_bus")] if tt_day == "weekday" else []    # GPT 5 — 출퇴근 주의는 평일만
    if bus_rows:
        by = collections.defaultdict(list)
        for x in bus_rows:
            by[x["_bus"]["route_id"]].append(x)
        for rid, xs in by.items():
            slow = est._bus_slow(xs[0]["_bus"], d, tt_day, [x["board"] for x in xs])
            if slow is not None and slow >= buf:
                out.append({"code": "bus_peak", "route": xs[0]["_bus"]["route"], "slower_min": slow, "threshold_min": buf,
                            "text": f"버스 {xs[0]['_bus']['route']} 이 시간대 승차가 하루 중 가장 빠른 때보다 {slow}분 느리다 "
                                    f"(정책 버퍼 {buf}분 이상)"})
    return out


def estimate(frm, to, date, slot, *, party=None, first_visit=True, runtime=None, stage="planning",
             modes=None, step=DEFAULT_STEP_MIN, samples=None):
    """출발지·도착지·날짜(운행일)·시간대 → 계획용 추정 dict. 판정기 무수정 — 39 출력 위에 얹는다."""
    if runtime is None:
        from .runtime import get_verifier
        runtime = get_verifier(quiet=True)
    return Estimator(runtime, stage=stage, modes=modes).estimate(
        frm, to, date, slot, party=party, first_visit=first_visit, step=step, samples=samples)


def _parse_point(s):
    """'역명' 또는 '이름@lat,lon' → estimate 입력."""
    if "@" in s:
        nm, ll = s.rsplit("@", 1)
        la, lo = (float(x) for x in ll.split(","))
        return {"name": nm, "lat": la, "lon": lo}
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description="계획용 이동 추정(P1) — 날짜 유형 · 소요 p10/중앙값/p90 · 출퇴근·막차 주의")
    ap.add_argument("--from", dest="frm", required=True, help="역명 또는 '이름@lat,lon'")
    ap.add_argument("--to", required=True, help="역명 또는 '이름@lat,lon'")
    ap.add_argument("--date", required=True, help="운행일 YYYY-MM-DD")
    ap.add_argument("--slot", required=True, help="오전/오후/저녁/밤")
    ap.add_argument("--step", type=int, default=DEFAULT_STEP_MIN)
    ap.add_argument("--modes", nargs="*", help="후보 수단(subway bus walk bike) — 없으면 subway bus walk")
    ap.add_argument("--samples", help="표본마다 내부 값을 이 JSON 에 적는다 — 대조용")
    ap.add_argument("--no-basis", action="store_true")
    a = ap.parse_args(argv)
    from .runtime import build_verifier
    rt = build_verifier(quiet=True)
    sm = [] if a.samples else None
    res = estimate(_parse_point(a.frm), _parse_point(a.to), a.date, a.slot, runtime=rt, modes=a.modes,
                   step=a.step, samples=sm)
    if a.samples:
        Path(a.samples).write_text(json.dumps(sm, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.no_basis:
        res.pop("basis", None)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
