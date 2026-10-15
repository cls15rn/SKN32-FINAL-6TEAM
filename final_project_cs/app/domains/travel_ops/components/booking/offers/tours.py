# -*- coding: utf-8 -*-
"""booking · offers — 예약하는 **상품형** 이동(서울 시티투어버스 · 한강 유람선 · 렌터카 · 한강버스). `[2026-10-15]` 예약 모듈 확장.

기차 · 버스(`transit.py`)는 「A → B · 그날 운행」이고, 여기는 「코스 하나를 고르고 그날 탄다」다 — 출발 · 도착이 없고 날짜 · 시간대 · 인원만 쓴다.

- **공급자를 부르지 않는다.** 타이거버스는 날짜별 잔여석 · 가격 JSON 이 있지만 `robots.txt` 가 `Disallow: /api/` 라 자동 호출하지 않는다
  (10-15 확인). 노랑풍선은 예약 폼이 POST 라 조건을 채운 주소가 없다. 그래서 **코스 목록(아래 `CATALOG`) + 코스 페이지 링크**로 답한다.
- 목록의 운행 정보는 각 회사 **공식 코스 페이지에 적힌 글**을 옮긴 것이다(`CHECKED` 날짜). 요금은 옮기지 않는다(바뀌는 값 — 링크한 페이지에서).
  그날 운행 여부 · 남은 좌석 · 임시 변경은 모른다 → 답에 그렇게 적는다. 페이지가 바뀌면 이 표를 고친다.
- 휴무: 타이거버스는 「매주 월요일 휴무(월요일이 공휴일이면 운행하고 화요일 휴무)」 — 공휴일 여부를 모르면 그 단서를 같이 적는다.
- 한강 유람선(이랜드크루즈)은 예매처가 maketicket(스마틱스)이고 상품 페이지 `/ticket/{상품 코드}` 가 로그인 없이 열린다(10-15 확인, robots.txt 없음).
  회차 · 잔여석은 그 화면이 내부 HTML 조각으로 받아 오는데 공식 API 가 아니라 쓰지 않는다. 운항 시각은 **상품 이름에 적힌 것**(10-15 목록).
  잠실 상품은 주말만, 불꽃 상품은 「10월 금요일 · 11~12월 토요일」(이름 그대로) — 그날 안 다니면 `closed_today`.
- 렌터카(롯데 · SK)는 예약 화면이 조건을 URL 로 받지 않는다(롯데는 숨은 칸에만 들어가고 화면엔 안 보임 · SK 는 상태가 URL 에 없음, 10-15) → 예약 화면 링크.
- 한강버스는 대중교통이라 **개인 예약이 없다**(누리집 FAQ 2026-03-02: 「예매 불가 · 단체 승차권 구매 및 예약 불가」, 탑승은 교통카드 · 발권기 카드 결제).
  10명 이상 단체는 서울시 발표(2026-10-14 보도)로 10-20 부터 평일 마곡 · 잠실 선착장 네이버 지도 사전 예약 시범(회차당 10~30명) — 누리집 공지는
  10-15 기준 아직 없다. 그래서 단체 안내는 출처를 그렇게 밝혀 `notes` 로 낸다.
"""
from __future__ import annotations

from datetime import date
from typing import Any

#: 공식 페이지를 보고 옮긴 날(playdata 내장 브라우저)
CHECKED = "2026-10-15"
WEEKDAYS = "월화수목금토일"

