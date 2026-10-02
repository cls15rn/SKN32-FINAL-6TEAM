# -*- coding: utf-8 -*-
"""사고를 계산기 조건으로 옮기고, 저장된 대안이 다 막히면 계산기로 새 경로 — 이동 계산기 문제목록(2026-09-29) #38·#39."""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from app.modules.travel_ops.itinerary import Item
from app.modules.travel_ops.itinerary_changes import ItineraryChange, NoChange, plan_route_adjustment
from app.modules.travel_ops.mobility import wiring

T = lambda hm: datetime.fromisoformat(f"2026-10-07T{hm}:00+09:00")  # noqa: E731


def test_39_events_become_engine_disruptions_only_when_meaning_matches():
    out, unmapped = wiring.disruptions_from_events({
        "2호선:잠실": {"effect": "skip_station", "summary": "무정차"},
        "경의중앙선:용산": {"effect": "skip_station"},
        "9호선:*": {"effect": "line_closed"},
        "버스:2224": {"effect": "route_closed"},
        "도로:세종대로": {"effect": "road_control"}})
    kinds = {(d["kind"], d.get("line"), d.get("station"), d.get("route")) for d in out}
    assert ("station_skip", "02호선", "잠실", None) in kinds, "2호선 → 시간표 표기 02호선"
    assert ("station_skip", "경의선", "용산", None) in kinds, "공식명 → 시간표 노선명"
    assert ("line_closed", "09호선", None, None) in kinds and ("route_closed", None, None, "2224") in kinds
    assert unmapped == ["도로:세종대로"], "도로 통제는 뜻이 달라 옮기지 않고 이름으로 돌려준다"


def test_39_bus_events_split_by_stop_detour_and_unknown():
    """☆84(2026-10-01) — 버스 사건을 노선 전체 중단 하나로 옮기지 않는다.
    정류장을 아는 무정차 → 정류장 단위(stop_skip) · 우회(도로 통제) → 소요 모름(route_detour) · 정류장 모르는 무정차 → 종전대로."""
    out, unmapped = wiring.disruptions_from_events({
        "버스:2016": {"effect": "skip_station", "stops": ["05230", "01-126"],
                     "window_start": "2026-10-07T13:00:00+09:00", "window_end": "2026-10-07T14:00:00+09:00"},
        "버스:405": {"effect": "road_control", "detour": True},
        "버스:2224": {"effect": "skip_station"},
        "버스:740": {"effect": "route_closed"}})
    assert unmapped == []
    got = sorted((d["kind"], d.get("route"), d.get("ars"), tuple(d.get("window") or ())) for d in out)
    assert got == [("route_closed", "2224", None, ()), ("route_closed", "740", None, ()),
                   ("route_detour", "405", None, ()),
                   ("stop_skip", "2016", "01-126", ("13:00", "14:00")),
                   ("stop_skip", "2016", "05230", ("13:00", "14:00"))], got
    assert not any(d["kind"] == "route_closed" and d["route"] == "2016" for d in out), "정류장을 알면 노선 전체를 막지 않는다"


def test_39_bus_stop_window_across_days_is_dropped():
    """시간대가 날을 넘기면 window 를 싣지 않는다 — 계산기는 늘 무정차로 본다(보수적)."""
    out, _ = wiring.disruptions_from_events({
        "버스:2016": {"effect": "skip_station", "stops": ["05230"],
                     "window_start": "2026-10-07T23:00:00+09:00", "window_end": "2026-10-08T01:00:00+09:00"}})
    assert out[0]["kind"] == "stop_skip" and "window" not in out[0]


