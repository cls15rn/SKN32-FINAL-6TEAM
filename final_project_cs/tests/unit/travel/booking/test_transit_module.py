# -*- coding: utf-8 -*-
"""예약 모듈 — 기차 · 버스(`[2026-10-14]` TAGO). 규칙 해석(교통수단 · 역 · 등급) → booking 모듈(역 · 터미널 옮기기 · 운행 합치기) → 답. 어댑터는 가짜다."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.core.contracts import ContextPack, TeamTask, ToolNotAllowed
from app.domains.travel_ops.components.booking.offers import search_transit
from app.domains.travel_ops.components.booking.offers.transit import STATIONS
from app.domains.travel_ops.components.booking.offers.transit_links import RAIL_CODES, bustago_link, run_links, srt_link, trip_link
from app.domains.travel_ops.instances.booking import BookingTeam
from app.domains.travel_ops.instances.booking.interpret import needs, parse
from app.domains.travel_ops.instances.booking.interpret_rules import interpret
from app.domains.travel_ops.ports.data_sources.tago_transit import TagoTransit

TODAY = datetime.now(ZoneInfo("Asia/Seoul")).date()
_NEXT = (TODAY.replace(day=1) + timedelta(days=32)).replace(day=1)
GO, BACK = _NEXT.replace(day=6), _NEXT.replace(day=9)
STAMP = GO.strftime("%Y%m%d")


def _rules(text):
    found = parse(interpret(text, today=TODAY))
    return found, needs(found, today=TODAY)


# ── 규칙 해석 ─────────────────────────────────────────────
@pytest.mark.parametrize("text,mode,origin,destination,grade,kind", [
    ("11월 6일 서울에서 부산 가는 KTX 2명 오전 출발", "train", "서울", "부산", "KTX", None),
    ("내일 동대구역에서 서울역 기차 혼자", "train", "동대구", "서울", None, None),
    ("다음 달 3일 서울에서 강릉 고속버스 3명", "bus", "서울", "강릉", None, "express"),
    ("서울 광주 시외버스 내일 우등 2명", "bus", "서울", "광주", "우등", "suburbs"),
    ("비행기 말고 기차로 부산 가고 싶어 11월 6일 2명", "train", None, "부산", None, None),
    ("기차 말고 비행기 김포 제주 내일 1명", "flight", "GMP", "CJU", None, None),
    ("11월 6일 인천에서 나리타 가는 편도 2명", "flight", "ICN", "NRT", None, None),
    ("대구에서 광주 가는 열차 11월 6일 2명", "train", "대구", "광주", None, None),   # 말한 이름 그대로 — 동대구 · 광주송정으로 옮기는 것은 booking 모듈
    ("서울에서 부산 KTX로 2명 내일", "train", "서울", "부산", "KTX", None),               # 검토 10-14: 영문 뒤 한글 조사는 단어 경계
    ("SRT를 타고 수서에서 부산 내일 1명", "train", "수서", "부산", "SRT", None),
    ("기차 제외하고 버스로 서울에서 부산 내일 2명", "bus", "서울", "부산", None, None),       # 팀장 점검 LB-06(10-15): 「제외」도 빼는 말
    ("KTX 안 타고 고속버스로 서울 부산 내일 1명", "bus", "서울", "부산", None, "express"),
    ("내일 서울에서 인천공항 리무진 2명", "bus", "서울", "인천공항", None, None),           # `[10-15]` 리무진 · 공항버스도 버스(전에는 뺐다)
])


def test_the_rules_read_the_transport_mode_and_station_names(text, mode, origin, destination, grade, kind):
    found, _ = _rules(text)
    assert (found.mode, found.origin, found.destination, found.grade, found.kind) == (mode, origin, destination, grade, kind)
    if mode != "flight":
        assert found.domestic is True and found.cabin is None and found.direct_only is None


@pytest.mark.parametrize("text,adults,bands", [
    ("저녁 7시 KTX 서울에서 부산 2명 내일", 2, ["evening"]),                # 검토 10-14: 「저녁 7시」는 저녁만(전에는 오전이 섞였다)
    ("밤 11시 서울에서 부산 버스 1명 내일", 1, ["evening"]),
    ("서울에서 부산 가는 KTX 오후 3시 30분 2명 내일", 2, ["afternoon"]),   # 「30분」은 인원이 아니다
    ("오후에 3시 비행기 김포 제주 내일 1명", 1, ["afternoon"]),
])
def test_hours_with_evening_words_and_minutes_are_read_right(text, adults, bands):
    found, _ = _rules(text)
    assert (found.adults, found.depart_times) == (adults, bands)


def test_depart_word_marks_the_outbound_leg_unless_the_clause_is_about_coming_back():
    """팀장 점검 LB-07(10-15): 「오전 출발하고 오후 귀국」 — 「출발」 절은 가는 편, 「귀국」 절은 오는 편."""
    found, _ = _rules(f"{GO.month}월 {GO.day}일 김포에서 제주 2명 오전 출발하고 {BACK.month}월 {BACK.day}일 오후 귀국")
    assert (found.depart_times, found.return_times) == (["morning"], ["afternoon"])
    found, _ = _rules(f"{GO.month}월 {GO.day}일 서울에서 부산 KTX 1명 오후에 출발하는 걸로 {BACK.month}월 {BACK.day}일 저녁에 돌아올게")
    assert (found.depart_times, found.return_times) == (["afternoon"], ["evening"]), "「돌아」가 있는 절의 「출발」은 가는 편 표지가 아니다"


def test_a_train_sentence_without_a_station_is_asked_back_not_guessed():
    found, missing = _rules("비행기 말고 기차로 부산 가고 싶어 11월 6일 2명")
    assert found.mode == "train" and missing == ["origin"]


def test_every_station_in_the_dictionary_maps_to_a_name_and_a_region():
    assert all(isinstance(name, str) and isinstance(city, str) and name and city for name, city in STATIONS.values())
    assert STATIONS["대구"] == ("동대구", "대구") and STATIONS["광주"] == ("광주송정", "광주") and STATIONS["청주"] == ("오송", "충북")


def test_rail_codes_cover_the_station_dictionary_and_booking_links_fill_the_conditions():
    """`[2026-10-14 저녁]` 조건을 채워 여는 예매 주소 — 코레일 역 코드(= SRT · 트립닷컴) · 버스타고 터미널 id. 사전에 없으면 주소를 안 만든다."""
    from app.domains.travel_ops.components.booking.offers.transit_links import _rail_code
    missing = sorted({name for name, _ in STATIONS.values() if _rail_code(name) is None})
    assert missing == ["검암", "인천공항T1", "인천공항T2"], "트립닷컴 · SRT 역 검색에 없는 곳만(10-14) — 인천공항은 `[10-15]` AREX 직통 시간표로 간다"
    assert _rail_code("울산(통도사)") == RAIL_CODES["울산"] == "0509" and _rail_code("김천(구미)") == "0507" and _rail_code("진부(오대산)") == "0519"
    assert all(len(code) == 4 and code.isdigit() for code in RAIL_CODES.values())
    assert srt_link("수서", "부산", "2026-10-16", hour="09", adults=1) == (
        "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000&dptRsStnCd=0551&arvRsStnCd=0020"
        "&dptDt=20261016&dptTm=090000&psgNum=1&psgInfoPerPrnb1=1")
    assert trip_link("동대구", "부산", "2026-10-16", adults=3).endswith("&departurecitycode=KR0015&arrivalcitycode=KR0020")
    assert "hidadultnum=3" in trip_link("동대구", "부산", "2026-10-16", adults=3)
    assert srt_link("인천공항T1", "부산", "2026-10-16") is None and trip_link("서울", "부산", "20261016") is None, "모르는 역 · 날짜 모양"
    bus = bustago_link("동서울", "부산동부(노포)", "2026-10-16", hour="09", adults=2)
    assert bus.startswith("https://www.bustago.or.kr/newweb/kr/ticket/ticket.do?startType=I&start=%EB%8F%99%EC%84%9C%EC%9A%B8&startId=0001&finish=")
    assert "&finishId=9001&goDateField=2026-10-16&goTimeField=09&adultCnt=2&universityCnt=0&studentCnt=0&childrenCnt=0" in bus
    assert bustago_link("동서울종합터미널", "강릉시외버스터미널", "2026-10-16") is not None, "「터미널 · 종합 · 시외버스」를 뗀 이름으로 맞춤"
    assert bustago_link("부산", "동서울", "2026-10-16") is None, "「부산」은 버스타고에 동부 · 서부 · 해운대가 있어 애매 → 안 만든다"
    assert bustago_link("동서울", "없는곳", "2026-10-16") is None
    assert [link["site"] for link in run_links(_train("SRT", "19:00", "21:20", 52600))] == ["srt", "trip"]
    assert [link["site"] for link in run_links(_train("KTX", "07:00", "09:40", 59800), adults=2)] == ["trip"]
    suseo = {**_train("KTX", "05:30", "08:06", 52700, "301"), "origin": "수서"}
    assert [link["site"] for link in run_links(suseo)] == ["srt", "trip"], "TAGO 는 SRT 운행도 등급 KTX 로 준다(10-14) — 수서 · 동탄이 끼면 SRT"
    assert run_links(_bus("express", "서울경부", "부산", "06:00", 26600)) == [], "고속버스(코버스)는 없다"
    assert run_links(_bus("suburbs", "동서울", "강릉시외터미널", "07:00", 15000))[0]["site"] == "bustago"
    assert run_links(_bus("suburbs", "동서울", "부산", "07:00", 15000)) == []


# ── 어댑터 ─────────────────────────────────────────────
class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload, self.text = payload, ""

    def json(self):
        return self._payload


def _payload(items):
    return {"response": {"header": {"resultCode": "00", "resultMsg": "OK"},
                         "body": {"items": {"item": items} if items else "", "numOfRows": 300, "pageNo": 1, "totalCount": len(items)}}}


def test_the_adapter_uses_the_2026_03_addresses_and_normalises_rows():
    """★주소는 `…/1613000/TrainInfo/GetStrtpntAlocFndTrainInfo`(2026-03 개편 — 옛 `TrainInfoService/get…` 은 NO_OPENAPI_SERVICE)."""
    urls = []

    def transport(url, params):
        urls.append((url, dict(params)))
        if url.endswith("/TrainInfo/GetCtyCodeList"):
            return _Response(_payload([{"citycode": 11, "cityname": "서울특별시"}, {"citycode": 26, "cityname": "부산광역시"}]))
        if url.endswith("/TrainInfo/GetCtyAcctoTrainSttnList"):
            code = params["cityCode"]
            return _Response(_payload([{"nodeid": "NAT010000", "nodename": "서울"}, {"nodeid": "NAT010032", "nodename": "영등포"}]
                                      if code == "11" else [{"nodeid": "NAT014445", "nodename": "부산"}]))
        if url.endswith("/TrainInfo/GetStrtpntAlocFndTrainInfo"):
            return _Response(_payload([
                {"adultcharge": 59800, "arrplacename": "부산", "arrplandtime": int(f"{STAMP}0940"), "depplacename": "서울",
                 "depplandtime": int(f"{STAMP}0700"), "traingradename": "KTX", "trainno": 101},
                {"adultcharge": 28600, "arrplacename": "부산", "arrplandtime": f"{STAMP}1330", "depplacename": "서울",
                 "depplandtime": f"{STAMP}0800", "traingradename": "무궁화호", "trainno": 1201}]))
        if url.endswith("/ExpBusInfo/GetExpBusTrminlList"):
            return _Response(_payload([{"terminalId": "NAEK010", "terminalNm": "서울경부"}, {"terminalId": "NAEK011", "terminalNm": "서울호남"},
                                       {"terminalId": "NAEK700", "terminalNm": "부산"}]))
        if url.endswith("/ExpBusInfo/GetStrtpntAlocFndExpbusInfo"):
            if params["depTerminalId"] == "NAEK010":
                return _Response(_payload([{"arrPlaceNm": "부산", "arrPlandTime": f"{STAMP}1030", "charge": 26600, "depPlaceNm": "서울경부",
                                            "depPlandTime": f"{STAMP}0600", "gradeNm": "우등", "routeId": "R1"}]))
            return _Response(_payload([]))
        raise AssertionError(url)

    source = TagoTransit(service_key="k", transport=transport)
    stations = source.train_stations("서울")
    assert stations == [{"id": "NAT010000", "name": "서울"}, {"id": "NAT010032", "name": "영등포"}]
    rows = source.train_schedules(dep_id="NAT010000", arr_id="NAT014445", date=GO.isoformat())
    assert [(r["grade"], r["number"], r["depart"], r["arrive"], r["minutes"], r["price"], r["date"]) for r in rows] == [
        ("KTX", "101", "07:00", "09:40", 160, 59800, GO.isoformat()), ("무궁화호", "1201", "08:00", "13:30", 330, 28600, GO.isoformat())]
    assert source.bus_terminals("express", "서울") == [{"id": "NAEK010", "name": "서울경부"}, {"id": "NAEK011", "name": "서울호남"}]
    bus = source.bus_schedules("express", dep_id="NAEK010", arr_id="NAEK700", date=GO.isoformat())
    assert bus[0]["kind"] == "express" and bus[0]["grade"] == "우등" and bus[0]["price"] == 26600 and bus[0]["minutes"] == 270
    assert all(("Service" not in url and "/Get" in url) for url, _ in urls), "2026-03 개편 주소 — Service 없음 · 동작 대문자"
    assert all(params["serviceKey"] == "k" and params["_type"] == "json" for _, params in urls)
    sent = [params for url, params in urls if url.endswith("GetStrtpntAlocFndTrainInfo")][0]
    assert sent["depPlandTime"] == STAMP and sent["depPlaceId"] == "NAT010000"


def test_a_time_stamp_with_only_a_date_or_an_hour_is_read_as_midnight_or_on_the_hour():
    """팀장 점검 LB-10(10-15): 00시대 출발 1건이 안 읽혔다 — 원인은 「2400」(실호출 08:43 `depart_raw`). 8 · 10자리 값도 받는다."""
    from app.domains.travel_ops.ports.data_sources.tago_transit import _stamp, _when
    assert _when("20261016") == ("2026-10-16", "00:00") and _when(2026101600) == ("2026-10-16", "00:00")
    assert _when("202610160030") == ("2026-10-16", "00:30") and _when("20261016003000") == ("2026-10-16", "00:30")
    assert _when(202610162400) == ("2026-10-17", "00:00"), "실호출 10-15: 고속버스 자정 출발은 「2400」으로 온다 → 다음 날 00:00"
    from app.domains.travel_ops.ports.data_sources.tago_transit import _minutes
    assert _minutes(202610162400, 202610170400) == 240
    assert _stamp("2026") is None and _stamp(None) is None


def test_a_failed_city_list_is_not_cached_as_empty():
    """검토 10-14: 도시 목록을 한 번 못 받으면 빈 목록이 굳어 열차 검색이 영영 「역 이름 못 찾음」이 되던 것."""
    import httpx

    answers = [None, _payload([{"citycode": 11, "cityname": "서울특별시"}])]

    def transport(url, params):
        payload = answers.pop(0)
        if payload is None:
            raise httpx.ConnectError("down")
        return _Response(payload)

    source = TagoTransit(service_key="k", transport=transport)
    assert source.train_cities() is None and source._cities is None, "실패는 None — 빈 목록으로 굳히지 않는다"
    assert source.train_cities() == [{"code": "11", "name": "서울특별시"}]
    assert source.train_stations("강원") == [], "목록에 없는 시 · 도는 빈 목록(못 찾음) — 장애가 아니다"


# ── booking 모듈 ─────────────────────────────────────────────
class FakeTago:
    """어댑터 대신 — 역 · 터미널 · 운행을 정해 둔 값으로. 부른 것을 남긴다."""

    def __init__(self, *, trains=None, express=None, suburbs=None, stations=None, terminals=None):
        self.calls = []
        self.trains, self.express, self.suburbs = trains or [], express, suburbs
        self.stations = stations or {"서울": [{"id": "S1", "name": "서울"}, {"id": "S2", "name": "영등포"}],
                                     "부산": [{"id": "B1", "name": "부산"}], "울산": [{"id": "U1", "name": "울산(통도사)"}]}
        self.terminals = terminals or {"서울": [{"id": "T1", "name": "서울경부"}, {"id": "T2", "name": "서울호남"}, {"id": "T3", "name": "동서울"},
                                               {"id": "T4", "name": "서울남부"}],
                                       "부산": [{"id": "T7", "name": "부산"}, {"id": "T8", "name": "부산사상"}], "강릉": [{"id": "T9", "name": "강릉"}]}

    def train_stations(self, city):
        self.calls.append(("train_stations", city))
        return self.stations.get(city, [])            # 목록에 없는 시 · 도는 빈 목록(어댑터와 같다 — None 은 장애)

    def train_schedules(self, *, dep_id, arr_id, date):
        self.calls.append(("train_schedules", dep_id, arr_id, date))
        return self.trains

    def bus_terminals(self, kind, keyword):
        self.calls.append(("bus_terminals", kind, keyword))
        return self.terminals.get(keyword, [])

    def bus_schedules(self, kind, *, dep_id, arr_id, date):
        self.calls.append(("bus_schedules", kind, dep_id, arr_id, date))
        table = self.express if kind == "express" else self.suburbs
        if table is None:
            return None
        return list(table.get((dep_id, arr_id), []))


def _train(grade, depart, arrive, price, number="101"):
    return {"mode": "train", "kind": None, "grade": grade, "number": number, "origin": "서울", "destination": "부산",
            "date": GO.isoformat(), "depart": depart, "arrive": arrive, "minutes": 160, "price": price}


def _bus(kind, dep, arr, depart, price, grade="우등"):
    return {"mode": "bus", "kind": kind, "grade": grade, "number": "R", "origin": dep, "destination": arr,
            "date": GO.isoformat(), "depart": depart, "arrive": None, "minutes": None, "price": price}


def test_train_search_turns_names_into_stations_and_sorts_by_departure():
    tago = FakeTago(trains=[_train("무궁화호", "08:00", "13:30", 28600), _train("KTX", "07:00", "09:40", 59800)])
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="train", origin="서울", destination="울산", depart_date=GO.isoformat(), adults=2)
    assert tago.calls == [("train_stations", "서울"), ("train_stations", "울산"), ("train_schedules", "S1", "U1", GO.isoformat())]
    assert got["resolved"] == {"origin": "서울", "destination": "울산(통도사)"}, "「울산」 → 역 목록에서 「울산(통도사)」(앞머리 일치)"
    assert [option["depart"] for option in got["options"]] == ["07:00", "08:00"]
    assert got["sources"]["tago"]["status"] == "ok" and got["sources"]["tago"]["count"] == 2
    assert [site["name"] for site in got["elsewhere"]] == ["코레일 승차권 예매(KTX · ITX · 무궁화)", "SRT 예매"]


def test_an_unknown_station_name_is_reported_not_guessed():
    tago = FakeTago()
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="train", origin="서울", destination="화성", depart_date=GO.isoformat())
    assert got["sources"]["tago"] == {"status": "unresolved", "count": 0, "calls": ["train_stations:서울"], "seconds": got["sources"]["tago"]["seconds"],
                                      "unresolved": ["화성"]}
    assert got["options"] == [] and all(call[0] != "train_schedules" for call in tago.calls)


def test_bus_search_asks_every_matching_terminal_pair_express_first_and_suburbs_only_when_needed():
    express = {("T1", "T7"): [_bus("express", "서울경부", "부산", "06:00", 26600)], ("T4", "T8"): [_bus("express", "서울남부", "부산사상", "07:10", 24000)]}
    tago = FakeTago(express=express, suburbs={})
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="bus", origin="서울", destination="부산", depart_date=GO.isoformat())
    pairs = [(c[2], c[3]) for c in tago.calls if c[0] == "bus_schedules"]
    assert pairs == [("T1", "T7"), ("T1", "T8")], "`[10-15]` 부산은 경부권 → 서울경부 먼저, 거기 운행이 있으면 다른 출발 터미널은 안 묻는다(호출 6 → 2)"
    assert got["terminals"] == {"origin": ["서울경부", "동서울", "서울호남"], "rule": "부산"}, "기본 순서 뒤 3곳(TERMINALS_EACH) — 동서울이 잘리지 않는다"
    assert all(c[1] == "express" for c in tago.calls if c[0] == "bus_schedules"), "고속에 결과가 있으면 시외는 안 본다"
    assert got["resolved"] == {"origin": "서울경부", "destination": "부산"}
    assert [option["depart"] for option in got["options"]] == ["06:00"]
    assert [site["name"] for site in got["elsewhere"]] == ["코버스 고속버스 예매"]

    tago = FakeTago(express=express, suburbs={}, terminals={"서울남부": [{"id": "T4", "name": "서울남부"}],
                                                            "부산": [{"id": "T7", "name": "부산"}, {"id": "T8", "name": "부산사상"}]})
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="bus", origin="서울남부", destination="부산", depart_date=GO.isoformat())
    assert [(c[2], c[3]) for c in tago.calls if c[0] == "bus_schedules"] == [("T4", "T7"), ("T4", "T8")], "터미널을 말하면 그 터미널만"
    assert got["resolved"] == {"origin": "서울남부", "destination": "부산사상"} and "terminals" not in got

    honam = FakeTago(express={("T2", "T9"): [_bus("express", "서울호남", "광주", "08:00", 20000)]}, suburbs={},
                     terminals={"서울": [{"id": "T1", "name": "서울경부"}, {"id": "T2", "name": "서울호남"}, {"id": "T3", "name": "동서울"}],
                                "광주": [{"id": "T9", "name": "광주"}]})
    got = search_transit(SimpleNamespace(transit_schedule=honam), mode="bus", origin="서울", destination="광주", depart_date=GO.isoformat())
    assert [(c[2], c[3]) for c in honam.calls if c[0] == "bus_schedules"] == [("T2", "T9")], "광주는 호남권 → 서울호남 먼저"
    empty = FakeTago(express={("T3", "T9"): [_bus("express", "동서울", "강릉", "08:00", 15000)]}, suburbs={},
                     terminals={"서울": [{"id": "T1", "name": "서울경부"}, {"id": "T3", "name": "동서울"}], "강릉": [{"id": "T9", "name": "강릉"}]})
    got = search_transit(SimpleNamespace(transit_schedule=empty), mode="bus", origin="서울", destination="강릉", depart_date=GO.isoformat())
    assert [(c[2], c[3]) for c in empty.calls if c[0] == "bus_schedules"] == [("T3", "T9")], "강원은 동서울 먼저"
    wrong = FakeTago(express={("T1", "T9"): [_bus("express", "서울경부", "강릉", "08:00", 15000)]}, suburbs={},
                     terminals={"서울": [{"id": "T1", "name": "서울경부"}, {"id": "T3", "name": "동서울"}], "강릉": [{"id": "T9", "name": "강릉"}]})
    got = search_transit(SimpleNamespace(transit_schedule=wrong), mode="bus", origin="서울", destination="강릉", depart_date=GO.isoformat())
    assert [(c[2], c[3]) for c in wrong.calls if c[0] == "bus_schedules"] == [("T3", "T9"), ("T1", "T9")], "기본 터미널에 없으면 다음 터미널 — 답이 비지 않는다"
    assert got["resolved"]["origin"] == "서울경부"
    budget = FakeTago(express={}, suburbs={("T3", "T9"): [_bus("suburbs", "동서울", "광주", "08:00", 20000)]},
                      terminals={"서울": [{"id": "T1", "name": "서울경부"}, {"id": "T2", "name": "서울호남"}, {"id": "T3", "name": "동서울"}],
                                 "광주": [{"id": "T9", "name": "광주"}, {"id": "T10", "name": "광주비아"}, {"id": "T11", "name": "광주운암"}]})
    got = search_transit(SimpleNamespace(transit_schedule=budget), mode="bus", origin="서울", destination="광주", depart_date=GO.isoformat())
    kinds = [c[1] for c in budget.calls if c[0] == "bus_schedules"]
    assert kinds.count("express") == 4 and kinds.count("suburbs") >= 1 and got["options"], \
        "`[10-15]` 예산은 종류마다(고속 4) — 고속이 전부 0편이어도 시외를 묻는다(서울→광주 실호출에서 못 물었던 것)"

    tago = FakeTago(express={}, suburbs={("T4", "T9"): [_bus("suburbs", "서울남부", "강릉", "09:00", 15000)]},
                    terminals={"서울": [{"id": "T4", "name": "서울남부"}], "강릉": [{"id": "T9", "name": "강릉"}]})
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="bus", origin="서울", destination="강릉", depart_date=GO.isoformat())
    kinds = [c[1] for c in tago.calls if c[0] == "bus_schedules"]
    assert kinds == ["express", "suburbs"] and got["options"][0]["kind"] == "suburbs"
    assert [site["name"] for site in got["elsewhere"]] == ["버스타고 시외버스 예매", "티머니 시외버스 예매"]


def test_a_broken_adapter_is_failed_not_blamed_on_the_customer():
    """검토 10-14: 어댑터가 None(장애 · 한도)이면 「역 이름을 찾지 못했다」가 아니라 `failed`."""
    class _Down(FakeTago):
        def train_stations(self, city):
            return None

        def bus_terminals(self, kind, keyword):
            return None

    got = search_transit(SimpleNamespace(transit_schedule=_Down()), mode="train", origin="서울", destination="부산", depart_date=GO.isoformat())
    assert got["sources"]["tago"]["status"] == "failed" and "unresolved" not in got["sources"]["tago"]
    got = search_transit(SimpleNamespace(transit_schedule=_Down()), mode="bus", origin="서울", destination="부산", depart_date=GO.isoformat())
    assert got["sources"]["tago"]["status"] == "failed"
    # 터미널은 찾았는데 그날 운행이 없다 — 「못 찾음」이 아니라 ok · 0편
    got = search_transit(SimpleNamespace(transit_schedule=FakeTago(express={}, suburbs={})), mode="bus", origin="서울", destination="부산", depart_date=GO.isoformat())
    assert got["sources"]["tago"]["status"] == "ok" and got["options"] == [] and "unresolved" not in got["sources"]["tago"]


def test_a_requested_bus_kind_skips_the_other_and_a_missing_source_is_failed():
    tago = FakeTago(express={}, suburbs={})
    search_transit(SimpleNamespace(transit_schedule=tago), mode="bus", origin="서울", destination="부산", depart_date=GO.isoformat(), kind="suburbs")
    assert {c[1] for c in tago.calls if c[0] in ("bus_terminals", "bus_schedules")} == {"suburbs"}
    got = search_transit(SimpleNamespace(transit_schedule=None), mode="train", origin="서울", destination="부산", depart_date=GO.isoformat())
    assert got["sources"]["tago"]["status"] == "failed" and got["options"] == []


# ── 팀(답) ─────────────────────────────────────────────
class FakeTools:
    def __init__(self, tago, values=None):
        self.calls, self.travel, self.values = [], SimpleNamespace(transit_schedule=tago), dict(values or {})

    def call(self, name, context, arguments, allowed_tools, seen, budget=None):
        if name not in allowed_tools:
            raise ToolNotAllowed(name)
        seen.add(name + repr(sorted(arguments.items())))
        self.calls.append((name, dict(arguments)))
        if name in self.values:
            return self.values[name]
        assert name == "read.booking_search_transit", f"기차 · 버스는 항공 도구를 부르지 않는다: {name}"
        return search_transit(self.travel, **arguments)


def _run(text, tago, *, values=None, capability="flight.assist", state=None):
    toolbox, case_id = FakeTools(tago, values), uuid4()
    pack = ContextPack(pack_id=uuid4(), case_id=case_id, team_id="flight", tenant_id="t", knowledge_scope=["flight"],
                       current_state={"customer_id": str(uuid4()), **(state or {})}, estimated_input_tokens=1)
    task = TeamTask(task_id=uuid4(), run_id=uuid4(), case_id=case_id, team_id="flight", capability=capability,
                    case_version=1, input_text=text, context=pack, allowed_tools=BookingTeam.manifest.allowed_tools,
                    deadline_at=datetime.now(UTC) + timedelta(seconds=90))
    return asyncio.run(BookingTeam(toolbox, None).execute(task)), toolbox


def test_a_train_sentence_is_answered_from_tago_by_time_band_with_booking_links():
    tago = FakeTago(trains=[_train("KTX", "07:00", "09:40", 59800, "101"), _train("KTX", "07:30", "10:10", 59800, "103"),
                            _train("KTX", "08:00", "10:40", 59800, "105"), _train("무궁화호", "13:00", "18:30", 28600, "1205"),
                            _train("SRT", "19:00", "21:20", 52600, "381")])
    result, tools = _run(f"{GO.month}월 {GO.day}일 서울에서 부산 가는 기차 2명", tago)
    assert result.outcome == "completed"
    assert tools.calls == [("read.booking_search_transit", {"mode": "train", "origin": "서울", "destination": "부산",
                                                            "depart_date": GO.isoformat(), "adults": 2})]
    assert [e.source_id for e in result.evidence] == ["flight.interpret_rules", "read.booking_search_transit:가는 편"]
    answer = result.answer
    assert answer.startswith(f"서울 → 부산 · {GO.isoformat()} · 성인 2명 조건으로 찾은 기차 운행입니다.")
    assert "[가는 편] 서울 → 부산 · 전체 5편 중 시간대마다 앞 운행을 보여 드립니다." in answer
    assert "[오전] KTX 101 — 서울 07:00 → 부산 09:40 (2시간 40분) · 어른 59,800원 (성인 2명 119,600원)" in answer
    assert "KTX 103" in answer and "KTX 105" not in answer, "시간대마다 앞 2편"
    assert "[오후] 무궁화호 1205" in answer and "[저녁] SRT 381" in answer
    assert "예매는 아래 주소로 — 출발 · 도착 · 날짜를 채워 엽니다(운행은 그 화면에서 고르세요):" in answer
    assert ("   · 가는 편 트립닷컴 기차(출발 · 도착 · 날짜 채움): https://kr.trip.com/trains/asia/list?tripTab=train&scheduleType=single"
            f"&hidadultnum=2&hidchildnum=0&biztype=SouthKorea&departurecountrycode=KR&arrivalcountrycode=KR&departdate={GO.isoformat()}"
            "&departhouript=07&departminuteipt=00&departurecitycode=KR0001&arrivalcitycode=KR0020") in answer, "보여 준 첫 KTX 운행(07시) 기준"
    assert ("   · 가는 편 SRT 예매(출발 · 도착 · 날짜 · 시각 채움): https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000"
            f"&dptRsStnCd=0001&arvRsStnCd=0020&dptDt={GO.isoformat().replace('-', '')}&dptTm=190000&psgNum=2&psgInfoPerPrnb1=2") in answer
    assert "그 밖의 예매 사이트(조건을 채워 주지는 않습니다" in answer
    assert "코레일 승차권 예매(KTX · ITX · 무궁화): https://www.korail.com/ticket/main" in answer
    assert "SRT 예매: https://etk.srail.kr/" not in answer, "조건을 채운 SRT 주소가 있으면 첫 화면은 안 보여 준다"
    assert "어른 1인 기준 조회 시점 참고값" in answer
    assert result.decisions[0]["booking_links"] == ["srt", "trip"]
    assert result.decisions[0]["mode"] == "train" and result.decisions[0]["tago_calls"] == 3


def test_a_time_band_and_a_grade_narrow_the_trains_and_a_return_day_adds_a_second_search():
    tago = FakeTago(trains=[_train("KTX", "07:00", "09:40", 59800, "101"), _train("무궁화호", "07:20", "12:50", 28600, "1201"),
                            _train("KTX", "13:00", "15:40", 59800, "121")])
    result, tools = _run(f"{GO.month}월 {GO.day}일 서울에서 부산 KTX 2명 오전 출발, {BACK.day}일에 오는", tago)
    assert [a["depart_date"] for _, a in tools.calls] == [GO.isoformat(), BACK.isoformat()]
    assert [(a["origin"], a["destination"]) for _, a in tools.calls] == [("서울", "부산"), ("부산", "서울")]
    answer = result.answer
    assert "[오는 편]" in answer
    going = answer.split("[오는 편]")[0]                       # 오는 편은 가짜 어댑터가 같은 열차를 돌려줘 시간대 제한 없이 다 보인다
    assert "[가는 편] 서울 → 부산 · 전체 2편 중 오전 출발 1편 보여 드립니다." in going, "KTX 만 · 오전만"
    assert "KTX 101" in going and "무궁화호 1201" not in going and "KTX 121" not in going


def test_an_unknown_station_or_no_schedule_is_said_plainly_and_a_dead_source_is_unknown():
    result, _ = _run(f"{GO.month}월 {GO.day}일 서울에서 강릉 가는 기차 2명", FakeTago())   # 사전엔 있지만 공급자 목록(가짜)에 없는 역
    assert "「강릉」을(를) 역 이름으로 찾지 못했습니다. 역 이름을 정확히 알려 주세요." in result.answer
    found, missing = _rules(f"{GO.month}월 {GO.day}일 서울에서 화성 가는 기차 2명")             # 사전에 없는 곳은 해석에서 비워 되묻는다
    assert found.destination is None and missing == ["destination"]
    result, _ = _run(f"{GO.month}월 {GO.day}일 서울에서 부산 가는 기차 2명", FakeTago(trains=[]))
    assert "그날 운행이 없습니다(공급자 결과 기준)" in result.answer
    result, _ = _run(f"{GO.month}월 {GO.day}일 서울에서 부산 가는 기차 2명", None)
    assert result.outcome != "completed" or "모름" in (result.answer or ""), "소스가 없으면 모름(지어내지 않음)"


class DatedTago(FakeTago):
    """버스 운행을 날짜별로 — 공급자가 가까운 날만 주는 것(팀장 점검 LB-03)을 흉내 낸다."""

    def __init__(self, by_date, **kwargs):
        super().__init__(express={}, suburbs={}, **kwargs)
        self.by_date = by_date

    def bus_schedules(self, kind, *, dep_id, arr_id, date):
        self.calls.append(("bus_schedules", kind, dep_id, arr_id, date))
        return list(self.by_date.get((kind, dep_id, arr_id, date), []))


def test_a_far_bus_date_with_no_runs_is_not_yet_available_not_no_service():
    """팀장 점검 LB-03(10-15): 먼 날짜 버스 0편 — 오늘은 같은 구간에 운행이 있으면 「아직 안 줌」, 오늘도 없으면 「운행 없음」."""
    today = TODAY.isoformat()
    far = (TODAY + timedelta(days=5)).isoformat()
    tago = DatedTago({("express", "T1", "T7", today): [_bus("express", "서울경부", "부산", "06:00", 26600)]})
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="bus", origin="서울", destination="부산", depart_date=far, adults=1)
    assert got["sources"]["tago"]["status"] == "not_yet" and got["options"] == []
    assert got["sources"]["tago"]["calls"][-1] == "bus_schedules:express:서울경부→부산:today", "오늘 날짜로 한 번 더 물어 가른다"
    result, _ = _run(f"{far[5:7].lstrip('0')}월 {far[8:10].lstrip('0')}일 서울에서 부산 가는 고속버스 1명", tago)
    assert "그날 버스 운행은 아직 확인할 수 없습니다 — 공급자가 가까운 날(보통 출발 1~2일 전)부터 운행표를 줍니다." in result.answer
    nothing = DatedTago({})
    got = search_transit(SimpleNamespace(transit_schedule=nothing), mode="bus", origin="서울", destination="부산", depart_date=far, adults=1)
    assert got["sources"]["tago"]["status"] == "ok" and got["options"] == [], "오늘도 없으면 그 구간 운행이 없는 것"
    soon = DatedTago({})
    got = search_transit(SimpleNamespace(transit_schedule=soon), mode="bus", origin="서울", destination="부산", depart_date=today, adults=1)
    assert not any(call.endswith(":today") for call in got["sources"]["tago"]["calls"]), "가까운 날은 되묻지 않는다(호출 아끼기)"


def test_a_bus_sentence_shows_the_terminal_used_and_the_bus_links():
    express = {("T1", "T7"): [_bus("express", "서울경부", "부산", "06:00", 26600)]}
    result, tools = _run(f"{GO.month}월 {GO.day}일 서울에서 부산 가는 고속버스 3명 새벽", FakeTago(express=express, suburbs={}))
    assert tools.calls[0][1]["kind"] == "express"
    assert "[가는 편] 서울경부 → 부산 · 전체 1편 중 새벽 출발 1편 보여 드립니다." in result.answer
    assert "  서울은(는) 터미널이 여럿이라 서울경부 출발 기준이에요 — 동서울 · 서울호남 출발을 원하시면 터미널 이름으로 말씀해 주세요." in result.answer
    assert "[새벽] 고속우등 — 서울경부 06:00 → 부산 ? (소요 모름) · 어른 26,600원 (성인 3명 79,800원)" in result.answer
    assert "예매는 아래 사이트에서 직접(조건을 채워 주지는 않습니다" in result.answer, "고속버스는 조건을 채워 여는 주소가 없다(코버스 POST 전용)"
    assert "코버스 고속버스 예매: https://www.kobus.co.kr/" in result.answer
    assert result.decisions[0]["booking_links"] == []


# ── 일정 기차 · 버스 항목 — `flight.itinerary` (`[2026-10-14 2차]`, 항공 항목과 같은 모양) ─────────
TRIP_ID, RUN_ID = "11111111-1111-4111-8111-111111111111", "88888888-8888-4888-8888-888888888888"


def _plan(kind="train", detail=None, booking_id=None, starts="09:00", ends="11:40"):
    run = {"item_id": RUN_ID, "seq": 1, "kind": kind, "title": "서울 → 부산 KTX", "place_id": None,
           "starts_at": f"{GO.isoformat()}T{starts}:00+09:00", "ends_at": f"{GO.isoformat()}T{ends}:00+09:00", "locked": False,
           "booking_id": booking_id, "replaces_item_id": None, "place": None,
           "detail": {"origin": "서울", "destination": "부산", "grade": "KTX", "number": "11", "per_person": 59800, **(detail or {})}}
    return {"trip": {"trip_id": TRIP_ID, "version": 5, "party_size": 2, "customer_id": "c"}, "items": [run]}


RUN_STATE = {"subject_ref": {"kind": "trip", "id": TRIP_ID, "part_id": RUN_ID, "base_version": 5},
             "interpretation": {"report": {"type": "change"}}, "request_id": "r-train-1"}
TRAINS = [_train("KTX", "07:00", "09:40", 59800, "101"), _train("KTX", "08:30", "11:10", 59800, "107"),
          _train("KTX", "09:00", "11:40", 59800, "11"),            # 원래 타는 운행 — 빠져야 한다
          _train("무궁화호", "09:20", "14:50", 28600, "1203"), _train("KTX", "10:30", "13:10", 59800, "117"),
          _train("SRT", "11:00", "13:20", 52600, "321"), _train("KTX", "19:00", "21:40", 59800, "161")]


def _runchange(plan, tago=None, text="이 기차 다른 걸로 바꿔줘", state=None):
    return _run(text, tago or FakeTago(trains=list(TRAINS)), values={"read.itinerary": plan, "read.offers_shown": {"offers": []}},
                capability="flight.itinerary", state=state or RUN_STATE)


def test_change_on_a_train_item_offers_same_grade_runs_near_the_original_departure_without_touching_the_plan():
    result, tools = _runchange(_plan())
    assert [name for name, _ in tools.calls] == ["read.itinerary", "read.booking_search_transit", "read.offers_shown"]
    assert tools.calls[1][1] == {"mode": "train", "origin": "서울", "destination": "부산", "depart_date": GO.isoformat(), "adults": 2}
    assert result.answer == ("서울 → 부산 KTX 대신 탈 수 있는 기차 운행이에요 (서울 → 부산) — 1) KTX 107 08:30→11:10(1인 59,800원) · "
                             "2) KTX 117 10:30→13:10(1인 59,800원) · 3) KTX 101 07:00→09:40(1인 59,800원). "
                             "요금은 어른 1인 기준 조회 시점 값이에요. 고르시면 바꿀게요. 고르지 않으시면 원래 일정을 그대로 둡니다."), \
        "같은 등급(KTX) 먼저 · 원래 09:00 앞뒤 3시간을 가까운 순 · 원래 운행(KTX 11 09:00)은 뺌"
    (proposal,) = result.action_proposals
    assert proposal.action_type == "itinerary.apply" and proposal.arguments["reason"] == "requested_options"
    assert "replacements" not in proposal.arguments, "후보를 보여 줄 때 일정 판을 올리지 않는다"
    ask = proposal.arguments["ask_options"]
    assert ask["item_id"] == RUN_ID and ask["shown_key"] == f"train:서울-부산:{GO.isoformat()}"
    first = ask["options"][0]
    assert {"key", "name", "place_id", "starts_at", "ends_at", "walk_min"} <= set(first), "식당 · 활동의 「다른 안」 칸"
    assert first["starts_at"] == f"{GO.isoformat()}T08:30:00+09:00" and first["ends_at"] == f"{GO.isoformat()}T11:10:00+09:00", "국내 — 도착 시각도"
    assert first["per_person"] == 59800 and first["best"] == 119600
    assert first["link"].startswith("https://kr.trip.com/trains/asia/list?") and "departhouript=08" in first["link"], "운행 시각이 채워진 주소"
    assert first["link_kind"] == "route_search" and first["link_seller"] == "트립닷컴 기차(출발 · 도착 · 날짜 채움)"
    assert proposal.arguments["summary"]["train_options"] == [o["key"] for o in ask["options"]]
    assert proposal.arguments["summary"]["near_original"] == 3


def test_cheaper_on_a_train_item_keeps_only_runs_below_the_current_fare_and_drops_the_grade_rule():
    state = {**RUN_STATE, "interpretation": {"report": {"type": "change", "cheaper": True}}}
    result, _ = _runchange(_plan(), state=state)
    assert "1) 무궁화호 1203 09:20→14:50(1인 28,600원) · 2) SRT 321 11:00→13:20(1인 52,600원)" in result.answer, "등급 규칙 없이 원래 출발에 가까운 순"
    assert "지금 운행(1인 59,800원)보다 싼 것만 골랐어요." in result.answer


def test_cheaper_without_a_known_fare_sorts_every_run_by_price_before_cutting():
    """검토 10-14: 전에는 출발 시각순 3편을 자른 뒤 요금순으로 돌려 더 싼 SRT 11:00 이 빠졌다."""
    state = {**RUN_STATE, "interpretation": {"report": {"type": "change", "cheaper": True}}}
    plan = _plan(detail={"per_person": None})
    result, _ = _runchange(plan, state=state)
    assert "1) 무궁화호 1203 09:20→14:50(1인 28,600원) · 2) SRT 321 11:00→13:20(1인 52,600원) · 3) KTX 101 07:00→09:40(1인 59,800원)" in result.answer
    assert "지금 운행 요금을 몰라 1인 요금이 낮은 순으로 골랐어요." in result.answer


def test_the_bus_the_customer_already_rides_is_not_offered_again_and_recheck_matches_the_terminal():
    """검토 10-14: 버스 항목엔 노선 id 가 없어 지금 타는 운행이 후보로 다시 나왔고, 재확인이 다른 터미널의 같은 시각 운행을 「같다」고 봤다."""
    express = {("T1", "T7"): [_bus("express", "서울경부", "부산", "09:00", 43300), _bus("express", "서울경부", "부산", "10:00", 43300)],
               ("T2", "T7"): [_bus("express", "서울호남", "부산", "10:00", 39000)]}
    plan = _plan(kind="bus", detail={"bus_kind": "express", "grade": "우등", "number": None, "per_person": 43300}, starts="09:00", ends="13:00")
    plan["items"][0]["title"] = "서울 → 부산 고속버스"
    terminals = {"서울": [{"id": "T1", "name": "서울경부"}, {"id": "T2", "name": "서울호남"}], "서울호남": [{"id": "T2", "name": "서울호남"}],
                 "부산": [{"id": "T7", "name": "부산"}]}
    first, _ = _runchange(plan, tago=FakeTago(express=express, suburbs={}, terminals=terminals))
    options = first.action_proposals[0].arguments["ask_options"]["options"]
    assert [(o["origin"], o["depart"]) for o in options] == [("서울경부", "10:00")], "09:00 우등(지금 타는 것)은 뺀다 · `[10-15]` 「서울」은 기본 터미널(서울경부)만"
    plan["items"][0]["detail"] = {**plan["items"][0]["detail"], "origin": "서울호남"}      # 항목이 서울호남 출발이면 그 터미널로 찾는다
    first, _ = _runchange(plan, tago=FakeTago(express=express, suburbs={}, terminals=terminals))
    options = first.action_proposals[0].arguments["ask_options"]["options"]
    assert [(o["origin"], o["depart"]) for o in options] == [("서울호남", "10:00")]
    plan["items"][0]["detail"] = {**plan["items"][0]["detail"], "alternates": options, "other_options": [o["name"] for o in options]}
    key = options[0]["key"]                                           # 서울호남 10:00 39,000원
    state = {**RUN_STATE, "subject_ref": {**RUN_STATE["subject_ref"], "request": {"type": "change", "choice": key}}}
    tago = FakeTago(express=express, suburbs={}, terminals=terminals)
    result, _ = _runchange(plan, tago=tago, text="1번", state=state)
    assert ("bus_terminals", "express", "서울호남") in tago.calls, "재확인은 고른 운행의 터미널 이름으로 찾는다(10-15)"
    new = result.action_proposals[0].arguments["replacements"][0]["item"]
    assert new["detail"]["chosen"]["origin"] == "서울호남" and new["detail"]["chosen"]["per_person"] == 39000, "같은 시각 서울경부 운행과 섞지 않는다"
    assert result.answer.endswith(" 지금 가격도 1인 39,000원 그대로예요.")


def test_choosing_a_train_run_moves_both_times_and_rechecks_the_fare():
    plan = _plan()
    first, _ = _runchange(plan)
    options = first.action_proposals[0].arguments["ask_options"]["options"]
    plan["items"][0]["detail"] = {**plan["items"][0]["detail"], "alternates": options, "other_options": [o["name"] for o in options]}
    key = options[0]["key"]
    state = {**RUN_STATE, "subject_ref": {**RUN_STATE["subject_ref"], "request": {"type": "change", "choice": key}}}
    result, tools = _runchange(plan, text="1번", state=state)
    assert [name for name, _ in tools.calls] == ["read.itinerary", "read.booking_search_transit"], "고르면 운행 재확인 한 번"
    assert result.answer.startswith("요청하신 대로 서울 → 부산 KTX 대신 KTX 107 08:30→11:10(으)로 바꿨습니다. 아직 예약 전이에요 — 이 편은 검색 결과 페이지만 "
                                    "있어요. 그 화면에서 KTX 107 08:30→11:10 편을 골라 예약해 주세요: https://kr.trip.com/trains/asia/list?")
    assert "departhouript=08&departminuteipt=00&departurecitycode=KR0001&arrivalcitycode=KR0020. 지금 가격도 1인 59,800원 그대로예요." in result.answer
    new = result.action_proposals[0].arguments["replacements"][0]["item"]
    assert new["starts_at"] == f"{GO.isoformat()}T08:30:00+09:00" and new["ends_at"] == f"{GO.isoformat()}T11:10:00+09:00"
    assert new["detail"]["chosen"]["key"] == key and new["detail"]["booked"] is False
    assert new["detail"]["alternates"][-1]["name"] == "서울 → 부산 KTX", "원래 운행은 「다른 안」 끝으로"

    dearer = FakeTago(trains=[{**t, "price": 61000} if t["number"] == "107" else t for t in TRAINS])
    changed, _ = _runchange(plan, tago=dearer, text="1번", state=state)
    assert changed.answer.endswith(" 보여 드릴 때 1인 59,800원이던 가격이 지금 1인 61,000원이에요.")
    assert changed.action_proposals[0].arguments["summary"]["price_check"] == {"status": "changed", "before": 59800, "now": 61000}

    gone, _ = _runchange(plan, tago=FakeTago(trains=[t for t in TRAINS if t["number"] != "107"]), text="1번", state=state)
    assert gone.answer.startswith("KTX 107 08:30→11:10 편을 다시 찾아보니 지금은 나오지 않아요") and not gone.action_proposals


def test_a_booked_train_is_kept_and_a_bus_item_asks_tago_with_its_terminal_kind():
    kept, tools = _runchange(_plan(booking_id="77777777-7777-4777-8777-777777777777"))
    assert kept.answer.startswith("서울 → 부산 KTX은(는) 이미 예약하신 기차라 바꾸지 않았어요") and len(tools.calls) == 1   # 「기차라」(받침 없음)
    express = {("T1", "T7"): [_bus("express", "서울경부", "부산", "08:00", 43300), _bus("express", "서울경부", "부산", "10:00", 45000, "프리미엄")]}
    plan = _plan(kind="bus", detail={"bus_kind": "express", "grade": "우등", "number": None, "per_person": 43300}, starts="09:00", ends="13:00")
    plan["items"][0]["title"] = "서울 → 부산 고속버스"
    result, tools = _runchange(plan, tago=FakeTago(express=express, suburbs={}))
    assert tools.calls[1][1] == {"mode": "bus", "origin": "서울", "destination": "부산", "depart_date": GO.isoformat(), "adults": 2, "kind": "express"}
    assert result.answer.startswith("서울 → 부산 고속버스 대신 탈 수 있는 버스 운행이에요 (서울경부 → 부산) — 1) 우등 08:00→?(1인 43,300원) · "
                                    "2) 프리미엄 10:00→?(1인 45,000원).")
    assert result.action_proposals[0].arguments["ask_options"]["options"][0]["link"] == "https://www.kobus.co.kr/"


# ── `[2026-10-15]` 버스 정리 — 서울 ↔ 인천공항 버스(공항리무진) · 「서울」 터미널 후보 ───────────────
def test_a_seoul_airport_bus_is_answered_as_the_airport_limousine_without_calling_tago():
    """10-15 실호출: 서울 ↔ 인천공항은 고속 · 시외 운행이 0편인데 13회 불렀다 — 이제 부르지 않고 공항리무진 안내."""
    tago = FakeTago()
    got = search_transit(SimpleNamespace(transit_schedule=tago), mode="bus", origin="서울", destination="인천공항", depart_date=GO.isoformat())
    assert tago.calls == [] and got["options"] == []
    assert got["sources"]["tago"]["status"] == "airport_bus" and got["sources"]["tago"]["calls"] == []
    assert [site["url"] for site in got["elsewhere"]] == ["https://www.airportlimousine.co.kr/sub/sub01.php"]
    back = search_transit(SimpleNamespace(transit_schedule=tago), mode="bus", origin="인천공항 2터미널", destination="서울", depart_date=GO.isoformat())
    assert back["sources"]["tago"]["status"] == "airport_bus"
    other = FakeTago(terminals={"강릉": [{"id": "G1", "name": "강릉"}], "인천공항": [{"id": "A1", "name": "인천공항T1"}]}, express={}, suburbs={})
    search_transit(SimpleNamespace(transit_schedule=other), mode="bus", origin="강릉", destination="인천공항", depart_date=GO.isoformat())
    assert other.calls[0] == ("bus_terminals", "express", "강릉"), "지방 ↔ 인천공항 시외는 그대로 묻는다"


def test_a_seoul_terminal_with_another_place_in_brackets_is_not_a_seoul_departure():
    """10-15 실호출: 「동서울(홍천)」이 서울 출발 후보 셋 중 하나를 차지했다. 「센트럴시티(서울)」은 남긴다."""
    from app.domains.travel_ops.components.booking.offers.transit import _terminals
    tago = FakeTago(terminals={"서울": [{"id": "T3", "name": "동서울"}, {"id": "T5", "name": "동서울(홍천)"},
                                       {"id": "T6", "name": "센트럴시티(서울)"}, {"id": "T4", "name": "서울남부"}],
                               "부산": [{"id": "T7", "name": "부산동부(노포)"}]})
    names = [t["name"] for t in _terminals(tago, "suburbs", "서울", [])]
    assert names == ["서울남부", "동서울", "센트럴시티(서울)"], "「서울」로 시작하는 것 먼저(원래 규칙) · 홍천만 빠짐"
    assert [t["name"] for t in _terminals(tago, "suburbs", "부산", [])] == ["부산동부(노포)"], "도시 이름 「서울」에만 — 괄호가 동네인 곳은 그대로"


def test_an_airport_bus_sentence_explains_how_to_ride_and_points_to_the_timetable_page():
    tago = FakeTago()
    result, _ = _run(f"{GO.month}월 {GO.day}일 서울에서 인천공항 공항버스 2명", tago)
    answer = result.answer
    assert tago.calls == [] and result.decisions[0]["tago_calls"] == 0 and result.decisions[0]["via"] == ["airport_bus_guide"]
    assert "[가는 편] 서울 ↔ 인천공항 버스는 공항리무진(공항버스)이라 고속 · 시외 운행표에 없어요." in answer
    assert "시간표가 필요하시면 「공항철도」로 물어봐 주세요" in answer
    assert "노선 · 시간표는 아래에서 확인해 주세요:" in answer and "공항리무진 노선 · 시간표: https://www.airportlimousine.co.kr/sub/sub01.php" in answer
    assert "예매는 아래" not in answer and "어른 1인 기준 조회 시점 참고값" not in answer


# ── `[2026-10-15]` 상품형 — 서울 시티투어버스(공급자 호출 0, 공식 페이지 글 + 코스 링크) ───────────────
@pytest.mark.parametrize("text,bands", [
    ("11월 7일 시티투어버스 2명 저녁", ["evening"]),
    ("내일 서울 타이거버스 타고 싶어 1명", None),
    ("노랑풍선 시티투어 다음 주 토요일 3명 오전", ["morning"]),
])
def test_the_rules_read_a_city_tour_bus_as_a_tour_not_a_bus(text, bands):
    found, missing = _rules(text)
    assert (found.mode, found.kind, found.origin, found.destination, found.depart_times, missing) == ("tour", "city_tour", None, None, bands, [])


def test_a_city_tour_without_a_date_is_asked_back():
    found, missing = _rules("시티투어버스 타고 싶어 2명")
    assert found.mode == "tour" and missing == ["depart_date"]


def test_tour_courses_follow_the_time_band_and_flag_the_closed_weekday():
    from datetime import date as _date

    from app.domains.travel_ops.components.booking.offers.tours import search_tours
    monday = _date(2026, 10, 19)
    night = search_tours(kind="city_tour", day=monday, bands=["evening"])
    assert [course["id"] for course in night["options"]] == ["tiger_night", "yellow_night", "yellow_hangang"], "시간대를 모르는 코스(한강잠실)는 남긴다"
    assert night["options"][0]["closed_today"] is True and night["options"][1]["closed_today"] is False, "타이거버스만 월요일 휴무"
    assert night["calls"] == 0 and night["weekday"] == "월"
    day = search_tours(kind="city_tour", day=_date(2026, 10, 20), bands=None)
    assert len(day["options"]) == 5 and not any(course["closed_today"] for course in day["options"])


def test_a_city_tour_sentence_lists_courses_with_links_and_calls_no_supplier():
    tago = FakeTago()
    result, tools = _run(f"{GO.month}월 {GO.day}일 시티투어버스 2명 저녁", tago)
    answer = result.answer
    assert tools.calls == [] and tago.calls == [], "공급자 호출 0 — 타이거버스 /api/ 는 robots.txt 가 막는다"
    assert answer.startswith(f"{GO.isoformat()}(") and "· 성인 2명 — 서울 시티투어버스 상품이에요." in answer
    assert "  1. [야간] 서울시티투어버스(타이거버스) · 야간 한강·남산 코스 — 광화문 매표소 출발 · 19:00 하루 한 번(5~8월 19:30)" in answer
    assert "https://www.seoulcitybus.com/ko/tours/2" in answer and "https://www.seoulcitytourbus.co.kr/tour-course/night-traditional-culture" in answer
    assert "https://www.seoulcitybus.com/ko/tours/1" not in answer, "저녁이라 주간 코스는 뺀다"
    assert "그날 운행 · 남은 좌석 · 요금은 링크한 페이지에서" in answer and "원" not in answer.replace("1인", ""), "요금을 지어내지 않는다"
    decision = result.decisions[0]
    assert decision["mode"] == "tour" and decision["supplier_calls"] == 0 and decision["shown"][0] == "tiger_night"
    assert [e.source_id for e in result.evidence][-1] == "booking.tours_catalog"


# ── `[2026-10-15]` 상품형 — 한강 유람선(이랜드크루즈, maketicket 상품 링크 · 이름에 적힌 시각) ───────────────
@pytest.mark.parametrize("text,origin,bands", [
    ("11월 7일 잠실 한강 유람선 2명 저녁", "잠실", ["evening"]),
    ("내일 여의도 크루즈 타고 싶어 1명", "여의도", None),
    ("다음 주 토요일 한강 유람선 3명", None, None),
])
def test_the_rules_read_a_han_river_cruise_with_its_pier(text, origin, bands):
    found, missing = _rules(text)
    assert (found.mode, found.kind, found.origin, found.destination, found.depart_times, missing) == ("tour", "cruise", origin, None, bands, [])


def test_cruise_products_follow_the_pier_and_the_days_they_sail():
    from datetime import date as _date

    from app.domains.travel_ops.components.booking.offers.tours import search_tours
    friday, saturday = _date(2026, 10, 16), _date(2026, 11, 7)
    jamsil = search_tours(kind="cruise", day=friday, place="잠실")
    assert {course["start"] for course in jamsil["options"]} == {"잠실 선착장"} and all(course["closed_today"] for course in jamsil["options"]), "잠실은 주말만"
    fire = {course["id"]: course["closed_today"] for course in search_tours(kind="cruise", day=friday, bands=["evening"])["options"]}
    assert fire["cruise_GD24910"] is False, "불꽃: 10월은 금요일"
    fire_nov = {course["id"]: course["closed_today"] for course in search_tours(kind="cruise", day=saturday)["options"]}
    assert fire_nov["cruise_GD24910"] is False and fire_nov["cruise_GD24911"] is False, "불꽃: 11월은 토요일 · 잠실 주말"
    assert search_tours(kind="cruise", day=_date(2026, 11, 6))["options"][5]["closed_today"] is True, "11월 금요일은 불꽃 안 다님"
    assert all(course["url"].startswith("https://www.maketicket.co.kr/ticket/GD") for course in jamsil["options"])


def test_a_cruise_sentence_lists_products_with_links_and_marks_days_they_do_not_sail():
    tago = FakeTago()
    friday = TODAY + timedelta(days=(4 - TODAY.weekday()) % 7 or 7)
    result, tools = _run(f"{friday.month}월 {friday.day}일 잠실 한강 유람선 2명 저녁", tago)
    answer = result.answer
    assert tools.calls == [] and result.decisions[0]["kind"] == "cruise" and result.decisions[0]["supplier_calls"] == 0
    assert "· 성인 2명 — 잠실 한강 유람선 상품이에요." in answer
    assert "[야간] 이랜드크루즈 · 잠실 선셋크루즈 — 잠실 선착장 출발 · 주말 18:30" in answer
    assert "※ 그날은 안 다닐 수 있어요 — 주말에만 운항: https://www.maketicket.co.kr/ticket/GD24912" in answer
    assert "여의도" not in answer and set(result.decisions[0]["closed_today"]) == {"cruise_GD24912", "cruise_GD24913", "cruise_GD24914"}


# ── `[2026-10-15]` 상품형 — 렌터카(예약 화면 링크) · 한강버스(개인 예약 없음 · 단체 시범) ───────────────
@pytest.mark.parametrize("text,kind,adults", [
    ("11월 7일부터 9일까지 렌터카 2명", "rental", 2),
    ("내일 서울에서 차 빌려서 다닐래 3명", "rental", 3),
    ("내일 한강버스 타고 싶어 2명", "river_bus", 2),
    ("11월 4일 한강 버스 단체 15명", "river_bus", 15),
])
def test_the_rules_read_rental_cars_and_the_han_river_bus_as_products(text, kind, adults):
    found, missing = _rules(text)
    assert (found.mode, found.kind, found.adults, missing) == ("tour", kind, adults, [])


def test_more_than_nine_people_is_still_refused_for_flights_trains_and_buses():
    from app.domains.travel_ops.instances.booking.interpret import InterpretationInvalid
    with pytest.raises(InterpretationInvalid):
        parse({"task": "search", "mode": "train", "origin": "서울", "destination": "부산", "adults": 15})
    assert parse({"task": "search", "mode": "tour", "kind": "river_bus", "adults": 15}).adults == 15


def test_the_han_river_bus_needs_no_booking_and_groups_get_the_pilot_rules():
    from datetime import date as _date

    from app.domains.travel_ops.components.booking.offers.tours import search_tours
    alone = search_tours(kind="river_bus", day=_date(2026, 10, 21), adults=2)
    assert alone["notes"] == ["한강버스는 대중교통이라 예약할 필요가 없어요(누리집 FAQ) — 선착장에서 바로 타시면 됩니다."]
    group = search_tours(kind="river_bus", day=_date(2026, 10, 21), adults=15)["notes"][0]
    assert group.startswith("10명 이상 단체는 평일 마곡 · 잠실 선착장에서 네이버 지도") and "그날은 대상이 아니에요" not in group
    assert "그날은 대상이 아니에요" in search_tours(kind="river_bus", day=_date(2026, 10, 24), adults=15)["notes"][0], "토요일"
    assert "그날은 대상이 아니에요" in search_tours(kind="river_bus", day=_date(2026, 10, 19), adults=15)["notes"][0], "시작일 전"
    assert "나눠 예약" in search_tours(kind="river_bus", day=_date(2026, 10, 21), adults=40)["notes"][0]
    assert search_tours(kind="rental", day=_date(2026, 10, 21))["notes"] == []


def test_a_rental_sentence_gives_the_booking_pages_with_the_dates_said():
    result, tools = _run(f"{GO.month}월 {GO.day}일부터 {BACK.month}월 {BACK.day}일까지 렌터카 2명", FakeTago())
    answer = result.answer
    assert tools.calls == [] and result.decisions[0]["kind"] == "rental"
    assert f"{GO.isoformat()}(" in answer and f"~{BACK.isoformat()} · 성인 2명 — 렌터카 상품이에요." in answer
    assert "롯데렌터카 · 내륙 단기 렌터카 예약 — 대여 · 반납 일시와 지점은 그 화면에서 고르세요: https://www.lotterentacar.net/" in answer
    assert "https://rent.skdirect.co.kr/short-rent/location-list?branchOrder=inlandFirst" in answer
