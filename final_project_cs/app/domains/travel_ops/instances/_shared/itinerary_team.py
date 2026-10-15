# -*- coding: utf-8 -*-
"""Case 버전의 **일정 관리** — activity · dining · mobility Team 이 같이 쓰는 부품.

★`[결정 2026-09-17]` 시나리오용 여행 버전(`trip_watch`·`trip_desk`)이 하던 일을 Case 로 한다.

    Case(대상 = 여행)  →  Controller  →  Team 이 **읽고 계산해서 제안**  →  코어가 적용·통지

★Team 은 쓰지 않는다. 계산 결과를 `itinerary.apply` 제안(승인 불요 · 위험 낮음)으로 낸다.
  적용과 통지는 코어가 Case 완료와 한 트랜잭션으로 한다(`app/core/actions.py`).

★계산은 `itinerary_changes.py` 하나다 — 시나리오 버전과 **같은 함수**라 문구·판단이 같다.

★읽기는 전부 도구로 한다(`read.itinerary` · `read.itinerary_version` · `read.place_catalog` ·
  `read.disruptions` · `read.route_events` · `read.customer_report`). 도구가 모르면(`None`)
  지어내지 않고 escalate 한다.

누가 무엇을 하나:

    공통      change(다른 안으로) · rollback(되돌리기) · ★question(규정 질문 — 일정을 안 바꾸고 규정 근거로 답)
    activity  감시 Case(성립 점검 disrupted → 대안) · stock_out(동선 위 매장, 일정 안 바꿈)
    dining    delay(늦음) · closed(휴무)
    mobility  감시 Case(구간 사건 → 경로 재선택)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.contracts import ActionProposal, NextAction, TeamResult, TeamTask, ToolNotAllowed
from app.core.idempotency import idempotency_key

from app.domains.travel_ops.components.itinerary.itinerary import Item, item_from_dict
from app.domains.travel_ops.components.actions.itinerary_actions import ACTION_TYPE, CHOSEN_BY_CUSTOMER, change_arguments, options_arguments
from app.domains.travel_ops.components.itinerary.itinerary_fit import fit_change
from app.tools.read_tools import ToolBudgetExceeded, ToolLoopExceeded

from app.domains.travel_ops.components.itinerary.itinerary_changes import ItineraryChange, NoChange, plan_rollback, plan_swap

KST = ZoneInfo("Asia/Seoul")

#: 일정 관리에 쓰는 읽기 도구 — 각 Team 의 manifest 가 필요한 만큼 더한다.
ITINERARY_TOOLS = ["read.itinerary", "read.itinerary_version", "read.place_catalog",
                   "read.customer_report"]

#: 바꾸지 않아도 되는 결과 — 사람에게 넘길 일이 아니라 **답**이다(시나리오 화면 문구와 같다).
ANSWERS = {
    "needs_check": "영업 여부는 확인이 필요해요. 기존 일정은 바꾸지 않았어요.",
    "still_fits": "지금 일정 그대로도 괜찮아요 — 바꾸지 않았어요.",
    "no_meal": "그 시각 뒤에는 식사 일정이 없어요.",
    "no_alternate": "바꿀 수 있는 다른 안이 없어요.",
    "clear": "지금 확인해 보니 일정에 영향이 없어요 — 바꾸지 않았어요.",
    "gone": "그 일정은 이미 바뀌었어요 — 다시 바꾸지 않았어요.",
}


@dataclass(frozen=True)
class Consent:
    """`[2026-10-03]` 감시 계산(`plan_*_trigger`)의 결과 중 하나 — 바꿀지 **먼저 묻는다**(활동에 날씨 사건만 걸렸을 때, `pending.needs_consent`).
    Case 하나짜리 흐름은 이것을 「바꿀까요?」 제안으로, 같은 여행 묶음(`trip_watch_batch`)은 묶음 안의 한 항목으로 다룬다."""

    report: dict[str, Any]


#: 문장이 **계산 결과에 따라** 달라서 `NoChange.detail["text"]` 로 내려오는 답
TEXT_ANSWERS = frozenset({"knock_on"})


class ItineraryWork:
    """`TravelTeamBase` 를 상속한 Team 에 섞는다. `manifest.team_id` 로 자기 capability 를 안다."""

    #: 웹 채팅(`request.type: conversation`)의 「바꿔 줘」 · 「다른 곳」을 공용 계획(식당 · 활동의 장소 목록)이 아니라 팀 자신의 `_change` 가 받나 — 후보를 직접 찾는 팀(숙소)만 True
    conversation_change_by_team: bool = False

    # ── 선택 ────────────────────────────────────────────────────
    @property
    def itinerary_capability(self) -> str:
        return f"{self.manifest.team_id}.itinerary"

    @property
    def question_capability(self) -> str:
        """`[2026-09-25]` 규정 질문 — **규정을 읽는** capability. 면제 목록(`policy_optional_capabilities`)에
        넣지 않는다 → Controller 가 RAG 를 돌고, 근거가 없으면(degraded) `escalated`(처리 불가)로 닫는다."""
        return f"{self.manifest.team_id}.itinerary_question"

    @classmethod
    def _wants_itinerary(cls, state: dict[str, Any]) -> bool:
        ref = (state or {}).get("subject_ref") or {}
        return ref.get("kind") == "trip" and bool(ref.get("id"))

    @classmethod
    def _wants_question(cls, state: dict[str, Any]) -> bool:
        """접수 때 **질문**으로 읽힌 여행 Case — 일정 관리(규정 면제)가 아니라 규정 질문으로 보낸다."""
        report = ((state or {}).get("interpretation") or {}).get("report") or {}
        return cls._wants_itinerary(state) and report.get("type") == "question"

    @classmethod
    def itinerary_route(cls, team_id: str, state: dict[str, Any] | None) -> str | None:
        """여행 Case 의 capability — 질문이면 `.itinerary_question`, 그 밖은 `.itinerary`, 여행이 아니면 None."""
        if cls._wants_question(state or {}):
            return f"{team_id}.itinerary_question"
        return f"{team_id}.itinerary" if cls._wants_itinerary(state or {}) else None

    # ── 진입 ────────────────────────────────────────────────────
    async def run_itinerary(self, task: TeamTask) -> TeamResult:
        state = task.context.current_state
        ref = state.get("subject_ref") or {}
        seen: set[str] = set()
        view = self._read(task, "read.itinerary", {"trip_id": ref.get("id")}, seen)
        evidence = self._evidence(task, source_id="read.itinerary", claim="현재 일정 버전", value=view)
        if view is None:
            return self._unknown(task, "여행 일정", evidence)
        trip = view["trip"]
        items = [item_from_dict(entry) for entry in view["items"]]
        request = dict(ref.get("request") or {})
        at = self._moment(request.get("at") or (state.get("trigger") or {}).get("at"))
        ctx = {"seen": seen, "evidence": evidence, "trip": trip, "items": items, "ref": ref,
               "request": request, "at": at}

        if state.get("trigger_source") == "schedule":
            if (state.get("trigger") or {}).get("batch"):
                # ★`[2026-10-03]` 같은 여행의 문제가 한 회차에 둘 이상 — 한 초안 위에서 차례로 고쳐 판 하나 · 알림 하나(`trip_watch_batch`)
                from app.domains.travel_ops.components.watch.trip_watch_batch import WatchBatch

                return WatchBatch(self, task, ctx).run()
            return await self.handle_trigger(task, ctx)

        kind = request.get("type")
        if kind == "conversation":
            from .conversation_work import run_conversation

            return await run_conversation(self, task, ctx)
        report: dict[str, Any] = {}
        reading = (state.get("interpretation") or {}).get("report")
        if not kind and isinstance(reading, dict) and reading.get("type"):
            # ★접수가 분류 직전에 이미 뽑았다 — 같은 문장을 모델에 두 번 묻지 않는다.
            report, kind = reading, reading["type"]
            ctx["evidence"] = self._evidence(task, source_id="case.interpretation",
                                             claim="접수 때 고객 문장에서 뽑은 신고", value=report,
                                             base=ctx["evidence"])
            if kind == "other":
                return self._escalate(task, "report_not_understood", ctx["evidence"])
        if not kind:
            report = self._read(task, "read.customer_report", {"text": task.input_text}, seen) or {}
            ctx["evidence"] = self._evidence(task, source_id="read.customer_report",
                                             claim="고객 문장에서 뽑은 신고", value=report or None,
                                             base=ctx["evidence"])
            if not report:
                return self._unknown(task, "신고 내용", ctx["evidence"])
            kind = report.get("type")
            if not kind or kind == "other":
                return self._escalate(task, "report_not_understood", ctx["evidence"],
                                      warnings=[str(report.get("error") or "일정을 바꿀 신고로 읽히지 않는다")])
        ctx["report"] = {**report, **request}
        if kind == "delay" and not ctx["report"].get("minutes"):
            from .conversation_work import run_conversation

            ctx["request"] = {**ctx["report"], "type": "conversation", "action": "report_delay"}
            return await run_conversation(self, task, ctx)
        if kind == "question":
            return self._answer_question(task, ctx)
        if kind == "change":
            return self._change(task, ctx)
        if kind == "rollback":
            return self._rollback(task, ctx)
        return await self.handle_report(task, kind, ctx)

    # ── 팀별로 채운다 ───────────────────────────────────────────
    async def handle_trigger(self, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
        return self._escalate(task, "trigger_not_handled_by_team", ctx["evidence"])

    async def handle_report(self, task: TeamTask, kind: str, ctx: dict[str, Any]) -> TeamResult:
        return self._escalate(task, f"report_{kind}_not_handled_by_team", ctx["evidence"])

    # ── 공통: 다른 안으로 · 되돌리기 ─────────────────────────────
    def _change(self, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
        ref, trip, items = ctx["ref"], ctx["trip"], ctx["items"]
        target = ref.get("part_id") or ref.get("recent_part_id")
        if not target:
            return self._unknown(task, "바꿀 일정 항목", ctx["evidence"])
        item = next((i for i in items if str(i.item_id) == str(target)), None)
        if item is not None and item.kind != self.manifest.team_id:
            return self._escalate(task, "target_kind_mismatch", ctx["evidence"],
                                  warnings=[f"{item.kind} 항목은 {self.manifest.team_id} 팀 일이 아니다"])
        places = self.catalog(task, ctx)
        if places is None:
            return self._unknown(task, "장소 목록", ctx["evidence"])
        check = self.recheck(task, ctx) if self.manifest.team_id == "activity" else None
        plan = plan_swap(trip_version=trip["version"],
                         base_version=int(ref.get("base_version") or trip["version"]),
                         items=items, places_by_id={p["place_id"]: p for p in places},
                         item_id=UUID(str(target)), choice=ctx["request"].get("choice"),
                         message=task.input_text, request_id=task.context.current_state.get("request_id"),
                         check=check)
        return self.settle(task, ctx, plan)

    def _rollback(self, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
        ref, trip, items = ctx["ref"], ctx["trip"], ctx["items"]
        to_version = ctx["report"].get("to_version")
        if to_version is None:
            return self._unknown(task, "되돌릴 버전", ctx["evidence"])
        base = int(ref.get("base_version") or trip["version"])
        old: list[Item] = []
        if trip["version"] == base and 1 <= int(to_version) < trip["version"]:
            view = self._read(task, "read.itinerary_version",
                              {"trip_id": trip["trip_id"], "version": int(to_version)}, ctx["seen"])
            ctx["evidence"] = self._evidence(task, source_id="read.itinerary_version",
                                             claim=f"일정 버전 {to_version}", value=view, base=ctx["evidence"])
            if view is None:
                return self._unknown(task, "되돌릴 일정 버전", ctx["evidence"])
            old = [item_from_dict(entry) for entry in view["items"]]
        plan = plan_rollback(trip_version=trip["version"], base_version=base, current_items=items,
                             old_items=old, to_version=int(to_version), message=task.input_text,
                             request_id=task.context.current_state.get("request_id"))
        return self.settle(task, ctx, plan)

    # ── 공통: 규정 질문 (2026-09-25) ────────────────────────────
    def _answer_question(self, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
        """규정 질문 — **일정을 바꾸지 않고** 규정 근거로 답한다.

        ★짚은 일정 항목을 기준으로 `read.policy`(문장 근거)를 읽고, 그 항목에 예약이 있으면
          `read.booking_terms`(취소 기한·위약금 수치)도 읽는다. 판정 입력은 **예약이 아니라 일정 항목**이다
          (v11 결정 — `activity.check_cancelable` 은 `read.booking` 을 전제해 무료·무예약 항목에서 「모름」이 된다).
        ★모델로 문장을 짓지 않는다 — 규정 절에 써 둔 **고객용 문장**을 출처와 함께 싣는다(근거 없는 문장 금지).
          규정을 못 찾으면 지어내지 않고 「모름」으로 `escalated`(처리 불가) 닫는다.
        """
        ref, items, seen = ctx["ref"], ctx["items"], ctx["seen"]
        item = next((i for i in items if str(i.item_id) == str(ref.get("part_id"))), None) \
            or mentioned_item(items, task.input_text)
        query = task.input_text if item is None else f"{item.title} {task.input_text}"
        chunks = self._read(task, "read.policy", {"query": query}, seen) or []
        evidence = self._evidence(task, source_id="read.policy", claim="질문에 맞는 여행 규정",
                                  value=chunks or None, base=ctx["evidence"])
        if not chunks:
            return self._unknown(task, "관련 규정", evidence)
        terms = None
        if item is not None and item.booking_id and "read.booking_terms" in (task.allowed_tools or []):
            terms = self._read(task, "read.booking_terms", {"booking_id": str(item.booking_id)}, seen)
            evidence = self._evidence(task, source_id="read.booking_terms", claim="이 예약의 취소 조건",
                                      value=terms, base=evidence)
        answer, sources = question_answer(item, chunks, terms)
        if answer is None:                     # 걸린 절이 모두 내부 절차 — 고객에게 보일 규정 문장이 없다
            return self._unknown(task, "관련 규정", evidence)
        return self._result(task, outcome="completed", confidence=0.7, evidence=evidence, answer=answer,
                            next_action=NextAction.RESPOND,
                            decisions=[{"itinerary": "question_answered", "sources": sources,
                                        "item": item.title if item else None,
                                        "booking_terms": bool(terms)}])

    # ── 도우미 ──────────────────────────────────────────────────
    def catalog(self, task: TeamTask, ctx: dict[str, Any]) -> list[dict[str, Any]] | None:
        if "places" not in ctx:
            # ★`[2026-09-29]` 여행 id 를 넘긴다 — 그 여행 전용 장소 행과, 대본 여행이면 시연 장소까지 본다(`TripStore.places`)
            trip = ctx.get("trip") or {}
            ctx["places"] = self._read(task, "read.place_catalog",
                                       {"trip_id": str(trip["trip_id"])} if trip.get("trip_id") else {}, ctx["seen"])
            ctx["evidence"] = self._evidence(task, source_id="read.place_catalog", claim="대안 후보 장소",
                                             value=ctx["places"], base=ctx["evidence"])
        return ctx["places"]

    def recheck(self, task: TeamTask, ctx: dict[str, Any]):
        """대안을 **그 시각에 다시 점검**하는 콜러블 — 점검기는 도구로만 부른다.

        ★도구가 모르면(`None`) 「clear」로 읽지 않는다 — `fatal` 로 돌려 그 후보를 탈락시킨다.
        """
        def check(*, place: dict[str, Any], starts_at: datetime | None) -> dict[str, Any]:
            report = self._read(task, "read.disruptions", self.check_arguments(place, starts_at), ctx["seen"])
            return report if isinstance(report, dict) else {"verdict": "fatal", "unknown": True}
        return check

    #: 재점검 도구가 **못 불리는** 이유 — 권한 · 예산 · 같은 질문 반복. 이것은 후보를 「못 봤다」는 뜻이지 실행 실패가 아니다
    TOOL_LIMITS = (ToolNotAllowed, ToolBudgetExceeded, ToolLoopExceeded)

    def safe_recheck(self, task: TeamTask, ctx: dict[str, Any]):
        """`recheck` 인데 **도구 한도에 걸려도 예외로 터지지 않는다** — 그 후보는 `not_checked`(못 봤다)로 돌려 고르지 않는다.

        ☆`[2026-10-02 결함 인계 #1]` 후보가 많으면 예산이 바닥나 `ToolBudgetExceeded` 가 Controller 까지 올라가 실행이 실패했다 —
          앞 후보가 이미 통과했어도 변경안을 못 내고, 감시 반복이 끊겼다. 요식 팀의 `safe_check` 와 같은 생각이다:
          **점검하지 않은 곳을 괜찮다고 하지 않는다**(`choose` 가 `clear` 가 아닌 판정은 탈락시킨다).
        """
        check = self.recheck(task, ctx)

        def safe(*, place: dict[str, Any], starts_at: datetime | None) -> dict[str, Any]:
            try:
                return check(place=place, starts_at=starts_at)
            except self.TOOL_LIMITS:
                return {"verdict": "not_checked"}
        return safe

    @staticmethod
    def check_arguments(place: dict[str, Any], starts_at: datetime | None) -> dict[str, Any]:
        return {"place_id": place.get("place_id"), "latitude": place.get("latitude"),
                "longitude": place.get("longitude"), "weather_sensitive": place.get("weather_sensitive"),
                "district": place.get("district"),
                "starts_at": starts_at.isoformat() if starts_at else None}

    @staticmethod
    def _moment(value: Any) -> datetime:
        if value:
            moment = datetime.fromisoformat(str(value))
            return moment if moment.tzinfo else moment.replace(tzinfo=KST)
        return datetime.now(KST)

    def settle(self, task: TeamTask, ctx: dict[str, Any], plan: ItineraryChange | NoChange) -> TeamResult:
        """계산 결과 → Team 결과. 바꾸면 제안 + 통지 문구, 안 바꾸면 답 또는 처리 불가(`escalated`)."""
        evidence = ctx["evidence"]
        if isinstance(plan, NoChange):
            if plan.status in ANSWERS:
                # ★`[2026-10-05]` 영업 여부를 못 알아 바꾸지 않은 경우(`needs_check`) — 확신을 낮추고(0.5) 계산이 만든 문장과
                #   주의 문장(warnings)을 그대로 싣는다. 그 밖의 「안 바꿈」 답은 지금처럼 고정 문장
                checking = plan.status == "needs_check"
                return self._result(task, outcome="completed", confidence=0.5 if checking else 0.9,
                                    evidence=evidence,
                                    answer=plan.detail.get("message", ANSWERS[plan.status]) if checking
                                           else ANSWERS[plan.status],
                                    warnings=plan.detail.get("warnings") or [], next_action=NextAction.RESPOND,
                                    decisions=[{"itinerary": plan.status, **self._plain(plan.detail)}])
            if plan.status in TEXT_ANSWERS and plan.detail.get("text"):
                # ★`[2026-10-03 L2]` 문장이 계산 결과에 따라 달라지는 답(어느 뒤 일정이 왜 걸리는지) — 일정은 안 바꿨다
                return self._result(task, outcome="completed", confidence=0.9, evidence=evidence,
                                    answer=str(plan.detail["text"]), next_action=NextAction.RESPOND,
                                    decisions=[{"itinerary": plan.status, **self._plain(plan.detail)}])
            return self._escalate(task, f"itinerary_{plan.status}", evidence,
                                  warnings=[f"일정을 바꾸지 못했다: {plan.status}"])
        trip = ctx["trip"]
        if plan.reason not in CHOSEN_BY_CUSTOMER:
            # ★`[2026-10-03]` 자동 변경은 **쓰기 전에 일정 전체를 다시 판정**하고, 최고 안이 걸리면 담당이 뽑아 둔 다음 순위 안을 차례로 같은 판정에 넣는다(`itinerary_fit`).
            #   다 걸리면 쓰지 않고 `itinerary_recheck_failed` — 감시가 고객에게 「일정은 그대로 두었어요」를 알린다. 적용기의 같은 재판정은 마지막 안전망이다.
            fit = fit_change(plan, trip=trip, items=ctx["items"])
            if fit.change is None:
                return self._escalate(task, "itinerary_recheck_failed", evidence,
                                      warnings=["일정 전체 재판정에 걸려 바꾸지 못했다: " + " / ".join(
                                          f"{s.rank}순위 {s.name} — {chr(59).join(s.reasons)}" for s in fit.skipped)])
            plan = fit.change
        base = int(ctx["ref"].get("base_version") or trip["version"]) \
            if plan.reason in ("customer_request", "rollback") else trip["version"]
        arguments = change_arguments(trip_id=trip["trip_id"], base_version=base, change=plan)
        proposal = ActionProposal(
            action_type=ACTION_TYPE, arguments=arguments,
            idempotency_key=idempotency_key(
                tenant_id=task.context.tenant_id,
                request_id=str(task.context.current_state.get("request_id") or task.case_id),
                action_type=ACTION_TYPE, business_subject=f"{trip['trip_id']}:v{base}"),
            # ★우리 DB 안의 일정 버전만 바꾼다(v11 §4-C) — 승인 없이 먼저 고치고 알린다.
            #   근거 대조는 적용기가 적용 순간에 한다(기준 버전 · 항목 · 장소 실재).
            approval_required=False, risk_level="low", rationale_evidence_ids=[])
        return self._result(task, outcome="completed", confidence=0.9, evidence=evidence,
                            answer=plan.notice["text"], next_action=NextAction.RESPOND,
                            warnings=plan.notice.get("warnings") or [],
                            action_proposals=[proposal],
                            decisions=[{"itinerary": plan.reason, **self._plain(plan.summary)}])

    def offer(self, task: TeamTask, ctx: dict[str, Any], item: Item, *, options: list[dict[str, Any]], text: str,
              causes: list[dict[str, Any]], decision: dict[str, Any], shown_key: str | None = None,
              summary: dict[str, Any] | None = None) -> TeamResult:
        """★`[2026-10-13]` 고를 안을 **일정을 바꾸지 않고** 내민다 — 「선택이 필요해요」 보류 제안(`options_arguments`).

        식당 · 활동의 「다른 후보」(`TripDesk.propose_alternatives`)와 같은 모양이다. 고르면 고르기 경로(`pending.choose` → `plan_swap`)가 바꾼다.
        ☆숙소 · 항공 팀은 전에 후보를 항목 상세에 싣는 `itinerary.apply` 로 내서 후보를 볼 때마다 일정 판이 오르고 「일정이 바뀌었어요」가 나갔다."""
        trip = ctx["trip"]
        base = int(ctx["ref"].get("base_version") or trip["version"])
        arguments = options_arguments(trip_id=trip["trip_id"], base_version=base, item_id=item.item_id, options=options,
                                      text=text, causes=causes, shown_key=shown_key, summary=summary)
        proposal = ActionProposal(
            action_type=ACTION_TYPE, arguments=arguments,
            idempotency_key=idempotency_key(
                tenant_id=task.context.tenant_id,
                request_id=str(task.context.current_state.get("request_id") or task.case_id),
                action_type=ACTION_TYPE, business_subject=f"{trip['trip_id']}:v{base}"),
            # ★일정은 안 바꾼다 — 보류 제안을 열기만 한다. 고르는 것은 고객이다
            approval_required=False, risk_level="low", rationale_evidence_ids=[])
        return self._result(task, outcome="completed", confidence=0.9, evidence=ctx["evidence"],
                            answer=text, next_action=NextAction.RESPOND, action_proposals=[proposal],
                            decisions=[self._plain(decision)])

    @staticmethod
    def _plain(value: dict[str, Any]) -> dict[str, Any]:
        import json

        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


# ── 규정 질문의 순수 부품 ────────────────────────────────────
#: 제목에 흔히 붙어 항목을 가리지 못하는 말 — 이것만 겹쳐서는 그 항목으로 보지 않는다
_GENERIC = frozenset({"식사", "체험", "관람", "일정", "방문", "투어", "이동", "시간", "여행"})
_MEAL_WORDS = {"아침": (0, 11), "조식": (0, 11), "점심": (11, 15), "저녁": (17, 24), "석식": (17, 24)}


#: ★`[2026-10-07 사용자 지시]` 일정 항목 짚기 **모델**(우리 데이터로 가르친 파인튜닝 모델) 자리 — 조립 때 `set_item_pointer` 로 꽂는다
#:  (`components/conversation/item_pointer.py`). 없으면 아래 낱말 규칙만 쓴다(지금까지와 같다). 모델이 못 정하면(`NotImplemented`) 규칙으로 돌아간다.
_POINTER: Any = None


def set_item_pointer(pointer: Any) -> None:
    """조립 때 한 번(다시 부르면 교체, `None` 이면 뗀다). 부르는 모양: `pointer(items, text, rule)` → 항목 · None · `NotImplemented`."""
    global _POINTER
    _POINTER = pointer


def mentioned_item(items: list[Item], text: str) -> Item | None:
    """질문이 짚은 일정 항목. 가르친 모델이 꽂혀 있고 켜져 있으면 모델이 번호를 고른다(못 고르면 규칙으로).
    규칙: ①제목·장소 이름의 단어가 문장에 나오면 가장 많이 겹친 것 ②「점심·저녁 식당」처럼 끼니 + 식당이면 그 시간대의 식사. 모르면 None(지어내지 않는다)."""
    pointer = _POINTER
    if pointer is not None:
        got = pointer(items, text, lambda: _rule_item(items, text))
        if got is not NotImplemented:
            return got
    return _rule_item(items, text)


def _rule_item(items: list[Item], text: str) -> Item | None:
    """낱말 규칙 — 옛 `mentioned_item` 그대로(모델 없이도, 모델이 실패해도 이 길로 돈다)."""
    text = text or ""
    best, hits = None, 0
    for item in items:
        if item.kind == "mobility":
            continue
        name = f"{item.title} {(item.place or {}).get('name', '')}"
        words = {w for w in re.split(r"[\s·()\[\],./→\-]+", name) if len(w) >= 2 and w not in _GENERIC}
        count = sum(1 for w in words if w in text)
        if count > hits:
            best, hits = item, count
    if best is not None:
        return best
    if any(word in text for word in ("식당", "밥", "음식", "먹")):
        for word, (start, end) in _MEAL_WORDS.items():
            if word in text:
                from app.domains.travel_ops.components.itinerary.itinerary_checks import seoul

                meals = [i for i in items if i.kind == "dining" and start <= seoul(i.starts_at).hour < end]      # ★서울 시각의 시
                if len(meals) == 1:
                    return meals[0]
    return None


def _chunk(chunk: Any) -> tuple[str, str, float]:
    if isinstance(chunk, dict):
        source = chunk.get("source_id") or f"{chunk.get('document_id')}#c{chunk.get('chunk_no')}"
        return str(chunk.get("content") or ""), str(source), float(chunk.get("score") or 0)
    return str(getattr(chunk, "content", "")), str(getattr(chunk, "source_id", "")), float(getattr(chunk, "score", 0))


_LINES: dict[str, str] | None = None


def customer_lines() -> dict[str, str]:
    """규정 절(`t_doc_02#c5`) → **고객에게 하는 말** 한 줄. 규정 문서 머리(`customer_answers`)에서 읽는다.

    ★`[2026-09-28 사용자 결정]` 규정 문서 12개는 **직원에게 쓴 글**이다(「여기에 위약금 문장을 붙이면 없는 비용을
      만들어 말하는 것이 되고…」). 전에는 그 조각을 그대로 붙여 직원용 문장이 고객 답에 나갔다(ui 세션 실서버 시험).
      이제 조각 원문은 싣지 않고 **그 절에 써 둔 고객용 문장만** 싣는다. 문장이 없는 절(내부 절차)은 싣지 않는다.
    ★문서 본문을 바꾸지 않아 청크·임베딩은 그대로다. 문장과 규정이 한 파일에 있어 규정을 고치면 같이 고친다."""
    global _LINES
    if _LINES is None:
        from pathlib import Path

        from knowledge.ingest import load_corpus

        manifest = Path(__file__).resolve().parents[5] / "knowledge" / "travel" / "manifest.json"
        found: dict[str, str] = {}
        current = manifest.parent.parent / "current-travel" / "manifest.json"
        manifests = [manifest] + ([current] if current.is_file() else [])
        for document in (doc for path in manifests for doc in load_corpus(path)):
            answers = document.frontmatter.get("customer_answers") or {}
            for section in document.sections:
                line = answers.get(section.title)
                if line and str(line).strip():
                    found[f"{document.frontmatter['document_id']}#c{section.number}"] = str(line).strip()
        _LINES = found
    return _LINES


def question_answer(item: Item | None, chunks: list[Any], terms: dict[str, Any] | None,
                    *, top: int = 2) -> tuple[str | None, list[str]]:
    """규정 절의 **고객용 문장**(점수 높은 순 `top` 개)을 출처와 함께 싣는 답. (답, 출처 목록).
    ★고객용 문장이 있는 절이 하나도 없고 예약 조건도 없으면 `(None, [])` — 부르는 쪽이 「규정 못 찾음」으로 다룬다."""
    by_source = customer_lines()
    ranked: list[tuple[str, str]] = []
    for _, source, _ in sorted((_chunk(c) for c in chunks), key=lambda c: -c[2]):
        line = by_source.get(source)
        if line and all(line != shown for shown, _ in ranked):
            ranked.append((line, source))
        if len(ranked) >= top:
            break
    if not ranked and not terms:
        return None, []
    subject = item.title if item is not None else "문의하신 내용"
    # ☆`[2026-10-06 사람 평가 메모]` 규정 문장은 「해당 상황이 되면 이렇게 해 드려요」라는 일반 규정인데, 맨 끝의 「일정은 바꾸지 않았어요」와 붙어 있으면
    #   「경로에 넣어 드려요」(규정)와 「일정은 바꾸지 않았어요」(지금)가 서로 모순으로 읽혔다 — 규정은 앞으로의 일이고 지금 일정은 그대로라고 갈라 말한다
    lines = [f"{subject} — 여행 규정에서 찾은 내용이에요. 해당하는 상황이 되면 이 기준으로 처리해요."]
    # ★`[2026-09-29 사용자 지시]` 근거 id(t_doc_… · #c…)·예약 조건 scope 는 **고객 문장에 싣지 않는다** — 관리자(개발 모드)만 본다.
    #   근거는 버리지 않는다 — 돌려주는 출처 목록이 Case 기록에 남고, 웹은 `web.dev_mode` 가 켜졌을 때만 `basis` 칸으로 받는다
    for line, _source in ranked:
        lines.append(f"· {line}")
    if terms:
        deadline = terms.get("cancel_deadline_hours")
        line = f"이 예약의 취소 기한: 시작 {deadline:g}시간 전까지" if isinstance(deadline, (int, float)) else \
            "이 예약의 취소 조건이 등록돼 있어요"
        penalty = terms.get("penalty_by_hours") or {}
        if penalty:
            line += " · 위약금 기준 " + ", ".join(f"{k}시간 전부터 {v}" for k, v in penalty.items())
        lines.append(line)
    lines.append("지금 일정은 바꾸지 않았어요. 바꾸고 싶으시면 말씀해 주세요.")
    sources = [source for _, source in ranked]
    if terms:
        sources.append(f"booking_terms:{terms.get('matched_scope')}:{terms.get('source')}")
    return "\n".join(lines), sources


__all__ = ["ANSWERS", "Consent", "ITINERARY_TOOLS", "ItineraryWork", "customer_lines", "mentioned_item", "question_answer", "set_item_pointer"]
