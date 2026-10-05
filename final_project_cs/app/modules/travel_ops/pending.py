# -*- coding: utf-8 -*-
"""일정이 꼬였을 때 **바꿀지 · 물을지 · 알리기만 할지**를 가르고, 묻는 동안 안을 들고 있는다. `[2026-09-24]` D-020

★★판정(`decide`) — 위에서부터 먼저 걸리는 것이 이긴다.

    ① 「변경 안 할 일정」(고객 고정 `customer_pinned` · 잠긴 예약 `locked`) → ask (15번 답과 상관없이 묻는다)
    ② 설문 15번 「먼저 물어봐줘」 + 안전 사건            → safety_alert (바꾸지 않고, 「안전」이 분명한 알림)
    ③ 설문 15번 「먼저 물어봐줘」                       → ask      (바꾸지 않고 안 1·2·3을 보이고 기다린다)
    ④ 그 밖(15번이 없거나 「비슷한 곳으로」)             → apply    (지금까지처럼 최고 안을 바로 적용하고 알린다)

  ★「먼저 물어봐줘」가 아닌 여행은 안전 사건도 **바로 바꾼다** — 사용자 결정(2026-09-24 새벽).
  ★「변경 안 할 일정」이 안전 사건에 걸리면 ①이다 — 바꾸지 않되 알림의 머리는 「안전」이다(`safety` 칸).
  ★돈이 걸렸는지(예약 연결)는 **따로 따지지 않는다** — 사용자 결정(2026-09-24). 바꾸기 싫은 일정은
    사용자가 「변경 안 할 일정」으로 적는다.

★무응답 — 그 일정이 **끝날 때까지** 기다렸다가 `expired` 로 닫는다. **바꾸지 않는다.**
  그 뒤로 알림은 일정 순서대로 다음 일정으로 이어 간다(여러 건을 묶어 묻지 않는다).

★고르면 — 기존 「다른 안으로 바꾸기」(`plan_swap`)를 **그대로 탄다.** 계산한 뒤로 시간이 흘렀으니
  그 시각에 다시 점검하고, 그 사이 일정이 바뀌었으면(`stale`) 고르지 못한다. 한 건은 한 번만 닫힌다 —
  일행이 동시에 누르면 **먼저 닫은 쪽**이 이기고 나중 쪽은 `already_decided`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from .itinerary import Item, StaleItinerary, TripStore
from .itinerary_changes import ItineraryChange, NoChange, applied_record, plan_swap
from .survey import on_disruption

#: 안전 사건 — 몸이 다칠 수 있는 것만. 기상특보는 「경보」만(주의보 제외). 사용자와 합의(2026-09-24).
SAFETY_CATEGORIES = frozenset({"earthquake", "disaster_msg"})


def is_safety(report: dict[str, Any] | None) -> bool:
    for event in (report or {}).get("disruptions") or []:
        category = event.get("category")
        if category in SAFETY_CATEGORIES:
            return True
        if category == "weather_warning" and "경보" in str(event.get("kind") or ""):
            return True
    return False


def protected_reason(item: Item) -> str | None:
    """「변경 안 할 일정」인가. ★항목에 이미 있는 표시만 본다 — 돈이 걸렸는지는 따지지 않는다."""
    if item.detail.get("customer_pinned"):
        return "customer_pinned"
    if item.locked:
        return "locked"
    if item.detail.get("booking"):
        # ★`[2026-09-27]` 고객 계획에서 읽은 예약번호(`intake/assemble.py`). 업체 확인 전이라 예약 표(`bookings`)에는
        #   없지만, 돈이 걸렸을 수 있는 일정을 묻지 않고 바꾸지 않는다.
        return "booked"
    return None


@dataclass(frozen=True)
class Decision:
    action: str                    # apply · ask · safety_alert
    reason: str | None             # ask_first · protected · safety_alert (apply 면 None)
    protected_by: str | None
    safety: bool


def decide(*, constraints: dict[str, Any] | None, item: Item,
           report: dict[str, Any] | None = None) -> Decision:
    safety = is_safety(report)
    protected = protected_reason(item)
    if protected:
        return Decision("ask", "protected", protected, safety)
    if on_disruption(constraints) == "ask_first":
        if safety:
            return Decision("safety_alert", "safety_alert", None, True)
        return Decision("ask", "ask_first", None, False)
    return Decision("apply", None, None, safety)


def options_from(plan: ItineraryChange, item: Item) -> list[dict[str, Any]]:
    """계산된 안을 **적용에 필요한 값 그대로** 1위부터 적는다(다시 계산하지 않게)."""
    best = plan.replacements.get(item.item_id)
    return [] if best is None else options_for(best)


def options_for(best: Item) -> list[dict[str, Any]]:
    """최고 안(바꿔 넣을 항목) 하나와 그것이 들고 있는 「다른 안」을 1위부터."""
    ranked = [applied_record(best)] + list(best.detail.get("alternates") or [])
    return [{**record, "rank": rank} for rank, record in enumerate(ranked, start=1)]


def _cause_text(causes: list[dict[str, Any]]) -> str:
    for cause in causes:
        for field in ("summary", "kind", "reason", "type", "category"):
            if cause.get(field):
                return str(cause[field])
    return "일정에 문제가 생겼어요"


def proposal_notice(*, item: Item, decision: Decision, causes: list[dict[str, Any]],
                    options: list[dict[str, Any]], proposal_id: UUID) -> dict[str, Any]:
    """묻는 알림. ★「답이 없으면 원래 일정을 그대로 둡니다」를 **반드시** 적는다 — 무응답의 결과다."""
    head = "⚠️ 안전 알림 — " if decision.safety else ""
    why = _cause_text(causes)
    listed = " · ".join(f"{o['rank']}) {o.get('option_label') or o['name']}" for o in options[:3])
    if decision.reason == "protected":
        body = (f"{item.title} — {why}. 변경하지 않기로 한 일정이라 **바꾸지 않았어요.** "
                f"어떻게 할까요? {listed}")
    elif decision.reason == "safety_alert":
        body = (f"{item.title} — {why}. 일정은 **바꾸지 않았어요.** 안전을 먼저 살펴 주세요."
                + (f" 다른 안: {listed}" if listed else ""))
    else:
        body = f"{item.title} — {why}. 어떻게 할까요? {listed}"
    warnings = [f"{o['name']}: {warning}" for o in options[:3] for warning in o.get("warnings") or []]
    text = f"{head}{body} " + (" ".join(warnings) + " " if warnings else "") \
        + "답이 없으면 원래 일정을 그대로 둡니다."
    return {"type": "safety_alert" if decision.safety else "proposal_request",
            "text": text, "language": "ko", "causes": causes, "proposal_id": str(proposal_id),
            "item_id": str(item.item_id), "reason": decision.reason,
            "protected_by": decision.protected_by,
            "options": [{"key": o["key"], "rank": o["rank"],
                         "name": o.get("option_label") or o["name"],
                         "starts_at": o.get("starts_at"),
                         **({"warnings": list(o["warnings"])} if o.get("warnings") else {})}
                        for o in options[:3]],
            "replay": False, **({"warnings": warnings} if warnings else {})}


class PendingStore:
    """보류 제안을 쓰고 · 읽고 · 닫는다. ★모든 쿼리에 `tenant_id` — 조건 없는 조회는 보안 결함이다."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    def open(self, conn, *, trip_id: UUID, item: Item, base_version: int, decision: Decision,
             causes: list[dict[str, Any]], options: list[dict[str, Any]]) -> UUID | None:
        """새로 열었으면 id, **이미 같은 제안이 있으면 None**(감시가 3분마다 같은 것을 만들지 않게)."""
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pending_changes (tenant_id, trip_id, item_id, base_version, reason, "
                "protected_by, safety, cause_json, options_json, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id, trip_id, item_id, base_version) DO NOTHING "
                "RETURNING proposal_id",
                (self.tenant_id, trip_id, item.item_id, base_version, decision.reason,
                 decision.protected_by, decision.safety,
                 json.dumps(causes, ensure_ascii=False, default=str),
                 json.dumps(options, ensure_ascii=False, default=str),
                 item.ends_at or item.starts_at))
            row = cur.fetchone()
        return row[0] if row else None

    _COLUMNS = ("proposal_id", "trip_id", "item_id", "base_version", "reason", "protected_by",
                "safety", "cause_json", "options_json", "status", "expires_at", "chosen_key",
                "chosen_by", "chosen_version", "decided_at", "created_at")

    def list(self, conn, trip_id: UUID, *, only_open: bool = False) -> list[dict[str, Any]]:
        where = " AND status='open'" if only_open else ""
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(self._COLUMNS)} FROM pending_changes "
                        f"WHERE tenant_id=%s AND trip_id=%s{where} ORDER BY created_at",
                        (self.tenant_id, trip_id))
            return [dict(zip(self._COLUMNS, row)) for row in cur.fetchall()]

    def get(self, conn, trip_id: UUID, proposal_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(self._COLUMNS)} FROM pending_changes "
                        f"WHERE tenant_id=%s AND trip_id=%s AND proposal_id=%s"
                        + (" FOR UPDATE" if lock else ""),
                        (self.tenant_id, trip_id, proposal_id))
            row = cur.fetchone()
        return dict(zip(self._COLUMNS, row)) if row else None

    def close(self, conn, proposal_id: UUID, *, status: str, key: str | None = None,
              by: str | None = None, version: int | None = None) -> bool:
        """★`open` 인 것만 닫는다 — 이미 닫혔으면 False(먼저 닫은 쪽이 이긴다)."""
        with conn.cursor() as cur:
            cur.execute("UPDATE pending_changes SET status=%s, chosen_key=%s, chosen_by=%s, "
                        "chosen_version=%s, decided_at=now() "
                        "WHERE tenant_id=%s AND proposal_id=%s AND status='open'",
                        (status, key, by, version, self.tenant_id, proposal_id))
            return cur.rowcount == 1

    def expire(self, conn, *, now: datetime) -> list[dict[str, Any]]:
        """무응답 — 그 일정이 끝난 제안을 닫는다. ★바꾸지 않는다. 닫은 것을 돌려준다(조용히 닫지 않는다)."""
        with conn.cursor() as cur:
            cur.execute("UPDATE pending_changes SET status='expired', decided_at=now() "
                        "WHERE tenant_id=%s AND status='open' AND expires_at <= %s "
                        "RETURNING proposal_id, trip_id, item_id",
                        (self.tenant_id, now))
            return [dict(zip(("proposal_id", "trip_id", "item_id"), row)) for row in cur.fetchall()]


