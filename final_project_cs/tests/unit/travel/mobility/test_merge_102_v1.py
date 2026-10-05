# -*- coding: utf-8 -*-
"""합치기 2(102 · 2026-10-05) — 걷기 마무리 + 요금 거리 원천 순서.

① 걷기 남은 자리: 버스 환승·지하철+버스 후보의 정류장 쪽 걷기와 정류장↔역·정류장↔정류장 환승 걷기도 길 기준(`plan._cand_walks` ·
   판정기 `_stop_station_walk`·`_bus_bus_walk` — 재는 함수는 `verify_time.foot_walk` 하나).
② 조용히 되돌리지 않기: 길찾기가 못 찾은 까닭을 가른다(지도 밖 · 경로 없음 · 그래프 없음) · 직선의 2배 넘는 길은 되돌리지 않고
   경고 · 계획 수단의 걷기 근거를 봉투 `walk_basis` 에.
③ 요금 거리 원천 순서: 역 순서 표 → 공표 역간거리 표(`station_gap_v1.jsonl`) → 선로 길이 추정. 요금을 내는 노선 밖은 하한에만.

길찾기를 끈 실행(pytest 기본)은 앞 판과 같은 값이어야 한다 — 그 잠금은 회귀 199건·골든이 한다. 여기는 켠 판의 뜻을 대역으로 잠근다.
"""
from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from app.modules.travel_ops.mobility.engine import options as O
from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine import verify_time as VT
from app.modules.travel_ops.mobility.engine.bike import BikeRouter
from app.modules.travel_ops.mobility.engine.car import RouterDown
from app.modules.travel_ops.mobility.engine.geo import meters
from app.modules.travel_ops.mobility.engine.line_order import LineOrder, _load_gap_edges

DETOUR, SPEED = 1.4, 1.04
A = {"name": "가 장소", "lat": 37.5700, "lon": 126.9800}
B = {"name": "나 장소", "lat": 37.5800, "lon": 126.9900}


class FakeRouter:
    """graph_router 대역 — 정해 둔 거리로 답하거나, 정해 둔 문장으로 RouterDown 을 낸다(길찾기가 내는 문장 머리 그대로)."""
    is_local = True
    source_id = "osm_road_graph_v2@test"
    basis = "로컬 도로그래프"

    def __init__(self, dist=650.0, fail=None, optimistic=False, foot_ok=True):
        self.dist, self.fail, self.optimistic, self.foot_ok, self.calls = dist, fail, optimistic, foot_ok, 0

    def route(self, s, e, profile="car", via=None):
        self.calls += 1
        if self.fail:
            raise RouterDown(self.fail)
        return {"paths": [{"distance": self.dist, "time": 1000,
                           "quality": {"access_max_m": 5.0, "arterial_pct": 0.0, "optimistic": self.optimistic}}]}


def _v(router=None, **kw):
    return SimpleNamespace(bike_router=None if router is None else BikeRouter(router), R={}, walk_log={}, **kw)


def _planner(router=None, **kw):
    p = object.__new__(P.Planner)
    p.detour, p.speed = DETOUR, SPEED
    p.v = _v(router, **kw)
    return p


# ── ② 못 찾은 까닭을 가른다 ───────────────────────────────────────────────────
@pytest.mark.parametrize("msg, kind", [
    ("out_of_area: 좌표(127.00000,37.50000) 근처 1200 m 안에 foot 길이 없다", "out_of_area"),
    ("no_path: 이 그래프에서 두 지점이 이어지지 않는다(길이 없다는 뜻이 아니다)", "no_path"),
    ("도로 그래프 자료가 없다(/x/road_graph_v2)", "no_graph"),
    ("지원하지 않는 프로파일: boat", "router_down")])
def test_router_failures_are_told_apart(msg, kind):
    br = BikeRouter(FakeRouter(fail=msg))
    assert br.route("foot", 37.5, 127.0, 37.501, 127.001) is None
    assert br.last_error["kind"] == kind, "앞 판은 전부 router_down 이었다"


