# -*- coding: utf-8 -*-
"""이동 값 내놓기 — 32번 방(2026-09-25). 팀장이 꽂을 수 있는 **함수 하나 + CLI**.

    from app.modules.travel_ops.mobility.engine.plan import plan
    out = plan(places, items, party_size=2, constraints={"first_visit": True})
    body["items"] = out["items"]                                     # CreateTrip 의 기존 칸 그대로
    body["routes"] = {**body.get("routes", {}), **out["routes"]}     # 합친다(남겨 둔 입력 이동 항목의 route 보존)

    python -m app.modules.travel_ops.mobility.engine.plan --in trip_in.json --out trip_out.json

입력 = CreateTrip 의 기존 칸(`places[]`·`items[]`·`party_size`·`constraints`) — 새 칸 없음.
출력 = 출력 스펙 v1.4 의 기존 칸만 — 새 키 0. (23번 방 · 2026-09-25 · plan-v2)
  items[kind=mobility]  seq · kind · title · starts_at · ends_at · route
  routes{<키>}          from · to · planned · options[{id, label, eta_min, walk_m?, fare_krw?, uses}]
                        id 접두 = 수단 태그(subway_ · bus_ · walk · bike) · label = 경로 + 이유(축별 사실 · 순위 없음)
                        walk_m·fare_krw 는 팀 route_def 기존 칸 — 모르면 키를 뺀다(요금은 규칙 fare 절 · options.py · 54)
  그 밖에 봉투에 `skipped`(이동 항목을 못 만든 구간과 이유)·`left_out`(싣지 않은 후보와 이유)·`basis` 를 같이 준다 —
  **코어 몸통에는 `items`·`routes` 두 칸만 옮긴다**(CreateTrip 은 extra=forbid).

값의 뜻 (39 인계 §1-2 · 스펙 v1.3 §5)
  starts_at = 도착 목표(다음 항목 starts_at) − (eta_min + margin_min) − slack_min   (slack_min ≥ 0)
              = **그 구간이 성립하는 마지막 출발**(판정기의 worst 역산 · 버퍼 포함).
              slack_min 은 시간표가 분 단위로 연속이 아니라서 남는 몫이다 — 지하철은 「그 다음 편」이 늦으면
              출발을 더 미룰 수 없다. slack 을 빼고 식 그대로 쓰면 편성을 놓치는 시각이 나온다(결정 1).
  ends_at   = starts_at + eta_min
  eta_min   = 문→문 예정 소요(중앙값 · 버퍼 안 섞음 — 팀 density.py 가 자기 버퍼를 더한다)
  다음 항목까지 남는 간격(= margin_min + slack_min)이 곧 여유다. 여유 분은 따로 안 낸다(v1.3).

안 내는 것: last_feasible_depart_min · 후보별 출발 시각(23 결정 1) · 등급 · 내부 판정 넷 · p90·slack·verdict·reason·modes 등
            상세 값(계획서 작성 콜이 오면 `out` 에서 뽑는다 — 스펙 v1.3).

시각·날짜 (45 계약)
  `case.date` 는 **운행일**이다 — 04:00 전 시각은 그 운행일의 연장(00:30 → 전날 24:30).
  입력 시각은 ISO 8601. 오프셋이 없으면 서울 시각으로 읽는다(trip_api._seoul 과 같은 규칙).
  출력 시각은 항상 `+09:00`, 초 `:00`.
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import options as O
from .options import line_name, station_name  # noqa: F401 — 32 시험·호출 쪽이 plan 에서 가져간다
from .geo import same_station
from .timeutil import MIN_DAY, SERVICE_DAY_START_MIN
from .verify_time import leg_mode

KST = timezone(timedelta(hours=9))
PLAN_VERSION = "plan-v2.2"   # 58 — modes 에 bike 를 주면 자전거 후보를 싣는다 · 기본(bike 없음)은 v2.1 과 같다 · 모양 무변경
# 56 (2026-09-27 · 본인) — modes 를 안 주면 지하철·버스·도보. 자전거는 modes 에 "bike" 를 줄 때만(48 결정 8 · ◆선호 「요청 시만」).
#   뺄 때는 **후보 생성 전에** 끊는다(아래 Planner — 따릉이 실시간·GraphHopper 호출 0).
DEFAULT_MODES = ("subway", "bus", "walk")
KNOWN_MODES = frozenset(DEFAULT_MODES) | {"bike"}
# 58 (2026-09-27 · ◆테마 ① 자전거 살림 · 본인) — 자전거 후보의 출발 시각 규칙(_bike_direct).
#   판정기는 시간표 없는 수단의 마지막 성립 출발(lfd)을 None 으로 낸다(verify_time._last_feasible_depart — 무수정).
#   따릉이는 24시간(rules bike.ddareungi.no_timetable · 확정)이라 「마지막 편」이 없고 소요가 출발 시각에 안 달린다 →
#     lfd = 도착 목표 − (eta + @)      @ = 판정기가 낸 margin_min(자전거는 스프레드 없음 → 단계 정책 버퍼)
#   그 lfd 에서 판정기로 **다시 봐서** 성립한 것만 싣는다(다른 후보와 같은 확인 · 식 start+eta+@+slack = 목표).
#   · 장소 좌표 기준(버스 직행 23 결정 4 와 같은 이유 — 역 경유면 장소→역→대여소 이중 도보). 판정기 multi 의 역 기준
#     자전거 후보는 싣지 않는다.
#   · 계획 단계(stage=planning)는 따릉이 실시간 거치를 **안 본다** — 지금 거치 대수는 계획한 출발 시각의 값이 아니다.
#     판정기가 「거치 미상 · 가용 근거없음」 경고로 내고, label 에 「대여 가능 여부는 출발 때 확인」을 붙인다.
#   · **자전거는 추천하지 않는다**(본인 9/27) — 여행자가 「자전거로 이동한다」고 했을 때만 호출 쪽이 modes=["bike","walk"]
#     (도보는 짧은 구간용)로 부른다. 지하철·버스와 섞어 주면 계획 수단 규칙(가장 늦게 떠나도 되는 후보)이 그대로라
#     더 일찍 떠나야 하는 자전거는 봉투 left_out 에 이유만 남는다. 자전거가 안 되는 구간은 skipped + 이유
#     (대중교통으로 몰래 바꾸지 않는다 — 입력 이동 항목은 그대로 남는다).

# ── 시각 ────────────────────────────────────────────────────────────────
def _parse_dt(v):
    """ISO 문자열·datetime → 서울 시각 aware datetime. 오프셋 없으면 서울로 읽는다."""
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def service_day(dt):
    """서울 시각 → (운행일, 운행일 분). 04:00 전은 전날 운행일의 24:xx(45 계약)."""
    m = dt.hour * 60 + dt.minute
    d = dt.date()
    if m < SERVICE_DAY_START_MIN:
        return d - timedelta(days=1), m + MIN_DAY
    return d, m


def iso_of(service_date, minute):
    """(운행일, 운행일 분) → 'YYYY-MM-DDTHH:MM:00+09:00'. 24:xx 는 다음 날 벽시계로."""
    dt = datetime.combine(service_date, time(0, 0), tzinfo=KST) + timedelta(minutes=int(minute))
    return dt.strftime("%Y-%m-%dT%H:%M:00+09:00")


# ── 표기 ── (노선명·역명은 options.py — 23 이 잡는다. 여기서는 다시 내보내기만)
def uses_of(legs):
    """탄 역·갈아탄 역·내린 역만(환승역은 두 노선 각각) · 버스는 `버스:<노선번호>` · 도보·자전거는 안 적는다.
    ★ 버스가 지나는 `도로:` 는 아직 못 채운다 — 노선별 경유 도로 표가 없다(확인 안 한 것)."""
    out = []
    for leg in legs:
        m = leg_mode(leg)
        if m == "subway":
            ln = line_name(leg["line"])
            out += [f"{ln}:{station_name(leg['from'])}", f"{ln}:{station_name(leg['to'])}"]
        elif m == "bus":
            out.append(f"버스:{leg['route']}")
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def label_of(legs):
    parts = []
    for leg in legs:
        m = leg_mode(leg)
        if m == "subway":
            parts.append(f"{line_name(leg['line'])} {leg['from']}→{leg['to']}")
        elif m == "bus":
            parts.append(f"버스 {leg['route']} {leg['from']}→{leg['to']}")
        else:
            parts.append(f"{m} {leg.get('from')}→{leg.get('to')}")
    return " → ".join(parts)


# ── 입력 정리 ────────────────────────────────────────────────────────────
def _as_dict(x):
    if hasattr(x, "model_dump"):
        return x.model_dump()
    return dict(x)


def party_of(party_size, constraints):
    """판정기 party — 기존 칸에서만 만든다.
    constraints.mobility_ease == "needs_rest"(팀 density.py 가 읽는 값) → fatigue_high(환승 상한 1).
    party_size 는 지금 판정에 쓰는 자리가 없다(따릉이 인원 등은 22 규칙이 party 플래그로만 본다) — 그대로 싣기만 한다."""
    c = constraints or {}
    p = {}
    if party_size:
        p["size"] = int(party_size)
    if c.get("mobility_ease") == "needs_rest":
        p["fatigue_high"] = True
    return p


# ── 본체 ─────────────────────────────────────────────────────────────────
def _is_bus(o):
    return bool(o["_legs"]) and all(leg_mode(x) == "bus" for x in o["_legs"])



def _multi(oa, ob):
    """장소→역 결과 둘로 verify_multi 의 multi 칸. 동명이역이면 노선군을 같이 싣는다(55 ④)."""
    m = {"from": oa[0], "to": ob[0]}
    if len(oa) > 2 and oa[2]:
        m["from_lines"] = list(oa[2])
    if len(ob) > 2 and ob[2]:
        m["to_lines"] = list(ob[2])
    return m


class Planner:
    def __init__(self, runtime, *, stage="planning", modes=None, display=False):
        self.rt = runtime
        # 후보 수단 거르기(예: {"subway"}) — None 이면 DEFAULT_MODES(자전거 뺌 · 56).
        #   빈 목록·모르는 수단은 거절한다(GPT 56 #9 — 빈 목록이 조용히 기본으로 넓어지지 않게).
        self.modes = set(DEFAULT_MODES) if modes is None else set(modes)
        if not self.modes or not self.modes <= KNOWN_MODES:
            raise ValueError(f"modes 는 {sorted(KNOWN_MODES)} 중 하나 이상 — 받은 값 {sorted(self.modes)}")
        self.trace = None                                 # 시험·대조용 — 리스트를 주면 구간마다 내부 값을 적는다
        # 56 ① — 판정기 싱글턴을 요청마다 **얕은 복사본**으로 쓴다(48 plan_estimate 와 같은 방식).
        #   verify_case 가 건마다 재할당하는 상태(_case_date · disr · _leg_cache · lfd_capped)가 복사본에만 남는다 —
        #   sync 엔드포인트(스레드풀)에서 두 요청이 같은 객체를 동시에 쓰면 섞이던 자리(24 ①).
        #   시간표·표·캐시(_passes/_origin/_dominant · 후보 그래프)는 공유한다 — 키가 입력 전부라 값이 요청과 무관하다.
        self.v = copy.copy(runtime._v)
        # 56 ② — 자전거를 안 볼 때는 복사본에서 따릉이 대여소 표를 뗀다 → verify_multi 가 자전거 후보를 **만들지 않는다**
        #   (따릉이 실시간·라우터 호출 0). 종전에는 modes 거르기가 후보 생성 뒤(leg() 의 후보 루프)라 빼도 호출이 났다.
        if "bike" not in self.modes:
            self.v.bk = None
        elif stage == "planning":
            self.v.bike_live = None       # 58 — 계획 단계는 지금 거치 대수를 안 본다(가용 근거없음 · 실시간 호출 0)
        self.stage = stage
        self.speed = self.v.R["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        self.detour = self.v.R["transfer"]["stop_station_walk"]["detour_factor"]["value"]
        # 표시 전용 필드(◆칸 — 답 전엔 만들어만 둔다). 켜면 transfer_car 를 options 에 싣는다(새 키 · 스펙 밖).
        self.display = display
        self._tc = O.TransferCar.load() if display else None

    def _walk_limit(self, party):
        return self.v._walk_limit(party)

    def _walk(self, straight_m):
        """장소 도보 분 = 직선 × 우회계수 ÷ 1.04 m/s (rules transfer.stop_station_walk — 정류장↔역과 같은 식)."""
        return math.ceil(straight_m * self.detour / self.speed / 60) if straight_m else 0

    def _near_station(self, place, limit_m):
        """장소 → 가장 가까운 역(역 좌표 직선 · 도보 상한 안). 거리는 그 역에서 장소에 가장 가까운 출구까지
        (출구표가 없으면 역 좌표) — 정류장↔역 환승(19번)과 같은 방식. (역명, 직선 m, 노선군) 또는 None.
        노선군은 **동명이역(양평·신촌)일 때만** 그 물리적 역의 노선 목록(55 ④) — 아니면 None(역명으로 충분)."""
        sc = self.v.sc
        if sc is None:
            return None
        near = sc.stations_near(place["lat"], place["lon"], limit_m)
        if not near:
            return None
        d, rec = near[0]
        nm = rec["station_nm"]
        lines = (sorted(sc.group_lines(rec)) if getattr(sc, "is_ambiguous", None) and sc.is_ambiguous(nm) else None)
        if self.v.ex is not None:
            e = self.v.ex.nearest(nm, place["lat"], place["lon"], rec.get("line"))
            if e is not None:
                d = e[0]
        return nm, d, lines

    def _bus_direct(self, a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id, wlim):
        """장소 → 장소 한 노선 버스 후보. 후보마다 마지막 성립 출발에서 다시 판정해 성립한 것만 — 도보 짧은 순(상한은 leg())."""
        v = self.v
        if v.bus is None or (self.modes is not None and "bus" not in self.modes):
            return []
        radius = v.rv("alternatives", "정류장_반경_m")
        excluded = v.rv("bus", "route_type_제외") or []
        found = []
        for i, (r, x, y, _span, da, db) in enumerate(v.bus.routes_between(
                a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"], radius)):
            if r.route_type_nm in excluded or max(da, db) > wlim:
                continue
            legs = [{"mode": "bus", "route": r.route_nm, "from": x["station_nm"], "to": y["station_nm"]}]
            wi, wo = self._walk(da), self._walk(db)
            st_date, by_stop = service_day(arrive_dt - timedelta(minutes=wo))
            off = (st_date - sdate).days * MIN_DAY
            base = {"id": f"{case_id}/bus{i}", "date": st_date.isoformat(), "stage": self.stage, "legs": legs,
                    "arrive_by": by_stop, "party": party, "first_visit": first_visit, "no_alternatives": True}
            r1 = v.verify_case(dict(base, depart_at=max(SERVICE_DAY_START_MIN, by_stop - 180)))
            lfd = (r1.out or {}).get("last_feasible_depart_min")
            if lfd is None:
                continue
            r2 = v.verify_case(dict(base, depart_at=lfd))
            o2 = r2.out or {}
            if o2.get("verdict") != "feasible" or o2.get("eta_min") is None or (o2.get("slack_min") or 0) < 0:
                continue
            eta = int(wi + o2["eta_min"] + wo)
            start = lfd - wi + off
            margin, slack = o2.get("margin_min") or 0, o2.get("slack_min") or 0
            if start + eta + margin + slack != arrive_by:
                continue
            found.append((da + db, i, {
                "eta_min": eta, "uses": uses_of(legs), "_legs": legs, "_route": label_of(legs), "_start": start,
                "_transfers": 0, "_n": 100 + i, "_margin": margin, "_slack": slack,
                "_walk_min": wi + wo, "_walk_m": (da + db) * self.detour,
                "_fare": O.fare_of(v, legs, r2.legs),
                "_severe": [], "_covered": False, "_lr": r2.legs, "_day_type": r2.day_type,
                "_check": {"date": st_date.isoformat(), "legs": legs, "off": off, "walk_place_in": wi,
                           "walk_place_out": wo, "walk_stop_in": 0, "walk_stop_out": 0, "by_station": by_stop}}))
        found.sort(key=lambda t: (t[0], t[1]))
        return [o for _w, _i, o in found]          # 상한은 leg() 가 자격 검사 뒤에 자른다(GPT 23 #2)

    def _bike_direct(self, a_place, b_place, sdate, arrive_by, party, first_visit, case_id):
        """장소 → 장소 따릉이 후보(58). ([후보], None) 또는 ([], 이유 dict). modes 에 bike 가 없으면 ([], None).
        ① 출발 시각 없이 한 번 판정해 소요(eta)·@(margin)를 얻는다 → lfd = 도착 목표 − (eta + @)
        ② lfd 에서 도착 목표를 걸고 다시 판정. **소요·@ 가 ① 과 같을 때만** 싣는다(그때 slack = 0 — 마지막 성립 출발).
           다르면(실시간 대여소 선택이 바뀐 출발 전·진행 중 단계 등) 새 값으로 lfd 를 한 번 더 셈 — 최대 2회 ·
           수렴 안 하면 뺀다(GPT 58 #2·#3 — 「24시간」은 운행 근거일 뿐, 소요의 출발 시각 독립은 **같은 소요가 두 번
           나왔다**는 확인으로만 쓴다).
        대여소 도보·대여·반납은 판정기 자전거 구간 안에 있다(verify_leg_bike) — 장소 도보를 따로 안 더한다.
        ★ 한계(확인 안 한 것): lfd < 04:00 은 운행일 축 경계라 안 본다(GPT 58 #4 — 운행 불가가 아니라 표현 축 제한) ·
          인원수만큼의 대수는 판정기가 안 본다(거치 ≥1 · GPT 58 #6) — label 에 필요 대수만 적는다."""
        if "bike" not in self.modes:
            return [], None
        v = self.v
        legs = [{"mode": "bike",
                 "from": {"lat": a_place["lat"], "lng": a_place["lon"], "name": a_place["name"]},
                 "to": {"lat": b_place["lat"], "lng": b_place["lon"], "name": b_place["name"]}}]
        base = {"id": f"{case_id}/bike", "date": sdate.isoformat(), "stage": self.stage, "legs": legs,
                "party": party, "first_visit": first_visit, "no_alternatives": True}
        r1 = v.verify_case(dict(base, depart_at=arrive_by))
        o1 = r1.out or {}
        if o1.get("verdict") != "feasible" or o1.get("eta_min") is None or o1.get("margin_min") is None:
            return [], {"code": o1.get("code") or "no_data", "reason": "자전거 — " + (o1.get("reason") or r1.reason or "소요를 못 냈다")}
        prev = (o1["eta_min"], o1["margin_min"])
        for _ in range(2):
            lfd = arrive_by - sum(prev)
            if lfd < SERVICE_DAY_START_MIN:
                # 04:00 전 출발은 앞 운행일 축이다 — 정수 분으로 넘기면 판정기가 +24h 로 읽는다(leg() 주석). 안 본다.
                return [], {"code": "no_data", "reason": "자전거 — 출발이 04:00 전(운행일 경계)이라 보지 않는다(표현 축 제한)"}
            r2 = v.verify_case(dict(base, depart_at=lfd, arrive_by=arrive_by))
            o2 = r2.out or {}
            if o2.get("eta_min") is None or o2.get("margin_min") is None:
                return [], {"code": o2.get("code") or "no_data",
                            "reason": "자전거 — 역산 출발에서 다시 보니 소요를 못 냈다: " + (o2.get("reason") or r2.reason or "")}
            got = (o2["eta_min"], o2["margin_min"])
            if got != prev:
                prev = got                      # 소요·@ 가 바뀌었다 — 새 값으로 한 번 더(성립 여부는 그 뒤에 본다)
                continue
            break
        else:
            return [], {"code": "no_data", "reason": "자전거 — 판정마다 소요가 달라 출발 시각을 정하지 못했다"}
        if o2.get("verdict") != "feasible" or (o2.get("slack_min") or 0) != 0:
            return [], {"code": o2.get("code") or "no_data",
                        "reason": "자전거 — 역산 출발에서 다시 보니 성립이 아니다: " + (o2.get("reason") or r2.reason or "")}
        eta, margin = int(prev[0]), prev[1]
        if lfd + eta + margin != arrive_by:
            return [], {"code": "no_data", "reason": "자전거 — 역산 식이 맞지 않는다(내지 않는다)"}
        walk_min = sum((lr.walk_min or 0) for lr in r2.legs)
        n = int(party.get("size") or 1)
        return [{"eta_min": eta, "uses": [], "_legs": legs,
                 "_route": f"자전거(따릉이) {a_place['name']}→{b_place['name']}"
                           + (f" · {n}명 — {n}대 필요" if n > 1 else "")
                           + (" · 대여 가능 여부는 출발 때 확인" if self.stage == "planning" else ""),
                 "_start": lfd, "_transfers": 0, "_n": 200, "_margin": margin, "_slack": 0,
                 "_walk_min": walk_min, "_walk_m": None,           # 대여소 도보 m 은 판정기 밖으로 안 나온다 — 키를 뺀다
                 "_fare": O.fare_of(v, legs, r2.legs), "_severe": [], "_covered": False,
                 "_lr": r2.legs, "_day_type": r2.day_type,
                 "_check": {"date": sdate.isoformat(), "legs": legs, "off": 0, "walk_place_in": 0,
                            "walk_place_out": 0, "walk_stop_in": 0, "walk_stop_out": 0, "by_station": arrive_by}}], None

    def _fold_left(self, left):
        """뺀 후보 목록 정리 — 버스 직행은 도보 짧은 순으로 상한(버스_직행_최대)개만 한 줄씩 적고 나머지는 코드별 개수 한 줄로
        접는다(판정기 bus_rejected 와 같은 뜻 · 역 앞 노선이 많은 곳에서 목록이 덮이지 않게). 내부 참조(_o)는 뺀다."""
        bmax = self.v.R["candidates"]["버스_직행_최대"]["value"]
        buses = sorted((e for e in left if _is_bus(e["_o"])),
                       key=lambda e: (e["_o"]["_walk_m"] if e["_o"]["_walk_m"] is not None else float("inf"), e["_o"]["_n"]))
        drop = {id(e) for e in buses[bmax:]}
        out = [{k: v for k, v in e.items() if k != "_o"} for e in left if id(e) not in drop]
        if drop:
            codes = collections.Counter(e["code"] for e in buses[bmax:])
            out.append({"label": f"그 밖 버스 직행 {len(drop)}개", "code": "bus_more",
                        "reason": "도보 긴 쪽 — " + " · ".join(f"{c} {n}" for c, n in sorted(codes.items()))})
        return out

    def _recheck_at(self, o, start, arrive_by, party, first_visit, case_id):
        """후보 o 를 계획 출발 start(도착 목표 축 분)에서 판정기로 다시 본다. (갱신된 후보, None) 또는 (None, verdict).
        도보 직행은 판정기 밖이라 다시 안 본다(식으로 정해진다 — 더 늦게 떠나면 늦는다)."""
        ck = o.get("_check")
        if ck is None:
            return None, "infeasible"
        dep = start + ck["walk_place_in"] - ck["off"] + ck["walk_stop_in"]
        r = self.v.verify_case({"id": f"{case_id}/at{start}", "date": ck["date"], "stage": self.stage,
                                "legs": ck["legs"], "depart_at": dep,
                                "arrive_by": ck["by_station"] - ck["walk_stop_out"],
                                "party": party, "first_visit": first_visit, "no_alternatives": True})
        out = r.out or {}
        if out.get("verdict") != "feasible":
            return None, ("unknown" if r.verdict == "unknown" else "infeasible")
        if out.get("eta_min") is None or (out.get("slack_min") or 0) < 0:
            return None, "unknown"
        eta = int(ck["walk_place_in"] + ck["walk_stop_in"] + out["eta_min"] + ck["walk_stop_out"] + ck["walk_place_out"])
        margin, slack = out.get("margin_min") or 0, out.get("slack_min") or 0
        if start + eta + margin + slack != arrive_by:
            return None, "unknown"
        sd = date.fromisoformat(ck["date"])
        n = dict(o, eta_min=eta, _start=start, _margin=margin, _slack=slack, _lr=r.legs, _day_type=r.day_type,
                 _severe=O.severe_hits(r.warnings), _fare=O.fare_of(self.v, ck["legs"], r.legs),
                 _covered=O.congestion_checked(self.v, ck["legs"], r.legs, sd, r.day_type))
        return n, None

    def leg(self, a_place, b_place, arrive_dt, party, first_visit, case_id, not_before_dt=None):
        """장소 a → 장소 b, 도착 목표 arrive_dt. ((route_def, 시작 분, 끝 분, 운행일, 뺀 후보), None) 또는 (None, 이유 dict).

        분은 **도착 목표의 운행일 축**이다. 역 도착 목표가 04:00 을 넘어 앞 운행일로 넘어가면(04:00 목표 − 도보 2분)
        판정기에는 그 운행일·그 분으로 넘기고 결과를 이 축으로 되돌린다 — 정수 분을 그대로 넘기면 판정기가
        240 미만을 +24h 로 읽어 그날 밤 막차로 간다(자체 대조 #1).
        not_before_dt: 앞 항목이 끝나는 시각. 이보다 먼저 떠나야 하는 후보는 싣지 않는다(겹치면 코어 등록이 거절한다).
        뺀 후보(left): 성립은 하지만 이동 항목 starts_at 보다 먼저 떠나야 하는 후보 · uses 표기 검사 불통과 후보 —
        [{label, reason}]. 코어로는 안 나간다(봉투 `left_out` · 23 결정 1)."""
        from .geo import meters
        sdate, arrive_by = service_day(arrive_dt)
        nb = None
        if not_before_dt is not None:
            # 출발 하한은 분 **올림**(09:47:30 에 끝나면 09:48 부터) — 도착 목표는 내림이라 둘 다 안전 쪽(GPT #3)
            up = not_before_dt.astimezone(KST)
            if up.second or up.microsecond:
                up = up.replace(second=0, microsecond=0) + timedelta(minutes=1)
            nd, nm_ = service_day(up)
            nb = nm_ + (nd - sdate).days * MIN_DAY
        wlim = self._walk_limit(party)
        buf = self.v.rv("buffer", "by_stage", self.stage)
        opts, left = [], []

        # ① 도보 직행 — 두 장소 직선이 도보 상한 안이면 후보. 여유는 정책 버퍼(수단 무관 · 39 결정 2).
        direct = meters(a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"])
        if direct <= wlim and (self.modes is None or "walk" in self.modes):
            eta = max(1, self._walk(direct))
            opts.append({"eta_min": eta, "uses": [], "_legs": [], "_route": "도보",
                         "_start": arrive_by - eta - buf, "_transfers": 0, "_n": 0,
                         "_margin": buf, "_slack": 0, "_walk_min": eta,
                         "_walk_m": direct * self.detour, "_fare": 0, "_severe": [], "_covered": False})

        # ② 대중교통 — 가까운 역끼리 다목적 후보(판정기 verify_multi) → 후보마다 마지막 성립 출발로 다시 판정
        oa, ob = self._near_station(a_place, wlim), self._near_station(b_place, wlim)
        why = None
        if oa is None or ob is None:
            why = {"code": "no_data", "reason": "도보 상한 안에 지하철역이 없다"
                   + (f"({a_place['name']})" if oa is None else f"({b_place['name']})")}
        elif same_station(oa[0], oa[2], ob[0], ob[2]):          # 55 GPT #2 — 동명이역은 역명이 같아도 다른 역
            why = {"code": "no_data", "reason": f"두 장소의 가장 가까운 역이 같다({oa[0]}) — 도보만 본다"}
        else:
            wa, wb = self._walk(oa[1]), self._walk(ob[1])
            st_date, by_station = service_day(arrive_dt - timedelta(minutes=wb))
            off = (st_date - sdate).days * MIN_DAY            # 역 운행일 축 → 도착 목표 축 (0 또는 −1440)
            probe = {"id": case_id, "date": st_date.isoformat(), "stage": self.stage,
                     "depart_at": max(SERVICE_DAY_START_MIN, by_station - 180), "arrive_by": by_station,
                     "multi": _multi(oa, ob), "party": party, "first_visit": first_visit}
            r = self.v.verify_case(probe)
            n_mode = 0
            for c in r.candidates or []:
                if any(leg_mode(x) == "bike" for x in c["legs"]):
                    continue          # 판정기 multi 의 자전거 후보는 역 기준 — 장소 기준 ④ 로 따로 본다(58)
                if self.modes is not None and any(leg_mode(x) not in self.modes for x in c["legs"]):
                    continue
                n_mode += 1
                if any(leg_mode(x) == "bus" for x in c["legs"]):
                    continue          # 버스 직행은 ③ 에서 **장소 기준**으로 다시 찾는다(역 경유 이중 도보를 없앤다 · 23 결정 4)
                lfd = (c.get("out") or {}).get("last_feasible_depart_min")
                sub_by = by_station - c["walk_out_min"]
                if lfd is None or sub_by < SERVICE_DAY_START_MIN:
                    continue
                sub = {"id": f"{case_id}/{c['n']}", "date": st_date.isoformat(), "stage": self.stage,
                       "legs": c["legs"], "depart_at": lfd + c["walk_in_min"], "arrive_by": sub_by,
                       "party": party, "first_visit": first_visit, "no_alternatives": True}
                r2 = self.v.verify_case(sub)
                o2 = r2.out or {}
                if o2.get("verdict") != "feasible" or o2.get("eta_min") is None or (o2.get("slack_min") or 0) < 0:
                    continue          # 역산 시각으로 다시 봐도 성립이 아니면 싣지 않는다(모르면 뺀다)
                eta = int(wa + c["walk_in_min"] + o2["eta_min"] + c["walk_out_min"] + wb)
                start = lfd - wa + off
                margin, slack = o2.get("margin_min") or 0, o2.get("slack_min") or 0
                if start + eta + margin + slack != arrive_by:
                    continue          # 식이 안 맞으면 어딘가 축이 어긋난 것 — 내지 않는다(늦은 출발을 조용히 내지 않게)
                # 도보 m — 장소↔역(직선×우회) + 환승 거리표 m. 모르는 조각(거리표 밖 환승 · 자전거 대여소 도보)이 있으면 None
                inner = O.transfer_walk_m(self.v, c["legs"])
                walk_m = None if inner is None else (oa[1] + ob[1]) * self.detour + inner
                opts.append({"eta_min": eta, "uses": uses_of(c["legs"]), "_legs": c["legs"],
                             "_route": label_of(c["legs"]), "_start": start,
                             "_transfers": c.get("transfers") or 0, "_n": c["n"],
                             "_margin": margin, "_slack": slack,
                             "_walk_min": wa + wb + (c.get("walk_min") or 0), "_walk_m": walk_m,
                             "_fare": O.fare_of(self.v, c["legs"], r2.legs),
                             "_severe": O.severe_hits(r2.warnings),
                             "_covered": O.congestion_checked(self.v, c["legs"], r2.legs, st_date, r2.day_type),
                             "_lr": r2.legs, "_day_type": r2.day_type,
                             "_check": {"date": st_date.isoformat(), "legs": c["legs"], "off": off,
                                        "walk_place_in": wa, "walk_place_out": wb, "walk_stop_in": c["walk_in_min"],
                                        "walk_stop_out": c["walk_out_min"], "by_station": by_station}})
        # ③ 버스 직행 — **장소 좌표 기준**(23 결정 4). 판정기 verify_multi 의 버스 후보는 역 좌표 기준이라
        #   장소→역→정류장 이중 도보가 붙었다(32 자체 대조 #4 보류). 같은 규칙(정류장_반경_m · route_type_제외 ·
        #   도보 상한은 직선 · 버스_직행_최대 · 성립 후보를 도보 짧은 순)으로 장소에서 바로 찾는다. 판정은 판정기가 한다.
        bus_opts = self._bus_direct(a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id, wlim)
        opts.extend(bus_opts)
        # ④ 자전거 — modes 에 bike 를 줄 때만 · 장소 좌표 기준 · lfd = 목표 − (eta+@)(58 · _bike_direct)
        bike_opts, bike_why = self._bike_direct(a_place, b_place, sdate, arrive_by, party, first_visit, case_id)
        opts.extend(bike_opts)
        if oa is not None and ob is not None and not same_station(oa[0], oa[2], ob[0], ob[2]):
            if not any(o["_legs"] for o in opts):
                if r.candidates and n_mode == 0 and not bus_opts:
                    why = {"code": "no_data", "reason": f"고른 수단({', '.join(sorted(self.modes))}) 안의 후보가 없다"}
                else:
                    why = {"code": (r.out or {}).get("code") or "no_data",
                           "reason": (r.out or {}).get("reason") or r.reason}
        if bike_why is not None and not any(o["_legs"] for o in opts) and not (self.modes & {"subway", "bus"}):
            why = bike_why            # 자전거(·도보)만 고른 구간 — 자전거가 왜 안 됐는지를 이유로

        # uses 자가 검사(41 넘김 · 팀 route_uses.problem) — 불통과 후보는 코어 등록이 통째로 거절하므로 싣지 않는다
        keep = []
        for o in opts:
            bad = O.uses_problems(o["uses"])
            if bad:
                left.append({"_o": o, "label": o["_route"], "code": "uses_format",
                             "reason": "uses 표기 검사 불통과 — " + " · ".join(f"`{u}`: {p}" for u, p in bad)})
            else:
                keep.append(o)
        opts = keep
        if not opts:
            if left:
                return None, {"code": "no_data", "reason": left[0]["reason"]}
            return None, why or {"code": "no_data", "reason": "성립하는 후보가 없다"}
        if bike_why is not None:      # 58 — 자전거를 요청했는데 못 실은 이유를 봉투 left_out 에(코어로는 안 나감)
            left.append({"_o": {"_legs": []}, "label": "자전거(따릉이)", "code": bike_why["code"], "reason": bike_why["reason"]})
        # 계획 수단 — **가장 늦게 떠나도 되는 후보**(동률은 환승 적은 · 소요 짧은 · 생성 순). 순위가 아니라
        #   「일정대로 움직이게」 하나를 고르는 규칙이다. 나머지는 options 에 순위 없이 남는다.
        #   앞 일정 끝(nb)보다 이른 _start 후보도 **버리지 않고** 공통 출발 재판정까지 둔다(GPT 23 2차 #1) —
        #   역산이 실제보다 이르게 나왔을 수 있다. 자격(_start ≥ nb)이 되는 후보가 하나도 없으면 nb 에서 다시 본다.
        def rank(o):
            return (o["_start"], -o["_transfers"], -o["eta_min"], -o["_n"])
        eligible = [o for o in opts if nb is None or o["_start"] >= nb]
        if eligible:
            planned = max(eligible, key=rank)
        else:
            revived = [g for g, _v in (self._recheck_at(o, nb, arrive_by, party, first_visit, case_id) for o in opts)
                       if g is not None]
            if not revived:
                for o in opts:
                    left.append({"_o": o, "label": o["_route"], "code": "before_prev_end",
                                 "reason": f"앞 일정이 {iso_of(sdate, nb)[11:16]}에 끝나는데 그 시각 출발은 재판정에서 불성립 · "
                                           f"{iso_of(sdate, o['_start'])[11:16]} 출발은 성립 확인(역산) · "
                                           f"환승 {o['_transfers']}회 · 소요 {o['eta_min']}분"})
                return None, {"code": "arrive_late",
                              "reason": f"앞 항목이 끝난 뒤({not_before_dt.astimezone(KST):%H:%M}) 떠나서는 "
                                        f"{arrive_dt.astimezone(KST):%H:%M} 도착에 맞는 후보가 없다"}
            back = {g["_n"]: g for g in revived}
            opts = [back.get(o["_n"], o) for o in opts]
            planned = max(revived, key=rank)
        start = planned["_start"]
        end = start + planned["eta_min"]
        # ★ options 는 **이동 항목 starts_at 에 떠나도 성립하는 후보만** 싣는다(32 GPT #1 · 23 결정 1).
        #   후보별 출발 시각 칸을 두지 않는다 — 코어는 후보를 바꿀 때 이동 항목 starts_at 을 그대로 쓴다
        #   (itinerary_changes.py:170 `depart=item.starts_at`). 더 일찍 떠나야 하는 후보는 봉투 `left_out` 에 이유와 함께.
        # ★ 더 이른 _start 후보는 **계획 출발(start)에서 판정기로 다시 본다**(GPT 23 #3) — 성립하면 그 값으로 싣고,
        #   불성립이면 「start 출발은 불성립 · _start 출발은 성립 확인」(그 사이 마지막 성립 시각은 찾지 않는다 · 2차 #2),
        #   판정기가 모르면 「그 시각에서 성립 확인 안 됨」.
        listed = []
        at = iso_of(sdate, start)[11:16]
        for o in opts:
            if o is planned or o["_start"] >= start:
                listed.append(o)
                continue
            got, verdict = self._recheck_at(o, start, arrive_by, party, first_visit, case_id)
            if got is not None:
                listed.append(got)
                continue
            facts = [f"환승 {o['_transfers']}회", f"소요 {o['eta_min']}분"]
            if o["_severe"]:
                facts.append("극심 혼잡 구간 있음")
            need = iso_of(sdate, o["_start"])[11:16]
            if verdict == "unknown":
                left.append({"_o": o, "label": o["_route"], "code": "not_confirmed",
                             "reason": f"이동 출발 {at}에서 성립이 확인되지 않아 뺐다(판정기 재판정 결과 모름) · "
                                       f"{need} 출발은 성립 확인(역산) · " + " · ".join(facts)})
            elif nb is not None and o["_start"] < nb:
                left.append({"_o": o, "label": o["_route"], "code": "before_prev_end",
                             "reason": f"앞 일정이 {iso_of(sdate, nb)[11:16]}에 끝나고, 이동 출발 {at} 출발은 재판정에서 불성립 · "
                                       f"{need} 출발은 성립 확인(역산) · " + " · ".join(facts)})
            else:
                left.append({"_o": o, "label": o["_route"], "code": "earlier_departure",
                             "reason": f"이동 출발 {at} 출발은 재판정에서 불성립 · {need} 출발은 성립 확인(역산 · "
                                       f"그 사이 마지막 성립 시각은 안 찾음 · 후보별 출발 시각 칸 없음) · " + " · ".join(facts)})
        opts = listed
        # 버스 직행 상한(rules candidates.버스_직행_최대) — **실을 자격을 다 본 뒤에** 자른다(GPT 23 #2).
        #   계획 버스는 유지하고, 남은 자리를 도보 짧은 순으로(2차 #6 · 상한이 0 이어도 계획 수단은 남긴다).
        bmax = self.v.R["candidates"]["버스_직행_최대"]["value"]
        buses = [o for o in opts if _is_bus(o)]
        buses.sort(key=lambda o: (o is not planned, o["_walk_m"] if o["_walk_m"] is not None else float("inf"), o["_n"]))
        n_keep = max(bmax, 1 if any(o is planned for o in buses) else 0)
        for o in buses[n_keep:]:
            left.append({"_o": o, "label": o["_route"], "code": "bus_cap",
                         "reason": f"버스 직행 상한 {bmax}개 — 계획 버스 유지 후 남은 자리를 도보 짧은 순으로(순위 아님)"})
        cut = {id(o) for o in buses[n_keep:]}
        opts = [o for o in opts if id(o) not in cut]
        taken = set()
        for o in opts:
            o["id"] = O.make_id(o["_legs"], taken)
            taken.add(o["id"])
        O.add_reasons(opts)
        for o in opts:
            if o["_walk_m"] is not None:
                o["walk_m"] = int(round(o["_walk_m"]))
            if o["_fare"] is not None:
                o["fare_krw"] = int(o["_fare"])
            if self.display and o["_legs"]:
                tc = O.transfer_cars(self.v, self._tc, o["_legs"], o.get("_lr"), o.get("_day_type"))
                if tc:
                    o["transfer_car"] = tc
        left_checks = [{"code": e["code"], "check": e["_o"].get("_check")} for e in left]   # 시험·대조용(trace 에만)
        left = self._fold_left(left)
        if self.trace is not None:
            self.trace.append({"case": case_id, "date": sdate.isoformat(), "arrive_by_min": arrive_by,
                               "planned": planned["id"],
                               "options": [{"id": o["id"], "start_min": o["_start"], "eta_min": o["eta_min"],
                                            "margin_min": o.get("_margin"), "slack_min": o.get("_slack"),
                                            "transfers": o["_transfers"], "check": o.get("_check")}
                                           for o in opts],
                               "left_out": left, "left_checks": left_checks, "start_min": start})
        keys = ("id", "label", "eta_min", "walk_m", "fare_krw", "uses", "transfer_car")
        route = {"from": a_place["name"], "to": b_place["name"], "planned": planned["id"],
                 "options": [{k: o[k] for k in keys if k in o} for o in opts]}
        return (route, start, end, sdate, left), None


CORE_ROUTE_KEYS = ("from", "to", "planned", "options")
CORE_OPTION_KEYS = ("id", "label", "eta_min", "walk_m", "fare_krw", "uses")


def core_routes(routes):
    """코어(CreateTrip)로 옮길 routes — 계약 칸만 남긴다. `display=True` 로 뽑은 표시 전용 필드(transfer_car 등)를 뺀다
    (GPT 23 #10). 기본 호출(display=False)이면 그대로와 같다.
    ★ **이번에 plan() 이 만든 routes 에만** 쓴다 — 기존 팀 routes 와 합친 뒤에 쓰면 `eta_min_if_controlled`·`walk_note`
    같은 팀 칸까지 지운다(2차 #7). 순서: `body["routes"] = {**body.get("routes", {}), **core_routes(out["routes"])}`."""
    return {k: {**{f: r[f] for f in CORE_ROUTE_KEYS if f in r},
                "options": [{f: o[f] for f in CORE_OPTION_KEYS if f in o} for o in r.get("options", [])]}
            for k, r in (routes or {}).items()}


def _key_time(it):
    return (_parse_dt(it["starts_at"]), it.get("seq", 0))


def plan(places, items, party_size=None, constraints=None, *, runtime=None, stage="planning",
         modes=None, trace=None, routes=None, display=False):
    """places·items(·party_size·constraints) → {"items", "routes", "skipped", "basis"}.

    items : 입력 항목 중 이동이 아닌 것을 시각 순으로 두고, **장소가 다른 이웃 둘 사이마다** 이동 항목을 끼운다.
            그 구간에 입력 이동 항목(kind=mobility)이 있었으면 우리 값으로 바꾼다 — 이동 시각의 정본은 우리다.
            우리가 그 구간을 못 만들면(skipped) 입력 이동 항목을 그대로 남긴다.
            seq 는 합친 순서대로 1부터 다시 매긴다. 입력 항목의 다른 칸은 손대지 않는다.
            장소가 없는 항목(자유 시간 등)을 사이에 두면 그 앞뒤는 잇지 않는다 — 어디서 떠나는지 모른다.
            앞 항목이 끝나기 전에 떠나야 하는 후보는 싣지 않는다(코어 등록의 overlap 을 미리 피한다).
    routes: 이번에 만든 이동 항목의 경로 정의만 — **호출 쪽 routes 에 합친다**(update). 통째로 바꾸면
            남겨 둔 입력 이동 항목의 route 키가 사라진다.
    skipped: 이동 항목을 못 만든 구간 [{from, to, code, reason}] — 그 구간은 **값을 빼고** 낸다(모르면 뺀다).
    modes  : 후보 수단 거르기 — None(기본)이면 DEFAULT_MODES(지하철·버스·도보 · 자전거는 "bike" 를 줄 때만 · 56).
             bike 를 주면 장소→장소 따릉이 후보를 싣는다(58 · 출발 = 목표 − (eta+@) · 계획 단계는 실시간 거치 안 봄).
             자전거 테마는 modes=["bike", "walk"] — 여행자가 자전거로 이동한다고 했을 때만(추천하지 않는다 · 58).
             예시 파일은 {"subway", "walk"} 로 뽑았다(노트북 버스 데이터가 옛 판).
    trace  : 리스트를 주면 구간마다 내부 값(시작 분·@·slack·환승)을 적는다 — 코어로는 안 나간다.
    routes : (선택) 호출 쪽이 이미 가진 routes — 그 키와 입력 항목이 참조하는 route 키는 **새 키로 쓰지 않는다**(GPT #2).
    left_out: (봉투) 만든 구간에서 **싣지 않은 후보**와 이유 {route 키: [{label, code, reason}]} — 코어로 안 나간다.
            code `earlier_departure` = 성립하지만 이동 항목 starts_at 보다 먼저 떠나야 한다(후보별 출발 칸 없음 · 23 결정 1)
            · `uses_format` = 팀 route_uses 표기 검사 불통과(코어 등록이 거절한다).
            · (58) label 「자전거(따릉이)」 = bike 를 요청했지만 못 실은 이유(판정기 code 그대로 · 다른 후보는 실렸을 때).
    display: 표시 전용 필드(◆칸 — transfer_car)를 싣는다. 기본 off(답 전엔 만들어만 둔다 · 스펙 v1.4 밖 새 키).
    """
    if runtime is None:
        from .runtime import get_verifier
        runtime = get_verifier(quiet=True)
    P = Planner(runtime, stage=stage, modes=modes, display=display)
    P.trace = trace
    constraints = dict(constraints or {})
    party = party_of(party_size, constraints)
    first_visit = constraints.get("first_visit", True)

    pl = {p["key"]: p for p in (_as_dict(x) for x in places)}
    its = [_as_dict(x) for x in items]
    stay = sorted((it for it in its if it.get("kind") != "mobility"), key=_key_time)
    moves_in = sorted((it for it in its if it.get("kind") == "mobility"), key=_key_time)

    def moves_between(a, b):
        """입력에 있던 이동 항목 중 a 시작 ~ b 시작 사이의 것 — 우리가 그 구간을 못 만들면 그대로 남긴다."""
        lo, hi = _parse_dt(a["starts_at"]), _parse_dt(b["starts_at"])
        return [dict(m) for m in moves_in if lo <= _parse_dt(m["starts_at"]) < hi]

    reserved = set(routes or {}) | {str(it["route"]) for it in its if it.get("route")}
    merged, routes, skipped, left_out = [], {}, [], {}
    if not stay:                                   # 이동 항목만 온 입력 — 그대로 돌려준다(GPT #4)
        merged.extend(dict(m) for m in moves_in)
    else:                                          # 첫 비이동 항목보다 앞선 입력 이동 항목은 그대로 앞에 둔다(GPT #4)
        first = _parse_dt(stay[0]["starts_at"])
        merged.extend(dict(m) for m in moves_in if _parse_dt(m["starts_at"]) < first)

    def skip(a, b, entry):
        skipped.append(entry)
        merged.extend(moves_between(a, b))

    for i, a in enumerate(stay):
        merged.append(dict(a))
        if i + 1 >= len(stay):
            merged.extend(dict(m) for m in moves_in if _parse_dt(m["starts_at"]) >= _parse_dt(a["starts_at"]))
            break
        b = stay[i + 1]
        pa, pb = pl.get(a.get("place")), pl.get(b.get("place"))
        if pa is None or pb is None:
            skip(a, b, {"from": a.get("title"), "to": b.get("title"), "code": "no_data",
                        "reason": "장소가 없는 항목이다 — 어디서 떠나는지(어디로 가는지) 모른다"})
            continue
        if a.get("place") == b.get("place"):
            merged.extend(moves_between(a, b))
            continue
        if pa.get("lat") is None or pb.get("lat") is None:
            skip(a, b, {"from": pa["name"], "to": pb["name"], "code": "no_data", "reason": "좌표가 없다"})
            continue
        got, why = P.leg(pa, pb, _parse_dt(b["starts_at"]), party, first_visit,
                         case_id=f"{a.get('place')}_to_{b.get('place')}",
                         not_before_dt=_parse_dt(a.get("ends_at") or a["starts_at"]))
        if got is None:
            skip(a, b, {"from": pa["name"], "to": pb["name"], **why})
            continue
        route, start, end, sdate, left = got
        key = f"{a.get('place')}_to_{b.get('place')}"
        n = 2
        while key in routes or key in reserved:
            key = f"{a.get('place')}_to_{b.get('place')}_{n}"
            n += 1
        routes[key] = route
        if left:
            left_out[key] = left
        if trace is not None and trace:
            trace[-1]["route"] = key
        merged.append({"seq": 0, "kind": "mobility", "title": f"{pa['name']} → {pb['name']}",
                       "starts_at": iso_of(sdate, start), "ends_at": iso_of(sdate, end), "route": key})
    for n, it in enumerate(merged, 1):
        it["seq"] = n
    return {"items": merged, "routes": routes, "skipped": skipped, "left_out": left_out,
            "basis": {"timetable_built_at": runtime.timetable_built_at, "rules_version": runtime.rules_version,
                      "plan_version": PLAN_VERSION,
                      "decided_at": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:00+09:00")}}


def plan_doc(doc, *, runtime, stage="planning", modes=None, trace=None, display=False):
    """CLI 가 읽는 입력 JSON 한 벌 → plan(). 기존 `routes` 도 넘긴다(그 키를 새 키로 안 쓰게 · GPT 2차 #1)."""
    return plan(doc.get("places") or [], doc.get("items") or [], doc.get("party_size"), doc.get("constraints"),
                runtime=runtime, stage=stage, modes=modes, trace=trace, routes=doc.get("routes"),
                display=display)


def main(argv=None):
    ap = argparse.ArgumentParser(description="이동 값 내놓기 — places·items → 이동 항목 + routes (출력 스펙 v1.3)")
    ap.add_argument("--in", dest="inp", required=True, help="입력 JSON {places, items, party_size?, constraints?, routes?}")
    ap.add_argument("--out", help="출력 JSON 경로(없으면 표준출력)")
    ap.add_argument("--stage", default="planning", choices=("planning", "pre_departure", "in_progress"))
    ap.add_argument("--no-basis", action="store_true", help="basis 를 빼고 낸다(예시 파일을 판 바뀔 때마다 안 흔들리게)")
    ap.add_argument("--modes", nargs="*", help="후보 수단 거르기(subway bus walk bike · 콤마도 됨) — 없으면 subway bus walk(자전거 뺌 · 56)")
    ap.add_argument("--display", action="store_true",
                    help="표시 전용 필드(transfer_car · ◆칸)를 싣는다 — 기본 off, 코어 계약(v1.4) 밖")
    ap.add_argument("--trace", help="구간마다 내부 값(시작 분·@·slack)을 이 JSON 에 적는다 — 대조용")
    a = ap.parse_args(argv)
    if a.modes is not None:           # 58 — `--modes subway,bus,walk,bike` 도 받는다(띄어쓰기와 같다)
        a.modes = [m for x in a.modes for m in x.split(",") if m]
    doc = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    from .runtime import build_verifier
    rt = build_verifier(quiet=True)
    tr = [] if a.trace else None
    res = plan_doc(doc, runtime=rt, stage=a.stage, modes=a.modes, trace=tr, display=a.display)
    if a.trace:
        Path(a.trace).write_text(json.dumps(tr, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.no_basis:
        res.pop("basis", None)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(txt + "\n", encoding="utf-8")
        print(f"[plan] 이동 {sum(1 for x in res['items'] if x['kind'] == 'mobility')} · "
              f"못 만든 구간 {len(res['skipped'])} → {a.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
