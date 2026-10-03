# -*- coding: utf-8 -*-
"""못 채운 구간을 택시 소요로 메우기(leg/plan(taxi_fallback=True)) — 97번 방(2026-10-03 · 66건 #27 둘째 겹).

세 겹: ① 계산기 대중교통 ② 안 되면 택시 소요(추정 · 「택시 기준」 표시) ③ 그것도 안 되면 근거없음(지금처럼 skipped).
켤 때만 한다 — 끄면(기본) 결과가 앞 판과 같다. 택시를 계획 수단 후보에 나란히 넣는 것(#47)이 아니다.

두 층:
  게이트(데이터 없음) — 이유 dict 에 붙는 `taxi` 칸 모양 · 표시 문구 · 앞 일정과 겹치면 싣지 않음(모자란 분) · 택시가 못 내는
    갈래 · 끈 실행·수단을 좁힌 호출은 그대로 · 운행일 경계 · by_mode 값 재사용 · plan() 봉투(셋째 묶음 `taxi_fallback` ·
    skipped·kept_unverified 와 겹치지 않음 · 입력 이동 교체 · 계약 칸 · uses 자가 검사 · 끄면 칸 없음).
  전체층(실데이터 · mobility_full) — 대중교통이 되는 구간 무변화 · 막차 뒤 구간 · 범위 밖 장소 · 앞 일정과 겹침 · 식(출발 +
    느린 쪽 소요 ≤ 다음 일정 시작) · 라우터를 끈 실행은 끈 것과 같은 결과.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.modules.travel_ops.mobility.engine import options as O
from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine.car import RouterDown

SDATE = date(2026, 10, 6)
PL_A, PL_B = {"key": "a", "name": "가", "lat": 37.56, "lon": 126.98}, {"key": "b", "name": "나", "lat": 37.51, "lon": 127.10}
LATE = {"code": "arrive_late", "reason": "앞 항목이 끝난 뒤(10:30) 떠나서는 11:00 도착에 맞는 후보가 없다"}


class _Car:
    """CarService 자리 — arrive_by 만. 값 또는 RouterDown."""

    def __init__(self, ret=None, exc=None):
        self.ret, self.exc, self.calls = ret, exc, []

    def arrive_by(self, s, e, arrive, taxi=True, kind="중형"):
        self.calls.append((s, e, arrive, taxi))
        if self.exc is not None:
            raise self.exc
        return dict(self.ret)


def _car_ret(dep=(2026, 10, 6, 10, 35), eta_s=1200, worst_s=1500, grade="추정", default_pct=3.0, fare=11100,
             access_m=0.0, access_s=0):
    return {"grade": grade, "distance_m": 9400.0, "topis_time_s": eta_s, "worst_time_s": worst_s,
            "access_m": access_m, "access_s": access_s, "p10_pct": 88.4, "depart_dt": datetime(*dep), "fare_won": fare,
            "coverage_pct": {"topis": 90.0, "class": 7.0, "default": default_pct},
            "roads": [{"name": "세종대로", "m": 2100.0}, {"name": "대로", "m": 900.0}, {"name": "을지로", "m": 800.0}]}


def _stub(car=None, modes=None, car_why=None):
    pl = P.Planner.__new__(P.Planner)
    pl.modes = set(P.DEFAULT_MODES) if modes is None else set(modes)
    pl.v = SimpleNamespace(car=car)
    if car_why:
        pl.v.car_why = car_why
    pl.last_by_mode = None
    return pl


def _fb(pl, nb=630, arrive_by=660, why=LATE, sdate=SDATE):
    return pl._taxi_fallback(PL_A, PL_B, sdate, arrive_by, nb, dict(why))


# ── 이유 dict 에 붙는 칸 ──────────────────────────────────────────────────
def test_found_shape_and_label():
    pl = _stub(_Car(_car_ret()))
    out = _fb(pl)
    assert (out["code"], out["reason"]) == (LATE["code"], LATE["reason"])          # 대중교통이 안 된 이유는 그대로
    t = out["taxi"]
    assert t["status"] == "found" and t["basis"] == "taxi_fallback" and t["id"] == P.TAXI_FALLBACK_ID == "taxi_1"
    assert t["label"].startswith("택시 기준 · 추정 · 가→나 · 9.4 km")                 # 표시 문구(본인 10/3)
    assert "호출·승차 대기 제외" in t["label"] and "하한" in t["label"]
    assert t["legs"] == [{"mode": "taxi", "from": "가", "to": "나"}] and t["transfers"] == 0
    assert (t["depart_at"], t["arrive_at"]) == ("2026-10-06T10:35:00+09:00", "2026-10-06T10:55:00+09:00")
    assert (t["eta_min"], t["worst_min"], t["fare_krw"], t["walk_m"], t["slow_speed_pct"]) == (20, 25, 11100, 0, 88)
    assert t["uses"] == ["도로:세종대로", "도로:을지로"] and not O.uses_problems(t["uses"])   # uses 자가 검사(팀 표기)
    assert "within_range" not in t                                                  # 범위 표시는 by_mode 의 것
    assert t["road_control_checked"] is False and "도로 통제" not in t["label"]     # (GPT 97 #2) 미반영을 늘 칸으로
    (_s, _e, arrive, taxi), = pl.v.car.calls
    assert taxi is True and (arrive.hour, arrive.minute) == (11, 0)                 # 다음 일정 시작에 맞춘다
    txt = json.dumps(out, ensure_ascii=False)
    assert '"lat"' not in txt and '"lon"' not in txt and '"_' not in txt            # 경로 저장 금지 — 좌표·내부 칸 없음


def test_fare_unknown_and_walk_note():
    t = _fb(_stub(_Car(_car_ret(fare=None))))["taxi"]
    assert t["status"] == "found" and "fare_krw" not in t                           # 요금을 모르면 키가 없다
    t = _fb(_stub(_Car(_car_ret(dep=(2026, 10, 6, 10, 30), access_m=190.0, access_s=256, worst_s=1756))))["taxi"]
    assert (t["eta_min"], t["worst_min"], t["walk_m"]) == (25, 30, 190) and "걷는 약 190 m" in t["label"]


def test_disruptions_note_road_control_not_reflected():
    """GPT 97 #2 — 사고 조건이 걸린 호출이면 사람이 읽는 label 에도 「도로 통제·우회 미반영」을 적는다."""
    pl = _stub(_Car(_car_ret()))
    pl.disruptions = ({"kind": "station_skip", "line": "02호선", "station": "성수"},)
    t = _fb(pl)["taxi"]
    assert t["status"] == "found" and t["road_control_checked"] is False
    assert t["label"].endswith(" · 도로 통제·우회 미반영") and t["label"].startswith("택시 기준 · 추정 · ")


