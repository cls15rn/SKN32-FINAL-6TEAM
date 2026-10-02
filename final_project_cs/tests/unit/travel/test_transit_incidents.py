# -*- coding: utf-8 -*-
"""경로 사건 소스 — 지하철 알림 · TOPIS 공지 · 합치기(이동 문제목록 #36·#39 · 2026-10-01). 네트워크 없이 돈다.

잠그는 것:
  ① 못 보는 대상은 unsupported() 로 — 합친 뒤에도 「사건 없음」으로 읽히지 않는다(팀장 9/30 요청)
  ② 지하철 알림을 못 읽으면 None(감시 루프가 치명으로 남김) · TOPIS(비공식)를 못 읽으면 버스 대상만 「확인 못 함」
  ③ 지하철 종류는 제목으로(R0~R8) — 끝 알림이 사건을 닫는다 · 계획 무정차는 시간대로
  ④ TOPIS 정류장 무정차는 정류장 번호 → 그 정류장을 지나는 노선으로, 우회는 노선 번호로 · 시간대 밖은 사건 아님
표본 문구는 공개 공지(서울교통공사 지하철알림정보 · TOPIS 공지) 그대로 줄였다.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from app.infrastructure.travel.route_events_chain import CombinedRouteEvents
from app.infrastructure.travel.seoulmetro_alert import SeoulMetroAlerts, items_of, read_items
from app.infrastructure.travel.topis_notice import (TopisNotices, read_rows, stops_of, windows_of)

T = lambda s: datetime.fromisoformat(f"2026-09-22T{s}:00+09:00")  # noqa: E731


def _item(at, title, line="2호선", codes="0241", **kw):
    return {"noftOcrnDt": f"2026-09-22T{at}", "noftTtl": title, "lineNmLst": line, "stnSctnCdLst": codes,
            "noftSeCd": kw.pop("se", "6"), "nonstopYn": "Y", "upbdnbSe": kw.pop("dir", "내선, 외선"),
            "xcseSitnBgngDt": kw.pop("begin", None), "xcseSitnEndDt": kw.pop("end", None), "noftCn": "", **kw}


# 이대역 9/22 저녁(표본) — 연기 무정차 → 정상운행 → 안전문 장애 무정차 → 운행재개
EWHA = [_item("20:27:00", "2호선 이대역 승강장 연기 발생으로 열차 무정차 통과중"),
        _item("20:27:01", "2호선 이대역 승강장 연기 발생으로 열차 무정차 통과중", codes=""),   # 채널 복사(R0)
        _item("21:13:33", "2호선 이대역 열차 정상운행"),
        _item("21:22:23", "2호선 이대역 안전문장애로 열차 무정차 통과중"),
        _item("22:19:15", "2호선 이대역 열차 운행재개"),
        _item("08:00:00", "4호선 혜화역 시위 사전 안내", line="4호선", codes=""),
        _item("09:00:00", "고객센터 운영 안내", codes=""),
        _item("10:00:00", "7호선 강남구청역 차량고장으로 열차 지연", line="7호선", codes="")]

STATIONS = {"0241": ("2호선:이대", True), "0150": ("1호선:서울역", True), "0426": ("4호선:서울역", True),
            "1003": ("1호선:용산", False),                    # 코레일 구간 — 알림이 안 올 수 있다
            "0239": ("2호선:시청", True), "0202": ("2호선:충정로", True)}


def _payload(items, total=None):
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
                         "body": {"totalCount": len(items) if total is None else total, "items": {"item": items}}}}


def _metro(payload=None, *, status=200, raise_=False, now="21:30", table=STATIONS):
    calls = []

    def transport(url, params):
        calls.append(params)
        if raise_:
            raise httpx.ConnectTimeout("down")
        return httpx.Response(status, json=payload)
    src = SeoulMetroAlerts(service_key="k", stations=lambda: table, now=lambda: T(now), transport=transport)
    return src, calls


# ── 지하철 알림 ─────────────────────────────────────────────────────
def test_metro_reads_title_kinds_and_end_alerts_close():
    r = read_items(EWHA)
    assert [(i.kind, i.closed) for i in r.incidents] == [("skip", True), ("skip", True)], "끝 알림이 각 사건을 닫는다"
    assert r.incidents[0].ends_at == T("21:13:33")
    assert r.not_conditions == {"protest_notice": 1, "irrelevant": 1, "delay": 1}, "버리지 않고 센다"


def test_metro_affecting_now_and_after_end():
    src, calls = _metro(_payload(EWHA), now="21:30")
    got = src.affecting(["2호선:이대", "2호선:시청", "버스:2016"])
    assert set(got) == {"2호선:이대"} and got["2호선:이대"]["effect"] == "skip_station"
    assert got["2호선:이대"]["source"] == "seoulmetro_alert" and got["2호선:이대"]["confirmed_at"]
    assert calls[0]["srchStartNoftOcrnYmd"] == "20260921" and calls[0]["dataType"] == "JSON"
    after, _ = _metro(_payload(EWHA), now="22:30")
    assert after.affecting(["2호선:이대"]) == {}, "운행재개 뒤에는 사건이 아니다"


def test_metro_unsupported_lines_and_korail_stations():
    src, _ = _metro(_payload([]))
    targets = ["2호선:이대", "9호선:여의도", "공항철도:서울역", "1호선:용산", "2호선:없는역", "버스:2016", "도로:세종대로"]
    assert src.unsupported(targets) == ["9호선:여의도", "공항철도:서울역", "1호선:용산", "2호선:없는역"]


def test_metro_without_station_table_is_all_unsupported_not_no_event():
    src, calls = _metro(_payload(EWHA), table=None)
    assert src.affecting(["2호선:이대"]) == {} and calls == [], "표가 없으면 부르지도 않는다"
    assert src.unsupported(["2호선:이대"]) == ["2호선:이대"], "사건 없음이 아니라 「확인 못 함」"


@pytest.mark.parametrize("kw", [{"status": 500}, {"raise_": True}])
def test_metro_failure_is_none(kw):
    src, _ = _metro(_payload(EWHA), **kw)
    assert src.affecting(["2호선:이대"]) is None


def test_metro_body_error_and_truncation_are_none():
    bad = {"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED"}}}
    assert _metro(bad)[0].affecting(["2호선:이대"]) is None
    assert _metro(_payload(EWHA, total=150))[0].affecting(["2호선:이대"]) is None, "잘린 응답은 모름"


def test_metro_suspension_hits_every_target_on_the_line_with_section():
    items = [_item("21:00:00", "서소문 고가 철거공사 관련 2호선 일부구간 열차운행 중단", codes="0202, 0239")]
    got = _metro(_payload(items), now="21:30")[0].affecting(["2호선:이대", "2호선:시청", "4호선:서울역"])
    assert set(got) == {"2호선:이대", "2호선:시청"}, "지나가는지는 계산기가 구간 차단으로 다시 본다"
    assert got["2호선:이대"]["suspended"] is True and got["2호선:이대"]["section"] == ["충정로", "시청"]
    assert got["2호선:이대"]["effect"] == "skip_station", "재계획이 「못 씀」으로 알아듣는 효과 이름"


def test_metro_planned_skip_uses_its_window():
    items = [_item("18:11:00", "5호선 여의나루역 무정차 통과 계획", line="5호선", codes="2528", se="8",
                   begin="2026-09-22T19:00:00", end="2026-09-22T22:00:00")]
    table = {"2528": ("5호선:여의나루", True)}
    assert _metro(_payload(items), now="16:30", table=table)[0].affecting(["5호선:여의나루"]) == {}, "아직 멀다(2시간 넘게 전)"
    assert set(_metro(_payload(items), now="18:30", table=table)[0].affecting(["5호선:여의나루"])) == {"5호선:여의나루"}, \
        "시작 2시간 안이면 미리 걸린다 — 감시 루프는 지금으로 묻지만 일정은 앞으로의 것이다"
    assert set(_metro(_payload(items), now="20:00", table=table)[0].affecting(["5호선:여의나루"])) == {"5호선:여의나루"}
    assert _metro(_payload(items), now="22:30", table=table)[0].affecting(["5호선:여의나루"]) == {}, "끝난 뒤"


def test_metro_planned_suspension_is_an_incident_not_an_end_alert():
    """☆GPT 84 #1 — 시작·종료 일시가 찬 계획 운행 중단. 앞 판은 종료 일시 칸 때문에 끝 알림으로 읽어 사건 0 · 열린 사건까지 닫았다."""
    items = [_item("09:00:00", "2호선 이대역 무정차 통과중"),
             _item("10:00:00", "2호선 일부구간 열차운행 중단 계획", codes="0202, 0239", se="8",
                   begin="2026-09-22T21:00:00", end="2026-09-23T05:00:00")]
    r = read_items(items)
    assert [(i.kind, i.closed) for i in r.incidents] == [("skip", False), ("suspend", False)] and r.unpaired_end == 0
    got = _metro(_payload(items), now="21:30")[0].affecting(["2호선:이대"])
    assert got["2호선:이대"]["suspended"] and got["2호선:이대"]["station_skipped"], "운행 중단 + 그 역 무정차 둘 다 싣는다(#3)"


