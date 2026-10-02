# -*- coding: utf-8 -*-
"""버스 정류장 무정차·우회(84 · 문제목록 #39 · 2026-10-01) — 노선 전체 중단(route_closed) 하나로 받던 버스 사건을 나눈다.

    stop_skip    {ars, route?, window?}  그 정류장에서 **타거나 내리는** 버스 구간만 불가 · 지나가기만 하면 그대로
    route_detour {route}                 그 노선 버스 구간은 판단불가(소요 모름 · no_data) — 불가가 아니다

두 층:
  게이트(데이터 없음) — 입력 검사 · ARS 표기 · 시간대 겹침 · 일정 짜기의 정류장 거르기를 작은 입력으로 잠근다.
  전체층(실데이터 · mobility_full) — 2016 건대입구역1번출구(05230) → 성수역1번출구(04240) · 사이 성수사거리(04257).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine.errors import CaseInputError
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR
from app.modules.travel_ops.mobility.engine.runtime import Runtime
from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
BUS_LEG = [{"mode": "bus", "route": "2016", "from": "건대입구역1번출구", "to": "성수역1번출구"}]


def _v(disr=()):
    v = Verifier(Timetable(), None, RULES, set())
    v.disr = list(disr)
    return v


# ── 게이트 — 데이터 없이 ───────────────────────────────────────────────
@pytest.mark.parametrize("raw, want", [("05230", "05230"), ("01-126", "01126"), (" 23285 ", "23285"),
                                       ("1234", None), ("ABCDE", None), (None, None), (23285, "23285")])
def test_ars_norm(raw, want):
    assert Verifier.ars_norm(raw) == want


@pytest.mark.parametrize("bad", [
    {"kind": "stop_skip", "ars": "123"},
    {"kind": "stop_skip", "ars": "05230", "window": ["14:00"]},
    {"kind": "stop_skip", "ars": "05230", "window": ["14:00", "13:00"]},
    {"kind": "stop_skip", "ars": "05230", "window": ["두시", "13:00"]},
    {"kind": "route_detour"},
    {"kind": "route_closed"},
])
def test_bad_bus_disruption_is_rejected(bad):
    """모양이 틀린 사고를 조용히 무시하지 않는다 — 무시하면 「사고를 넣었는데 판정이 그대로」가 된다."""
    case = {"id": "BAD", "date": "2026-09-11", "depart_at": "11:00", "legs": BUS_LEG, "disruptions": [bad]}
    with pytest.raises(CaseInputError):
        _v().verify_case(case)


@pytest.mark.parametrize("line", ["02호선", "09호선", "경의선", "인천선", "우이신설경전철", "김포도시철도", "수인분당선", "GTX-A"])
@pytest.mark.parametrize("nm", ["서울역", "강남역", "이대", "왕십리(성동구청)", "역"])
def test_incident_target_names_match_engine_uses(line, nm):
    """사고 출처(코어 · route_events_chain)가 만드는 대상 표기 = 계산기 uses 표기 — 어긋나면 사건을 못 잡는다."""
    from app.infrastructure.travel import route_events_chain as C
    from app.modules.travel_ops.mobility.engine import options as O
    assert C.line_name(line) == O.line_name(line) and C.station_name(nm) == O.station_name(nm)


def test_incident_tables_without_data_dir_are_none(tmp_path):
    from app.infrastructure.travel.route_events_chain import MobilityTables
    assert MobilityTables("").station_table() is None and MobilityTables("").stop_table() is None
    assert MobilityTables(str(tmp_path)).station_table() is None, "파일이 없으면 표 없음 → 확인 못 함"


def test_known_kinds_include_bus_split():
    assert {"stop_skip", "route_detour", "route_closed"} <= set(Verifier.DISR_KINDS)


STOP = {"ars_id": "05230", "station_nm": "건대입구역1번출구"}


def test_stop_skip_matches_route_and_window():
    v = _v([{"kind": "stop_skip", "ars": "05230", "route": "2016", "window": ["13:00", "14:00"]}])
    assert v._stop_skip_at("2016", STOP, 13 * 60 + 30, 13 * 60 + 40) is not None
    assert v._stop_skip_at("2016", STOP, 12 * 60, 12 * 60 + 59) is None, "시간대 앞"
    assert v._stop_skip_at("2016", STOP, 14 * 60 + 1, 14 * 60 + 9) is None, "시간대 뒤"
    assert v._stop_skip_at("2016", STOP, 12 * 60 + 50, None) is not None, "끝을 모르면 그 뒤 전부(보수적)"
    assert v._stop_skip_at("2224", STOP, 13 * 60 + 30, 13 * 60 + 40) is None, "다른 노선은 선다"
    assert v._stop_skip_at("2016", {"ars_id": "04257"}, 13 * 60 + 30, 13 * 60 + 40) is None
    assert v._stop_skip_at("2016", {"ars_id": None}, 13 * 60 + 30, 13 * 60 + 40) is None, "번호 없는 정류장은 대조 안 함"


def test_stop_skip_without_route_or_window_hits_every_route_all_day():
    v = _v([{"kind": "stop_skip", "ars": "05-230"}])
    assert v._stop_skip_at("2224", STOP, 300, 310) is not None and v._stop_skip_at("N62", STOP, 1500, None) is not None


def _planner(disr):
    v = _v()
    pl = P.Planner(Runtime(v, timetable_built_at="t", rules_version="v", stats={}))
    pl.disruptions = tuple(disr)
    return pl


def _tiny_bus():
    """한 노선에 같은 이름의 정류장이 두 번(왕복 노선의 길 건너 짝) — 가(1) 나(2) 다(3) 나(4) 가(5)."""
    from app.modules.travel_ops.mobility.engine.bus import BusRoutes
    route = {"route_id": "R1", "route_nm": "T1", "route_type": "3", "route_type_nm": "간선", "term_min": 10,
             "first_time": "05:00", "last_time": "23:00"}
    stops = [{"route_id": "R1", "route_nm": "T1", "seq": i + 1, "station_nm": nm, "ars_id": f"0000{i + 1}",
              "lat": 37.5 + i * 0.001, "lng": 127.0, "sect_dist_m": 0 if i == 0 else 300}
             for i, nm in enumerate(["가", "나", "다", "나", "가"])]
    return BusRoutes([route], stops)


def test_leg_with_seq_uses_that_row_not_the_name_match():
    """☆GPT 84 #6 — 일정 짜기가 고른 정류장 행을 판정기가 그대로 쓴다. 이름만으로 찾으면 「나→가」는 (4→5)가 아니라
    가장 짧은 짝을 다시 고른다 — 사고로 빼 둔 행이 돌아올 수 있다. 순번이 이름과 안 맞으면 이름으로."""
    v = _v()
    v.bus = _tiny_bus()
    r = v.bus.route("T1")
    by_name = v._bus_seg(r, {"route": "T1", "from": "나", "to": "가"})
    assert (by_name[0]["seq"], by_name[1]["seq"]) == (4, 5)
    pinned = v._bus_seg(r, {"route": "T1", "from": "나", "to": "가", "from_seq": 2, "to_seq": 5})
    assert (pinned[0]["seq"], pinned[1]["seq"], pinned[2]) == (2, 5, 3), "순번으로 고정한 행"
    wrong = v._bus_seg(r, {"route": "T1", "from": "나", "to": "가", "from_seq": 3, "to_seq": 5})
    assert (wrong[0]["seq"], wrong[1]["seq"]) == (4, 5), "순번이 이름과 다르면 이름으로 찾는다"
    v.disr = [{"kind": "stop_skip", "ars": "00004"}]
    assert v._stop_skip_at("T1", pinned[0], 600, 610) is None and v._stop_skip_at("T1", by_name[0], 600, 610) is not None


def test_plan_stop_filter_only_when_stop_skip_overlaps_search_span():
    assert _planner([])._stop_skip_pred(600, 780) is None, "사고가 없으면 앞 판과 같은 정류장 짝"
    assert _planner([{"kind": "line_closed", "line": "02호선"}])._stop_skip_pred(600, 780) is None
    pred = _planner([{"kind": "stop_skip", "ars": "05230", "route": "2016"}])._stop_skip_pred(600, 780)
    assert pred({"ars_id": "05230", "route_nm": "2016"}) and not pred({"ars_id": "05230", "route_nm": "2224"})
    assert not pred({"ars_id": "04257", "route_nm": "2016"})
    late = _planner([{"kind": "stop_skip", "ars": "05230", "window": ["20:00", "21:00"]}])
    assert late._stop_skip_pred(600, 780) is None, "탐색 폭(도착 목표 3시간 전~도착 목표)과 안 겹치면 다른 짝을 찾지 않는다"


# ── 전체층 — 실데이터 ───────────────────────────────────────────────────
_RT = None


def _runtime():
    global _RT
    if _RT is None:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT = build_verifier(quiet=True)
        except RuntimeError as e:
            _RT = e
    if isinstance(_RT, Exception):
        pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
    return _RT


def _judge(disr, depart="11:00"):
    case = {"id": "B84", "date": "2026-09-11", "depart_at": depart, "legs": BUS_LEG, "no_alternatives": True,
            "disruptions": disr}
    res = _runtime()._v.verify_case(case)
    return res.verdict, (res.out or {}).get("code"), res.reason or ""


@pytest.mark.mobility_full
@pytest.mark.parametrize("disr, want", [
    ([], ("feasible", None)),
    ([{"kind": "stop_skip", "ars": "05230"}], ("infeasible", "disruption")),                      # 타는 정류장
    ([{"kind": "stop_skip", "ars": "04240", "route": "2016"}], ("infeasible", "disruption")),     # 내리는 정류장
    ([{"kind": "stop_skip", "ars": "04257"}], ("feasible", None)),                                # 사이(지나감)
    ([{"kind": "stop_skip", "ars": "05230", "route": "2224"}], ("feasible", None)),                # 다른 노선
    ([{"kind": "stop_skip", "ars": "05230", "window": ["13:00", "14:00"]}], ("feasible", None)),   # 시간대 밖
    ([{"kind": "stop_skip", "ars": "05230", "window": ["10:30", "11:05"]}], ("infeasible", "disruption")),
    # GPT 84 #5 — 예정 도착 11:13 · 최악 11:19 사이에 낀 무정차(양 끝만 보면 통과한다)
    ([{"kind": "stop_skip", "ars": "04240", "window": ["11:15", "11:17"]}], ("infeasible", "disruption")),
    ([{"kind": "stop_skip", "ars": "04240", "window": ["11:25", "11:40"]}], ("feasible", None)),   # 최악 도착 뒤
    ([{"kind": "route_detour", "route": "2016"}], ("unknown", "no_data")),                        # 우회 — 판단불가
    ([{"kind": "route_detour", "route": "2016", "window": ["18:00", "20:00"]}], ("feasible", None)),   # 우회 시간대 밖
    ([{"kind": "route_closed", "route": "2016"}], ("infeasible", "disruption")),                  # 종전 그대로
])
def test_full_bus_leg_split(disr, want):
    verdict, code, reason = _judge(disr)
    assert (verdict, code) == want, reason
    if disr and disr[0]["kind"] == "stop_skip" and verdict == "infeasible":
        assert "지나가기는 한다" in reason and ("타는" in reason or "내리는" in reason), reason
    if disr and disr[0]["kind"] == "route_detour" and verdict == "unknown":
        assert "우회" in reason, reason


def _bus_places():
    v = _runtime()._v
    r = v.bus.route("2016")
    a, b, _ = v.bus.segment(r.route_id, "건대입구역1번출구", "성수역1번출구")
    return ({"key": "a", "name": "건대쪽", "lat": a["lat"], "lon": a["lng"]},
            {"key": "b", "name": "성수쪽", "lat": b["lat"], "lon": b["lng"]})


def _plan(disr):
    pl = P.Planner(_runtime(), stage="planning", modes=["bus", "walk"])
    pl.disruptions = tuple(disr)
    a, b = _bus_places()
    got, why = pl.leg(a, b, datetime.fromisoformat("2026-09-11T12:00:00+09:00"), P.party_of(None, {}), True, "b84")
    assert got is not None, why
    route = got[0]
    return next(o for o in route["options"] if o["id"] == route["planned"]), got[4]


@pytest.mark.mobility_full
def test_full_plan_moves_to_next_stop_of_same_route():
    """타는 정류장이 무정차면 같은 노선의 반경 안 다른 정류장으로 — 노선을 버리지 않는다."""
    base, _ = _plan([])
    assert "건대입구역1번출구→성수역1번출구" in base["label"], base
    moved, _ = _plan([{"kind": "stop_skip", "ars": "05230"}])
    assert moved["label"].startswith("버스 2016 ") and "건대입구역1번출구" not in moved["label"], moved


@pytest.mark.mobility_full
def test_full_plan_keeps_default_pair_when_skip_window_misses_the_ride():
    """☆GPT 84 #6 — 무정차 시간대가 탐색 폭(도착 목표 3시간 전~)과 겹쳐도 실제 승차가 그 밖이면 기본 정류장 짝이 남는다."""
    kept, _ = _plan([{"kind": "stop_skip", "ars": "05230", "window": ["09:10", "09:40"]}])
    assert "건대입구역1번출구→성수역1번출구" in kept["label"], kept


