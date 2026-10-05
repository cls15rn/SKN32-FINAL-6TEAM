# -*- coding: utf-8 -*-
"""101(2026-10-05) 합치기 — 팀장 이동 작업(길찾기 · 걷기 길 기준 · 택시 후보) 위에 이동 담당 쪽이 더한 것만 잠근다. 자료 없이 돈다.

  ① 걸음 길이 없는 판(road_graph_v1)으로는 걷기를 재지 않는다 — 조용히 길게 재지 않고 종전 식(직선 × 우회계수)
  ② 택시는 따로 뒤 무리 — 앞 무리(지하철·버스·지하철+버스·도보·자전거 · 한 무리 · 「소요 + 일찍 떠나는 분」)에서 못 고를 때만
  ③ 택시가 계획 수단이면 수단별 칸의 planned_mode 는 taxi
  ④ 택시 서비스(_LazyCar)가 도착 목표 역산(arrive_by)을 넘겨주고, 속도 자료가 없으면 갈래 no_profile
팀장 시험(`test_graph_router` · `test_plan_access_walk` · `test_plan_taxi` · `test_route_shape` · `test_fare_est_v1`)은 그대로 둔다.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine.car import RouterDown
from app.modules.travel_ops.mobility.engine.graph_router import GraphRouter


# ── ① 걸음 판 여부 ─────────────────────────────────────────────────────────
def _graph_dir(tmp_path, name, version=None):
    d = tmp_path / name
    d.mkdir()
    if version:
        (d / "MANIFEST.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return d


def test_foot_ok_is_read_without_loading_the_graph(tmp_path):
    assert GraphRouter(_graph_dir(tmp_path, "road_graph_v1")).foot_ok is False, "차도·자전거만 있는 판"
    assert GraphRouter(_graph_dir(tmp_path, "road_graph_v2")).foot_ok is True, "명세가 없으면 폴더 이름으로"
    assert GraphRouter(_graph_dir(tmp_path, "g_a", "road_graph_v2")).foot_ok is True, "명세의 version 이 먼저"
    assert GraphRouter(_graph_dir(tmp_path, "g_b", "road_graph_v1")).foot_ok is False
    assert GraphRouter(records={"nodes": [], "edges": []}).foot_ok is True, "합성 자료(시험)는 걸을 수 있는 것으로"


class _Bike:
    """v.bike_router 대역 — 부른 횟수를 센다."""

    def __init__(self, router, dist):
        self.router, self.dist, self.calls = router, dist, 0
        self.last_error = None

    def available(self):
        return True

    def route(self, profile, lat1, lng1, lat2, lng2):
        self.calls += 1
        return {"distance_m": self.dist, "time_s": 1, "basis": "로컬 도로그래프", "source_id": "x"}


def _planner(router, dist=900.0):
    p = object.__new__(P.Planner)
    p.detour, p.speed, p._eff_cache = 1.4, 1.04, {}
    p.v = SimpleNamespace(bike_router=_Bike(router, dist), R={})
    return p


def test_walk_is_not_measured_on_a_graph_without_foot_paths():
    v1 = SimpleNamespace(is_local=True, foot_ok=False)
    p = _planner(v1)
    assert p._foot_router() is None
    assert p._eff(37.5, 127.0, 37.501, 127.001, 300) == 300, "길찾기를 부르지 않고 직선 그대로(직선 × 우회계수 식으로 간다)"
    assert p._walk_net({"lat": 37.5, "lon": 127.0}, {"lat": 37.501, "lon": 127.001}, 300) == (300 * 1.4, False)
    assert p.v.bike_router.calls == 0

    v2 = SimpleNamespace(is_local=True, foot_ok=True)
    q = _planner(v2)
    assert q._eff(37.5, 127.0, 37.501, 127.001, 300) == pytest.approx(900.0 / 1.4), "길 거리 ÷ 우회계수 = 직선 환산"
    assert q._walk_net({"lat": 37.5, "lon": 127.0}, {"lat": 37.501, "lon": 127.001}, 300) == (900.0, False)

    server = SimpleNamespace(is_local=False)                 # 로컬이 아닌 길찾기는 걷기에 쓰지 않는다(호출 비용)
    assert _planner(server)._foot_router() is None
    plain = SimpleNamespace(is_local=True)                   # foot_ok 를 말하지 않는 대역 = 걸을 수 있는 것으로(팀장 시험 대역)
    assert _planner(plain)._foot_router() is not None


# ── ② ③ 택시는 뒤 무리 ─────────────────────────────────────────────────────
def _opt(start, eta, *, taxi=False, legs=(), transfers=0, n=0):
    o = {"_start": start, "eta_min": eta, "_transfers": transfers, "_n": n, "_legs": list(legs)}
    if taxi:
        o["_taxi"] = True
    return o


def test_taxi_never_beats_the_main_group_even_when_it_is_far_quicker():
    subway = _opt(600, 60, legs=[{"line": "02호선", "from": "A", "to": "B"}])
    taxi = _opt(645, 15, taxi=True, n=300)                  # 「소요 + 일찍 떠나는 분」으로는 택시가 훨씬 작다
    planned, _ = P.Planner._choose_planned([taxi, subway], None, lambda o: None, 5)
    assert planned is subway, "택시는 앞 무리에서 못 고를 때만 본다(본인 10/5 · 팀장 #47)"
    planned, _ = P.Planner._choose_planned([taxi, subway], 620, lambda o: None, 5)      # 앞 일정이 620 에 끝남 → 지하철 못 맞춤
    assert planned is taxi and P.planned_mode_of(planned) == "taxi"
    planned, _ = P.Planner._choose_planned([taxi, subway], 650, lambda o: None, 5)      # 택시도 못 맞춤
    assert planned is None


def test_taxi_is_not_rechecked_by_the_verifier():
    asked = []
    taxi = _opt(600, 15, taxi=True)
    planned, revived = P.Planner._choose_planned([taxi], 610, lambda o: asked.append(o), 5)
    assert planned is None and revived == [] and asked == [], "택시는 판정기 밖 — 다시 판정을 부르지 않는다"


def test_known_modes_and_version():
    assert P.KNOWN_MODES == frozenset({"subway", "bus", "walk", "bike", "taxi"})
    assert "taxi" not in P.DEFAULT_MODES and P.PLAN_VERSION == "plan-v2.6"


# ── ④ 택시 서비스 ──────────────────────────────────────────────────────────
def test_lazy_car_passes_arrive_by_and_says_no_profile(tmp_path, monkeypatch):
    from app.modules.travel_ops.mobility.engine import paths, runtime
    before = (paths.DATA_DIR, paths.SOURCE)
    try:
        paths.configure(str(tmp_path))                       # 속도 자료가 없는 빈 자료 폴더
        car = runtime._LazyCar(router=object(), rules={}, holidays=frozenset())
        for call in (lambda: car.leg((127.0, 37.5), (127.1, 37.5), None),
                     lambda: car.arrive_by((127.0, 37.5), (127.1, 37.5), None)):
            with pytest.raises(RouterDown) as ex:
                call()
            assert ex.value.code == "no_profile"
    finally:
        if before[0] is None:
            paths.disable()
        else:
            paths._layout(before[0], before[1])


# ── GPT 대조 반영(2·3·4) — 택시 후보는 확인된 출발 · 접근 걷기 · 이동 출발에서 다시 잼 ──────────────────
import datetime as _dt  # noqa: E402

A_, B_ = {"name": "가", "lat": 37.5796, "lon": 126.9770}, {"name": "나", "lat": 37.5433, "lon": 127.0557}
DAY = _dt.date(2026, 10, 7)


class _JumpCar:
    """leg 만 있는 대역 — 09:50 부터는 60분, 그 전은 시각마다 다르게(역산이 수렴하지 않는 모양)."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def leg(self, s, e, depart, taxi=False, kind="중형"):
        m = depart.hour * 60 + depart.minute
        self.calls.append(m)
        sec = next(v for lim, v in self.table if m < lim) * 60
        return {"distance_m": 5000.0, "topis_time_s": sec, "fare_won": 7000 + m, "grade": "추정"}


