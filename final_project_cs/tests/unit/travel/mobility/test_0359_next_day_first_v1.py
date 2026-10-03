# -*- coding: utf-8 -*-
"""77(78 GPT 기록 Q4) — 03:59:59 요청은 그 운행일의 연장(28:00)으로 읽히고, 「첫차를 기다리면」은 **다음 달력일의 요일형**
첫차를 댄다(공휴일 전후). 1680 을 %1440 하거나 운행일 판별에 다시 넣으면 요일형이 하루 어긋난다 — 그걸 잠근다.

전체층(실데이터 · 시간표). 2호선 잠실→성수 · 초 입력 27:59:59(= 03:59:59 +1일) · 올림 → 04:00 표기 · 첫차 05:36 까지 96분.
  10-08(목) → 10-09 한글날(금 · 휴일)          : 전날 막차 = 평일 24:50 · 첫차 = holiday
  10-04(일) → 10-05 대체공휴일(월 · 개천절)      : 전날 막차 = 휴일 23:50 · 첫차 = holiday
  10-05(대체공휴일) → 10-06(화)                 : 전날 막차 = 휴일 23:50 · 첫차 = weekday
  10-02(금) → 10-03 개천절(토)                  : 전날 막차 = 평일 24:50 · 첫차 = holiday
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.mobility_full

CASES = [
    ("2026-10-08", "holiday", "24:50"),
    ("2026-10-04", "holiday", "23:50"),
    ("2026-10-05", "weekday", "23:50"),
    ("2026-10-02", "holiday", "24:50"),
]


@pytest.fixture(scope="module")
def rt():
    """판정기 하나를 이 파일에서만 쓰고 끝나면 놓는다(전체층은 한 프로세스라 메모리가 쌓인다)."""
    import gc
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    try:
        r = build_verifier(quiet=True, gh_url="", seoul_key="", road_graph="none")
    except RuntimeError as e:
        pytest.skip(f"data not present: {e}")
    yield r
    del r
    gc.collect()


@pytest.mark.parametrize("date,first_day,last", CASES)
def test_0359_next_day_first(rt, date, first_day, last):
    r = rt.verify_case({"id": f"D0359-{date}", "date": date, "depart_at": "27:59:59",
                           "legs": [{"line": "02호선", "from": "잠실", "to": "성수"}]})
    leg = r.legs[0]
    assert r.verdict == "infeasible" and leg.code == "before_first", (r.verdict, leg.code, leg.reason)
    assert f"{first_day} 첫차(05:36)" in leg.reason, leg.reason
    assert f"전날 막차 {last}" in leg.reason, leg.reason
    assert "04:00 (+1일)" in leg.reason, leg.reason


def test_0359_same_as_wallclock_form(rt):
    """「03:59:59」와 「27:59:59」는 같은 값(같은 운행일 연장)이다."""
    a = rt.verify_case({"id": "A", "date": "2026-10-08", "depart_at": "03:59:59",
                        "legs": [{"line": "02호선", "from": "잠실", "to": "성수"}]})
    b = rt.verify_case({"id": "B", "date": "2026-10-08", "depart_at": "27:59:59",
                        "legs": [{"line": "02호선", "from": "잠실", "to": "성수"}]})
    assert (a.verdict, a.legs[0].code, a.legs[0].reason) == (b.verdict, b.legs[0].code, b.legs[0].reason)