def test_36_subway_suspension_without_engine_closes_the_line():
    """지하철 알림의 운행 중단(suspended) — 계산기가 꺼져 노선 순서를 모르면 노선 전체 line_closed(보수적) ·
    같은 조건이 대상마다 겹쳐 와도 하나로. 구간 양 끝이 같은 역이면 그 역 무정차로."""
    wiring.configure(data_dir=None)
    out, _ = wiring.disruptions_from_events({
        "2호선:시청": {"effect": "skip_station", "suspended": True, "section": ["시청", "충정로"]},
        "2호선:아현": {"effect": "skip_station", "suspended": True, "section": ["시청", "충정로"]},
        "4호선:선바위": {"effect": "skip_station", "suspended": True, "section": ["선바위", "선바위"]}})
    got = sorted((d["kind"], d["line"], d.get("station")) for d in out)
    assert got == [("line_closed", "02호선", None), ("line_closed", "04호선", None)], \
        f"역 하나만 적힌 운행 중단도 무정차로 낮추지 않는다(지나가지도 못한다 · GPT 84 #7): {got}"


def test_84_events_keep_per_stop_windows_and_stacked_subway_incidents():
    """☆GPT 84 #3 — 정류장마다 제 시간대 · 한 역에 운행 중단과 무정차가 같이 걸리면 둘 다 계산기 조건으로."""
    wiring.configure(data_dir=None)
    out, _ = wiring.disruptions_from_events({
        "버스:2016": {"effect": "skip_station", "stops": ["05230", "04240"], "stop_windows": [
            {"ars": "05230", "start": "2026-10-07T10:00:00+09:00", "end": "2026-10-07T11:00:00+09:00"},
            {"ars": "04240", "start": "2026-10-07T10:30:00+09:00", "end": "2026-10-07T12:00:00+09:00"}]},
        "버스:405": {"effect": "road_control", "detour": True,
                    "window_start": "2026-10-07T12:00:00+09:00", "window_end": "2026-10-07T24:00:00+09:00"},
        "2호선:이대": {"effect": "skip_station", "suspended": True, "section": None, "station_skipped": True}})
    by = {(d["kind"], d.get("ars") or d.get("route") or d.get("station") or d.get("line")): d for d in out}
    assert by[("stop_skip", "05230")]["window"] == ["10:00", "11:00"]
    assert by[("stop_skip", "04240")]["window"] == ["10:30", "12:00"], "시간대를 첫 공지 것으로 뭉개지 않는다"
    assert "window" not in by[("route_detour", "405")], "24:00 처럼 못 옮기는 시각이면 시간대 없이(늘 우회 · 보수적)"
    assert ("line_closed", "02호선") in by and ("station_skip", "이대") in by


def _place(name, lat, lon):
    return {"place_id": str(uuid4()), "name": name, "latitude": lat, "longitude": lon}


def _scene():
    a = Item(item_id=uuid4(), seq=1, kind="activity", title="잠실", place_id=None, starts_at=T("09:00"),
             ends_at=T("10:40"), place=_place("잠실 타워", 37.512, 127.102))
    route = {"from": "잠실", "to": "성수", "planned": "subway_1",
             "options": [{"id": "subway_1", "label": "2호선 잠실→성수", "eta_min": 13, "uses": ["2호선:잠실", "2호선:성수"]}]}
    move = Item(item_id=uuid4(), seq=2, kind="mobility", title="잠실 → 성수", place_id=None, starts_at=T("10:50"),
                ends_at=T("11:03"), detail={"route_def": route})
    b = Item(item_id=uuid4(), seq=3, kind="activity", title="성수", place_id=None, starts_at=T("11:15"),
             ends_at=T("12:00"), place=_place("성수 쇼룸", 37.5445, 127.056))
    return a, move, b, route


EVENTS = {"2호선:잠실": {"effect": "skip_station", "summary": "2호선 잠실 무정차"}}


def test_38_blocked_everywhere_asks_engine_with_disruptions(monkeypatch):
    a, move, b, route = _scene()
    seen = {}

    def leg_planner(party_size, constraints, *, disruptions=None):
        seen["disruptions"] = disruptions

        def leg(pa, pb, arrive, not_before):
            seen["not_before"] = not_before
            new = {"from": pa["name"], "to": pb["name"], "planned": "bus_2224",
                   "options": [{"id": "bus_2224", "label": "버스 2224", "eta_min": 25, "uses": ["버스:2224"]}]}
            return {"route": new, "starts_at": arrive - timedelta(minutes=35), "ends_at": arrive - timedelta(minutes=10),
                    "eta_min": 25, "left_out": []}, None
        return leg
    monkeypatch.setattr(wiring, "leg_planner", leg_planner)
    plan = plan_route_adjustment(item=move, following=b, route=route, events=EVENTS, now=T("10:30"), previous=a)
    assert isinstance(plan, ItineraryChange), plan
    assert seen["disruptions"][0]["kind"] == "station_skip" and seen["disruptions"][0]["line"] == "02호선"
    assert seen["not_before"] == T("10:40"), "앞 일정이 끝난 뒤부터 떠난다"
    new = next(iter(plan.replacements.values()))
    assert new.detail["route_def"]["options"][0]["uses"] == ["버스:2224"] and "route" not in new.detail
    assert plan.summary["rerouted_by"] == "mobility_engine"


