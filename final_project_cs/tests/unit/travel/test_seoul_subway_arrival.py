# -*- coding: utf-8 -*-
"""서울 실시간 지하철 도착정보 소스 — 「실시간 지하철 인증키」 전용 (2026-10-05).

★응답 칸 이름은 공식 안내(OA-12764)만 믿는다 — 실제 값은 운행 시간대에 확인한다(`[미확인]`). 빈 결과(INFO-200)와 인증 오류를 가르는지,
  키가 오류 기록에 새지 않는지를 본다.
"""
from __future__ import annotations

import logging

import httpx

from app.infrastructure.travel.seoul_subway import SeoulSubwayArrival, clean_station

KEY = "SECRETKEY123456"


def _src(payload=None, status=200, text=None, seen=None, key=KEY):
    def transport(url, params):
        if seen is not None:
            seen.append(url)
        kw = {"text": text} if text is not None else {"json": payload}
        return httpx.Response(status, request=httpx.Request("GET", url), **kw)
    return SeoulSubwayArrival(service_key=key, transport=transport)


OK = {"errorMessage": {"status": 200, "code": "INFO-000", "message": "정상 처리되었습니다.", "total": 2},
      "realtimeArrivalList": [
          {"subwayId": "1002", "updnLine": "내선", "trainLineNm": "성수행 - 역삼방면", "arvlMsg2": "3분 후 (역삼)", "arvlMsg3": "역삼",
           "arvlCd": "99", "barvlDt": "180", "recptnDt": "2026-10-05 08:01:02", "btrainSttus": "일반"},
          {"subwayId": "1007", "updnLine": "상행", "trainLineNm": "장암행 - 학동방면", "arvlMsg2": "전역 도착", "arvlMsg3": "학동",
           "arvlCd": "1", "barvlDt": "0", "recptnDt": "2026-10-05 08:01:02", "btrainSttus": "급행"}]}


def test_arrivals_are_normalized_and_the_key_stays_in_the_path_only():
    seen = []
    got = _src(OK, seen=seen).arrivals("강남역")
    assert got["station"] == "강남" and got["source"] == "seoul_subway_arrival" and got["kind"] == "subway_arrival"
    t = got["trains"]
    assert [(x["line"], x["direction"], x["eta_seconds"]) for x in t] == [("2호선", "내선", 180), ("7호선", "상행", 0)]
    assert t[0]["message"] == "3분 후 (역삼)" and "raw" not in t[0]
    assert seen[0].startswith("http://swopenapi.seoul.go.kr/api/subway/" + KEY + "/json/realtimeStationArrival/0/20/")
    assert "%EA%B0%95%EB%82%A8" in seen[0] and clean_station("강남역") == "강남" and clean_station("서울") == "서울"


def test_line_filter_keeps_only_that_line():
    got = _src(OK).arrivals("강남", line="2호선")
    assert [x["line"] for x in got["trains"]] == ["2호선"]


def test_no_data_code_is_an_empty_result_not_a_failure():
    empty = {"status": 500, "code": "INFO-200", "message": "해당하는 데이터가 없습니다.", "link": "", "developerMessage": "", "total": 0}
    got = _src(empty).arrivals("이대")
    assert got is not None and got["trains"] == [], "운행 시간 밖은 빈 결과 — 인증은 통과했다"


def test_auth_or_request_errors_are_unreadable_and_never_leak_the_key(caplog):
    src = _src({"status": 500, "code": "INFO-100", "message": f"인증키가 유효하지 않습니다. ({KEY})"})
    with caplog.at_level(logging.WARNING):
        assert src.arrivals("강남") is None
    assert src.misses["body_error"] == 1 and KEY not in caplog.text, "오류 기록에 키가 새면 안 된다"
    assert _src({"errorMessage": {"code": "ERROR-300", "message": "필수 값이 누락"}}).arrivals("강남") is None
    assert _src(text="<html>x</html>").arrivals("강남") is None and _src(status=500, text="x").arrivals("강남") is None


def test_missing_key_or_station_is_unknown():
    assert SeoulSubwayArrival(service_key="").arrivals("강남") is None
    assert _src(OK).arrivals("  ") is None