def test_zero_minute_taxi_is_not_loaded():
    """GPT 97 #5 — 거리·걷기 모두 0(같은 자리)인 0분 택시는 이동으로 싣지 않는다(starts_at == ends_at 항목을 만들지 않는다)."""
    out = _fb(_stub(_Car(_car_ret(dep=(2026, 10, 6, 11, 0), eta_s=0, worst_s=0))), nb=None)
    assert out["taxi"] == {"status": "none", "code": "zero_distance", "reason": out["taxi"]["reason"]}
    assert "0분" in out["taxi"]["reason"] and "택시로도 못 채움" in out["reason"]
    assert _fb(_stub(_Car(_car_ret(dep=(2026, 10, 6, 10, 59), eta_s=1, worst_s=1))), nb=None)["taxi"]["eta_min"] == 1


def test_earliest_search_does_not_wipe_by_mode():
    """GPT 97 #1 — earliest() 는 같은 인스턴스의 leg() 를 다시 불러 last_by_mode 를 비운다. 바깥 호출의 값을 지켜야
    by_mode 줄이 남고 택시 칸을 다시 계산하지 않는다(by_mode + earliest_on_late + taxi_fallback 을 같이 켠 구간)."""
    pl = _stub(_Car(_car_ret()))
    slot = pl._taxi_slot(PL_A, PL_B, SDATE, 660, 600)
    bm = {"modes": {"taxi": slot}}
    pl.last_by_mode = bm

    def inner(*a, **k):
        pl.last_by_mode = None                       # 안쪽 leg() 호출이 하는 일
        return None, {"code": "not_found", "reason": "x"}
    pl.earliest = inner
    e = pl._earliest_or_reason(PL_A, PL_B, None, {}, True, "t", None)
    assert e["status"] == "not_found" and pl.last_by_mode is bm
    assert _fb(pl)["taxi"]["status"] == "found" and len(pl.v.car.calls) == 1        # 택시 칸 재사용 — 도로 계산 1회 그대로

    def boom(*a, **k):
        pl.last_by_mode = None
        raise RuntimeError("x")
    pl.earliest = boom
    with pytest.raises(RuntimeError):
        pl._earliest_or_reason(PL_A, PL_B, None, {}, True, "t", None)
    assert pl.last_by_mode is bm                                                    # 예외 뒤에도 되돌린다