def test_foot_walk_measures_by_road_and_keeps_the_basis():
    r = FakeRouter(dist=650.0)
    v = _v(r)
    walk_m, info = VT.foot_walk(v, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR, what=("access", "가", "나"))
    assert walk_m == 650.0 and info["basis"] == "road" and info["why"] is None
    assert info["source_id"] == "osm_road_graph_v2@test" and info["grade"] == "추정"
    assert info["straight_m"] == 400 and info["ratio"] == 1.62 and info["detour_over"] is False
    assert v.walk_log[(("access", "가", "나"), 37.57, 126.98, 37.5736, 126.98)] is info, "이름표 + 좌표로 근거를 적어 둔다"
    VT.foot_walk(v, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)
    assert r.calls == 1, "같은 좌표 쌍은 한 번만 잰다(판정기 캐시)"


@pytest.mark.parametrize("fail, why", [("out_of_area: x", "out_of_area"), ("no_path: x", "no_path"),
                                         ("도로 그래프 자료가 없다(x)", "no_graph")])
def test_foot_walk_falls_back_to_straight_with_the_reason(fail, why):
    walk_m, info = VT.foot_walk(_v(FakeRouter(fail=fail)), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)
    assert walk_m == 400 * DETOUR and info["basis"] == "straight" and info["why"] == why
    assert info["source_id"] is None and info["detour_over"] is False


def test_foot_walk_without_a_foot_router_says_why():
    assert VT.foot_walk(_v(None), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)[1]["why"] == "no_router"
    v1 = FakeRouter(foot_ok=False)                           # 차도만 있는 판(v1) — 걷기를 재지 않는다(101)
    walk_m, info = VT.foot_walk(_v(v1), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)
    assert (walk_m, info["why"], v1.calls) == (400 * DETOUR, "not_foot_graph", 0)


def test_long_detour_is_not_reverted_only_flagged():
    """본인 10/3 결정 — 길을 찾았으면 직선의 2배를 넘게 돌아도 되돌리지 않는다(경고만). 2배 = 걷는 거리 ÷ 직선."""
    walk_m, info = VT.foot_walk(_v(FakeRouter(dist=900.0)), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)
    assert walk_m == 900.0 and info["basis"] == "road" and info["detour_over"] is True and info["ratio"] == 2.25
    assert VT.foot_walk(_v(FakeRouter(dist=800.0)), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)[1]["detour_over"] is False, \
        "정확히 2배는 넘는 것이 아니다"
    assert VT.WALK_DETOUR_WARN_RATIO_PROPOSED == 2.0 and VT.WALK_DETOUR_WARN_EXTRA_M_PROPOSED == 200.0
    # (본인 10/5) 배수만으로는 짧은 걸음까지 울린다 — 종전 식(직선 × 1.4)보다 200 m 넘게 더 걸을 때만
    short = VT.foot_walk(_v(FakeRouter(dist=132.0)), 37.57, 126.98, 37.5703, 126.98, 31, DETOUR)[1]
    assert short["ratio"] == 4.26 and short["detour_over"] is False, "직선 31 m → 길 132 m 는 4배여도 경고가 아니다"
    edge = VT.foot_walk(_v(FakeRouter(dist=759.0)), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)[1]
    assert edge["detour_over"] is False and VT.foot_walk(_v(FakeRouter(dist=761.0)), 37.57, 126.98, 37.5736, 126.98, 400,
                                                         DETOUR)[1]["detour_over"] is False, "2배(800) 를 안 넘는다"
    assert VT.foot_walk(_v(FakeRouter(dist=801.0)), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)[1]["detour_over"] is True
    v = _v(FakeRouter(dist=900.0))
    v.R = {"transfer": {"stop_station_walk": {"detour_warn_ratio": {"value": 3.0}}}}
    assert VT.foot_walk(v, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)[1]["detour_over"] is False, "규칙 값이 생기면 그 값이 이긴다"


def test_optimistic_route_takes_the_larger_side():
    walk_m, info = VT.foot_walk(_v(FakeRouter(dist=300.0, optimistic=True)), 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)
    assert walk_m == 400 * DETOUR and info["basis"] == "road" and info["optimistic"] is True