def test_metro_end_alert_closes_only_the_stations_it_names():
    """☆GPT 84 #2 — 시청·서울역 무정차에 시청 정상운행이 오면 서울역은 열린 채. 「전구간」·코드 없는 끝 알림은 그 호선 전부."""
    table = {"0151": ("1호선:시청", True), "0150": ("1호선:서울역", True)}
    base = [_item("09:07:00", "1호선 시청역 서울역 무정차 통과", line="1호선", codes="0151, 0150")]
    part = base + [_item("09:30:00", "1호선 시청역 정상운행", line="1호선", codes="0151")]
    got = _metro(_payload(part), now="09:40", table=table)[0].affecting(["1호선:시청", "1호선:서울역"])
    assert set(got) == {"1호선:서울역"}
    whole = base + [_item("09:30:00", "1호선 열차 정상운행", line="1호선", codes="전구간")]
    assert _metro(_payload(whole), now="09:40", table=table)[0].affecting(["1호선:시청", "1호선:서울역"]) == {}


def test_metro_end_alert_does_not_close_a_future_planned_incident():
    items = [_item("08:00:00", "5호선 여의나루역 무정차 통과 계획", line="5호선", codes="2528", se="8",
                   begin="2026-09-22T19:00:00", end="2026-09-22T22:00:00"),
             _item("09:00:00", "5호선 여의나루역 정상운행", line="5호선", codes="2528")]
    assert [i.closed for i in read_items(items).incidents] == [False], "아직 시작 안 한 계획 사건을 지금 온 끝 알림이 닫지 않는다"


