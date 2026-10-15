# -*- coding: utf-8 -*-
"""Booking Module(예약 모듈) — 항공편을 찾아 준다. `[2026-10-08]` 숙소 팀(`instances/lodging/`)과 같은 모양이다.

`[2026-10-14 팀장 결정]` 항공은 **규칙 기반 예약 모듈**(LLM 없음)이고 기차 · 버스로 넓힌다 — 폴더 · 클래스 이름을 `booking` 으로 바꿨다
(`instances/flight/` → `instances/booking/`, `FlightTeam` → `BookingTeam`). 코어와 주고받는 식별자(`team_id="flight"` · `flight.*` 능력 ·
항목 `kind:"flight"` · case type `flight`)는 그대로다 — `booking.*` 은 `booking_handoff` 팀 능력 이름이라 겹친다(팀장 결정 대기).
옛 이름 `FlightTeam` 은 별칭으로 남긴다.
`[2026-10-14 저녁]` **기차 · 버스**(`mode` train · bus): 같은 규칙 해석 → 같은 서버 검증 → `read.booking_search_transit`(TAGO 열차 · 고속 · 시외버스) 한 번(오는 날이 있으면 두 번) →
운행을 출발 시각순 · 시간대별로 보여 주고 예매 링크를 준다(`_search_transit`) — 조건을 채워 여는 주소(SRT · 트립닷컴 · 버스타고,
`offers/transit_links.py`)가 있으면 그것을, 없으면 사이트 첫 화면을.
`[2026-10-14 2차]` 일정 항목 `kind` train · bus 도 이 모듈이 맡는다(`ITEM_KINDS`) — 「바꿔 줘」는 같은 날 같은 구간의 다른 운행을 보류 제안으로
(원래 출발 앞뒤 3시간 · 같은 등급 먼저), 고르면 그 운행으로(출발 · 도착 시각 둘 다 — 국내라 시간대가 같다), 고를 때 운행 재확인(같음 · 요금 바뀜 · 없음).
★코어 라우팅(`trip_messages.ITEM_TEAMS` · `owner_team`)은 항목 kind == 팀 id 로 보므로 train · bus 항목이 이 팀으로 오려면 코어가 kind → 팀 표를
두어야 한다(항공 항목 입구와 같은 몫 — 팀장). 우리 쪽은 받으면 돈다.

흐름(`flight.assist`): 모델 **한 번**이 문장(+ 여행 일정 요약)을 고정된 구조로 옮긴다(도시 이름 → 공항 코드도 모델이) →
서버가 검증한다 → 빠진 값이 있으면 되묻고, 없으면 booking 모듈(`read.booking_search_flights`) **한 번** → 받은 값으로 답을 쓴다.
`[2026-10-10]` 어느 판매처를 부르고 어떻게 묶는지는 booking 모듈(`components/booking/offers/flights.py`)로 옮겼다(팀 피드백). 이 팀은
해석과 「무엇을 보여 줄지」(시간대 · 느린 경유 빼기)와 답 쓰기만 한다.

- 모델은 해석만 한다. 항공편 · 시각 · 가격 · 링크는 전부 도구가 준 값이다. 순서는 제공처가 준 그대로다.
- 예약 · 결제는 하지 않는다. 국제선은 마이리얼트립 **검색 결과 페이지** 링크(`searchUrl`)를 준다. 국내선은 그 링크가 오지 않아
  노선 검색 주소를 끝에 한 번 준다.
- `[2026-10-09]` **비교**: 같은 조건으로 Ignav(`read.flight_offers`, 판매처 = 항공사 · OTA)도 부르고, 같은 편(항공사 코드 · 출발 공항 ·
  출발 날짜 · 시각 · 도착 공항)끼리 묶어 판매처별 가격과 링크를 나란히 보여 준다. 순서는 편마다 가장 낮은 가격순(같은 통화끼리만).
  한쪽이 못 주면 다른 쪽만으로 답하고 그렇다고 적는다. 가격은 조회 시점 참고값이다(두 소스 모두 정확성을 보장하지 않는다).
- `[2026-10-09]` **시간대**: 출발 시각으로 새벽 · 오전 · 오후 · 저녁(`interpret.TIME_BANDS`)을 나눈다. 고객이 시간대를 말했으면 그 시간대 편만
  가격순으로 보여 주고, 말하지 않았으면 **시간대마다 최저가 한 편씩** 보여 준다 — 가장 싼 편이 새벽인 경우가 많다는 사용자 지적.
  ★시간대가 비면 「그 시간대 편이 없다」가 아니라 「받은 결과에 없다」다 — 소스마다 가격순 상위 `FETCH` 개만 받는다.
- 되묻기는 숙소 팀과 같은 까닭으로 `waiting` 이 아니라 답(`completed`)으로 한다.
- `flight.status` 는 전과 같다 — 잠긴 예약을 조회만 한다.
- `[2026-10-11]` **일정 항공편 항목(`flight.itinerary`)** — 숙소 팀과 같게(사용자 10-11 「숙소랑 똑같이」). 식당 · 활동 팀의 공용 부품
  (`_shared/itinerary_team.ItineraryWork`)을 쓴다. 여행 Case 가 **항공편 항목**(`subject_ref.part_id`, 한 항목 = 한 다리 — 가는 편 · 오는 편 따로)을
  가리키면 이 일감이다. 문장을 모델로 해석하지 않고 항목 · 여행에서 읽는다(출발 · 도착 공항 = `detail.origin` · `detail.destination`,
  날짜 = 항목 시작 날, 인원 = 여행 인원, 좌석 · 직항 = `detail.cabin` · `detail.direct_only`).
  - 이미 예약한 편(`booking_id` · `locked` · `detail.booked`)은 바꾸지 않는다.
  - 「바꿔 줘」 = 보여 준 편이 마음에 안 든다 → **새 편 3개**(같은 날 · 같은 노선 편도, 앞서 보여 준 편 · 지금 항목은 뺀다), 가격순.
    `[2026-10-12]` 식당 · 활동처럼 후보는 항목의 「다른 안」(`detail.alternates`)에, 답은 한 줄. 편은 그대로 두고 상세만 바꾼 `itinerary.apply`.
    고르면(`request.choice`) 그 편으로 — 출발 시각을 그 편으로, 원래 편은 「다른 안」으로, 예약 전(`detail.booked = False`).
  - 직항이 있으면 직항만(사용자 결정 10-10) — 새 직항이 없으면 경유까지 넓혀 다시 고르고(느린 경유는 여전히 뺀다) 그렇다고 적는다. 그래도 없으면
    「더 찾지 못했다」.
  - 항목은 우리 에이전트가 반영될 때 일정에 생긴다(사용자 10-11) — 그 전까지 가짜 일정 시험으로만 돈다.
  - `[2026-10-12]` **고를 때 가격 재확인** — 후보를 보여 준 뒤 시간이 지나 값이 바뀔 수 있어, 고르는 순간 같은 조건으로 한 번 더 찾아
    고른 편을 다시 본다(`_recheck`). 값이 바뀌면 바뀐 값으로 바꾸고 답에 적는다. 그 편의 소스가 모두 답했는데 편이 없으면 바꾸지 않고
    그렇다고 답한다. 찾기에 실패했거나 그 편의 소스 중 답하지 않은 곳이 있으면 바꾸되 「다시 확인하지 못했다」고 적는다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from app.core.contracts import Evidence, NextAction, TeamManifest, TeamResult, TeamTask

from app.domains.travel_ops.instances._shared._base import TravelTeamBase
from app.domains.travel_ops.instances._shared.itinerary_team import ITINERARY_TOOLS, ItineraryWork
from app.domains.travel_ops.components.booking.offers.tours import KIND_LABEL as TOUR_LABEL, search_tours
from app.domains.travel_ops.components.itinerary.itinerary_changes import ItineraryChange, NoChange, applied_record, with_request
from .interpret import _KOREAN_AIRPORTS, TIME_BANDS, Interpretation, InterpretationInvalid, ask, ground, needs, parse, settle_years
from .interpret_rules import interpret as interpret_rules

logger = logging.getLogger(__name__)

PROMPT_KEY = "flight.interpret"            # `[2026-10-14]` 더는 부르지 않는다(규칙 해석) — 프롬프트 등록 표와 시험이 이름을 알아 당분간 남긴다
RULES_KEY = "flight.interpret_rules"
SEOUL = ZoneInfo("Asia/Seoul")
#: 고객이 시간대를 말했을 때 그 시간대에서 보여 주는 편 수. 우리가 고른 값
SHOWN = 3
#: 소스마다 받는 수. 소스가 가격순으로 주므로 적게 받으면 비싼 시간대(오전)가 통째로 빠진다 —
#: ☆2026-10-09 12:35~36 playdata 김포→제주: 10 · 30개에는 오전 편 0, 100개에서 07:05 BX8025 가 잡혔다.
#: 마이리얼트립 100개는 0.2초(10개 2.4초), 느린 쪽은 Ignav(10개 15.9~25.2초)다. 국내선 상한 100 · 국제선 250(입력 정의).
#: Ignav 는 어댑터가 10개로 줄인다(`ignav.MAX_RESULTS`). 우리가 고른 값
FETCH = 100


def _won(value: Any, currency: str) -> str:
    if not isinstance(value, (int, float)):
        return "가격 모름"
    return f"{int(value):,}원" if currency in ("", "KRW") else f"{value:,} {currency}"


def _leg(leg: dict[str, Any]) -> str:
    minutes = leg.get("durationMinutes")
    took = f"{minutes // 60}시간 {minutes % 60}분" if isinstance(minutes, int) else "소요 모름"
    stops = leg.get("stops")
    # 국내선 응답에는 경유 칸이 없다(2026-10-07 17:31 playdata) — 모르면 적지 않는다
    route = "직항, " if leg.get("isDirect") or stops == 0 else (f"경유 {stops}회, " if isinstance(stops, int) else "")
    number = f" {leg['flightNumber']}" if leg.get("flightNumber") else ""
    return (f"{leg.get('departDate') or ''} {leg.get('origin') or '?'} {leg.get('departTime') or '?'} → "
            f"{leg.get('destination') or '?'} {leg.get('arriveTime') or '?'}{number} ({route}{took})")


def _band(option: dict[str, Any]) -> str | None:
    """첫 다리 출발 시각(HH:MM, 출발 공항 현지)이 드는 시간대 이름. 시각을 모르면 None — 어느 시간대에도 넣지 않는다."""
    clock = str((option["legs"][0] if option["legs"] else {}).get("departTime") or "")
    if len(clock) != 5 or clock[2] != ":":
        return None
    return next((name for name, _, start, end in TIME_BANDS if start <= clock < end), None)


#: 시간대 이름 → 한국어
BAND_LABEL = {name: label for name, label, _, _ in TIME_BANDS}

#: `[2026-10-14]` 기차 · 버스 — 시간대를 말했으면 그 시간대 운행을 출발 시각순으로 최대 이만큼, 안 말했으면 시간대마다 앞 운행 이만큼
TRANSIT_SHOWN, TRANSIT_PER_BAND = 6, 2
#: 이 모듈이 맡는 일정 항목 종류 — 항공 · 기차 · 버스(코어가 kind → 팀 표를 두면 train · bus 항목이 이리 온다)
ITEM_KINDS = ("flight", "train", "bus")
KIND_LABEL = {"flight": "항공편", "train": "기차", "bus": "버스"}


def _transit_key(option: dict[str, Any]) -> str:
    """운행 열쇠 — 버스는 노선 id(`number`)가 터미널 쌍마다 같은 값이라 출발 · 도착 터미널을 넣는다(☆10-14 시험: 서울경부 · 서울호남 10:00 이 같은 열쇠였다)."""
    where = f":{option.get('origin') or '?'}-{option.get('destination') or '?'}" if option.get("mode") != "train" else ""
    return f"{option.get('mode')}:{option.get('grade') or '?'}:{option.get('number') or '?'}:{option.get('date') or '?'}:{option.get('depart') or '?'}{where}"


def _same_run(option: dict[str, Any], pick: dict[str, Any]) -> bool:
    """다시 찾은 운행이 고른 운행과 같은가 — 종류 · 등급 · 날짜 · 출발 시각 + 열차는 번호, 버스는 출발 · 도착 터미널(터미널 조합을 합쳐 받으므로 — 검토 10-14)."""
    # `[10-15]` AREX 직통은 열차 번호가 없다(시간표에 칸이 없음) — 번호가 없는 열차는 버스처럼 출발 · 도착 역으로 가른다
    keys = ("mode", "grade", "date", "depart") + (("number",) if option.get("mode") == "train" and pick.get("number")
                                                  else ("origin", "destination"))
    return all(option.get(key) is not None and option.get(key) == pick.get(key) for key in keys)


def _is_own_run(option: dict[str, Any], item: Any, clock: str) -> bool:
    """후보가 지금 타는 운행인가 — 출발 시각이 같고, 번호(있으면)가 같거나 등급이 같은 계열(「KTX」 ↔ 「KTX-산천」). 버스 항목엔 노선 id 가 없다(검토 10-14)."""
    if option.get("depart") != clock:
        return False
    chosen = item.detail.get("chosen") or {}
    number, grade = chosen.get("number") or item.detail.get("number"), chosen.get("grade") or item.detail.get("grade")
    if number and option.get("number"):
        return str(number).lstrip("0") == str(option["number"]).lstrip("0")
    mine, theirs = str(grade or "").casefold(), str(option.get("grade") or "").casefold()
    return not mine or not theirs or mine.startswith(theirs) or theirs.startswith(mine)


def _transit_option(rank: int, option: dict[str, Any], item: Any, people: int | None, sites: list[dict[str, Any]]) -> dict[str, Any]:
    """운행 하나 → 식당 · 활동의 「다른 안」 기록 모양 + 기차 · 버스 칸. 출발 · 도착은 국내 같은 시간대(항목 시각의 시간대)."""
    def at(clock: Any) -> str | None:
        try:
            return datetime.fromisoformat(f"{option.get('date')}T{clock}").replace(tzinfo=item.starts_at.tzinfo).isoformat()
        except (TypeError, ValueError):
            return None
    price = option.get("price") if isinstance(option.get("price"), int) else None
    number = f" {option['number']}" if option.get("mode") == "train" and option.get("number") else ""
    deep = (option.get("links") or [None])[0]                     # 조건을 채워 여는 주소(SRT · 트립닷컴 · 버스타고) — 없으면 첫 화면
    site = deep or (sites[0] if sites else {})
    return {"key": _transit_key(option), "rank": rank,
            "name": f"{option.get('grade') or KIND_LABEL.get(option.get('mode'), '')}{number} {option.get('depart') or '?'}→{option.get('arrive') or '?'}",
            "place_id": None, "option": None, "option_label": None,
            "starts_at": at(option.get("depart")) or item.starts_at.isoformat(), "ends_at": at(option.get("arrive")), "walk_min": None,
            "mode": option.get("mode"), "kind": option.get("kind"), "grade": option.get("grade"), "number": option.get("number"),
            "date": option.get("date"), "depart": option.get("depart"), "arrive": option.get("arrive"), "minutes": option.get("minutes"),
            "origin": option.get("origin"), "destination": option.get("destination"), "band": _band_of(option.get("depart")),
            "per_person": price, "best": price * people if price is not None and people else price,
            "seller": site.get("name"), "link": site.get("url"), "link_seller": site.get("name"), "link_price": None,
            "link_kind": "route_search" if deep else "site",
            "sources": [option.get("source") or "tago"]}


def _fresh_runs(options: list[dict[str, Any]], exclude: set[str], *, around: int | None, grade: str | None,
                by_price: bool = False) -> list[dict[str, Any]]:
    """새 운행 고르기 — 보여 준 것 · 원래 운행은 빼고, 같은 등급 먼저, 원래 출발 앞뒤 `NEAR_HOURS` 안을 가까운 순, 나머지는 원래 출발에서 먼 순.
    `by_price` 면 등급 · 시각 대신 어른 요금 낮은 순(전체에서 — 검토 10-14: 자른 뒤 정렬하면 더 싼 운행이 빠진다). 최대 `SHOWN`."""
    fresh = [option for option in options if _transit_key(option) not in exclude and option.get("depart")]
    if by_price:
        return sorted(fresh, key=lambda option: (option.get("price") if isinstance(option.get("price"), int) else 10 ** 9,
                                                 option.get("depart") or "99:99"))[:SHOWN]
    def rank(option: dict[str, Any]) -> tuple[int, int, int]:
        same = 0 if grade and str(option.get("grade") or "").casefold() == grade.casefold() else 1
        clock = _clock_minutes(option.get("depart"))
        near = 0 if around is not None and clock is not None and abs(clock - around) <= NEAR_HOURS * 60 else 1
        distance = abs(clock - around) if around is not None and clock is not None else (clock or 0)
        return (same, near, distance)
    return sorted(fresh, key=rank)[:SHOWN]


def _band_of(clock: Any) -> str | None:
    clock = str(clock or "")
    if len(clock) != 5 or clock[2] != ":":
        return None
    return next((name for name, _, start, end in TIME_BANDS if start <= clock < end), None)


def _pick_transit(options: list[dict[str, Any]], wanted: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """운행을 고른다 — 말한 시간대가 있으면 그 시간대 안에서 출발 시각순 `TRANSIT_SHOWN` 개, 없으면 시간대마다 앞 `TRANSIT_PER_BAND` 개.
    둘째 값은 말했지만 운행이 없던 시간대."""
    by_band: dict[str, list[dict[str, Any]]] = {}
    for option in options:
        band = _band_of(option.get("depart"))
        if band:
            by_band.setdefault(band, []).append(option)
    if wanted:
        picked = [option for band in wanted for option in by_band.get(band, [])]
        picked.sort(key=lambda option: str(option.get("depart") or "99:99"))
        return picked[:TRANSIT_SHOWN], [band for band in wanted if band not in by_band]
    picked = [option for name, _, _, _ in TIME_BANDS for option in by_band.get(name, [])[:TRANSIT_PER_BAND]]
    return picked, []


def _transit_line(option: dict[str, Any], adults: int | None) -> str:
    took = f"{option['minutes'] // 60}시간 {option['minutes'] % 60}분" if isinstance(option.get("minutes"), int) else "소요 모름"
    if option.get("kind") == "arex" and not option.get("arrive"):     # `[10-15]` AREX 개정 판 — 출발 시각만 있다
        return (f"[{BAND_LABEL.get(_band_of(option.get('depart')) or '', '') or '시각 모름'}] {option.get('grade')} — "
                f"{option.get('origin') or '?'} {option.get('depart') or '?'} 출발 → {option.get('destination') or '?'} · 요금은 예매 화면에서")
    grade = option.get("grade") or ("열차" if option.get("mode") == "train" else "버스")
    number = f" {option['number']}" if option.get("mode") == "train" and option.get("number") else ""
    kind = {"express": "고속", "suburbs": "시외"}.get(option.get("kind") or "", "")
    price = option.get("price")
    fare = _won(price, "KRW") if isinstance(price, int) else ("요금은 예매 화면에서" if option.get("kind") == "arex" else "요금 모름")
    total = f"(성인 {adults}명 {price * adults:,}원)" if isinstance(price, int) and isinstance(adults, int) and adults > 1 else ""
    band = BAND_LABEL.get(_band_of(option.get("depart")) or "", "")
    return (f"[{band or '시각 모름'}] {kind}{grade}{number} — {option.get('origin') or '?'} {option.get('depart') or '?'} → "
            f"{option.get('destination') or '?'} {option.get('arrive') or '?'} ({took}) · 어른 {fare} {total}").rstrip()


#: 공항버스 답에 보여 줄 노선 수 · 노선마다 시각 수
AIRPORT_ROUTES_SHOWN, AIRPORT_TIMES_SHOWN = 10, 4


#: 노선 정보의 정류장 이름 중 좁히기에 쓰지 않는 것(양 끝 · 너무 넓은 말)
_STOP_IGNORED = {"서울", "인천공항", "인천공항1터미널", "인천공항2터미널", "공항"}


def _stop_core(stop: str) -> str:
    """「신논현역」 → 「신논현」, 「수락산역(직통)」 → 「수락산」 — 괄호 · 띄어쓰기 · 끝의 「역」을 뗀다."""
    core = re.sub(r"\(.*?\)", "", stop).replace(" ", "")
    return core[:-1] if core.endswith("역") and len(core) > 2 else core


def _route_stops(route: dict[str, Any]) -> set[str]:
    parts = re.split(r"[,·/]", str(route.get("route") or "") + "," + str(route.get("number") or "").partition("(")[2].rstrip(")"))
    return {core for part in parts if len(core := _stop_core(part)) >= 2 and core not in _STOP_IGNORED}


def _airport_area(routes: list[dict[str, Any]], text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """`[10-15]` 고객 문장에 나온 정류장 이름(노선 정보에서) — 있으면 그 정류장을 지나는 노선만. (찾은 이름, 노선)."""
    squeezed = text.replace(" ", "")
    stops = {route["number"]: _route_stops(route) for route in routes}
    said = sorted({core for cores in stops.values() for core in cores if core in squeezed}, key=len, reverse=True)
    said = [core for core in said if not any(core != longer and core in longer for longer in said)]   # 짧은 이름이 긴 이름 안에 들면 긴 쪽만
    if not said:
        return [], routes
    return said, [route for route in routes if stops[route["number"]] & set(said)]


def _airport_bus_lines(name: str, board: dict[str, Any], bands: list[str], how: str, *, text: str = "") -> list[str]:
    """`[10-15]` 공항버스(인천국제공항공사 버스정보) — 공항 → 서울은 그 터미널 공항 출발 시각(말한 시간대 안 앞 몇 개), 서울 → 공항은 노선 · 요금만.
    고객이 정류장 이름(「잠실」 「노원」)을 말했으면 그 정류장을 지나는 노선만(노선 정보 `routeinfo` 기준)."""
    terminal = board["terminal"]
    said, routes = _airport_area(board["routes"], text)
    day = "주말" if board.get("day_type") == "weekend" else "평일"
    out = [f"[{name}] 서울 ↔ 인천공항 버스는 공항리무진(공항버스)이에요. {how}"]
    if said:
        out.append(f"  「{' · '.join(said)}」을(를) 지나는 노선 {len(routes)}개만 골랐어요(노선 정보의 정류장 이름 기준).")
    if board["direction"] == "from_airport":
        def fits(clock: str) -> bool:
            return not bands or _band_of(clock) in bands
        running = [(route, [clock for clock in route["times"] if fits(clock)]) for route in routes]
        running = [(route, clocks) for route, clocks in running if clocks]
        said = " · ".join(BAND_LABEL[band] for band in bands if band in BAND_LABEL)
        out.append(f"  인천공항{terminal} 출발 {day} 시간표 기준(인천국제공항공사 공항버스 정보) — {said + ' ' if said else ''}출발이 있는 노선 "
                   f"{len(running)}개 중 {min(len(running), AIRPORT_ROUTES_SHOWN)}개:")
        for route, clocks in running[:AIRPORT_ROUTES_SHOWN]:
            fare = f"{route['price']:,}원" if isinstance(route.get("price"), int) else "요금 모름"
            ride = f" · 타는 곳 {route['ride']}" if route.get("ride") else ""
            out.append(f"   · {route['number']} {route.get('grade') or ''} · 성인 {fare}{ride} — {' · '.join(clocks[:AIRPORT_TIMES_SHOWN])}"
                       + (f" 외 {len(clocks) - AIRPORT_TIMES_SHOWN}회" if len(clocks) > AIRPORT_TIMES_SHOWN else ""))
        if len(running) > AIRPORT_ROUTES_SHOWN:
            out.append(f"  노선 번호순 앞 {AIRPORT_ROUTES_SHOWN}개만 보여 드렸어요 — 나머지 {len(running) - AIRPORT_ROUTES_SHOWN}개 노선은 아래 운영사 시간표에서 확인해 주세요.")
        out.append("  시각은 공항 출발 기준이고 그날 운행 · 결행 · 남은 좌석은 매표소에서 확인해 주세요. 공휴일은 따로 확인하지 못했어요.")
    else:
        out.append(f"  인천공항{terminal}로 가는 노선 {len(routes)}개(인천국제공항공사 공항버스 정보) — 서울 정류장별 출발 시각은 이 정보에 없어 아래 운영사 시간표에서 확인해 주세요:")
        for route in routes[:AIRPORT_ROUTES_SHOWN]:
            fare = f"{route['price']:,}원" if isinstance(route.get("price"), int) else "요금 모름"
            out.append(f"   · {route['number']} {route.get('grade') or ''} · 성인 {fare}")
        if len(routes) > AIRPORT_ROUTES_SHOWN:
            out.append(f"   · 그 밖에 {len(routes) - AIRPORT_ROUTES_SHOWN}개 노선")
    return out


def _carriers(option: dict[str, Any]) -> list[str]:
    """다리마다 운항 항공사 코드(편명 앞 두 글자, 없으면 `carrierCode`). 모르면 빈 글자."""
    found = []
    for leg in option["legs"]:
        number = str(leg.get("flightNumber") or "")
        found.append(number[:2].upper() if len(number) > 2 else str(leg.get("carrierCode") or "").upper())
    return found


def _minutes(option: dict[str, Any]) -> int | None:
    """편 전체 소요 분. 편에 값이 없으면 다리마다 값을 더한다. 하나라도 모르면 None."""
    if isinstance(option.get("minutes"), int):
        return option["minutes"]
    parts = [leg.get("durationMinutes") for leg in option["legs"]]
    return sum(parts) if parts and all(isinstance(part, int) for part in parts) else None


def _connecting(option: dict[str, Any]) -> bool:
    """경유가 **확실한** 편만 참. 경유 칸이 없으면(국내선 응답) 경유로 보지 않는다."""
    return any(isinstance(leg.get("stops"), int) and leg["stops"] > 0 for leg in option["legs"])


#: 직항이 있는 노선에서 경유 편이 가장 짧은 편의 몇 배를 넘으면 시간대 대표에서 빼나. 우리가 고른 값(2026-10-09 사용자와 정함)
SLOW_FACTOR = 2


def _drop_slow(options: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """직항이 하나라도 있으면, 경유이면서 가장 짧은 편의 `SLOW_FACTOR` 배를 넘게 걸리는 편을 뺀다. (남은 편, 뺀 수)

    ☆2026-10-09 12:39 · 14:46 playdata 인천→나리타 — 「새벽 최저가」가 세부퍼시픽 06:35 경유 1회 11시간 25분 732,000원이었다.
      직항 2시간대가 11만 원대부터 있는데 그 편을 시간대 대표로 내세웠다.
    """
    known = [minutes for option in options if (minutes := _minutes(option)) is not None]
    if not known or not any(not _connecting(option) and _minutes(option) is not None for option in options):
        return options, 0
    limit = min(known) * SLOW_FACTOR
    kept = [option for option in options
            if not (_connecting(option) and (minutes := _minutes(option)) is not None and minutes > limit)]
    return kept, len(options) - len(kept)


def _back_band(option: dict[str, Any], destination: str | None) -> str | None:
    """오는 다리(목적지에서 떠나는 첫 다리) 출발 시각의 시간대. 오는 다리가 없거나 시각을 모르면 None."""
    back = next((leg for leg in option["legs"][1:] if destination and leg.get("origin") == destination), None)
    return _band({"legs": [back]}) if back else None


def _apply_limits(pool: list[dict[str, Any]], found: Interpretation) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """`[2026-10-11]` 오는 편 시간대 · 1인 가격 상한으로 편을 거른다. (남은 편, 답에 붙일 줄, 결정 기록)

    ☆10-11 사용자 문장 「오는날에는 … 오후 출발로」 「가격은 왕복으로 50을 안넘었으면」 — 받는 칸이 없어 말없이 버려졌다.
    - 거를 편이 하나도 남지 않으면 거르지 않고 그렇다고 적는다(빈 답 대신 — 시간대와 같은 방식).
    - 1인 가격 = 편 가격 ÷ (성인 + 아동). 소스 가격은 일행 전체 값 — 10-11 16:57 playdata, 11/6 인천→나리타 편도 성인 2명으로
      판매처 화면과 맞춰 봄: 구글 항공권 「성인 2명의 세금 · 수수료 포함」 에어프레미아 13:55 ₩298,400(우리 298,400),
      카약 1인 142,335 / 총 284,670, 트립닷컴 1인 150,900 / 총 301,800, 스카이스캐너 제주항공 08:35 1인 171,700 / 총 343,400(우리 351,800).
      아동 · 유아가 섞인 경우는 확인 안 함(유아는 나누지 않는다). 답에 셈 방식을 적는다. 상한 기준이 「편도」이고 왕복 검색이면 상한을 두 배로 본다.
    """
    notes: list[str] = []
    record: dict[str, Any] = {}
    back_times = list(found.return_times or []) if found.return_date else []
    if back_times:
        said = " · ".join(BAND_LABEL[name] for name in back_times)
        matched = [option for option in pool if _back_band(option, found.destination) in back_times]
        if matched:
            pool = matched
            notes.append(f"오는 편은 {said} 출발 편만 골랐습니다.")
        elif any(_back_band(option, found.destination) for option in pool):
            notes.append(f"받은 결과에 오는 편 {said} 출발이 없어 오는 편 시간은 가리지 않았습니다.")
        else:
            notes.append(f"오는 편 시각이 받은 결과에 없어 오는 편 시간대({said})는 가리지 못했습니다.")
        record["return_times"] = back_times
    if found.max_price:
        people = max(1, (found.adults or 1) + (found.children or 0))
        cap = found.max_price * (2 if found.return_date and found.max_price_basis == "one_way" else 1)
        within = [option for option in pool if isinstance(option.get("best"), (int, float)) and option["best"] / people <= cap]
        basis = "가격 ÷ 인원으로 셈" if people > 1 else "1인 가격"
        if within:
            pool = within
            notes.append(f"1인 {cap:,}원 이하 편만 골랐습니다({basis}).")
        else:
            notes.append(f"1인 {cap:,}원 이하 편이 받은 결과에 없어 가장 싼 편들을 보여 드립니다({basis}).")
        record["price_cap"] = {"per_person": cap, "people": people, "within": len(within)}
    return pool, notes, record


def _pick(options: list[dict[str, Any]], wanted: list[str] | None) -> tuple[list[tuple[dict[str, Any], str]], list[str]]:
    """답에 실을 편과 그 줄의 표시, 그리고 받은 결과에 편이 없던 시간대들.

    - 말한 시간대가 **하나**면: 그 시간대 편만 가격순 `SHOWN` 개. 표시는 시간대 이름.
    - **여럿**이면(「새벽은 싫어」 → 오전 · 오후 · 저녁): 그 시간대들 안에서 시간대마다 최저가 하나.
      ☆12:22 playdata — 여럿을 가격순 3개로 했더니 셋 다 저녁이었다. 고객은 시간대를 넓혔지 저녁을 고른 것이 아니다.
    - 없으면: 네 시간대마다 가격이 가장 낮은 편 하나, 시간대 순서(새벽 → 저녁). 표시는 「오전 최저가」.
    `options` 는 이미 가격순이다(`_merge`).
    """
    if wanted and len(wanted) == 1:
        picked = [(option, BAND_LABEL[band]) for option in options if (band := _band(option)) in wanted][:SHOWN]
        return picked, [name for name in wanted if not any(_band(option) == name for option in options)]
    picked, empty = [], []
    for name, label, _, _ in TIME_BANDS:
        if wanted and name not in wanted:
            continue
        first = next((option for option in options if _band(option) == name), None)
        if first is None:
            empty.append(name)
        else:
            picked.append((first, f"{label} 최저가"))
    return picked, empty


def _flight_key(option: dict[str, Any]) -> str:
    """편 하나를 가리키는 이름 — 항공사 · 편명들 · 첫 출발 시각(편명이 없는 소스가 있어 시각을 붙인다)."""
    numbers = "/".join(str(leg.get("flightNumber") or "?") for leg in option["legs"])
    first = option["legs"][0] if option["legs"] else {}
    return f"{option.get('airline') or '?'} {numbers} {first.get('departDate') or ''} {first.get('departTime') or '?'}".strip()


#: 「바꿔 줘」 새 편을 고를 때 원래 편 출발 시각 앞뒤 몇 시간을 「같은 시간대」로 보나. 우리가 고른 값(2026-10-12 사용자와 정함 —
#:  ☆10-12 00:18 playdata: 원래 10:00 편인데 가격순만 보니 후보 셋이 다 오후였다)
NEAR_HOURS = 3


def _clock_minutes(clock: Any) -> int | None:
    """「HH:MM」 → 자정부터 분. 모르면 None."""
    text = str(clock or "")
    if len(text) != 5 or text[2] != ":" or not (text[:2] + text[3:]).isdigit():
        return None
    return int(text[:2]) * 60 + int(text[3:])


def _near(option: dict[str, Any], around: int | None) -> bool:
    """첫 다리 출발 시각이 `around`(자정부터 분) 앞뒤 `NEAR_HOURS` 시간 안인가. 어느 쪽 시각이든 모르면 거짓."""
    first = _clock_minutes((option["legs"][0] if option["legs"] else {}).get("departTime"))
    return around is not None and first is not None and abs(first - around) <= NEAR_HOURS * 60


def _fresh_flights(options: list[dict[str, Any]], exclude: set[str], *, direct_only: bool,
                   around: int | None = None) -> list[dict[str, Any]]:
    """새 편 `SHOWN` 개 — 앞서 보여 준 편은 뺀다. `direct_only` 면 경유가 확실한 편은 뺀다.
    `around`(원래 편 출발, 자정부터 분)가 있으면 그 앞뒤 `NEAR_HOURS` 시간 안의 편을 가격순으로 먼저, 모자라면 나머지를 가격순으로 채운다."""
    pool, _ = _drop_slow(options)
    fresh = [option for option in pool
             if not (direct_only and _connecting(option)) and _flight_key(option) not in exclude]
    near = [option for option in fresh if _near(option, around)]
    return (near + [option for option in fresh if not _near(option, around)])[:SHOWN]


def _shown_key(query: dict[str, Any]) -> str | None:
    """원장(`lg_offer_shown`)에서 이 항목을 부르는 이름 — 숙소 원장 `ledger.item_key("flight", …)` 와 같은 모양(팀끼리 import 하지 않으려고 여기 둔다)."""
    if query.get("origin") and query.get("destination") and query.get("depart_date"):
        return f"flight:{query['origin']}-{query['destination']}:{query['depart_date']}"
    return None


def _flight_option(rank: int, option: dict[str, Any], item: Any, people: int) -> dict[str, Any]:
    """새 편 하나 → 식당 · 활동의 「다른 안」 기록 모양 + 항공 칸(항공사 · 구간 · 총액 · 1인 값 · 가장 싼 판매처 · 링크).
    출발 시각은 첫 다리의 날짜 · 시각에 항목 시각의 시간대를 붙인다(출발 공항 현지 시각으로 본다 — 확인 안 함)."""
    first = option["legs"][0] if option["legs"] else {}
    numbers = "/".join(str(leg.get("flightNumber")) for leg in option["legs"] if leg.get("flightNumber"))
    clock = str(first.get("departTime") or "")
    try:
        starts = datetime.fromisoformat(f"{first.get('departDate')}T{clock}").replace(tzinfo=item.starts_at.tzinfo).isoformat()
    except (TypeError, ValueError):
        starts = item.starts_at.isoformat()
    offer = option["offers"][0] if option["offers"] else {}
    booking = _booking_offer(option)
    best = option.get("best")
    return {"key": _flight_key(option), "rank": rank, "name": " ".join(part for part in (option.get("airline"), numbers, clock) if part),
            "place_id": None, "option": None, "option_label": None, "starts_at": starts, "ends_at": None, "walk_min": None,
            "airline": option.get("airline"), "legs": option["legs"], "band": _band(option), "best": best,
            "per_person": round(best / people) if isinstance(best, (int, float)) and people else None,
            "seller": offer.get("label"),
            # `[2026-10-12]` 링크는 가장 싼 곳이 아니라 「그 편을 고를 수 있는」 가장 싼 곳 — 아래 `_booking_offer`
            "link": booking.get("link"), "link_seller": booking.get("label"), "link_price": booking.get("price_total"),
            "link_kind": booking.get("link_kind"),
            # `[2026-10-12]` 이 편을 준 소스 — 고를 때 다시 찾아 편이 없을 때 「소스가 답했는데 없다」와 「소스가 답하지 않았다」를 가른다
            "sources": list(dict.fromkeys(str(offer.get("source")) for offer in option.get("offers") or [] if offer.get("source")))}


def _same_flight(option: dict[str, Any], pick: dict[str, Any]) -> bool:
    """다시 찾은 편이 고른 편과 같은가 — 항공사 · 첫 다리 출발 공항 · 날짜 · 시각 · 마지막 다리 도착 공항 · 다리 수.
    편명은 보지 않는다 — 편명은 소스마다 있거나 없어(묶일 때 빌려 온다) 같은 편이어도 열쇠(`_flight_key`)가 달라질 수 있다."""
    def mark(flight: dict[str, Any]) -> tuple[Any, ...] | None:
        legs = flight.get("legs") or []
        if not legs:
            return None
        first, last = legs[0], legs[-1]
        return (flight.get("airline"), first.get("origin"), first.get("departDate"), first.get("departTime"),
                last.get("destination"), len(legs))
    return mark(option) is not None and mark(option) == mark(pick)


def _conditions(ctx: dict[str, Any]) -> dict[str, Any]:
    """「바꿔 줘」에 붙은 조건 — 접수 해석(`interpretation.report`) · 요청(`subject_ref.request`)이 준 칸만 읽는다(숙소 팀과 같은 계약).
    `cheaper`(참이면 지금 편보다 싼 편만) · `max_price`(**1인** 원화 상한)."""
    report = ctx.get("report") or {}
    limit = report.get("max_price")
    return {"cheaper": report.get("cheaper") is True,
            "max_price": int(limit) if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0 else None}


_SOURCE_NAMES = {"myrealtrip": "마이리얼트립", "ignav": "항공사 · 여행사 판매처 비교", "google": "구글 항공권"}


def _eun(word: str) -> str:
    """「은 / 는」 — 마지막 글자에 받침이 있으면 「은」."""
    last = str(word or "").rstrip()[-1:]
    return "은" if last and "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else "는"


def _ira(word: str) -> str:
    """「이라 / 라」 — 받침이 있으면 「이라」(항공편이라 · 기차라 · 버스라)."""
    return "이라" if _eun(word) == "은" else "라"


def _missing_note(sources: dict[str, Any] | None) -> str:
    """「바꿔 줘」 답 끝 한 문장 — 상한 안에 답이 없거나(`timeout`) · 쉬는 중(`resting`)인 소스를 적는다. 조회 실패(`failed`)는 적지 않는다(기록에만) — 건너뜀 · 참고도 안 적는다.
    ☆`[2026-10-13]` 14:49 playdata ICN→NRT: Ignav 가 20초 상한에 걸려 `timeout` — 전에는 검색 답(`_search`)에만 적고 「바꿔 줘」 답은 아무 말 없이
      두 소스 결과만 보였다. 고객이 판매처 비교가 빠진 줄 모르고 고르게 된다."""
    parts = []
    for name, state in (sources or {}).items():
        status, label = (state or {}).get("status"), _SOURCE_NAMES.get(name, name)
        if status == "timeout":
            parts.append(f"{label}{_eun(label)} {int(state.get('seconds') or 0)}초 안에 답이 없어 뺐어요")
        elif status == "resting":
            parts.append(f"{label}{_eun(label)} 요청 한도로 잠시 쉬는 중이에요")
    return (" " + " · ".join(parts) + " — 그쪽 가격이 더 쌀 수 있어요.") if parts else ""


def _per_person(option: dict[str, Any], people: int) -> float | None:
    best = option.get("best")
    return best / people if isinstance(best, (int, float)) and people else None


def _booking_offer(option: dict[str, Any]) -> dict[str, Any]:
    """예약 링크로 쓸 판매처 — 링크가 있고 노선 검색 결과 페이지(`route_search`)가 아닌 곳 중 가장 싼 곳(`offers` 는 이미 가격순).
    그런 곳이 없으면 가장 싼 곳(검색 결과 페이지일 수 있다 — `link_kind` 로 알린다).

    ☆2026-10-12 00:18 playdata 인천→나리타: 이스타 두 편은 가장 싼 판매처가 구글 항공권이라 링크가 구글 검색 결과 페이지였다 —
      고객이 그 편을 다시 찾아야 했다. 구글 항공권은 편마다 링크를 주지 않고 결과 페이지 하나만 준다(`serpapi_flights`)."""
    offers = option.get("offers") or []
    direct = next((offer for offer in offers if offer.get("link") and offer.get("link_kind") != "route_search"), None)
    return direct or (offers[0] if offers else {})


def _booking_note(pick: dict[str, Any], people: int | None) -> str:
    """고른 편의 예약 안내 — 링크 판매처가 가장 싼 곳과 다르면 그 까닭과 값을, 결과 페이지뿐이면 그렇다고 적는다."""
    link = pick.get("link")
    if not link:
        return ""
    if pick.get("link_kind") == "route_search":
        return f" — 이 편은 검색 결과 페이지만 있어요. 그 화면에서 {pick['name']} 편을 골라 예약해 주세요: {link}"
    seller, price = pick.get("link_seller"), pick.get("link_price")
    if seller and pick.get("seller") and seller != pick["seller"] and isinstance(price, (int, float)):
        each = f"(1인 {_won(round(price / people), 'KRW')})" if people else f"({_won(price, 'KRW')})"
        return (f" — 가장 싼 {pick['seller']}은(는) 검색 결과 페이지라, 바로 예약할 수 있는 {seller}{each} 링크예요: {link}")
    return f" — 예약은 이 링크에서 해 주세요: {link}"


def _price_note(check: dict[str, Any]) -> str:
    """고를 때 가격 재확인 결과 한 문장(앞에 빈칸). 1인 값을 모르면 아무것도 적지 않는다."""
    before, now = check.get("before"), check.get("now")
    seller_before, seller_now = check.get("seller_before"), check.get("seller_now")
    if check["status"] == "changed" and seller_before and seller_now and seller_before != seller_now:
        return (f" 보여 드릴 때 가장 싼 곳은 {seller_before}(1인 {_won(before, 'KRW')})이었는데, 지금은 "
                f"{seller_now}이(가) 1인 {_won(now, 'KRW')}으로 가장 싸요.")
    if check["status"] == "changed":
        return f" 보여 드릴 때 1인 {_won(before, 'KRW')}이던 가격이 지금 1인 {_won(now, 'KRW')}이에요."
    if check["status"] == "same":
        return f" 지금 가격도 1인 {_won(now, 'KRW')} 그대로예요."
    if before is not None:
        return f" 지금 가격은 다시 확인하지 못했어요(보여 드릴 때 1인 {_won(before, 'KRW')}) — 예약 전에 링크에서 확인해 주세요."
    return ""


class BookingTeam(ItineraryWork, TravelTeamBase):
    manifest = TeamManifest(
        team_id="flight",
        display_name="Booking Module (항공)",
        contract_name="a_cop.team_task",
        supported_contract_versions=["1.0"],
        capabilities=["flight.assist", "flight.status",
                      "flight.itinerary",             # ★`[2026-10-11]` 일정 항공편 항목 — 새 편 3개 · 예약한 편은 그대로(숙소 팀과 같다)
                      "flight.itinerary_question"],   # 공용 부품이 질문 Case 를 이리로 보낸다(규정을 읽고, 없으면 사람에게)
        accepted_case_types=["flight"],
        # ★`[2026-10-11]` 식당 · 활동 · 숙소 팀과 같게 — `policy` 를 선언하고 **규정 질문만** 규정을 읽는다. 나머지는 면제.
        #   전에는 `flight` scope 에 문서가 0건이라 선언하지 않았다 — 이제 여행 문서가 있는 취소 · 이용 조건 scope 를 둔다(항공 전용 문서는 없다).
        required_context=["case_state", "policy", "db_facts", "history"],
        policy_optional_capabilities=["flight.assist", "flight.status", "flight.itinerary"],
        allowed_tools=["read.booking", "read.itinerary", "read.booking_search_flights", "read.policy", "read.booking_terms",
                       "read.offers_shown",
                       "read.booking_search_transit",       # `[2026-10-14]` 기차 · 버스(TAGO)
                       *[name for name in ITINERARY_TOOLS if name != "read.itinerary"]],
        knowledge_scope=["travel_cancellation", "travel_access"],
        max_steps=4,                                  # 일정 1 + booking 1 + 여유(규정 질문 · 되돌리기) 2
        active=True,
        implementation_revision="2026-10-10",
        default_capability="flight.assist",
    )

    @staticmethod
    def select_capability(intent: str | None, input_text: str, state: dict | None = None) -> str | None:
        """여행 Case 가 **일정 항목을 가리킬 때만**(`part_id`) 일정 관리로 — 숙소 팀과 같다. 항목 없이 여행만 붙은 Case 는 `flight.assist`."""
        ref = (state or {}).get("subject_ref") or {}
        if not ref.get("part_id"):
            return None
        return ItineraryWork.itinerary_route("flight", state)

    async def execute(self, task: TeamTask) -> TeamResult:
        blocked = self._guard(task)
        if blocked is not None:
            return blocked
        if task.capability in (self.itinerary_capability, self.question_capability):
            return await self.run_itinerary(task)
        seen: set[str] = set()
        if task.capability == "flight.status":
            return self._status(task, seen)

        trip, evidence = self._trip(task, seen)
        today = datetime.now(SEOUL).date()
        # ★`[2026-10-14 팀장 결정]` 예약(항공 → 기차 · 버스)은 **규칙 기반 모듈** — 문장 해석에 모델을 쓰지 않는다(`interpret_rules`).
        #   칸(공항 · 날짜 · 인원 · 좌석 · 직항 · 시간대 · 가격 상한)이 정해진 영역이라 판단이 아니라 채우기다. 정답 50문장 50/50(10-14).
        #   뒤의 서버 검증(`parse → ground → settle_years → needs`)은 모델 때와 같다. 못 뽑으면 비워 두고 되묻는다. `self.llm` 은 쓰지 않는다.
        try:
            raw = interpret_rules(task.input_text, today=today, trip=trip)
            found, ungrounded = ground(parse(raw), task.input_text, has_trip=trip is not None, trip=trip)
            found, year_fixed = settle_years(found, today=today, text=task.input_text)   # `[2026-10-10]` 연도를 말하지 않은 날짜는 연도만 서버가 정한다
        except InterpretationInvalid as exc:
            logger.warning("flight interpretation invalid case=%s %s", task.case_id, exc)
            return self._escalate(task, "interpretation_invalid", evidence, warnings=[str(exc)[:200]])

        evidence = [*evidence, Evidence(
            evidence_id=f"interpretation:{task.team_id}", source_type="customer_message", source_id=RULES_KEY,
            claim="고객 문장을 규칙으로 옮긴 구조", value={**found.model_dump(mode="json"), "ungrounded": ungrounded, "year_fixed": year_fixed}, confidence=1.0,
            observed_at=datetime.now(SEOUL))]
        decision = {"task": found.task,
                    "interpretation": found.model_dump(mode="json", exclude={"question", "missing"}),
                    "ungrounded": ungrounded, "year_fixed": year_fixed}

        if found.task == "status":
            return self._status(task, seen, evidence)
        if found.task == "unclear":
            return self._respond(task, evidence, {**decision, "needs": ["task"]},
                                 "항공편을 찾아 드리려면 출발지 · 도착지 · 출발 날짜 · 인원을 알려 주세요. "
                                 "이미 예약하신 항공권에 대한 문의라면 그렇게 말씀해 주세요.")
        missing = needs(found, today=today)
        if missing:
            return self._respond(task, evidence, {**decision, "needs": missing}, ask(found, missing))
        if found.mode == "tour":
            return self._search_tours(task, found, evidence, decision)
        if found.mode != "flight":
            return self._search_transit(task, found, seen, evidence, decision)
        return self._search(task, found, seen, evidence, decision)

    def _status(self, task: TeamTask, seen: set[str], base: list[Evidence] | None = None) -> TeamResult:
        booking = self._read(task, "read.booking", {"case_id": str(task.case_id)}, seen)
        evidence = self._evidence(task, source_id="read.booking", claim="잠긴 예약", value=booking, base=base)
        if booking is None:
            return self._unknown(task, "예약 내역", evidence)
        return self._result(task, outcome="completed", confidence=1.0, evidence=evidence, next_action=NextAction.RESPOND,
                            answer="이 예약은 잠긴 예약으로 취급합니다. 일정 조정 대상이 아닙니다.",
                            decisions=[{"locked": True, "team": self.manifest.team_id}])

    def _search(self, task: TeamTask, found: Interpretation, seen: set[str], evidence: list[Evidence],
                decision: dict[str, Any]) -> TeamResult:
        when = found.depart_date.isoformat() + (f"~{found.return_date.isoformat()}" if found.return_date else "")
        arguments = {
            "origin": found.origin, "destination": found.destination, "depart_date": found.depart_date.isoformat(),
            "return_date": found.return_date.isoformat() if found.return_date else None, "domestic": found.domestic,
            "direct_only": found.direct_only, "cabin": found.cabin, "max_results": FETCH,
            "adults": found.adults, "children": found.children, "infants": found.infants}
        booked = self._read(task, "read.booking_search_flights", dict(arguments), seen)
        evidence = self._evidence(task, source_id="read.booking_search_flights",
                                  claim="항공편 비교 결과(booking: 마이리얼트립 · Ignav · 구글 항공권)", value=booked, base=evidence)
        states = {name: (booked or {}).get("sources", {}).get(name, {}).get("status", "failed")
                  for name in ("myrealtrip", "ignav", "google")}
        if not booked or not any(state in ("ok", "reference") for state in states.values()):
            return self._unknown(task, "항공편 검색 결과", evidence)
        route = f"{found.origin} → {found.destination}"
        options = booked.get("options") or []
        if not options:
            return self._respond(task, evidence, {**decision, "found": 0},
                                 f"{route} · {when} 조건으로 찾은 항공편이 없습니다. 날짜나 공항을 바꿔 다시 말씀해 주세요.")
        party = f"성인 {found.adults}명" + (f" · 아동 {found.children}명" if found.children else "") \
            + (f" · 유아 {found.infants}명" if found.infants else "")
        wanted = list(found.depart_times or [])
        pool, slow = _drop_slow(options)
        # `[2026-10-10 사용자 결정]` 고객이 「경유도 괜찮다」(`direct_only` False)고 하지 않으면 직항만 보여 준다.
        #   받은 결과에 직항이 하나도 없으면 빈 답 대신 경유 편을 보여 주고 그렇다고 적는다. 소스에는 고객이 말한 값만 보낸다(그대로).
        prefer_direct = found.direct_only is not False
        directs = [option for option in pool if not _connecting(option)]
        no_direct = prefer_direct and not directs
        via = len(pool) - len(directs) if prefer_direct and directs else 0
        if via:
            pool = directs
        pool, notes, limits = _apply_limits(pool, found)
        picked, empty = _pick(pool, wanted)
        fallback = bool(wanted) and not picked
        if fallback:
            # 말한 시간대 편이 받은 결과에 하나도 없다 — 빈 답 대신 다른 시간대 최저가를 보여 주고 그렇다고 적는다
            picked, _ = _pick(pool, None)
        shown_options = [option for option, _ in picked]
        cheapest = pool[0] if pool and pool[0]["best"] is not None else None
        minutes = [option["minutes"] for option in shown_options if isinstance(option["minutes"], int)]
        # ★소요 시간이 다 같으면 「가장 짧음」을 붙이지 않는다 — ☆12:09 김포→제주, 셋 다 75분인데 셋 다 붙었다
        fastest = min(minutes) if minutes and len(set(minutes)) > 1 else None
        lines = []
        for number, (option, mark) in enumerate(picked, start=1):
            legs = " / ".join(_leg(leg) for leg in option["legs"]) or "구간 정보 없음"
            tags = [mark]
            if option is cheapest:
                tags.append("전체 최저가")
            if fastest is not None and option["minutes"] == fastest:
                tags.append("가장 짧음")
            lines.append(f"{number}. [{' · '.join(tags)}] {option['airline'] or '항공사 모름'} — {legs}")
            codes = [code for code in _carriers(option) if code]
            if len(option["legs"]) > 1 and len(codes) == len(option["legs"]) and len(set(codes)) > 1:
                # ☆13:41 왕복 — 에어부산 BX164 / 제주항공 7C1104 묶음이었는데 에어부산 링크는 `TripType=OneWay` 가는 편만이었다
                lines.append(f"   ※ 가는 편과 오는 편 항공사가 달라({' · '.join(dict.fromkeys(codes))}) 따로 예약해야 할 수 있습니다. "
                             "판매처 화면에서 두 편이 함께 잡히는지 확인해 주세요.")
            for offer in option["offers"]:
                seats = f" · 남은 좌석 {offer['seats']}" if isinstance(offer.get("seats"), int) else ""
                # 국내선 마이리얼트립은 편마다가 아니라 노선 검색 주소 하나다 — 줄마다 붙이지 않고 끝에 한 번 적는다
                link = "" if offer.get("link_kind") == "route_search" else f": {offer.get('link') or '링크 없음'}"
                lines.append(f"   · {offer['label']} {_won(offer.get('price_total'), offer.get('currency') or '')}{seats}{link}")
        if empty and not fallback:
            # 뺀 경유 편만 있던 시간대는 「없었다」가 아니라 「뺐다」고 적는다
            kept_slow, _ = _drop_slow(options)
            removed = [name for name in empty if any(_band(option) == name for option in options)]
            slow_only = [name for name in removed if not any(_band(option) == name for option in kept_slow)]
            via_only = [name for name in removed if name not in slow_only]
            none = [name for name in empty if name not in removed]
            if none:
                lines.append(f"받은 결과에 {' · '.join(BAND_LABEL[name] for name in none)} 출발 편은 없었습니다"
                             "(가격이 낮은 순으로 일부만 받습니다).")
            if slow_only:
                lines.append(f"{' · '.join(BAND_LABEL[name] for name in slow_only)} 출발은 경유로 직항보다 "
                             f"{SLOW_FACTOR}배 넘게 걸리는 편뿐이라 뺐습니다.")
            if via_only:
                lines.append(f"{' · '.join(BAND_LABEL[name] for name in via_only)} 출발은 경유 편뿐이라 뺐습니다"
                             "(직항만 보여 드립니다. 경유도 괜찮으시면 말씀해 주세요).")
        if no_direct:
            lines.append("받은 결과에 직항이 없어 경유 편을 보여 드립니다.")
        lines += notes
        searches = sorted({(offer["label"], offer["link"]) for option in shown_options for offer in option["offers"]
                           if offer.get("link_kind") == "route_search" and offer.get("link")})
        pages = [f"{label} 이 노선 · 날짜의 검색 결과 페이지(위 편을 목록에서 고르시면 됩니다): {url}" for label, url in searches]
        sources = booked["sources"]
        count = {name: sources[name].get("count", 0) for name in sources}
        # `[2026-10-12]` 상한 안에 답하지 않은 소스(`timeout`)는 「응답이 늦어 뺌」 — 조회 실패와 가른다
        late = {name: f"{int(sources[name].get('seconds') or 0)}초 안에 답이 없어 뺌" for name in sources
                if sources[name].get("status") == "timeout"}
        counted = [{"ok": f"마이리얼트립 {count.get('myrealtrip')}개",
                    "resting": "마이리얼트립은 요청 한도로 잠시 조회를 쉬는 중",
                    "timeout": f"마이리얼트립은 {late.get('myrealtrip')}"}.get(states["myrealtrip"], "마이리얼트립 조회 실패"),
                   {"ok": f"항공사·여행사 판매처 {count.get('ignav')}개",
                    "skipped": "항공사·여행사 판매처는 편도만",
                    "timeout": f"항공사·여행사 판매처는 {late.get('ignav')}"}.get(states["ignav"], "판매처 비교 조회 실패"),
                   {"ok": f"구글 항공권 {count.get('google')}개",
                    "reference": "구글 항공권 왕복 최저가 참고",
                    "timeout": f"구글 항공권은 {late.get('google')}"}.get(states["google"], "구글 항공권 조회 실패")]
        if states["google"] == "reference" and sources["google"].get("page"):
            # 왕복 — 편끼리 묶지 못해 최저가 참고와 결과 페이지만 준다(`serpapi_flights.py`)
            lowest = sources["google"].get("round_trip_lowest")
            pages.append(f"구글 항공권 왕복 최저 참고 {_won(lowest, 'KRW') if lowest is not None else '가격 모름'}"
                         f"(가는 편 · 오는 편 합, 편별 비교 안 함): {sources['google']['page']}")
        said = " · ".join(BAND_LABEL[name] for name in wanted)
        if fallback:
            head = (f"{route} · {when} · {party} 조건으로 찾은 결과({' · '.join(counted)})에는 {said} 출발 편이 없어, "
                    "다른 출발 시간대마다 가장 싼 편을 보여 드립니다(가격이 낮은 순으로 일부만 받습니다).")
        else:
            how = (f"{said} 출발 편을 가격이 낮은 순으로 {len(shown_options)}개" if len(wanted) == 1
                   else f"{said} 출발 시간대마다 가장 싼 편을" if wanted else "출발 시간대마다 가장 싼 편을")
            # ★편 목록을 준 소스가 하나뿐이면(왕복 등) 「같은 편끼리 묶어」라고 하지 않는다 — 묶은 것이 없다
            listed = sum(1 for name, state in states.items() if state == "ok" and count.get(name))
            merged = "같은 편끼리 묶어 " if listed > 1 else ""
            head = f"{route} · {when} · {party} 조건으로 찾은 결과({' · '.join(counted)})를 {merged}{how} 보여 드립니다."
        others = ["같은 조건으로 다른 곳에서 보기(가격은 그 사이트에서 확인):",
                  *[f"   · {link['name']}" + (f"({link['note']})" if link.get("note") else "") + f": {link['url']}"
                    for link in booked.get("elsewhere") or []]]
        tail = ("가격과 좌석은 조회 시점 참고값이며 판매처 화면에서 달라질 수 있습니다. 링크가 열리지 않으면 같은 편의 다른 판매처 링크를 써 주세요. "
                "예약 · 결제는 링크한 판매처에서 직접 하시면 됩니다.")
        shown = [{"airline": option["airline"], "best": option["best"], "band": _band(option),
                  "offers": [{"source": offer["source"], "price_total": offer.get("price_total"), "link_kind": offer.get("link_kind")}
                             for offer in option["offers"]]} for option in shown_options]
        # 두 소스가 다 판 편 — 보여 준 것뿐 아니라 묶인 전부. 같은 편 가격 차이를 재려고 남긴다(답에는 안 싣는다)
        both = [{"flight": "/".join(str(leg.get("flightNumber") or "?") for leg in option["legs"]),
                 "depart": option["legs"][0].get("departTime"),
                 **{source: min((offer["price_total"] for offer in option["offers"]
                                 if offer["source"] == source and isinstance(offer["price_total"], (int, float))), default=None)
                    for source in ("myrealtrip", "ignav")}}
                for option in options if {offer["source"] for offer in option["offers"]} >= {"myrealtrip", "ignav"}]
        return self._respond(task, evidence, {**decision, "found": len(options), "shown": shown, "both": both,
                                              "times": wanted or None, "empty_bands": empty, "fallback": fallback,
                                              "dropped_slow": slow, "dropped_connecting": via, "no_direct": no_direct,
                                              **limits},
                             "\n".join([head, *lines, *pages, *others, tail]))

    # ── 기차 · 버스 (`[2026-10-14]` 예약 모듈 확장) ─────────────
    def _search_tours(self, task: TeamTask, found: Interpretation, evidence: list[Evidence], decision: dict[str, Any]) -> TeamResult:
        """`[2026-10-15]` 상품형(시티투어버스) — 공급자 호출 0. 코스 목록(공식 페이지 글) + 코스 페이지 링크. 그날 운행 · 좌석 · 요금은 링크에서."""
        kind = found.kind or "city_tour"
        got = search_tours(kind=kind, day=found.depart_date, bands=list(found.depart_times or []), place=found.origin, adults=found.adults)
        evidence = self._evidence(task, source_id="booking.tours_catalog", claim=f"{TOUR_LABEL.get(kind, kind)} 코스 목록(공식 페이지 {got['checked']} 기준)",
                                  value=got, base=evidence)
        party = f" · 성인 {found.adults}명" if found.adults else ""
        where = f"{found.origin} " if found.origin else ""
        until = f"~{found.return_date.isoformat()}" if found.return_date else ""
        lines = [f"{got['date']}({got['weekday']}){until}{party} — {where}{TOUR_LABEL.get(kind, kind)} 상품이에요. 운행 정보는 각 회사 공식 페이지({got['checked']} 확인) 기준이고, "
                 "그날 운행 · 남은 좌석 · 요금은 링크한 페이지에서 확인해 주세요."]
        if got["band_fallback"]:
            said = " · ".join(BAND_LABEL[band] for band in found.depart_times or [] if band in BAND_LABEL)
            lines.append(f"{said} 코스는 없어 전체를 보여 드립니다.")
        for number, course in enumerate(got["options"], start=1):
            band = {"day": "[주간] ", "night": "[야간] "}.get(course["band"] or "", "")
            facts = " · ".join(part for part in (f"{course['start']} 출발" if course["start"] else None, course["hours"], course["booking"]) if part) \
                or "운행 시각은 페이지에서"
            closed = f" ※ 그날은 안 다닐 수 있어요 — {course['note']}" if course["closed_today"] and course.get("note") else ""
            lines.append(f"  {number}. {band}{course['operator']} · {course['name']} — {facts}{closed}: {course['url']}")
        lines += got.get("notes") or []
        lines.append("예약 · 결제는 각 페이지에서 직접 해 주세요(우리는 조건을 채워 주지 못합니다)." if kind != "river_bus"
                     else "운항 시간 · 결항은 링크한 누리집에서 확인해 주세요.")
        return self._respond(task, evidence, {**decision, "mode": "tour", "kind": kind, "shown": [course["id"] for course in got["options"]],
                                              "closed_today": [course["id"] for course in got["options"] if course["closed_today"]],
                                              "supplier_calls": 0}, "\n".join(lines))

    def _search_transit(self, task: TeamTask, found: Interpretation, seen: set[str], evidence: list[Evidence],
                        decision: dict[str, Any]) -> TeamResult:
        """열차 · 버스 운행 비교 — TAGO 한 소스. 가는 날 한 번, 오는 날이 있으면 방향을 바꿔 한 번 더. 예매는 링크로."""
        label = "기차" if found.mode == "train" else "버스"
        legs = [("가는 편", found.origin, found.destination, found.depart_date, found.depart_times)]
        if found.return_date:
            legs.append(("오는 편", found.destination, found.origin, found.return_date, found.return_times))
        results: list[tuple[str, dict[str, Any] | None, list[str] | None]] = []
        for name, origin, destination, day, times in legs:
            arguments = {"mode": found.mode, "origin": origin, "destination": destination, "depart_date": day.isoformat(),
                         "adults": found.adults, "kind": found.kind}
            got = self._read(task, "read.booking_search_transit", {k: v for k, v in arguments.items() if v is not None}, seen)
            evidence = self._evidence(task, source_id=f"read.booking_search_transit:{name}",
                                      claim=f"{label} 운행 비교 결과(booking: TAGO)", value=got, base=evidence)
            results.append((name, got, times))
        states = [((got or {}).get("sources", {}).get("tago") or {}).get("status", "failed") for _, got, _ in results]
        if all(state == "failed" for state in states):
            return self._unknown(task, f"{label} 운행 검색 결과", evidence)
        when = found.depart_date.isoformat() + (f"~{found.return_date.isoformat()}" if found.return_date else "")
        party = f"성인 {found.adults}명" + (f" · 아동 {found.children}명" if found.children else "") \
            + (f" · 유아 {found.infants}명" if found.infants else "")
        head = f"{found.origin} → {found.destination} · {when} · {party} 조건으로 찾은 {label} 운행입니다."
        if found.children or found.infants:
            head += " 요금은 어른 기준이라 아동 · 유아 요금은 예매 화면에서 확인해 주세요."
        lines: list[str] = [head]
        shown_all: list[dict[str, Any]] = []
        deep: dict[str, str] = {}                           # 조건을 채워 여는 예매 주소 — 다리 · 사이트마다 하나(보여 준 첫 운행 기준)
        for (name, got, times), state in zip(results, states):
            source = (got or {}).get("sources", {}).get("tago") or {}
            if state == "failed":
                lines.append(f"[{name}] 운행 정보를 가져오지 못했습니다(조회 실패).")
                continue
            if state == "unresolved":
                places = " · ".join(source.get("unresolved") or [])
                lines.append(f"[{name}] 「{places}」을(를) {'역' if found.mode == 'train' else '터미널'} 이름으로 찾지 못했습니다. "
                             f"{'역' if found.mode == 'train' else '터미널'} 이름을 정확히 알려 주세요.")
                continue
            if state == "revised":                       # `[10-15]` AREX 10-23 시간표 개정 뒤 날짜 — 옛 시각을 주지 않는다
                revised_from = ((got or {}).get("timetable") or {}).get("revised_from") or ""
                lines.append(f"[{name}] 공항철도 직통은 {revised_from[5:7].lstrip('0')}월 {revised_from[8:10].lstrip('0')}일부터 시간표가 전면 바뀌어요"
                             "(공항철도 공지 10-14). 새 시각을 아직 반영하지 못해 그날 시각은 드리지 않을게요 — 아래 공지의 새 시간표를 확인해 주세요.")
                continue
            if state == "airport_bus":                   # `[10-15]` 서울 ↔ 인천공항 버스 = 공항리무진 — TAGO 운행표에 없다
                board = (got or {}).get("airport_bus")
                how = ("서울 → 공항은 사전 예약 없이 정류장에서 타고(교통카드 · 현금), 공항 → 서울은 공항 매표소 · 무인발권기에서 표를 삽니다"
                       "(공항리무진 공지 기준 — 노선마다 다를 수 있어요).")
                if not board or not board.get("routes"):
                    lines.append(f"[{name}] 서울 ↔ 인천공항 버스는 공항리무진(공항버스)이라 고속 · 시외 운행표에 없어요. {how} "
                                 "시간표가 필요하시면 「공항철도」로 물어봐 주세요 — 직통열차 시각을 드려요.")
                    continue
                lines += _airport_bus_lines(name, board, list(times or []), how, text=task.input_text)
                continue
            if state == "not_yet":                       # 팀장 점검 LB-03 — 공급자가 먼 날짜 버스 운행을 아직 안 준다
                lines.append(f"[{name}] 그날 버스 운행은 아직 확인할 수 없습니다 — 공급자가 가까운 날(보통 출발 1~2일 전)부터 운행표를 줍니다. "
                             "출발이 가까워지면 다시 물어봐 주세요.")
                continue
            options = (got or {}).get("options") or []
            resolved = (got or {}).get("resolved") or {}
            where = (f"{resolved.get('origin')} → {resolved.get('destination')}"
                     if resolved.get("origin") and resolved.get("destination") else "")
            if not options:
                lines.append(f"[{name}] {where + ' ' if where else ''}그날 운행이 없습니다(공급자 결과 기준). 날짜나 역 · 터미널을 바꿔 다시 말씀해 주세요.")
                continue
            note = ""
            if found.grade:
                graded = [option for option in options if found.grade.casefold() in str(option.get("grade") or "").casefold()]
                note = "" if graded else f"{found.grade} 운행은 없어 전체를 보여 드립니다. "
                options = graded or options
            if found.max_price is not None:
                # 말한 가격 상한(어른 1인) — 조용히 버리지 않는다(검토 10-14). 안 맞으면 전체를 보여 주고 그렇다고 적는다
                within = [option for option in options if isinstance(option.get("price"), int) and option["price"] <= found.max_price]
                note += (f"어른 1인 {_won(found.max_price, 'KRW')} 이하만 " if within
                         else f"어른 1인 {_won(found.max_price, 'KRW')} 이하 운행은 없어 전체를 보여 드립니다. ")
                options = within or options
            picked, bands_empty = _pick_transit(options, list(times or []))
            fallback = bool(times) and not picked
            if fallback:
                picked, bands_empty = _pick_transit(options, [])
            said = " · ".join(BAND_LABEL[band] for band in (times or []))
            how = (f"{said} 출발 {len(picked)}편" if times and not fallback
                   else f"{said} 출발은 없어 시간대마다 앞 운행을" if fallback else "시간대마다 앞 운행을")
            lines.append(f"[{name}] {where} · 전체 {len(options)}편 중 {note}{how} 보여 드립니다.")
            terminals = (got or {}).get("terminals") or {}
            if terminals.get("origin") and resolved.get("origin"):          # `[10-15]` 출발 터미널이 여럿 — 기본 터미널 기준으로 봤다고 알린다
                others = [t for t in terminals["origin"] if t not in str(resolved["origin"])]
                if others:
                    lines.append(f"  {found.origin}은(는) 터미널이 여럿이라 {resolved['origin']} 출발 기준이에요 — "
                                 f"{' · '.join(others)} 출발을 원하시면 터미널 이름으로 말씀해 주세요.")
            for number, option in enumerate(picked, start=1):
                lines.append(f"  {number}. {_transit_line(option, found.adults)}")
            timetable = (got or {}).get("timetable") or {}
            if timetable.get("kind") == "arex" and timetable.get("edition"):   # `[10-15]` 개정 판(10-23~) — 공지 첨부표, 도착 시각 없음
                since = timetable["edition"]
                lines.append(f"  공항철도 직통은 {since[5:7].lstrip('0')}월 {since[8:10].lstrip('0')}일 개정 시간표 기준이에요(공항철도 공지 첨부 · "
                             "평일 · 휴일 같은 표). 개정표에는 도착 시각이 없어 소요 시간은 비워 뒀어요. "
                             "그날 운휴 · 임시 변경 · 남은 좌석 · 요금은 예매 화면에서 확인해 주세요.")
            elif timetable.get("kind") == "arex":              # `[10-15]` AREX 직통 — 그날 운행이 아니라 요일 시간표라고 밝힌다
                table = "주말 · 공휴일" if timetable.get("day_type") == "holiday" else "평일"
                unsure = " 공휴일인지 확인하지 못해 평일 시간표로 봤어요." if timetable.get("basis") == "holiday_unknown" else ""
                lines.append(f"  공항철도 직통은 {table} 시간표 기준이에요({timetable.get('collected') or '수집일 모름'} 수집).{unsure} "
                             "그날 운휴 · 임시 변경 · 남은 좌석 · 요금은 예매 화면에서 확인해 주세요.")
            if times and bands_empty and not fallback:
                lines.append(f"  받은 결과에 {' · '.join(BAND_LABEL[b] for b in bands_empty)} 출발은 없었습니다.")
            shown_all += [{"leg": name, "grade": option.get("grade"), "depart": option.get("depart"), "price": option.get("price")}
                          for option in picked]
            for option in picked:
                for link in option.get("links") or []:
                    deep.setdefault(f"{name}:{link.get('site')}", f"{name} {link['name']}: {link['url']}")
        deep_sites = {key.split(":", 1)[1] for key in deep}
        if deep:
            lines.append("예매는 아래 주소로 — 출발 · 도착 · 날짜를 채워 엽니다(운행은 그 화면에서 고르세요):")
            lines += [f"   · {label}" for label in deep.values()]
        sites = {site["name"]: site["url"] for _, got, _ in results for site in ((got or {}).get("elsewhere") or [])
                 if not ("srail" in site["url"] and "srt" in deep_sites) and not ("bustago" in site["url"] and "bustago" in deep_sites)}
        guide_only = all(state in ("airport_bus", "revised") for state in states)
        if sites:
            lines.append("노선 · 시간표는 아래에서 확인해 주세요:" if guide_only else
                         "그 밖의 예매 사이트(조건을 채워 주지는 않습니다 — 날짜 · 역 · 터미널을 다시 고르셔야 합니다):"
                         if deep else "예매는 아래 사이트에서 직접(조건을 채워 주지는 않습니다 — 날짜 · 역 · 터미널을 다시 고르셔야 합니다):")
            lines += [f"   · {name}: {url}" for name, url in sites.items()]
        if any(isinstance(shown.get("price"), int) for shown in shown_all) or not (shown_all or guide_only):
            lines.append("요금은 공급자가 준 어른 1인 기준 조회 시점 참고값이며 좌석 등급 · 할인에 따라 예매 화면에서 달라질 수 있습니다. "
                         "좌석이 남았는지는 예매 화면에서 확인해 주세요.")
        calls = sum(len(((got or {}).get("sources", {}).get("tago") or {}).get("calls") or []) for _, got, _ in results)
        return self._respond(task, evidence, {**decision, "mode": found.mode, "legs": len(results), "shown": shown_all,
                                              "tago_calls": calls, "states": states, "booking_links": sorted(deep_sites),
                                              "via": sorted({((got or {}).get("sources", {}).get("tago") or {}).get("via") or "tago"
                                                             for _, got, _ in results})},
                             "\n".join(lines))

    # ── 일정 항공편 항목 (`flight.itinerary`, 2026-10-11) ─────────────
    def _change(self, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
        """「바꿔 줘」 — 공용 부품(들고 있던 다른 안으로 바꾸기)을 항공편용으로 덮는다. 숙소 팀과 같은 모양(2026-10-12 — 식당 · 활동처럼):
        고른 안이 없으면 새 편 3개를 항목의 「다른 안」(`detail.alternates`)에 싣고 답은 한 줄, 고른 안(`request.choice`)이 있으면 그 편으로 바꾼다."""
        ref, trip, items = ctx["ref"], ctx["trip"], ctx["items"]
        target = ref.get("part_id") or ref.get("recent_part_id")
        item = next((i for i in items if str(i.item_id) == str(target)), None) if target else None
        if item is None:
            return self._unknown(task, "항공편 · 기차 · 버스 일정 항목", ctx["evidence"])
        if item.kind not in ITEM_KINDS:
            return self._escalate(task, "target_kind_mismatch", ctx["evidence"],
                                  warnings=[f"{item.kind} 항목은 {self.manifest.team_id} 팀 일이 아니다"])
        if item.locked or item.booking_id or item.detail.get("booked"):
            return self._result(task, outcome="completed", confidence=1.0, evidence=ctx["evidence"],
                                next_action=NextAction.RESPOND,
                                answer=f"{item.title}은(는) 이미 예약하신 {KIND_LABEL[item.kind]}{_ira(KIND_LABEL[item.kind])} 바꾸지 않았어요. 다른 편을 원하시면 예약하신 곳에서 "
                                       "먼저 변경 · 취소 조건을 확인해 주세요.",
                                decisions=[{"itinerary": "booked_kept", "item": item.title}])
        choice = (ctx.get("request") or {}).get("choice")
        if choice:
            return self._choose(task, ctx, item, str(choice))
        if item.kind != "flight":
            return self._change_transit(task, ctx, item)
        query = self._flight_query(item, trip)
        if query is None:
            return self._unknown(task, "항공편 항목의 공항 · 날짜 · 인원", ctx["evidence"])
        booked = self._read(task, "read.booking_search_flights", {**query, "max_results": FETCH}, ctx["seen"]) or {}
        ctx["evidence"] = self._evidence(task, source_id="read.booking_search_flights",
                                         claim="항공편 새 후보(booking: 마이리얼트립 · Ignav · 구글 항공권)", value=booked or None,
                                         base=ctx["evidence"])
        options = booked.get("options") or []
        if not booked:
            return self._unknown(task, "항공편 검색 결과", ctx["evidence"])
        shown = [str(key) for key in item.detail.get("shown") or []]
        # `[2026-10-13]` 후보는 이제 일정에 싣지 않고 보류 제안으로 내민다 — 앞서 보여 준 편은 숙소 · 항공 원장에서 읽는다(항목 상세 `shown` 은 옛 판 몫)
        shown_key = _shown_key(query)
        ledger = self._read(task, "read.offers_shown", {"trip_id": trip["trip_id"], "item_key": shown_key}, ctx["seen"])             if shown_key else None
        shown += [str(offer["key"]) for offer in (ledger or {}).get("offers") or [] if offer.get("key")]
        exclude = {*shown, *([str(item.detail["key"])] if item.detail.get("key") else [])}
        prefer_direct = query.get("direct_only") is not False
        clock = item.starts_at.strftime("%H:%M")
        around = _clock_minutes(clock)   # 원래 편 출발 시각 — 그 앞뒤 편을 먼저(2026-10-12 사용자 결정)
        # `[2026-10-12]` 조건(접수 해석이 넘김) — 1인 가격 상한 · 지금 편보다 싸게. 지금 편 1인 값을 모르면 「더 싸게」는 가격순(시각 우선을 끈다)
        conditions, people = _conditions(ctx), query["adults"]
        chosen = item.detail.get("chosen") or {}
        reference = next((value for value in (chosen.get("per_person"), item.detail.get("per_person"))
                          if isinstance(value, (int, float)) and value > 0), None)
        price_limit = conditions["max_price"]
        if conditions["cheaper"] and reference is not None:
            price_limit = min(price_limit or reference, reference - 1)
        by_price = conditions["cheaper"] and reference is None
        if by_price:
            around = None
        if price_limit is not None:
            options = [option for option in options
                       if (each := _per_person(option, people)) is not None and each <= price_limit]
        asked = "더 싼" if conditions["cheaper"] else (f"1인 {_won(price_limit, 'KRW')} 이하" if price_limit is not None else "")
        picks = _fresh_flights(options, exclude, direct_only=prefer_direct, around=around)
        widened = False
        if not picks and prefer_direct:
            picks, widened = _fresh_flights(options, exclude, direct_only=False, around=around), True   # 새 직항이 없으면 경유까지
        near_count = sum(_near(option, around) for option in picks)
        record = {"itinerary": "flight_options", "item": item.title, "query": query, "shown_before": len(shown),
                  "widened_to_connecting": widened and bool(picks), "around": clock, "near_original": near_count,
                  "conditions": {**conditions, "price_limit": price_limit, "by_price": by_price},
                  "options": [_flight_key(option) for option in picks],
                  "sources": {name: (state or {}).get("status") for name, state in (booked.get("sources") or {}).items()}}
        if not picks:
            return self._result(task, outcome="completed", confidence=0.9, evidence=ctx["evidence"],
                                next_action=NextAction.RESPOND, decisions=[record],
                                answer=f"{item.title} 대신 탈 수 있는 {asked + ' ' if asked else ''}편을 더 찾지 못했어요. 일정은 그대로 두었어요.")
        people = query["adults"]
        alternates = [_flight_option(rank, option, item, people) for rank, option in enumerate(picks, start=1)]
        listed = " · ".join(f"{o['rank']}) {o['name']}" + (f"(1인 {_won(o['per_person'], 'KRW')})" if o.get("per_person") is not None else "")
                            for o in alternates)
        answer = (f"{item.title} 대신 탈 수 있는 편이에요 — {listed}."
                  + (" 새로 보여 드릴 직항이 없어 경유 편이에요." if widened else "")
                  + (f" 원래 출발({clock}) 앞뒤 {NEAR_HOURS}시간 안에는 새 편이 없어 다른 시간대예요." if not near_count and not by_price else "")
                  + (" 지금 편 가격을 몰라 1인 가격이 낮은 순으로 골랐어요." if by_price
                     else f" 지금 편(1인 {_won(reference, 'KRW')})보다 싼 편만 골랐어요." if conditions["cheaper"]
                     else f" 말씀하신 대로 1인 {_won(price_limit, 'KRW')} 이하만 골랐어요." if price_limit is not None else "")
                  + _missing_note(booked.get("sources"))
                  + " 고르시면 바꿀게요. 고르지 않으시면 원래 일정을 그대로 둡니다.")
        keys = [o["key"] for o in alternates]
        cause = with_request({"category": "customer_request", "type": "requested_options", "from": item.title,
                              "message": task.input_text}, task.context.current_state.get("request_id"))
        # ★`[2026-10-13]` 일정은 바꾸지 않는다 — 「선택이 필요해요」 제안으로 내민다(일정 판 그대로). 고르면 고르기 경로(`pending.choose` → `plan_swap`)가 바꾼다
        return self.offer(task, ctx, item, options=alternates, text=answer, causes=[cause], decision=record, shown_key=shown_key,
                          summary={"flight_options": keys, "shown_total": len(shown) + len(keys),
                                   "widened_to_connecting": widened, "near_original": near_count,
                                   "conditions": record["conditions"], "sources": record["sources"]})

    def _choose(self, task: TeamTask, ctx: dict[str, Any], item: Any, choice: str) -> TeamResult:
        """후보 중 고른 편으로 항목을 바꾼다 — 출발 시각을 그 편으로, 원래 편은 「다른 안」 끝으로. 예약 전(`detail.booked = False`)."""
        trip = ctx["trip"]
        people = trip.get("party_size") if isinstance(trip.get("party_size"), int) else None
        base = int(ctx["ref"].get("base_version") or trip["version"])
        if trip["version"] != base:
            return self.settle(task, ctx, NoChange("stale", {"version": trip["version"]}))
        alternates = list(item.detail.get("alternates") or [])
        pick = next((option for option in alternates if str(option.get("key")) == choice), None)
        if pick is None:
            return self._escalate(task, "itinerary_unknown_choice", ctx["evidence"],
                                  warnings=[f"고른 안 {choice} 이(가) 이 항목의 후보에 없다: {[o.get('key') for o in alternates]}"])
        before = (item.detail.get("chosen") or {}).get("name") or item.title
        held = pick
        pick, check = (self._recheck if item.kind == "flight" else self._recheck_transit)(task, ctx, item, pick, people)
        if check["status"] == "gone":
            return self._result(task, outcome="completed", confidence=0.9, evidence=ctx["evidence"],
                                next_action=NextAction.RESPOND,
                                decisions=[{"itinerary": "flight_gone", "item": item.title, "choice": held["key"], "price_check": check}],
                                answer=f"{held['name']} 편을 다시 찾아보니 지금은 나오지 않아요(매진됐거나 시각이 바뀌었을 수 있어요). "
                                       "일정은 그대로 두었어요. 다른 후보를 고르시거나 「바꿔 줘」라고 하시면 새로 찾아 드릴게요.")
        previous = {**applied_record(item), "key": str(item.detail.get("key") or item.item_id), "name": before}
        remaining = [option for option in alternates if option is not held] + [previous]
        detail = {**item.detail, "alternates": remaining, "other_options": [option["name"] for option in remaining],
                  "chosen": pick, "key": pick["key"], "booked": False, "customer_requested": True}
        starts = datetime.fromisoformat(pick["starts_at"]) if pick.get("starts_at") else item.starts_at
        cause = with_request({"category": "customer_request", "type": "alternate", "from": before, "to": pick["name"],
                              "message": task.input_text}, task.context.current_state.get("request_id"))
        text = (f"요청하신 대로 {before} 대신 {pick['name']}(으)로 바꿨습니다. 아직 예약 전이에요" + _booking_note(pick, people) + "."
                + _price_note(check))
        # ★항공은 도착 시각을 비운다 — 편 응답의 도착 시각은 도착 공항 현지 시각이라 출발과 같은 시간대로 적으면 틀린다(확인 안 함 — 시간대 정보가 없다)
        #   기차 · 버스는 국내라 같은 시간대 — 도착 시각을 그대로 적는다(`[2026-10-14 2차]`)
        ends = datetime.fromisoformat(pick["ends_at"]) if item.kind != "flight" and pick.get("ends_at") else None
        replacement = replace(item.replaced_by(place=None, title=item.title, detail=detail, starts_at=starts), ends_at=ends)
        change = ItineraryChange(reason="customer_request", causes=[cause],
                                 notice={"text": text, "language": "ko", "causes": [cause],
                                         "changed": {"from": before, "to": pick["name"], "at": starts.isoformat()},
                                         "other_options": detail["other_options"], "replay": False},
                                 replacements={item.item_id: replacement},
                                 summary={"to": pick["name"], "key": pick["key"], "price_check": check})
        return self.settle(task, ctx, change)

    def _recheck(self, task: TeamTask, ctx: dict[str, Any], item: Any, pick: dict[str, Any],
                 people: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """고른 편을 같은 조건으로 한 번 더 찾는다(booking 도구 1번). → (바꿀 때 쓸 편, 확인 결과).

        확인 결과 `status`: `same`(1인 값 그대로) · `changed`(바뀜 — 새 값으로) · `gone`(그 편의 소스가 모두 답했는데 편이 없다) ·
        `unchecked`(찾기 실패 · 조건 모름 · 답하지 않은 소스가 있다 — 보여 줄 때 값 그대로 쓴다)."""
        before = pick.get("per_person")
        unchecked = {"status": "unchecked", "before": before, "now": None}
        query = item.detail.get("query") or self._flight_query(item, ctx["trip"])
        if not query:
            return pick, unchecked
        booked = self._read(task, "read.booking_search_flights", {**query, "max_results": FETCH}, ctx["seen"]) or {}
        ctx["evidence"] = self._evidence(task, source_id="read.booking_search_flights:recheck",
                                         claim="고른 편 가격 재확인(booking)", value=booked or None, base=ctx["evidence"])
        fresh = next((option for option in booked.get("options") or [] if _same_flight(option, pick)), None)
        if fresh is None:
            states = booked.get("sources") or {}
            mine = pick.get("sources") or [name for name, state in states.items() if state.get("status") != "skipped"]
            answered = bool(states) and bool(mine) and all((states.get(name) or {}).get("status") == "ok" for name in mine)
            return pick, ({"status": "gone", "before": before, "now": None} if answered else unchecked)
        renewed = {**_flight_option(pick.get("rank") or 1, fresh, item, people or 1), "key": pick["key"], "name": pick["name"]}
        if not people:
            renewed["per_person"] = None
        now = renewed.get("per_person")
        if before is None or now is None:
            return renewed, {"status": "unchecked", "before": before, "now": now}
        # ★가장 싼 값은 「그 편을 준 판매처 중 가장 싼 곳」이라, 같은 편이어도 판매처 묶음이 달라지면 값이 바뀐다(가격 변동이 아니다).
        #   ☆10-12 18:41 playdata ICN→NRT MM642: 보일 때 1인 150,700원 → 고를 때 143,616원(링크는 Ignav 판매처) — 판매처를 함께 남겨 가른다
        seller_before, seller_now = pick.get("seller"), renewed.get("seller")
        return renewed, {"status": "same" if now == before else "changed", "before": before, "now": now,
                         "seller_before": seller_before, "seller_now": seller_now}

    # ── 기차 · 버스 항목 (`[2026-10-14 2차]`) ─────────────
    @staticmethod
    def _transit_query(item: Any, trip: dict[str, Any]) -> dict[str, Any] | None:
        """기차 · 버스 항목 · 여행 → 같은 날 같은 구간 검색 조건. 출발 · 도착 이름(역 · 터미널 또는 도시) · 인원을 모르면 `None`."""
        origin, destination = item.detail.get("origin"), item.detail.get("destination")
        adults = trip.get("party_size")
        if item.kind not in ("train", "bus") or not origin or not destination or not isinstance(adults, int) or adults < 1:
            return None
        query = {"mode": item.kind, "origin": str(origin), "destination": str(destination),
                 "depart_date": item.starts_at.date().isoformat(), "adults": adults}
        if item.detail.get("bus_kind") in ("express", "suburbs"):
            query["kind"] = item.detail["bus_kind"]
        return query

    def _change_transit(self, task: TeamTask, ctx: dict[str, Any], item: Any) -> TeamResult:
        """기차 · 버스 「바꿔 줘」 — 같은 날 같은 구간의 다른 운행을 보류 제안으로(항공과 같은 모양). 모델 0회 · TAGO 한 번."""
        trip, label = ctx["trip"], KIND_LABEL[item.kind]
        query = self._transit_query(item, trip)
        if query is None:
            return self._unknown(task, f"{label} 항목의 출발 · 도착 · 날짜 · 인원", ctx["evidence"])
        got = self._read(task, "read.booking_search_transit", query, ctx["seen"]) or {}
        ctx["evidence"] = self._evidence(task, source_id="read.booking_search_transit",
                                         claim=f"{label} 새 후보(booking: TAGO)", value=got or None, base=ctx["evidence"])
        state = ((got.get("sources") or {}).get("tago") or {}).get("status", "failed")
        if not got or state == "failed":
            return self._unknown(task, f"{label} 운행 검색 결과", ctx["evidence"])
        if state == "not_yet":                           # 팀장 점검 LB-03 — 「없음」이 아니라 「아직」
            return self._result(task, outcome="completed", confidence=0.9, evidence=ctx["evidence"], next_action=NextAction.RESPOND,
                                decisions=[{"path": "transit_change", "sources": {"tago": state}, "reason": "not_yet"}],
                                answer=f"{item.title}의 다른 {label} 운행은 아직 확인할 수 없어요 — 공급자가 가까운 날(보통 출발 1~2일 전)부터 "
                                       "운행표를 줘요. 출발이 가까워지면 다시 말씀해 주세요. 일정은 그대로 두었어요.")
        shown_key = f"{item.kind}:{query['origin']}-{query['destination']}:{query['depart_date']}"
        ledger = self._read(task, "read.offers_shown", {"trip_id": trip["trip_id"], "item_key": shown_key}, ctx["seen"])
        shown = [str(key) for key in item.detail.get("shown") or []] \
            + [str(offer["key"]) for offer in (ledger or {}).get("offers") or [] if offer.get("key")]
        clock = item.starts_at.strftime("%H:%M")
        around = _clock_minutes(clock)
        chosen = item.detail.get("chosen") or {}
        # 지금 타는 운행은 뺀다 — 항목 상세(등급 · 번호)와 출발 시각으로 열쇠를 만든다(고른 뒤에는 `detail.key`)
        exclude = {*shown, *([str(item.detail["key"])] if item.detail.get("key") else [])}
        conditions, people = _conditions(ctx), query["adults"]
        reference = next((value for value in (chosen.get("per_person"), item.detail.get("per_person"))
                          if isinstance(value, (int, float)) and value > 0), None)
        price_limit = conditions["max_price"]
        if conditions["cheaper"] and reference is not None:
            price_limit = min(price_limit or reference, reference - 1)
        options = [option for option in got.get("options") or [] if not _is_own_run(option, item, clock)]
        if price_limit is not None:
            options = [option for option in options if isinstance(option.get("price"), int) and option["price"] <= price_limit]
        grade = (chosen.get("grade") or item.detail.get("grade")) if not conditions["cheaper"] else None
        by_price = conditions["cheaper"] and reference is None
        picks = _fresh_runs(options, exclude, around=around, grade=grade, by_price=by_price)
        near_count = sum(1 for option in picks if (c := _clock_minutes(option.get("depart"))) is not None and around is not None
                         and abs(c - around) <= NEAR_HOURS * 60)
        resolved = got.get("resolved") or {}
        record = {"itinerary": f"{item.kind}_options", "item": item.title, "query": query, "resolved": resolved, "shown_before": len(shown),
                  "around": clock, "near_original": near_count, "conditions": {**conditions, "price_limit": price_limit},
                  "options": [_transit_key(option) for option in picks], "sources": {"tago": state},
                  "unresolved": ((got.get("sources") or {}).get("tago") or {}).get("unresolved")}
        asked = "더 싼" if conditions["cheaper"] else (f"1인 {_won(price_limit, 'KRW')} 이하" if price_limit is not None else "")
        if not picks:
            why = (" 역 · 터미널 이름을 찾지 못했어요(「" + " · ".join(record["unresolved"]) + "」)." if record["unresolved"] else "")
            return self._result(task, outcome="completed", confidence=0.9, evidence=ctx["evidence"],
                                next_action=NextAction.RESPOND, decisions=[record],
                                answer=f"{item.title} 대신 탈 수 있는 {asked + ' ' if asked else ''}{label} 운행을 더 찾지 못했어요.{why} 일정은 그대로 두었어요.")
        alternates = [_transit_option(rank, option, item, people, got.get("elsewhere") or []) for rank, option in enumerate(picks, start=1)]
        listed = " · ".join(f"{o['rank']}) {o['name']}" + (f"(1인 {_won(o['per_person'], 'KRW')})" if o.get("per_person") is not None else "")
                            for o in alternates)
        where = (f"({resolved['origin']} → {resolved['destination']}) " if resolved.get("origin") and resolved.get("destination") else "")
        answer = (f"{item.title} 대신 탈 수 있는 {label} 운행이에요 {where}— {listed}."
                  + (f" 원래 출발({clock}) 앞뒤 {NEAR_HOURS}시간 안에는 새 운행이 없어 다른 시간대예요." if not near_count and not by_price else "")
                  + (f" 지금 운행(1인 {_won(reference, 'KRW')})보다 싼 것만 골랐어요." if conditions["cheaper"] and reference is not None
                     else " 지금 운행 요금을 몰라 1인 요금이 낮은 순으로 골랐어요." if conditions["cheaper"]
                     else f" 말씀하신 대로 1인 {_won(price_limit, 'KRW')} 이하만 골랐어요." if price_limit is not None else "")
                  + " 요금은 어른 1인 기준 조회 시점 값이에요. 고르시면 바꿀게요. 고르지 않으시면 원래 일정을 그대로 둡니다.")
        keys = [o["key"] for o in alternates]
        cause = with_request({"category": "customer_request", "type": "requested_options", "from": item.title,
                              "message": task.input_text}, task.context.current_state.get("request_id"))
        return self.offer(task, ctx, item, options=alternates, text=answer, causes=[cause], decision=record, shown_key=shown_key,
                          summary={f"{item.kind}_options": keys, "shown_total": len(shown) + len(keys), "near_original": near_count,
                                   "conditions": record["conditions"], "sources": record["sources"]})

    def _recheck_transit(self, task: TeamTask, ctx: dict[str, Any], item: Any, pick: dict[str, Any],
                         people: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """고른 운행을 같은 조건으로 한 번 더 찾는다(TAGO 1번). `same` · `changed`(어른 요금) · `gone`(소스가 답했는데 그 운행이 없다) · `unchecked`."""
        before = pick.get("per_person")
        unchecked = {"status": "unchecked", "before": before, "now": None}
        query = item.detail.get("query") or self._transit_query(item, ctx["trip"])
        if not query:
            return pick, unchecked
        if item.kind == "bus" and pick.get("origin") and pick.get("destination"):
            # `[10-15]` 고른 운행의 터미널 이름으로 다시 찾는다 — 「서울」로 찾으면 기본 터미널(서울경부)에서 멈춰 서울호남 운행이 「없음」이 된다
            query = {**query, "origin": str(pick["origin"]), "destination": str(pick["destination"])}
        got = self._read(task, "read.booking_search_transit", query, ctx["seen"]) or {}
        ctx["evidence"] = self._evidence(task, source_id="read.booking_search_transit:recheck",
                                         claim="고른 운행 재확인(booking: TAGO)", value=got or None, base=ctx["evidence"])
        state = ((got.get("sources") or {}).get("tago") or {}).get("status")
        fresh = next((option for option in got.get("options") or [] if _same_run(option, pick)), None)
        if fresh is None:
            return pick, ({"status": "gone", "before": before, "now": None} if state == "ok" else unchecked)
        renewed = {**_transit_option(pick.get("rank") or 1, fresh, item, people, got.get("elsewhere") or []),
                   "key": pick["key"], "name": pick["name"]}
        now = renewed.get("per_person")
        if before is None or now is None:
            return renewed, {"status": "unchecked", "before": before, "now": now}
        return renewed, {"status": "same" if now == before else "changed", "before": before, "now": now}

    @staticmethod
    def _flight_query(item: Any, trip: dict[str, Any]) -> dict[str, Any] | None:
        """항공편 항목 · 여행 → 편도 검색 조건(한 항목 = 한 다리). 공항 · 인원을 모르면 `None`(지어내지 않는다).
        날짜는 항목 시작 시각에 붙은 시간대 그대로의 날(출발 공항 현지 시각으로 적혀 있다고 본다 — 확인 안 함)."""
        origin, destination = item.detail.get("origin"), item.detail.get("destination")
        adults = trip.get("party_size")
        if not origin or not destination or not isinstance(adults, int) or adults < 1:
            return None
        domestic = item.detail.get("domestic")
        if domestic is None:
            domestic = origin in _KOREAN_AIRPORTS and destination in _KOREAN_AIRPORTS
        query = {"origin": str(origin), "destination": str(destination), "depart_date": item.starts_at.date().isoformat(),
                 "domestic": bool(domestic), "adults": adults}
        for key in ("cabin", "direct_only"):
            if item.detail.get(key) is not None:
                query[key] = item.detail[key]
        return query

    def _trip(self, task: TeamTask, seen: set[str]) -> tuple[dict[str, Any] | None, list[Evidence]]:
        """Case 에 여행이 붙어 있으면 그 일정을 모델에 줄 요약으로(숙소 팀과 같다 — 팀끼리 import 하지 않으려고 따로 둔다)."""
        ref = task.context.current_state.get("subject_ref") or {}
        evidence = list(task.context.evidence)
        if ref.get("kind") != "trip" or not ref.get("id"):
            return None, evidence
        view = self._read(task, "read.itinerary", {"trip_id": ref["id"]}, seen)
        evidence = self._evidence(task, source_id="read.itinerary", claim="현재 일정 버전", value=view, base=evidence)
        if not view:
            return None, evidence
        days = sorted(str(item["starts_at"])[:10] for item in view.get("items") or [] if item.get("starts_at"))
        return {"party_size": (view.get("trip") or {}).get("party_size"),
                "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
                "places": [str(item.get("title")) for item in view.get("items") or [] if item.get("title")]}, evidence

    def _respond(self, task: TeamTask, evidence: list[Evidence], decision: dict[str, Any], answer: str) -> TeamResult:
        # `[2026-10-11]` 고객이 말했지만 우리가 받는 칸이 없는 요청(모델이 문장에서 그대로 옮긴 조각, 서버가 문장에 있는지 확인함)은
        #   조용히 버리지 않고 답 끝에 적는다 — ☆10-11 사용자 문장 측정: 「욕조 있는」 「얼리체크인」 「저가항공 말고」가 말없이 사라졌다
        others = (decision.get("interpretation") or {}).get("other_requests") or []
        if others:
            answer = answer + "\n말씀하신 조건 중 아직 반영하지 못한 것: " + " · ".join(f"「{item}」" for item in others) \
                + " — 링크한 화면에서 직접 확인해 주세요."
        return self._result(task, outcome="completed", confidence=1.0, evidence=evidence,
                            next_action=NextAction.RESPOND, answer=answer[:6000], decisions=[decision])


FlightTeam = BookingTeam   # 옛 이름 — 등록 문자열 · 시험 · 스크립트가 옮겨 갈 때까지

__all__ = ["BookingTeam", "FlightTeam", "PROMPT_KEY"]