def test_walk_candidate_is_dropped_only_when_the_router_says_no_path():
    """「길 없음이면 도보 후보를 뺀다」 가지 — 앞 판은 길찾기의 no_path 가 router_down 으로 뭉쳐 한 번도 안 탔다."""
    straight = meters(A["lat"], A["lon"], B["lat"], B["lon"])
    assert _planner(FakeRouter(fail="no_path: x"))._walk_net(A, B, straight) == (None, True)
    for fail in ("out_of_area: x", "도로 그래프 자료가 없다(x)"):
        assert _planner(FakeRouter(fail=fail))._walk_net(A, B, straight) == (straight * DETOUR, False), "길이 없다는 근거가 아니다"
    assert _planner(None)._walk_net(A, B, straight) == (straight * DETOUR, False)
    assert _planner(FakeRouter(dist=2000.0))._walk_net(A, B, straight) == (2000.0, False)


# ── ① 환승·혼합 후보의 걷기 ───────────────────────────────────────────────────
def _stop_near(place, north_m, nm="정류장"):
    return {"station_nm": nm, "lat": place["lat"] + north_m / 111320, "lng": place["lon"]}


def _mc(walk_in, walk_out, link=100.0, board=None):
    legs = [{"mode": "bus", "route": "1", "from": "정류장", "to": "환승 정류장"}, {"line": "02호선", "from": "역", "to": "끝역"}]
    mc = SimpleNamespace(legs=legs, walk_in_m=walk_in, walk_out_m=walk_out, link_m=link)
    if board is not None:
        mc.board = board
    return mc


def _xfer_ok(a, b, party):
    return {"walk_m": 310.0, "walk_basis": _info("road", 80, 310)}


def test_cand_walks_without_a_router_are_the_old_values():
    p = _planner(None)
    assert p._cand_walks(_mc(200.0, 300.0), A, B, {})[:4] == (200.0, 300.0, math.ceil(100.0 * DETOUR / SPEED / 60), None)


def test_cand_walks_route_the_stop_side_and_take_transfers_from_the_verifier():
    row = _stop_near(A, 200)
    straight = meters(A["lat"], A["lon"], row["lat"], row["lng"])
    p = _planner(FakeRouter(dist=420.0), _stop_station_walk=_xfer_ok, _bus_bus_walk=_xfer_ok)
    w_in, w_out, link_min, link_m, ev = p._cand_walks(_mc(straight, 300.0, board=row), A, B, {})
    assert w_in == round(420.0 / DETOUR), "정류장 쪽(첫 구간이 버스)은 길 기준 — 직선 환산 m"
    assert w_out == 300.0, "역 쪽은 부르는 쪽이 이미 길 기준으로 준 값이다 — 다시 재지 않는다"
    assert (link_m, link_min) == (310.0, math.ceil(310.0 / SPEED / 60)), "환승 걷기는 판정기 값 그대로"
    assert ev["in"] == (row["lat"], row["lng"]) and ev["out"] is None
    assert [(a, b, i["walk_m"]) for a, b, i in ev["xfer"]] == [("환승 정류장", "역", 310.0)], "환승 근거를 후보가 들고 다닌다"


def test_cand_stop_uses_the_row_the_candidate_was_built_with_not_a_name_lookup():
    """GPT 102 #3 — 장소의 북쪽 200 m 와 남쪽 200 m 에 이름이 같은 정류장이 있으면 장소까지의 거리는 같다. 거리 차로는 못 가른다:
    후보를 만든 행(board)까지 잰다. 판정기의 이름 찾기(_bus_stop_row)가 남쪽 행을 돌려줘도 쓰지 않는다."""
    north, south = _stop_near(A, 200), _stop_near(A, -200)
    asked = []
    p = _planner(FakeRouter(dist=420.0), _bus_stop_row=lambda leg, which: asked.append(which) or south,
                 _stop_station_walk=_xfer_ok, _bus_bus_walk=_xfer_ok)
    _w, _o, _lm, _l, ev = p._cand_walks(_mc(200.0, 300.0, board=north), A, B, {})
    assert ev["in"] == (north["lat"], north["lng"]) and asked == [], "이름으로 다시 찾지 않았다"
    assert [k[3:] for k in p.v.walk_log] == [(north["lat"], north["lng"])], "길을 잰 곳은 북쪽 정류장"


