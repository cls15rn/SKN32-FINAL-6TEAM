# -*- coding: utf-8 -*-
"""공표 역간거리가 없는 구간의 지하철 요금을 OSM 선로 길이 추정으로 — 문제목록 #21 (2026-10-04).

공표만으로 요금이 안 나올 때만 추정 길이를 더한 요금망으로 다시 계산한다. 경로 오차 = tolerance/√(추정 간선 수).
하한 요금과 상한 요금이 같을 때만 값을 낸다. 공표로 이미 나오던 값은 바뀌지 않는다.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

from app.modules.travel_ops.mobility.engine import options as O
from app.modules.travel_ops.mobility.engine.line_order import LineOrder, _load_est_edges

FARE = {"subway": {
    "base": {"value": {"won": 1550, "base_m": 10000}},
    "distance_steps": {"value": [{"upto_m": 50000, "every_m": 5000, "won": 100}, {"upto_m": None, "every_m": 8000, "won": 100}]},
    "early_bird": {"value": {"until_min": 390, "rate": 0.2, "applies": "base"}},
    "early_bird_gate_window": {"value": 15},
    "하한_연결_반경_m": {"value": 300},
    "distance_estimate": {"value": {"lines": ["09호선"], "tolerance": 0.30, "unknown_edge_floor": 0.75}},
}}


def _lo(official_ab=None, est=None, straight=None):
    st = [{"station_nm": n, "station_key": f"09호선|{n}"} for n in "ABC"]
    edges = [{"a": "A", "b": "B", "grade": "확정", "distance_m": official_ab}, {"a": "B", "b": "C", "grade": "확정"}]
    lo = LineOrder({"built_at": "t", "lines": {"09호선": {"stations": st, "edges": edges}}})
    lo.est_edges, lo.straight_edges = est or {}, straight or {}
    return lo


def _v(lo):
    return SimpleNamespace(lo=lo, sc=None, tw=None, R={"fare": FARE})


def _legs(a="A", b="C"):
    return [{"mode": "subway", "line": "09호선", "from": a, "to": b}]


def test_without_estimates_there_is_no_estimate_net():
    v = _v(_lo())
    assert O.est_lengths(v)[0] == {} and O.fare_net_est(v) is None


def test_estimate_net_carries_lengths_tolerance_allowed_lines_and_floors():
    lo = _lo(est={("09호선", "A", "B"): 2000}, straight={("09호선", "B", "C"): 1000})
    est, tau, ok, floor = O.est_lengths(_v(lo))
    assert est == {("09호선", "A", "B"): 2000} and tau == 0.30 and ok == {"09호선"}
    assert floor == {("09호선", "B", "C"): 750}, "공표도 추정도 없는 간선의 하한 바닥값 = 직선 × 0.75"


def test_bounds_shrink_with_the_number_of_estimated_edges():
    lo = _lo(est={("09호선", "A", "B"): 2000, ("09호선", "B", "C"): 3000})
    net = O.fare_net_est(_v(lo))
    legs = _legs()
    tot, est_sum, n = net._ridden(legs)
    assert (tot, est_sum, n) == (5000, 5000, 2)
    half = 0.30 / math.sqrt(2)
    assert math.isclose(net.ridden_m(legs), 5000 * (1 + half))
    assert net.lower_m(legs) == math.floor(5000 * (1 - half))
    one = O.fare_net_est(_v(_lo(est={("09호선", "A", "B"): 2000}, straight={("09호선", "B", "C"): 100})))
    assert one._ridden(legs) is None, "추정 길이가 없는 간선이 끼면 탄 경로 길이를 모른다 — 요금 없음"


def test_official_only_net_still_does_not_know_estimated_edges():
    lo = _lo(est={("09호선", "A", "B"): 2000, ("09호선", "B", "C"): 3000})
    assert O.fare_net(_v(lo)).ridden_m(_legs()) is None


def test_official_distance_wins_where_it_exists():
    lo = _lo(official_ab=2000, est={("09호선", "A", "B"): 9999, ("09호선", "B", "C"): 3000})
    net = O.fare_net_est(_v(lo))
    assert net.exact["09호선"]["A"]["B"] == 2000
    assert ("09호선", frozenset("AB")) not in net._est_pairs and ("09호선", frozenset("BC")) in net._est_pairs
    assert net._ridden(_legs()) == (5000, 3000, 1), "추정 오차는 추정 간선(B-C) 몫만 — 공표 간선 몫은 오차 없음"


def test_a_line_outside_the_allowed_list_never_gets_an_estimated_fare():
    lo = _lo(est={("09호선", "A", "B"): 2000, ("09호선", "B", "C"): 3000})
    v = _v(lo)
    v.R = {"fare": {"subway": dict(FARE["subway"], distance_estimate={"value": {"lines": ["02호선"], "tolerance": 0.30}})}}
    net = O.fare_net_est(v)
    assert net._ridden(_legs()) is None, "별도운임일 수 있는 노선은 길이를 알아도 요금을 내지 않는다(하한 그래프에만 쓴다)"


def test_bracket_must_collapse_to_one_fare_else_no_value():
    F = FARE
    near = O.fare_net_est(_v(_lo(est={("09호선", "A", "B"): 2000, ("09호선", "B", "C"): 3000})))     # 5 km ± 21% — 모두 10 km 이내
    ub, lb = near.upper_m(_legs()), near.lower_m(_legs())
    assert O.subway_fare_at(F, lb, 700) == O.subway_fare_at(F, ub, 700) == 1550
    edge = O.fare_net_est(_v(_lo(est={("09호선", "A", "B"): 4000, ("09호선", "B", "C"): 6000})))     # 10 km ± 21% — 경계를 가로지른다
    ub, lb = edge.upper_m(_legs()), edge.lower_m(_legs())
    assert O.subway_fare_at(F, lb, 700) != O.subway_fare_at(F, ub, 700)


def test_load_est_edges_splits_estimates_straights_and_drops_official_and_wrong_matches(tmp_path):
    import gzip
    import json
    rows = [
        {"line": "09호선", "a": "A", "b": "B", "official_m": None, "track_m": 1500, "straight_m": 1200},      # 추정
        {"line": "09호선", "a": "B", "b": "C", "official_m": 1000, "track_m": 1100, "straight_m": 900},       # 공표 → 둘 다 안 씀
        {"line": "09호선", "a": "C", "b": "D", "official_m": None, "track_m": 4000, "straight_m": 900},       # 평행 선로 → 직선만
        {"line": "09호선", "a": "D", "b": "E", "official_m": None, "why": "no_path", "straight_m": 800},      # 직선만
        {"line": "09호선", "a": "E", "b": "F", "official_m": None, "why": "no_coords"},                       # 아무것도 없음
    ]
    p = tmp_path / "rail_edge_track_v1.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    est, straight = _load_est_edges(p)
    assert est == {("09호선", "A", "B"): 1500}
    assert straight == {("09호선", "C", "D"): 900, ("09호선", "D", "E"): 800}
    assert _load_est_edges(tmp_path / "없음.gz") == ({}, {})