def test_overlap_with_previous_item_is_not_loaded():
    """택시 출발이 앞 일정 끝보다 이르면 싣지 않는다 — 이유에 모자란 분 · 정각이면 싣는다."""
    out = _fb(_stub(_Car(_car_ret())), nb=640)                                      # 10:35 출발 < 10:40 끝
    t = out["taxi"]
    assert t == {"status": "none", "code": "before_prev_end", "short_min": 5, "eta_min": 20, "worst_min": 25,
                 "reason": t["reason"]}
    assert "10:40" in t["reason"] and "10:35" in t["reason"] and "5분 모자람" in t["reason"] and "대기 제외" in t["reason"]
    assert out["code"] == "arrive_late" and out["reason"].startswith(LATE["reason"] + " · 택시로도 못 채움 — ")
    assert "depart_at" not in t and "label" not in t                                # 이동으로 쓸 값은 내지 않는다
    assert _fb(_stub(_Car(_car_ret())), nb=635)["taxi"]["status"] == "found"        # 앞 일정 끝 = 택시 출발(겹치지 않음)
    assert _fb(_stub(_Car(_car_ret())), nb=None)["taxi"]["status"] == "found"       # 앞 일정 끝을 안 주면 겹침 판단 없음


def test_taxi_cannot_answer_stays_skipped_with_reason():
    for code in ("out_of_area", "no_snap", "no_path", "router_down", "depart_unconfirmed"):
        out = _fb(_stub(_Car(exc=RouterDown("x", code=code))))
        assert out["taxi"] == {"status": "none", "code": code, "reason": P.Planner.TAXI_NONE[code]}, code
        assert out["reason"] == f"{LATE['reason']} · 택시로도 못 채움 — {P.Planner.TAXI_NONE[code]}"
        assert out["code"] == "arrive_late"                                         # 대표 코드는 대중교통 쪽 그대로
    out = _fb(_stub(_Car(_car_ret(grade="근거없음", default_pct=62.0))))             # 골목 절반 초과 — 숫자를 내지 않는다
    assert out["taxi"]["code"] == "low_coverage" and "eta_min" not in out["taxi"] and "택시로도 못 채움" in out["reason"]
    no_coord = _stub(_Car(_car_ret()))._taxi_fallback({"name": "가"}, PL_B, SDATE, 660, 630, dict(LATE))
    assert no_coord["taxi"]["code"] == "no_data"


def test_router_off_and_narrowed_modes_return_reason_untouched():
    """도로 경로 계산을 끈 실행·대중교통을 고르지 않은 호출은 이유를 그대로 — 끈 것과 같은 결과."""
    why = dict(LATE)
    assert _stub(None)._taxi_fallback(PL_A, PL_B, SDATE, 660, 630, why) is why
    for cw in ("no_graph", "no_profile", "router_off"):
        assert _stub(None, car_why=cw)._taxi_fallback(PL_A, PL_B, SDATE, 660, 630, why) is why
    car = _Car(_car_ret())
    for modes in ({"bike", "walk"}, {"walk"}, {"bike"}):                            # 자전거 테마 등 — 수단을 뺀 것은 호출 쪽 뜻
        assert _stub(car, modes=modes)._taxi_fallback(PL_A, PL_B, SDATE, 660, 630, why) is why
    assert car.calls == [] and "taxi" not in why
    for modes in ({"subway", "walk"}, {"bus"}, {"subway", "bus", "walk", "bike"}):
        assert _fb(_stub(_Car(_car_ret()), modes=modes))["taxi"]["status"] == "found"