def test_metro_open_incident_past_limit_is_counted_as_stale_not_as_recovered():
    items = [{**_item("09:00:00", "2호선 이대역 무정차 통과중"), "noftOcrnDt": "2026-09-21T09:00:00"}]
    src, _ = _metro(_payload(items), now="21:30")
    assert src.affecting(["2호선:이대"]) == {} and src.last_stale == 1 and src.misses["stale_open_incident"] == 1


def test_metro_suspension_with_unknown_codes_covers_the_whole_line():
    """☆GPT 84 #9 — 역 코드가 우리 역표에 없으면 구간을 모른다 → 「사건 없음」이 아니라 그 노선 전 구간."""
    items = [_item("21:00:00", "2호선 일부구간 열차운행 중단", codes="9998, 9999")]
    got = _metro(_payload(items), now="21:30")[0].affecting(["2호선:이대"])
    assert got["2호선:이대"]["suspended"] and got["2호선:이대"]["section"] is None


def test_items_of_single_and_empty():
    assert items_of(_payload([])) == []
    one = {"response": {"body": {"items": {"item": EWHA[0]}}}}
    assert items_of(one) == [EWHA[0]]
    assert items_of({"response": {"body": {"items": ""}}}) == []
    assert items_of({"nope": 1}) is None


# ── TOPIS 공지 ──────────────────────────────────────────────────────
ROWS = [
    {"bdwrSeq": "6194", "bdwrDivCd": "0202", "createDate": "2026-09-23 09:08:14",
     "bdwrTtlNm": "9/24(목) 강남구 관내 집회 대비 시내버스 정류소 무정차 안내",
     "bdwrCts": "강남구 관내 집회 대비 시내버스 정류소 무정차 예정입니다.<br> - 일시 : '26.9.24.(목)<br>"
                "ㅇ 무정차 버스정류소 및 노선 내역<br> - 26.9.24. (목) 13:00 ~ 14:00<br> [무정차 통과 정류장]<br>23285, 23641, 99999"},
    {"bdwrSeq": "6163", "bdwrDivCd": "0202", "createDate": "2026-09-14 10:47:25",
     "bdwrTtlNm": "9/19(토) 서울100K 관련 교통통제 및 버스 무정차 안내",
     "bdwrCts": "버스 임시무정차 예정\n1차) 2026.9.19.(토) 04:50~05:50 / 60분간\n- 대상 정류소\n세종문화회관(01-126) : 401, 406"},
    {"bdwrSeq": "6164", "bdwrDivCd": "0202", "createDate": "2026-09-14 11:15:46",
     "bdwrTtlNm": "9/20(일) 은평 차없는 날 행사에 따른 임시우회 안내",
     "bdwrCts": "나. 대상노선 : 2개업체, 3개 노선\n- (시내버스) 7613, 7022\n- 통제일시 : 2026.9.20.(일) 07:00 ~ 20:00"},
    {"bdwrSeq": "6187", "bdwrDivCd": "0202", "createDate": "2026-09-18 09:37:12",
     "bdwrTtlNm": "9/20(일) 서울 걷자 페스티벌 버스 임시우회 안내",
     "bdwrCts": "- 대상노선 : 25개 운수사 36개 노선\n- 통제일시 : 9.20.(일) 06:00 ~ 11:00"},
    {"bdwrSeq": "6166", "bdwrDivCd": "0201", "createDate": "2026-09-14 11:23:10",
     "bdwrTtlNm": "잠수교 뚜벅뚜벅축제 버스 우회 안내", "bdwrCts": "매주 일요일 · 405, 740번 반포대교로 임시 우회"},
    {"bdwrSeq": "1", "bdwrDivCd": "0203", "createDate": "2026-09-14 11:23:10",
     "bdwrTtlNm": "정책 안내 무정차", "bdwrCts": "2026.9.20.(일) 07:00 ~ 20:00 12345"},
]
BY_ARS = {"23285": {"2016", "146"}, "23641": {"146"}, "01126": {"401", "406"}}
TABLE = SimpleNamespace(routes_by_ars=BY_ARS, routes={"2016", "146", "401", "406", "7613", "7022", "405", "740"})


