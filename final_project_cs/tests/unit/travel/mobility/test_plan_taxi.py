# -*- coding: utf-8 -*-
"""택시를 일정 계산 수단으로 — 문제목록 #47 (2026-10-04).

가짜 택시 서비스로 후보 모양·역산·이유·계획 수단 순서를 보고(자료 없이 돈다), 실제 자료가 있으면 로컬 길찾기로 한 구간을 끝까지 본다.
택시는 modes 에 "taxi" 를 줄 때만 후보가 된다 — 기본 호출은 앞 판과 같다(아래 시험이 못 박는다).
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine.car import RouterDown

A = {"name": "경복궁", "lat": 37.5796, "lon": 126.9770}
B = {"name": "성수", "lat": 37.5433, "lon": 127.0557}


class FakeCar:
    """CarService.leg 와 같은 모양으로 답한다 — 출발 시각이 이르면 막혀서 느리다고 흉내."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def leg(self, s, e, depart, taxi=False, kind="중형"):
        if self.fail:
            raise RouterDown(self.fail)
        self.calls.append((s, e, depart, taxi))
        slow = 2400 if depart.hour < 9 else 1800            # 출근 시간 40분 · 그 밖 30분
        return {"distance_m": 9800.0, "topis_time_s": slow, "fare_won": 11700, "grade": "추정"}


def _planner(car, modes=("taxi", "walk")):
    p = object.__new__(P.Planner)
    p.v = SimpleNamespace(car=car)
    p.modes = set(modes)
    return p


def test_taxi_is_a_known_mode_but_not_a_default_one():
    assert "taxi" in P.KNOWN_MODES and "taxi" not in P.DEFAULT_MODES
    with pytest.raises(ValueError):
        P.Planner.__init__(object.__new__(P.Planner), None, modes=["boat"])


def test_taxi_option_shape_and_backward_time_fit():
    car, left = FakeCar(), []
    o = _planner(car)._taxi_option(A, B, date(2026, 10, 7), 12 * 60, 10, left)        # 12:00 도착 목표
    assert left == [] and o["_taxi"] is True and o["_legs"] == [] and o["uses"] == []
    assert o["eta_min"] == 30 and o["_fare"] == 11700 and o["_walk_m"] == 0
    assert o["_start"] == 12 * 60 - 30 - 10, "도착 목표 − 소요 − 여유 정책 버퍼"
    assert "11,700원" in o["_route"] and "하한" in o["_route"] and "[추정]" in o["_route"]
    assert len(car.calls) == 2, "첫 추정 출발로 재고, 그 소요로 출발을 다시 맞춰 한 번 더"


def test_taxi_option_refits_when_the_departure_hour_changes_the_speed():
    car = FakeCar()
    o = _planner(car)._taxi_option(A, B, date(2026, 10, 7), 9 * 60 + 20, 10, [])       # 09:20 도착 → 출발은 08:xx 대
    assert o["eta_min"] == 40, "출근 시간대 출발이라 40분으로 다시 맞춘다"
    assert o["_start"] == 9 * 60 + 20 - 40 - 10


def test_no_car_service_or_no_route_leaves_a_reason_instead_of_inventing_a_taxi():
    left = []
    assert _planner(None)._taxi_option(A, B, date(2026, 10, 7), 720, 10, left) is None
    assert left[-1]["code"] == "taxi_unavailable" and "꺼짐" in left[-1]["reason"]
    left = []
    assert _planner(FakeCar(fail="경로 없음"))._taxi_option(A, B, date(2026, 10, 7), 720, 10, left) is None
    assert left[-1]["code"] == "taxi_no_route" and "경로 없음" in left[-1]["reason"]


def _opt(start, kind=None, n=0, legs=()):
    o = {"_start": start, "_transfers": 0, "eta_min": 10, "_n": n, "_legs": list(legs)}
    if kind == "taxi":
        o["_taxi"] = True
    return o


def test_taxi_is_chosen_only_when_no_other_group_can_be_planned():
    walk, taxi = _opt(600, n=0), _opt(650, "taxi", n=300)          # 택시가 더 늦게 떠나도 된다
    planned, _ = P.Planner._choose_planned([walk, taxi], None, lambda o: None)
    assert planned is walk, "도보·대중교통 무리가 먼저 — 택시는 마지막 무리"
    planned, _ = P.Planner._choose_planned([taxi], None, lambda o: None)
    assert planned is taxi
    planned, _ = P.Planner._choose_planned([taxi], 700, lambda o: None)          # 앞 일정이 700분에 끝남 → 택시 출발 650 은 못 맞춤
    assert planned is None


# ── 실제 자료(있을 때만) ─────────────────────────────────────────────────────
def _real_runtime():
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    try:
        return build_verifier(quiet=True, local_router=True)
    except RuntimeError as ex:
        pytest.skip(f"이동 자료 없음 — 자료 기기에서만 돈다({str(ex)[:60]})")


@pytest.fixture(scope="module")
def real_rt():
    from app.modules.travel_ops.mobility.engine import paths
    before = (paths.DATA_DIR, paths.SOURCE)
    rt = _real_runtime()
    yield rt
    paths._layout(before[0], before[1])


def _items():
    return [{"seq": 1, "kind": "activity", "title": "경복궁", "place": "g", "starts_at": "2026-10-07T09:30:00+09:00",
             "ends_at": "2026-10-07T11:00:00+09:00"},
            {"seq": 2, "kind": "dining", "title": "성수 점심", "place": "s", "starts_at": "2026-10-07T12:30:00+09:00"}]


PLACES = [{"key": "g", "name": "경복궁", "lat": 37.5796, "lon": 126.9770},
          {"key": "s", "name": "성수 식당", "lat": 37.5433, "lon": 127.0557}]


def test_real_data_taxi_mode_lists_a_taxi_option_with_time_and_fare(real_rt):
    out = P.plan(PLACES, _items(), 2, {}, runtime=real_rt, modes=["taxi", "walk"])
    assert not out["skipped"], out["skipped"]
    route = next(iter(out["routes"].values()))
    taxi = next(o for o in route["options"] if o["id"] == "taxi")
    assert taxi["eta_min"] >= 15 and taxi["fare_krw"] >= 8000 and taxi["uses"] == []
    assert "택시" in taxi["label"] and "하한" in taxi["label"]
    assert route["planned"] == "taxi", "도보 상한 밖이라 택시만 남는다"


def test_real_data_default_modes_never_list_a_taxi(real_rt):
    out = P.plan(PLACES, _items(), 2, {}, runtime=real_rt)
    route = next(iter(out["routes"].values()))
    assert all(not o["id"].startswith("taxi") for o in route["options"])
