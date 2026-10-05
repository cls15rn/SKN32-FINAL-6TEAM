# -*- coding: utf-8 -*-
"""파이썬 길찾기(graph_router) — 작은 합성 그래프로 정확성을, 실제 서울 그래프로 GraphHopper 기록과의 차이를 본다(2026-10-04).

합성 그래프 시험은 자료 없이 도는 것 — 최단경로가 독립 다익스트라와 같은지 · 일방통행 · 자동차전용 도로 · 간선 한가운데 좌표 · 지도 밖 · 응답 모양.
실제 그래프 시험은 `datasets/mobility/processed/mobility/road_graph_v1` 이 있을 때만 돈다(없으면 건너뜀 — 이유를 보인다).
"""
from __future__ import annotations

import heapq
import json
import math
import random
from pathlib import Path

import pytest

from app.modules.travel_ops.mobility.engine.car import RouterDown, hav
from app.modules.travel_ops.mobility.engine.graph_router import GraphRouter, decode_polyline


def encode_polyline(coords, precision=1e6):
    """(lng, lat) 열 → Google polyline(시험용 역함수)."""
    out, plat, plng = [], 0, 0
    for lng, lat in coords:
        for cur, prev in ((round(lat * precision), plat), (round(lng * precision), plng)):
            d = cur - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plng = round(lat * precision), round(lng * precision)
    return "".join(out)


LAT0, LON0, STEP = 37.5000, 127.0000, 0.001            # 4×4 격자, 칸 ≈ 100 m


def nid(i, j):
    return 1000 + i * 10 + j


def xy(i, j):
    return (LON0 + j * STEP, LAT0 + i * STEP)           # (lng, lat)


def make_records(*, oneway=(), motorway=(), car_off=(), v2=False, extra=()):
    """격자. oneway/motorway/car_off 는 ((i,j),(i2,j2)) 쌍 — 일방은 u→v 방향이 쌍의 순서.

    v2=True 면 road_graph_v2 모양(걸음 칸 `foot`·`st`·`fmain`)으로 만든다. extra 는 보행자 전용 길 목록 [((i,j),(i2,j2),계단여부)]."""
    nodes = [{"id": nid(i, j), "lat": xy(i, j)[1], "lon": xy(i, j)[0], "r": 1} for i in range(4) for j in range(4)]
    edges, way = [], 1
    for i in range(4):
        for j in range(4):
            for di, dj in ((0, 1), (1, 0)):
                i2, j2 = i + di, j + dj
                if i2 > 3 or j2 > 3:
                    continue
                a, b = (i, j), (i2, j2)
                hw, ow, car, bike = "residential", 0, 1, 1
                if (b, a) in oneway:
                    a, b, ow = b, a, 1
                elif (a, b) in oneway:
                    ow = 1
                pts = [xy(*a), xy(*b)]
                if (a, b) in motorway or (b, a) in motorway:
                    hw, bike = "motorway", 0
                if (a, b) in car_off or (b, a) in car_off:
                    car = 0
                edges.append({"u": nid(*a), "v": nid(*b), "way": way, "sa": 0, "sb": 1,
                              "len": round(hav(pts[0], pts[1]), 1), "ow": ow, "hw": hw, "ms": None,
                              "car": car, "bike": bike, "bow": 0, "g": encode_polyline(pts), "main": 1, "bmain": 1})
                if v2:
                    edges[-1].update({"foot": int(hw != "motorway"), "st": 0, "fmain": 1})
                way += 1
    for a, b, steps in extra:                              # 보행자 전용 길 — 차·자전거는 못 다닌다
        pts = [xy(*a), xy(*b)]
        edges.append({"u": nid(*a), "v": nid(*b), "way": way, "sa": 0, "sb": 1, "len": round(hav(pts[0], pts[1]), 1),
                      "ow": 0, "hw": "steps" if steps else "footway", "ms": None, "car": 0, "bike": 0, "bow": 0,
                      "foot": 1, "st": int(steps), "g": encode_polyline(pts), "main": 0, "bmain": 0, "fmain": 1})
        way += 1
    return {"nodes": nodes, "edges": edges}