def test_topis_windows_and_stops():
    assert windows_of("- 26.9.24. (목) 13:00 ~ 14:00", datetime(2026, 9, 23).date()) == [
        (datetime.fromisoformat("2026-09-24T13:00:00+09:00"), datetime.fromisoformat("2026-09-24T14:00:00+09:00"))]
    two_days = windows_of("- 통제기간 : 2026.10.3.(토) 07:00 ~ 10.4.(일) 22:00", datetime(2026, 9, 28).date())
    assert two_days[0][1] == datetime.fromisoformat("2026-10-04T22:00:00+09:00")
    assert stops_of("일시 2026.9.19 인원 30000명\n대상 정류소 세종문화회관(01-126, 01-911)") == ["01126", "01911"]


def test_topis_reads_stop_skips_detours_and_counts_the_rest():
    r = read_rows(ROWS, stop_known=lambda a: a in BY_ARS, route_known=lambda x: x in TABLE.routes)
    got = {(n.seq, n.kind, tuple(n.stops), tuple(n.routes)) for n in r.notices}
    assert got == {("6194", "stop_skip", ("23285", "23641"), ()), ("6163", "stop_skip", ("01126",), ()),
                   ("6164", "detour", (), ("7613", "7022"))}, got
    assert {x["seq"] for x in r.not_read} == {"6187", "6166"}, "첨부만·반복 공지는 버리지 않고 센다"


def _topis(rows=ROWS, *, now="2026-09-24T13:30:00+09:00", fail=False, table=TABLE):
    calls = []

    def post(url, form):
        calls.append(form)
        if fail:
            return httpx.Response(200, text="<html>점검 중</html>")
        return httpx.Response(200, json={"rows": rows if form["pageIndex"] == 1 else []})
    clock = {"now": datetime.fromisoformat(now)}
    src = TopisNotices(stops=lambda: table, now=lambda: clock["now"], post=post)
    return src, calls, clock


def test_topis_stop_skip_maps_to_routes_through_that_stop_in_window():
    src, calls, clock = _topis()
    got = src.affecting(["버스:2016", "버스:146", "버스:7613", "2호선:이대"])
    assert set(got) == {"버스:2016", "버스:146"}
    assert got["버스:146"]["effect"] == "skip_station" and got["버스:146"]["stops"] == ["23285", "23641"]
    assert got["버스:2016"]["stops"] == ["23285"] and got["버스:2016"]["window_end"].startswith("2026-09-24T14:00")
    clock["now"] = datetime.fromisoformat("2026-09-24T15:00:00+09:00")
    assert src.affecting(["버스:2016"]) == {}, "시간대 밖"
    assert len(calls) == 3, "운행일에 한 번(3쪽)만 받는다"


