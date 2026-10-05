# -*- coding: utf-8 -*-
"""택시·자동차 서비스(engine/car.py CarService · CarGraph) 단위 시험 — 길찾기는 팀장 `graph_router.GraphRouter`.

☆101(2026-10-05 · 합치기) 이 파일은 `test_road_router_v1.py`(77 · 우리 파이썬 도로 라우터 시험)를 옮긴 것이다. 우리 라우터
  `road_router.py` 를 내리고 길찾기를 팀장 것 하나로 맞췄다(본인 10/5). 그래서
  · **라우터 자체를 보던 시험 13건은 없앴다**(polyline 왕복 · 자료 없음 · 스냅 200 m 상한 · 섬 · 일방통행 · 중앙분리 차로 ·
    범위 밖 · 시간 가중 · 자전거 간선 · 승하차 지점이 자동차전용도로를 피함 · 경유점 이어짐 · 속도 함수별 라우터 분리) —
    팀장 길찾기의 같은 종류 시험은 `test_graph_router.py`(21건)에 있다. 팀장 길찾기에 **없는 동작 셋**은 닫힘 문서에 적었다
    (스냅 한도 200 m → 1,200 m · 승하차 지점의 자동차전용도로 회피 없음 · 경로 고르는 가중이 TOPIS 정적 시간 → 도로급 고정 속도).
  · **서비스 쪽 시험 12건은 팀장 길찾기 위로 옮겼다**(합성 그래프는 그대로 · `GraphRouter(records=…)`): 응답 모양 → 소요 ·
    서비스 순서(길찾기 → 근거없음) · 경로 서버 클라이언트 없음 · 합성 경로 대역 · 도착 목표에 맞춘 가장 늦은 출발 4 · 최악 소요(p10)·
    도로명 · 명령줄 기본값 · 실데이터 2(전체층).
게이트 층(데이터 없이 돈다) + 전체층(mobility_full · 실데이터).
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

import pytest

from app.modules.travel_ops.mobility.engine.car import (CarGraph, CarService, GRAPH_SOURCE_ID,  # noqa: E402
                                                        RouterDown, hav)
from app.modules.travel_ops.mobility.engine.graph_router import GraphRouter  # noqa: E402


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
    L = sum(hav((a[1], a[0]), (b[1], b[0])) for a, b in zip(pts, pts[1:]))
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


@pytest.fixture()
def rr():
    """합성 그래프 위 팀장 길찾기 — 파일 없이(records)."""
    return GraphRouter(records={"nodes": [{"id": i, "lat": la, "lon": lo, "r": r} for i, (la, lo, r) in N.items()],
                                "edges": [dict(e) for e in EDGES]})


def _pt(lat, lon):
    return (lon, lat)


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
    svc = CarService(_mini_graph(), rr, RULES)
    out = svc.leg(_pt(37.5, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    assert out["source_id"][1] == rr.source_id == GRAPH_SOURCE_ID and out["grade"] == "근거없음"   # 전부 골목 → 기존 등급 규칙 그대로
    with pytest.raises(RouterDown) as ex:                      # 길찾기 실패 → 넘길 곳 없음 → 그 이유·갈래 그대로(99 · 101)
        svc.leg(_pt(37.6, 127.105), _pt(37.5, 127.005), dt.datetime(2026, 10, 6, 10, 0))
    assert ex.value.code in ("no_path", "out_of_area"), ex.value.code
    plain = CarService(_mini_graph(), None, RULES)             # 길찾기 없음 = 근거없음
    with pytest.raises(RouterDown, match="도로 경로 계산 없이") as ex2:
        plain.leg(_pt(37.5, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    assert ex2.value.code == "router_down"


def test_router_down_code_is_read_from_the_router_message():
    """101 — 팀장 길찾기는 못 낸 갈래를 문장 머리로 낸다. 코드를 따로 안 줘도 그 머리를 읽는다(택시 칸이 이유를 가른다)."""
    assert RouterDown("out_of_area: 좌표 근처에 길이 없다").code == "out_of_area"
    assert RouterDown("no_path: 이어지지 않는다").code == "no_path"
    assert RouterDown("그 밖").code == "router_down"
    assert RouterDown("no_path: x", code="depart_unconfirmed").code == "depart_unconfirmed"


def test_no_route_server_client_left():
    """99(2026-10-04) · 101(2026-10-05) — **경로 서버를 부르는 코드가 엔진에 없다**(클라이언트·주소 인자·주소 환경변수·네트워크 모듈).
    ☆101 에서 뜻이 바뀐 곳: 앞 판은 `make_router`·`NoRouter`·`BikeRouter` 라는 **이름**이 없어야 했다. 팀장 판을 받으면서 그 이름들은
      다시 있다(명령줄·시험 대역 · 자전거·걷기가 길찾기를 꺼내는 자리) — 대신 그 자리에 올 수 있는 것이 **로컬 길찾기와 시험 대역뿐**
      임을 잠근다(서버 주소를 주면 거절)."""
    import inspect

    from app.modules.travel_ops.mobility.engine import bike, car, graph_router, runtime, verify_time, verify_time_cli
    assert not hasattr(car, "GraphHopperClient")
    for mod in (car, bike, graph_router, runtime, verify_time, verify_time_cli):
        src = inspect.getsource(mod)
        assert "GraphHopperClient" not in src.replace("`GraphHopperClient`", ""), mod.__name__    # 주석의 옛 이름 언급은 뺀다
        assert "MOBILITY_GH_URL" not in src, mod.__name__
    for mod in (car, graph_router, runtime):
        assert "urllib" not in inspect.getsource(mod), mod.__name__      # bike.py 의 urllib 은 따릉이 실시간 거치 조회(#50)
    assert "gh_url" not in inspect.signature(runtime.build_verifier).parameters
    for bad in ("http://localhost:8989", "https://example.invalid"):
        with pytest.raises(ValueError):
            car.make_router(bad)
    assert car.make_router("none").info() is None


def test_fixture_router_sits_in_the_router_slot(tmp_path):
    """99 — 합성 경로 대역은 길찾기 자리에 끼운다(`--road-graph fixture:<파일>`) · 없는 키·down 은 RouterDown."""
    from app.modules.travel_ops.mobility.engine.car_fixtures import FixtureRouter
    from app.modules.travel_ops.mobility.engine.verify_time_cli import road_of
    f = tmp_path / "fx.json"
    f.write_text(json.dumps({"routes": {"127.0020,37.5000|127.0080,37.5000": {"down": True}}}), encoding="utf-8")
    fx = road_of(f"fixture:{f}")
    assert isinstance(fx, FixtureRouter)
    svc = CarService(_mini_graph(), fx, RULES)
    with pytest.raises(RouterDown, match="다운"):
        svc.leg(_pt(37.5, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    with pytest.raises(RouterDown, match="픽스처에 경로가 없다"):
        svc.leg(_pt(37.6, 127.002), _pt(37.5, 127.008), dt.datetime(2026, 10, 6, 10, 0))
    assert road_of("none") is None and road_of("") is None
    assert road_of(str(tmp_path / "no_such_graph")) is None          # 폴더에 그래프가 없으면 없음(근거없음)


# ── 전체층(실데이터) ──────────────────────────────────────────────────────
def _real():
    from app.modules.travel_ops.mobility.engine import paths
    if paths.SOURCE in ("unset",):
        paths.load_cli_env()
    d = paths.PROCESSED / "mobility"
    if not (GraphRouter.default().available() and (d / "graph" / "topis_class_factor_v1.json").exists()):
        pytest.skip(f"road graph not present: {d}")
    return d


@pytest.mark.mobility_full
def test_real_seoul_station_gangnam():
    d = _real()
    from app.modules.travel_ops.mobility.engine.timeutil import HolidayCalendar
    from app.modules.travel_ops.mobility.engine.paths import RULES_DIR
    hol = HolidayCalendar.from_doc(json.loads((RULES_DIR / "holidays_2026_2027.json").read_text(encoding="utf-8")))
    cg = CarGraph.load(d / "graph", hol)
    res = GraphRouter.default().route((126.9707, 37.5547), (127.0276, 37.4979))
    c = cg.compute(res, dt.datetime(2026, 9, 22, 18, 0))
    assert 9000 < c["distance_m"] < 12500                   # 네이버 12.0 km · 옛 경로 서버 10.5 km
    assert 25 * 60 < c["topis_time_s"] < 45 * 60             # 옛 경로 서버 33.5분 · 77 판(우리 옛 라우터) 35.1분
    assert c["coverage_pct"]["topis"] > 70


@pytest.mark.mobility_full
def test_real_runtime_taxi_alternative():
    """서버 적재 경로(runtime.build_verifier · local_router=True)에서 경로 서버 없이 택시 대안이 소요·요금으로 나온다."""
    _real()
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    try:
        rt = build_verifier(quiet=True, seoul_key="", local_router=True)
    except RuntimeError as e:
        pytest.skip(f"data not present: {e}")
    assert rt.stats["car_router"] in ("road_graph_v2", "road_graph_v1") and rt.stats["router"] == "local"
    case = {"id": "RR-TAXI", "date": "2026-09-11", "depart_at": "24:55",
            "legs": [{"line": "02호선", "from": "잠실", "to": "성수"}]}
    res = rt.verify_case(case)
    tx = res.taxi
    assert tx["verdict"] == "feasible" and tx["grade"] == "추정", tx
    assert 4000 < tx["distance_m"] < 9000 and 5 <= tx["ride_min"] <= 20
    assert tx["car"]["source_id"][1].startswith("osm_road_graph")
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
    off = copy.copy(rt._v)                                                     # 택시 서비스를 뺀 같은 판정기
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
    svc = CarService(_graph_with_link(), rr, RULES)
    arrive = dt.datetime(2026, 10, 6, 11, 0)
    out = svc.arrive_by(_pt(37.5, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    assert out["worst_time_s"] >= out["topis_time_s"] and out["grade"] == "추정"
    worst_min = -(-out["worst_time_s"] // 60)
    assert out["depart_dt"] + dt.timedelta(minutes=worst_min) <= arrive          # 느린 쪽으로도 닿는다
    assert out["depart_dt"] + dt.timedelta(minutes=worst_min + 1) > arrive       # 1분 더 늦으면 못 닿는다 = 가장 늦은 출발
    assert out["roads"][0]["name"] == "시험대로" and out["source_id"][1] == GRAPH_SOURCE_ID
    assert out["p10_pct"] == 100.0 and out["access_m"] < 1 and out["access_s"] <= 1   # 길 위의 점 — 걷는 몫 없음
    with pytest.raises(RouterDown) as ex:                                         # 못 내는 갈래가 code 로 나온다
        svc.arrive_by(_pt(38.2, 128.3), _pt(37.5, 127.005), arrive, taxi=False)   # 그래프에서 아주 먼 점(1,200 m 안에 길 없음)
    assert ex.value.code == "out_of_area"
    with pytest.raises(RouterDown) as ex:
        svc.arrive_by(_pt(37.6, 127.105), _pt(37.5, 127.005), arrive, taxi=False)  # 따로 떨어진 간선(20–21) — 이을 길 없음
    assert ex.value.code == "no_path"


def test_arrive_by_finds_later_window_across_hour_boundary(rr):
    """GPT 77-2 #1 — 11시대는 빠르고 12시대는 느리다. 12:05 도착 목표면 12시대 출발은 못 닿지만 11:59 출발은 닿는다.
    「못 닿는 분이 나오면 멈춤」이면 더 이른 값에서 끝난다 — 하한에서 뒤로 훑어 처음 성립하는 분을 고른다."""
    g = _graph_with_link()
    for h in range(24):
        g.prof[("L1", "평일", h)] = 30.0 if h == 11 else 3.0        # 약 528 m: 11시대 약 1분 · 그 밖 약 11분
        g.p10[("L1", "평일", h)] = g.prof[("L1", "평일", h)]
    svc = CarService(g, rr, RULES)
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
    svc = CarService(g, rr, RULES)
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
    svc = CarService(_graph_with_link(), rr, RULES)
    arrive = dt.datetime(2026, 10, 6, 11, 0)
    on = svc.arrive_by(_pt(37.5, 127.002), _pt(37.5, 127.008), arrive, taxi=False)
    off = svc.arrive_by(_pt(37.5009, 127.002), _pt(37.5, 127.008), arrive, taxi=False)      # 출발지가 길에서 100 m
    assert 95 < off["access_m"] < 105 and 125 < off["access_s"] < 145
    assert off["worst_time_s"] - on["worst_time_s"] == pytest.approx(off["access_s"], abs=3)
    assert off["depart_dt"] < on["depart_dt"]                         # 그만큼 일찍 떠나야 한다
    assert off["topis_time_s"] == on["topis_time_s"]                  # 도로 소요 자체는 같다(걷는 몫은 따로)


def test_cli_road_graph_default():
    """99 · 101 — `--road-graph` 를 안 주면 auto. 회귀 대조도 켠 채가 기본이다. 환경변수 MOBILITY_ROAD_GRAPH 는 읽지 않는다(101)."""
    from app.modules.travel_ops.mobility.engine.verify_time_cli import road_graph_default as f
    assert f() == "auto" and f({"MOBILITY_ROAD_GRAPH": "none"}) == "auto"