def test_service_day_boundary():
    """운행일 축 — 자정 뒤 출발(24:xx)과 04:00 앞 출발."""
    # 다음 일정 00:30(운행일 10/6 의 1470분) · 택시 00:05 출발 · 앞 일정 24:00 끝(1440)
    t = _fb(_stub(_Car(_car_ret(dep=(2026, 10, 7, 0, 5)))), nb=1440, arrive_by=1470)["taxi"]
    assert t["status"] == "found" and t["depart_at"] == "2026-10-07T00:05:00+09:00"
    out = _fb(_stub(_Car(_car_ret(dep=(2026, 10, 7, 0, 5)))), nb=1450, arrive_by=1470)   # 앞 일정 00:10 끝
    assert out["taxi"]["code"] == "before_prev_end" and out["taxi"]["short_min"] == 5 and "00:10" in out["taxi"]["reason"]
    # 다음 일정 04:10(운행일 10/6 의 250분) · 택시 03:50 출발 = 앞 운행일(−10분이 아니라 230분)
    assert _fb(_stub(_Car(_car_ret(dep=(2026, 10, 6, 3, 50)))), nb=None, arrive_by=250)["taxi"]["status"] == "found"
    out = _fb(_stub(_Car(_car_ret(dep=(2026, 10, 6, 3, 50)))), nb=240, arrive_by=250)    # 앞 일정 04:00 끝
    assert out["taxi"]["code"] == "before_prev_end" and out["taxi"]["short_min"] == 10
    out = _fb(_stub(_Car(_car_ret(dep=(2026, 10, 6, 3, 50)))), nb=225, arrive_by=250)    # 앞 일정 03:45 끝(전 운행일)
    assert out["taxi"]["status"] == "found"


def test_reuses_by_mode_taxi_slot():
    """by_mode 를 같이 켰으면 그 택시 칸 값을 다시 쓴다 — 도로 경로 계산 1회."""
    pl = _stub(_Car(_car_ret()))
    slot = pl._taxi_slot(PL_A, PL_B, SDATE, 660, 600)
    assert slot["within_range"] is True and len(pl.v.car.calls) == 1
    pl.last_by_mode = {"modes": {"taxi": slot}}
    t = _fb(pl)["taxi"]
    assert len(pl.v.car.calls) == 1 and t["status"] == "found" and t["label"].startswith("택시 기준 · 추정 · ")
    assert slot["label"].startswith("택시 가→나") and "basis" not in slot and "within_range" in slot   # 칸 값은 안 바뀐다


# ── plan() 봉투 ───────────────────────────────────────────────────────────
PLACES = [{"key": "a", "name": "가", "lat": 37.56, "lon": 126.98}, {"key": "b", "name": "나", "lat": 37.51, "lon": 127.10},
          {"key": "c", "name": "다", "lat": 37.58, "lon": 126.97}, {"key": "d", "name": "라", "lat": 37.55, "lon": 126.99}]
ITEMS = [{"seq": 1, "kind": "activity", "title": "가", "place": "a",
          "starts_at": "2026-10-06T09:00:00+09:00", "ends_at": "2026-10-06T10:00:00+09:00"},
         {"seq": 2, "kind": "mobility", "title": "옛 이동", "route": "old_ab", "detail": {"x": 1},
          "starts_at": "2026-10-06T10:05:00+09:00", "ends_at": "2026-10-06T10:50:00+09:00"},
         {"seq": 3, "kind": "activity", "title": "나", "place": "b",
          "starts_at": "2026-10-06T11:00:00+09:00", "ends_at": "2026-10-06T12:00:00+09:00"},
         {"seq": 4, "kind": "mobility", "title": "옛 이동 2", "route": "old_bc",
          "starts_at": "2026-10-06T12:00:00+09:00", "ends_at": "2026-10-06T12:20:00+09:00"},
         {"seq": 5, "kind": "activity", "title": "다", "place": "c",
          "starts_at": "2026-10-06T12:30:00+09:00", "ends_at": "2026-10-06T13:30:00+09:00"},
         {"seq": 6, "kind": "activity", "title": "라", "place": "d",
          "starts_at": "2026-10-06T15:00:00+09:00", "ends_at": "2026-10-06T16:00:00+09:00"}]