def test_cand_stop_without_a_known_row_is_not_routed():
    r = FakeRouter(dist=420.0)
    p = _planner(r, _bus_stop_row=lambda leg, which: _stop_near(A, 200), _stop_station_walk=_xfer_ok, _bus_bus_walk=_xfer_ok)
    w_in, _o, _lm, _l, ev = p._cand_walks(_mc(120.0, 300.0), A, B, {})          # 행도 순번도 없는 후보
    assert (w_in, ev["in"], r.calls) == (120.0, None, 0), "어느 정류장인지 모르면 재지 않는다(직선 그대로)"
    mc = _mc(200.0, 300.0)
    mc.legs[0].update(from_seq=3, to_seq=9)                                        # 환승 후보 — 순번이 있어 판정기가 그 행을 쓴다
    assert p._cand_walks(mc, A, B, {})[4]["in"] is not None and r.calls == 1


def test_cand_walks_keep_the_old_formula_when_the_verifier_cannot_measure_a_transfer():
    row = _stop_near(A, 200)
    none = lambda a, b, party: {"walk_m": None}          # noqa: E731
    p = _planner(FakeRouter(dist=420.0), _stop_station_walk=none, _bus_bus_walk=none)
    out = p._cand_walks(_mc(meters(A["lat"], A["lon"], row["lat"], row["lng"]), 300.0, board=row), A, B, {})
    assert out[2:4] == (math.ceil(100.0 * DETOUR / SPEED / 60), None) and out[4]["xfer"] == [], "좌표 없는 환승 — 종전 식"


# ── ② 걷기 근거(봉투) ─────────────────────────────────────────────────────────
def _info(basis, straight, walk, why=None, over=False, optimistic=False):
    return {"basis": basis, "why": why, "source_id": "g" if basis == "road" else None, "grade": "추정",
            "straight_m": float(straight), "walk_m": float(walk), "ratio": round(walk / straight, 2),
            "optimistic": optimistic, "detour_over": over}


def _key(what, pt):
    return (what, 0.0, 0.0, pt[0], pt[1])


def test_walk_basis_lists_the_parts_of_the_planned_option_with_warnings():
    p = _planner(FakeRouter())
    p.v.walk_log.update({_key(("stop", "가 장소", "정류장"), (1.0, 1.0)): _info("road", 200, 520, over=True),
                         _key(("station", "나 장소", "끝역"), (2.0, 2.0)): _info("straight", 300, 420, why="out_of_area"),
                         _key(("station", "가 장소", "다른 역"), (3.0, 3.0)): _info("road", 100, 130)})   # 다른 후보의 것 — 안 실린다
    o = {"_legs": _mc(0, 0).legs, "_wev": {"in": (1.0, 1.0), "out": None, "xfer": [("환승 정류장", "역", _info("road", 80, 110))]}}
    wb = p._walk_basis(o, A, B)
    assert [(x["part"], x["from"], x["to"], x["basis"]) for x in wb["parts"]] == [
        ("access", "가 장소", "정류장", "road"), ("transfer", "환승 정류장", "역", "road"), ("egress", "끝역", "나 장소", "straight")]
    assert wb["basis"] == "mixed" and wb["grade"] == "추정" and wb["source_id"] == "osm_road_graph_v2@test"
    assert [w["code"] for w in wb["warnings"]] == ["walk_detour", "walk_fallback"]
    assert "지도 밖" in wb["warnings"][1]["reason"] and "2.6배" in wb["warnings"][0]["reason"]
    assert wb["parts"][0]["detour_over"] is True and wb["parts"][2]["why"] == "out_of_area"


