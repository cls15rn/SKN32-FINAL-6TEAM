# -*- coding: utf-8 -*-
"""Dining Team — 그 시각에 그 식당이 여는지, 조건이 맞는지 본다.

★**「오늘 여는지」가 아니라 「그 일정 시각에 여는지」**를 본다.

★확인 수준을 같이 남긴다. 제공자의 영업시간은 **오늘부터 며칠의 예정**이지
  조회 순간의 개점 확인이 아니다(v10 §4-D). 재조회 시각을 현장 관찰 시각처럼
  표시하면 안 된다 — `confirmed_at` 과 출처를 함께 담는다.
"""
from __future__ import annotations

from typing import Any

from app.core.contracts import (NextAction, TeamManifest, TeamResult, TeamTask,
                                ToolNotAllowed)
from app.tools.read_tools import ToolBudgetExceeded

from .._base import TravelTeamBase
from ..itinerary_changes import plan_closed, plan_delay
from ..itinerary_team import ITINERARY_TOOLS, ItineraryWork
from .ledger import merge_state


class DiningTeam(ItineraryWork, TravelTeamBase):
    manifest = TeamManifest(
        team_id="dining",
        display_name="Dining Team",
        contract_name="a_cop.team_task",
        supported_contract_versions=["1.0"],
        capabilities=["dining.check_open", "dining.check_conditions",
                      "dining.itinerary",    # ★`[2026-09-17]` 여행 일정 관리 — 늦음 · 휴무 · 재요청
                      "dining.itinerary_question"],   # ★`[2026-09-25]` 규정 질문 — 규정을 읽는다(면제 아님)
        accepted_case_types=["dining"],
        # ★`[2026-09-17]` `policy` 를 뺐다 — 이 Team 은 정책 문서를 판단에 쓰지 않는다(선언만 있었다).
        # ★`[2026-09-22]` **되돌렸다.** 여행 코퍼스가 생겨 이 scope 에 문서가 있다
        #   (조정·노쇼 기준 `t_doc_06`, 대체 식당 판정 `t_doc_07`, 동행 조건 `t_doc_12`).
        #   ★단 **「이 집이 할랄인가」는 여전히 RAG 가 아니다** — 그건 사실이라 `read.place` 가 댄다.
        #   코퍼스에는 「무엇이 확인되면 가능하다고 말해도 되나」만 들어 있다(t_doc_12 §조건은 사실이고…).
        required_context=["case_state", "policy", "db_facts", "history"],
        policy_optional_capabilities=["dining.itinerary"],
        # ★`[2026-09-25]` `read.booking_terms` — 규정 질문에서 식당 예약이 있으면 취소 조건 수치를 댄다
        # ★`read.dining_state` — 요식 원장에 「그 시각에 여는가」를 묻는다. 코어에 등록되지 않았으면
        #   `ToolNotAllowed` 가 나고, 그때는 원장 없이 예전처럼 답한다 — 등록 전에 이 Team 이 죽으면 안 된다.
        # ★`[2026-09-30]` `read.place_price` — 대체 식당을 세울 때 구글 가격(1인당 범위 · 가격대)을 묻는다. 없으면 없이 세운다
        allowed_tools=["read.place", "read.policy", "read.booking", "read.booking_terms",
                       "read.dining_state", "read.dining_states", "read.place_price", *ITINERARY_TOOLS],
        # ★`[2026-09-22]` `opening_hours`·`dietary` 는 **실물이 없던 scope** 였다(문서 0건). 지운다 —
        #   안 쓰는 선언은 나중에 누가 잘못 채운다(재점검 문서 §1 이 지적한 그대로).
        knowledge_scope=["travel_dining", "travel_cancellation", "travel_access"],
        max_steps=12,
        active=True,
        implementation_revision="2026-09-22",
        default_capability="dining.check_open",
    )

    @staticmethod
    def select_capability(intent: str | None, input_text: str, state: dict | None = None) -> str | None:
        """★여행이 정해진 Case 는 일정 관리로 — 그 밖은 기본 동작에 맡긴다."""
        return ItineraryWork.itinerary_route("dining", state)

    async def handle_report(self, task: TeamTask, kind: str, ctx: dict[str, Any]) -> TeamResult:
        if kind not in ("delay", "closed"):
            return await super().handle_report(task, kind, ctx)
        places = self.catalog(task, ctx)
        if places is None:
            return self._unknown(task, "장소 목록", ctx["evidence"])
        request_id = task.context.current_state.get("request_id")
        prices = self._price_lookup(task, ctx)
        states = self._state_lookup(task, ctx)
        if kind == "delay":
            minutes = ctx["report"].get("minutes")
            if not minutes:
                return self._unknown(task, "늦는 시간", ctx["evidence"])
            plan = plan_delay(trip=ctx["trip"], items=ctx["items"], places=places, at=ctx["at"],
                              minutes=int(minutes), message=task.input_text, request_id=request_id,
                              price_lookup=prices, state_lookup=states)
        else:
            plan = plan_closed(trip=ctx["trip"], items=ctx["items"], places=places, at=ctx["at"],
                               message=task.input_text, request_id=request_id, price_lookup=prices,
                               state_lookup=states)
        return self.settle(task, ctx, plan)

    def _state_lookup(self, task: TeamTask, ctx: dict[str, Any]):
        """코어 도구로 대체 후보의 시간대별 원장 판정을 읽는다."""
        def lookup(slots):
            try:
                states = self._read(task, "read.dining_states",
                                    {"slots": [{**s, "at": s["at"].isoformat(),
                                                "until": s["until"].isoformat()} for s in slots]}, ctx["seen"])
            except (ToolNotAllowed, ToolBudgetExceeded):
                return None
            ctx["evidence"] = self._evidence(task, source_id="read.dining_states",
                                             claim="대체 식당의 방문 시간대 원장 판정",
                                             value=states, base=ctx["evidence"])
            return states
        return lookup

    def _price_lookup(self, task: TeamTask, ctx: dict[str, Any]):
        """대체 식당의 구글 가격을 묻는 함수(`read.place_price`). ★한 번에 한 번만 부른다(도구 호출 하나).

        ★도구가 없거나(`ToolNotAllowed`) 못 불렀으면 `None` — 구글 가격 없이 예전처럼 세운다.
          가격대는 더해 주는 것이지 없으면 못 도는 것이 아니다(`_dining_state` 와 같다).
        ★받은 값을 근거(`evidence`)에 싣지 않는다 — 구글 약관상 저장 금지라 결정 기록엔 비교 결과만 남는다.
        """
        def lookup(places: list[dict[str, Any]]) -> dict[str, Any] | None:
            try:
                return self._read(task, "read.place_price",
                                  {"place_ids": [str(p["place_id"]) for p in places]}, ctx["seen"])
            except (ToolNotAllowed, ToolBudgetExceeded):
                return None
        return lookup

    async def execute(self, task: TeamTask) -> TeamResult:
        blocked = self._guard(task)
        if blocked is not None:
            return blocked
        if task.capability in (self.itinerary_capability, self.question_capability):
            return await self.run_itinerary(task)

        seen: set[str] = set()
        # ★★2026-09-09 결함 수정. 전에는 `read.place` 를 `case_id` 로 불렀다.
        #   장소 조회의 열쇠는 **`place_id`** 이고 그건 예약에 들어 있다 —
        #   Case 번호로는 어느 장소인지 알 수 없다. 실 DB 종단 실행에서
        #   Dining 이 항상 `unknown_장소·영업 정보` 로 떨어져 드러났다.
        #   Activity 는 처음부터 예약을 먼저 읽고 있었다 — **같은 팀 안에서도
        #   갈라졌던** 것이고, 그래서 종단 실행이 필요했다.
        booking = self._read(task, "read.booking", {"case_id": str(task.case_id)}, seen)
        evidence = self._evidence(task, source_id="read.booking",
                                  claim="예약 내역", value=booking)
        if booking is None:
            return self._unknown(task, "예약 내역", evidence)

        place = self._read(task, "read.place",
                           {"place_id": booking.get("place_id")}, seen)
        evidence = self._evidence(task, source_id="read.place",
                                  claim="장소·영업 정보", value=place, base=evidence)
        if place is None:
            return self._unknown(task, "장소·영업 정보", evidence)

        # ★예약 시각으로 요식 원장에 다시 묻는다. `read.place` 가 시각을 받지
        #   않아서 그 칸만으로는 「12시 예약」과 「22시 예약」을 가를 수 없다.
        state = self._dining_state(task, booking, seen)
        if state is not None:
            evidence = self._evidence(task, source_id="read.dining_state",
                                      claim="요식 원장의 그 시각 판정",
                                      value=state, base=evidence)
            place = merge_state(place, state)

        # ★확인 시각이 없으면 "확인했다" 고 말하지 않는다.
        confirmed_at = place.get("confirmed_at")
        if confirmed_at is None:
            return self._unknown(task, "영업 정보의 확인 시각", evidence)

        if task.capability == "dining.check_conditions":
            return self._conditions(task, place, evidence)

        open_at_slot = place.get("open_at_slot")
        if open_at_slot is None:
            return self._unknown(task, "그 시각의 영업 여부", evidence)

        # ★원장이 「확인이 필요하다」고 하면 그 말을 답에 붙인다. 판정은 바꾸지
        #   않는다 — 명절 영업시간은 어떤 자료에도 없어서 추정이고, 추정으로
        #   일정을 바꾸지는 않되 확인할 곳은 알려 준다.
        extra = place.get("dining") or {}
        warnings = ["제공자 예정 정보이지 현장 확인이 아니다"]
        if extra.get("needs_holiday_check"):
            warnings.append("명절이나 공휴일이라 영업시간이 다를 수 있다")
        if extra.get("needs_check"):
            warnings.append("영업 종료가 임박해 마지막 주문을 확인해야 한다")

        decided: dict[str, Any] = {"open_at_slot": open_at_slot,
                                   "confirmed_at": str(confirmed_at)}
        if place.get("dining_source"):
            decided["source"] = place["dining_source"]
        if extra.get("holiday_context"):
            decided["holiday_context"] = extra["holiday_context"]

        return self._result(
            task, outcome="completed", confidence=0.9, evidence=evidence,
            next_action=NextAction.RESPOND,
            answer=(f"일정 시각에 영업합니다(확인 시각 {confirmed_at})."
                    if open_at_slot else
                    f"일정 시각에는 영업하지 않습니다(확인 시각 {confirmed_at})."),
            decisions=[decided],
            warnings=warnings)

    def _dining_state(self, task: TeamTask, booking: dict, seen: set[str]) -> Any:
        """요식 원장에 예약 시각을 넣어 묻는다.

        ★도구가 아직 코어에 등록되지 않았으면 `ToolNotAllowed` 가 난다.
          그때는 조용히 비운다. 원장이 붙기 전이라고 이 Team 이 죽으면
          지금 돌아가던 것까지 멈춘다 — 더해 주는 것이지 없으면 못 도는 것이 아니다.
        """
        starts_at = booking.get("starts_at")
        place_id = booking.get("place_id")
        if starts_at is None or place_id is None:
            return None
        try:
            return self._read(task, "read.dining_state",
                              {"place_id": str(place_id), "at": str(starts_at)}, seen)
        except ToolNotAllowed:
            return None

    def _conditions(self, task: TeamTask, place: dict, evidence: list) -> TeamResult:
        """동행 조건 — 아이 동반 · 할랄 · 채식. ★모르는 조건은 「모름」으로 둔다."""
        wanted = task.context.current_state.get("dietary") or []
        known = place.get("dietary") or []
        absent = place.get("dietary_absent") or []
        unknown = [item for item in wanted if item not in known and item not in absent]
        if unknown:
            return self._unknown(task, f"조건 {', '.join(unknown)}", evidence)

        met = [item for item in wanted if item in known]
        return self._result(
            task, outcome="completed", confidence=0.9, evidence=evidence,
            next_action=NextAction.RESPOND,
            answer=(f"요청한 조건을 모두 만족합니다 — {', '.join(met)}." if met else
                    "요청한 조건이 없어 별도 확인 없이 진행할 수 있습니다."),
            decisions=[{"conditions_met": met, "requested": list(wanted)}])
