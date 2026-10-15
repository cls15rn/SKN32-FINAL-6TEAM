# -*- coding: utf-8 -*-
"""예약 모듈 — 공항철도(AREX) 직통(`[2026-10-15]`). 시간표 파일에서 직통 가르기 → 서울역 ↔ 인천공항이면 TAGO 대신 시간표로 → 답.

시각 값은 `arex_direct.json`(이동 모듈 시간표에서 빌드)을 그대로 읽는다 — 시험이 시각을 지어내지 않는다. 가르는 규칙만 작은 가짜 행으로 본다.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.domains.travel_ops.components.booking.offers import arex, search_transit
from app.domains.travel_ops.instances.booking.team import _same_run
from scripts.build_arex_direct import build

from .test_transit_module import GO, FakeTago, _rules, _run

FRIDAY, SATURDAY = date(2026, 10, 16), date(2026, 10, 17)


def _row(station, direction, clock, day_type="weekday"):
    return {"line": "공항철도", "station_nm": station, "day_type": day_type, "dep_time": clock, "dir": direction}


def test_the_build_rule_keeps_runs_that_skip_the_next_station():
    """서울역 하행 중 공덕에 안 이어지는 편 · T2 상행 중 T1 에 일반 간격으로 안 이어지는 편(그 T1 출발은 직통 간격으로 찾는다)."""
    rows = [_row("서울역", "D", "06:00:00"), _row("공덕", "D", "06:04:30"), _row("서울역", "D", "06:10:00"), _row("공덕", "D", "06:14:30"),
            _row("서울역", "D", "06:05:00"),                                                   # 공덕 06:09:30 이 없다 → 직통
            _row("인천공항2터미널", "U", "05:00:00"), _row("인천공항1터미널", "U", "05:06:30"),
            _row("인천공항2터미널", "U", "05:30:00"), _row("인천공항1터미널", "U", "05:36:30"),
            _row("인천공항2터미널", "U", "05:16:00"), _row("인천공항1터미널", "U", "05:24:00")]   # 6분 30초가 아니라 8분 → 직통
    got = build(rows)
    assert got["runs"]["weekday"] == {"서울역": ["06:05"], "인천공항2터미널": ["05:16"], "인천공항1터미널": ["05:24"]}
    assert got["checks"]["weekday"]["t2_to_t1_express_gap_s"] == 480


def test_the_built_file_has_both_day_types_and_every_direction():
    data = arex.table()
    assert data["collected"] and "timetable_v1.jsonl.gz" in data["source"]
    for day_type in ("weekday", "holiday"):
        runs = data["runs"][day_type]
        assert set(runs) == {"서울역", "인천공항1터미널", "인천공항2터미널"}
        assert all(clocks == sorted(clocks) and clocks for clocks in runs.values())
        assert len(runs["인천공항1터미널"]) == len(runs["인천공항2터미널"]), "T1 출발은 T2 출발 편마다 하나"


@pytest.mark.parametrize("origin,destination,expected", [
    ("서울", "인천공항", ("서울역", "인천공항1터미널")), ("서울역", "인천공항T2", ("서울역", "인천공항2터미널")),
    ("인천공항 2터미널", "서울", ("인천공항2터미널", "서울역")), ("인천공항1터미널", "서울역", ("인천공항1터미널", "서울역")),
    ("서울", "부산", None), ("인천공항T1", "인천공항T2", None), ("용산", "인천공항", None),
])
def test_only_seoul_station_to_the_airport_is_an_arex_direct_pair(origin, destination, expected):
    assert arex.pair(origin, destination) == expected


def test_the_day_type_uses_weekends_and_the_holiday_source_and_says_when_it_does_not_know():
    holiday = SimpleNamespace(is_holiday=lambda day: {"is_holiday": day == FRIDAY})
    assert arex.day_type(SATURDAY) == ("holiday", "weekend")
    assert arex.day_type(FRIDAY, holiday) == ("holiday", "public_holiday")
    assert arex.day_type(date(2026, 10, 15), holiday) == ("weekday", "weekday")
    assert arex.day_type(FRIDAY) == ("weekday", "holiday_unknown"), "공휴일 소스가 없으면 평일표 + 모름"
    broken = SimpleNamespace(is_holiday=lambda day: None)
    assert arex.day_type(FRIDAY, broken) == ("weekday", "holiday_unknown")


def test_a_seoul_to_airport_train_search_reads_the_timetable_without_calling_tago():
    tago = FakeTago()
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="train", origin="서울", destination="인천공항 2터미널",
                         depart_date=SATURDAY.isoformat(), adults=2)
    assert tago.calls == [], "TAGO 기차에는 AREX 직통이 없다 — 부르지 않는다"
    expected = arex.table()["runs"]["holiday"]["서울역"]
    assert [option["depart"] for option in got["options"]] == expected
    first = got["options"][0]
    assert (first["grade"], first["kind"], first["number"], first["price"], first["links"]) == ("AREX 직통", "arex", None, None, [])
    assert first["minutes"] == arex.RIDE_MINUTES[("서울역", "인천공항2터미널")]
    assert got["resolved"] == {"origin": "서울역", "destination": "인천공항2터미널"}
    assert got["timetable"]["day_type"] == "holiday" and got["timetable"]["basis"] == "weekend"
    assert got["sources"]["tago"]["status"] == "ok" and got["sources"]["tago"]["via"] == "arex_timetable" and got["sources"]["tago"]["calls"] == []
    assert got["elsewhere"] == [arex.SITE]


def test_the_timetable_answers_even_without_a_tago_key():
    got = search_transit(SimpleNamespace(), mode="train", origin="인천공항", destination="서울", depart_date=FRIDAY.isoformat())
    assert got["sources"]["tago"]["status"] == "ok" and got["options"]
    assert got["options"][0]["origin"] == "인천공항1터미널" and got["timetable"]["basis"] == "holiday_unknown"


@pytest.mark.parametrize("text,origin,destination,grade", [
    ("내일 서울역에서 인천공항 2터미널 가는 공항철도 2명", "서울", "인천공항 2터미널", None),
    ("AREX로 인천공항에서 서울역 내일 1명", "인천공항", "서울", "AREX"),
    ("내일 서울에서 인천공항 직통열차 2명", "서울", "인천공항", None),
])
def test_the_rules_read_arex_words_as_a_train(text, origin, destination, grade):
    found, missing = _rules(text)
    assert (found.mode, found.origin, found.destination, found.grade, missing) == ("train", origin, destination, grade, [])


def test_an_arex_sentence_is_answered_from_the_timetable_with_the_first_page_link_and_no_fare():
    tago = FakeTago()
    result, tools = _run(f"{GO.month}월 {GO.day}일 서울역에서 인천공항 가는 공항철도 2명 오전", tago)
    assert result.outcome == "completed" and tago.calls == []
    answer = result.answer
    table = "주말 · 공휴일" if GO.weekday() >= 5 else "평일"
    assert answer.startswith(f"서울 → 인천공항 · {GO.isoformat()} · 성인 2명 조건으로 찾은 기차 운행입니다.")
    assert "[가는 편] 서울역 → 인천공항1터미널 · 전체" in answer
    assert "AREX 직통 — 서울역 " in answer and "→ 인천공항1터미널 " in answer and "(0시간 43분) · 어른 요금은 예매 화면에서" in answer
    assert f"공항철도 직통은 {table} 시간표 기준이에요(" in answer and "그날 운휴 · 임시 변경 · 남은 좌석 · 요금은 예매 화면에서 확인해 주세요." in answer
    assert "AREX 공항철도 직통열차 예매: https://www.airportrailroad.com/intro" in answer
    assert "출발 · 도착 · 날짜를 채워 엽니다" not in answer, "AREX 는 조건을 채운 주소가 없다"
    assert "어른 1인 기준 조회 시점 참고값" not in answer, "요금을 안 보여 줬으면 요금 안내 줄도 없다"
    decision = result.decisions[0]
    assert decision["via"] == ["arex_timetable"] and decision["tago_calls"] == 0 and decision["booking_links"] == []
    assert all(shown["price"] is None and shown["grade"] == "AREX 직통" for shown in decision["shown"])


def test_a_train_without_a_number_is_matched_by_its_stations_on_recheck():
    run = {"mode": "train", "grade": "AREX 직통", "number": None, "date": "2026-10-16", "depart": "09:45",
           "origin": "서울역", "destination": "인천공항1터미널"}
    assert _same_run(dict(run), dict(run))
    assert not _same_run({**run, "destination": "인천공항2터미널"}, run)
    ktx = {**run, "grade": "KTX", "number": "101"}
    assert _same_run(dict(ktx), dict(ktx)) and not _same_run({**ktx, "number": "103"}, ktx), "번호가 있는 열차는 번호로(그대로)"