@pytest.mark.mobility_full
def test_full_single_station_suspension_cuts_the_edges_around_it(monkeypatch):
    """☆GPT 84 #7 — 역 하나만 적힌 운행 중단은 무정차가 아니다(지나가지도 못한다) → 그 역에 닿는 간선을 끊는다."""
    from app.modules.travel_ops.mobility import wiring
    rt = _runtime()
    monkeypatch.setitem(wiring._STATE, "mode", "enabled")
    monkeypatch.setitem(wiring._STATE, "kw", {})
    monkeypatch.setattr(wiring.engine_runtime, "get_verifier", lambda **kw: rt)
    out, _ = wiring.disruptions_from_events({
        "4호선:선바위": {"effect": "skip_station", "suspended": True, "section": ["선바위", "선바위"]}})
    edges = sorted(tuple(d["between"]) for d in out if d["kind"] == "edge_closed")
    assert len(edges) == 2 and all(e[0] == "선바위" for e in edges), edges
    assert any(d["kind"] == "station_skip" and d["station"] == "선바위" for d in out)
    case = {"id": "S84", "date": "2026-09-11", "depart_at": "11:00", "no_alternatives": True, "disruptions": out,
            "legs": [{"line": "04호선", "from": "사당", "to": "과천"}]}                      # 선바위를 지나가는 구간
    res = rt._v.verify_case(case)
    assert (res.out or {}).get("verdict") == "infeasible" and (res.out or {}).get("code") == "disruption", res.reason