def test_38_engine_off_keeps_old_unresolved(monkeypatch):
    a, move, b, route = _scene()
    monkeypatch.setattr(wiring, "leg_planner", lambda *a, **k: None)
    plan = plan_route_adjustment(item=move, following=b, route=route, events=EVENTS, now=T("10:30"), previous=a)
    assert isinstance(plan, NoChange) and plan.status == "unresolved"


def _bus_scene():
    a, move, b, _ = _scene()
    route = {"from": "건대", "to": "성수", "planned": "bus_2016",
             "options": [{"id": "bus_2016", "label": "버스 2016", "eta_min": 13, "uses": ["버스:2016"]}]}
    return a, move.replaced_by(place=None, title=move.title, starts_at=move.starts_at, ends_at=move.ends_at,
                               detail={"route_def": route}), b, route


def _spy(monkeypatch):
    seen = {}

    def leg_planner(party_size, constraints, *, disruptions=None):
        seen["disruptions"] = disruptions

        def leg(pa, pb, arrive, not_before):
            new = {"from": pa["name"], "to": pb["name"], "planned": "bus_2016b",
                   "options": [{"id": "bus_2016b", "label": "버스 2016 다음 정류장", "eta_min": 15, "uses": ["버스:2016"]}]}
            return {"route": new, "starts_at": arrive - timedelta(minutes=30), "ends_at": arrive - timedelta(minutes=15),
                    "eta_min": 15, "left_out": []}, None
        return leg
    monkeypatch.setattr(wiring, "leg_planner", leg_planner)
    return seen


def test_84_bus_stop_skip_event_reaches_engine_as_stop_condition(monkeypatch):
    """☆84 — TOPIS 정류장 무정차(`skip_station` + stops)는 저장된 버스 후보를 빼고(재계획이 아는 효과) 계산기에
    정류장 단위 조건으로 간다 — 계산기가 같은 노선의 다른 정류장을 찾을 수 있다(노선 전체를 막지 않는다)."""
    a, move, b, route = _bus_scene()
    seen = _spy(monkeypatch)
    events = {"버스:2016": {"effect": "skip_station", "stops": ["05230"], "summary": "버스 2016 정류장 05230 무정차",
                           "window_start": "2026-10-07T10:00:00+09:00", "window_end": "2026-10-07T12:00:00+09:00"}}
    plan = plan_route_adjustment(item=move, following=b, route=route, events=events, now=T("10:30"), previous=a)
    assert isinstance(plan, ItineraryChange), plan
    assert [(d["kind"], d["route"], d["ars"], d.get("window")) for d in seen["disruptions"]] == \
        [("stop_skip", "2016", "05230", ["10:00", "12:00"])]


def test_84_bus_detour_event_reaches_engine_as_detour(monkeypatch):
    """☆84 — TOPIS 우회(`road_control` + detour)는 저장 후보가 「통제 구간 소요 모름」으로 빠지고 계산기에 우회 조건으로."""
    a, move, b, route = _bus_scene()
    seen = _spy(monkeypatch)
    events = {"버스:2016": {"effect": "road_control", "detour": True, "summary": "버스 2016 임시우회"}}
    plan = plan_route_adjustment(item=move, following=b, route=route, events=events, now=T("10:30"), previous=a)
    assert isinstance(plan, ItineraryChange), plan
    assert [(d["kind"], d["route"]) for d in seen["disruptions"]] == [("route_detour", "2016")]
