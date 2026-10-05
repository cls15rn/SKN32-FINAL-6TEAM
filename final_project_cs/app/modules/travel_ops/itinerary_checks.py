# -*- coding: utf-8 -*-
"""일정을 **받을 때** 코드로 판정한다 — v11 §12 DoD-2·3.

★`[2026-09-21]` 전에는 등록이 **참조와 순서**만 봤다(모르는 장소·중복 seq). 시간이 겹치든 이동
  시간이 모자라든 그대로 받아 두고, 감시가 항목마다 고쳤다. v11 DoD-2 는 「받을 때 코드로 판정」,
  DoD-3 은 「불가능하면 **이유와 완화 조건**을 붙여 거절」이다.

★**보낸 값으로만 판정한다.** 바깥 조회가 없다 — 등록은 사람이 기다리는 요청이고, 여기서 외부를
  부르면 늦고 실패 경로가 는다. 그래서 **모르는 것은 판정하지 않는다**:

    이동 시간   이동 항목이 들고 온 경로 정의(`route_def`)의 계획 수단 `eta_min` 만 본다.
                이동 항목이 없는 구간은 **간격이 0 일 때만** 걸린다 — 몇 분 걸리는지 모르기 때문이다.
    영업시간    장소 속성의 `hours`·`break` 가 있을 때만 본다. 없으면 모른다(통과시킨다).
                식당은 요식 원장의 그 시각 판정(`place["ledger"]`)도 본다 — 붙이는 것은 부르는 쪽(`with_ledger`)이다.
    결제·예산   여행 제약(`constraints.payment`·`budget_krw`)이 있을 때만 본다.

★**거절은 「무엇이·왜·무엇을 바꾸면 되는지」를 같이 낸다.** 완화 조건(`remedy`)이 없는 위반은
  만들지 않는다 — 고칠 방법을 안 주면 거절이 벽이 된다.

★모름을 위반으로 세지 않는다. 값이 없는 칸은 **판정 대상이 아니다**(v11 §0-4 결정 15 — 모르는
  상태를 지어내지 않는다). 그 자리는 감시 루프가 실제 소스로 다시 본다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class Violation:
    code: str
    seq: tuple[int, ...]
    reason: str
    remedy: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "items": list(self.seq), "reason": self.reason, "remedy": self.remedy}


@dataclass(frozen=True)
class Part:
    """판정에 필요한 만큼의 일정 항목. API·저장소 어느 쪽 모양에도 안 묶인다."""

    seq: int
    kind: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    place: Mapping[str, Any] | None = None      # {"name":…, "attributes": {...}}
    route: Mapping[str, Any] | None = None      # route_def — {"planned": id, "options": [...]}
    detail: Mapping[str, Any] | None = None


def _hm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _clock(value: Any) -> time | None:
    try:
        hour, minute = str(value).split(":")
        return time(int(hour), int(minute))
    except (AttributeError, TypeError, ValueError):
        return None


def _minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def _planned_eta(route: Mapping[str, Any]) -> tuple[str, int] | None:
    options = {str(option.get("id")): option for option in route.get("options") or []}
    option = options.get(str(route.get("planned")))
    if option is None or option.get("eta_min") is None:
        return None
    return str(option.get("label") or option.get("id")), int(option["eta_min"])


def check_itinerary(parts: Iterable[Part], *, constraints: Mapping[str, Any] | None = None,
                    party_size: int | None = None) -> list[Violation]:
    """받은 일정에서 **보낸 값만으로 판정되는** 위반을 전부 모은다. 빈 목록이면 받아도 된다."""
    items = sorted(parts, key=lambda part: (part.starts_at, part.seq))
    constraints = dict(constraints or {})
    found: list[Violation] = []

    for part in items:
        if part.ends_at is not None and part.ends_at <= part.starts_at:
            found.append(Violation("time_order", (part.seq,),
                                   f"{part.title}: 끝나는 시각({_hm(part.ends_at)})이 시작({_hm(part.starts_at)})보다 빠르다",
                                   "끝나는 시각을 시작 뒤로 보내거나 비워 둔다"))
        found += _place_violations(part)

    for earlier, later in zip(items, items[1:]):
        found += _pair_violations(earlier, later)

    found += _trip_violations(items, constraints, party_size)
    return found


def _place_violations(part: Part) -> list[Violation]:
    from .place_hours import hours_on

    attributes = dict((part.place or {}).get("attributes") or {})
    brk = attributes.get("break")
    found: list[Violation] = []
    # ★`[2026-10-02]` 식당은 요식 원장의 그 시각 판정(`with_ledger`)이 있으면 그것을 본다. None 은 모름 — 거르지 않는다
    ledger = (part.place or {}).get("ledger") or {}
    if ledger.get("open_at_slot") is False:
        when = f"{part.starts_at:%m월 %d일} {_hm(part.starts_at)}"
        found.append(Violation("dining_closed_at_slot", (part.seq,),
                               f"{part.title}: 폐업한 곳이다(요식 원장)" if ledger.get("closed") else
                               f"{part.title}: {when} 에는 영업하지 않는다(요식 원장 — 휴무 · 브레이크 · 영업시간)",
                               "그 시각에 여는 다른 식당으로 바꾸거나 영업하는 시각으로 옮긴다"))
    # ★`[2026-09-28]` 그날의 영업시간 — 요일별 칸(`hours_week`, 관광공사 원문을 옮긴 것)이 먼저, 없으면 하루 한 칸
    #   (`hours`). 전에는 하루 한 칸만 봐서 **쉬는 요일**을 몰랐다(월요일 휴무인 곳이 월요일에 들어갔다)
    today = hours_on(attributes, part.starts_at.date())
    end = part.ends_at or part.starts_at
    if today == "closed":
        found.append(Violation("closed_day", (part.seq,),
                               f"{part.title}: {part.starts_at:%m월 %d일}은 쉬는 날이다",
                               "그날 여는 다른 곳으로 바꾼다"))
    elif today is not None:
        opens, closes = today.opens.strftime("%H:%M"), today.closes.strftime("%H:%M")
        if part.starts_at.time() < today.opens:
            found.append(Violation("before_opening", (part.seq,),
                                   f"{part.title}: {_hm(part.starts_at)} 시작인데 {opens} 에 연다",
                                   f"{opens} 이후로 옮기거나 그 시각에 여는 다른 곳으로 바꾼다"))
        if end.time() > today.closes:
            found.append(Violation("after_closing", (part.seq,),
                                   f"{part.title}: {_hm(end)} 까지인데 {closes} 에 닫는다",
                                   f"{closes} 이전에 끝나게 줄이거나 앞당긴다"))
        if today.last_entry and part.starts_at.time() > today.last_entry:
            last = today.last_entry.strftime("%H:%M")
            found.append(Violation("after_last_entry", (part.seq,),
                                   f"{part.title}: {_hm(part.starts_at)} 시작인데 입장·주문 마감이 {last} 다",
                                   f"{last} 전에 들어가게 앞당기거나 다른 곳으로 바꾼다"))
    if isinstance(brk, (list, tuple)) and len(brk) == 2:
        starts, ends = _clock(brk[0]), _clock(brk[1])
        end = part.ends_at or part.starts_at
        if starts and ends and part.starts_at.time() < ends and end.time() > starts:
            found.append(Violation("break_time", (part.seq,),
                                   f"{part.title}: {brk[0]}~{brk[1]} 은 브레이크 타임인데 그 시간에 걸친다",
                                   f"{brk[1]} 이후로 옮기거나 {brk[0]} 전에 끝낸다"))
    return found


def _pair_violations(earlier: Part, later: Part) -> list[Violation]:
    end = earlier.ends_at or earlier.starts_at
    gap = _minutes(end, later.starts_at)
    if gap < 0:
        return [Violation("overlap", (earlier.seq, later.seq),
                          f"{earlier.title}({_hm(end)} 종료)와 {later.title}({_hm(later.starts_at)} 시작)이 겹친다",
                          f"{later.title} 를 {_hm(end)} 뒤로 옮기거나 앞 항목을 줄인다")]
    if later.kind == "mobility" and later.route:
        planned = _planned_eta(later.route)
        length = _minutes(later.starts_at, later.ends_at) if later.ends_at else None
        if planned and length is not None and length < planned[1]:
            label, eta = planned
            return [Violation("move_too_short", (later.seq,),
                              f"{later.title}: {length}분으로 잡혔는데 계획한 수단({label})은 {eta}분 걸린다",
                              f"{eta}분 이상으로 늘리거나 더 빠른 수단을 고른다")]
        return []
    if earlier.kind != "mobility" and later.kind != "mobility" and gap == 0:
        here, there = _district(earlier), _district(later)
        # ★**구가 다른 것을 아는 경우에만** 건다. 걸어서 옮기는 같은 동네의 0분 간격은 정상이고
        #   (확정 시나리오의 성수동 쇼핑 → 성수 점심이 그렇다), 몇 분 걸리는지는 여기서 모른다.
        if here and there and here != there:
            return [Violation("no_transfer_time", (earlier.seq, later.seq),
                              f"{earlier.title}({here}) 와 {later.title}({there}) 은 지역이 다른데 "
                              f"이동할 시간이 0분이다",
                              "사이에 이동 항목을 넣거나 여유를 둔다")]
    return []


def _district(part: Part) -> str | None:
    attributes = ((part.place or {}).get("attributes") or {})
    return str(attributes.get("district") or "") or None


def _trip_violations(items: list[Part], constraints: Mapping[str, Any],
                     party_size: int | None) -> list[Violation]:
    found: list[Violation] = []
    wanted = constraints.get("payment")
    if wanted:
        for part in items:
            accepted = ((part.place or {}).get("attributes") or {}).get("payment")
            if accepted and str(wanted) not in [str(value) for value in accepted]:
                found.append(Violation("payment_not_accepted", (part.seq,),
                                       f"{part.title}: 결제 조건은 {wanted} 인데 이곳은 {', '.join(accepted)} 만 받는다",
                                       f"{wanted} 을(를) 받는 곳으로 바꾸거나 여행 제약에서 결제 조건을 뺀다"))
    budget = constraints.get("budget_krw")
    if budget is not None:
        heads = party_size or 1
        total = sum(int(((part.place or {}).get("attributes") or {}).get("price_krw") or 0) for part in items) * heads
        if total > int(budget):
            found.append(Violation("over_budget", tuple(part.seq for part in items),
                                   f"예산은 {int(budget):,}원인데 입장·식사 비용 합이 {total:,}원이다({heads}인 기준)",
                                   f"{total - int(budget):,}원만큼 항목을 빼거나 더 싼 곳으로 바꾼다"))
    return found


def parts_from_items(items: Iterable[Any], routes: Mapping[str, Any] | None = None) -> list[Part]:
    """저장된 일정 항목(`itinerary.Item`)을 판정 입력으로. ★등록 때와 **같은 판정기**를 쓰려고 둔다 —
    평가(`eval/runners/travel_scenarios.py`)가 적용된 버전마다 이걸로 다시 본다."""
    parts: list[Part] = []
    for item in items:
        route = (routes or {}).get(str((item.detail or {}).get("route"))) or (item.detail or {}).get("route_def")
        parts.append(Part(seq=item.seq, kind=item.kind, title=item.title, starts_at=item.starts_at,
                          ends_at=item.ends_at, place=item.place, route=route, detail=item.detail))
    return parts


def with_ledger(parts: Iterable[Part],
                lookup: Callable[[list[dict[str, Any]]], Mapping[int, Mapping[str, Any]]]) -> list[Part]:
    """식사 항목에 요식 원장의 그 시각 판정을 붙인다(`place["ledger"]`). `[2026-10-02]`

    ★일정 접수는 원장에서 식당을 **찾기만** 하고 영업 정보는 넘기지 않는다(attributes 에 출처 · 관광공사 ID 뿐).
      그래서 등록 판정이 휴무 · 브레이크 · 영업 종료 뒤 식당을 통과시켰다. 원장의 판정은 휴무 · 명절 · 자정 넘김 ·
      폐업까지 이미 본 값이라 영업시간 문자열을 다시 맞추지 않고 그대로 쓴다.
    ★DB 는 부르는 쪽(`lookup`)이 본다 — 이 파일은 여전히 바깥을 모른다. 못 읽으면 원래 일정 그대로(모름).
    ★원장 가게 ID(`dining_place_uid`)가 먼저다 — 관광공사 ID 가 없는 원장 가게(미쉐린 · 비건 · 할랄 큐레이션)도 본다.
      없으면 일정 접수가 싣는 관광공사 ID(`source_content_id`)로 찾는다.
    """
    parts = list(parts)
    slots = [{"seq": part.seq, **key, "at": part.starts_at, "until": part.ends_at}
             for part in parts if part.kind == "dining" and (key := _ledger_key(part))]
    if not slots:
        return parts
    try:
        verdicts = lookup(slots)
    except Exception:   # noqa: BLE001 — 요식 표가 없는 DB · 연결 실패. 판정하지 않는다(모름)
        return parts
    return [replace(part, place={**part.place, "ledger": dict(verdicts[part.seq])})
            if part.seq in verdicts and part.place else part
            for part in parts]


def _ledger_key(part: Part) -> dict[str, str] | None:
    attributes = (part.place or {}).get("attributes") or {}
    if attributes.get("dining_place_uid"):
        return {"place_uid": str(attributes["dining_place_uid"])}
    if attributes.get("source_content_id"):
        return {"content_id": str(attributes["source_content_id"])}
    return None


__all__ = ["Part", "Violation", "check_itinerary", "parts_from_items", "with_ledger"]