TAXI_OK = {"status": "found", "basis": "taxi_fallback", "id": "taxi_1",
           "label": "택시 기준 · 추정 · 가→나 · 9.4 km · 호출·승차 대기 제외 · 요금은 호출료·정차·시계외 할증을 뺀 하한",
           "legs": [{"mode": "taxi", "from": "가", "to": "나"}], "uses": ["도로:세종대로"],
           "depart_at": "2026-10-06T10:35:00+09:00", "arrive_at": "2026-10-06T10:55:00+09:00",
           "eta_min": 20, "worst_min": 25, "transfers": 0, "walk_m": 12, "fare_krw": 11100, "slow_speed_pct": 88}
TAXI_NO = {"status": "none", "code": "no_snap", "reason": P.Planner.TAXI_NONE["no_snap"]}
WALK = ({"from": "다", "to": "라", "planned": "walk", "options": [{"id": "walk", "label": "도보", "eta_min": 30, "uses": []}]},
        14 * 60 + 20, 14 * 60 + 50, SDATE, [])


def _plan(monkeypatch, taxi_fallback, by_mode=False, earliest=None):
    """판정기 없이 plan() 봉투만 — Planner 를 가짜로(가→나: 택시로 메움 · 나→다: 택시도 못 냄 · 다→라: 도보로 만듦)."""
    seen = []

    class FakePlanner:
        disruptions = ()

        def __init__(self, runtime, **kw):
            self.trace, self.last_by_mode = None, None

        def leg(self, a, b, arrive_dt, party, first_visit, case_id, **kw):
            seen.append((a["key"], b["key"], kw.get("taxi_fallback")))
            self.last_by_mode = ({"range_from": None, "range_to": None, "range_min": None,
                                  "modes": {k: {"status": "none", "code": "no_data", "reason": "x"} for k in P.BY_MODE_KEYS}}
                                 if kw.get("by_mode") else None)
            if (a["key"], b["key"]) == ("c", "d"):
                return WALK, None
            why = dict(LATE, left_out=[{"label": "2호선", "code": "before_prev_end", "reason": "r"}])
            if earliest:
                why["earliest"] = earliest
            if kw.get("taxi_fallback"):
                why["taxi"] = dict(TAXI_OK if a["key"] == "a" else TAXI_NO)
                if a["key"] != "a":
                    why["reason"] += " · 택시로도 못 채움 — " + TAXI_NO["reason"]
            return None, why
    monkeypatch.setattr(P, "Planner", FakePlanner)
    rt = SimpleNamespace(timetable_built_at="t", rules_version="r")
    out = P.plan(PLACES, ITEMS, runtime=rt, taxi_fallback=taxi_fallback, by_mode=by_mode)
    return out, seen


def test_plan_envelope_third_group(monkeypatch):
    on, seen = _plan(monkeypatch, True)
    assert seen == [("a", "b", True), ("b", "c", True), ("c", "d", True)]
    row, = on["taxi_fallback"]
    key = row["route"]
    assert key == "a_to_b" and (row["from"], row["to"], row["from_place"], row["to_place"]) == ("가", "나", "a", "b")
    assert (row["code"], row["reason"]) == (LATE["code"], LATE["reason"])           # 대중교통이 안 된 이유 그대로
    assert row["left_out"] and row["taxi"]["worst_min"] == 25 and row["taxi"]["basis"] == "taxi_fallback"
    assert row["replaced_input_moves"] == [{"title": "옛 이동", "route": "old_ab",          # (GPT 97 #3) 대체 이력
                                            "starts_at": "2026-10-06T10:05:00+09:00"}]
    assert "status" not in row["taxi"] and "id" not in row["taxi"]
    # routes — 계약 칸만 · 옵션 하나 · 표시 문구 · uses 자가 검사
    r = on["routes"][key]
    assert r == {"from": "가", "to": "나", "planned": "taxi_1",
                 "options": [{"id": "taxi_1", "label": TAXI_OK["label"], "eta_min": 20, "walk_m": 12, "fare_krw": 11100,
                              "uses": ["도로:세종대로"]}]}
    assert P.core_routes(on["routes"])[key] == r and not O.uses_problems(r["options"][0]["uses"])
    # 이동 항목 — 입력 이동을 우리 값으로 바꾸되 다른 칸(detail)은 잃지 않는다
    mv = next(it for it in on["items"] if it.get("route") == key)
    assert (mv["starts_at"], mv["ends_at"], mv["kind"], mv["title"]) == (
        "2026-10-06T10:35:00+09:00", "2026-10-06T10:55:00+09:00", "mobility", "가 → 나")
    assert mv["detail"] == {"x": 1} and not any(it.get("route") == "old_ab" for it in on["items"])
    # 택시도 못 낸 구간 — 지금처럼 skipped + 입력 이동은 검증 안 된 것으로 · 이유에 택시도 안 됨
    sk, = on["skipped"]
    assert (sk["from"], sk["to"], sk["code"], sk["taxi"]) == ("나", "다", "arrive_late", TAXI_NO)
    assert "택시로도 못 채움" in sk["reason"] and sk["kept_input_moves"] == 1
    assert [(k["route"], k["why"]) for k in on["kept_unverified"]] == [("old_bc", "arrive_late")]
    assert [it["seq"] for it in on["items"]] == list(range(1, len(on["items"]) + 1))