def _taxi_planner(car):
    p = object.__new__(P.Planner)
    p.v, p.modes = SimpleNamespace(car=car), {"taxi", "walk"}
    return p


def test_taxi_option_never_returns_a_departure_it_did_not_measure():
    """GPT 2 — 10:00 목표 · 버퍼 5: 09:30 소요 10분 → 09:45 소요 1분 → 09:54 를 재지 않고 내면 실제는 60분(10:54 도착)이다."""
    car = _JumpCar([(9 * 60 + 40, 10), (9 * 60 + 50, 1), (24 * 60, 60)])
    left = []
    assert _taxi_planner(car)._taxi_option(A_, B_, DAY, 10 * 60, 5, left) is None
    assert left[-1]["code"] == "taxi_unconfirmed" and car.calls[-1] == 9 * 60 + 54, (left, car.calls)
    steady = _JumpCar([(24 * 60, 30)])                                        # 수렴하면 앞 판과 같다(두 번 · 재확인 호출 없음)
    o = _taxi_planner(steady)._taxi_option(A_, B_, DAY, 12 * 60, 10, [])
    assert o["_start"] == 12 * 60 - 30 - 10 and o["eta_min"] == 30 and len(steady.calls) == 2


class _ArriveCar(_JumpCar):
    """도착 역산을 하는 서비스 대역 — 접근 걷기 13.5분(양끝 300 m)."""

    def arrive_by(self, s, e, arrive, taxi=True, kind="중형"):
        self.asked = arrive
        dep = arrive - _dt.timedelta(minutes=40)
        return {"depart_dt": dep, "topis_time_s": 20 * 60, "worst_time_s": 40 * 60, "access_m": 600.0, "access_s": 810,
                "distance_m": 9000.0, "fare_won": 11000, "grade": "추정"}