def test_walk_basis_does_not_borrow_another_stop_of_the_same_name():
    """GPT 102 #4 — 이름이 같은 정류장 둘(100 m · 900 m)을 쟀어도, 계획 수단의 근거는 **그 후보가 쓴 정류장**의 값이다."""
    p = _planner(FakeRouter())
    near, far = (1.0, 1.0), (9.0, 9.0)
    p.v.walk_log.update({_key(("stop", "가 장소", "정류장"), near): _info("road", 80, 100),
                         _key(("stop", "가 장소", "정류장"), far): _info("road", 300, 900, over=True),
                         _key(("station", "나 장소", "끝역"), (2.0, 2.0)): _info("road", 300, 400)})
    legs = _mc(0, 0).legs
    for order in (near, far):
        wb = p._walk_basis({"_legs": legs, "_wev": {"in": order, "out": None, "xfer": []}}, A, B)
        assert wb["parts"][0]["walk_m"] == (100 if order == near else 900)
    assert p._walk_basis({"_legs": legs, "_wev": {"in": near}}, A, B)["warnings"] == [], "먼 정류장의 우회 경고가 붙지 않는다"
    p.v.walk_log[_key(("station", "가 장소", "정류장"), (5.0, 5.0))] = _info("road", 50, 60)      # 이름이 같은 **역**의 값 — 정류장 조각에 안 붙는다
    assert p._walk_basis({"_legs": legs, "_wev": {"in": near}}, A, B)["parts"][0]["walk_m"] == 100
    unknown = p._walk_basis({"_legs": legs}, A, B)                       # 어느 정류장인지 모르는 후보 — 값이 둘이면 붙이지 않는다
    assert [x["part"] for x in unknown["parts"]] == ["egress"], "틀린 근거를 붙이느니 뺀다"


def test_walk_basis_with_the_router_off_is_straight_without_warnings():
    p = _planner(None)
    p.v.walk_log[_key(("direct", "가 장소", "나 장소"), (2.0, 2.0))] = _info("straight", 300, 420, why="no_router")
    wb = p._walk_basis({"_legs": []}, A, B)
    assert wb["basis"] == "straight" and wb["source_id"] is None and wb["warnings"] == [], "끈 것은 되돌린 것이 아니다 — 경고 없음"
    assert wb["parts"] == [{"part": "direct", "from": "가 장소", "to": "나 장소", "basis": "straight", "straight_m": 300,
                            "walk_m": 420, "ratio": 1.4, "why": "no_router"}]
    assert p._walk_basis({"_legs": [{"mode": "taxi"}], "_taxi": True}, A, B)["parts"] == [], "택시는 걷기 조각으로 세지 않는다"
    bike = {"_legs": [{"mode": "bike", "from": {"lat": 1, "lng": 2}, "to": {"lat": 3, "lng": 4}}]}
    assert p._walk_basis(bike, A, B)["basis"] is None, "자전거 구간(좌표 dict)은 건너뛴다"


# ── GPT 102 대조 ──────────────────────────────────────────────────────────────
def test_reason_is_returned_with_the_answer_not_read_from_shared_state():
    """GPT 102 #1 — 다른 요청이 객체의 last_error 를 바꿔도 이 호출의 까닭은 이 호출의 것이다(route_ex)."""
    br = BikeRouter(FakeRouter(fail="그 밖의 실패"))
    out, err = br.route_ex("foot", 37.5, 127.0, 37.501, 127.001)
    assert out is None and err["kind"] == "router_down" and br.last_error is None, "route_ex 는 객체의 칸을 건드리지 않는다"
    v = _v(FakeRouter(fail="그 밖의 실패"))
    orig = v.bike_router.route_ex

    def racing(*a):                                   # 이 호출이 끝나기 전에 다른 요청이 「경로 없음」을 적어 놓는다
        got = orig(*a)
        v.bike_router.last_error = {"kind": "no_path"}
        return got
    v.bike_router.route_ex = racing
    assert VT.foot_walk(v, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)[1]["why"] == "router_down"
    p = _planner(None)
    p.v = v
    straight = meters(A["lat"], A["lon"], B["lat"], B["lon"])
    assert p._walk_net(A, B, straight) == (straight * DETOUR, False), "남의 「경로 없음」 때문에 도보 후보가 빠지지 않는다"


def test_a_transient_router_failure_is_not_cached():
    """GPT 102 #2 — 한 번 실패한 길찾기가 회복되면 다음 요청은 길 기준으로 돌아온다. 같은 그래프면 늘 같은 답(지도 밖·경로 없음)만 담는다."""
    r = FakeRouter(dist=900.0, fail="도로 그래프 자료가 없다(x)")
    v = _v(r)
    assert VT.foot_walk(v, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)[1]["why"] == "no_graph"
    r.fail = None
    walk_m, info = VT.foot_walk(v, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)
    assert (walk_m, info["basis"], r.calls) == (900.0, "road", 2)
    for fail, calls in (("out_of_area: x", 1), ("no_path: x", 1), ("그 밖", 2)):
        r2 = FakeRouter(fail=fail)
        v2 = _v(r2)
        VT.foot_walk(v2, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR), VT.foot_walk(v2, 37.57, 126.98, 37.5736, 126.98, 400, DETOUR)
        assert r2.calls == calls, fail
    assert VT.FOOT_CACHE_FAIL_KINDS == {"out_of_area", "no_path"}


