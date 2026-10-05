# -*- coding: utf-8 -*-
"""일정을 **어떻게 바꿀지 계산만** 한다 — DB 도 바깥 소스도 부르지 않는다.

★왜 뺐나(`[결정 2026-09-17]`). 이 계산은 두 곳이 쓴다.

    시나리오용 여행 버전   trip_watch.py(감시) · trip_desk.py(신고·재요청)  → 계산 후 직접 쓴다
    Case 버전             activity · dining · mobility Team               → 계산 후 제안으로 낸다

  두 벌로 두면 문구와 판단이 조용히 갈린다(RULE §3.3 — 같은 기능의 두 구현 금지).
  그래서 계산은 여기 하나만 두고, 읽기와 쓰기는 부르는 쪽이 한다.
  옮기기 전 원본: `legacy/final_project_cs/app/modules/travel_ops/trip_desk.py`·`trip_watch.py`.

★입력은 **이미 읽은 값**이다(항목·장소·점검 결과). 점검을 다시 해야 하는 자리(대안 재검증)만
  `check` 콜러블을 받는다 — 시나리오 버전은 점검기를 직접, Team 은 읽기 도구를 넣는다.

★결과는 둘 중 하나다.
    ItineraryChange  새 일정 버전으로 쓸 것(바꾼 항목 · 원인 · 통지)
    NoChange         바꾸지 않는 이유(status 는 옮기기 전 반환값과 같은 문자열)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID

from .itinerary import Item
from .replan import (SEATING_BUFFER_MIN, WALK_M_PER_MIN, DiningStateLookup, activity_candidates, alternate_record,
                     apply_google_prices, change_notice, choose, dining_candidates, dining_fits, dining_notice,
                     route_candidates, route_notice, store_candidates)

#: 식당 가격 조회 — 장소 목록 → {place_id: 구글 가격 또는 None}. ★`[2026-09-30]` 대안을 세우는 순간에만 부르고
#:  값은 버린다(`replan.apply_google_prices`). 없으면 구글 가격 없이 예전처럼 세운다.
PriceLookup = Callable[[list[dict[str, Any]]], "dict[str, dict[str, int | None] | None] | None"]

#: 대안 식당을 찾는 반경(미터). ★우리가 고른 값이다 — 도보 약 9분.
DINING_RADIUS_M = 700
#: 새벽에 닫힌 것을 확인한 활동의 대체를 찾는 거리. ★우리가 고른 값(2026-09-28) — 기상 대체(600m, 같은 건물·근처
#: 실내로 피한다)보다 넓다. 원인이 날씨가 아니라 「그날 안 연다」라 근처 실내일 필요가 없고, 대체 후보는 **그날 그 시각에
#: 연다고 아는 곳**만 남아 좁은 반경이면 거의 비었다
ACTIVITY_CLOSED_RADIUS_M = 1500


@dataclass
class ItineraryChange:
    """새 일정 버전 하나. `replacements` 는 바뀌는 항목만, `full_items` 는 되돌림처럼 통째로 쓸 때."""

    reason: str
    causes: list[dict[str, Any]]
    notice: dict[str, Any]
    replacements: dict[UUID, Item] = field(default_factory=dict)
    full_items: list[Item] | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def new_items(self, current: list[Item]) -> list[Item]:
        if self.full_items is not None:
            return list(self.full_items)
        out = [self.replacements.get(item.item_id, item) for item in current]
        return refresh_moves_around(current, out, self.replacements)


def _leg_place(item: Item) -> dict[str, Any] | None:
    p = item.place or {}
    if p.get("latitude") is None or p.get("longitude") is None:
        return None
    return {"key": str(p.get("place_id") or item.place_id), "name": p.get("name") or item.title,
            "lat": float(p["latitude"]), "lon": float(p["longitude"])}


def refresh_moves_around(before: list[Item], after: list[Item], replacements: dict[UUID, Item]) -> list[Item]:
    """☆`[2026-09-29 이동 계산기 문제목록 #44]` 장소가 바뀐 항목의 **바로 앞뒤 이동**을 새 장소 기준으로 다시 만든다.

    앞 판은 장소 항목만 바꾸고 이동 항목은 옛 장소로 가는 경로(탈 노선 uses · 소요)를 그대로 둬, 식당을 바꿨는데
    출발 안내가 옛 경로로 나갔다. 이동 계산기가 켜져 있으면 시간표로 다시 판정하고(출발·도착·경로), 못 하면
    옛 노선 정보를 떼고 직선 어림값 경로(추정 · uses 없음)로 바꾼다 — 옛 경로를 새 장소의 경로처럼 두지 않는다.
    시각은 계산기가 채울 때만 바꾼다(어림값으로 일정을 옮기지 않는다).

    ☆`[2026-09-29 오후 — 실서버 결함]` 앞 판은 새 장소가 옛 장소에서 **걸어갈 거리 안이면 이동을 통째로 건너뛰어**
      제목·목적지가 옛 장소로 남았다(여행 f81afc61… — 일품당프리미엄 → 7 m 옆 금용문으로 바꿨는데 이동 제목이
      「… → 일품당프리미엄」, 출발 알림도 옛 이름). v11 §6-C 재계획 2번(영향 범위는 깨진 항목의 앞뒤 이동까지) ·
      4번(이동은 고른 조합에 맞춰 새로 만들고 옛 경로를 재사용하지 않는다)에 따라 **거리와 상관없이 늘 새 판**을 만든다.
        · 계산기가 켜져 있으면 가까워도 새 장소로 다시 판정한다
        · 계산기가 꺼져 있거나 못 찾을 때 — 걸어갈 거리 안이면 탈 노선(uses)·소요·시각은 둔다(같은 역 권역이라
          여전히 맞다 · 시나리오의 90 m·450 m 교체). 제목·목적지 이름은 새 장소로 바꾸고, 옛 경로를 둔 것을
          `route_basis: "kept_nearby"` 로 드러낸다. 멀면 종전대로 어림값.
    """
    from .replan import distance_m, walk_minutes
    olds = {i.item_id: i for i in before}
    # 「옛 경로를 둘 수 있는 거리」 = 이동 계산기의 도보 상한(guardrails mobility.limits.walk_m.default) — 새 수치를 만들지 않는다
    from .mobility.engine.guardrails import GuardrailMissing, lookup
    try:
        keep_m = float(lookup("mobility.limits.walk_m.default"))
    except GuardrailMissing:
        keep_m = 0.0                                     # 못 읽으면 늘 어림값으로(보수적)

    def moved_far(old_id: UUID, new: Item) -> bool:
        a, b = _leg_place(olds[old_id]) if old_id in olds else None, _leg_place(new)
        if a is None or b is None:
            return True
        return distance_m({"latitude": a["lat"], "longitude": a["lon"]},
                          {"latitude": b["lat"], "longitude": b["lon"]}) > keep_m

    changed = {old_id: moved_far(old_id, new) for old_id, new in replacements.items()
               if new.kind != "mobility" and old_id in olds and olds[old_id].place_id != new.place_id}
    if not changed:
        return after
    seq_sorted = sorted(after, key=lambda i: i.seq)
    far_of_new = {replacements[i].item_id: far for i, far in changed.items()}
    targets: dict[int, bool] = {}                      # 이동 자리 → 옆의 바뀐 장소 중 하나라도 멀리 갔나
    for k, it in enumerate(seq_sorted):
        if it.item_id in far_of_new:
            for j in (k - 1, k + 1):
                if 0 <= j < len(seq_sorted) and seq_sorted[j].kind == "mobility":
                    targets[j] = targets.get(j, False) or far_of_new[it.item_id]
    if not targets:
        return after
    from .mobility.wiring import leg_planner
    engine = leg_planner(None, {})
    fresh: dict[UUID, Item] = {}
    for j, far in sorted(targets.items()):
        move = seq_sorted[j]
        prev = next((i for i in reversed(seq_sorted[:j]) if i.kind != "mobility"), None)
        nxt = next((i for i in seq_sorted[j + 1:] if i.kind != "mobility"), None)
        a, b = (_leg_place(prev) if prev else None), (_leg_place(nxt) if nxt else None)
        if a is None or b is None:
            continue
        got = None
        if engine is not None:
            got, _why = engine(a, b, nxt.starts_at, prev.ends_at or prev.starts_at)
        if got is not None:
            detail = {**move.detail, "route_def": got["route"], "refreshed_for": "place_changed",
                      "route_basis": "rejudged"}
            detail.pop("route", None)
            detail.pop("option", None)
            fresh[move.item_id] = move.replaced_by(place=None, title=f"{a['name']} → {b['name']}",
                                                   starts_at=got["starts_at"], ends_at=got["ends_at"], detail=detail)
        elif not far:
            # 걸어갈 거리 안 — 탈 노선·소요·시각은 두고 이름만 새 장소로(옛 이름이 출발 알림에 나가지 않게)
            suffix = f" · {move.title.split(' · ', 1)[1]}" if " · " in move.title else ""
            detail = {**move.detail, "refreshed_for": "place_changed", "route_basis": "kept_nearby"}
            if isinstance(detail.get("route_def"), dict):
                detail["route_def"] = {**detail["route_def"], "from": a["name"], "to": b["name"]}
            fresh[move.item_id] = move.replaced_by(place=None, title=f"{a['name']} → {b['name']}{suffix}",
                                                   detail=detail)
        else:
            # 일정 짜기의 어림 규칙(planner._transfer_minutes)과 같다 — 도보 환산이 상한을 넘으면 상한 · 「대중교통 권장」
            from .planner import TRANSFER_MAX_MIN
            m = walk_minutes(distance_m({"latitude": a["lat"], "longitude": a["lon"]},
                                        {"latitude": b["lat"], "longitude": b["lon"]}))
            label = "도보 기준 [추정]" if m <= TRANSFER_MAX_MIN else "대중교통 권장 [추정]"
            route = {"from": a["name"], "to": b["name"], "planned": "estimate",
                     "options": [{"id": "estimate", "label": label, "eta_min": min(m, TRANSFER_MAX_MIN), "uses": []}]}
            detail = {**move.detail, "route_def": route, "refreshed_for": "place_changed", "route_basis": "estimate"}
            detail.pop("route", None)
            detail.pop("option", None)
            fresh[move.item_id] = move.replaced_by(place=None, title=f"{a['name']} → {b['name']}", detail=detail)
    return [fresh.get(i.item_id, i) for i in after]


@dataclass
class NoChange:
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


Plan = ItineraryChange | NoChange


# ── 작은 도우미 ────────────────────────────────────────────────
def minutes_between(start: datetime, end: datetime | None, default: int = 60) -> int:
    return default if end is None else int((end - start).total_seconds() // 60)


def object_particle(word: str) -> str:
    """을/를. 마지막 글자가 한글이 아니면 「을(를)」로 둔다 — 틀리게 붙이지 않는다."""
    last = word.strip()[-1:] if word.strip() else ""
    if not ("가" <= last <= "힣"):
        return "을(를)"
    return "을" if (ord(last) - 0xAC00) % 28 else "를"


def round_up_5(moment: datetime) -> datetime:
    extra = (-moment.minute) % 5
    return (moment + timedelta(minutes=extra)).replace(second=0, microsecond=0)


def with_request(cause: dict[str, Any], request_id: str | None) -> dict[str, Any]:
    return {**cause, "request_id": request_id} if request_id else cause


def option_label(item: Item) -> str:
    """이동 항목 제목 「A → B · 수단」에서 수단 부분."""
    return item.title.split(" · ", 1)[1] if " · " in item.title else item.title


def applied_record(item: Item) -> dict[str, Any]:
    """지금 적용된 항목을 「다른 안」 하나로 적는다 — 바꾼 뒤 되돌아올 수 있게."""
    mobility = item.kind == "mobility"
    return {"key": (item.detail.get("option") if mobility else None)
            or (str(item.place_id) if item.place_id else str(item.item_id)),
            "name": option_label(item) if mobility else (item.place or {}).get("name", item.title),
            "place_id": None if mobility or item.place_id is None else str(item.place_id),
            "option": item.detail.get("option") if mobility else None,
            "option_label": option_label(item) if mobility else None,
            "starts_at": item.starts_at.isoformat(),
            "ends_at": item.ends_at.isoformat() if item.ends_at else None,
            "walk_min": None,
            **({"card_payment": item.detail["card_payment"]} if item.detail.get("card_payment") is not None else {}),
            **({"warnings": list(item.detail["warnings"])} if item.detail.get("warnings") else {})}


def title_for(item: Item, name: str) -> str:
    if item.kind == "activity":
        return f"{name} 관람"
    if item.kind == "dining":
        return f"{name} 식사"
    if item.kind == "mobility":
        return f"{item.title.split(' · ', 1)[0]} · {name}"
    return name


def next_after(items: list[Item], item: Item) -> Item | None:
    later = [other for other in items if other.seq > item.seq]
    return min(later, key=lambda other: other.seq) if later else None


def place_before(items: list[Item], item: Item) -> Item | None:
    """이 항목 앞의 **장소 항목**(이동 아님) — 경로를 다시 찾을 때 출발지(#38·#39)."""
    earlier = [other for other in items if other.seq < item.seq and other.kind != "mobility"]
    return max(earlier, key=lambda other: other.seq) if earlier else None


# ── 감시 — 활동 ────────────────────────────────────────────────
def plan_activity_adjustment(*, item: Item, report: dict[str, Any], places: list[dict[str, Any]],
                             check: Callable[..., dict[str, Any]], now: datetime) -> Plan:
    """성립 점검이 `disrupted` 인 활동 항목 — 대안 후보 → 탈락·재검증·사전식 비교로 **하나**."""
    causes = report.get("disruptions", [])
    candidates = activity_candidates(original=item.place, places=places,
                                     start=item.starts_at, end=item.ends_at, causes=causes)
    best, alternates, rejected = choose(
        candidates, lambda c: check(place=c.place, starts_at=item.starts_at))
    if best is None:
        # ★못 풀면 부분 반영하지 않는다(§6-C-5). 사람에게 넘길 재료를 남긴다.
        return NoChange("unresolved", {"causes": causes,
                                       "rejected": {c.name: c.rejected for c in rejected}})
    replay = any(cause.get("mode") == "replay" for cause in causes)
    notice = change_notice(original=item.place, replacement=best.place,
                           start=item.starts_at, causes=causes,
                           alternates=alternates, replay=replay)
    replacement = item.replaced_by(
        place=best.place, title=f"{best.place['name']} 관람",
        detail={"auto_adjusted_at": now.isoformat(),
                "other_options": notice["other_options"],
                "alternates": [alternate_record(c) for c in alternates]})
    return ItineraryChange(reason="auto_adjusted", causes=causes, notice=notice,
                           replacements={item.item_id: replacement},
                           summary={"from": item.place["name"], "to": best.place["name"]})


# ── 감시 — 이동 ────────────────────────────────────────────────
def route_of(item: Item, routes: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """★경로 정의는 항목이 들고 온다(`route_def`). 밖에서 준 `routes` 가 있으면 그것이 먼저다."""
    return (routes or {}).get(str(item.detail.get("route"))) or item.detail.get("route_def")


def route_targets(route: dict[str, Any]) -> list[str]:
    return sorted({target for option in route["options"] for target in option.get("uses", [])})


def planned_option(item: Item, route: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    options = {option["id"]: option for option in route["options"]}
    chosen = str(item.detail.get("option") or route["planned"])
    return chosen, options.get(chosen, {})


def plan_route_adjustment(*, item: Item, following: Item | None, route: dict[str, Any],
                          events: dict[str, Any], now: datetime, previous: Item | None = None) -> Plan:
    """계획한 수단이 쓰는 구간에 사건이 걸렸으면 경로를 다시 고른다. 안 걸렸으면 `clear`.

    ☆`[2026-09-29 이동 계산기 문제목록 #38·#39]` 저장된 후보가 모두 막히면(unresolved) 이동 계산기가 켜져 있고 앞뒤
      장소를 알 때 **사고를 반영해 새 경로를 찾는다**(사건 → 계산기 사고 조건 변환은 뜻이 같은 것만 — wiring).
      출발은 지금·앞 일정 끝 중 늦은 쪽 이후. 옮기지 못한 사건(도로 통제)은 결과에 이름으로 남긴다."""
    chosen, planned = planned_option(item, route)
    hit = {target: events[target] for target in planned.get("uses", []) if target in events}
    if not hit:
        return NoChange("clear")
    causes = [{"category": "route_event", "target": target, **event}
              for target, event in hit.items()]
    candidates = route_candidates(
        route={**route, "planned": chosen}, depart=item.starts_at,
        planned_arrival=item.ends_at or item.starts_at,
        next_start=following.starts_at if following else None, events=events)
    best, alternates, rejected = choose(candidates)
    if best is None:
        rerouted = _engine_reroute(item=item, previous=previous, following=following, events=events, now=now,
                                   causes=causes, rejected=rejected, planned=planned)
        if rerouted is not None:
            return rerouted
        return NoChange("unresolved", {"causes": causes,
                                       "rejected": {c.name: c.rejected for c in rejected}})
    replay = any(cause.get("mode") == "replay" for cause in causes)
    notice = route_notice(route={**route, "planned": chosen}, planned=planned, best=best,
                          alternates=alternates, rejected=rejected, causes=causes,
                          planned_arrival=item.ends_at or item.starts_at,
                          next_title=following.title if following else None, replay=replay)
    replacement = item.replaced_by(
        place=None, title=f"{route['from']} → {route['to']} · {(best.option or {}).get('label')}",
        starts_at=best.starts_at, ends_at=best.ends_at,
        detail={**item.detail, "option": best.key,
                "auto_adjusted_at": now.isoformat(),
                "other_options": notice["other_options"],
                "alternates": [alternate_record(c) for c in alternates]})
    return ItineraryChange(reason="auto_adjusted", causes=causes, notice=notice,
                           replacements={item.item_id: replacement},
                           summary={"from": planned.get("label"),
                                    "to": (best.option or {}).get("label")})


def _engine_reroute(*, item: Item, previous: Item | None, following: Item | None, events: dict[str, Any],
                    now: datetime, causes: list[dict[str, Any]], rejected: list, planned: dict[str, Any]
                    ) -> ItineraryChange | None:
    """저장된 후보가 다 막혔을 때 이동 계산기로 사고를 피하는 새 경로를 찾는다. 못 찾으면 None(종전 unresolved)."""
    if previous is None or following is None:
        return None
    from .mobility import wiring
    from .replan import Candidate
    a, b = _leg_place(previous), _leg_place(following)
    if a is None or b is None:
        return None
    disruptions, unmapped = wiring.disruptions_from_events(events)
    leg = wiring.leg_planner(None, {}, disruptions=disruptions)
    if leg is None:
        return None
    start_floor = max(now, previous.ends_at or previous.starts_at)
    got, _why = leg(a, b, following.starts_at, start_floor)
    if got is None:
        return None
    new_route = got["route"]
    option = next(o for o in new_route["options"] if o["id"] == new_route["planned"])
    best = Candidate(key=option["id"], place=None, changed_items=1, extra_cost_krw=None,
                     shift_minutes=max(0, int((got["ends_at"] - (item.ends_at or item.starts_at)).total_seconds() // 60)),
                     option=dict(option), starts_at=got["starts_at"], ends_at=got["ends_at"])
    notice = route_notice(route=new_route, planned=planned, best=best, alternates=[], rejected=rejected,
                          causes=causes, planned_arrival=item.ends_at or item.starts_at,
                          next_title=following.title, replay=False)
    detail = {**item.detail, "route_def": new_route, "option": best.key, "auto_adjusted_at": now.isoformat(),
              "other_options": notice["other_options"], "alternates": [],
              "rerouted_by": "mobility_engine", **({"unmapped_events": unmapped} if unmapped else {})}
    detail.pop("route", None)                    # 새 경로 정의를 들고 간다 — 옛 routes 키를 가리키지 않는다
    replacement = item.replaced_by(place=None, title=f"{a['name']} → {b['name']} · {option.get('label')}",
                                   starts_at=got["starts_at"], ends_at=got["ends_at"], detail=detail)
    return ItineraryChange(reason="auto_adjusted", causes=causes, notice=notice,
                           replacements={item.item_id: replacement},
                           summary={"from": planned.get("label"), "to": option.get("label"),
                                    "rerouted_by": "mobility_engine"})


# ── 고객 신고 — 식당 ───────────────────────────────────────────
def _dining_change(meal: Item, best, alternates, notice: dict[str, Any], *,
                   reason: str = "customer_report") -> ItineraryChange:
    replacement = meal.replaced_by(
        place=best.place, title=f"{best.name} 식사", starts_at=best.starts_at,
        ends_at=best.ends_at,
        detail={"other_options": notice["other_options"],
                **({"customer_reported": True} if reason == "customer_report" else {}),
                **({"price_compare": best.price_compare} if best.price_compare else {}),
                **({"warnings": list(best.warnings)} if best.warnings else {}),
                **({"card_payment": best.card_payment} if best.card_payment is not None else {}),
                "alternates": [alternate_record(c) for c in alternates]})
    # ★가격은 비교 결과만(`won` · `same_or_lower` · `higher` · `unknown`) — 구글 가격대 원값은 남기지 않는다
    summary = {"to": best.name, **({"price": best.price_compare} if best.price_compare else {}),
               **({"price_basis": best.price_basis} if best.price_basis else {})}
    return ItineraryChange(reason=reason, causes=notice["causes"], notice=notice,
                           replacements={meal.item_id: replacement}, summary=summary)


def plan_delay(*, trip: dict[str, Any], items: list[Item], places: list[dict[str, Any]],
               at: datetime, minutes: int, message: str, request_id: str | None,
               price_lookup: PriceLookup | None = None, state_lookup: DiningStateLookup | None = None) -> Plan:
    """「N분 늦는다」. 다음 식사 항목이 그 도착 시각에 성립하는지 보고, 안 되면 바꾼다."""
    meal = next((i for i in items if i.kind == "dining" and i.starts_at >= at), None)
    if meal is None or meal.place is None:
        return NoChange("no_meal", {"message": "늦어지는 시각 뒤에 식사 일정이 없다"})
    arrival = meal.starts_at + timedelta(minutes=minutes)
    duration = minutes_between(meal.starts_at, meal.ends_at)
    place_id = str(meal.place["place_id"])
    states = (state_lookup([{"place_id": place_id, "at": arrival,
                             "until": arrival + timedelta(minutes=duration)}]) if state_lookup else None) or {}
    fits, why = dining_fits(meal.place, arrival, duration, state=states.get(place_id))
    cause = with_request({"category": "customer_report", "type": "delay", "minutes": minutes,
                          "message": message, "evidence": "고객 신고"}, request_id)
    if fits is True:
        return NoChange("still_fits", {"arrival": arrival.isoformat()})
    if fits is None:
        # 확인하지 못했다는 이유만으로 이미 정한 식당을 바꾸지 않는다.
        return NoChange("needs_check", {
            "arrival": arrival.isoformat(),
            "message": f"{meal.place['name']}의 {arrival:%H:%M} 도착 시 영업 여부는 확인이 필요해요. "
                       "기존 일정은 바꾸지 않았어요.",
            "warnings": [why]})
    following = next((i for i in items if i.seq > meal.seq), None)
    candidates = dining_candidates(
        original=meal.place, places=places, arrival=arrival, minutes=duration,
        constraints=trip.get("constraints") or {}, radius_m=DINING_RADIUS_M,
        next_start=following.starts_at if following else None, state_lookup=state_lookup)
    if price_lookup is not None:
        apply_google_prices(candidates, original=meal.place, lookup=price_lookup)
    best, alternates, rejected = choose(candidates)
    if best is None:
        return NoChange("unresolved", {"reason": why,
                                       "rejected": {c.name: c.rejected for c in rejected}})
    notice = dining_notice(
        original=meal.place, best=best, alternates=alternates,
        reason=f"점심 도착이 {arrival:%H:%M}(으)로 늦어져 {meal.place['name']}은 {why}",
        cause=cause, after=None, constraint_note="브레이크타임 없는")
    return _dining_change(meal, best, alternates, notice)


def plan_closed(*, trip: dict[str, Any], items: list[Item], places: list[dict[str, Any]],
                at: datetime, message: str, request_id: str | None,
                price_lookup: PriceLookup | None = None, state_lookup: DiningStateLookup | None = None) -> Plan:
    """「오늘 임시휴무」. 지금 식사 항목을 걸어갈 수 있는 대체 식당으로 바꾼다."""
    meal = next((i for i in items if i.kind == "dining"
                 and i.starts_at <= at < (i.ends_at or i.starts_at + timedelta(hours=1))), None)
    if meal is None or meal.place is None:
        return NoChange("no_meal", {"message": "지금 시각에 식사 일정이 없다"})
    duration = minutes_between(meal.starts_at, meal.ends_at)
    following = next((i for i in items if i.seq > meal.seq), None)
    cause = with_request({"category": "customer_report", "type": "closed_today",
                          "message": message, "evidence": "고객 신고 — 현장 안내문"},
                         request_id)
    # 후보마다 도보 시간이 달라 입장 시각도 다르다 — 먼저 거리로 입장 시각을 잡는다.
    candidates = dining_candidates(
        original=meal.place, places=places, arrival=at, minutes=duration,
        constraints=trip.get("constraints") or {}, radius_m=DINING_RADIUS_M,
        next_start=following.starts_at if following else None, state_lookup=state_lookup,
        arrival_for=lambda walk: round_up_5(at + timedelta(minutes=walk + SEATING_BUFFER_MIN)))
    if price_lookup is not None:
        apply_google_prices(candidates, original=meal.place, lookup=price_lookup)
    best, alternates, rejected = choose(candidates)
    if best is None:
        return NoChange("unresolved", {"rejected": {c.name: c.rejected for c in rejected}})
    after = None
    later_activity = next((i for i in items if i.seq > meal.seq and i.kind == "activity"), None)
    if later_activity and best.ends_at and best.ends_at <= later_activity.starts_at:
        after = later_activity.place["name"] if later_activity.place else later_activity.title
    payment = (trip.get("constraints") or {}).get("payment")
    notice = dining_notice(
        original=meal.place, best=best, alternates=alternates,
        reason=f"{meal.place['name']}이(가) 오늘 임시휴무라고 알려 주셨습니다",
        cause=cause, after=after,
        constraint_note="카드 결제가 가능한" if payment == "card" else None)
    return _dining_change(meal, best, alternates, notice)


# ── 새벽 확인 — 그날 그 시각에 안 연다 (D-020, 2026-09-25) ──────────
def plan_closed_on_day(*, trip: dict[str, Any], items: list[Item], places: list[dict[str, Any]],
                       meal: Item, source: str, detail: str, checked_at: datetime,
                       exclude: set[str] = frozenset(), price_lookup: PriceLookup | None = None,
                       state_lookup: DiningStateLookup | None = None) -> Plan:
    """새벽 확인에서 **계획한 시각에 안 여는** 식당 — 같은 시각에 근처 대체 식당으로 바꾼다.

    ★고객 신고(`plan_closed`)와 다르다 — 그쪽은 고객이 **지금 가게 앞에** 있어 걸어갈 시간만큼 입장을
      뒤로 민다. 여기는 새벽이라 고객이 아직 나서지 않았다 → **계획한 입장 시각 그대로** 찾는다.
    ★근거는 바깥 소스의 판정이다(고객 문장이 아니다). 원인에 소스와 확인 시각을 남긴다.
    """
    if meal.place is None:
        return NoChange("no_meal", {"message": "장소가 없는 식사 일정이다"})
    duration = minutes_between(meal.starts_at, meal.ends_at)
    following = next((i for i in items if i.seq > meal.seq and i.kind != "mobility"), None)
    cause = {"category": "place_closed", "type": "closed_on_day", "source": source,
             "checked_at": checked_at.isoformat(), "detail": detail,
             "evidence": f"{source} 새벽 확인 — {detail}"}
    candidates = dining_candidates(
        original=meal.place, places=places, arrival=meal.starts_at, minutes=duration,
        constraints=trip.get("constraints") or {}, radius_m=DINING_RADIUS_M,
        next_start=following.starts_at if following else None, exclude=set(exclude), state_lookup=state_lookup)
    if price_lookup is not None:
        apply_google_prices(candidates, original=meal.place, lookup=price_lookup)
    best, alternates, rejected = choose(candidates)
    if best is None:
        return NoChange("unresolved", {"causes": [cause],
                                       "rejected": {c.name: c.rejected for c in rejected}})
    payment = (trip.get("constraints") or {}).get("payment")
    notice = dining_notice(
        original=meal.place, best=best, alternates=alternates,
        reason=(f"{meal.place['name']}이(가) {meal.starts_at:%m월 %d일 %H:%M}에 영업하지 않는 것으로 "
                f"새벽에 확인했습니다({detail})"),
        cause=cause, after=None,
        constraint_note="카드 결제가 가능한" if payment == "card" else None)
    return _dining_change(meal, best, alternates, notice, reason="auto_adjusted")


def plan_activity_closed_on_day(*, items: list[Item], places: list[dict[str, Any]], item: Item, source: str,
                                detail: str, checked_at: datetime, exclude: set[str] = frozenset()) -> Plan:
    """★`[2026-09-28]` 새벽 확인에서 **그날 안 여는** 활동 — 같은 시각에 근처에서 **그 시각에 연다고 아는** 활동으로.

    ☆왜 — 새벽 확인이 식당만 봤다. 영업시간을 모르는 활동이 쉬는 날에 들어가도 당일까지 아무도 몰랐다
      (「13:00~17:00 · 일~목 휴무」인 곳이 월요일 09:00). 사용자 결정: 최종 판정은 당일 새벽 구글 확인이 한다.
    ★가격을 모르는 후보도 받는다(결정 15 — 불확실해도 하나를 고른다). 기상 대체(`plan_activity_adjustment`)는
      추가 비용을 계산하려고 가격 모름을 탈락시키지만, 여기서 그러면 관광공사 장소가 전부 빠진다(가격 칸이 없다).
      **그 시각 영업**은 그대로 요구한다 — 닫힌 곳을 닫힌 곳으로 바꾸지 않는다.
    """
    if item.place is None:
        return NoChange("no_place", {"message": "장소가 없는 활동이다"})
    cause = {"category": "place_closed", "type": "closed_on_day", "source": source,
             "checked_at": checked_at.isoformat(), "detail": detail,
             "evidence": f"{source} 새벽 확인 — {detail}"}
    candidates = [c for c in activity_candidates(original=item.place, places=places, start=item.starts_at,
                                                 end=item.ends_at, causes=[cause],
                                                 radius_m=ACTIVITY_CLOSED_RADIUS_M)
                  if str(c.place["place_id"]) not in {str(x) for x in exclude}]
    for candidate in candidates:
        candidate.rejected = [r for r in candidate.rejected if not r.startswith("가격을 몰라")]
    best, alternates, rejected = choose(candidates)
    if best is None:
        return NoChange("unresolved", {"causes": [cause],
                                       "rejected": {c.name: c.rejected for c in rejected}})
    notice = change_notice(original=item.place, replacement=best.place, start=item.starts_at,
                           causes=[cause], alternates=alternates, replay=False)
    notice["text"] = (f"{item.place['name']}이(가) {item.starts_at:%m월 %d일 %H:%M}에 운영하지 않는 것으로 새벽에 "
                      f"확인했습니다({detail}). 같은 시각 {best.place['name']}(으)로 바꿨습니다.")
    replacement = item.replaced_by(
        place=best.place, title=title_for(item, best.place["name"]),
        detail={"auto_adjusted_at": checked_at.isoformat(), "other_options": notice["other_options"],
                "alternates": [alternate_record(c) for c in alternates]})
    return ItineraryChange(reason="auto_adjusted", causes=[cause], notice=notice,
                           replacements={item.item_id: replacement},
                           summary={"from": item.place["name"], "to": best.place["name"]})


# ── 고객 신고 — 품절(일정은 안 바꾼다) ─────────────────────────
def plan_nearby_store(*, items: list[Item], places: list[dict[str, Any]], at: datetime,
                      products: list[str], message: str, request_id: str | None) -> dict[str, Any]:
    """품절 상품을 **취급할 만한** 매장을 귀가 동선에서 고른다. 일정은 안 바꾼다.

    ★재고는 확인하지 않는다 — 재고 소스가 없다(§7-C 3단계). 그래서 답에
      `[미확인]` 을 붙인다. 「있다」고 말하지 않는다.
    """
    here_item = next((i for i in items if i.kind == "activity"
                      and i.starts_at <= at < (i.ends_at or at)), None)
    home = next((i.place for i in items if i.place and i.place.get("kind") == "lodging"), None)
    if here_item is None or here_item.place is None or home is None:
        return {"status": "unresolved", "message": "지금 위치나 숙소를 알 수 없다"}
    candidates = store_candidates(here=here_item.place, home=home, places=places,
                                  products=products, at=at)
    best, alternates, rejected = choose(candidates)
    if best is None:
        return {"status": "unresolved", "rejected": {c.name: c.rejected for c in rejected}}
    detour = (best.option or {}).get("detour_m") or 0
    # ★100m 안쪽이면 「돌아가는 거리」를 숫자로 말하지 않는다 — 9m·1분 같은 값은
    #   정밀해 보이지만 좌표 근사에서 나온 잡음이다.
    extra = ("거의 돌아가지 않아도 됩니다" if detour < 100 else
             f"돌아가는 거리 약 {detour}m 추가, 도보 약 {max(1, round(detour / WALK_M_PER_MIN))}분")
    listed = ", ".join(products)
    text = (f"{best.name}이(가) 호텔로 돌아가는 동선 위에 있습니다({extra}). "
            f"{listed}{object_particle(listed)} 취급하는 매장이지만 지금 재고는 확인하지 "
            f"못했습니다[미확인].")
    return {"status": "answered", "text": text, "recommendation": best.name,
            "stock": "unverified", "other_options": [c.name for c in alternates],
            "rejected": {c.name: c.rejected for c in rejected},
            "cause": with_request({"category": "customer_report", "type": "stock_out",
                                   "products": products, "message": message}, request_id)}


# ── 재요청 ① — 다른 안으로 ─────────────────────────────────────
def plan_swap(*, trip_version: int, base_version: int, items: list[Item],
              places_by_id: dict[str, dict[str, Any]], item_id: UUID, choice: str | None,
              message: str | None, request_id: str | None,
              check: Callable[..., dict[str, Any]] | None) -> Plan:
    """적용된 안을 들고 있던 「다른 안」으로 바꾼다. 원래 안은 다시 「다른 안」이 된다.

    ★고객이 **본 버전**(`base_version`)을 기준으로만 바꾼다. 그 사이 일정이 또
      바뀌었으면 `stale` — 고객이 보지 못한 일정 위에 요청을 얹지 않는다.
    """
    if trip_version != base_version:
        return NoChange("stale", {"version": trip_version})
    current = next((i for i in items if i.item_id == item_id), None)
    if current is None:
        return NoChange("not_found")
    alternates = list(current.detail.get("alternates") or [])
    if not alternates:
        return NoChange("no_alternate")
    pick = alternates[0] if choice is None else next(
        (a for a in alternates if a["key"] == choice), None)
    if pick is None:
        return NoChange("unknown_choice", {"choices": [a["key"] for a in alternates]})
    starts = datetime.fromisoformat(pick["starts_at"]) if pick.get("starts_at") else current.starts_at
    ends = datetime.fromisoformat(pick["ends_at"]) if pick.get("ends_at") else current.ends_at
    following = next((i for i in items if i.seq > current.seq), None)
    if following and ends and ends > following.starts_at:
        return NoChange("conflicts_next", {"next": following.title})
    place = places_by_id.get(str(pick["place_id"])) if pick.get("place_id") else None
    if pick.get("place_id") and place is None:
        return NoChange("not_found")
    rechecked = None
    if place is not None and current.kind == "activity" and check is not None:
        # ★계산한 뒤로 시간이 흘렀다 — 그 시각에 다시 점검한다.
        report = check(place=place, starts_at=starts)
        rechecked = (report or {}).get("verdict")
        if rechecked != "clear":
            return NoChange("alternate_invalid", {"verdict": rechecked, "report": report})
    applied = applied_record(current)
    remaining = [a for a in alternates if a is not pick] + [applied]
    name = pick.get("option_label") or pick["name"]
    detail = {**current.detail, "alternates": remaining,
              "other_options": [a["name"] for a in remaining], "customer_requested": True}
    detail.pop("warnings", None)
    if pick.get("warnings"):
        detail["warnings"] = list(pick["warnings"])
    if current.kind == "mobility":
        detail["option"] = pick.get("option") or pick["key"]
    cause = with_request({"category": "customer_request", "type": "alternate",
                          "from": applied["name"], "to": name, "message": message},
                         request_id)
    text = (f"요청하신 대로 {applied['name']} 대신 {name}(으)로 바꿨습니다"
            f"({starts:%H:%M} 시작).")
    warnings = [f"{name}: {warning}" for warning in pick.get("warnings") or []]
    if warnings:
        text += " " + " ".join(warnings)
    notice = {"text": text, "language": "ko", "causes": [cause],
              "changed": {"from": applied["name"], "to": name, "at": starts.isoformat()},
              "other_options": [a["name"] for a in remaining], "replay": False,
              **({"warnings": warnings} if warnings else {})}
    replacement = current.replaced_by(place=place, title=title_for(current, name),
                                      detail=detail, starts_at=starts, ends_at=ends)
    return ItineraryChange(reason="customer_request", causes=[cause], notice=notice,
                           replacements={current.item_id: replacement},
                           summary={"to": name, "rechecked": rechecked})


# ── 재요청 ② — 되돌려 줘 ───────────────────────────────────────
def plan_rollback(*, trip_version: int, base_version: int, current_items: list[Item],
                  old_items: list[Item], to_version: int, message: str | None,
                  request_id: str | None) -> Plan:
    """옛 버전의 항목을 **새 버전으로 다시 쓴다**(append-only — 옛 버전을 지우지 않는다).

    ★되살린 항목은 `customer_pinned` 로 표시한다. 감시 루프가 다음 틱에 같은 원인으로
      다시 바꾸면 되돌림이 무의미해진다 — 고객이 알고 고른 것이다.
    """
    if trip_version != base_version:
        return NoChange("stale", {"version": trip_version})
    if not 1 <= to_version < trip_version:
        return NoChange("invalid_version", {"version": trip_version})
    current_ids = {i.item_id for i in current_items}
    restored = []
    for item in old_items:
        if item.item_id not in current_ids:
            item.detail = {**item.detail, "customer_pinned": True}
            restored.append(item.title)
    cause = with_request({"category": "customer_request", "type": "rollback",
                          "to_version": to_version, "message": message}, request_id)
    text = f"요청하신 대로 일정을 버전 {to_version} 상태로 되돌렸습니다."
    if restored:
        text += " 되돌린 항목: " + ", ".join(restored) + "."
    notice = {"text": text, "language": "ko", "causes": [cause],
              "changed": {"rollback_to": to_version, "restored": restored},
              "other_options": [], "replay": False}
    return ItineraryChange(reason="rollback", causes=[cause], notice=notice,
                           full_items=list(old_items), summary={"restored": restored})


__all__ = ["DINING_RADIUS_M", "ItineraryChange", "NoChange", "Plan", "applied_record",
           "minutes_between", "next_after", "object_particle", "plan_activity_adjustment",
           "plan_closed", "plan_closed_on_day", "plan_delay", "plan_nearby_store", "plan_rollback",
           "plan_route_adjustment", "plan_swap", "planned_option", "route_of", "route_targets",
           "title_for", "with_request"]
