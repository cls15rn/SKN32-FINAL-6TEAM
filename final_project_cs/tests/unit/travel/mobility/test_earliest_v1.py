# -*- coding: utf-8 -*-
"""가장 이른 도착(83 E2 · #42 보조 · 86번 방 2026-10-01) + 답 문장 등급 빼기(83 E3).

E2 — 앞 일정이 끝난 뒤 떠나서 닿는 **가장 이른 출발·도착**(Planner.earliest). 앞 판은 「도착 목표로 역산한 마지막 출발」
     하나만 있어, 코어가 그 출발과 앞 일정 끝의 차이만큼 밀어도 시간표가 띄엄띄엄이라 다음 편을 타게 되어 또 늦었다
     (83: N서울타워→남대문 13분 밀고도 「12:13 도착 후보 없음」).
E3 — 이동 에이전트 답 문장(fold answer)에 등급(「가능(추정)」)을 싣지 않는다 · 등급은 봉투(evidence·decisions)에 그대로.

두 층:
  게이트(데이터 없음) — earliest() 의 확인 순서·축·실패 이유 · leg() 의 켜기/끄기 · 요약 칸 · 접기 문장.
  전체층(실데이터 · mobility_full) — 83 사례 셋: 성수 무정차(아쿠아리움 10:45 끝) · N서울타워→남대문(11:30 끝) ·
    경복궁→롯데월드(11:00 끝). 가장 이른 도착 목표가 몇 시인지 · 그 1분 전은 안 되는지 · 역산 모드와 같은 값인지.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from app.modules.travel_ops.mobility.engine import plan as P
from app.modules.travel_ops.mobility.engine.fold import fold_case
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR
from app.modules.travel_ops.mobility.engine.runtime import Runtime
from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
D = datetime.fromisoformat
A = {"key": "a", "name": "A", "lat": 0.0, "lon": 0.0}
B = {"key": "b", "name": "B", "lat": 0.0, "lon": 0.01}


def _bare():
    v = Verifier(Timetable(), None, RULES, set())
    v.sc, v.ex = None, None
    return P.Planner(Runtime(v, timetable_built_at="t", rules_version="v", stats={}))


INFO = {"causes": [], "stops": {}}


def _route(pid="subway_1", eta=33):
    return {"from": "A", "to": "B", "planned": pid,
            "options": [{"id": pid, "label": "2호선 잠실→뚝섬", "eta_min": eta, "uses": ["2호선:잠실", "2호선:뚝섬"]}]}


# ── 게이트 — 데이터 없이 ───────────────────────────────────────────────
def test_earliest_needs_prev_end():
    with pytest.raises(ValueError):
        _bare().earliest(A, B, None, {}, True, "c")


def test_earliest_confirms_targets_in_order_and_returns_first_that_holds(monkeypatch):
    """이른 목표부터 leg(목표, 앞 일정 끝) 로 확인 — 성립한 첫 목표가 답."""
    pl = _bare()
    sd = date(2026, 9, 23)
    monkeypatch.setattr(pl, "_earliest_targets", lambda *a, **k: (sd, [691, 692, 700, 710], INFO))
    seen = []

    def fake_leg(a, b, arrive_dt, party, fv, cid, not_before_dt=None, earliest_on_late=False):
        seen.append((arrive_dt.strftime("%H:%M"), not_before_dt.strftime("%H:%M"), earliest_on_late))
        if arrive_dt.strftime("%H:%M") == "11:32":
            return (_route(), 649, 682, sd, []), None
        return None, {"code": "arrive_late", "reason": "x"}
    monkeypatch.setattr(pl, "leg", fake_leg)
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c")
    assert why is None and got[5] == 692 and got[1] == 649
    assert seen == [("11:31", "10:45", False), ("11:32", "10:45", False)], "앞 일정 끝을 그대로 넘기고 · 재귀 안 함"


def _leg_ok_at(hm_ok, calls):
    def fake_leg(a, b, arrive_dt, *args, **k):
        calls.append(arrive_dt.strftime("%H:%M"))
        if arrive_dt.strftime("%H:%M") in hm_ok:
            return (_route(), 600, 633, date(2026, 9, 23), []), None
        return None, {"code": "arrive_late", "reason": "안 맞음"}
    return fake_leg


def test_earliest_budget_exhausted_is_unconfirmed_not_infeasible(monkeypatch):
    """GPT 86 Q2 — 네 번째 목표만 성립: 확인 호출 한도(EARLIEST_TRIES)를 다 쓰면 「불성립」이 아니라 「탐색 한도로 미확인」."""
    pl = _bare()
    monkeypatch.setattr(pl, "_earliest_targets", lambda *a, **k: (date(2026, 9, 23), [691, 700, 710, 720], INFO))
    calls = []
    monkeypatch.setattr(pl, "leg", _leg_ok_at({"12:00"}, calls))
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c")
    assert got is None and len(calls) == P.EARLIEST_TRIES
    assert why["code"] == P.EARLIEST_UNCONFIRMED and "불성립 확인 아님" in why["reason"] and "안 맞음" in why["reason"]
    calls.clear()
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c", tries=20)
    assert why is None and got[5] == 720, "한도를 넓히면 네 번째 목표를 찾는다"


def test_earliest_checks_minutes_between_targets(monkeypatch):
    """GPT 86 Q2 — 목표 [11:31, 11:40] 에서 11:31 이 실패하면 11:32 를 본다(목표 + EARLIEST_FILL_MIN 분까지)."""
    pl = _bare()
    monkeypatch.setattr(pl, "_earliest_targets", lambda *a, **k: (date(2026, 9, 23), [691, 700], INFO))
    calls = []
    monkeypatch.setattr(pl, "leg", _leg_ok_at({"11:32", "11:40"}, calls))
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c")
    assert why is None and got[5] == 692 and calls == ["11:31", "11:32"]


def test_earliest_all_checked_fail_is_unconfirmed_with_search_range(monkeypatch):
    """GPT 86 R2 — 목표 하나와 보충 2분이 모두 실패해도 그 뒤를 안 봤으므로 not_found 가 아니라 미확인 · 본 범위를 남긴다."""
    pl = _bare()
    monkeypatch.setattr(pl, "_earliest_targets", lambda *a, **k: (date(2026, 9, 23), [691], INFO))
    calls = []
    monkeypatch.setattr(pl, "leg", _leg_ok_at({"11:34"}, calls))      # 목표 + 3분에서만 성립
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c")
    assert got is None and calls == ["11:31", "11:32", "11:33"]
    assert why["code"] == P.EARLIEST_UNCONFIRMED and "불성립 확인 아님" in why["reason"]
    assert why["searched"] == {"checked": ["11:31", "11:32", "11:33"], "calls": 3, "causes": [], "stop": "targets_checked"}
    assert "보충 2분까지만" in why["reason"] and "호출 한도" not in why["reason"]


def test_reason_names_wait_limit_not_call_limit(monkeypatch):
    """GPT 86 R2 — 대기 탐색 상한 때문에 미확인이면 「확인 호출 한도」가 아니라 「대기 탐색 상한」으로 적는다."""
    pl = _bare()
    monkeypatch.setattr(pl, "_earliest_targets",
                        lambda *a, **k: (date(2026, 9, 23), [], {"causes": [{"before_first": "첫차 전"}],
                                                                  "stops": {"wait_limit": 1}}))
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c")
    assert why["code"] == P.EARLIEST_UNCONFIRMED and "대기 탐색 범위(앞 일정 끝 뒤 480분" in why["reason"]
    assert "호출 한도" not in why["reason"]
    assert why["searched"]["stop"] == "wait_limit"


@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "3"])
def test_earliest_tries_must_be_positive_int(bad):
    """GPT 86 Q7 — tries=0 이면 앞 판은 TypeError, 음수는 슬라이스로 읽혔다."""
    with pytest.raises(ValueError):
        _bare().earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c", tries=bad)


def test_earliest_no_targets_wait_limit_is_unconfirmed(monkeypatch):
    pl = _bare()
    monkeypatch.setattr(pl, "_earliest_targets",
                        lambda *a, **k: (date(2026, 9, 23), [], {"causes": [{"before_first": "첫차 전"}],
                                                                  "stops": {"incomplete": 1}}))
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c")
    assert got is None and why["code"] == P.EARLIEST_UNCONFIRMED and why["searched"]["stop"] == "wait_incomplete"


def test_earliest_no_targets_keeps_engine_reason(monkeypatch):
    """GPT 86 Q7·R3 — 후보가 없을 때 판정기 이유를 **소스마다 한 줄**로 보존한다(시각과 무관한 이유뿐 → not_found)."""
    pl = _bare()
    causes = [{"no_service": "운행 중단(사고)"}, {"no_data": "시간표 없음", "no_service": "운행 중단(사고)"},
              {"no_data": "시간표 없음"}]
    monkeypatch.setattr(pl, "_earliest_targets",
                        lambda *a, **k: (date(2026, 9, 23), [], {"causes": causes, "stops": {}}))
    got, why = pl.earliest(A, B, D("2026-09-23T10:45:00+09:00"), {}, True, "c")
    assert why["code"] in ("no_service", "no_data") and "no_service 2곳" in why["reason"] and "no_data 2곳" in why["reason"]
    assert f"(첫 사유: {'운행 중단(사고)' if why['code'] == 'no_service' else '시간표 없음'})" in why["reason"], \
        "대표 코드와 첫 사유가 짝이 맞는다(3차 #4)"
    assert "검사한 후보에서 다음 사유로" in why["reason"] and why["searched"]["stop"] == "no_targets"


def test_run_sources_counts_one_cause_per_source_not_per_wait_call():
    """GPT 86 R3 — 대기 탐색이 같은 소스를 여러 번 불러도 원인은 소스당 한 줄(호출 수가 대표 원인을 정하지 않는다)."""
    rec = {"on": False, "fails": []}

    def waiting(m):                     # 끝까지 첫차 전 — 매 호출 before_first
        if rec["on"]:
            rec["fails"].append(("before_first", "첫차 전"))
        return [], True

    def closed(m):
        if rec["on"]:
            rec["fails"].append(("no_service", "운행 중단"))
        return [], False
    ts, info = P.Planner._run_sources([waiting, closed, closed], 600, rec)
    assert ts == [] and info["causes"] == [{"before_first": "첫차 전"}, {"no_service": "운행 중단"},
                                           {"no_service": "운행 중단"}]
    assert info["stops"]["wait_limit"] == 1


def test_run_sources_waits_for_waiting_candidate_even_when_another_holds():
    """GPT 86 R5② — 같은 소스에서 A 는 지금 성립(목표 12:00) · B 는 첫차(nb+20) 전이고 더 빠르다(목표 11:00) — B 도 본다."""
    nb = 600

    def mixed(m):
        ts = [720]                              # A — 언제 떠나도 12:00
        if m >= nb + 20:
            ts.append(m + 40)                   # B — nb+20 부터 성립 · 40분 뒤 도착 목표
        return ts, m < nb + 20
    ts, info = P.Planner._run_sources([mixed], nb, {"on": False, "fails": []})
    assert min(ts) == nb + 20 + 40 and 720 in ts and not info["stops"]


def test_after_wait_finds_first_feasible_departure():
    """GPT 86 Q1 — 첫차 전 불가(기다리면 됨)인 소스: 출발을 뒤로 옮겨 첫 성립 출발(05:32)의 도착 목표를 찾는다."""
    first = 5 * 60 + 32

    def src(m):
        return ([m + 60] if m >= first else []), m < first
    ts, stop = P.Planner._after_wait(src, 4 * 60 + 30)
    assert min(ts) == first + 60 and stop == "done"


def test_after_wait_stop_reasons():
    assert P.Planner._after_wait(lambda m: ([], False), 600) == ([], "incomplete"), "기다릴 이유 아닌 불가 → 끝내지 못함"
    assert P.Planner._after_wait(lambda m: ([], True), 600) == ([], "wait_limit"), "상한까지 못 찾으면 탐색 한도"


def test_after_wait_non_wait_failure_during_bisection_is_incomplete():
    """GPT 86 R1 — 이분 탐색 중 다른 불가를 만나면 hi 목표(성립 출발에서 얻은 값)를 쓰되 「끝내지 못함」으로 표시."""
    nb = 600

    def src(m):
        if m >= nb + 15:
            return [m + 30], False
        if m == nb + 7:
            return [], False                 # 중간에 기다릴 이유가 아닌 불가
        return [], True
    ts, stop = P.Planner._after_wait(src, nb)
    assert min(ts) == nb + 15 + 30 and stop == "incomplete"


def test_after_wait_short_window_is_skipped_and_reported_as_limit():
    """GPT 86 R5① — 두 배 간격의 사각지대: nb+16~20분만 성립하면 건너뛴다 → 미확인(wait_limit)으로 끝난다(문서화된 한계)."""
    nb = 600

    def src(m):
        return ([m + 30] if nb + 16 <= m <= nb + 20 else []), not (nb + 16 <= m <= nb + 20)
    assert P.Planner._after_wait(src, nb) == ([], "wait_limit")


def test_earliest_no_targets_no_reason_is_no_data(monkeypatch):
    pl = _bare()
    monkeypatch.setattr(pl, "_earliest_targets", lambda *a, **k: (date(2026, 9, 23), [], INFO))
    got, why = pl.earliest(A, B, D("2026-09-23T23:50:00+09:00"), {}, True, "c")
    assert got is None and why["code"] == "no_data" and "23:50" in why["reason"]


def test_earliest_target_axis_follows_leg_service_day(monkeypatch):
    """앞 일정 끝이 03:50(전날 운행일 27:50) · 목표가 04:00 을 넘으면 leg() 는 새 운행일 축 — 목표 분도 그 축으로."""
    pl = _bare()
    prev_day, new_day = date(2026, 9, 22), date(2026, 9, 23)
    # 목표 1690 분 = 전날 운행일 28:10 = 벽시계 9/23 04:10 → leg() 는 새 운행일(9/23) 축 250 분으로 돌려준다
    monkeypatch.setattr(pl, "_earliest_targets", lambda *a, **k: (prev_day, [1440 + 250], INFO))
    monkeypatch.setattr(pl, "leg", lambda *a, **k: ((_route(), 230, 245, new_day, []), None))
    got, _ = pl.earliest(A, B, D("2026-09-23T03:50:00+09:00"), {}, True, "c")
    assert got[3] == new_day and got[5] == 250


def test_summary_fields_follow_output_spec_names():
    got = (_route(), 649, 682, date(2026, 9, 23), [], 692)
    s = P.Planner.earliest_summary(got, D("2026-09-23T11:30:00+09:00"))
    assert (s["starts_at"], s["ends_at"], s["arrive_by"]) == (
        "2026-09-23T10:49:00+09:00", "2026-09-23T11:22:00+09:00", "2026-09-23T11:32:00+09:00")
    assert s["status"] == "found" and s["requested_arrive_by"] == "2026-09-23T11:30:00+09:00"
    assert s["eta_min"] == 33 and s["planned"] == "subway_1" and s["uses"] == ["2호선:잠실", "2호선:뚝섬"]
    assert s["slack_min"] == -2, "원래 목표 − 제안 목표 · 음수 = 다음 항목을 2분 밀면 맞는다(이 경로 자체의 여유 아님)"
    assert "grade" not in json.dumps(s) and "last_feasible" not in json.dumps(s), "등급·늦어도 출발은 안 낸다"


def test_leg_attaches_earliest_only_when_asked(monkeypatch):
    """기본(끄기)은 앞 판과 같다 — earliest 를 부르지 않는다. 켜면 arrive_late 이유에 earliest 를 붙인다."""
    pl = _bare()
    calls = []
    monkeypatch.setattr(pl, "earliest", lambda *a, **k: (calls.append(1), (None, {"code": "before_first", "reason": "r"}))[1])
    walk_far = {"key": "c", "name": "C", "lat": 0.0, "lon": 0.0005}          # 약 55 m — 도보 직행만
    arrive, prev_end = D("2026-09-23T11:30:00+09:00"), D("2026-09-23T11:29:00+09:00")
    got, why = pl.leg(A, walk_far, arrive, {}, True, "c", not_before_dt=prev_end)
    assert got is None and why["code"] == "arrive_late" and "earliest" not in why and not calls
    got, why = pl.leg(A, walk_far, arrive, {}, True, "c", not_before_dt=prev_end, earliest_on_late=True)
    assert why["earliest"] == {"status": "not_found", "code": "before_first", "reason": "r", "searched": None}
    assert calls == [1]


def test_earliest_on_codes_are_time_reasons_only():
    """GPT 86 Q7 — 적용 범위: 시각 때문에 못 맞춘 것만. 역·데이터가 없는 구간에는 붙이지 않는다."""
    assert P.EARLIEST_ON_CODES == {"arrive_late", "before_first", "service_gap", "after_last"}
    assert not P.EARLIEST_ON_CODES & {"no_data", "no_service", P.EARLIEST_UNCONFIRMED}


def test_summary_requested_arrive_by_is_floored_minute():
    """GPT 86 R4 — 원래 목표에 초가 있으면 분 내림(leg() 판정 기준과 같다 · 원문 시각 아님) · slack 도 같은 기준."""
    got = (_route(), 649, 682, date(2026, 9, 23), [], 692)
    s = P.Planner.earliest_summary(got, D("2026-09-23T11:30:40+09:00"))
    assert s["requested_arrive_by"] == "2026-09-23T11:30:00+09:00" and s["slack_min"] == -2


def test_walk_only_earliest_real_planner():
    """도보만 되는 짧은 구간 — 가장 이른 도착 목표 = 앞 일정 끝 + 도보 분 + 정책 버퍼(데이터 없이 도는 실제 계산)."""
    pl = _bare()
    near = {"key": "c", "name": "C", "lat": 0.0, "lon": 0.0005}
    prev_end = D("2026-09-23T11:29:00+09:00")
    got, why = pl.earliest(A, near, prev_end, {}, True, "c")
    assert why is None, why
    s = P.Planner.earliest_summary(got, D("2026-09-23T11:30:00+09:00"))
    buf = pl.v.rv("buffer", "by_stage", "planning")
    assert s["starts_at"] == "2026-09-23T11:29:00+09:00" and s["planned"] == "walk"
    assert D(s["arrive_by"]) == prev_end + timedelta(minutes=s["eta_min"] + buf)
    assert s["slack_min"] == -(s["eta_min"] + buf - 1)


def test_plan_version_bumped():
    assert P.PLAN_VERSION == "plan-v2.8"      # 102 — 합치기 2(환승·혼합 후보 걷기 길 기준 · 걷기 근거 · 요금 거리 원천 순서) · 101 판 = plan-v2.6 · 98 판 = plan-v2.5 · 87 판 = plan-v2.4


# ── E3 — 답 문장에 등급 없음 ───────────────────────────────────────────
_BASIS = {"timetable_built_at": "built:t", "rules_version": "v", "service_date": "2026-10-05", "decided_at": "t"}
GRADES = ("(확정)", "(추정)", "(근거없음)")


def test_e3_answer_has_no_grade_but_envelope_keeps_it():
    out = fold_case({"id": "c", "verdict": "feasible", "grade": "추정", "reason": "예정 10:11 +@10분",
                     "legs": [{"label": "02호선 잠실→성수", "verdict": "feasible", "grade": "추정", "arrive_min": 611}],
                     "taxi": {"verdict": "feasible", "depart_min": 600, "arrive_min": 620, "reason": "택시", "grade": "추정"}},
                    task_id="t", basis=_BASIS)
    ans = out["answer"]
    assert not any(g in ans for g in GRADES), ans
    assert "판정 가능 — 예정 10:11 +@10분" in ans, "여유값(+@분)은 남는다"
    assert "[L1] 02호선 잠실→성수 가능 — 도착 10:11" in ans
    grades = {e["value"].get("grade") for e in out["evidence"]}
    assert "추정" in grades, "등급은 봉투(evidence)에 그대로"


# ── 전체층 — 실데이터(83 사례) ─────────────────────────────────────────
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


AQUARIUM = {"key": "aquarium", "name": "아쿠아리움", "lat": 37.5131, "lon": 127.1033}
SEONGSU = {"key": "seongsu", "name": "성수동 팝업·향수 쇼룸 거리", "lat": 37.5445, "lon": 127.056}
NTOWER = {"key": "ntower", "name": "N서울타워", "lat": 37.5512, "lon": 126.9882}
NAMDAE = {"key": "namdae", "name": "남대문 갈치조림", "lat": 37.5594, "lon": 126.9770}
GYEONG = {"key": "gb", "name": "경복궁", "lat": 37.5796, "lon": 126.9770}
LOTTE = {"key": "lw", "name": "롯데월드", "lat": 37.5111, "lon": 127.0980}
SKIP_SEONGSU = ({"kind": "station_skip", "line": "02호선", "station": "성수"},)

# (이름, 출발 장소, 도착 장소, 원래 도착 목표, 앞 일정 끝, 사고, 기대: 출발 · 예정 도착 · 가장 이른 도착 목표 · 계획 수단 uses)
CASES = [
    ("seongsu_skip", AQUARIUM, SEONGSU, "2026-09-23T11:30", "2026-09-23T10:45", SKIP_SEONGSU,
     ("10:49", "11:22", "11:32", ["2호선:잠실", "2호선:뚝섬"])),         # 85 §3: 10:44 출발 필요 · 1분 부족 → 11:32 부터
    ("ntower_namdae", NTOWER, NAMDAE, "2026-10-07T12:13", "2026-10-07T11:30", (),
     ("11:30", "11:59", "12:14", ["버스:01A"])),                           # 83 E2: 13분 밀고(12:13) 실패 → 12:14 부터
    ("gyeongbok_lotte", GYEONG, LOTTE, "2026-10-07T12:01", "2026-10-07T11:00", (),
     ("11:01", "11:56", "12:06", ["5호선:광화문", "5호선:을지로4가", "2호선:을지로4가", "2호선:잠실"])),   # 83 ①보충: 46분 밀고 실패
]


def _pl(dis):
    pl = P.Planner(_runtime(), stage="planning")
    pl.disruptions = tuple(dis)
    return pl


@pytest.mark.mobility_full
@pytest.mark.parametrize("name,a,b,arrive,prev_end,dis,exp", CASES, ids=[c[0] for c in CASES])
def test_full_earliest_on_late_gives_earliest_arrival(name, a, b, arrive, prev_end, dis, exp):
    arr, nb = D(arrive + ":00+09:00"), D(prev_end + ":00+09:00")
    got, why = _pl(dis).leg(a, b, arr, P.party_of(2, {}), True, name, not_before_dt=nb, earliest_on_late=True)
    assert got is None and why["code"] == "arrive_late", "83 그대로 — 원래 목표로는 못 맞춘다"
    e = why["earliest"]
    assert (e["starts_at"][11:16], e["ends_at"][11:16], e["arrive_by"][11:16], e["uses"]) == exp, e
    assert D(e["starts_at"]) >= nb, "앞 일정 끝 뒤에 떠난다"
    assert e["slack_min"] == -int((D(e["arrive_by"]) - arr).total_seconds() // 60) < 0


@pytest.mark.mobility_full
@pytest.mark.parametrize("name,a,b,arrive,prev_end,dis,exp", CASES, ids=[c[0] for c in CASES])
def test_full_earliest_is_minimal_and_matches_latest_mode(name, a, b, arrive, prev_end, dis, exp):
    """그 목표로 역산 모드(leg)를 부르면 같은 값 · 1분 이른 목표는 앞 일정 끝 뒤로는 안 된다(가장 이르다)."""
    nb = D(prev_end + ":00+09:00")
    t = D(f"{arrive[:10]}T{exp[2]}:00+09:00")
    pl = _pl(dis)
    got, why = pl.leg(a, b, t, P.party_of(2, {}), True, name, not_before_dt=nb)
    assert got is not None, why
    route, start, end, sd, _left = got
    assert (P.iso_of(sd, start)[11:16], P.iso_of(sd, end)[11:16]) == exp[:2]
    got1, why1 = pl.leg(a, b, t - timedelta(minutes=1), P.party_of(2, {}), True, name, not_before_dt=nb)
    assert got1 is None and why1["code"] == "arrive_late"


@pytest.mark.mobility_full
def test_full_default_leg_unchanged_without_flag():
    """끄기(기본) — 이유 dict 에 earliest 가 없다. (98 · GPT 98 #4) 뺀 후보(left_out)와 「판정하지 않은 후보가 남았다」 표시는 온다."""
    got, why = _pl(SKIP_SEONGSU).leg(AQUARIUM, SEONGSU, D("2026-09-23T11:30:00+09:00"), P.party_of(2, {}), True, "x",
                                    not_before_dt=D("2026-09-23T10:45:00+09:00"))
    assert got is None and "earliest" not in why and set(why) - {"left_out", "search_limited"} == {"code", "reason"}


@pytest.mark.mobility_full
def test_full_plan_puts_earliest_in_skipped():
    """plan(earliest_on_late=True) — 못 맞춘 구간의 skipped 에 earliest(봉투만 · 코어 몸통 items·routes 는 그대로)."""
    places = [dict(NTOWER, key="nt"), dict(NAMDAE, key="nd")]
    items = [{"seq": 1, "kind": "activity", "title": "N서울타워", "place": "nt",
              "starts_at": "2026-10-07T10:00:00+09:00", "ends_at": "2026-10-07T11:30:00+09:00"},
             {"seq": 2, "kind": "dining", "title": "남대문 갈치조림", "place": "nd",
              "starts_at": "2026-10-07T12:13:00+09:00", "ends_at": "2026-10-07T13:13:00+09:00"}]
    off = P.plan(places, items, 2, {}, runtime=_runtime())
    on = P.plan(places, items, 2, {}, runtime=_runtime(), earliest_on_late=True)
    assert off["items"] == on["items"] and off["routes"] == on["routes"] == {}
    assert "earliest" not in off["skipped"][0]
    e = on["skipped"][0]["earliest"]
    assert on["skipped"][0]["code"] == "arrive_late" and e["arrive_by"][11:16] == "12:14" and e["slack_min"] == -1


# (GPT 86 Q1·Q4) 기다려 떠나는 경우 — 실제 시간표 · 3호선 경복궁 하행 첫차 05:32(평일·휴일 같음) · 역까지 도보 9분
WAIT_CASES = [
    ("before_first_0430", "2026-10-07T04:30:00", "2026-10-07T05:30:00", "before_first", "2026-10-07T05:23"),
    ("boundary_035930", "2026-10-07T03:59:30", "2026-10-07T05:00:00", "before_first", "2026-10-07T05:23"),   # 04:00 올림 · 새 운행일
    ("after_last_holiday", "2026-10-03T00:50:00", "2026-10-03T01:30:00", "arrive_late", "2026-10-03T05:23"),  # 금 운행일 → 개천절 첫차
]


@pytest.mark.mobility_full
@pytest.mark.parametrize("name,prev_end,arrive,code,start", WAIT_CASES, ids=[c[0] for c in WAIT_CASES])
def test_full_earliest_waits_for_first_train(name, prev_end, arrive, code, start):
    """GPT 86 Q1 — 앞 판 목표 생성기는 성립 후보만 받아 「첫차까지 기다렸다 출발」을 놓쳤다(목표 0개)."""
    got, why = _pl(()).leg(GYEONG, LOTTE, D(arrive + "+09:00"), P.party_of(2, {}), True, name,
                           not_before_dt=D(prev_end + "+09:00"), earliest_on_late=True)
    assert got is None and why["code"] == code
    e = why["earliest"]
    assert e["status"] == "found" and e["starts_at"][:16] == start, e
    assert e["arrive_by"][:16] == start[:11] + "06:24" and e["uses"][0] == "3호선:경복궁"


@pytest.mark.mobility_full
def test_full_e3_real_answers_have_no_grade():
    """GPT 86 Q6 — 실제 판정(회귀 real 묶음)을 접은 답 문장에 등급 딱지가 없다. reason·relief·경고 문장 포함."""
    import re
    from pathlib import Path

    from app.modules.travel_ops.mobility.engine.adapter import _to_plain
    from app.modules.travel_ops.mobility.engine.verify_time import load_cases
    rt = _runtime()
    cases = load_cases(str(Path(__file__).resolve().parent / "real_legs_v1.json"))
    n = 0
    for c in cases:
        out = fold_case(_to_plain(rt.verify_case(c)), task_id="t", basis=_BASIS)   # 어댑터와 같은 변환
        if out.get("answer"):
            n += 1
            assert not re.search(r"[(\[](확정|추정|근거없음)[)\]]", out["answer"]), (c.get("id"), out["answer"])
    assert n >= 10


# ── 3차 대조 재현 사례(GPT 86 S1·S3) ─────────────────────────────────
def test_s1_observed_target_is_kept_when_another_candidate_keeps_waiting():
    """S1 — A 목표 nb+120 · B 는 nb+20 부터 성립(목표 = 출발+10) · C 는 계속 대기. 앞 판은 nb+30 에서 받은 B 의 목표를
    버리고 [nb+120] · stops 빈 값을 냈다. 관측한 목표는 모으고, 혼합 소스의 대기 상한 도달도 남긴다."""
    nb = 600

    def src(m):
        ts = [nb + 120]
        if m >= nb + 20:
            ts.append(m + 10)
        return ts, True                       # C 가 늘 대기
    ts, info = P.Planner._run_sources([src], nb, {"on": False, "fails": []})
    assert min(ts) == nb + 30 and nb + 120 in ts, "B 의 첫 성립 출발(nb+20)의 목표까지 본다(4차 T1)"
    assert info["stops"]["wait_limit"] == 1


def test_s3_short_horizon_below_first_step_is_still_probed():
    """S3① — A 목표 nb+12(상한 12분 < 첫 칸 15분) · B 는 nb+5 부터 목표 nb+7. 앞 판은 nb 한 번만 보고 [nb+12]."""
    nb = 600

    def src(m):
        return [nb + 12] + ([nb + 7] if m >= nb + 5 else []), m < nb + 5
    ts, info = P.Planner._run_sources([src], nb, {"on": False, "fails": []})
    assert min(ts) == nb + 7 and not info["stops"]


def test_s3_last_interval_up_to_horizon_is_probed():
    """S3② — A 목표 nb+25 · B 는 nb+20 부터 목표 nb+22. 앞 판은 nb·nb+15 만 보고(다음 칸 30 > 상한) 15~25분을 건너뛰었다."""
    nb = 600

    def src(m):
        return [nb + 25] + ([nb + 22] if m >= nb + 20 else []), m < nb + 20
    ts, info = P.Planner._run_sources([src], nb, {"on": False, "fails": []})
    assert min(ts) == nb + 22 and not info["stops"]


# ── 4차 대조(GPT 86 #2·#3) ────────────────────────────────────────────
def test_improve_bisection_records_non_wait_failure():
    """#2 — 「새로 나온 더 이른 목표」 이분 탐색 중 비대기 불가(([], False))를 만나면 탐색은 잇되 incomplete 로 남긴다."""
    nb = 600

    def src(m):
        if m == nb + 22:
            return [], False                      # 중간점에서 비대기 불가
        ts = [nb + 120]
        if m >= nb + 20:
            ts.append(m + 10)
        return ts, True                           # 다른 후보는 계속 대기
    ts, stop = P.Planner._after_wait(src, nb, 120, until_clear=True, known=nb + 120)
    assert min(ts) <= nb + 40 and nb + 120 in ts, "관측한 목표는 보존(중간 불가 때문에 더 이른 칸은 못 좁힐 수 있다)"
    assert stop == "incomplete", "비대기 불가를 만났다는 사실을 남긴다(앞 판은 wait_limit)"


def test_wait_note_uses_actual_reduced_range():
    """#3 — 섞인 소스는 지금 목표까지로 줄여 본다 → 문구에 8시간이 아니라 실제 범위(분)."""
    nb = 600

    def src(m):
        return [nb + 30], True                    # 지금 목표 nb+30 · 다른 후보는 끝까지 대기
    ts, info = P.Planner._run_sources([src], nb, {"on": False, "fails": []})
    assert info["stops"]["wait_limit"] == 1 and info["wait_ranges"] == [30]

