# -*- coding: utf-8 -*-
"""서울교통공사 지하철알림정보로 역 무정차 통과를 감시한다 — 문제목록 #36 (2026-10-04).

★실측 모양(공공데이터포털 15144070): `{header:{resultCode:"00"}, body:{totalCount, items:{item:[…]}}}` · 최신순.
★「사건 없음」은 「알림에서 못 찾음」이다 — 못 읽으면 `None`(치명), 이 소스가 모르는 대상은 `unsupported()` 로 드러난다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.infrastructure.travel.subway_notice import (CompositeRouteEvents, SubwayNotices, SubwayRouteEvents,
                                                     is_nonstop_start, mentions_station)
from app.infrastructure.travel.utic import UticIncidents, UticRouteEvents

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 22, 21, 30, tzinfo=KST)


def _row(title, when="2026-09-22T21:22:23", line="2호선", content="", nonstop="Y"):
    return {"noftTtl": title, "noftCn": content, "noftOcrnDt": when, "lineNmLst": line, "stnSctnCdLst": "0241",
            "crtrYmd": "20260922", "noftSeCd": "4", "nonstopYn": nonstop, "upbdnbSe": "내선, 외선",
            "xcseSitnBgngDt": None, "xcseSitnEndDt": None}


def _events(*rows, status=200, body=None):
    payload = body if body is not None else {"header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
                                             "body": {"numOfRows": 100, "pageNo": 1, "totalCount": len(rows),
                                                      "items": {"item": list(rows)}}}
    seen = []

    def transport(url, params):
        seen.append(dict(params))
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    notices = SubwayNotices(service_key="k", now=lambda: NOW, transport=transport)
    events = SubwayRouteEvents(notices)
    events.seen = seen
    return events


def test_a_running_nonstop_notice_for_the_station_becomes_a_skip_station_event():
    events = _events(_row("2호선 이대역 안전문장애로 열차 무정차 통과중"))
    found = events.affecting(["2호선:이대", "2호선:신촌"])
    assert list(found) == ["2호선:이대"]
    ev = found["2호선:이대"]
    assert ev["effect"] == "skip_station" and ev["source"] == "subway_notice" and ev["ends_at"] is None
    assert "이대역 무정차 통과" in ev["summary"] and "서울교통공사" in ev["summary"] and ev["notice_at"] == "2026-09-22T21:22:23"
    assert events.seen[0]["serviceKey"] == "k" and events.seen[0]["dataType"] == "JSON", "키는 그대로(이중 인코딩 없음) · JSON 요청"


def test_the_latest_notice_decides_so_a_resolved_nonstop_is_not_active():
    events = _events(_row("2호선 이대역 무정차 통과 종료", when="2026-09-22T21:28:00"),
                     _row("2호선 이대역 안전문장애로 열차 무정차 통과중", when="2026-09-22T21:22:23"))
    assert events.affecting(["2호선:이대"]) == {}, "종료 알림이 더 늦다"
    events = _events(_row("2호선 이대역 열차 정상운행", when="2026-09-22T20:00:00"),
                     _row("2호선 이대역 안전문장애로 열차 무정차 통과중", when="2026-09-22T21:22:23"))
    assert list(events.affecting(["2호선:이대"])) == ["2호선:이대"], "무정차 알림이 더 늦다"


def test_nonstop_yn_alone_is_not_the_signal_because_end_notices_carry_it_too():
    events = _events(_row("5호선 여의나루역 무정차 통과 종료", line="5호선", nonstop="Y"))
    assert events.affecting(["5호선:여의나루"]) == {}
    assert is_nonstop_start("2호선 이대역 무정차 통과중") and not is_nonstop_start("2호선 이대역 무정차 통과 종료")
    assert not is_nonstop_start("2호선 이대역 열차 지연")


def test_a_notice_older_than_the_window_or_in_the_future_is_not_active():
    old = (NOW - timedelta(hours=3, minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    events = _events(_row("2호선 이대역 무정차 통과중", when=old))
    assert events.affecting(["2호선:이대"]) == {}, "끝 알림이 안 오는 경우를 위해 3시간이 지나면 진행 중으로 보지 않는다"
    events = _events(_row("2호선 이대역 무정차 통과중"))
    assert events.affecting(["2호선:이대"], NOW - timedelta(hours=1)) == {}, "그 시각엔 아직 알림이 없었다"
    assert list(events.affecting(["2호선:이대"], NOW)) == ["2호선:이대"]


def test_station_matching_needs_the_full_name_and_the_right_line():
    assert mentions_station("2호선 대방역 무정차", "대방") and not mentions_station("1호선 신대방역 무정차", "대방")
    assert mentions_station("서울역 열차 무정차 통과", "서울역")
    events = _events(_row("1호선 신대방역 무정차 통과중", line="2호선"))
    assert events.affecting(["2호선:대방"]) == {}, "신대방역 알림이 대방에 걸리면 안 된다"
    events = _events(_row("2호선 이대역 무정차 통과중", line="2호선"))
    assert events.affecting(["3호선:이대"]) == {}, "호선이 다르면 같은 역 이름이어도 아니다"


def test_a_station_list_title_only_catches_the_last_station_a_documented_limit():
    events = _events(_row("4호선 시위로 혜화, 한성대입구역(하선) 무정차 통과", line="4호선"))
    assert list(events.affecting(["4호선:한성대입구", "4호선:혜화"])) == ["4호선:한성대입구"]


def test_unsupported_targets_are_reported_not_answered_as_no_event():
    events = _events()
    assert events.unsupported(["2호선:이대", "9호선:여의도", "버스:405", "도로:세종대로", "경의선:공덕"]) == [
        "9호선:여의도", "버스:405", "도로:세종대로", "경의선:공덕"]
    assert events.affecting(["9호선:여의도", "도로:세종대로"]) == {} and events.seen == [], "모르는 대상만이면 부르지도 않는다"


def test_an_unreadable_feed_is_unknown_not_no_event():
    assert _events(status=500).affecting(["2호선:이대"]) is None
    bad = {"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}
    assert _events(body=bad).affecting(["2호선:이대"]) is None
    assert _events(body={"header": {"resultCode": "00"}, "body": {"items": ""}}).affecting(["2호선:이대"]) == {}, "알림이 하나도 없는 것은 실패가 아니라 빈 결과"
    assert SubwayRouteEvents(SubwayNotices(service_key="", now=lambda: NOW)).affecting(["2호선:이대"]) is None, "키가 없으면 모름"


def _utic(*, text="<result></result>"):
    source = UticIncidents(service_key="k", now=lambda: NOW, transport=lambda url, params:
                           httpx.Response(200, text=text, request=httpx.Request("GET", url)))
    return UticRouteEvents(source)


def test_the_composite_asks_each_source_only_its_own_targets_and_merges():
    road = ("<result><record><incidentId>X1</incidentId><locationDataX>126.97</locationDataX><locationDataY>37.57</locationDataY>"
            "<incidentTitle>[집회] 세종대로 광화문</incidentTitle><startDate>2026년 09월 22일  21시 00분</startDate>"
            "<endDate>2026년 09월 22일  23시 00분</endDate><lane>차로</lane><roadName>세종대로</roadName></record></result>")
    both = CompositeRouteEvents([_utic(text=road), _events(_row("2호선 이대역 무정차 통과중"))])
    found = both.affecting(["도로:세종대로", "2호선:이대", "2호선:신촌", "버스:405"])
    assert sorted(found) == ["2호선:이대", "도로:세종대로"]
    assert found["도로:세종대로"]["effect"] == "road_control" and found["2호선:이대"]["effect"] == "skip_station"
    assert both.unsupported(["도로:세종대로", "2호선:이대", "버스:405", "9호선:여의도"]) == ["버스:405", "9호선:여의도"], \
        "어느 소스도 모르는 대상만 확인 못 한 대상"


def test_the_composite_is_unknown_when_any_source_cannot_read_its_own_targets():
    broken = CompositeRouteEvents([_utic(), _events(status=500)])
    assert broken.affecting(["2호선:이대"]) is None
    assert broken.affecting(["도로:세종대로"]) == {}, "지하철 소스가 죽어도 도로만 물으면 도로 답은 낸다(자기 대상이 아니다)"
