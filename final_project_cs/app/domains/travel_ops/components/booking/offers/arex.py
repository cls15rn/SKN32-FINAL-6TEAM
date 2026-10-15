# -*- coding: utf-8 -*-
"""booking · offers — 공항철도(AREX) **직통열차** 운행. `[2026-10-15]` 예약 모듈 확장(기차 · 버스 → 예매가 있는 이동수단 더하기).

TAGO 기차에는 AREX 직통이 없다(서울→인천공항T1 0편). 이동 모듈이 모은 지하철 시간표에서 직통 편을 뽑아 둔 파일(`arex_direct.json`,
`scripts/build_arex_direct.py`)을 읽는다 — 공급자를 부르지 않는다. 그래서:

- **요일 시간표**다(평일 · 휴일). 그날의 운휴 · 임시 변경 · 남은 좌석은 모른다 → 답에 그렇게 적는다.
- 열차 번호가 없다(시간표에 칸이 없다). 요금도 없다(지어내지 않는다 — 예매 화면에서).
- 소요 시간은 시간표에 없어(종착 서울역 · 인천공항2터미널은 도착 행이 없다) AREX 예매 화면 값을 쓴다(`RIDE_MINUTES`, 10-15 확인).
- 예매 링크는 첫 화면뿐이다 — 예매 사이트가 검색 조건을 세션에만 둬서 조건을 채운 주소를 만들 수 없다(10-15 확인).
- ★`[10-15]` **2026-10-23 부터 일반 · 직통 시간표 전면 개정**(공항철도 공지 10-14, 첨부 xlsx · pdf). 이 파일은 10-01 수집본(= 공식 시간표
  페이지 `/train/express/info` 의 현행표와 첫 · 막차 · 편수 일치, 10-15 확인)이라 **10-22 까지만** 쓴다. 그 뒤 날짜는 시각을 주지 않고
  `revised` 로 알린다(새 표를 넣으면 `TABLE_UNTIL` 을 옮긴다).
- 직통은 서울역 ↔ 인천공항1 · 2터미널만 선다. 그 밖의 역(공덕 · 홍대입구 · 김포공항 …)은 일반열차(교통카드, 예매 없음) — 이동 모듈 몫이다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).with_name("arex_direct.json")
SEOUL, T1, T2 = "서울역", "인천공항1터미널", "인천공항2터미널"

#: 고객 말 → AREX 직통 역. 「인천공항」만 말하면 1터미널(대부분의 항공사 — 2터미널 항공사는 고객이 말해야 안다)
STATION_OF: dict[str, str] = {
    "서울": SEOUL, "서울역": SEOUL,
    "인천공항": T1, "인천공항T1": T1, "인천공항1터미널": T1, "인천공항 1터미널": T1, "인천공항 T1": T1,
    "인천공항T2": T2, "인천공항2터미널": T2, "인천공항 2터미널": T2, "인천공항 T2": T2,
}

#: 직통 소요 분 — AREX 예매 화면(2026-10-15 조회, 10-16 · 10-17 운행): 서울역 ↔ T1 43분, 서울역 ↔ T2 51분(양방향 같은 값으로 나옴).
#: 시간표 파일에는 종착역 도착 행이 없어 여기서만 온다. 바뀌면 이 표를 고친다.
RIDE_MINUTES: dict[tuple[str, str], int] = {(SEOUL, T1): 43, (T1, SEOUL): 43, (SEOUL, T2): 51, (T2, SEOUL): 51}

SITE = {"name": "AREX 공항철도 직통열차 예매", "url": "https://www.airportrailroad.com/intro"}
#: 이 시간표를 쓸 수 있는 마지막 날 — 10-23 개정(공지 등록 10-14). 개정 공지 · 공식 시간표 페이지
TABLE_UNTIL = date(2026, 10, 22)
REVISED_FROM = date(2026, 10, 23)
NOTICE = {"name": "공항철도 열차운행시각 변경 알림(10/23~)", "url": "https://www.airportrailroad.com/help/notice/CT202610140000000001"}
TIMETABLE_PAGE = {"name": "AREX 직통열차 시간표(공식)", "url": "https://www.airportrailroad.com/train/express/info"}
GRADE = "AREX 직통"


def pair(origin: str | None, destination: str | None) -> tuple[str, str] | None:
    """출발 · 도착이 AREX 직통 구간(서울역 ↔ 인천공항)이면 (출발역, 도착역). 아니면 None."""
    dep, arr = STATION_OF.get(str(origin or "").strip()), STATION_OF.get(str(destination or "").strip())
    if dep is None or arr is None or (dep == SEOUL) == (arr == SEOUL):
        return None                                       # 둘 다 서울역 · 둘 다 공항(T1 ↔ T2 는 무료 셔틀 · 일반열차)
    return dep, arr


@lru_cache(maxsize=1)
def table() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def day_type(day: date, holiday: Any = None) -> tuple[str, str]:
    """그날 쓸 시간표(weekday · holiday)와 근거. 토 · 일은 휴일표. 평일은 공휴일 소스(`travel.holiday`)에 묻고, 모르면 평일표 + 「모름」."""
    if day.weekday() >= 5:
        return "holiday", "weekend"
    if holiday is not None:
        try:
            answer = holiday.is_holiday(day)
        except Exception:                                 # 공휴일 소스 장애는 시간표 선택만 흐리게 한다 — 답을 막지 않는다
            answer = None
        if isinstance(answer, dict):
            return ("holiday", "public_holiday") if answer.get("is_holiday") else ("weekday", "weekday")
    return "weekday", "holiday_unknown"


def runs(origin: str, destination: str, depart_date: str, *, holiday: Any = None) -> dict[str, Any] | None:
    """그날의 직통 운행(출발 시각순). 구간이 아니거나 날짜를 못 읽으면 None."""
    found = pair(origin, destination)
    if found is None:
        return None
    try:
        day = datetime.strptime(depart_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    dep, arr = found
    if day > TABLE_UNTIL:                                 # 개정 뒤 날짜 — 옛 시각을 주지 않는다
        return {"options": [], "resolved": {"origin": dep, "destination": arr},
                "timetable": {"kind": "arex", "revised": True, "revised_from": REVISED_FROM.isoformat(), "notice": NOTICE["url"],
                              "collected": table().get("collected")}}
    kind, basis = day_type(day, holiday)
    data = table()
    clocks = ((data.get("runs") or {}).get(kind) or {}).get(dep) or []
    minutes = RIDE_MINUTES.get((dep, arr))
    options = []
    for clock in clocks:
        start = datetime.combine(day, datetime.strptime(clock, "%H:%M").time())
        end = start + timedelta(minutes=minutes) if minutes is not None else None
        options.append({
            "mode": "train", "kind": "arex", "grade": GRADE, "number": None,
            "origin": dep, "destination": arr, "date": day.isoformat(), "depart": clock,
            "arrive": end.strftime("%H:%M") if end else None, "minutes": minutes, "price": None,
            "source": "arex_timetable", "links": []})
    return {"options": options, "resolved": {"origin": dep, "destination": arr},
            "timetable": {"kind": "arex", "day_type": kind, "basis": basis, "collected": data.get("collected"),
                          "built_at": data.get("built_at")}}


__all__ = ["GRADE", "NOTICE", "REVISED_FROM", "RIDE_MINUTES", "SITE", "STATION_OF", "TABLE_UNTIL", "TIMETABLE_PAGE", "day_type", "pair", "runs"]