def test_plan_three_groups_do_not_mix(monkeypatch):
    """66건 #40 — 출력 이동 항목은 「검증된 이동」·「택시 기준 추정」·「검증 안 된 이동」 중 정확히 하나다."""
    on, _ = _plan(monkeypatch, True)
    taxi_keys = {r["route"] for r in on["taxi_fallback"]}
    unv = {(k["route"], k["starts_at"]) for k in on["kept_unverified"]}
    groups = []
    for it in on["items"]:
        if it["kind"] != "mobility":
            continue
        g = [(it.get("route"), it["starts_at"]) in unv, it.get("route") in taxi_keys,
             it.get("route") in on["routes"] and it.get("route") not in taxi_keys]
        assert sum(g) == 1, (it, g)
        groups.append(g.index(True))
    assert sorted(groups) == [0, 1, 2]                                              # 검증 안 됨 1 · 택시 1 · 검증됨 1
    assert taxi_keys <= set(on["routes"]) and not taxi_keys & {s.get("route") for s in on["skipped"]}
    assert all(on["routes"][k]["planned"] == P.TAXI_FALLBACK_ID for k in taxi_keys)
    assert not any(o["id"].startswith("taxi") for k, r in on["routes"].items() if k not in taxi_keys for o in r["options"])


def test_plan_off_is_unchanged_and_has_no_key(monkeypatch):
    off, seen = _plan(monkeypatch, False)
    assert [x[2] for x in seen] == [False, False, False]
    assert "taxi_fallback" not in off
    assert set(off) == {"items", "routes", "skipped", "left_out", "not_linked", "kept_unverified", "basis"}
    assert [s["to"] for s in off["skipped"]] == ["나", "다"] and all("taxi" not in s for s in off["skipped"])
    assert list(off["routes"]) == ["c_to_d"]
    on, _ = _plan(monkeypatch, True)
    assert on["routes"]["c_to_d"] == off["routes"]["c_to_d"]                        # 만든 구간은 그대로
    assert [it for it in on["items"] if it.get("route") == "c_to_d"] == [it for it in off["items"] if it.get("route") == "c_to_d"]


def test_plan_keeps_earliest_and_links_by_mode_row(monkeypatch):
    ear = {"status": "found", "arrive_by": "2026-10-06T11:06:00+09:00"}
    on, _ = _plan(monkeypatch, True, by_mode=True, earliest=ear)
    assert on["taxi_fallback"][0]["earliest"] == ear                                # 가장 이른 도착(86)도 같이 남는다
    assert [x["route"] for x in on["by_mode"]] == ["a_to_b", None, "c_to_d"]        # 택시로 메운 구간의 줄은 그 routes 키


def test_plan_route_key_avoids_reserved(monkeypatch):
    items = [dict(it) for it in ITEMS]
    items[1]["route"] = "a_to_b"                                                    # 입력이 이미 쓰는 키
    monkeypatch.setattr(P, "Planner", type("F", (), {
        "disruptions": (), "__init__": lambda self, rt, **kw: setattr(self, "last_by_mode", None) or setattr(self, "trace", None),
        "leg": lambda self, a, b, *x, **kw: (None, dict(LATE, taxi=dict(TAXI_OK)))}))
    out = P.plan(PLACES[:2], items[:3], runtime=SimpleNamespace(timetable_built_at="t", rules_version="r"),
                 routes={"a_to_b_2": {}}, taxi_fallback=True)
    assert out["taxi_fallback"][0]["route"] == "a_to_b_3" and "a_to_b_3" in out["routes"]