#: 코스 목록 — kind(city_tour · cruise) · band(day · night · None=페이지에서) · closed(쉬는 요일들, 월=0) ·
#: months(달 → 다니는 요일들, 그 달만 그 요일에) · note(그날 안 다닐 때 붙이는 말) · 나머지는 공식 페이지 글 그대로 줄인 것
CATALOG: list[dict[str, Any]] = [
    {"id": "tiger_day", "kind": "city_tour", "operator": "서울시티투어버스(타이거버스)", "name": "주간 도심고궁남산코스", "band": "day",
     "start": "광화문 매표소", "hours": "09:20~16:50 · 약 25분 간격 · 한 바퀴 1시간 30분 · 15개 정류장 자유 승하차",
     "booking": "온라인 예매는 이용 전날까지(당일은 광화문 매표소)", "closed": {0}, "note": "매주 월요일 휴무 — 월요일이 공휴일이면 운행하고 화요일 휴무(공휴일 여부는 확인 못 함)",
     "url": "https://www.seoulcitybus.com/ko/tours/1"},
    {"id": "tiger_night", "kind": "city_tour", "operator": "서울시티투어버스(타이거버스)", "name": "야간 한강·남산 코스", "band": "night",
     "start": "광화문 매표소", "hours": "19:00 하루 한 번(5~8월 19:30) · 90분 · N서울타워 약 25분 정차",
     "booking": "하루 한 번 운행이라 미리 예약 권장", "closed": {0}, "note": "매주 월요일 휴무 — 월요일이 공휴일이면 운행하고 화요일 휴무(공휴일 여부는 확인 못 함)",
     "url": "https://www.seoulcitybus.com/ko/tours/2"},
    {"id": "yellow_day", "kind": "city_tour", "operator": "노랑풍선시티버스", "name": "전통문화코스(주간)", "band": "day",
     "start": "동대문디자인플라자(DDP)", "hours": "09:30~17:00 · 30~40분 간격 · 1시간 30분",
     "booking": "온라인 예약(홈페이지)", "closed": None, "note": None,
     "url": "https://www.seoulcitytourbus.co.kr/tour-course/traditional-culture"},
    {"id": "yellow_night", "kind": "city_tour", "operator": "노랑풍선시티버스", "name": "야간운행코스", "band": "night",
     "start": None, "hours": None, "booking": "온라인 예약(홈페이지)", "closed": None, "note": None,
     "url": "https://www.seoulcitytourbus.co.kr/tour-course/night-traditional-culture"},
    {"id": "yellow_hangang", "kind": "city_tour", "operator": "노랑풍선시티버스", "name": "한강잠실코스", "band": None,
     "start": None, "hours": None, "booking": "온라인 예약(홈페이지)", "closed": None, "note": None,
     "url": "https://www.seoulcitytourbus.co.kr/tour-course/han-river-jamsil"},
    # 한강 유람선 — 이랜드크루즈(예매 maketicket). 이름 · 시각은 maketicket 상품 목록(10-15 아침) 그대로, 요금 · 패키지 · 단체 상품은 뺐다
    *[{"id": f"cruise_{code}", "kind": "cruise", "operator": "이랜드크루즈", "name": name, "band": band, "start": start, "hours": hours,
       "booking": "maketicket 예매(당일 예약은 취소 · 변경 불가)", "closed": closed, "months": months, "note": note,
       "url": f"https://www.maketicket.co.kr/ticket/{code}"}
      for code, name, band, start, hours, closed, months, note in (
          ("GD19514", "여의도 한강투어크루즈", "day", "여의도 선착장", "매일 낮 시간", None, None, None),
          ("GD31093", "여의도 한강파노라마크루즈", "day", "여의도 선착장", "지정일 13:00", None, None, None),
          ("GD19518", "여의도 선셋크루즈", "night", "여의도 선착장", "매일 18:00", None, None, None),
          ("GD19516", "여의도 달빛뮤직크루즈", "night", "여의도 선착장", "매일 19:00 · 20:30", None, None, None),
          ("GD19517", "여의도 별빛크루즈", "night", "여의도 선착장", "매일 22:00", None, None, None),
          ("GD24910", "여의도 불꽃뮤직크루즈", "night", "여의도 선착장", "20:30", None, {10: {4}, 11: {5}, 12: {5}},
           "10월은 금요일, 11~12월은 토요일만 운항"),
          ("GD27129", "선셋 디너 크루즈", "night", "여의도 선착장", "지정일 16:50", None, None, None),
          ("GD27130", "달빛 디너 크루즈", "night", "여의도 선착장", "지정일 19:30", None, None, None),
          ("GD24911", "잠실 한강투어크루즈", "day", "잠실 선착장", "주말 낮 시간", {0, 1, 2, 3, 4}, None, "주말에만 운항"),
          ("GD24912", "잠실 선셋크루즈", "night", "잠실 선착장", "주말 18:30", {0, 1, 2, 3, 4}, None, "주말에만 운항"),
          ("GD24913", "잠실 달빛뮤직크루즈", "night", "잠실 선착장", "주말 19:20", {0, 1, 2, 3, 4}, None, "주말에만 운항"),
          ("GD24914", "잠실 별빛크루즈", "night", "잠실 선착장", "주말 21:30", {0, 1, 2, 3, 4}, None, "주말에만 운항"))],
]

