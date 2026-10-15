# -*- coding: utf-8 -*-
"""접수 — 체크인 줄 + 체크아웃 줄 → 숙소 항목 하나(`kind: lodging`). `[2026-10-13 사용자 결정 — 숙소 항목 B안]`

☆전에는 「15:00 롯데호텔 체크인」이 숙소 분류 활동 하나로, 「11:00 체크아웃」은 장소를 못 찾은 활동으로 따로 남았다.
  숙소 팀은 항목의 시작 날을 체크인 · 끝 날을 체크아웃으로 읽는다 — 한 숙박이 한 항목이어야 「바꿔 줘」가 그 숙박을 찾는다.
"""
from __future__ import annotations

from datetime import datetime

from app.domains.travel_ops.components.intake.assemble import assemble, collect
from app.domains.travel_ops.components.itinerary.itinerary_checks import Part, check_itinerary

HOTEL = {"name": "롯데호텔 서울", "latitude": 37.5653, "longitude": 126.9810, "kind": "activity", "source": "kakao"}
DINNER = {"name": "명동교자", "latitude": 37.5636, "longitude": 126.9858, "kind": "dining", "source": "kakao"}
TOWER = {"name": "남산서울타워", "latitude": 37.5512, "longitude": 126.9882, "kind": "activity", "source": "kakao"}


def _claims(rows):
    claims = []
    for index, (line, title, day, start, place) in enumerate(rows):
        for field, value in (("title", title), ("date", day), ("starts_at", start), ("place", place)):
            if value is None:
                continue
            claims.append({"source_id": "s1", "field": f"items[{index}].{field}", "value": value, "method": "rule",
                           "evidence": {"line": line}})
    return claims


SOURCE = [{"source_id": "s1", "position": 0, "kind": "text",
           "transcript": "15:00 롯데호텔 체크인\n18:00 명동교자\n10:00 남산타워\n11:00 체크아웃"}]


def test_check_in_and_check_out_lines_become_one_lodging_item_across_days():
    claims = _claims([(1, "롯데호텔 체크인", "2030-11-06", "15:00", HOTEL), (2, "명동교자", "2030-11-06", "18:00", DINNER),
                      (3, "남산타워", "2030-11-07", "10:00", TOWER), (4, "체크아웃", "2030-11-09", "11:00", None)])
    got = assemble(intake_id="i1", revision=1, sources=SOURCE, claims=claims)
    items = got.body["items"]
    stay = next(item for item in items if item["kind"] == "lodging")
    assert stay["title"] == "롯데호텔", "제목에서 「체크인」을 뺀다"
    assert (stay["starts_at"], stay["ends_at"]) == ("2030-11-06T15:00:00+09:00", "2030-11-09T11:00:00+09:00")
    assert [item["title"] for item in items] == ["롯데호텔", "명동교자", "남산타워"], "체크아웃 줄은 따로 항목이 되지 않는다"
    assert not any(p.code == "no_place" for p in got.problems), "합친 체크아웃 줄의 「장소를 못 정함」은 막지 않는다"
    parts = [Part(seq=i["seq"], kind=i["kind"], title=i["title"],
                  starts_at=datetime.fromisoformat(i["starts_at"]),
                  ends_at=datetime.fromisoformat(i["ends_at"]) if i["ends_at"] else None)
             for i in items]
    assert check_itinerary(parts) == [], "등록 검사가 숙박 기간과 낮 일정의 겹침으로 거절하지 않는다"


def test_without_a_check_out_line_the_stay_runs_to_the_last_day_at_eleven_and_says_so():
    claims = _claims([(1, "롯데호텔 체크인", "2030-11-06", None, HOTEL), (3, "남산타워", "2030-11-08", "10:00", TOWER)])
    got = collect(sources=SOURCE, claims=claims)
    stay = next(row for row in got.rows if row["kind"] == "lodging")
    assert (stay["date"], stay["start"], stay["end_date"], stay["end"]) == ("2030-11-06", "15:00", "2030-11-08", "11:00")
    notes = {f["field"]: f["note"] for f in got.filled}
    assert "체크인 시각이 없어 15:00" in notes["items[0].starts_at"]
    assert "체크아웃 줄이 없어 2030-11-08 11:00" in notes["items[0].ends_at"]


def test_a_one_day_trip_with_only_a_check_in_stays_one_night():
    got = collect(sources=SOURCE, claims=_claims([(1, "롯데호텔 체크인", "2030-11-06", "15:00", HOTEL)]))
    stay = got.rows[0]
    assert (stay["kind"], stay["end_date"], stay["end"]) == ("lodging", "2030-11-07", "11:00")


def test_a_hotel_breakfast_is_still_a_meal_and_lines_without_check_in_are_untouched():
    claims = _claims([(1, "호텔 조식 체크인 전", "2030-11-06", "08:00", DINNER), (2, "명동교자", "2030-11-06", "18:00", DINNER)])
    claims.append({"source_id": "s1", "field": "items[0].kind", "value": "dining", "method": "rule", "evidence": {"line": 1}})
    got = collect(sources=SOURCE, claims=claims)
    assert [row["kind"] for row in got.rows] == ["dining", "dining"]


def test_the_lookup_title_drops_the_check_in_and_check_out_words_but_never_empties_a_line():
    """`[2026-10-15 LB-01]` 장소는 동작 말을 뺀 말로 찾는다 — 제목은 그대로 두고(조립이 같은 말로 체크인 · 체크아웃 줄을 묶는다) 찾는 말만 다르다."""
    from app.domains.travel_ops.components.intake.line_parts import lookup_title, parse

    assert lookup_title("더 플라자 호텔 서울 체크인") == "더 플라자 호텔 서울"
    assert lookup_title("롯데호텔 체크아웃") == "롯데호텔" and lookup_title("롯데호텔 퇴실") == "롯데호텔"
    assert lookup_title("Hotel Check-in") == "Hotel" and lookup_title("호텔 체크인/체크아웃") == "호텔"
    assert lookup_title("체크인") == "체크인" and lookup_title("체크아웃") == "체크아웃", "동작 말뿐인 줄은 그대로 — 앞 체크인 줄에 합쳐진다"
    assert lookup_title("경복궁 관람") == "경복궁 관람" and lookup_title(None) == ""
    # 이름 없는 「호텔 체크인」은 「호텔」로 읽어야 이름 없는 숙소 줄(숙소를 고르는 줄)이 된다
    assert parse("호텔 체크인").nameless is False and parse(lookup_title("호텔 체크인")).nameless is True
    assert parse(lookup_title("호텔 체크인")).content_type == "32"