def test_cli_flag_and_plan_doc_pass_through(monkeypatch):
    got = {}
    monkeypatch.setattr(P, "plan", lambda *a, **kw: got.update(kw) or {})
    P.plan_doc({"places": [], "items": []}, runtime=object(), taxi_fallback=True)
    assert got["taxi_fallback"] is True
    P.plan_doc({"places": [], "items": []}, runtime=object())
    assert got["taxi_fallback"] is False


# ── 전체층 — 실데이터 ─────────────────────────────────────────────────────
_RT = {}
MYEONGDONG = {"key": "md", "name": "명동", "lat": 37.5609, "lon": 126.9862}
GYEONGBOK = {"key": "gb", "name": "경복궁", "lat": 37.5796, "lon": 126.977}
HONGDAE = {"key": "hd", "name": "홍대", "lat": 37.5572, "lon": 126.9245}
JAMSIL = {"key": "js", "name": "잠실", "lat": 37.5133, "lon": 127.1001}
SINYONGSAN = {"key": "sy", "name": "신용산", "lat": 37.5292, "lon": 126.968}
NAMSEONG = {"key": "ns", "name": "남성", "lat": 37.4847, "lon": 126.971}
BUSAN = {"key": "bs", "name": "부산역", "lat": 35.1151, "lon": 129.0415}


def _runtime(road_graph=None):
    if road_graph not in _RT:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT[road_graph] = build_verifier(quiet=True, road_graph=road_graph)
        except RuntimeError as e:
            _RT[road_graph] = e
    rt = _RT[road_graph]
    if isinstance(rt, Exception):
        pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
    if road_graph is None and getattr(rt._v, "car", None) is None:
        pytest.skip("차도 그래프 없음(road_graph_v1) — 택시 축 SKIP")
    return rt


def _at(s):
    return datetime.fromisoformat(f"2026-10-0{s}:00+09:00")


def _stay(seq, title, place, start, end):
    return {"seq": seq, "kind": "activity", "title": title, "place": place, "starts_at": _at(start).isoformat(),
            "ends_at": _at(end).isoformat()}


@pytest.mark.mobility_full
def test_full_transit_legs_are_untouched():
    """대중교통(·도보)으로 이동을 만드는 구간은 켜도 그대로다 — 계획 수단·출발·도착·options·뺀 후보."""
    legs = [(MYEONGDONG, GYEONGBOK, "6T10:00"), (HONGDAE, JAMSIL, "6T18:30"), (SINYONGSAN, NAMSEONG, "6T15:00"),
            (GYEONGBOK, JAMSIL, "6T12:00"), (JAMSIL, MYEONGDONG, "6T21:00")]
    for a, b, arr in legs:
        off = P.Planner(_runtime(), stage="planning").leg(a, b, _at(arr), {}, True, "x")
        on = P.Planner(_runtime(), stage="planning").leg(a, b, _at(arr), {}, True, "x", taxi_fallback=True)
        assert off[0] is not None and on == off, (a["name"], b["name"])


@pytest.mark.mobility_full
def test_full_after_last_train_leg_is_filled():
    """막차 뒤(앞 일정 00:50 끝 → 01:30 시작) — 대중교통은 못 맞추고 택시 소요로 메워진다 · 식 · 표시 · 봉투."""
    places = [dict(HONGDAE), dict(JAMSIL)]
    items = [_stay(1, "홍대", "hd", "6T23:30", "7T00:50"), _stay(2, "잠실", "js", "7T01:30", "7T02:30")]
    off = P.plan(places, items, runtime=_runtime())
    on = P.plan(places, items, runtime=_runtime(), taxi_fallback=True)
    assert [s["code"] for s in off["skipped"]] == ["arrive_late"] and not off["routes"] and "taxi_fallback" not in off
    assert on["skipped"] == [] and on["kept_unverified"] == []
    row, = on["taxi_fallback"]
    assert row["replaced_input_moves"] == [] and row["taxi"]["road_control_checked"] is False
    t, r = row["taxi"], on["routes"][row["route"]]
    assert (row["code"], row["reason"]) == (off["skipped"][0]["code"], off["skipped"][0]["reason"])
    dep, arr = datetime.fromisoformat(t["depart_at"]), datetime.fromisoformat(t["arrive_at"])
    assert _at("7T00:50") <= dep and (dep.timestamp() + t["worst_min"] * 60) <= _at("7T01:30").timestamp()   # 식
    assert (arr - dep).total_seconds() == t["eta_min"] * 60 and t["eta_min"] <= t["worst_min"]
    assert 10 <= t["eta_min"] <= 60 and t["fare_krw"] > 10000                      # 홍대→잠실 심야(할증) — 어림 범위만
    o, = r["options"]
    assert r["planned"] == o["id"] == "taxi_1" and o["label"].startswith("택시 기준 · 추정 · 홍대→잠실")
    assert "호출·승차 대기 제외" in o["label"] and not O.uses_problems(o["uses"]) and all(u.startswith("도로:") for u in o["uses"])
    assert set(o) <= set(P.CORE_OPTION_KEYS)
    mv, = [it for it in on["items"] if it["kind"] == "mobility"]
    assert (mv["starts_at"], mv["ends_at"], mv["route"]) == (t["depart_at"], t["arrive_at"], row["route"])