def test_topis_parser_year_end_midnight_and_headcount():
    """☆GPT 84 #4 — 연말을 넘기는 끝 날짜 · 24:00 · 인원 숫자를 정류장으로 줍지 않기."""
    from datetime import date
    w = windows_of("- 통제일시 : 2026.12.31. 23:00 ~ 1.1. 02:00", date(2026, 12, 28))
    assert w == [(datetime.fromisoformat("2026-12-31T23:00:00+09:00"), datetime.fromisoformat("2027-01-01T02:00:00+09:00"))]
    w = windows_of("- 통제일시 : 2026.10.1. 23:00 ~ 24:00", date(2026, 9, 28))
    assert w[0][1] == datetime.fromisoformat("2026-10-02T00:00:00+09:00"), "24:00 = 다음 날 00:00"
    assert stops_of("무정차 안내\n예상 인원 23285명\n정류장 01126") == ["01126"]
    assert stops_of("[무정차 통과 정류장]\n23285, 23641\n\n참가 인원\n22838") == ["23285", "23641"], "빈 줄 뒤는 정류장 줄이 아니다"
    assert windows_of("- 일시 : 매주 일요일\n- 시간 : 14시 ~ 22시", date(2026, 9, 14)) == [], "날짜 없는 반복은 못 읽는다(센다)"


def test_topis_keeps_each_stops_own_window_and_looks_ahead():
    """☆GPT 84 #3 — 공지 둘이 한 노선에 걸리면 정류장마다 제 시간대. 내다보는 폭 안에 시작하는 무정차도 시간대와 함께 낸다."""
    rows = [{"bdwrSeq": "A", "bdwrDivCd": "0202", "createDate": "2026-09-23 09:00:00", "bdwrTtlNm": "정류소 무정차 A",
             "bdwrCts": "- 26.9.24. (목) 10:00 ~ 11:00\n[무정차 통과 정류장]\n23285"},
            {"bdwrSeq": "B", "bdwrDivCd": "0202", "createDate": "2026-09-23 09:00:00", "bdwrTtlNm": "정류소 무정차 B",
             "bdwrCts": "- 26.9.24. (목) 10:30 ~ 12:00\n[무정차 통과 정류장]\n23641"}]
    src, _, _ = _topis(rows, now="2026-09-24T10:45:00+09:00")
    got = src.affecting(["버스:146"])["버스:146"]
    wins = {r["ars"]: (r["start"][11:16], r["end"][11:16]) for r in got["stop_windows"]}
    assert wins == {"23285": ("10:00", "11:00"), "23641": ("10:30", "12:00")}, wins
    early, _, _ = _topis(rows, now="2026-09-24T08:00:00+09:00")
    assert early.affecting(["버스:146"])["버스:146"]["stops"] == ["23285", "23641"], "3시간 안에 시작 — 시간대와 함께 미리 낸다"
    far, _, _ = _topis(rows, now="2026-09-24T05:30:00+09:00")
    assert far.affecting(["버스:146"]) == {}