@pytest.mark.mobility_full
def test_full_plan_detour_drops_that_route():
    detour, left = _plan([{"kind": "route_detour", "route": "2016"}])
    assert not detour["label"].startswith("버스 2016 "), detour
    assert any(x.get("label", "").startswith("버스 2016 ") for x in left), "뺀 이유를 남긴다"


@pytest.mark.mobility_full
def test_full_incident_tables_from_data_files():
    """실데이터 표 — 표본 역 코드(0241 이대 · 0150 1호선 서울역 · 1003 용산=코레일) · TOPIS 정류장(14106 · 01126)."""
    from app.modules.travel_ops.mobility.engine import paths
    from app.infrastructure.travel.route_events_chain import MobilityTables
    _runtime()
    t = MobilityTables(str(paths.PROCESSED))
    st = t.station_table()
    assert st["0241"] == ("2호선:이대", True) and st["0150"] == ("1호선:서울역", True)
    assert st["1003"] == ("1호선:용산", False), "코레일 운영 역 — 알림이 안 올 수 있어 「확인 못 함」"
    stops = t.stop_table()
    assert "2016" in stops.routes and stops.routes_by_ars.get("01126") and stops.routes_by_ars.get("14106")


@pytest.mark.mobility_full
def test_full_subway_suspension_section_becomes_edges(monkeypatch):
    """지하철 알림의 운행 중단 구간 → 계산기 구간 차단(edge_closed) 간선들 — 노선 순서표로 사이 역을 채운다."""
    from app.modules.travel_ops.mobility import wiring
    rt = _runtime()
    monkeypatch.setitem(wiring._STATE, "mode", "enabled")
    monkeypatch.setitem(wiring._STATE, "kw", {})
    monkeypatch.setattr(wiring.engine_runtime, "get_verifier", lambda **kw: rt)
    out, _ = wiring.disruptions_from_events({
        "2호선:시청": {"effect": "skip_station", "suspended": True, "section": ["신촌", "시청"]}})
    edges = [tuple(d["between"]) for d in out if d["kind"] == "edge_closed"]
    assert edges == [("신촌", "이대"), ("이대", "아현"), ("아현", "충정로"), ("충정로", "시청")], edges
    assert all(d["line"] == "02호선" for d in out)