@pytest.mark.mobility_full
def test_full_overlap_out_of_area_and_router_off():
    places = [dict(MYEONGDONG), dict(GYEONGBOK), dict(JAMSIL), dict(BUSAN)]
    items = [_stay(1, "명동", "md", "6T08:30", "6T09:00"), _stay(2, "경복궁", "gb", "6T10:00", "6T11:00"),
             _stay(3, "잠실", "js", "6T11:10", "6T12:00"),          # 경복궁 끝 11:00 → 11:10 — 택시로도 못 맞춘다
             _stay(4, "부산역", "bs", "6T20:00", "6T21:00")]         # 차도 그래프 범위 밖
    off = P.plan(places, items, runtime=_runtime())
    on = P.plan(places, items, runtime=_runtime(), taxi_fallback=True)
    assert on.pop("taxi_fallback") == []
    assert [s["to"] for s in on["skipped"]] == ["잠실", "부산역"] and on["skipped"][0]["taxi"]["code"] == "before_prev_end"
    # 그래프에서 먼 좌표는 라우터가 「200 m 안 차도 없음(no_snap)」으로 낸다 — out_of_area 는 범위 가장자리 여백에서만(77-2 그대로)
    assert on["skipped"][1]["taxi"]["code"] in ("out_of_area", "no_snap") and on["skipped"][1]["taxi"]["status"] == "none"
    assert on["skipped"][0]["taxi"]["short_min"] > 0 and all("택시로도 못 채움" in s["reason"] for s in on["skipped"])
    for s, s0 in zip(on["skipped"], off["skipped"]):                               # 덧붙인 것 말고는 같다
        assert s["reason"].startswith(s0["reason"]) and s["code"] == s0["code"]
        s.pop("taxi"), s.update(reason=s0["reason"])
    off.pop("basis"), on.pop("basis")
    assert on == off                                                               # 만든 구간(명동→경복궁)·나머지 봉투 그대로
    # 도로 경로 계산을 끈 실행 — 켜도 끈 것과 같은 결과(빈 칸만 생긴다)
    rt0 = _runtime("none")
    assert rt0._v.car is None
    off0 = P.plan(places, items, runtime=rt0)
    on0 = P.plan(places, items, runtime=rt0, taxi_fallback=True)
    assert on0.pop("taxi_fallback") == []
    off0.pop("basis"), on0.pop("basis")
    assert on0 == off0 and on0 == off


@pytest.mark.mobility_full
def test_full_before_first_train_and_bike_only_modes():
    """첫차 전(05:00 도착 · 앞 일정 04:20 끝) — 메워진다 · 자전거만 고른 호출은 메우지 않는다."""
    arr, nb = _at("6T05:00"), _at("6T04:20")
    got, why = P.Planner(_runtime(), stage="planning").leg(HONGDAE, JAMSIL, arr, {}, True, "x", not_before_dt=nb,
                                                           taxi_fallback=True)
    assert got is None and why["taxi"]["status"] == "found" and "택시로도" not in why["reason"]
    assert datetime.fromisoformat(why["taxi"]["depart_at"]) >= nb
    got, why = P.Planner(_runtime(), stage="planning", modes=["bike", "walk"]).leg(
        MYEONGDONG, JAMSIL, _at("6T10:00"), {}, True, "x", taxi_fallback=True)
    assert got is None and "taxi" not in why