def test_plan_version_is_bumped():
    assert P.PLAN_VERSION == "plan-v2.7"


# ── ③ 요금 거리 원천 순서 ─────────────────────────────────────────────────────
FARE = {"subway": {
    "base": {"value": {"won": 1550, "base_m": 10000}},
    "distance_steps": {"value": [{"upto_m": 50000, "every_m": 5000, "won": 100}, {"upto_m": None, "every_m": 8000, "won": 100}]},
    "early_bird": {"value": {"until_min": 390, "rate": 0.2, "applies": "base"}},
    "early_bird_gate_window": {"value": 15},
    "하한_연결_반경_m": {"value": 300},
    "distance_estimate": {"value": {"lines": ["09호선"], "tolerance": 0.30, "unknown_edge_floor": 0.75}},
}}


def _lo(gap=None, est=None, lines=("09호선",)):
    doc = {}
    for ln in lines:
        st = [{"station_nm": n, "station_key": f"{ln}|{n}"} for n in "ABC"]
        doc[ln] = {"stations": st, "edges": [{"a": "A", "b": "B", "grade": "확정", "distance_m": 1000},
                                             {"a": "B", "b": "C", "grade": "확정"}]}
    lo = LineOrder({"built_at": "t", "lines": doc})
    lo.est_edges, lo.gap_edges = est or {}, gap or {}
    return lo


def _fv(lo):
    return SimpleNamespace(lo=lo, sc=None, tw=None, R={"fare": FARE})


def _legs(ln="09호선"):
    return [{"mode": "subway", "line": ln, "from": "A", "to": "C"}]


def test_upper_bound_never_shortcuts_through_a_line_outside_the_fare_list():
    """GPT 102 #5 — 허용 노선 A→C 는 20 km. 양끝에서 갈아탈 수 있는 목록 밖 노선의 **추정** 길이 1 km 가 상한 그래프에 들어가면
    상한이 1.3 km 로 줄어 요금이 싸게 나온다. 목록 밖 노선은 상한 그래프에 넣지 않는다(하한에는 넣는다)."""
    st = lambda ln: [{"station_nm": n, "station_key": f"{ln}|{n}"} for n in "AC"]          # noqa: E731
    lo = LineOrder({"built_at": "t", "lines": {
        "09호선": {"stations": st("09호선"), "edges": [{"a": "A", "b": "C", "grade": "확정", "distance_m": 20000}]},
        "신분당선": {"stations": st("신분당선"), "edges": [{"a": "A", "b": "C", "grade": "확정"}]}}})
    lo.est_edges, lo.gap_edges = {("신분당선", "A", "C"): 1000}, {}
    tw = SimpleNamespace(pairs={k: {"station_nm": k, "from_line": "09호선", "to_line": "신분당선"} for k in "AC"})
    v = SimpleNamespace(lo=lo, sc=None, tw=tw, R={"fare": FARE})
    net = O.fare_net_est(v)
    legs = [{"mode": "subway", "line": "09호선", "from": "A", "to": "C"}]
    assert net.ridden_m(legs) == 20000 and net.upper_m(legs) == 20000, "앞 판은 상한 1,300 m"
    assert ("신분당선", "C") not in net.ub.get(("신분당선", "A"), {})
    assert net.lower_m(legs) == 700, "하한 그래프에는 그대로 있다(낮아질 뿐 틀린 값은 안 나온다) → 하한≠상한이라 값 없음"
    assert O._subway_fare_on(v, net, legs, [SimpleNamespace(depart_min=600)]) is None


