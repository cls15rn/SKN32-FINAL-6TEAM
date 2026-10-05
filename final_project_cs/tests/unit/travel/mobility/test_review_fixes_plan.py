# -*- coding: utf-8 -*-
"""이동 계산기 점검(2026-09-29) — 일정 계산(plan) 수정 회귀. 시간표 데이터 없이 작은 판정기로 돈다.

장소 두 곳(P1·P2)은 약 300 m — 도보 직행 후보만 나온다(역 좌표표가 없어 대중교통 후보는 없다).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine.errors import CaseInputError
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR
from app.modules.travel_ops.mobility.engine.runtime import Runtime
from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
PLACES = [{"key": "P1", "name": "첫 장소", "lat": 37.5700, "lon": 126.9800},
          {"key": "P2", "name": "둘째 장소", "lat": 37.5727, "lon": 126.9800},
          {"key": "P0", "name": "좌표 없는 곳", "lat": None, "lon": None}]


def _rt():
    return Runtime(Verifier(Timetable(), None, RULES, set()), timetable_built_at="built:t", rules_version="v", stats={})


def _item(key, place, start, end, kind="activity", **kw):
    return dict({"key": key, "kind": kind, "title": key, "place": place,
                 "starts_at": start, "ends_at": end}, **kw)


# ── #45 날짜 경계 ─────────────────────────────────────────────────────
def test_45_no_move_across_service_days():
    items = [_item("저녁", "P1", "2026-10-05T18:00:00+09:00", "2026-10-05T19:00:00+09:00"),
             _item("아침", "P2", "2026-10-06T09:00:00+09:00", "2026-10-06T10:00:00+09:00")]
    out = P.plan(PLACES, items, 2, {}, runtime=_rt())
    assert not [it for it in out["items"] if it["kind"] == "mobility"], "전날 저녁 → 다음 날 아침 이동을 만들지 않는다"
    assert out["not_linked"] and out["not_linked"][0]["code"] == "day_boundary"


def test_45_service_day_boundary_is_04_00_sharp():
    """운행일 경계는 다음 항목의 **시작 시각**이 04:00:00 인지로 본다(이동 담당·팀장 결정 — 73 후속 · GPT Q5).
    03:59:59 는 전날 운행일이라 잇고, 04:00:00 정각은 다음 운행일 아침이라 잇지 않는다(day_boundary).
    ☆앞서 「도착 마감 1분 전」 기준으로 바꿨다가 이 결정과 반대라 물렸다 — 새벽 04:00 정각 시작 항목 앞 이동은 만들지 않는다."""
    items = [_item("저녁", "P1", "2026-10-05T22:00:00+09:00", "2026-10-05T23:00:00+09:00"),
             _item("새벽", "P2", "2026-10-06T03:59:59+09:00", "2026-10-06T05:00:00+09:00")]
    out = P.plan(PLACES, items, 2, {}, runtime=_rt())
    assert not out["not_linked"], out["not_linked"]
    assert [it for it in out["items"] if it["kind"] == "mobility"], "03:59:59 는 같은 운행일 — 두 장소가 300 m 라 도보 이동이 만들어진다"
    items[1] = _item("새벽", "P2", "2026-10-06T04:00:00+09:00", "2026-10-06T05:00:00+09:00")
    out = P.plan(PLACES, items, 2, {}, runtime=_rt())
    assert [n["code"] for n in out["not_linked"]] == ["day_boundary"], out["not_linked"]
    items[1] = _item("아침", "P2", "2026-10-06T09:00:00+09:00", "2026-10-06T10:00:00+09:00")
    assert P.plan(PLACES, items, 2, {}, runtime=_rt())["not_linked"][0]["code"] == "day_boundary"


def test_45_same_day_move_is_made_and_keeps_input_detail():
    items = [_item("A", "P1", "2026-10-05T10:00:00+09:00", "2026-10-05T11:00:00+09:00"),
             _item("이동", None, "2026-10-05T11:05:00+09:00", "2026-10-05T11:20:00+09:00", kind="mobility",
                   item_id="it-7", detail={"note": "원래 이동"}),
             _item("B", "P2", "2026-10-05T12:00:00+09:00", "2026-10-05T13:00:00+09:00")]
    out = P.plan(PLACES, items, 2, {}, runtime=_rt())
    moves = [it for it in out["items"] if it["kind"] == "mobility"]
    assert len(moves) == 1 and moves[0]["route"] in out["routes"], out
    assert moves[0].get("item_id") == "it-7" and moves[0].get("detail") == {"note": "원래 이동"}, \
        "우리 값으로 바꿔도 입력 이동의 다른 칸을 잃지 않는다"
    assert moves[0]["title"] == "첫 장소 → 둘째 장소", "시각·경로·제목은 우리 값"


# ── #26 못 채운 구간의 옛 이동은 검증 안 됐다고 드러낸다 ────────────────
def test_26_kept_input_move_is_flagged_unverified():
    items = [_item("A", "P1", "2026-10-05T10:00:00+09:00", "2026-10-05T11:00:00+09:00"),
             _item("이동", None, "2026-10-05T11:05:00+09:00", "2026-10-05T11:20:00+09:00", kind="mobility",
                   route="old"),
             _item("C", "P0", "2026-10-05T12:00:00+09:00", "2026-10-05T13:00:00+09:00")]
    out = P.plan(PLACES, items, 2, {}, runtime=_rt(), routes={"old": {"planned": "x", "options": []}})
    assert out["skipped"][0]["code"] == "no_data" and out["skipped"][0]["kept_input_moves"] == 1
    assert out["kept_unverified"] == [{"title": "이동", "route": "old", "starts_at": "2026-10-05T11:05:00+09:00",
                                       "why": "no_data"}]


# ── #38 사고 입력 ────────────────────────────────────────────────────
def test_38_disruptions_reach_every_verify_call():
    rt = _rt()
    seen = []
    real = rt._v.verify_case

    def spy(case):
        seen.append(case.get("disruptions"))
        return real(case)
    planner = P.Planner(rt)
    planner.disruptions = ({"kind": "line_closed", "line": "01호선"},)
    planner.v.verify_case = spy
    planner._vc({"id": "x", "date": "2026-10-05", "depart_at": "10:00", "legs": []})
    assert seen == [[{"kind": "line_closed", "line": "01호선"}]]


def test_38_unknown_disruption_kind_is_refused_upfront():
    with pytest.raises(CaseInputError, match="모르는 사고 kind"):
        P.plan(PLACES, [], 2, {}, runtime=_rt(), disruptions=[{"kind": "bogus"}])


# ── #29 버리는 후보는 이유를 남긴다 ────────────────────────────────────
def test_29_dropped_transit_candidates_are_listed_with_reason():
    planner = P.Planner(_rt(), modes=["subway"])
    planner._near_stations = lambda place, limit: [("역A", 100.0, None) if place["key"] == "P1" else ("역C", 100.0, None)]
    cand = {"n": 1, "legs": [{"line": "01호선", "from": "역A", "to": "역C"}], "walk_in_min": 1, "walk_out_min": 1,
            "out": {}, "reason": "막차 이후"}
    planner._vc = lambda case: SimpleNamespace(candidates=[cand], out={}, reason="후보 없음", verdict="infeasible")
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    got, why = planner.leg(PLACES[0], dict(PLACES[1], lat=37.60, lon=127.05),
                           datetime(2026, 10, 5, 12, 0, tzinfo=kst), {}, True, "P1_to_P2")
    assert got is None
    codes = [e["code"] for e in why.get("left_out", [])]
    assert "no_last_departure" in codes, "앞 판은 역산 못 한 후보를 이유 없이 버렸다"


# ── #20 버스가 섞인 환승 — 확정 규칙(탈것별 요금 합)으로 상한 ─────────────
def test_20_mixed_bus_transfer_gets_sum_of_single_upper_bound():
    from app.modules.travel_ops.mobility.engine import options as O
    from app.modules.travel_ops.mobility.engine.bus import BusRoutes
    from app.modules.travel_ops.mobility.engine.verify_time import LegResult
    routes = [{"route_id": "R1", "route_nm": "101", "route_type_nm": "간선", "term_min": 10,
               "first_time": "05:00", "last_time": "23:00"},
              {"route_id": "R2", "route_nm": "2", "route_type_nm": "마을", "term_min": 10,
               "first_time": "05:00", "last_time": "23:00"}]
    v = Verifier(Timetable(), None, RULES, set(), bus=BusRoutes(routes, []))
    legs = [{"mode": "bus", "route": "101", "from": "갑", "to": "을"}, {"mode": "bus", "route": "2", "from": "을", "to": "병"}]
    lr = [LegResult(1, "버스 101 갑→을", "feasible", "", depart_min=600, arrive_min=610, wait_min=5),
          LegResult(2, "환승 을", "feasible", ""),
          LegResult(3, "버스 2 을→병", "feasible", "", depart_min=615, arrive_min=620, wait_min=3)]
    assert O.fare_of(v, legs, lr) is None, "합성 요금은 여전히 근거가 없다(버스 운임거리)"
    assert O.fare_upper_of(v, legs, lr) == 1500 + 1200, "간선 1,500 + 마을 1,200 = 상한 2,700"
    assert O.fare_upper_of(v, legs[:1], lr[:1]) is None, "버스가 안 섞인(한 번) 경로는 fare_of 의 몫"


# ── #13 장소 사이 도보 ─────────────────────────────────────────────────
#   99(2026-10-04) — 보행망 거리를 끼우던 자리(경로 서버 foot 프로파일)를 지웠다(본인: 자리까지 지움). 「보행망 거리 사용」
#   「길 없음이면 도보 후보 뺌」 시험 둘은 대상 코드와 함께 없어졌다 — 보행 그래프 방에서 다시 만든다. 남는 것: 직선 × 우회계수.
def _walk_leg():
    from datetime import datetime, timedelta, timezone
    planner = P.Planner(_rt(), modes=["walk"])
    kst = timezone(timedelta(hours=9))
    return planner.leg(PLACES[0], PLACES[1], datetime(2026, 10, 5, 12, 0, tzinfo=kst), {}, True, "P1_to_P2")


def test_13_walk_is_straight_line_times_detour():
    from app.modules.travel_ops.mobility.engine.geo import meters
    got, why = _walk_leg()
    walk = next(o for o in got[0]["options"] if o["id"] == "walk")
    straight = meters(PLACES[0]["lat"], PLACES[0]["lon"], PLACES[1]["lat"], PLACES[1]["lon"])
    detour = RULES["transfer"]["stop_station_walk"]["detour_factor"]["value"]
    assert walk["walk_m"] == int(round(straight * detour)), "도보 거리 = 직선 × 우회계수(추정)"
    assert "no_walk_path" not in [e["code"] for e in (got[4] or [])]


def test_13_walk_formula_is_the_same_in_leg_and_earliest():
    """99(GPT #3) — 도보 직행은 leg()(도착 목표 역산)와 earliest()(가장 이른 도착)가 **같은 식**(직선 × 우회계수 · 분 올림 ·
    단계 버퍼)을 쓴다. 보행망 자리를 지우면서 두 군데가 각자 식을 갖게 됐다 — 한쪽만 바뀌면 여기서 운다.
    ☆101(2026-10-05) 두 자리 모두 다시 `_walk_net`(길찾기가 있으면 길 거리 · 없으면 직선 × 우회계수) 한 함수를 부른다(팀장 판).
      이 시험은 길찾기를 끈 판(pytest 기본)이라 값은 그대로 직선 × 우회계수다 — 잠그는 것은 「두 자리가 같은 값」이다."""
    import math
    from datetime import datetime, timedelta, timezone

    from app.modules.travel_ops.mobility.engine.geo import meters
    kst = timezone(timedelta(hours=9))
    planner = P.Planner(_rt(), modes=["walk"])
    straight = meters(PLACES[0]["lat"], PLACES[0]["lon"], PLACES[1]["lat"], PLACES[1]["lon"])
    detour = RULES["transfer"]["stop_station_walk"]["detour_factor"]["value"]
    speed = RULES["measured_baseline"]["kakao_walk_speed_mps"]["value"]
    buf = planner.v.rv("buffer", "by_stage", planner.stage)
    eta = max(1, math.ceil(straight * detour / speed / 60))
    got, _why = planner.leg(PLACES[0], PLACES[1], datetime(2026, 10, 5, 12, 0, tzinfo=kst), {}, True, "P1_to_P2")
    route, start, end = got[0], got[1], got[2]
    assert next(o for o in route["options"] if o["id"] == "walk")["eta_min"] == eta
    assert end - start == eta and 12 * 60 - end == buf, (start, end, buf)
    nb = datetime(2026, 10, 5, 11, 0, tzinfo=kst)
    egot, ewhy = planner.earliest(PLACES[0], PLACES[1], nb, {}, True, "P1_to_P2")
    assert egot is not None, ewhy
    _r, estart, eend, _sd, _left, t = egot
    assert t == 11 * 60 + eta + buf, (t, eta, buf)          # 가장 이른 도착 목표 = 앞 일정 끝 + 도보 + 버퍼
    assert (estart, eend) == (11 * 60, 11 * 60 + eta)
