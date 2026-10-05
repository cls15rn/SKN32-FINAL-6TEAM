# -*- coding: utf-8 -*-
"""이동 계산기 점검(2026-09-29) 수정 회귀 — 문제 번호별로 한 시험 이상.

문제 목록: wiki/records/reports/2026-09-29_0210_이동계산기_점검_문제목록.md
시간표 데이터(DATA_DIR) 없이 도는 시험만 둔다 — 데이터 축은 기존 시험 파일이 맡는다.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.modules.travel_ops.mobility.engine.paths import RULES_DIR
from app.modules.travel_ops.mobility.engine.timeutil import (
    CalendarOutOfRange, HolidayCalendar, day_type_of, to_min, to_min_ceil, to_service_min)

RULES = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))


def _verifier(**kw):
    """데이터 없이 만드는 작은 판정기 — 빈 시간표 · 노선 순서 없음."""
    from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier
    return Verifier(Timetable(), None, RULES, kw.pop("holidays", set()), **kw)


# ── #12 요청 시각의 초 — 출발은 올린다 ─────────────────────────────
def test_12_depart_seconds_round_up():
    assert to_min("10:00:59") == 600, "시간표 쪽은 종전대로 내림"
    assert to_min_ceil("10:00:59") == 601, "10:00:59 에 떠나는 사람은 10:00 열차를 못 탄다"
    assert to_min_ceil("10:00:00") == 600 and to_min_ceil("10:00") == 600, "초가 0 이면 그대로"
    assert to_service_min("00:30:10", ceil_seconds=True) == 24 * 60 + 31, "운행일 축으로 올린 뒤에도 초 올림"
    assert to_service_min("00:30:10") == 24 * 60 + 30, "기본값(도착 기한)은 내림"


# ── #17 시각이 아닌 값 ────────────────────────────────────────────
@pytest.mark.parametrize("bad", [-1, -0.5, "10:00:99", "10:61", "106199", "31:00"])
def test_17_invalid_times_are_none(bad):
    assert to_min(bad) is None
    assert to_min_ceil(bad) is None


# ── #5 공휴일 표 밖의 날짜 ────────────────────────────────────────
def test_5_calendar_refuses_uncovered_year():
    cal = HolidayCalendar.from_doc({"years": [2026, 2027], "holidays": {"2026-10-03": "개천절"}})
    assert day_type_of(date(2026, 10, 3), cal) == "holiday"
    assert day_type_of(date(2026, 10, 5), cal) == "weekday"
    with pytest.raises(CalendarOutOfRange):
        day_type_of(date(2028, 3, 1), cal)          # 앞 판은 삼일절을 평일로 판정했다
    with pytest.raises(CalendarOutOfRange):
        day_type_of(date(2028, 3, 4), cal)          # 주말도 같이 거절 — 옆 날짜만 되는 모양을 안 만든다


def test_5_plain_set_keeps_old_behaviour():
    assert day_type_of(date(2028, 3, 1), {"2026-10-03"}) == "weekday", "덮는 해를 모르는 옛 집합은 종전대로"


def test_5_real_holiday_file_declares_years():
    cal = HolidayCalendar.from_doc(json.loads((RULES_DIR / "holidays_2026_2027.json").read_text(encoding="utf-8")))
    assert cal.years == {2026, 2027}
    assert "2026-10-03" in cal


# ── #66 · #30 판정기가 SystemExit 로 프로세스를 끝내지 않는다 ──────────
def test_66_unknown_disruption_kind_is_input_error_not_exit():
    from app.modules.travel_ops.mobility.engine.errors import CaseInputError
    with pytest.raises(CaseInputError):
        _verifier().verify_case({"id": "t", "date": "2026-10-05", "depart_at": "10:00",
                                 "disruptions": [{"kind": "bogus"}], "legs": []})
    assert not issubclass(CaseInputError, SystemExit), "일반 오류 처리(except Exception)로 잡혀야 한다"


def test_30_missing_depart_is_input_error():
    from app.modules.travel_ops.mobility.engine.errors import CaseInputError
    with pytest.raises(CaseInputError):
        _verifier().verify_case({"id": "t", "date": "2026-10-05", "arrive_by": "11:00", "legs": []})


def test_30_adapter_refuses_arrive_only_input():
    from app.modules.travel_ops.mobility.engine.adapter import map_task_to_case
    task = {"task_id": "t1", "case_id": "c1",
            "context": {"current_state": {"mobility": {
                "date": "2026-10-05", "arrive_by": "11:00",
                "legs": [{"line": "02호선", "from": "강남", "to": "잠실"}]}}}}
    case, note = map_task_to_case(task)
    assert case is None and note.startswith("mobility_input_incomplete:depart_at")


# ── #28 · #50 자전거 조회 ─────────────────────────────────────────────
#   99(2026-10-04) — 자전거 경로 계산(BikeRouter)을 지웠다. #10(빈 경로를 0 m·0 초로 만들지 않는다)·#28 의 라우터 실패 분류
#   시험 둘은 대상 코드와 함께 없어졌다. 승차 소요는 늘 근거없음이다(아래 test_99_*).
def test_28_bike_live_no_key_reason():
    from app.modules.travel_ops.mobility.engine.bike import BikeLive
    live = BikeLive(key=None)
    assert live.get("ST-1") is None and live.last_error == {"kind": "no_key"}


def test_50_bike_live_error_never_carries_key(monkeypatch):
    import urllib.request

    from app.modules.travel_ops.mobility.engine.bike import BikeLive

    def boom(url, timeout):
        raise OSError(f"failed {url}")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    live = BikeLive(key="SECRETKEY123")
    assert live.get("ST-1") is None
    assert "SECRETKEY123" not in json.dumps(live.last_error), "키가 든 주소를 오류에 싣지 않는다"


# ── #9 자전거 대수는 일행 인원만큼 · 설문 값 옮기기 ────────────────────
def test_9_bike_needs_one_per_person():
    from app.modules.travel_ops.mobility.engine.bike import BikeStations
    st = {"stationId": "ST-1", "name": "대여소1", "lat": 37.5000, "lon": 127.0000, "mode": "QR", "rack": 10}
    st2 = {"stationId": "ST-2", "name": "대여소2", "lat": 37.5100, "lon": 127.0100, "mode": "QR", "rack": 10}
    v = _verifier(bk=BikeStations([st, st2]))
    leg = {"mode": "bike", "from": {"lat": 37.5001, "lng": 127.0001, "name": "출발"},
           "to": {"lat": 37.5101, "lng": 127.0101, "name": "도착"}}
    live = {"checked_at": "2026-09-29T10:00", "counts": {"ST-1": 2, "ST-2": 9}}
    one = v.verify_leg_bike(1, leg, 600, "weekday", party={"size": 1}, live_fixture=live)
    four = v.verify_leg_bike(1, leg, 600, "weekday", party={"size": 4}, live_fixture=live)
    assert one.verdict == "feasible"
    assert four.verdict == "infeasible" and "4명" in four.reason, "4인 일행에 2대면 빌릴 수 없다"


def test_99_bike_ride_is_unknown_and_says_why():
    """99(2026-10-04) — 자전거 경로 계산이 없다: 대여소는 찾되 승차 소요·도착을 내지 않고 이유를 말한다(다른 수단 값으로 안 바꾼다).
    ☆101(2026-10-05) 뜻이 좁아졌다 — 「늘 없다」가 아니라 **길찾기가 없을 때**(시험·명령줄 기본 · 도로 그래프 없음)다. 길찾기가 있으면
      승차 소요를 다시 낸다(아래 test_101_bike_ride_comes_back_with_a_router)."""
    from app.modules.travel_ops.mobility.engine.bike import BikeStations
    from app.modules.travel_ops.mobility.engine.verify_time import BIKE_NO_ROUTE
    st = {"stationId": "ST-1", "name": "대여소1", "lat": 37.5000, "lon": 127.0000, "mode": "QR", "rack": 10}
    st2 = {"stationId": "ST-2", "name": "대여소2", "lat": 37.5100, "lon": 127.0100, "mode": "QR", "rack": 10}
    v = _verifier(bk=BikeStations([st, st2]))
    assert v.bike_router is None
    leg = {"mode": "bike", "from": {"lat": 37.5001, "lng": 127.0001, "name": "출발"},
           "to": {"lat": 37.5101, "lng": 127.0101, "name": "도착"}}
    r = v.verify_leg_bike(1, leg, 600, "weekday", party={"size": 1},
                          live_fixture={"checked_at": "2026-09-29T10:00", "counts": {"ST-1": 2}})
    assert r.verdict == "feasible" and r.ride_min is None and r.arrive_min is None and r.ride_grade == "근거없음"
    assert r.grade == "근거없음" and BIKE_NO_ROUTE in r.reason
    assert any(BIKE_NO_ROUTE in (e.get("claim") or e.get("source_id") or "") or BIKE_NO_ROUTE in str(e) for e in r.evidence)


def test_101_bike_ride_comes_back_with_a_router():
    """101(2026-10-05 · 본인 결정) — 길찾기가 있으면 자전거 승차 소요·도착을 다시 낸다(추정) · 근거에 길찾기가 말한 출처가 찍힌다."""
    from app.modules.travel_ops.mobility.engine.bike import BikeRouter, BikeStations

    class Local:
        is_local, basis, source_id = True, "로컬 도로그래프", "osm_road_graph_v2@2026-09-18"

        def route(self, s, e, profile="car", via=None):
            return {"paths": [{"distance": 1500.0 if profile == "bike" else 20.0,
                               "time": 360000 if profile == "bike" else 15000}]}

    st = {"stationId": "ST-1", "name": "대여소1", "lat": 37.5000, "lon": 127.0000, "mode": "QR", "rack": 10}
    st2 = {"stationId": "ST-2", "name": "대여소2", "lat": 37.5100, "lon": 127.0100, "mode": "QR", "rack": 10}
    v = _verifier(bk=BikeStations([st, st2]))
    v.bike_router = BikeRouter(Local(), {}, "2026-09-18")
    leg = {"mode": "bike", "from": {"lat": 37.5001, "lng": 127.0001, "name": "출발"},
           "to": {"lat": 37.5101, "lng": 127.0101, "name": "도착"}}
    r = v.verify_leg_bike(1, leg, 600, "weekday", party={"size": 1},
                          live_fixture={"checked_at": "2026-09-29T10:00", "counts": {"ST-1": 2}})
    assert r.verdict == "feasible" and r.ride_min == 6 and r.ride_grade == "추정" and r.arrive_min is not None
    assert any((e.get("source_id") or "") == "osm_road_graph_v2@2026-09-18" for e in r.evidence if isinstance(e, dict))


def _key_env(monkeypatch, tmp_path, env=None, dot_env=None, apikeys=None):
    """final_project_cs/.env · .env.apikeys 를 임시 폴더에 만들고 그 자리를 저장소 맨 위로 본다. None = 파일 없음."""
    from app.modules.travel_ops.mobility.engine import bike as B
    from app.modules.travel_ops.mobility.engine import paths
    cs = tmp_path / "final_project_cs"
    cs.mkdir(exist_ok=True)
    for name, text in ((".env", dot_env), (".env.apikeys", apikeys)):
        if text is not None:
            (cs / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ACOP_SEOUL_OPENAPI_KEY", raising=False)
    monkeypatch.delenv("SEOUL_OPENAPI_KEY", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    live = B.BikeLive.from_env()
    return live.key if live else None


def test_99_bike_live_key_name(monkeypatch, tmp_path):
    """99 — 명령줄 관례의 키는 팀 양식 이름(ACOP_SEOUL_OPENAPI_KEY)만. 옛 이름(SEOUL_OPENAPI_KEY)은 읽지 않는다."""
    assert _key_env(monkeypatch, tmp_path, env={"SEOUL_OPENAPI_KEY": "OLDNAME"}, dot_env="SEOUL_OPENAPI_KEY=OLDFILE\n") is None
    assert _key_env(monkeypatch, tmp_path, env={"ACOP_SEOUL_OPENAPI_KEY": "NEWNAME"}) == "NEWNAME"


def test_99_bike_live_key_order_matches_collect_scripts(monkeypatch, tmp_path):
    """99(GPT #2) — 읽는 순서가 수집 쪽 `_paths.api_key()` 와 같다: 두 파일을 먼저 합치고(뒤 파일 .env.apikeys 가 이긴다 ·
    **빈 값으로 적혀 있으면 빈 값**) 환경변수가 그 앞. 앞 판은 뒤 파일의 빈 값을 무시하고 앞 파일 키를 되살렸다."""
    one = "ACOP_SEOUL_OPENAPI_KEY=FROM_ENV_FILE\n"
    assert _key_env(monkeypatch, tmp_path, dot_env=one) == "FROM_ENV_FILE"
    assert _key_env(monkeypatch, tmp_path, dot_env=one, apikeys="ACOP_SEOUL_OPENAPI_KEY=FROM_APIKEYS\n") == "FROM_APIKEYS"
    assert _key_env(monkeypatch, tmp_path, dot_env=one, apikeys="ACOP_SEOUL_OPENAPI_KEY=\n") is None, "뒤 파일의 빈 값이 이긴다"
    assert _key_env(monkeypatch, tmp_path, dot_env=one, apikeys="OTHER=1\n") == "FROM_ENV_FILE", "뒤 파일에 칸이 없으면 앞 파일 값"
    assert _key_env(monkeypatch, tmp_path, env={"ACOP_SEOUL_OPENAPI_KEY": "FROM_OS"}, dot_env=one,
                    apikeys="ACOP_SEOUL_OPENAPI_KEY=FROM_APIKEYS\n") == "FROM_OS", "환경변수가 먼저"
    assert _key_env(monkeypatch, tmp_path, env={"ACOP_SEOUL_OPENAPI_KEY": "   "}, dot_env=one, apikeys="OTHER=1\n") is None, \
        "공백뿐인 환경변수는 그 값으로 읽고(파일로 안 넘어감) 다듬어 빈 값 — 수집 쪽과 같다"
    # 수집 쪽 함수와 같은 입력에서 같은 답인지 직접 대조(값이 있을 때)
    import importlib.util
    import sys
    from pathlib import Path
    src = Path(__file__).resolve().parents[5] / "datasets" / "mobility" / "scripts" / "_paths.py"
    if src.exists():
        spec = importlib.util.spec_from_file_location("_paths_collect_99", src)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod, "CS_ROOT", tmp_path / "final_project_cs")
        got = _key_env(monkeypatch, tmp_path, dot_env=one, apikeys="ACOP_SEOUL_OPENAPI_KEY=FROM_APIKEYS\n")
        assert mod.api_key("seoul") == got == "FROM_APIKEYS"
        _key_env(monkeypatch, tmp_path, dot_env=one, apikeys="ACOP_SEOUL_OPENAPI_KEY=\n")
        with pytest.raises(SystemExit):
            mod.api_key("seoul")


def test_9_party_of_reads_survey_domestic():
    from app.modules.travel_ops.mobility.engine.plan import party_of
    assert party_of(4, {"survey": {"domestic": True}}) == {"size": 4, "foreign": False}
    assert party_of(2, {"survey": {"domestic": False}})["foreign"] is True
    assert "foreign" not in party_of(2, {"survey": {"party": "아이 둘"}}), "자유 문장에서 짐작하지 않는다"


# ── #16 버스 소요 48시간 경계 ─────────────────────────────────────────
def test_16_bus_profile_checks_end_of_last_segment():
    from app.modules.travel_ops.mobility.engine.bus_profile import BusSegProfile
    prof = BusSegProfile.__new__(BusSegProfile)
    prof.row = lambda route_id, s, nxt: (None, False)
    stops = [{"seq": 1}, {"seq": 2, "sect_dist_m": 500}]
    late = BusSegProfile.walk(prof, "R", stops, 1, 2, 2 * 1440 - 1, lambda t: ("weekday",), "q50", 1,
                              fallback=lambda m: 2.0)
    assert late.out_of_range, "47:59 에 들어가 48:01 에 끝나면 범위 밖"
    ok = BusSegProfile.walk(prof, "R", stops, 1, 2, 600, lambda t: ("weekday",), "q50", 1, fallback=lambda m: 2.0)
    assert not ok.out_of_range and ok.minutes == 2.0


# ── #51 판정 기록 가리기 ──────────────────────────────────────────────
@pytest.mark.parametrize("raw,hidden", [("문의 a@b.com", "a@b.com"), ("010-1234-5678", "1234"),
                                        ("lat=37.5, lon=127.0", "37.5"), ("37.5,127.0", "127.0")])
def test_51_scrub_hides_pii_and_single_coords(raw, hidden):
    from app.modules.travel_ops.mobility.devtools.judgment_log import scan_blocked, scrub_text
    assert hidden not in scrub_text(raw)
    assert scan_blocked(raw), "검사도 잡는다"


@pytest.mark.parametrize("plain", ["2호선 강남 10:00 출발", "버스 7022 23:10", "요금 1,400원", "tago_subway@2026-09-09",
                                   # 실행 번호 — 새벽 2시대 + 무작위 뒷자리가 숫자 넷이면 서울 번호 모양이 됐다(시험이 흔들린 원인)
                                   "20260929T024107-1234ab", "20260929T031500-5678cd"])
def test_51_scrub_leaves_ordinary_text(plain):
    from app.modules.travel_ops.mobility.devtools.judgment_log import scan_blocked, scrub_text
    assert scrub_text(plain) == plain and not scan_blocked(plain)


# ── #18 결과 접기 ────────────────────────────────────────────────────
_BASIS = {"timetable_built_at": "built:t", "rules_version": "v", "service_date": "2026-10-05", "decided_at": "t"}


def test_18_answer_has_no_english_verdict_words():
    from app.modules.travel_ops.mobility.engine.fold import fold_case
    out = fold_case({"id": "c", "verdict": "feasible", "grade": "확정", "reason": "성립",
                     "legs": [{"label": "02호선 강남→잠실", "verdict": "feasible", "grade": "확정", "arrive_min": 620}]},
                    task_id="t", basis=_BASIS)
    assert out["outcome"] == "completed"
    assert " ok" not in out["answer"] and "가능" in out["answer"]


def test_18_unknown_verdict_is_not_packaged_as_completed():
    from app.modules.travel_ops.mobility.engine.fold import fold_case
    out = fold_case({"id": "c", "verdict": "unknown", "grade": "근거없음", "reason": "시간표 없음",
                     "legs": [{"label": "02호선 강남→잠실", "verdict": "unknown", "grade": "근거없음"}]},
                    task_id="t", basis=_BASIS)
    assert out["outcome"] == "escalated" and out["failure_code"] == "mobility_no_data"
    assert out["evidence"], "근거는 그대로 싣는다"
