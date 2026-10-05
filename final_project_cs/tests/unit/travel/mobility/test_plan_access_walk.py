# -*- coding: utf-8 -*-
"""장소 ↔ 역·정류장 걷기를 도로 그래프로 — 문제목록 #13 (2026-10-04).

걷는 거리는 플래너 안에서 「직선 m」로 다뤄지고 `_walk` 가 한 곳에서 × 우회계수 한다. 그래서 길 거리를 우회계수로 나눠
**같은 칸에 넣는다**(`_eff`). 로컬 라우터(`is_local`)가 있을 때만 — 서버 라우터면 후보마다 HTTP 가 나간다.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

from app.modules.travel_ops.mobility.engine import plan as P

DETOUR, SPEED = 1.3, 1.04
HOME = {"name": "집", "lat": 37.5700, "lon": 126.9800}


class FakeRouter:
    def __init__(self, local=True, meters=650.0, optimistic=False, no_path=False):
        self.is_local, self.meters, self.optimistic, self.no_path = local, meters, optimistic, no_path


class FakeBike:
    def __init__(self, router):
        self.router, self.calls, self.last_error = router, [], None

    def available(self):
        return True

    def route(self, prof, la1, lo1, la2, lo2):
        self.calls.append((prof, la1, lo1, la2, lo2))
        if self.router.no_path:
            self.last_error = {"kind": "no_path"}
            return None
        return {"distance_m": self.router.meters, "optimistic": self.router.optimistic}


def _planner(router=None, bus=None, sc=None, ex=None):
    p = object.__new__(P.Planner)
    p.detour, p.speed, p._eff_cache = DETOUR, SPEED, {}
    p.v = SimpleNamespace(bike_router=None if router is None else FakeBike(router), R={}, bus=bus, sc=sc, ex=ex)
    return p


def _eff(p, straight=400):
    return p._eff(37.57, 126.98, 37.5736, 126.98, straight)


def test_routed_distance_replaces_straight_times_detour():
    p = _planner(FakeRouter(meters=650.0))
    assert math.isclose(_eff(p), 650.0 / DETOUR)
    # 플래너가 쓰는 분 = 길 거리 ÷ 속도 — 직선 400 × 1.3 = 520 m 로 셀 때(9분)보다 길 거리(650 m → 11분)가 정직하다
    assert p._walk(_eff(p)) == math.ceil(650.0 / SPEED / 60) == 11
    assert p._walk(400) == 9


def test_a_detour_free_straight_road_is_not_inflated():
    p = _planner(FakeRouter(meters=410.0))               # 직선 400 에 길 410 — 우회계수 1.3 이 과하게 얹히던 자리
    assert p._walk(_eff(p)) == math.ceil(410.0 / SPEED / 60) == 7 and p._walk(400) == 9


def test_server_router_or_no_router_keeps_the_old_formula_and_makes_no_calls():
    srv = _planner(FakeRouter(local=False))
    assert _eff(srv) == 400 and srv.v.bike_router.calls == []
    assert _eff(_planner(None)) == 400


def test_no_path_or_zero_distance_never_turns_into_a_reason_to_drop_the_station():
    assert _eff(_planner(FakeRouter(no_path=True))) == 400, "길을 못 찾은 것은 걸을 수 없다는 근거가 못 된다"
    p = _planner(FakeRouter())
    assert p._eff(37.57, 126.98, 37.57, 126.98, 0) == 0 and p.v.bike_router.calls == []


def test_optimistic_route_uses_the_larger_of_route_and_straight_times_detour():
    p = _planner(FakeRouter(meters=300.0, optimistic=True))          # 길 밖 접근이 길어 짧게 나온 값
    assert math.isclose(_eff(p), 400.0), "max(300, 400×1.3)/1.3 = 400"


def test_same_pair_is_routed_once():
    p = _planner(FakeRouter())
    _eff(p), _eff(p), _eff(p)
    assert len(p.v.bike_router.calls) == 1


def test_near_stations_measure_to_the_exit_by_road_but_keep_candidate_order():
    class SC:
        def stations_near(self, lat, lng, within_m):
            return [(300.0, {"station_nm": "가", "line": "1", "lat": 37.5727, "lng": 126.98}),
                    (500.0, {"station_nm": "나", "line": "2", "lat": 37.5745, "lng": 126.98})]

        def group_lines(self, rec):
            return {rec["line"]}

    class EX:
        def nearest(self, nm, lat, lng, line=None):
            return (250.0, {"lat": 37.5722, "lng": 126.9801}) if nm == "가" else None

    p = _planner(FakeRouter(meters=520.0), sc=SC(), ex=EX())
    p._station_k = lambda: 3
    p._blocked_station = lambda rec: False
    out = p._near_stations(HOME, 1000)
    assert [o[0] for o in out] == ["가", "나"]
    assert all(math.isclose(o[1], 520.0 / DETOUR) for o in out)
    calls = p.v.bike_router.calls
    assert calls[0][3:] == (37.5722, 126.9801), "출구가 있으면 출구까지"
    assert calls[1][3:] == (37.5745, 126.98), "출구표가 없으면 역 좌표까지"


def test_stop_walk_is_remeasured_only_for_the_stop_it_is_given():
    p = _planner(FakeRouter(meters=260.0))
    assert p._stop_walk(HOME, {"lat": 37.5710, "lng": 126.9800}, 120) == round(260.0 / DETOUR) == 200
    assert p._stop_walk(HOME, {"lat": None, "lng": None}, 120) == 120, "좌표 없는 정류장은 직선 그대로"
    assert len(p.v.bike_router.calls) == 1


def test_bus_candidates_are_filtered_by_straight_meters_then_walk_is_routed():
    """코덱스 지적 — 직선 400 · 길 900 · 상한 500 이면 환산값(692)과 비교해 후보가 탈락하면 안 된다. 거른 뒤에만 길로 잰다."""
    r = SimpleNamespace(route_nm="종로01", route_type_nm="간선")
    x = {"station_nm": "승", "lat": 37.5710, "lng": 126.9800}
    y = {"station_nm": "하", "lat": 37.5800, "lng": 126.9900}
    far = {"station_nm": "먼", "lat": 37.5900, "lng": 126.9900}

    class Bus:
        def routes_between(self, *a):
            return [(r, x, y, 4, 400, 300), (r, x, far, 5, 400, 900)]       # 둘째는 직선 상한(500) 밖

    p = _planner(FakeRouter(meters=900.0), bus=Bus())
    p.v.rv = lambda *a: [] if a[-1] == "route_type_제외" else 800
    p.modes = {"bus"}
    p.stage, p.disruptions = "planning", []
    calls = []
    p._vc = lambda case: calls.append(case) or SimpleNamespace(out=None, reason="stub", legs=None, day_type=None)
    left = []
    from datetime import datetime, timezone
    p._bus_direct(HOME, {"name": "목적", "lat": 37.58, "lon": 126.99}, datetime(2026, 10, 7, 12, tzinfo=timezone.utc),
                  __import__("datetime").date(2026, 10, 7), 720, 1, False, "c", 500, left)
    assert len(calls) == 1, "상한 안 후보(직선 400/300)는 판정까지 갔다 — 길 900 m 의 환산값 692 가 상한 500 을 넘어도 탈락 아님"
    assert len(p.v.bike_router.calls) == 2, "거른 뒤 통과한 후보의 두 걸음만 라우팅(탈락 후보는 안 부른다)"
