# -*- coding: utf-8 -*-
"""새벽 3시 영업 확인 — 그날 식사·활동 일정이 **계획한 시각에 여는지** 구글 장소로 본다. `[2026-09-25]` D-020

★`[2026-09-28]` 활동도 본다(사용자 결정). 계획은 관광공사 운영시간 원문을 옮긴 값(`place_hours.py`)으로 짜고,
  **최종 판정은 여기서 구글이** 한다. 활동이 닫혔으면 `plan_activity_closed_on_day` 가 같은 시각 근처 활동으로 바꾼다.

★팀 결정(2026-09-24)
  - 식당은 **새벽 3시에만** 확인한다(첫 심야버스 03:30 기준). 그 뒤로는 따로 부르지 않는다 —
    당일 문제는 **고객 신고**로 받는다. 기상·교통의 3분 감시는 그대로 돈다(다른 소스).
  - 새벽에 모은 문제는 **하루 시작 알림**에 실린다 — 그 알림이 「어제 이후 바뀐 일정」·「답을 기다리는
    일정」을 센다(`trip_reminders.day_phrase`). 그래서 이 작업은 하루 시작 알림(기본 08:00) **전에** 끝난다.

★도는 창 — `travel.dawn_check.start`(03:00) ~ `travel.dawn_check.until`(08:00). 창 밖이면 아무것도 안 부른다.
  창 안에서는 1분마다 돌아도 **항목·날짜마다 한 번만** 부른다(`place_open_checks` UNIQUE).

★안 열면 — 같은 시각에 근처 대체 식당을 계산하고(`plan_closed_on_day`) **바꾸는 한 문**
  (`pending.apply_or_ask`)으로 보낸다: 「먼저 물어봐줘」·「변경 안 할 일정」이면 묻고, 아니면 바로 바꾼다.

★모름을 지어내지 않는다
  - 구글을 **못 불렀으면**(키 오류·시간 초과) `fatal` 로 세고 기록하지 않는다 → 창 안에서 다시 부른다
  - 구글에 **맞는 장소가 없으면**(이름·좌표가 안 맞는다) `unmatched`, **영업시간 칸이 없으면** `unknown` 으로
    기록하고 센다 — 「연다」고 하지 않는다
  - 키가 없으면 `disabled` 로 답한다(조용히 넘기지 않는다)

★약관 — 구글 `place_id` 와 **판정·확인 시각**만 남긴다. 영업시간 원문은 저장하지 않는다(마이그레이션 026).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .itinerary import Item, TripStore, visible_to
from .dining.ledger import dining_states
from .itinerary_changes import ItineraryChange, NoChange, plan_activity_closed_on_day, plan_closed_on_day
from .pending import apply_or_ask

KST = ZoneInfo("Asia/Seoul")
PROVIDER = "google_places"


@dataclass
class DawnResult:
    disabled: str | None = None
    outside_window: bool = False
    checked: int = 0
    open: int = 0
    closed: list[dict[str, Any]] = field(default_factory=list)
    unknown: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    adjusted: list[dict[str, Any]] = field(default_factory=list)
    asked: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    fatal: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, Any]:
        return {"disabled": self.disabled, "outside_window": self.outside_window, "checked": self.checked,
                "open": self.open, "closed": len(self.closed), "unknown": len(self.unknown),
                "unmatched": len(self.unmatched), "adjusted": len(self.adjusted), "asked": len(self.asked),
                "unresolved": len(self.unresolved), "fatal": len(self.fatal)}


def _clock(text: str) -> time:
    hour, minute = str(text).split(":")
    return time(int(hour), int(minute))


class DawnCheck:
    def __init__(self, *, store: TripStore, connection_factory: Callable[[], Any],
                 clock: Callable[[], datetime], source: Any | None,
                 start: time | None = None, until: time | None = None) -> None:
        self.store, self._connect, self.clock, self.source = store, connection_factory, clock, source
        if start is None or until is None:
            from app.core.settings import get_guardrails

            guard = get_guardrails()
            start = start or _clock(guard.get("travel.dawn_check.start"))
            until = until or _clock(guard.get("travel.dawn_check.until"))
        self.start, self.until = start, until

    def tick(self) -> DawnResult:
        result = DawnResult()
        if self.source is None:
            # ★실행기가 이 문구를 표준출력(JSON)으로 낸다 — Windows 콘솔(cp949)이 못 찍는 긴 줄표를 쓰지 않는다
            result.disabled = "ACOP_GOOGLE_MAPS_API_KEY 가 비어 있다. 새벽 식당 확인을 돌리지 않았다"
            return result
        now = self.clock().astimezone(KST)
        if not self.start <= now.time() < self.until:
            result.outside_window = True        # ★3시 이후 하루 중에는 식당을 부르지 않는다(팀 결정)
            return result
        day = now.date()
        day_end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=KST)
        with self._connect() as conn:
            # ★`[2026-09-28]` 활동도 본다(사용자 결정 — 영업시간은 관광공사 원문을 옮겨 계획하고 **최종 판정은
            #   새벽 구글 확인**). 전에는 식당만 봐서, 쉬는 날에 들어간 활동을 당일까지 아무도 몰랐다
            meals = [(trip_id, item) for trip_id, item in self.store.due(conn, start=now, end=day_end)
                     if item.kind in ("dining", "activity") and item.place is not None]
            done = self._checked_items(conn, day)
            places = self.store.places(conn, every_trip=True)
        for trip_id, meal in meals:
            if meal.item_id in done:
                continue
            try:
                self._check(trip_id, meal, day, visible_to(places, trip_id), result)
            except _Retry:
                result.fatal.append({"trip_id": str(trip_id), "item": meal.title, "failed": "stale"})
        return result

    # ── 한 항목 ─────────────────────────────────────────────────
    def _check(self, trip_id, meal: Item, day, places: list[dict[str, Any]], result: DawnResult) -> None:
        entry = {"trip_id": str(trip_id), "item": meal.title, "place": meal.place.get("name")}
        with self._connect() as conn:
            google_id = self._provider_id(conn, meal.place["place_id"])
        if google_id is None:
            found = self.source.find_place_id(name=meal.place["name"], latitude=meal.place["latitude"],
                                              longitude=meal.place["longitude"])
            if found is None:
                result.fatal.append({**entry, "failed": "find_place_id"})
                return
            if found == "unmatched":
                self._record(trip_id, meal, day, "unmatched", "구글에서 이 이름·좌표의 장소를 찾지 못했다")
                result.checked += 1
                result.unmatched.append(entry)
                return
            google_id, meters = found
            with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute("INSERT INTO place_provider_ids (tenant_id, place_id, provider, provider_place_id, "
                            "match_distance_m) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (self.store.tenant_id, meal.place["place_id"], PROVIDER, google_id, meters))
        end = meal.ends_at or meal.starts_at + timedelta(hours=1)
        verdict = self.source.open_verdict(google_id, start=meal.starts_at, end=end)
        if verdict is None:
            result.fatal.append({**entry, "failed": "open_verdict"})
            return
        state, detail = verdict
        result.checked += 1
        if state != "closed":
            self._record(trip_id, meal, day, state, detail)
            if state == "open":
                result.open += 1
            else:
                result.unknown.append({**entry, "detail": detail})
            return
        result.closed.append({**entry, "detail": detail})
        checked_at = self.clock()
        with self._connect() as conn, conn.transaction():
            trip, items = self.store.latest(conn, trip_id)
            if meal.kind == "activity":
                plan = plan_activity_closed_on_day(items=items, places=places, item=meal, source=PROVIDER,
                                                   detail=detail, checked_at=checked_at,
                                                   exclude=self._closed_places(conn, day))
            else:
                def replan(candidates):
                    return plan_closed_on_day(trip=trip, items=items, places=candidates, meal=meal, source=PROVIDER,
                                              detail=detail, checked_at=checked_at,
                                              exclude=self._closed_places(conn, day),
                                              price_lookup=self._price_lookup(),
                                              state_lookup=lambda slots: dining_states(conn, self.store.tenant_id,
                                                                                       slots))
                plan = replan(places)
                # ★`[2026-10-01]` 대체 후보가 코어 장소에서만 나와 비던 것 — 근처 원장 가게를 그 여행 전용으로 들여놓고 다시
                if isinstance(plan, ItineraryChange) or (isinstance(plan, NoChange) and plan.status == "unresolved"):
                    from .dining import nearby

                    more = nearby.add_nearby(conn, self.store, trip_id, [meal], places)
                    if more != places:
                        plan = replan(more)
            self._insert_check(conn, trip_id, meal, day, "closed", detail)
            if isinstance(plan, NoChange):
                result.unresolved.append({**entry, "status": plan.status})
                return
            outcome = apply_or_ask(conn, store=self.store, trip_id=trip_id, item_id=meal.item_id, plan=plan)
            if outcome["status"] == "stale":
                # ★그 사이 일정이 움직였다 — 판정도 기록하지 않고 되돌린다. 창 안에서 다시 본다
                raise _Retry()
        if outcome["status"] == "adjusted":
            result.adjusted.append({**entry, "version": outcome["version"], **plan.summary})
        elif outcome["status"] == "asked":
            result.asked.append({**entry, "already": outcome.get("already", False)})

    # ── 저장 ────────────────────────────────────────────────────
    def _record(self, trip_id, meal: Item, day, verdict: str, detail: str) -> None:
        with self._connect() as conn, conn.transaction():
            self._insert_check(conn, trip_id, meal, day, verdict, detail)

    def _insert_check(self, conn, trip_id, meal: Item, day, verdict: str, detail: str) -> None:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO place_open_checks (tenant_id, trip_id, item_id, day, place_id, verdict, "
                        "detail, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (tenant_id, item_id, day) DO NOTHING",
                        (self.store.tenant_id, trip_id, meal.item_id, day, meal.place["place_id"], verdict,
                         detail, PROVIDER))

    def _checked_items(self, conn, day) -> set:
        with conn.cursor() as cur:
            cur.execute("SELECT item_id FROM place_open_checks WHERE tenant_id=%s AND day=%s",
                        (self.store.tenant_id, day))
            return {row[0] for row in cur.fetchall()}

    def _closed_places(self, conn, day) -> set[str]:
        """★오늘 안 연다고 확인한 곳 — 대체 후보에서 뺀다(닫힌 곳으로 바꾸지 않게)."""
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT place_id FROM place_open_checks WHERE tenant_id=%s AND day=%s "
                        "AND verdict='closed'", (self.store.tenant_id, day))
            return {row[0] for row in cur.fetchall()}

    def _price_lookup(self):
        """대체 식당의 구글 가격(`[2026-09-30]`). 소스가 가격을 못 주면 `None` — 구글 가격 없이 세운다.
        ★값은 비교에만 쓰고 버린다(구글 약관). 짝 표에 없는 식당은 「모름」이다."""
        if not hasattr(self.source, "price"):
            return None
        from app.infrastructure.travel.google_places import prices_for

        return lambda places: prices_for(self._connect, self.store.tenant_id, self.source,
                                         [str(p["place_id"]) for p in places])

    def _provider_id(self, conn, place_id) -> str | None:
        with conn.cursor() as cur:
            cur.execute("SELECT provider_place_id FROM place_provider_ids WHERE tenant_id=%s AND place_id=%s "
                        "AND provider=%s", (self.store.tenant_id, place_id, PROVIDER))
            row = cur.fetchone()
        return row[0] if row else None


class _Retry(Exception):
    """판정을 기록하지 않고 되돌린다 — 트랜잭션 밖에서 삼킨다."""


__all__ = ["DawnCheck", "DawnResult", "PROVIDER"]