def reference_distance(records, profile, a, b):
    """독립 다익스트라 — 거리 최소. a, b = 노드 id. 쓸 수 있는 간선 규칙은 이 시험이 따로 적는다(구현을 베끼지 않는다)."""
    adj = {}
    for e in records["edges"]:
        fwd = bwd = False
        if profile == "car" and e["car"]:
            fwd, bwd = e["ow"] >= 0, e["ow"] <= 0
        elif profile == "bike" and e["bike"]:
            fwd = bwd = True
        elif profile == "foot" and e["hw"] != "motorway":
            fwd = bwd = True
        if fwd:
            adj.setdefault(e["u"], []).append((e["v"], e["len"]))
        if bwd:
            adj.setdefault(e["v"], []).append((e["u"], e["len"]))
    dist, pq = {a: 0.0}, [(0.0, a)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == b:
            return d
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj.get(u, ()):
            if d + w < dist.get(v, math.inf):
                dist[v] = d + w
                heapq.heappush(pq, (d + w, v))
    return None


def route_len(gr, a, b, profile):
    return gr.route(xy(*a), xy(*b), profile=profile)["paths"][0]["distance"]


def test_polyline_roundtrip():
    pts = [(127.0, 37.5), (127.001234, 37.50055), (126.99999, 37.4999)]
    back = decode_polyline(encode_polyline(pts))
    assert all(abs(a[0] - b[0]) < 2e-6 and abs(a[1] - b[1]) < 2e-6 for a, b in zip(pts, back))


@pytest.mark.parametrize("profile", ["car", "foot", "bike"])
def test_every_pair_matches_independent_dijkstra(profile):
    rec = make_records(oneway=[((0, 0), (0, 1)), ((1, 1), (1, 2)), ((2, 2), (2, 1))], motorway=[((1, 0), (1, 1))])
    gr = GraphRouter(records=rec)
    cells = [(i, j) for i in range(4) for j in range(4)]
    checked = 0
    for a in cells:
        for b in cells:
            if a == b:
                continue
            want = reference_distance(rec, profile, nid(*a), nid(*b))
            if want is None:
                with pytest.raises(RouterDown):
                    route_len(gr, a, b, profile)
                continue
            got = route_len(gr, a, b, profile)
            if profile == "car":                               # 차량 비용은 시간 — 모두 같은 속도라 거리 최단과 같다
                assert abs(got - want) <= 1.5, (a, b, got, want)
            else:
                assert abs(got - want) <= 1.5, (a, b, got, want)
            checked += 1
    assert checked >= 200


def test_one_way_is_obeyed_by_car_but_not_by_foot():
    rec = make_records(oneway=[((0, 0), (0, 1))])             # (0,0) → (0,1) 만 가능
    gr = GraphRouter(records=rec)
    fwd = route_len(gr, (0, 0), (0, 1), "car")
    back = route_len(gr, (0, 1), (0, 0), "car")
    assert fwd < 130 and back > 250, (fwd, back)               # 거슬러 가려면 한 바퀴 돈다
    assert route_len(gr, (0, 1), (0, 0), "foot") < 130


def test_foot_does_not_use_motorway_but_car_does():
    rec = make_records(motorway=[((1, j), (1, j + 1)) for j in range(3)])
    gr = GraphRouter(records=rec)
    assert route_len(gr, (1, 0), (1, 3), "car") < 330
    assert route_len(gr, (1, 0), (1, 3), "foot") > 330


def test_unreachable_raises_router_down_not_empty_path():
    rec = make_records(car_off=[((i, 1), (i, 2)) for i in range(4)])    # 가운데를 가르는 세로 단절(차량)
    gr = GraphRouter(records=rec)
    with pytest.raises(RouterDown) as ex:
        gr.route(xy(0, 0), xy(0, 3), profile="car")
    assert "no_path" in str(ex.value)
    assert route_len(gr, (0, 0), (0, 3), "foot") > 0          # 걸어서는 간다


def test_point_in_the_middle_of_an_edge_is_snapped_and_counted():
    gr = GraphRouter(records=make_records())
    mid = ((xy(0, 0)[0] + xy(0, 1)[0]) / 2, xy(0, 0)[1])       # (0,0)-(0,1) 간선 한가운데
    d = gr.route(mid, xy(0, 1), profile="foot")["paths"][0]
    assert 40 < d["distance"] < 60, d["distance"]               # ≈ 반 칸(55 m)
    p2 = ((xy(0, 0)[0] + 0.75 * STEP), xy(0, 0)[1])
    d2 = gr.route(mid, p2, profile="foot")["paths"][0]["distance"]
    assert 20 < d2 < 35, d2


def test_same_edge_direct_respects_one_way():
    rec = make_records(oneway=[((0, 0), (0, 1))])
    gr = GraphRouter(records=rec)
    a = ((xy(0, 0)[0] + 0.25 * STEP), xy(0, 0)[1])
    b = ((xy(0, 0)[0] + 0.75 * STEP), xy(0, 0)[1])
    fwd = gr.route(a, b, profile="car")["paths"][0]["distance"]
    back = gr.route(b, a, profile="car")["paths"][0]["distance"]
    assert fwd < 60 and back > 200, (fwd, back)


def test_far_outside_the_map_raises_out_of_area():
    gr = GraphRouter(records=make_records())
    with pytest.raises(RouterDown) as ex:
        gr.route((128.5, 36.0), xy(0, 0), profile="foot")
    assert "out_of_area" in str(ex.value)


def test_bad_inputs_are_refused():
    gr = GraphRouter(records=make_records())
    with pytest.raises(RouterDown):
        gr.route(xy(0, 0), xy(0, 1), profile="car", via=[xy(1, 1)])
    with pytest.raises(RouterDown):
        gr.route(xy(0, 0), xy(0, 1), profile="boat")


def test_response_has_the_graphhopper_shape_the_callers_read():
    gr = GraphRouter(records=make_records())
    p = gr.route(xy(0, 0), xy(3, 3), profile="car")["paths"][0]
    coords = p["points"]["coordinates"]
    assert p["distance"] > 0 and p["time"] > 0 and len(coords) >= 2
    ways = p["details"]["osm_way_id"]
    assert ways and all(0 <= a < b < len(coords) for a, b, _w in ways)
    assert [w[0] for w in ways][1:] == [w[1] for w in ways][:-1], "구간이 끊김 없이 이어져야 한다(앞 구간 끝 = 다음 구간 처음)"
    assert all(c[2] == "residential" for c in p["details"]["road_class"])
    # 이동 판정기가 읽는 어댑터를 그대로 통과한다 — 거리·시간만 남기고 형상은 버린다
    from app.modules.travel_ops.mobility.engine.bike import BikeRouter
    br = BikeRouter(gr, {}, "2026-09-18")
    r = br.route("foot", xy(0, 0)[1], xy(0, 0)[0], xy(0, 3)[1], xy(0, 3)[0])
    assert r["basis"] == "로컬 도로그래프" and r["source_id"] == "osm_road_graph@2026-09-18" and r["distance_m"] > 250
    assert "points" not in r


def test_foot_distance_includes_the_walk_from_the_point_to_the_road():
    gr = GraphRouter(records=make_records())
    off = (xy(0, 0)[0], xy(0, 0)[1] - 0.0009)                   # 길에서 남쪽으로 ≈ 100 m 떨어진 점
    foot = gr.route(off, xy(0, 1), profile="foot")["paths"][0]
    bike = gr.route(off, xy(0, 1), profile="bike")["paths"][0]
    assert foot["snap_m"][0] > 80 and foot["distance"] > foot["snap_m"][0] + 80      # 길까지 + 길 위
    assert bike["distance"] < foot["distance"] - 50, "자전거·자동차는 길까지의 직선을 더하지 않는다"


def test_unavailable_graph_files_say_so_without_loading():
    gr = GraphRouter("/nonexistent/road_graph_v1")
    assert gr.available() is False and gr.info() is None
    with pytest.raises(RouterDown):
        gr.route(xy(0, 0), xy(0, 1), profile="foot")


def test_v2_pedestrian_only_path_is_a_shortcut_for_foot_but_closed_to_car_and_bike():
    rec = make_records(v2=True, extra=[((0, 0), (1, 1), False)])          # (0,0)→(1,1) 대각선 샛길
    gr = GraphRouter(records=rec)
    diag = route_len(gr, (0, 0), (1, 1), "foot")
    assert gr.has_foot is True
    assert diag < 150, diag                                                 # 샛길 ≈ 134 m (격자 두 칸은 ≈ 200 m)
    assert route_len(gr, (0, 0), (1, 1), "car") > 190, "차는 샛길을 못 쓴다"
    assert route_len(gr, (0, 0), (1, 1), "bike") > 190, "자전거도 샛길을 못 쓴다(자전거 허용 표시가 없다)"


def test_v2_steps_cost_more_than_their_length():
    plain = GraphRouter(records=make_records(v2=True, extra=[((0, 0), (1, 1), False)]))
    steps = GraphRouter(records=make_records(v2=True, extra=[((0, 0), (1, 1), True)]))
    d_plain = route_len(plain, (0, 0), (1, 1), "foot")
    d_steps = route_len(steps, (0, 0), (1, 1), "foot")
    assert d_plain < 150
    assert d_steps > 190, f"계단 비용(길이×1.5 ≈ 200)이 격자 두 칸(≈ 200)과 같거나 커서 격자로 돌아야 한다: {d_steps}"


def test_v2_foot_does_not_walk_on_a_motorway():
    rec = make_records(v2=True, motorway=[((1, j), (1, j + 1)) for j in range(3)])
    gr = GraphRouter(records=rec)
    assert route_len(gr, (1, 0), (1, 3), "car") < 330
    assert route_len(gr, (1, 0), (1, 3), "foot") > 330


def test_far_off_network_points_are_flagged_optimistic_and_the_adapter_passes_it_on():
    gr = GraphRouter(records=make_records())
    far = (xy(0, 0)[0], xy(0, 0)[1] - 0.0009)                   # 길에서 ≈ 100 m — 길 밖 직선 구간이 길다
    near = (xy(0, 0)[0], xy(0, 0)[1] - 0.00015)                 # ≈ 17 m
    q_far = gr.route(far, xy(0, 3), profile="foot")["paths"][0]["quality"]
    q_near = gr.route(near, xy(0, 3), profile="foot")["paths"][0]["quality"]
    assert q_far["optimistic"] is True and q_far["access_max_m"] > 90
    assert q_near["optimistic"] is False and q_near["access_max_m"] < 30
    from app.modules.travel_ops.mobility.engine.bike import BikeRouter
    br = BikeRouter(gr, {}, "2026-09-18")
    r = br.route("foot", far[1], far[0], xy(0, 3)[1], xy(0, 3)[0])
    assert r["optimistic"] is True and r["quality"]["access_max_m"] > 90
    assert br.route("bike", far[1], far[0], xy(0, 3)[1], xy(0, 3)[0])["optimistic"] is False, "자전거는 길 위에서 타므로 표시하지 않는다"


def test_route_share_on_arterial_roads_is_reported_for_foot():
    rec = make_records(v2=True)
    for e in rec["edges"]:
        e["hw"] = "primary"                                    # 전부 간선도로
    gr = GraphRouter(records=rec)
    q = gr.route(xy(0, 0), xy(0, 3), profile="foot")["paths"][0]["quality"]
    assert q["arterial_pct"] > 95


# ── 실제 서울 그래프 ──────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def real_graph():
    from app.modules.travel_ops.mobility.engine import paths
    before = (paths.DATA_DIR, paths.SOURCE)
    if paths.SOURCE in ("unset", "disabled"):
        paths.load_cli_env()
    gr = GraphRouter.default()
    if not gr.available():
        paths._layout(before[0], before[1])
        pytest.skip(f"road_graph_v1 이 없다({gr.dir}) — 자료 기기에서만 돈다")
    yield gr
    paths._layout(before[0], before[1])


def _gh_pairs():
    fx = json.loads((HERE / "bike_gh_fixture_v1.json").read_text(encoding="utf-8"))
    fx.update(json.loads((HERE / "plan_bike_gh_fixture_v1.json").read_text(encoding="utf-8"))["routes"])
    out = {}
    for k, v in fx.items():
        prof, a, b = k.split("|")
        la1, lo1 = map(float, a.split(","))
        la2, lo2 = map(float, b.split(","))
        out[(prof, la1, lo1, la2, lo2)] = v["distance_m"]
    return out


def test_real_graph_agrees_with_recorded_graphhopper_routes(real_graph):
    """GraphHopper 가 같은 pbf 로 낸 거리 기록과 비교 — 표본이 작아(걸음 14쌍·자전거 8쌍) 방향성만 말한다.

    기준은 지금의 대체식(직선×1.4)을 이긴다는 것 — 중앙 오차가 대체식보다 작고 ±30% 안 비율이 더 높다."""
    pairs = _gh_pairs()
    foot, bike, base = [], [], []
    for (prof, la1, lo1, la2, lo2), gh in pairs.items():
        d = real_graph.route((lo1, la1), (lo2, la2), profile=prof)["paths"][0]["distance"]
        (foot if prof == "foot" else bike).append(abs(d - gh) / gh)
        if prof == "foot":
            base.append(abs(hav((lo1, la1), (lo2, la2)) * 1.4 - gh) / gh)
    foot_med, base_med = sorted(foot)[len(foot) // 2], sorted(base)[len(base) // 2]
    assert foot_med < base_med, (foot_med, base_med)
    assert sum(e <= 0.30 for e in foot) > sum(e <= 0.30 for e in base), (foot, base)
    assert sorted(bike)[len(bike) // 2] < 0.20, bike


def test_real_graph_astar_equals_plain_dijkstra_on_random_pairs(real_graph):
    """휴리스틱(A*)이 최단경로를 놓치지 않는다 — 같은 쌍에서 휴리스틱을 끈 다익스트라와 비용(자동차는 시간)이 같다."""
    rnd = random.Random(7)
    pts = [(126.90 + rnd.random() * 0.18, 37.45 + rnd.random() * 0.14) for _ in range(40)]
    same = 0
    for k in range(0, 40, 2):
        a, b = pts[k], pts[k + 1]
        for prof in ("car", "foot"):
            out = []
            for dij in (False, True):
                real_graph.force_dijkstra = dij
                try:
                    out.append(real_graph.route(a, b, profile=prof)["paths"][0])
                except RouterDown:
                    out.append(None)
            real_graph.force_dijkstra = False
            x, y = out
            assert (x is None) == (y is None)
            if x is not None:
                key = "time" if prof == "car" else "distance"
                assert abs(x[key] - y[key]) <= max(2.0, 0.002 * y[key]), (prof, a, b, x[key], y[key])
                same += 1
    assert same >= 20
