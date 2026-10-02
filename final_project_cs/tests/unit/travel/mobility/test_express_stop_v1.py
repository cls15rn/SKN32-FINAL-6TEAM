# -*- coding: utf-8 -*-
"""92번 방(2026-10-02) — 「지나간다」와 「선다」를 가른다: 도착역 시간표에 그 편의 행이 없으면 그 편으로 내리지 않는다. 데이터 없이 돈다.

가짜 노선  L:  A — B — C — D — E — F   (역간 2분)
  · E 행 완행  — A 에서 10분마다(06:00~23:00) · 모든 역에 선다
  · E 행 급행  — A 에서 매시 03분(07:03~21:03) · A·D 에만 선다(B·C 무정차 · 완행과 **같은 행선지** = 섞인 묶음) · A→D 4분
  · F 행 급행  — A 에서 매시 33분(07:33~21:33) · A·D·E 에만 선다(B·C 는 이 묶음 0행 = 묶음 전체 무정차)
  · A 행(반대 방향) — E 에서 10분마다 · 모든 역에 선다

잠그는 것
  ① 짝짓기(match_stops) — 완행은 역 순서 표 소요 시차로, 급행은 같은 시차 묶음으로 · 급행이 완행의 행을 가로채지 않는다 ·
     행이 없는 편은 짝이 없다 · 같은 시차 묶음은 서로 다른 짝 둘 이상 · 반대로 가는 묶음은 전부 짝 없음 ·
     늦은 한 편·자정 정각(원천이 「출발 없음」과 같은 값으로 주는 자리)은 약한 짝
  ② 하차 — 급행(섞인 묶음 · 묶음 전체 무정차 둘 다)으로 B 에 내리지 않는다 → 다음 완행 · 정차역 D 는 급행 그대로(지나감 유지)
  ③ 「못 간다」는 도착역 행이 없어 뺀 편(이 편만 없음 · 묶음 전체 0행 둘 다)이 남아 있으면 말하지 않는다(no_data)
  ⑤ 약한 짝(늦은 한 편 · 자정 정각)으로 쓰는 편은 등급 추정 · 올린 역의 그 요일 행이 0 이면 모른다
  ④ 승차 — 출발역 행은 그 역에 서는 편뿐이라 달라지지 않는다
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))      # final_project_cs

from app.modules.travel_ops.mobility.engine.line_order import LineOrder      # noqa: E402
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR           # noqa: E402
from app.modules.travel_ops.mobility.engine.verify_time import (             # noqa: E402
    Dep, Timetable, Verifier, match_stops)

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
NAMES = ["A", "B", "C", "D", "E", "F"]


def _lo():
    return LineOrder({"built_at": "t", "dest_alias": {}, "lines": {"L": {
        "stations": [{"station_nm": n, "fr_order": i} for i, n in enumerate(NAMES)],
        "edges": [{"a": a, "b": b, "grade": "확정", "travel_min": 2.0} for a, b in zip(NAMES, NAMES[1:])],
        "dir_label": {"reliable": True}, "is_loop": False}}})


def _tt(local_until=23 * 60, express_until=21 * 60):
    tt = Timetable()

    def add(st, m, dest, d="D"):
        tt.by_key[("L", st, "weekday")].append(Dep(m, d, dest))

    for m in range(6 * 60, local_until + 1, 10):                 # E 행 완행 — A·B·C·D 출발(E 는 종착)
        for k, st in enumerate(["A", "B", "C", "D"]):
            add(st, m + 2 * k, "E")
    for h in range(7, express_until // 60 + 1):
        add("A", h * 60 + 3, "E")                                # E 행 급행 — A·D
        add("D", h * 60 + 7, "E")
        add("A", h * 60 + 33, "F")                               # F 행 급행 — A·D·E
        add("D", h * 60 + 37, "F")
        add("E", h * 60 + 39, "F")
    for m in range(6 * 60, 23 * 60 + 1, 10):                     # 반대 방향 A 행
        for k, st in enumerate(["E", "D", "C", "B"]):
            add(st, m + 2 * k, "A", "U")
    for st in NAMES:
        tt.stations.add(("L", st))
    for v in tt.by_key.values():
        v.sort(key=lambda x: x.min)
    tt.rows = sum(len(v) for v in tt.by_key.values())
    return tt


def _leg(v, a, b, at):
    return v.verify_leg(0, {"line": "L", "from": a, "to": b}, at, "weekday", False)


def _v(**kw):
    return Verifier(_tt(**kw), _lo(), RULES, set())


# ── ① 짝짓기 ──
def test_match_local_by_order_table_time():
    a = [600, 610, 620]
    assert match_stops(a, [606, 616, 626], 6.0, 3) == ({600: 606, 610: 616, 620: 626}, set())


def test_match_express_without_row_stays_unmatched_and_does_not_steal():
    a = [600, 603, 610, 620]                        # 603 = 급행(도착역 무정차)
    m, weak = match_stops(a, [606, 616, 626], 6.0, 3)
    assert 603 not in m and m[600] == 606 and m[610] == 616 and not weak


def test_match_express_that_stops_is_its_own_offset_class():
    a = [600, 603, 610, 663, 670]                   # 급행 603·663 은 4분 만에(완행 6분)
    m, weak = match_stops(a, [606, 607, 616, 667, 676], 6.0, 3)
    assert m[603] == 607 and m[663] == 667 and m[600] == 606 and not weak


def test_match_class_needs_two_independent_pairs():
    # GPT 대조 2 — 한 편(600)이 두 행(614·615)과 맞는 것은 「두 편」이 아니다
    assert match_stops([600, 900], [614, 615], 30, 10) == ({}, set())
    assert 700 not in match_stops([600, 610, 700], [606, 616], 6.0, 3)[0]       # 행이 없는 한 편


def test_match_opposite_direction_bundle_matches_nothing():
    # GPT 대조 1 — 도착역이 6분 **먼저**(반대로 가는 편). 배차가 고르면 다음 편의 행과 4분 시차가 되풀이되지만 짝이 아니다
    assert match_stops([600, 610, 620], [594, 604, 614], 6.0, 3) == ({}, set())


def test_match_late_single_train_is_weak_and_must_be_unambiguous():
    # GPT 대조 3 — 늦게 닿는 한 편은 약한 짝(추정) · 남은 편이 둘이고 남은 행이 하나면(급행 + 늦은 완행) 둘 다 짝 없음
    m, weak = match_stops([600, 610, 700], [606, 616, 715], 6.0, 3)
    assert m[700] == 715 and weak == {700}
    m, weak = match_stops([600, 610, 700, 703], [606, 616, 715], 6.0, 3)
    assert 700 not in m and 703 not in m and not weak


def test_match_exact_midnight_is_weak_and_only_one_train():
    # GPT 대조 5 — 24:00 행은 하나뿐이다 · 직접 확인한 것이 아니라 약한 짝
    m, weak = match_stops([1414, 1424, 1434], [1420, 1430], 6.0, 3)
    assert m[1434] == 1440 and weak == {1434}
    m, weak = match_stops([1400, 1410, 1434, 1435], [1405, 1416], 6.0, 3)
    assert m.get(1434) == 1440 and 1435 not in m
    assert 1433 not in match_stops([1414, 1424, 1433], [1420, 1430], 6.0, 3)[0]  # 24:00 이 아니면 예외 아님


# ── ② 하차 ──
def test_alight_at_skipped_station_uses_next_local_mixed_bundle():
    r = _leg(_v(), "A", "B", 7 * 60 + 2)            # 07:03 E 행 급행은 B 에 안 선다 → 07:10 완행
    assert r.verdict == "feasible" and r.depart_min == 7 * 60 + 10 and r.arrive_min == 7 * 60 + 12
    assert r.dropped["정차_미확인"] == 15 and r.dropped["무정차_통과"] == 15


def test_alight_at_skipped_station_whole_bundle():
    r = _leg(_v(), "A", "C", 7 * 60 + 31)           # 07:33 F 행 급행(C 는 이 묶음 0행) → 07:40 완행
    assert r.verdict == "feasible" and r.depart_min == 7 * 60 + 40


def test_passing_through_skipped_stations_is_kept():
    r = _leg(_v(), "A", "D", 7 * 60 + 2)            # D 는 급행 정차역 — 07:03 급행 그대로(B·C 는 지나간다)
    assert r.verdict == "feasible" and r.depart_min == 7 * 60 + 3 and "정차_미확인" not in r.dropped
    r = _leg(_v(), "A", "E", 7 * 60 + 31)           # F 행 급행으로 E — E 에 그 묶음 행이 있다
    assert r.verdict == "feasible" and r.depart_min == 7 * 60 + 33


# ── ③ 「못 간다」 ──
def test_no_infeasible_while_unverified_trains_remain():
    v = _v(local_until=20 * 60)                     # 완행은 20:00 까지 · E 행 급행(정차 미확인)은 21:03 까지
    r = _leg(v, "A", "B", 20 * 60 + 30)
    assert r.verdict == "unknown" and r.code == "no_data" and "확인하지 못했다" in r.reason


def test_whole_bundle_without_rows_also_blocks_infeasible():
    # GPT 대조 4 — 묶음 전체가 0행인 것도 원천 누락과 못 가른다. 21:10 완행 뒤 21:33 F 행 급행이 남아 있으면 「막차 이후」가 아니다
    v = _v(local_until=21 * 60 + 10)
    r = _leg(v, "A", "B", 21 * 60 + 20)
    assert r.verdict == "unknown" and r.code == "no_data"
    r = _leg(v, "A", "B", 21 * 60 + 40)             # 21:33 도 지난 뒤에는 남은 편이 없다 — 종전대로 막차 이후(확정)
    assert r.verdict == "infeasible" and r.code == "after_last" and r.grade == "확정"


def test_weak_match_lowers_grade():
    tt = _tt()
    rows = [d for d in tt.by_key[("L", "B", "weekday")] if not (d.dest == "E" and d.min == 6 * 60 + 32)]
    tt.by_key[("L", "B", "weekday")] = sorted(rows + [Dep(6 * 60 + 37, "D", "E")], key=lambda x: x.min)   # 06:30 완행만 B 를 5분 늦게
    v = Verifier(tt, _lo(), RULES, set())
    r = _leg(v, "A", "B", 6 * 60 + 30)
    assert r.verdict == "feasible" and r.depart_min == 6 * 60 + 30 and r.grade == "추정"
    r = _leg(v, "A", "B", 6 * 60 + 35)              # 06:40 완행은 행으로 확인 — 확정
    assert r.depart_min == 6 * 60 + 40 and r.grade == "확정"


def test_loaded_target_without_rows_that_day_is_unknown():
    # GPT 대조 6 — 올린 역인데 그 요일 출발 행이 0 이면 대조를 건너뛰지 않는다 · 안 올린 역은 종전대로(대조 안 함)
    tt = _tt()
    del tt.by_key[("L", "B", "weekday")]
    r = _leg(Verifier(tt, _lo(), RULES, set()), "A", "B", 8 * 60)
    assert r.verdict == "unknown" and r.code == "no_data"


def test_all_trains_skip_is_unknown_not_no_service():
    tt = _tt()
    tt.by_key[("L", "A", "weekday")] = [d for d in tt.by_key[("L", "A", "weekday")] if d.dest == "F"]
    r = _leg(Verifier(tt, _lo(), RULES, set()), "A", "B", 8 * 60)      # A 에서는 F 행 급행만 — B 에 서는 편이 없다
    assert r.verdict == "unknown" and r.code == "no_data" and "정차 행을 확인하지 못했다" in r.reason


# ── ④ 승차 ──
def test_boarding_side_is_unchanged():
    r = _leg(_v(), "B", "D", 7 * 60 + 4)            # B 에는 급행 행이 없다 — 07:12 완행
    assert r.verdict == "feasible" and r.depart_min == 7 * 60 + 12 and r.arrive_min == 7 * 60 + 16
