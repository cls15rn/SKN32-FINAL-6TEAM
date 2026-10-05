# -*- coding: utf-8 -*-
"""도로 사건의 **2차 소스(ITS)** 와 「1차·2차 대체」 합성 — 결정 15 의 대체를 이 자리에 구현 (2026-10-05).

UTIC 키는 고정 IP 에 묶여 서버 이전·경유 장애 때 통째로 거절된다. 그 전엔 경로 사건이 곧바로 치명이었다 — 이제 ITS 가 받고,
**둘 다 못 읽을 때만** 치명(`None`)이다. 한쪽만 못 읽으면 읽은 쪽으로 답하고 `degraded` 에 남긴다.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.infrastructure.travel.its_traffic import ItsRouteEvents, ItsTrafficEvents
from app.infrastructure.travel.subway_notice import AlternateRouteEvents, CompositeRouteEvents
from app.infrastructure.travel.utic import UticIncidents, UticRouteEvents

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 5, 8, 30, tzinfo=KST)


def _item(message, road="강변북로", event="교통사고", start="20261005080000", end="20261005120000", lanes=""):
    return {"eventId": "E1", "eventType": event, "roadName": road, "type": "도시고속도로", "coordX": "126.98", "coordY": "37.53",
            "message": message, "startDate": start, "endDate": end, "lanesBlocked": lanes, "eventDetailType": ""}


def _its(*items, status=200, body=None):
    payload = body if body is not None else {"header": {"resultCode": 0, "resultMsg": "SUCCESS"},
                                             "body": {"totalCount": len(items), "items": list(items)}}
    source = ItsTrafficEvents(service_key="k", now=lambda: NOW, transport=lambda url, params:
                              httpx.Response(status, json=payload, request=httpx.Request("GET", url)))
    return ItsRouteEvents(source)


def _utic(*records, text=None):
    body = text if text is not None else "<result>" + "".join(records) + "</result>"
    return UticRouteEvents(UticIncidents(service_key="k", now=lambda: NOW, transport=lambda url, params:
                                         httpx.Response(200, text=body, request=httpx.Request("GET", url))))


UTIC_RALLY = ("<record><incidentId>U1</incidentId><locationDataX>126.97</locationDataX><locationDataY>37.57</locationDataY>"
              "<incidentTitle>[집회] 세종대로 광화문</incidentTitle><startDate>2026년 10월 05일  08시 00분</startDate>"
              "<endDate>2026년 10월 05일  12시 00분</endDate><lane>차로</lane><roadName>세종대로</roadName></record>")
UTIC_DOWN = '[{"resultCode":"03","resultMsg":"허용된 IP가 아닙니다."}]'


def test_its_answers_road_targets_from_its_road_name_and_ignores_partial_lane_works():
    events = _its(_item("<교통사고>::강변북로::성수대교남단::동호대교남단::전체차로::[사고] 차량 사고"),
                  _item("<공사>::올림픽대로::a::b::1차로::[공사/통제] 부분 차로", road="올림픽대로", event="공사", lanes="1차로"))
    found = events.affecting(["도로:강변북로", "도로:올림픽대로", "2호선:이대"])
    assert list(found) == ["도로:강변북로"]
    ev = found["도로:강변북로"]
    assert ev["effect"] == "road_control" and ev["source"] == "its" and "ITS" in ev["summary"] and ev["ends_at"]
    assert events.unsupported(["2호선:이대", "도로:강변북로", "버스:405"]) == ["2호선:이대", "버스:405"]


def test_its_ignores_finished_or_not_yet_started_incidents_and_unreadable_is_none():
    assert _its(_item("<교통사고>::강변북로::a::b::전체차로::[사고]", end="20261005080000")).affecting(["도로:강변북로"]) == {}
    assert _its(_item("<교통사고>::강변북로::a::b::전체차로::[사고]", start="20261005100000")).affecting(["도로:강변북로"]) == {}
    assert _its(status=500).affecting(["도로:강변북로"]) is None
    assert _its(body={"header": {"resultCode": 4005, "resultMsg": "유효하지 않은 인증키"}}).affecting(["도로:강변북로"]) is None


def test_alternate_asks_both_and_merges_with_primary_first():
    alt = AlternateRouteEvents(_utic(UTIC_RALLY), _its(_item("<교통사고>::강변북로::a::b::전체차로::[사고]")))
    found = alt.affecting(["도로:세종대로", "도로:강변북로"])
    assert sorted(found) == ["도로:강변북로", "도로:세종대로"]
    assert found["도로:세종대로"]["source"] == "utic" and found["도로:강변북로"]["source"] == "its"
    assert alt.degraded == []
    both = AlternateRouteEvents(_utic(UTIC_RALLY.replace("세종대로", "강변북로")), _its(_item("<교통사고>::강변북로::a::b::전체차로::[사고]")))
    assert both.affecting(["도로:강변북로"])["도로:강변북로"]["source"] == "utic", "겹치면 1차 값"


def test_when_primary_is_down_the_secondary_answers_and_degraded_names_it_instead_of_fatal():
    alt = AlternateRouteEvents(_utic(text=UTIC_DOWN), _its(_item("<교통사고>::강변북로::a::b::전체차로::[사고]")))
    found = alt.affecting(["도로:강변북로", "도로:세종대로"])
    assert found is not None and list(found) == ["도로:강변북로"], "UTIC 가 거절돼도 ITS 로 답한다 — 치명이 아니다"
    assert alt.degraded == ["utic_route_events"]
    quiet = AlternateRouteEvents(_utic(text=UTIC_DOWN), _its()).affecting(["도로:세종대로"])
    assert quiet == {}, "읽은 쪽(ITS)이 비어 있으면 그 소스 기준의 「사건 없음」"


def test_both_down_is_fatal_none_and_unsupported_is_the_intersection():
    alt = AlternateRouteEvents(_utic(text=UTIC_DOWN), _its(status=500))
    assert alt.affecting(["도로:세종대로"]) is None and sorted(alt.degraded) == ["its_route_events", "utic_route_events"]
    assert alt.unsupported(["도로:세종대로", "2호선:이대"]) == ["2호선:이대"]


def test_composite_with_subway_still_asks_each_source_only_its_own_targets():
    from app.infrastructure.travel.subway_notice import SubwayNotices, SubwayRouteEvents
    subway = SubwayRouteEvents(SubwayNotices(service_key="k", now=lambda: NOW, transport=lambda url, params: httpx.Response(
        200, json={"header": {"resultCode": "00"}, "body": {"items": {"item": []}}}, request=httpx.Request("GET", url))))
    comp = CompositeRouteEvents([AlternateRouteEvents(_utic(text=UTIC_DOWN), _its(_item("<교통사고>::강변북로::a::b::전체차로::[사고]"))), subway])
    assert list(comp.affecting(["도로:강변북로", "2호선:이대", "버스:405"])) == ["도로:강변북로"]
    assert comp.unsupported(["도로:강변북로", "버스:405", "9호선:여의도"]) == ["버스:405", "9호선:여의도"]