def test_gap_table_file_is_read_and_a_missing_file_is_empty(tmp_path):
    f = tmp_path / "station_gap_v1.jsonl"
    rows = [{"line": "09호선", "a": "B", "b": "C", "distance_m": 2000, "grade": "확정", "n_sources": 1},
            {"line": "김포도시철도", "a": "X", "b": "Y", "distance_m": 1400, "grade": "추정", "n_sources": 1},
            {"line": "09호선", "a": "C", "b": "D", "distance_m": None, "grade": "확정"}]
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    assert _load_gap_edges(f) == {("09호선", "B", "C"): (2000, "확정"), ("김포도시철도", "X", "Y"): (1400, "추정")}
    assert _load_gap_edges(tmp_path / "없음.jsonl") == {}
    f.write_text("{깨진 줄", encoding="utf-8")
    assert _load_gap_edges(f) == {}, "못 읽으면 표가 없는 것과 같다(요금은 앞 판과 같은 결과)"


def test_without_the_gap_table_nothing_changes():
    v = _fv(_lo())
    assert O.pub_lengths(v) == ({}, {}, None)
    net = O.fare_net(v)
    assert net.fare_lines is None and net.ridden_m(_legs()) is None, "B–C 거리가 없다 — 앞 판과 같이 값 없음"


def test_source_order_is_order_table_then_gap_table_then_track_estimate():
    gap = {("09호선", "A", "B"): (9999, "확정"), ("09호선", "C", "B"): (2000, "확정")}     # 표의 a·b 순서가 뒤집혀 있어도 찾는다
    v = _fv(_lo(gap=gap, est={("09호선", "B", "C"): 5000}))
    pub, lb_only, lines = O.pub_lengths(v)
    assert pub == {k: m for k, (m, _g) in gap.items()} and lb_only == {} and lines == {"09호선"}
    net = O.fare_net(v)
    assert net.exact["09호선"]["A"]["B"] == 1000, "① 역 순서 표가 먼저(표에 값이 있으면 공표 역간거리 표를 안 본다)"
    assert net.exact["09호선"]["B"]["C"] == 2000 and not net._est_pairs, "② 공표 역간거리 표 — 확정 거리(추정 간선으로 세지 않는다)"
    assert net.ridden_m(_legs()) == 3000 and net.lower_m(_legs()) == 3000
    est = O.fare_net_est(v)
    assert est.exact["09호선"]["B"]["C"] == 2000 and not est._est_pairs, "③ 선로 길이 추정(5,000)은 공표 값이 없을 때만"
    rides = [SimpleNamespace(depart_min=600)]
    assert O._subway_fare_on(v, net, _legs(), rides) == 1550


def test_lines_outside_the_fare_list_get_no_fare_even_with_published_distances():
    """별도운임일 수 있는 노선(목록 밖)은 공표 거리가 있어도 값을 내지 않는다 — 그 거리는 하한 그래프에만 넣는다."""
    gap = {("09호선", "B", "C"): (2000, "확정"), ("공항철도", "B", "C"): (3000, "확정"), ("공항철도", "A", "B"): (700, "추정")}
    lo = _lo(gap=gap, lines=("09호선", "공항철도"))
    del lo.doc["lines"]["공항철도"]["edges"][0]["distance_m"]          # 공항철도는 역 순서 표에 거리가 없다
    v = _fv(lo)
    pub, lb_only, lines = O.pub_lengths(v)
    assert pub == {("09호선", "B", "C"): 2000}
    assert lb_only == {("공항철도", "B", "C"): 3000, ("공항철도", "A", "B"): 700 - O.GAP_EST_TOL_M}, "추정 등급은 오차만큼 뺀다"
    net = O.fare_net(v)
    assert net.ridden_m(_legs("공항철도")) is None, "요금을 내는 노선이 아니다"
    assert ("공항철도", "C") not in net.ub.get(("공항철도", "B"), {}), "상한 그래프에는 넣지 않는다"
    assert net.lb[("공항철도", "B")][("공항철도", "C")] == 3000 and net.lb[("공항철도", "A")][("공항철도", "B")] == 600
    assert net.ridden_m(_legs()) == 3000, "목록 안 노선은 그대로 낸다"


# ── 실데이터(전체층) — 판정기의 환승 걷기 ─────────────────────────────────────
_RT = None


def _real_v():
    global _RT
    if _RT is None:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT = build_verifier(quiet=True)
        except RuntimeError as e:
            _RT = e
    if isinstance(_RT, Exception):
        pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
    import copy
    return copy.copy(_RT._v)