def test_topis_concurrent_first_calls_fetch_once():
    """☆GPT 84 #8 — 동시에 들어온 첫 호출들이 한 번만 받고 같은 결과를 본다(반쯤 바뀐 상태로 예외가 나지 않는다)."""
    import threading
    import time as _t
    calls, lock = [], threading.Lock()

    def post(url, form):
        with lock:
            calls.append(form["pageIndex"])
        _t.sleep(0.02)
        return httpx.Response(200, json={"rows": ROWS if form["pageIndex"] == 1 else []})
    src = TopisNotices(stops=lambda: TABLE, now=lambda: datetime.fromisoformat("2026-09-24T13:30:00+09:00"), post=post)
    out, errs = [], []

    def run():
        try:
            out.append(set(src.affecting(["버스:2016"])))
        except Exception as exc:            # noqa: BLE001 — 시험이 예외 유무를 본다
            errs.append(exc)
    threads = [threading.Thread(target=run) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errs and out == [{"버스:2016"}] * 8 and sorted(calls) == [1, 2, 3], (errs, calls)


def test_topis_detour_is_road_control():
    src, _, _ = _topis(now="2026-09-20T09:00:00+09:00")
    got = src.affecting(["버스:7613", "버스:405"])
    assert set(got) == {"버스:7613"} and got["버스:7613"]["effect"] == "road_control" and got["버스:7613"]["detour"]


def test_topis_failure_is_none_and_retried_later():
    """못 읽으면 None(감시 루프 치명) — 새벽 영업 확인이 구글을 못 불렀을 때처럼 세고 다시 본다.
    비공식 출처라 RETRY_MINUTES 안에는 다시 부르지 않는다. 버스 대상을 「확인 못 함」으로 돌리지 않는다."""
    from datetime import timedelta
    from app.infrastructure.travel.topis_notice import RETRY_MINUTES
    src, calls, clock = _topis(fail=True)
    assert src.affecting(["버스:2016"]) is None
    assert src.unsupported(["버스:2016", "2호선:이대"]) == [], "못 읽은 것은 unsupported 가 아니라 None 이다"
    assert src.affecting(["2호선:이대"]) == {}, "버스 대상이 없으면 묻지 않는다"
    assert src.affecting(["버스:2016"]) is None and len(calls) == 1, "재시도 간격 안에는 다시 부르지 않는다"
    clock["now"] += timedelta(minutes=RETRY_MINUTES + 1)
    src.affecting(["버스:2016"])
    assert len(calls) == 2, "간격이 지나면 다시 부른다"


def test_topis_unknown_routes_and_no_table_are_unsupported():
    src, calls, _ = _topis()
    assert src.unsupported(["버스:2016", "버스:G1003"]) == ["버스:G1003"], "우리 노선 표 밖(경기 버스)"
    none, calls2, _ = _topis(table=None)
    assert none.unsupported(["버스:2016"]) == ["버스:2016"] and none.affecting(["버스:2016"]) == {} and calls2 == []


# ── 합치기 ──────────────────────────────────────────────────────────
class _Fake:
    def __init__(self, events, blind=(), broken=False):
        self.events, self.blind, self.broken, self.asked = events, set(blind), broken, []

    def affecting(self, targets, at=None):
        self.asked.append(list(targets))
        return None if self.broken else {t: e for t, e in self.events.items() if t in targets}

    def unsupported(self, targets):
        return [t for t in targets if t in self.blind]


def test_combined_routes_targets_by_head():
    road = _Fake({"도로:세종대로": {"effect": "road_control"}})
    subway = _Fake({"2호선:이대": {"effect": "skip_station"}})
    bus = _Fake({}, blind={"버스:G1003"})
    c = CombinedRouteEvents(road=road, subway=subway, bus=bus)
    targets = ["도로:세종대로", "2호선:이대", "버스:2016", "버스:G1003"]
    assert set(c.affecting(targets)) == {"도로:세종대로", "2호선:이대"}
    assert road.asked == [["도로:세종대로"]] and subway.asked == [["2호선:이대"]] and bus.asked == [["버스:2016", "버스:G1003"]]
    assert c.unsupported(targets) == ["버스:G1003"]


def test_combined_missing_source_is_unsupported_and_failure_is_none():
    c = CombinedRouteEvents(road=None, subway=_Fake({}, broken=True), bus=None)
    assert c.unsupported(["도로:세종대로", "2호선:이대", "버스:2016"]) == ["도로:세종대로", "버스:2016"], \
        "맡을 소스가 없는 대상은 「확인 못 함」 — 합쳐도 사라지지 않는다"
    assert c.affecting(["2호선:이대"]) is None, "맡은 소스가 못 읽으면 None(치명)"
    assert c.affecting(["버스:2016"]) == {}, "그 소스에 묻지 않는 대상만이면 None 이 아니다"
    assert CombinedRouteEvents(subway=_Fake({}), bus=_Fake({}, broken=True)).affecting(["2호선:이대"]) == {}, \
        "버스를 안 쓰는 이동은 버스 소스가 못 읽어도 치명이 아니다"


def test_combined_refuses_a_source_without_unsupported():
    class NoBlind:
        def affecting(self, targets, at=None):
            return {}
    with pytest.raises(TypeError):
        CombinedRouteEvents(subway=NoBlind()).unsupported(["2호선:이대"])