#: 고객이 말한 시간대(interpret 의 band) → 코스 시간대
_BAND_OF = {"dawn": "day", "morning": "day", "afternoon": "day", "evening": "night"}
#: 렌터카 · 한강버스 — 위 코스 목록과 같은 모양(링크 · 이용 조건)
CATALOG += [
    {"id": "lotte_rent", "kind": "rental", "operator": "롯데렌터카", "name": "내륙 단기 렌터카 예약", "band": None, "start": None, "hours": None,
     "booking": "대여 · 반납 일시와 지점은 그 화면에서 고르세요", "closed": None, "note": None,
     "url": "https://www.lotterentacar.net/hp/kor/reservation/index.do?areaFlag=1"},
    {"id": "lotte_ktx", "kind": "rental", "operator": "롯데렌터카", "name": "KTX+렌터카 예약", "band": None, "start": None, "hours": None,
     "booking": "기차와 렌터카를 함께 예약하는 화면", "closed": None, "note": None,
     "url": "https://www.lotterentacar.net/hp/kor/reservation/korail/index.do"},
    {"id": "sk_rent", "kind": "rental", "operator": "SK렌터카", "name": "내륙 단기 렌트(지점 고르기)", "band": None,
     "start": "서울 지점 18곳(서울역 · 용산역 · 영등포 · 홍대역 · 동대문 · 역삼 등)", "hours": None,
     "booking": "지점 · 일시는 그 화면에서 고르세요", "closed": None, "note": None,
     "url": "https://rent.skdirect.co.kr/short-rent/location-list?branchOrder=inlandFirst"},
    {"id": "hangang_bus", "kind": "river_bus", "operator": "한강버스", "name": "한강버스(수상 대중교통) 운항시간", "band": None, "start": None,
     "hours": "운항 시간표는 누리집에서", "booking": "개인은 예약 없이 선착장에서 교통카드(또는 발권기 카드 결제)로 탑승",
     "closed": None, "note": None, "url": "https://hgbus.co.kr/itc/information/schedule?menuId=5"},
]

#: 한강버스 단체 사전 예약 시범(서울시 2026-10-14 발표) — 이 날부터 · 평일 · 이 인원
RIVER_GROUP_FROM, RIVER_GROUP_MIN, RIVER_GROUP_MAX = date(2026, 10, 20), 10, 30

KIND_LABEL = {"city_tour": "서울 시티투어버스", "cruise": "한강 유람선", "rental": "렌터카", "river_bus": "한강버스"}


def _notes(kind: str, day: date, adults: int | None) -> list[str]:
    """종류별로 붙일 말 — 지금은 한강버스 단체 예약만."""
    if kind != "river_bus":
        return []
    if not adults or adults < RIVER_GROUP_MIN:
        return ["한강버스는 대중교통이라 예약할 필요가 없어요(누리집 FAQ) — 선착장에서 바로 타시면 됩니다."]
    when_ok = day >= RIVER_GROUP_FROM and day.weekday() < 5
    group = (f"{RIVER_GROUP_MIN}명 이상 단체는 평일 마곡 · 잠실 선착장에서 네이버 지도(선착장 검색 → 예약)로 사전 예약을 시범 운영해요"
             f"(회차당 {RIVER_GROUP_MIN}~{RIVER_GROUP_MAX}명, {RIVER_GROUP_FROM.month}월 {RIVER_GROUP_FROM.day}일부터 — 서울시 10-14 발표 기준, "
             "한강버스 누리집 공지는 10-15 기준 아직 없음).")
    if adults > RIVER_GROUP_MAX:
        group += f" 한 회차에 {RIVER_GROUP_MAX}명까지라 나눠 예약하셔야 해요."
    if not when_ok:
        group += " 그날은 대상이 아니에요(평일 · 시작일 이후만) — 예약 없이 선착장에서 타시면 됩니다."
    return [group]


def _runs_not(course: dict[str, Any], day: date) -> bool:
    """그날 안 다니는 코스인가 — 쉬는 요일이거나, 달별 요일 규칙이 있는데 그 달 · 그 요일이 아니면."""
    if course.get("closed") and day.weekday() in course["closed"]:
        return True
    months = course.get("months")
    return bool(months) and day.weekday() not in months.get(day.month, set())


def search_tours(*, kind: str, day: date, bands: list[str] | None = None, place: str | None = None,
                 adults: int | None = None) -> dict[str, Any]:
    """그날 · 그 시간대(· 그 출발 곳)에 맞는 코스. 시간대가 안 맞으면 전체를 돌려주고 `band_fallback` 으로 알린다. 공급자 호출 0."""
    wanted = {_BAND_OF[band] for band in bands or [] if band in _BAND_OF}
    same_kind = [course for course in CATALOG if course["kind"] == kind]
    if place:
        same_kind = [course for course in same_kind if course["start"] and place in course["start"]] or same_kind
    picked = [course for course in same_kind if not wanted or course["band"] in wanted or course["band"] is None]
    fallback = bool(wanted) and not any(course["band"] in wanted for course in picked)
    if fallback:
        picked = same_kind
    options = []
    for course in picked:
        options.append({key: value for key, value in course.items() if key not in ("closed", "months")}
                       | {"date": day.isoformat(), "closed_today": _runs_not(course, day)})
    return {"kind": kind, "date": day.isoformat(), "weekday": WEEKDAYS[day.weekday()], "place": place, "options": options,
            "notes": _notes(kind, day, adults),
            "band_fallback": fallback, "checked": CHECKED, "calls": 0}


__all__ = ["CATALOG", "CHECKED", "KIND_LABEL", "search_tours"]