class ProposalRefused(Exception):
    def __init__(self, code: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code, self.detail = code, detail or {}


def choose(*, conn, store: TripStore, pending: PendingStore, trip_id: UUID, proposal_id: UUID,
           key: str | None, by: str, places_by_id: dict[str, dict[str, Any]],
           check: Callable[..., dict[str, Any]] | None) -> dict[str, Any]:
    """고객이 안을 고른다. `key=None` 이면 **원래 일정을 그대로 둔다**(kept).

    ★한 트랜잭션 안에서 제안을 잠그고(`FOR UPDATE`) → 기존 「다른 안」 경로(`plan_swap`)로 다시 점검 →
      새 버전을 쓰고 → 제안을 닫는다. 어느 단계든 실패하면 **아무것도 안 바뀐다.**
    """
    proposal = pending.get(conn, trip_id, proposal_id, lock=True)
    if proposal is None:
        raise ProposalRefused("not_found")
    if proposal["status"] != "open":
        raise ProposalRefused("already_decided", {"status": proposal["status"],
                                                  "chosen_key": proposal["chosen_key"]})
    if key is None:
        pending.close(conn, proposal_id, status="kept", by=by)
        return {"status": "kept"}
    trip, items = store.latest(conn, trip_id)
    options = list(proposal["options_json"] or [])
    current = next((i for i in items if i.item_id == proposal["item_id"]), None)
    if current is None or trip["version"] != proposal["base_version"]:
        raise ProposalRefused("stale", {"version": trip["version"],
                                        "base_version": proposal["base_version"]})
    # ★기존 경로를 그대로 탄다 — 보관한 안을 「다른 안」 자리에 놓고 고른다
    probe = [i if i.item_id != current.item_id else _with_alternates(i, options) for i in items]
    plan = plan_swap(trip_version=trip["version"], base_version=proposal["base_version"], items=probe,
                     places_by_id=places_by_id, item_id=current.item_id, choice=key,
                     message=None, request_id=f"proposal:{proposal_id}", check=check)
    if isinstance(plan, NoChange):
        raise ProposalRefused(plan.status, plan.detail)
    try:
        version = store.append_version(conn, trip_id=trip_id, base_version=trip["version"],
                                       items=plan.new_items(probe), reason="customer_choice",
                                       causes=plan.causes)
    except StaleItinerary as exc:
        raise ProposalRefused("stale", {"message": str(exc)}) from None
    store.enqueue_notice(conn, trip_id=trip_id, version=version,
                         payload={**plan.notice, "type": "change_notice", "version": version,
                                  "proposal_id": str(proposal_id)})
    pending.close(conn, proposal_id, status="chosen", key=key, by=by, version=version)
    return {"status": "chosen", "version": version, "summary": plan.summary}


def apply_or_ask(conn, *, store: TripStore, trip_id: UUID, item_id: UUID, plan: ItineraryChange,
                 report: dict[str, Any] | None = None) -> dict[str, Any]:
    """★바꾸는 **한 문**(감시 · 새벽 확인) — 판정(`decide`)을 거쳐 새 버전을 쓰거나, 묻는다.

    돌려주는 `status`:
        adjusted  새 버전과 변경 통지를 썼다(같은 트랜잭션)
        asked     바꾸지 않고 보류 제안 + 묻는 알림(이미 물은 것이면 `already: True`, 알림은 다시 안 낸다)
        gone      그 사이 다른 쪽이 이 항목을 바꿨다 — 옛 계산을 밀어 넣지 않는다
        stale     기준 버전이 움직였다
    부르는 쪽이 트랜잭션을 연다.
    """
    trip, items = store.latest(conn, trip_id)
    current = next((i for i in items if i.item_id == item_id), None)
    if current is None:
        return {"status": "gone"}
    decision = decide(constraints=trip.get("constraints"), item=current, report=report)
    if decision.action != "apply":
        options = options_from(plan, current)
        pending = PendingStore(store.tenant_id)
        proposal_id = pending.open(conn, trip_id=trip_id, item=current, base_version=trip["version"],
                                   decision=decision, causes=plan.causes, options=options)
        if proposal_id is None:
            return {"status": "asked", "already": True, "reason": decision.reason, "item": current.title}
        store.enqueue_message(conn, trip_id=trip_id, key=f"proposal:{proposal_id}",
                              payload=proposal_notice(item=current, decision=decision, causes=plan.causes,
                                                      options=options, proposal_id=proposal_id))
        return {"status": "asked", "already": False, "proposal_id": str(proposal_id),
                "reason": decision.reason, "safety": decision.safety, "item": current.title}
    try:
        version = store.append_version(conn, trip_id=trip_id, base_version=trip["version"],
                                       items=plan.new_items(items), reason=plan.reason, causes=plan.causes)
    except StaleItinerary:
        return {"status": "stale"}
    store.enqueue_notice(conn, trip_id=trip_id, version=version,
                         payload={**plan.notice, "version": version,
                                  "type": "change_notice", "safety": decision.safety})
    return {"status": "adjusted", "version": version, "safety": decision.safety}


def _with_alternates(item: Item, options: list[dict[str, Any]]) -> Item:
    return Item(item_id=item.item_id, seq=item.seq, kind=item.kind, title=item.title,
                place_id=item.place_id, starts_at=item.starts_at, ends_at=item.ends_at,
                locked=item.locked, booking_id=item.booking_id,
                replaces_item_id=item.replaces_item_id,
                detail={**item.detail, "alternates": [
                    {k: v for k, v in o.items() if k != "rank"} for o in options]},
                place=item.place)


__all__ = ["Decision", "PendingStore", "ProposalRefused", "SAFETY_CATEGORIES", "apply_or_ask", "choose",
           "decide",
           "is_safety", "options_for", "options_from", "proposal_notice", "protected_reason"]