def test_taxi_option_uses_the_same_arrive_by_as_the_taxi_slot_and_counts_the_walk():
    """GPT 3 — 계획 택시도 접근 걷기를 넣는다(수단별 택시 칸과 같은 계산). 도착 목표 = 다음 일정 시작 − 버퍼."""
    car = _ArriveCar([(24 * 60, 20)])
    o = _taxi_planner(car)._taxi_option(A_, B_, DAY, 12 * 60, 10, [])
    assert car.asked == _dt.datetime(2026, 10, 7, 11, 50) and car.calls == [], "역산이 있으면 leg 로 추측하지 않는다"
    assert o["_start"] == 11 * 60 + 10 and o["eta_min"] == 34, "예정 = 도로 20분 + 접근 걷기 13.5분(올림)"
    assert o["_walk_m"] == 600 and o["_walk_min"] == 14 and "걷는 약 600m" in o["_route"]
    assert o["_start"] + o["eta_min"] + o["_margin"] == 12 * 60 and o["_margin"] >= 10


def test_taxi_is_measured_again_at_the_planned_departure():
    """GPT 4 — 10:50 출발 5분짜리 택시를 10:00 출발 이동의 대안으로 그대로 싣지 않는다. 그 시각으로 다시 잰다."""
    car = _JumpCar([(10 * 60 + 30, 70), (24 * 60, 5)])
    p = _taxi_planner(car)
    o = p._taxi_option(A_, B_, DAY, 11 * 60, 5, [])
    assert o["_start"] == 10 * 60 + 50 and o["eta_min"] == 5
    assert p._taxi_recheck(o, DAY, 10 * 60, 11 * 60, 5) is None, "10:00 출발은 70분 — 못 닿는다 → 싣지 않는다"
    g = p._taxi_recheck(o, DAY, 10 * 60 + 40, 11 * 60, 5)
    assert g["_start"] == 10 * 60 + 40 and g["eta_min"] == 5 and g["_fare"] == 7000 + 640, "요금도 그 시각 값"
    assert g["_start"] + g["eta_min"] + g["_margin"] == 11 * 60 and o["_start"] == 10 * 60 + 50, "원래 후보는 안 바꾼다"


# ── 전체층(실데이터) — 서버 경로(길찾기 켬)의 예시 일정 ───────────────────────────
@pytest.mark.mobility_full
def test_full_server_path_example_itinerary_is_all_planned_with_road_walks():
    """서버는 길찾기를 켠 채 돈다(설정 mobility_local_router 기본 켬). 그 경로로 예시 일정 4구간이 **다 성립**하고, 걷기가 길 기준이라
    끈 판(pytest 기본)보다 도보가 길고 출발이 같거나 이르다. 차도만 있는 v1 로 걷기를 재던 판에서는 4구간 중 2구간이 「못 만듦」
    이었다(팀장 판 확인 방 10/4 실측) — 그 모양이 돌아오면 여기가 운다. 값은 road_graph_v2(OSM 2026-09-18)에서 잠갔다."""
    import gc
    from pathlib import Path

    from app.modules.travel_ops.mobility.engine import paths
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    before = (paths.DATA_DIR, paths.SOURCE)
    ex = json.loads((Path(__file__).resolve().parent / "plan_example_in_v1.json").read_text(encoding="utf-8"))

    def run(on):
        try:
            rt = build_verifier(quiet=True, seoul_key="", local_router=on)
        except RuntimeError as e:
            pytest.skip(f"data not present: {e}")
        if on and not (rt.stats["router"] == "local" and rt.stats["car_router"] == "road_graph_v2"):
            pytest.skip(f"road_graph_v2 not present: {rt.stats.get('car_router')}")
        out = P.plan(ex["places"], ex["items"], ex.get("party_size"), ex.get("constraints"), runtime=rt)
        del rt
        gc.collect()
        return out

    try:
        off, on = run(False), run(True)
    finally:
        if before[0] is not None:
            paths._layout(before[0], before[1])
    moves = lambda o: [it for it in o["items"] if it.get("kind") == "mobility"]        # noqa: E731
    assert on["skipped"] == [] and len(moves(on)) == len(moves(off)) == 4
    assert [m["starts_at"][11:16] for m in moves(off)] == ["09:18", "12:36", "17:24", "19:16"]
    assert [m["starts_at"][11:16] for m in moves(on)] == ["09:17", "12:34", "17:22", "19:14"]

    def walk(o, m):
        r = o["routes"][m["route"]]
        return next(x for x in r["options"] if x["id"] == r["planned"])["walk_m"]
    for a, b in zip(moves(off), moves(on)):
        assert b["starts_at"] <= a["starts_at"] and walk(on, b) > walk(off, a), (a["starts_at"], b["starts_at"])
