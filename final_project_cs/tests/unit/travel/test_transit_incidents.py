# -*- coding: utf-8 -*-
"""경로 사건 소스 — 버스 TOPIS 공지 · 합치기에 끼우기(이동 문제목록 #39 · 2026-10-01 · 2026-10-05 합치기). 네트워크 없이 돈다.

☆`[2026-10-05 합치기]` 지하철 알림 소스는 팀장 `subway_notice.py` 하나로 맞췄다 — 이동 담당이 따로 만든 `seoulmetro_alert.py` 와
  그 시험 14건(제목으로 종류 가르기 R0~R8 · 끝 알림으로 닫기 · 계획 무정차 시간대 · 운행 중단 구간 · 코레일 운영 역은 못 봄)은 내렸다.
  지하철 쪽 시험은 `test_subway_notice.py`(팀장)다. 합치기도 팀장 `CompositeRouteEvents` 를 쓴다 — 여기서는 버스 소스가 그 합치기의
  한 조각으로 맞게 도는지를 잠근다.

잠그는 것:
  ① 못 보는 대상은 unsupported() 로 — 합친 뒤에도 「사건 없음」으로 읽히지 않는다(팀장 9/30 요청)
  ② TOPIS(비공식)를 못 읽으면 버스 대상을 물은 호출만 None · 버스를 안 쓰는 이동은 영향 없음
  ③ TOPIS 정류장 무정차는 정류장 번호 → 그 정류장을 지나는 노선으로, 우회는 노선 번호로 · 시간대 밖은 사건 아님
표본 문구는 공개 공지(TOPIS 공지) 그대로 줄였다.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import httpx  # noqa: F401 — 아래 TOPIS 대역이 쓴다
import pytest  # noqa: F401

from app.infrastructure.travel.subway_notice import CompositeRouteEvents
from app.infrastructure.travel.topis_notice import (TopisNotices, read_rows, stops_of, windows_of)

T = lambda s: datetime.fromisoformat(f"2026-09-22T{s}:00+09:00")  # noqa: E731


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
    assert src.unsupported(["버스:2016", "2호선:이대"]) == ["2호선:이대"], \
        "못 읽은 버스 대상은 unsupported 가 아니라 None 이다 · 버스가 아닌 대상은 이 소스가 못 본다(2026-10-05 GPT 1)"
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


# ── 합치기(팀장 CompositeRouteEvents)에 버스 소스 끼우기 ─────────────────
class _Fake:
    def __init__(self, events, blind=(), broken=False):
        self.events, self.blind, self.broken, self.asked = events, set(blind), broken, []

    def affecting(self, targets, at=None):
        self.asked.append(list(targets))
        return None if self.broken else {t: e for t, e in self.events.items() if t in targets}

    def unsupported(self, targets):
        return [t for t in targets if t in self.blind]


def test_composite_asks_each_source_only_its_own_targets():
    road = _Fake({"도로:세종대로": {"effect": "road_control"}}, blind={"2호선:이대", "버스:2016", "버스:G1003"})
    subway = _Fake({"2호선:이대": {"effect": "skip_station"}}, blind={"도로:세종대로", "버스:2016", "버스:G1003"})
    bus = _topis()[0]
    c = CompositeRouteEvents([road, subway, bus])
    targets = ["도로:세종대로", "2호선:이대", "버스:2016", "버스:G1003"]
    got = c.affecting(targets, datetime.fromisoformat("2026-09-24T13:30:00+09:00"))
    assert {"도로:세종대로", "2호선:이대", "버스:2016"} <= set(got), got
    assert got["버스:2016"]["effect"] == "skip_station"
    assert road.asked == [["도로:세종대로"]] and subway.asked == [["2호선:이대"]]
    assert c.unsupported(targets) == ["버스:G1003"], "우리 표에 없는 노선은 어느 소스도 모른다 — 합쳐도 「확인 못 함」으로 남는다"


def test_composite_bus_failure_is_fatal_only_for_bus_targets():
    subway = _Fake({}, blind={"버스:2016"})
    bus = _topis(fail=True)[0]
    c = CompositeRouteEvents([subway, bus])
    assert c.affecting(["2호선:이대"]) == {}, "버스를 안 쓰는 이동은 버스 소스가 못 읽어도 치명이 아니다"
    assert c.affecting(["2호선:이대", "버스:2016"]) is None, "맡은 소스가 못 읽으면 None(치명 · 결정 15)"


def test_composite_without_a_bus_table_leaves_bus_targets_unsupported():
    bus = _topis(table=None)[0]
    c = CompositeRouteEvents([_Fake({}, blind={"버스:2016"}), bus])
    assert c.unsupported(["2호선:이대", "버스:2016"]) == ["버스:2016"]


def test_topis_alone_does_not_claim_road_or_subway_targets():
    """2026-10-05 GPT 1 — 팀장 합치기는 모든 대상을 소스에 넘기고 「돌려주지 않은 대상 = 본다」로 읽는다. 버스 소스가 도로·지하철
    대상을 돌려주지 않으면, 도로·지하철 소스가 없을 때(키 없음) 그 대상의 「확인 못 한 대상」 표시가 사라진다."""
    bus = _topis()[0]
    targets = ["9호선:여의도", "도로:강변북로", "버스:2016", "버스:G1003"]
    assert bus.unsupported(targets) == ["9호선:여의도", "도로:강변북로", "버스:G1003"], "단독으로 꽂힌 경우"
    assert CompositeRouteEvents([bus]).unsupported(targets) == ["9호선:여의도", "도로:강변북로", "버스:G1003"]
    road_only = _Fake({}, blind={"9호선:여의도", "버스:2016", "버스:G1003"})          # 도로 소스만 더 있을 때 — 지하철은 여전히 못 본다
    assert CompositeRouteEvents([road_only, bus]).unsupported(targets) == ["9호선:여의도", "버스:G1003"]
    assert _topis(table=None)[0].unsupported(targets) == targets, "정류장 표가 없으면 전부 못 본다"
    assert bus.affecting(["9호선:여의도", "도로:강변북로"]) == {}, "버스 대상이 없으면 읽지도 않는다"