MIX02 = ({"line": "02호선", "from": "잠실", "to": "건대입구"},
         {"mode": "bus", "route": "2016", "from": "건대입구역1번출구", "to": "성수역1번출구"})
BB02 = ({"mode": "bus", "route": "2016", "from": "건대입구역1번출구", "to": "성수역1번출구"},
        {"mode": "bus", "route": "성동10", "from": "성수역4번출구", "to": "화양동현대아파트"})


@pytest.mark.mobility_full
def test_verifier_transfer_walks_use_the_road_but_judge_the_limit_by_straight():
    v = _real_v()
    if v.bus is None or v.sc is None:
        pytest.skip("버스·역 좌표 자료 없음")
    off_ss, off_bb = v._stop_station_walk(*MIX02, {}), v._bus_bus_walk(*BB02, {})
    assert off_ss["walk_basis"]["why"] == "no_router" and math.isclose(off_ss["walk_m"], off_ss["dist_m"] * off_ss["factor"])
    assert math.isclose(off_bb["walk_m"], off_bb["dist_m"] * off_bb["factor"]), "길찾기를 끄면 앞 판 식 그대로"
    r = FakeRouter(dist=333.0)
    v.bike_router, v._foot_cache = BikeRouter(r), {}
    on_ss, on_bb = v._stop_station_walk(*MIX02, {}), v._bus_bus_walk(*BB02, {})
    for on, off in ((on_ss, off_ss), (on_bb, off_bb)):
        assert on["verdict"] == "feasible" and on["walk_m"] == 333.0 and on["walk_basis"]["basis"] == "road"
        assert on["dist_m"] == off["dist_m"], "상한 판정에 쓰는 직선은 그대로"
        assert on["walk_min"] == math.ceil(333.0 / 1.04 / 60 * 10 - 1e-9) / 10
        assert "길 기준 333m" in on["evidence"][0]["claim"]
    assert "길 기준 333m" in v._walk_txt(on_ss)
    v.bike_router, v._foot_cache = BikeRouter(FakeRouter(fail="out_of_area: x")), {}
    back = v._stop_station_walk(*MIX02, {})
    assert math.isclose(back["walk_m"], off_ss["walk_m"]) and back["walk_basis"]["why"] == "out_of_area"
    assert "길 기준 못 냄 — out_of_area" in back["evidence"][0]["claim"], "되돌린 까닭이 근거 문구에 남는다"


@pytest.mark.mobility_full
def test_data_gap_table_matches_its_report_when_present():
    """자료 폴더의 공표 역간거리 표 — 688간선(확정 679 · 추정 9 = 김포골드라인). 표가 없는 기기는 건너뛴다(규칙 26 — 요금은 앞 판과 같다)."""
    gap = getattr(_real_v().lo, "gap_edges", None)
    if not gap:
        pytest.skip("station_gap_v1.jsonl 없음 — 요금은 역 순서 표 → 선로 길이 추정(앞 판과 같다)")
    assert len(gap) == 688 and sum(1 for _m, g in gap.values() if g == "확정") == 679
    assert {k[0] for k, (_m, g) in gap.items() if g == "추정"} == {"김포도시철도"}


@pytest.mark.mobility_full
def test_fare_lines_block_separate_fare_lines_on_real_data():
    """실데이터 — 공표 거리가 생긴 공항철도·용인·의정부·우이신설·인천은 요금을 내지 않고(목록 밖), 9호선·경의선 등은 낸다."""
    v = _real_v()
    if not getattr(v.lo, "gap_edges", None):
        pytest.skip("station_gap_v1.jsonl 없음")
    pub, lb_only, lines = O.pub_lengths(v)
    assert {k[0] for k in pub} <= lines and not ({k[0] for k in lb_only if k not in pub} & lines) - {"김포도시철도"}
    assert {"공항철도", "용인경전철", "의정부경전철", "우이신설경전철"} <= {k[0] for k in lb_only}
    net = O.fare_net(v)
    assert net.ridden_m([{"line": "공항철도", "from": "서울역", "to": "홍대입구"}]) is None, "공항철도는 거리가 있어도 값 없음"
    assert net.ridden_m([{"line": "09호선", "from": "노량진", "to": "신논현"}]) is not None, "9호선 노량진~신논현은 공표 거리(확정)"
