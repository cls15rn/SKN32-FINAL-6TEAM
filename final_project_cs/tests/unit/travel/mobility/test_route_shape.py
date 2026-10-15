# -*- coding: utf-8 -*-
"""지도에 그릴 경로선(`route_shape.py`) — 가짜 라우터·역 좌표로 계획 수단별 모양과 못 그렸을 때의 직선 대체를 본다(자료 없이 돈다)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domains.travel_ops.instances.mobility import route_shape as RS

A, B = (126.9770, 37.5796), (127.0557, 37.5433)          # (경도, 위도) 경복궁 · 성수


class FakeRouter:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def route(self, s, e, profile="car", via=None):
        self.calls.append((profile, tuple(s), tuple(e)))
        if self.fail:
            raise RuntimeError(self.fail)
        mid = [(s[0] + e[0]) / 2, (s[1] + e[1]) / 2 + 0.001]        # 직선이 아니게 한 점 비튼다
        return {"paths": [{"points": {"coordinates": [list(s), mid, list(e)]}}]}


class FakeStations:
    """StationCoords.get(line, station) 흉내 — 노선은 계산기 이름(02호선)으로 온다."""
    def __init__(self, table):
        self.table = table

    def get(self, line, station):
        return self.table.get(f"{line}|{station}")


SC = FakeStations({"02호선|을지로입구": {"lat": 37.5660, "lng": 126.9822}, "02호선|성수": {"lat": 37.5446, "lng": 127.0560},
                   "03호선|경복궁": {"lat": 37.5759, "lng": 126.9735}})


def _rd(planned, **opt):
    return {"planned": planned, "options": [dict({"id": planned, "label": planned, "eta_min": 20, "uses": []}, **opt)]}


def test_kind_follows_the_planned_option_id():
    k = RS._kind
    assert [k({"id": i}) for i in ("walk", "bike", "taxi", "subway_1", "bus_405", "subway_bus_1", "bus_subway_1", "x", None)] == \
        ["walk", "bike", "taxi", "subway", "bus", "mixed", "mixed", "unknown", "unknown"]


@pytest.mark.parametrize("planned,profile", [("walk", "foot"), ("bike", "bike"), ("taxi", "car")])
def test_walk_bike_taxi_use_the_router_with_the_matching_profile(planned, profile):
    r = FakeRouter()
    s = RS.build_shape(A, B, _rd(planned), router=r, sc=SC)
    assert r.calls == [(profile, A, B)]
    assert s["mode"] == planned and s["source"] == "local_road_graph" and s["grade"] == "추정" and s["note"] is None
    assert len(s["line"]["coordinates"]) == 3 and s["line"]["type"] == "LineString" and s["distance_m"] > 8000


def test_subway_joins_stations_in_riding_order_with_walks_at_both_ends():
    r = FakeRouter()
    s = RS.build_shape(A, B, _rd("subway_1", uses=["3호선:경복궁", "2호선:을지로입구", "2호선:성수"]), router=r, sc=SC)
    assert s["mode"] == "subway" and s["source"] == "stations" and s["grade"] == "추정"
    assert [c[0] for c in r.calls] == ["foot", "foot"], "양 끝 걸음만 길찾기 — 역 사이는 역 좌표를 잇는다"
    coords = s["line"]["coordinates"]
    assert coords[0] == list(A) and coords[-1] == list(B)
    stops = [[126.9735, 37.5759], [126.9822, 37.5660], [127.0560, 37.5446]]
    idx = [coords.index(p) for p in stops]
    assert idx == sorted(idx), "탄 순서대로"
    assert "선로 곡선이 아님" in s["note"]


def test_a_failed_walk_at_the_end_falls_back_to_a_straight_piece_and_says_so():
    s = RS.build_shape(A, B, _rd("subway_1", uses=["2호선:을지로입구", "2호선:성수"]), router=FakeRouter(fail="경로 없음"), sc=SC)
    assert s["source"] == "stations" and "걸음은 직선" in s["note"] and "경로 없음" in s["note"]
    assert s["line"]["coordinates"][0] == list(A) and s["line"]["coordinates"][-1] == list(B)


def test_bus_and_unknown_modes_are_straight_lines_without_pretending():
    for planned in ("bus_405", "mystery"):
        s = RS.build_shape(A, B, _rd(planned, uses=["버스:405"]), router=FakeRouter(), sc=SC)
        assert s["source"] == "straight_line" and s["grade"] == "근거없음" and s["line"]["coordinates"] == [list(A), list(B)]
        assert s["note"]
    s = RS.build_shape(A, B, None, router=FakeRouter(), sc=SC)
    assert s["mode"] == "unknown" and s["source"] == "straight_line"


def test_router_down_or_no_router_gives_a_straight_line_with_the_reason():
    s = RS.build_shape(A, B, _rd("taxi"), router=FakeRouter(fail="자료 없음"), sc=SC)
    assert s["source"] == "straight_line" and s["grade"] == "근거없음" and "자료 없음" in s["note"]
    s = RS.build_shape(A, B, _rd("walk"), router=None, sc=None)
    assert s["source"] == "straight_line" and "꺼져" in s["note"]


def test_unknown_stations_do_not_break_the_shape():
    s = RS.build_shape(A, B, _rd("subway_1", uses=["9호선:없는역", "2호선:성수"]), router=FakeRouter(), sc=SC)
    assert s["source"] == "straight_line" and "역 좌표" in s["note"], "좌표를 찾은 역이 둘 미만이면 직선"


def _wiggle(n, amp=0.002):
    """한 번 접히는 곳 없이 계속 꺾이는 선 — 점을 많이 줄일 수 없다(상한에 걸리는 모양)."""
    import math
    return [[126.9 + i * 2e-5, 37.5 + amp * math.sin(i * 0.9)] for i in range(n)]


def _max_dev_m(orig, line):
    """원본 각 점이 줄인 선(꺾은선)에서 떨어진 최대 거리(미터)."""
    import math

    def d(p, a, b):
        kx, ky = math.cos(math.radians(p[1])) * 111320.0, 110574.0
        vx, vy = (b[0] - a[0]) * kx, (b[1] - a[1]) * ky
        wx, wy = (p[0] - a[0]) * kx, (p[1] - a[1]) * ky
        vv = vx * vx + vy * vy
        t = 0.0 if vv == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / vv))
        return math.hypot(wx - t * vx, wy - t * vy)
    return max(min(d(p, line[i], line[i + 1]) for i in range(len(line) - 1)) for p in orig)


def test_point_count_is_capped_and_ends_are_kept():
    coords = _wiggle(1000)
    fit = RS._fit(coords, RS.MAX_POINTS)
    assert len(fit) <= RS.MAX_POINTS and fit[0] == coords[0] and fit[-1] == coords[-1]
    assert RS._fit(coords[:2], RS.MAX_POINTS) == coords[:2]
    assert RS._fit(coords[:5], RS.MAX_POINTS) != [], "점이 적으면 그대로 두거나 줄여도 처음·끝은 남는다"


def test_corners_are_kept_so_a_zoomed_in_line_still_turns():
    """★직선 구간의 점을 솎아도 꺾이는 점은 남는다 — 전에는 고르게 건너뛰어 모퉁이가 잘렸다."""
    east = [[126.9 + i * 1e-4, 37.5] for i in range(200)]
    north = [[126.9 + 199 * 1e-4, 37.5 + j * 1e-4] for j in range(1, 200)]
    corner = east[-1]
    fit = RS._fit(east + north, RS.MAX_POINTS)
    assert corner in fit and len(fit) <= 5, "한 번 꺾이는 선은 모퉁이만 남기고 접힌다"


def test_shape_error_stays_within_the_tolerance_when_under_the_cap():
    coords = _wiggle(300, amp=0.0004)
    fit = RS._fit(coords, 10_000)
    assert _max_dev_m(coords, fit) <= RS.SIMPLIFY_TOLERANCE_M + 1e-6
    assert len(fit) < len(coords)


def test_detail_returns_more_points_than_the_default_for_a_long_road_route():
    class Dense:
        def route(self, s, e, profile="car", via=None):
            n = 400
            pts = [[s[0] + (e[0] - s[0]) * i / n, s[1] + (e[1] - s[1]) * i / n + 0.0006 * ((i // 7) % 2) + 1e-5 * ((i % 2) * 2 - 1)] for i in range(n + 1)]   # 1 m 안팎 잔물결
            return {"paths": [{"points": {"coordinates": pts}}]}
    plain = RS.build_shape(A, B, _rd("taxi"), router=Dense(), sc=SC)
    full = RS.build_shape(A, B, _rd("taxi"), router=Dense(), sc=SC, detail=True)
    assert len(plain["line"]["coordinates"]) <= RS.MAX_POINTS
    assert len(full["line"]["coordinates"]) > len(plain["line"]["coordinates"])
    assert full["source"] == plain["source"] == "local_road_graph"


def _item(seq, kind, lat=None, lon=None, detail=None, name=None):
    place = {"name": name or f"p{seq}", "latitude": lat, "longitude": lon} if lat is not None else None
    return SimpleNamespace(seq=seq, kind=kind, item_id=uuid4(), title=name or f"t{seq}", place=place, detail=detail or {})


def test_shapes_for_items_pairs_each_move_with_the_places_around_it(monkeypatch):
    monkeypatch.setattr(RS, "_engine", lambda: (FakeRouter(), SC))
    a, mv, b = _item(1, "activity", 37.5796, 126.9770, name="경복궁"), _item(2, "mobility", detail={"route_def": _rd("taxi")}), \
        _item(3, "dining", 37.5433, 127.0557, name="성수 식당")
    loose = _item(4, "mobility")                      # 뒤에 장소가 없다 → 그릴 곳이 없다
    out = RS.shapes_for_items([b, loose, mv, a])      # 순서가 뒤섞여 와도 seq 로 본다
    assert len(out) == 1
    s = out[0]
    assert s["item_id"] == str(mv.item_id) and s["from_item_id"] == str(a.item_id) and s["to_item_id"] == str(b.item_id)
    assert s["from"] == "경복궁" and s["to"] == "성수 식당" and s["mode"] == "taxi"


def test_a_place_without_coordinates_is_skipped_not_used_as_an_endpoint(monkeypatch):
    monkeypatch.setattr(RS, "_engine", lambda: (FakeRouter(), SC))
    a, no_xy, mv, b = _item(1, "activity", 37.5796, 126.9770), _item(2, "activity"), _item(3, "mobility"), _item(4, "dining", 37.5433, 127.0557)
    out = RS.shapes_for_items([a, no_xy, mv, b])
    assert len(out) == 1 and out[0]["from_item_id"] == str(a.item_id), "좌표 없는 장소는 건너뛰고 그 앞 장소에서 그린다"


# ── 접수 확인 화면(등록 전) 경로선 — 저장된 검사(items · moves)에서 ──────────────────────────────────
def _review(moves, **places):
    items = [{"id": k, "title": k, "place": ({"name": k, "latitude": v[1], "longitude": v[0]} if v else None)} for k, v in places.items()]
    return {"items": items, "moves": moves}


def test_review_shapes_follow_the_stored_mode_and_use_the_item_ids(monkeypatch):
    router = FakeRouter()
    monkeypatch.setattr(RS, "_engine", lambda: (router, SC))
    review = _review([{"from": "0-0", "to": "0-1", "mode": "walk", "uses": []},
                      {"from": "0-1", "to": "0-2", "mode": "subway", "uses": ["2호선:을지로입구", "2호선:성수"]}],
                     **{"0-0": A, "0-1": B, "0-2": (127.0600, 37.5400)})
    out = RS.shapes_for_review(review)
    assert [(s["from_item_id"], s["to_item_id"], s["mode"]) for s in out] == [("0-0", "0-1", "walk"), ("0-1", "0-2", "subway")]
    assert out[0]["source"] == "local_road_graph" and out[1]["source"] == "stations"
    assert all("item_id" not in s and s["from"] and s["to"] for s in out)
    assert ("foot", A, B) in router.calls


def test_review_shapes_skip_moves_without_coordinates_and_say_why_for_old_reviews(monkeypatch):
    monkeypatch.setattr(RS, "_engine", lambda: (FakeRouter(), SC))
    review = _review([{"from": "0-0", "to": "0-1", "mode": "walk"},
                      {"from": "0-1", "to": "0-2", "mode": "subway"},          # 옛 검사 — 탄 역 정보(uses) 없음
                      {"from": "0-2", "to": "0-3", "mode": "estimate"}],
                     **{"0-0": None, "0-1": A, "0-2": B, "0-3": (127.0600, 37.5400)})
    out = RS.shapes_for_review(review)
    assert [(s["from_item_id"], s["to_item_id"]) for s in out] == [("0-1", "0-2"), ("0-2", "0-3")], "좌표 없는 장소가 낀 이동은 건너뛴다"
    assert out[0]["source"] == "straight_line" and "탄 역 정보가 없어" in out[0]["note"]
    assert out[1]["mode"] == "unknown" and out[1]["grade"] == "근거없음"
    assert RS.shapes_for_review(None) == [] and RS.shapes_for_review({"items": [], "moves": []}) == []


# ── 지하철: 탄 역~내린 역 사이의 모든 역(2026-10-05 — cs 세션 요청서, 사용자 지적) ──────────────────────────────

class FakeLO:
    """LineOrder 흉내 — path(호선, a, b) 가 사이 역을 포함한 목록을, is_loop 가 순환선 여부를 준다."""
    def __init__(self, table, loops=()):
        self.table, self.loops = table, set(loops)

    def path(self, line, a, b):
        return self.table.get((line, a, b))

    def is_loop(self, line):
        return line in self.loops


SC3 = FakeStations({
    "03호선|경복궁": {"lat": 37.5759, "lng": 126.9735}, "03호선|안국": {"lat": 37.5765, "lng": 126.9854},
    "03호선|종로3가": {"lat": 37.5717, "lng": 126.9917}, "03호선|을지로3가": {"lat": 37.5662, "lng": 126.9926},
    "02호선|을지로3가": {"lat": 37.5663, "lng": 126.9927}, "02호선|성수": {"lat": 37.5446, "lng": 127.0560},
})
LO3 = FakeLO({("03호선", "경복궁", "을지로3가"): ["경복궁", "안국", "종로3가", "을지로3가"]})


def test_subway_draws_every_station_between_boarding_and_alighting():
    r = FakeRouter()
    two = RS.build_shape(A, B, _rd("subway_1", uses=["3호선:경복궁", "3호선:을지로3가"]), router=r, sc=SC3)
    full = RS.build_shape(A, B, _rd("subway_1", uses=["3호선:경복궁", "3호선:을지로3가"]), router=r, sc=SC3, lo=LO3)
    assert len(full["line"]["coordinates"]) == len(two["line"]["coordinates"]) + 2, "안국·종로3가 두 역이 더해진다"
    assert full["source"] == "stations" and full["grade"] == "추정"
    assert full["rides"] == [{"line": "3호선", "from": "경복궁", "to": "을지로3가",
                              "stations": ["경복궁", "안국", "종로3가", "을지로3가"], "count": 4, "filled": True}]
    assert "모든 역" in full["note"] and "선로 곡선이 아님" in full["note"]


def test_without_line_order_only_the_scheduled_stations_are_joined_and_the_note_says_so():
    s = RS.build_shape(A, B, _rd("subway_1", uses=["3호선:경복궁", "3호선:을지로3가"]), router=FakeRouter(), sc=SC3)
    assert s["rides"][0]["filled"] is False and s["rides"][0]["count"] == 2
    assert "중간 역을 못 채웠다" in s["note"]


def test_a_station_without_coordinates_never_breaks_the_line():
    lo = FakeLO({("03호선", "경복궁", "을지로3가"): ["경복궁", "좌표없는역", "종로3가", "을지로3가"]})
    s = RS.build_shape(A, B, _rd("subway_1", uses=["3호선:경복궁", "3호선:을지로3가"]), router=FakeRouter(), sc=SC3, lo=lo)
    assert s["source"] == "stations"
    assert s["rides"][0]["stations"] == ["경복궁", "종로3가", "을지로3가"], "좌표 없는 역은 건너뛰고 나머지를 잇는다"


def test_a_loop_line_warns_that_the_direction_may_differ():
    sc = FakeStations({"02호선|시청": {"lat": 37.5640, "lng": 126.9770}, "02호선|충정로": {"lat": 37.5600, "lng": 126.9640},
                       "02호선|아현": {"lat": 37.5575, "lng": 126.9560}})
    lo = FakeLO({("02호선", "시청", "아현"): ["시청", "충정로", "아현"]}, loops={"02호선"})
    s = RS.build_shape(A, B, _rd("subway_1", uses=["2호선:시청", "2호선:아현"]), router=FakeRouter(), sc=sc, lo=lo)
    assert "순환선" in s["note"] and "운행 방향과 다를 수 있다" in s["note"]


def test_a_transfer_fills_each_ride_on_its_own_line():
    lo = FakeLO({("03호선", "경복궁", "을지로3가"): ["경복궁", "안국", "종로3가", "을지로3가"],
                 ("02호선", "을지로3가", "성수"): ["을지로3가", "성수"]})
    s = RS.build_shape(A, B, _rd("subway_1", uses=["3호선:경복궁", "3호선:을지로3가", "2호선:을지로3가", "2호선:성수"]),
                       router=FakeRouter(), sc=SC3, lo=lo)
    assert [r["line"] for r in s["rides"]] == ["3호선", "2호선"]
    assert [r["count"] for r in s["rides"]] == [4, 2]


def test_rides_are_only_reported_for_station_lines():
    assert "rides" not in RS.build_shape(A, B, _rd("taxi"), router=FakeRouter(), sc=SC3)
    assert "rides" not in RS.build_shape(A, B, _rd("bus_405", uses=["버스:405"]), router=FakeRouter(), sc=SC3)


# ── `[2026-10-15 사용자 결정 — ST4F-495]` 걷는 구간(`walks`) — 화면이 그 구간만 걸음 모양(회색 점)으로 그린다 ──

def test_a_walk_is_one_walking_stretch_and_rides_by_car_or_bike_have_none():
    walk = RS.build_shape(A, B, _rd("walk"), router=FakeRouter(), sc=SC)
    assert walk["walks"] == [[0, len(walk["line"]["coordinates"]) - 1]]
    for planned in ("taxi", "bike"):
        assert RS.build_shape(A, B, _rd(planned), router=FakeRouter(), sc=SC)["walks"] == []


def test_straight_lines_have_no_walking_stretch_even_for_a_walk():
    """직선 대체는 길을 모르는 선이다 — 걸음으로 그리지 않는다(화면은 흐린 긴 점선)."""
    assert RS.build_shape(A, B, _rd("walk"), router=None, sc=None)["walks"] == []
    assert RS.build_shape(A, B, _rd("bus_405", uses=["버스:405"]), router=FakeRouter(), sc=SC)["walks"] == []


def test_a_subway_walks_from_the_start_to_the_first_station_and_from_the_last_station_to_the_end():
    s = RS.build_shape(A, B, _rd("subway_1", uses=["3호선:경복궁", "2호선:을지로입구", "2호선:성수"]), router=FakeRouter(), sc=SC)
    coords, walks = s["line"]["coordinates"], s["walks"]
    assert len(walks) == 2
    (h0, h1), (t0, t1) = walks
    assert h0 == 0 and coords[h1] == [126.9735, 37.5759], "첫 걸음은 처음 → 탄 역(경복궁)"
    assert coords[t0] == [127.0560, 37.5446] and t1 == len(coords) - 1, "마지막 걸음은 내린 역(성수) → 끝"
    assert h1 < t0, "그 사이가 타는 구간"


def test_a_failed_walk_is_still_a_walking_stretch():
    s = RS.build_shape(A, B, _rd("subway_1", uses=["2호선:을지로입구", "2호선:성수"]), router=FakeRouter(fail="경로 없음"), sc=SC)
    coords = s["line"]["coordinates"]
    assert s["walks"] == [[0, coords.index([126.9822, 37.5660])], [len(coords) - 2, len(coords) - 1]]


def test_simplifying_keeps_the_boundaries_between_walking_and_riding():
    """걸음 길이 점이 많아 줄어도 경계 점(탄 역 · 내린 역)은 남고, 번호는 줄인 선을 가리킨다."""
    class Wiggly:
        def route(self, s, e, profile="car", via=None):
            n = 600
            return {"paths": [{"points": {"coordinates": [[s[0] + (e[0] - s[0]) * i / n, s[1] + (e[1] - s[1]) * i / n + 0.002 * ((i % 2) * 2 - 1)]
                                                          for i in range(n + 1)]}}]}
    s = RS.build_shape(A, B, _rd("subway_1", uses=["2호선:을지로입구", "2호선:성수"]), router=Wiggly(), sc=SC)
    coords, ((h0, h1), (t0, t1)) = s["line"]["coordinates"], s["walks"]
    assert len(coords) <= RS.MAX_POINTS
    assert h0 == 0 and t1 == len(coords) - 1
    assert coords[h1] == [126.9822, 37.5660] and coords[t0] == [127.0560, 37.5446]
