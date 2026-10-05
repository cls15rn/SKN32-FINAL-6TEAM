# -*- coding: utf-8 -*-
"""고객이 먼저 알린 사건과 재요청 — 늦음 · 도착해 보니 휴무 · 「근처 다른 곳 없어?」 ·
「다른 안으로 바꿔 줘」 · 「되돌려 줘」 — **시나리오용 여행 버전**의 창구.

★확정 시나리오의 여섯 사건 중 셋(요식-P3 · 요식-P7 · 액-08)은 **고객이 겪거나 물어서**
  시작한다(설계대응 §0). 감시 루프가 못 보는 것이다 — 식당 당일 휴무·매장 재고는
  소스가 없다.

★재요청 둘(v11 §1 접점 · DoD-8/9) — 최고 안 하나를 먼저 적용하고 알린 뒤, 마음에 안
  들면 고객이 **다른 안으로 바꾸거나** **옛 버전으로 되돌린다.** 알림을 승인 요청으로
  쓰지 않기 때문에 이 두 길이 있어야 성립한다.

★`[2026-09-17]` **계산은 `itinerary_changes.py` 가 한다.** 이 파일은 읽고 → 계산을 부르고 →
  쓴다. Case 버전의 Team 이 같은 계산을 쓰므로 두 버전의 문구·판단이 갈리지 않는다.
  옮기기 전 원본: `legacy/final_project_cs/app/modules/travel_ops/trip_desk.py`.
  `[정정 2026-09-17]` 이 머리말은 「자유 문장 배선 `[미구현]`」·「입구는
  `app/presentation/api/trips.py`」라고 적혀 있었다 — 배선은 `trip_messages.py` 에 있고
  입구 파일은 `trip_api.py` 다.

★고객 신고는 **근거가 고객 문장**이다. 「고객이 휴무라고 알렸다」를 근거로 남기고,
  우리 확인처럼 말하지 않는다(v11 §4-D).

★요청 id 를 원인 칸에 남긴다 — 같은 신고를 두 번 받아 두 번 고치지 않게
  (`TripStore.version_for_request`).

★`[2026-09-25]` **고객 신고(늦음 · 휴무)도 감시와 같은 판정 문을 지난다**(`pending.decide`, D-020).
  신고는 「문제가 생겼다」이지 「이 안으로 바꿔 달라」가 아니다 — 어느 안으로 바꿀지는 우리가 고른다.
  그래서 설문 15번 「먼저 물어봐줘」면 **바꾸지 않고 안 1·2·3을 보이며 묻고**, 「변경 안 할 일정」이
  걸리면 15번 답과 상관없이 묻는다. 그 밖은 지금처럼 최고 안을 바로 적용한다.
  ★고객이 **직접 고른 것**(다른 안으로 바꾸기 · 되돌리기)은 판정 문을 지나지 않는다 — 그 자체가 답이다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID

from .itinerary import Item, StaleItinerary, TripStore
from .itinerary_changes import (DINING_RADIUS_M, ItineraryChange, NoChange, Plan, plan_closed,
                                plan_delay, plan_nearby_store, plan_rollback, plan_swap)
from .pending import PendingStore, decide, options_from, proposal_notice


class TripDesk:
    def __init__(self, *, store: TripStore, connection_factory: Callable[[], Any],
                 check: Callable[..., dict[str, Any]] | None = None) -> None:
        self.store, self._connect = store, connection_factory
        # ★다른 안으로 바꿀 때 활동이면 **그 시각에 다시 점검**한다 — 계산한 뒤로 시간이
        #   흘렀다. 점검기가 없으면(재생 시험 일부) 점검 없이 바꾼다고 결과에 적는다.
        self._check = check

    def _read(self, trip_id: UUID):
        with self._connect() as conn:
            trip, items = self.store.latest(conn, trip_id)
            places = self.store.places(conn, trip_id)
        return trip, items, places

    def _with_nearby(self, trip_id: UUID, meal: Item | None, places: list[dict[str, Any]],
                     plan: Callable[[list[dict[str, Any]]], Plan]) -> Plan:
        """먼저 지금 장소로 계산하고, **바꿔야 할 때만** 그 식사 근처의 원장 가게를 들여놓고 다시 계산한다.

        ★`[2026-10-01]` 대체 후보는 코어 `places` 에서만 나와, 공용 식당이 없는 DB 에서 「문 닫았어요」의 후보가 0개였다.
          일정이 그대로 괜찮으면(`still_fits` 등) 원장을 읽지도, 장소를 들여놓지도 않는다.
        """
        first = plan(places)
        if meal is None or not (isinstance(first, ItineraryChange)
                                or (isinstance(first, NoChange) and first.status == "unresolved")):
            return first
        from .dining import nearby

        with self._connect() as conn:
            more = nearby.add_nearby(conn, self.store, trip_id, [meal], places)
        return plan(more) if more != places else first

    def _dining_states(self, slots):
        from .dining.ledger import dining_states

        with self._connect() as conn:
            return dining_states(conn, self.store.tenant_id, slots)

    # ── 요식-P3 — 늦는다 ────────────────────────────────────────
    def report_delay(self, *, trip_id: UUID, at: datetime, minutes: int,
                     message: str, request_id: str | None = None) -> dict[str, Any]:
        """「N분 늦는다」. 다음 식사 항목이 그 도착 시각에 성립하는지 보고, 안 되면 바꾼다."""
        trip, items, places = self._read(trip_id)

        def plan(candidates):
            return plan_delay(trip=trip, items=items, places=candidates, at=at, minutes=minutes,
                              message=message, request_id=request_id, state_lookup=self._dining_states)
        meal = next((i for i in items if i.kind == "dining" and i.starts_at >= at), None)   # plan_delay 와 같은 식사
        return self._outcome(trip_id, trip["version"], items,
                             self._with_nearby(trip_id, meal, places, plan), gate=True)

    # ── 요식-P7 — 도착했더니 휴무 ──────────────────────────────
    def report_closed(self, *, trip_id: UUID, at: datetime, message: str,
                      request_id: str | None = None) -> dict[str, Any]:
        """「오늘 임시휴무」. 지금 식사 항목을 걸어갈 수 있는 대체 식당으로 바꾼다."""
        trip, items, places = self._read(trip_id)

        def plan(candidates):
            return plan_closed(trip=trip, items=items, places=candidates, at=at, message=message,
                               request_id=request_id, state_lookup=self._dining_states)
        meal = next((i for i in items if i.kind == "dining"                                # plan_closed 와 같은 식사
                     and i.starts_at <= at < (i.ends_at or i.starts_at + timedelta(hours=1))), None)
        return self._outcome(trip_id, trip["version"], items,
                             self._with_nearby(trip_id, meal, places, plan), gate=True)

    # ── 액-08 — 품절, 근처 다른 곳? ────────────────────────────
    def ask_nearby_store(self, *, trip_id: UUID, at: datetime, products: list[str],
                         message: str, request_id: str | None = None) -> dict[str, Any]:
        """품절 상품을 **취급할 만한** 매장을 귀가 동선에서 고른다. 일정은 안 바꾼다."""
        _, items, places = self._read(trip_id)
        return plan_nearby_store(items=items, places=places, at=at, products=products,
                                 message=message, request_id=request_id)

    # ── 재요청 ① — 다른 안으로 바꿔 줘 ─────────────────────────
    def swap_alternate(self, *, trip_id: UUID, item_id: UUID, base_version: int,
                       choice: str | None = None, message: str | None = None,
                       request_id: str | None = None) -> dict[str, Any]:
        """적용된 안을 들고 있던 「다른 안」으로 바꾼다. 원래 안은 다시 「다른 안」이 된다."""
        trip, items, places = self._read(trip_id)
        plan = plan_swap(trip_version=trip["version"], base_version=base_version, items=items,
                         places_by_id={p["place_id"]: p for p in places}, item_id=item_id,
                         choice=choice, message=message, request_id=request_id,
                         check=self._check)
        return self._outcome(trip_id, base_version, items, plan)

    # ── 재요청 ② — 되돌려 줘 ───────────────────────────────────
    def rollback(self, *, trip_id: UUID, base_version: int, to_version: int,
                 message: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        """옛 버전의 항목을 **새 버전으로 다시 쓴다**(append-only — 옛 버전을 지우지 않는다)."""
        with self._connect() as conn:
            trip, items = self.store.latest(conn, trip_id)
            old = (self.store.items(conn, trip_id, to_version)
                   if trip["version"] == base_version and 1 <= to_version < trip["version"] else [])
        plan = plan_rollback(trip_version=trip["version"], base_version=base_version,
                             current_items=items, old_items=old, to_version=to_version,
                             message=message, request_id=request_id)
        if isinstance(plan, NoChange):
            return {"status": plan.status, **plan.detail}
        written = self._write(trip_id, base_version, items, plan)
        if written["status"] != "adjusted":
            return written
        return {"status": "rolled_back", "version": written["version"],
                "restored": plan.summary["restored"], "notice": plan.notice}

    # ── 적용(버전 + 통지를 한 트랜잭션) ─────────────────────────
    def _outcome(self, trip_id: UUID, base_version: int, items: list[Item],
                 plan: Plan, *, gate: bool = False) -> dict[str, Any]:
        if isinstance(plan, NoChange):
            return {"status": plan.status, **plan.detail}
        if gate:
            asked = self._ask_instead(trip_id, base_version, plan)
            if asked is not None:
                return asked
        outcome = self._write(trip_id, base_version, items, plan)
        if outcome["status"] == "adjusted":
            outcome.update(plan.summary)
        return outcome

    def _ask_instead(self, trip_id: UUID, base_version: int,
                     plan: ItineraryChange) -> dict[str, Any] | None:
        """바꿀 항목 중 하나라도 판정이 「바꾸지 말라」면 **바꾸지 않고 묻는다.** None 이면 바꿔도 된다.

        ★감시 경로(`trip_watch._apply`)와 같은 판정·같은 보류 제안·같은 알림이다. 신고는 안전 사건이
          아니라 `report=None` 이다. 같은 신고가 다시 와도 제안은 하나다(`pending_changes` UNIQUE) —
          이미 열려 있으면 그것을 돌려준다.
        """
        with self._connect() as conn, conn.transaction():
            trip, items = self.store.latest(conn, trip_id)
            if trip["version"] != base_version:
                return None            # 그 사이 바뀌었다 — `_write` 가 `stale` 로 답한다
            pending = PendingStore(self.store.tenant_id)
            for item_id in plan.replacements:
                current = next((i for i in items if i.item_id == item_id), None)
                if current is None:
                    continue
                decision = decide(constraints=trip.get("constraints"), item=current, report=None)
                if decision.action == "apply":
                    continue
                options = options_from(plan, current)
                proposal_id = pending.open(conn, trip_id=trip_id, item=current, base_version=base_version,
                                           decision=decision, causes=plan.causes, options=options)
                if proposal_id is None:        # 이미 물었다 — 다시 알리지 않는다
                    proposal_id = next(row["proposal_id"] for row in pending.list(conn, trip_id)
                                       if row["item_id"] == current.item_id
                                       and row["base_version"] == base_version)
                    return {"status": "asked", "already": True, "proposal_id": str(proposal_id),
                            "reason": decision.reason, "item": current.title,
                            "text": f"{current.title} — 이미 여쭤본 일정이에요. 계획서 링크에서 골라 주세요. "
                                    "답이 없으면 원래 일정을 그대로 둡니다."}
                notice = proposal_notice(item=current, decision=decision, causes=plan.causes,
                                         options=options, proposal_id=proposal_id)
                self.store.enqueue_message(conn, trip_id=trip_id, key=f"proposal:{proposal_id}",
                                           payload=notice)
                return {"status": "asked", "already": False, "proposal_id": str(proposal_id),
                        "reason": decision.reason, "protected_by": decision.protected_by,
                        "item": current.title, "notice": notice}
        return None

    def _write(self, trip_id: UUID, base_version: int, items: list[Item],
               plan: ItineraryChange) -> dict[str, Any]:
        """★계산에 쓴 버전(`base_version`) 위에만 쓴다. 그 사이 바뀌었으면 `stale`."""
        with self._connect() as conn, conn.transaction():
            try:
                version = self.store.append_version(
                    conn, trip_id=trip_id, base_version=base_version,
                    items=plan.new_items(items), reason=plan.reason, causes=plan.causes)
            except StaleItinerary:
                return {"status": "stale"}
            self.store.enqueue_notice(conn, trip_id=trip_id, version=version,
                                      payload={**plan.notice, "version": version})
        return {"status": "adjusted", "version": version, "notice": plan.notice}


__all__ = ["DINING_RADIUS_M", "TripDesk"]
