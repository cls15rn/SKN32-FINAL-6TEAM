# -*- coding: utf-8 -*-
"""77 — 서버 없는 파이썬 도로 라우터(engine/road_router.py) 단위 시험.

게이트 층(데이터 없이 돈다): 작은 합성 그래프를 tmp 폴더에 76 형식(nodes·edges .jsonl.gz · polyline 1e-6)으로 써서
  ① 스냅 200 m 상한 ② 일방통행 ③ 섬(main=0)에는 안 붙음 ④ 중앙분리 도로 반대 차로 ⑤ 범위 밖 ⑥ 시간 가중 ⑦ `/route` 모양 응답이
  CarGraph.compute 로 소요가 나오는지 ⑧ CarService 순서(파이썬 라우터 → 근거없음 · 99 에서 경로 서버 단 삭제) ⑨ 자전거 간선.
전체층(mobility_full · 실데이터): 76 그래프로 서울역→강남역 · 353 도로 끝점 스냅.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
from collections import defaultdict
from pathlib import Path

import pytest

pytest.importorskip("scipy")

from app.modules.travel_ops.mobility.engine import road_router as RRM  # noqa: E402
from app.modules.travel_ops.mobility.engine.car import (CarGraph, CarService,  # noqa: E402
                                                        RouterDown, ROAD_SOURCE_ID)
from app.modules.travel_ops.mobility.engine.road_router import RoadRouter, decode_polyline  # noqa: E402


# ── 합성 그래프 ────────────────────────────────────────────────────────────
def _enc_val(v):
    v = ~(v << 1) if v < 0 else v << 1
    out = []
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))
    return "".join(out)


def encode(pts, precision=6):
    f = 10 ** precision
    out, plat, plon = [], 0, 0
    for lat, lon in pts:
        a, b = int(round(lat * f)), int(round(lon * f))
        out.append(_enc_val(a - plat) + _enc_val(b - plon))
        plat, plon = a, b
    return "".join(out)


N = {  # id: (lat, lon, r)
    1: (37.500, 127.000, 1), 2: (37.500, 127.010, 1), 3: (37.510, 127.010, 1), 4: (37.510, 127.000, 1),
    5: (37.5010, 127.004, 1), 6: (37.5010, 127.006, 1),                          # 섬(main=0)
    7: (37.5200, 127.000, 1), 8: (37.5200, 127.020, 1),                          # 동행 차로(일방)
    9: (37.5202, 127.020, 1), 10: (37.5202, 127.000, 1),                         # 서행 차로(일방 · 22 m 북쪽)
    11: (37.540, 127.000, 1), 12: (37.540, 127.010, 1), 13: (37.545, 127.005, 1),  # 시간 가중: 골목 직선 vs 간선 우회
    20: (37.600, 127.100, 0), 21: (37.600, 127.110, 0),                          # 여백(r=0)
    30: (37.560, 127.000, 1), 31: (37.560, 127.010, 1),                          # 자전거 전용
    40: (37.5300, 127.000, 1), 41: (37.5300, 127.010, 1),                        # 자동차전용도로
    42: (37.5310, 127.000, 1), 43: (37.5310, 127.010, 1),                        # 그 옆 111 m 일반 도로
}


def _edge(u, v, way, ow=0, hw="residential", main=1, car=1, bike=1, bow=None, mid=None, sa=0):
    pts = [(N[u][0], N[u][1])] + (mid or []) + [(N[v][0], N[v][1])]
    L = sum(RRM._hav(a[1], a[0], b[1], b[0]) for a, b in zip(pts, pts[1:]))
    return {"u": u, "v": v, "way": way, "sa": sa, "sb": sa + len(pts) - 1, "len": round(L, 1), "ow": ow, "hw": hw,
            "ms": None, "car": car, "bike": bike, "bow": ow if bow is None else bow, "g": encode(pts),
            "main": main, "bmain": 1}


EDGES = [
    _edge(1, 2, 101, ow=1),          # 남쪽 변 동쪽으로만
    _edge(2, 3, 102), _edge(3, 4, 103), _edge(4, 1, 104),
    _edge(5, 6, 105, main=0),        # 섬
    _edge(7, 8, 201, ow=1, hw="primary"), _edge(9, 10, 202, ow=1, hw="primary"),
    _edge(10, 7, 203, ow=1), _edge(8, 9, 204, ow=1),
    _edge(11, 12, 301, hw="residential"),                                        # 골목 직선 약 880 m
    _edge(11, 13, 302, hw="primary"), _edge(13, 12, 303, hw="primary"),          # 간선 우회 약 1.4 km
    _edge(20, 21, 401),                                                           # 여백만의 간선
    _edge(30, 31, 501, car=0, hw="cycleway"),                                     # 자전거만
    _edge(4, 11, 601), _edge(4, 7, 602), _edge(11, 30, 603),                      # 이어 붙임(섬 아닌 한 망)
    _edge(40, 41, 701, hw="motorway"), _edge(42, 43, 702), _edge(40, 42, 703, hw="motorway_link"), _edge(41, 43, 704),
]


def _write(tmp: Path, region=None):
    d = tmp / "road_graph_v1"
    d.mkdir()
    with gzip.open(d / "nodes.jsonl.gz", "wt", encoding="utf-8") as f:
        for i, (la, lo, r) in N.items():
            f.write(json.dumps({"id": i, "lat": la, "lon": lo, "r": r}) + "\n")
    with gzip.open(d / "edges.jsonl.gz", "wt", encoding="utf-8") as f:
        for e in EDGES:
            f.write(json.dumps(e) + "\n")
    if region is not None:
        (d / "region_v1.geojson").write_text(json.dumps(region), encoding="utf-8")
    return d


REGION = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "시험"},
          "geometry": {"type": "Polygon", "coordinates": [[[126.99, 37.49], [127.03, 37.49], [127.03, 37.57],
                                                           [126.99, 37.57], [126.99, 37.49]]]}}]}


@pytest.fixture()
def rr(tmp_path):
    return RoadRouter.load(_write(tmp_path, REGION))


def _pt(lat, lon):
    return (lon, lat)


def test_polyline_roundtrip():
    pts = [(37.5, 127.0), (37.5012345, 127.0098765), (37.49, 126.99)]
    got = decode_polyline(encode(pts))
    assert all(abs(a - b) < 1e-6 and abs(c - d) < 1e-6 for (a, c), (b, d) in zip(pts, got))


def test_load_absent_is_none(tmp_path):
    assert RoadRouter.load(tmp_path) is None
    assert RRM.resolve("none") is None and RRM.resolve("") is None


def test_snap_limit(rr):
    s = rr.snap(_pt(37.5009, 127.002))                     # 남쪽 변에서 100 m
    assert s is not None and s["edge"] == 0 and 90 < s["d_m"] < 110
    assert rr.snap(_pt(37.505, 127.005)) is None             # 어느 간선에서도 400 m 넘게
    with pytest.raises(RouterDown, match="스냅 못 함"):
        rr.route(_pt(37.505, 127.005), _pt(37.5, 127.005))


def test_island_not_snapped(rr):
    s = rr.snap(_pt(37.5011, 127.005))                      # 섬 간선 11 m · 본망 남쪽 변 122 m
    assert s["edge"] == 0, "main=0 섬에 붙으면 안 된다"


def test_oneway(rr):
    a, b = _pt(37.5, 127.002), _pt(37.5, 127.008)
    fwd = rr.route(a, b)["paths"][0]
    assert 500 < fwd["distance"] < 560                      # 남쪽 변 그대로(약 528 m)
    back = rr.route(b, a)["paths"][0]
    assert back["distance"] > 3000                           # 일방이라 사각형을 돈다
    ways = [w for _, _, w in back["details"]["osm_way_id"]]
    assert ways[0] == 101 and 102 in ways and 103 in ways and 104 in ways


def test_divided_road_picks_right_carriageway(rr):
    # 서행 차로에서 5 m · 동행 차로에서 27 m — 동쪽으로 가는 질의는 동행 차로를 타야 한다(반대 차로에 붙어 U턴 금지)
    s, e = _pt(37.52025, 127.005), _pt(37.5200, 127.015)
    p = rr.route(s, e)["paths"][0]
    assert p["distance"] < 1000, p["distance"]
    assert [w for _, _, w in p["details"]["osm_way_id"]] == [201]


def test_out_of_region(rr):
    with pytest.raises(RouterDown, match="범위 밖"):
        rr.route(_pt(37.6, 127.105), _pt(37.5, 127.005))


def test_time_weight_prefers_arterial(tmp_path):
    d = _write(tmp_path, REGION)

    def speed(way, seg, direction, hw):
        return 60.0 if hw == "primary" else 10.0
    s, e = _pt(37.540, 127.0005), _pt(37.540, 127.0095)
    by_len = RoadRouter.load(d).route(s, e)["paths"][0]
    by_time = RoadRouter.load(d, speed=speed).route(s, e)["paths"][0]
    assert [w for _, _, w in by_len["details"]["osm_way_id"]] == [301]
    assert {w for _, _, w in by_time["details"]["osm_way_id"]} >= {302, 303}
    assert by_time["distance"] > by_len["distance"]


def test_bike_edges(rr):
    s, e = _pt(37.560, 127.001), _pt(37.560, 127.009)
    b = rr.route(s, e, profile="bike")["paths"][0]
    assert [w for _, _, w in b["details"]["osm_way_id"]] == [501]
    with pytest.raises(RouterDown, match="car 간선이 없다"):   # 차는 자전거 전용 간선에 붙지 않는다
        rr.route(s, e)
    with pytest.raises(RouterDown):
        rr.route(s, e, profile="foot")


def _mini_graph():
    g = CarGraph.__new__(CarGraph)
    g.holidays, g.cal_hol = set(), set()
    g.prof, g.seg, g.wayinfo = {}, defaultdict(dict), {}
    flat = {k: [30.0] * 24 for k in ("평일", "토요일", "휴일")}
    g.cf = {k: {"mean_kmh": flat} for k in ("도시고속도로", "주간선도로", "보조간선도로", "기타도로")}
    return g


RULES = {"car": {"coverage": {"warn_class_pct": {"value": 10}, "warn_default_pct": {"value": 10},
                              "unknown_default_pct": {"value": 50}},
                 "공항_상자": {"value": {"lng": [0, 0], "lat": [0, 0]}}},
         "taxi": {"fare": {}},
         "transfer": {"stop_station_walk": {"detour_factor": {"value": 1.4}}},
         "measured_baseline": {"kakao_walk_speed_mps": {"value": 1.04}}}


def test_route_shape_feeds_compute(rr):
    res = rr.route(_pt(37.5, 127.002), _pt(37.5, 127.008))
    c = _mini_graph().compute(res, dt.datetime(2026, 10, 6, 10, 0))
    assert abs(c["distance_m"] - res["paths"][0]["distance"]) < 2
    assert c["coverage_pct"]["default"] == 100.0 and c["topis_time_s"] > 0


def test_carservice_order(rr):
    svc = CarService(_mini_graph(), RULES, road=rr)
    out = svc.leg(_pt(37.5, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    assert out["source_id"][1] == ROAD_SOURCE_ID and out["grade"] == "근거없음"      # 전부 골목 → 기존 등급 규칙 그대로
    with pytest.raises(RouterDown) as ex:                      # 파이썬 라우터 실패 → 넘길 곳 없음 → 그 이유·갈래 그대로(99)
        svc.leg(_pt(37.505, 127.005), _pt(37.5, 127.005), dt.datetime(2026, 10, 6, 10, 0))
    assert ex.value.code in ("no_snap", "no_path", "out_of_area"), ex.value.code
    plain = CarService(_mini_graph(), RULES)                   # road 없음 = 근거없음
    with pytest.raises(RouterDown, match="도로 경로 계산 없이") as ex2:
        plain.leg(_pt(37.5, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    assert ex2.value.code == "router_down"


def test_no_route_server_client_left():
    """99(2026-10-04) — 경로 서버를 부르는 코드가 엔진에 없다(클라이언트·주소 인자·네트워크 모듈)."""
    import inspect

    from app.modules.travel_ops.mobility.engine import bike, car, runtime, verify_time
    for name in ("GraphHopperClient", "make_router", "NoRouter"):
        assert not hasattr(car, name), name
    assert not hasattr(bike, "BikeRouter")
    assert "urllib" not in inspect.getsource(car)
    assert "gh_url" not in inspect.signature(runtime.build_verifier).parameters
    assert "bike_router" not in inspect.signature(verify_time.Verifier.__init__).parameters


def test_fixture_router_sits_in_the_road_slot(tmp_path):
    """99 — 합성 경로 대역은 road 자리에 끼운다(`--road-graph fixture:<파일>`) · 없는 키·down 은 RouterDown."""
    from app.modules.travel_ops.mobility.engine.car import GRAPH_SOURCE_ID, FixtureRouter
    from app.modules.travel_ops.mobility.engine.verify_time import road_of
    f = tmp_path / "fx.json"
    f.write_text(json.dumps({"routes": {"127.0020,37.5000|127.0080,37.5000": {"down": True}}}), encoding="utf-8")
    fx = road_of(f"fixture:{f}")
    assert isinstance(fx, FixtureRouter) and fx.source_id == GRAPH_SOURCE_ID
    svc = CarService(_mini_graph(), RULES, road=fx)
    with pytest.raises(RouterDown, match="다운"):
        svc.leg(_pt(37.5, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    with pytest.raises(RouterDown, match="픽스처에 경로가 없다"):
        svc.leg(_pt(37.6, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    assert road_of("none") is None


# ── 전체층(실데이터) ──────────────────────────────────────────────────────
def _real():
    from app.modules.travel_ops.mobility.engine import paths
    if paths.SOURCE in ("unset",):
        paths.load_cli_env()
    d = paths.PROCESSED / "mobility"
    if not ((d / "road_graph_v1" / "edges.jsonl.gz").exists() and (d / "graph" / "topis_class_factor_v1.json").exists()):
        pytest.skip(f"road graph not present: {d}")
    return d


@pytest.mark.mobility_full
def test_real_seoul_station_gangnam():
    d = _real()
    from app.modules.travel_ops.mobility.engine.timeutil import HolidayCalendar
    from app.modules.travel_ops.mobility.engine.paths import RULES_DIR
    hol = HolidayCalendar.from_doc(json.loads((RULES_DIR / "holidays_2026_2027.json").read_text(encoding="utf-8")))
    cg = CarGraph.load(d / "graph", hol)
    rr = RoadRouter.load(d / "road_graph_v1", speed=cg.static_kmh)
    res = rr.route((126.9707, 37.5547), (127.0276, 37.4979))
    c = cg.compute(res, dt.datetime(2026, 9, 22, 18, 0))
    assert 9000 < c["distance_m"] < 12500                   # 네이버 12.0 km · 옛 경로 서버 10.5 km
    assert 25 * 60 < c["topis_time_s"] < 45 * 60             # 옛 경로 서버 33.5분 · 77 판 35.1분
    assert c["coverage_pct"]["topis"] > 70


@pytest.mark.mobility_full
def test_real_runtime_taxi_alternative():
    """서버 적재 경로(runtime.build_verifier)에서 경로 서버 없이 택시 대안이 소요·요금으로 나온다(앞 판은 늘 근거없음)."""
    d = _real()
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    try:
        rt = build_verifier(quiet=True, seoul_key="", road_graph=str(d / "road_graph_v1"))
    except RuntimeError as e:
        pytest.skip(f"data not present: {e}")
    assert rt.stats["car_router"] == "road_graph_v1"
    case = {"id": "RR-TAXI", "date": "2026-09-11", "depart_at": "24:55",
            "legs": [{"line": "02호선", "from": "잠실", "to": "성수"}]}
    res = rt.verify_case(case)
    tx = res.taxi
    assert tx["verdict"] == "feasible" and tx["grade"] == "추정", tx
    assert 4000 < tx["distance_m"] < 9000 and 5 <= tx["ride_min"] <= 20       # 옛 경로 서버 판 25:05 도착 · 12,300원 → 차도 그래프 25:04 · 12,200원(alt_legs ALT-01)
    assert tx["car"]["source_id"][1] == ROAD_SOURCE_ID
    # 77-2 — 수단별 후보의 택시 칸이 채워진다 · 계획·options[] 는 끔/켬이 같다(택시는 그 칸에서만)
    from app.modules.travel_ops.mobility.engine import plan as P
    ex = json.loads((Path(__file__).resolve().parent / "plan_example_in_v1.json").read_text(encoding="utf-8"))
    base = P.plan(ex["places"], ex["items"], ex.get("party_size"), ex.get("constraints"), runtime=rt)
    on = P.plan(ex["places"], ex["items"], ex.get("party_size"), ex.get("constraints"), runtime=rt, by_mode=True)
    strip = lambda o: {k: v for k, v in o.items() if k not in ("by_mode", "meta")}      # noqa: E731
    assert strip(base)["items"] == strip(on)["items"] and base["routes"] == on["routes"]
    taxis = [row["modes"]["taxi"] for row in on["by_mode"]]
    assert taxis and all(t["status"] == "found" for t in taxis), taxis
    for row, t in zip(on["by_mode"], taxis):
        assert t["legs"] == [{"mode": "taxi", "from": row["from"], "to": row["to"]}] and t["transfers"] == 0
        assert t["worst_min"] >= t["eta_min"] >= 1 and t["fare_krw"] >= 4800
        assert t["depart_at"] < t["arrive_at"] <= row["range_to"]
        assert all(u.startswith("도로:") for u in t["uses"])
    assert not any("taxi" in (o.get("id") or "") for r in on["routes"].values() for o in r.get("options", []))
    import copy
    import gc
    off = copy.copy(rt._v)                                                     # 라우터를 뺀 같은 판정기 = 종전 적재
    off.car = None
    assert off.verify_case(case).taxi["verdict"] == "unknown"                  # 끄면 종전(근거없음) 그대로
    del rt, off
    gc.collect()                                                               # 전체층은 한 프로세스 — 판정기를 놓는다


# ── 77-2: 도착 목표에 맞춘 가장 늦은 출발 · 최악 소요(p10) · 도로명 ─────────────────────────
def _graph_with_link():
    g = _mini_graph()
    g.wayinfo[101] = ("primary", [(N[1][1], N[1][0]), (N[2][1], N[2][0])])     # (lng, lat) — 남쪽 변
    g.wayname = {101: "시험대로"}
    g.seg[101][(0, "fwd")] = "L1"
    for h in range(24):
        g.prof[("L1", "평일", h)] = 30.0
    g.p10 = {("L1", "평일", h): 15.0 for h in range(24)}
    return g


def test_compute_worst_uses_p10_and_reports_roads(rr):
    g = _graph_with_link()
    res = rr.route(_pt(37.5, 127.002), _pt(37.5, 127.008))
    t0 = dt.datetime(2026, 10, 6, 10, 0)
    mean, worst = g.compute(res, t0), g.compute(res, t0, worst=True)
    assert mean["coverage_pct"]["topis"] == 100.0
    assert 60 <= mean["topis_time_s"] <= 66 and worst["topis_time_s"] == pytest.approx(2 * mean["topis_time_s"], abs=1)
    assert mean["roads"] == [{"name": "시험대로", "m": pytest.approx(mean["distance_m"], abs=0.2)}]
    assert g.compute(res, t0)["topis_time_s"] == mean["topis_time_s"]            # 기본(판정 경로)은 평균 그대로


def test_arrive_by_latest_departure_meets_worst(rr):
    svc = CarService(_graph_with_link(), RULES, road=rr)
    arrive = dt.datetime(2026, 10, 6, 11, 0)
    out = svc.arrive_by(_pt(37.5, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    assert out["worst_time_s"] >= out["topis_time_s"] and out["grade"] == "추정"
    worst_min = -(-out["worst_time_s"] // 60)
    assert out["depart_dt"] + dt.timedelta(minutes=worst_min) <= arrive          # 느린 쪽으로도 닿는다
    assert out["depart_dt"] + dt.timedelta(minutes=worst_min + 1) > arrive       # 1분 더 늦으면 못 닿는다 = 가장 늦은 출발
    assert out["roads"][0]["name"] == "시험대로" and out["source_id"][1] == ROAD_SOURCE_ID
    assert out["p10_pct"] == 100.0 and out["access_m"] < 1 and out["access_s"] <= 1   # 길 위의 점 — 걷는 몫 없음
    with pytest.raises(RouterDown) as ex:                                         # 못 내는 갈래가 code 로 나온다
        svc.arrive_by(_pt(37.6, 127.105), _pt(37.5, 127.005), arrive, taxi=False)
    assert ex.value.code == "out_of_area"
    with pytest.raises(RouterDown) as ex:
        svc.arrive_by(_pt(37.505, 127.005), _pt(37.5, 127.005), arrive, taxi=False)
    assert ex.value.code == "no_snap"


def test_arrive_by_finds_later_window_across_hour_boundary(rr):
    """GPT 77-2 #1 — 11시대는 빠르고 12시대는 느리다. 12:05 도착 목표면 12시대 출발은 못 닿지만 11:59 출발은 닿는다.
    「못 닿는 분이 나오면 멈춤」이면 더 이른 값에서 끝난다 — 하한에서 뒤로 훑어 처음 성립하는 분을 고른다."""
    g = _graph_with_link()
    for h in range(24):
        g.prof[("L1", "평일", h)] = 30.0 if h == 11 else 3.0        # 약 528 m: 11시대 약 1분 · 그 밖 약 11분
        g.p10[("L1", "평일", h)] = g.prof[("L1", "평일", h)]
    svc = CarService(g, RULES, road=rr)
    arrive = dt.datetime(2026, 10, 6, 12, 5)
    out = svc.arrive_by(_pt(37.5, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    assert out["depart_dt"] == dt.datetime(2026, 10, 6, 11, 59), out["depart_dt"]   # 11시대 마지막 분 — 12:00 부터는 못 닿는다
    late = g.run(g.prepare(rr.route(_pt(37.5, 127.002), _pt(37.5, 127.008))), dt.datetime(2026, 10, 6, 12, 0))
    assert late["topis_time_s"] > 600                                # 12:00 출발은 12:05 에 못 닿는다
    # GPT #2 — 훑는 분 수 안에 못 찾으면 「경로 없음」이 아니라 「출발 미확인」
    svc.SCAN_MAX_MIN = 0
    with pytest.raises(RouterDown) as ex:
        svc.arrive_by(_pt(37.5, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    assert ex.value.code == "depart_unconfirmed"


def test_arrive_by_drive_starts_after_the_walk_to_the_road(rr):
    """GPT 97 #4 — 차는 「장소 출발 + 출발지에서 차도까지 걷는 시간」에 달리기 시작한다. 11시대는 빠르고(약 1분) 12시대는
    느리다(약 11분). 출발지가 길에서 100 m(걷기 약 2분 15초)면 11:59 에 떠나도 차는 12시대에 달린다 — 장소 출발 시각의 속도로
    셈하면(앞 판) 11시대 속도로 계산해 못 닿는 출발을 성립으로 냈다."""
    g = _graph_with_link()
    for h in range(24):
        g.prof[("L1", "평일", h)] = 30.0 if h == 11 else 3.0
        g.p10[("L1", "평일", h)] = g.prof[("L1", "평일", h)]
    svc = CarService(g, RULES, road=rr)
    arrive = dt.datetime(2026, 10, 6, 12, 5)
    on_road = svc.arrive_by(_pt(37.5, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    off_road = svc.arrive_by(_pt(37.5009, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    assert on_road["depart_dt"] == dt.datetime(2026, 10, 6, 11, 59)
    walk = dt.timedelta(seconds=off_road["access_s"])
    assert 125 < off_road["access_s"] < 145
    # 걷고 나서 차가 달리기 시작하는 시각이 11시대 안이어야 닿는다 → 장소 출발은 11:57 이전
    assert off_road["depart_dt"] + walk < dt.datetime(2026, 10, 6, 12, 0), off_road["depart_dt"]
    assert off_road["depart_dt"] == dt.datetime(2026, 10, 6, 11, 57)
    assert off_road["depart_dt"] + dt.timedelta(seconds=off_road["worst_time_s"]) <= arrive


def test_arrive_by_counts_walk_to_the_road(rr):
    """GPT 77-2 #3 — 장소가 차도에서 100 m 떨어져 있으면 그만큼 걷는 시간이 소요에 든다(이격 × 1.4 ÷ 1.04 m/s)."""
    svc = CarService(_graph_with_link(), RULES, road=rr)
    arrive = dt.datetime(2026, 10, 6, 11, 0)
    on = svc.arrive_by(_pt(37.5, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    off = svc.arrive_by(_pt(37.5009, 127.002), _pt(37.5, 127.008), arrive, taxi=False)      # 출발지가 길에서 100 m
    assert 95 < off["access_m"] < 105 and 125 < off["access_s"] < 145
    assert off["worst_time_s"] - on["worst_time_s"] == pytest.approx(off["access_s"], abs=3)
    assert off["depart_dt"] < on["depart_dt"]                         # 그만큼 일찍 떠나야 한다
    assert off["topis_time_s"] == on["topis_time_s"]                  # 도로 소요 자체는 같다(걷는 몫은 따로)


def test_door_snap_skips_motorway(rr):
    """GPT 77-2 #4(부분) — 출발·도착은 승하차 지점이다. 자동차전용도로 본선·연결로에는 붙이지 않는다(경유점·도로 대조는 그대로)."""
    pt = _pt(37.53005, 127.005)                                       # 자동차전용도로에서 6 m · 일반 도로에서 105 m
    near = rr.snap(pt)
    assert rr.E[near["edge"]][4] == "motorway"
    door = rr.snap_candidates(pt, door=True)
    assert door and all(rr.E[c["edge"]][4] not in ("motorway", "motorway_link") for c in door)
    p = rr.route(pt, _pt(37.5310, 127.009))["paths"][0]
    assert p["snap_m"][0] > 100 and [w for _, _, w in p["details"]["osm_way_id"]] == [702]
    rr.door = False
    try:
        q = rr.route(pt, _pt(37.5310, 127.009))["paths"][0]
        assert q["snap_m"][0] < 10 and 701 in [w for _, _, w in q["details"]["osm_way_id"]]
    finally:
        del rr.door


def test_via_route_is_continuous(rr):
    """GPT 77-2 #8 — 경유점은 한 점으로 붙여 앞 구간 도착과 뒤 구간 출발이 이어진다(좌표가 건너뛰지 않는다)."""
    p = rr.route(_pt(37.5, 127.002), _pt(37.510, 127.005), via=[_pt(37.505, 127.0101)])["paths"][0]
    c = p["points"]["coordinates"]
    gaps = [RRM._hav(a[0], a[1], b[0], b[1]) for a, b in zip(c, c[1:])]
    assert max(gaps) < 1200 and sum(gaps) == pytest.approx(p["distance"], rel=0.01)
    assert len(p["snap_m"]) == 3


def test_resolve_does_not_share_across_plain_speed_functions(tmp_path):
    """GPT 77-2 #7 — 속도 함수가 다르면 다른 라우터다(맨 함수는 나눠 쓰지 않는다). 속도 없음(길이 가중)은 폴더로 나눠 쓴다."""
    d = _write(tmp_path, REGION)
    a = RRM.resolve(str(d), speed=lambda *k: 10.0)
    b = RRM.resolve(str(d), speed=lambda *k: 60.0)
    assert a is not b and a.speed is not b.speed
    assert RRM.resolve(str(d)) is RRM.resolve(str(d))


def test_cli_road_graph_default():
    """99 — `--road-graph` 를 안 주면 환경변수 → auto. 회귀 대조도 켠 채가 기본이다(앞 판은 경로 서버 픽스처 값을 지키려 껐다)."""
    from app.modules.travel_ops.mobility.engine.verify_time import road_graph_default as f
    assert f({}) == "auto"
    assert f({"MOBILITY_ROAD_GRAPH": "none"}) == "none"                         # 환경변수가 먼저
