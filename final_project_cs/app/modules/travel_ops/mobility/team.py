# -*- coding: utf-8 -*-
"""Mobility Team — 구간 이동이 일정 안에 들어가는지 본다.

★**문의와 사건을 가른다.** "막차가 몇 시인가요"(문의)와 "막차를 놓쳤어요"(사건)는
  다른 capability 다. 커머스에서 이 문구 판정이 지나치게 넓어 "지연 **기준**이
  어떻게 되나요" 같은 문의까지 예외로 잡은 적이 있다 — **요청을 분명히 밝히는
  문구만 잡고, 신호가 없으면 기본 동작(조회)에 맡긴다.**
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.contracts import Evidence, NextAction, TeamManifest, TeamResult, TeamTask

from .._base import TravelTeamBase
from ..itinerary_changes import (NoChange, next_after, place_before, plan_route_adjustment, route_of,
                                route_targets)
from ..itinerary_team import ITINERARY_TOOLS, ItineraryWork


class MobilityTeam(ItineraryWork, TravelTeamBase):
    manifest = TeamManifest(
        team_id="mobility",
        display_name="Mobility Team",
        contract_name="a_cop.team_task",
        supported_contract_versions=["1.0"],
        capabilities=["mobility.check_route", "mobility.status", "mobility.exception",
                      "mobility.itinerary",    # ★`[2026-09-17]` 여행 일정 관리 — 구간 사건 · 재요청
                      "mobility.itinerary_question"],   # ★`[2026-09-25]` 규정 질문 — 규정을 읽는다(면제 아님)
        accepted_case_types=["mobility"],
        # ★`[2026-09-17]` `policy` 를 뺐다 — 이 Team 은 정책 문서를 판단에 쓰지 않는다(선언만 있었다).
        # ★`[2026-09-22]` **되돌렸다.** 여행 코퍼스가 생겼다 — 중단·지연 대체(`t_doc_08`),
        #   연결 실패 책임과 여유(`t_doc_09`), 환불 안내 범위(`t_doc_10`).
        #   ★**여유 시간 계산은 여전히 코드가 한다**(`have >= need`). 코퍼스가 대는 것은
        #   「누구 사정으로 보나」와 「어디까지 말하나」이고 그건 계산이 아니라 규정 해석이다.
        required_context=["case_state", "policy", "db_facts", "history"],
        policy_optional_capabilities=["mobility.itinerary"],
        allowed_tools=["read.route", "read.transit", "read.policy", "read.route_events", *ITINERARY_TOOLS],
        # ★`[2026-09-22]` `transit`·`route_exception` 은 문서 0건이던 scope 라 지운다.
        #   날씨는 이동에도 걸리므로(태풍·대설) `travel_weather` 를 함께 둔다.
        knowledge_scope=["travel_mobility", "travel_weather", "travel_cancellation"],
        max_steps=12,
        active=True,
        implementation_revision="2026-09-22",
        default_capability="mobility.check_route",
    )

    #: ★사건을 **분명히 밝히는** 문구만 잡는다. 물음표로 끝나는 문의는 안 잡는다.
    #:  커머스에서 이 훅이 넓어 문의를 사건으로 잘못 잡은 적이 있다.
    _INCIDENT_MARKERS = ("놓쳤", "못 탔", "끊겼", "지연됐", "결항", "운행 중단")

    @staticmethod
    def select_capability(intent: str | None, input_text: str, state: dict | None = None) -> str | None:
        # ★`[2026-09-17]` 여행이 정해진 Case 는 일정 관리로 — 문구 판정보다 먼저 본다.
        if ItineraryWork._wants_itinerary(state or {}):
            return ItineraryWork.itinerary_route("mobility", state)
        if intent != "mobility":
            return None
        if any(marker in input_text for marker in MobilityTeam._INCIDENT_MARKERS):
            return "mobility.exception"
        return None   # 신호가 없으면 기본 동작에 맡긴다

    async def handle_trigger(self, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
        """감시가 연 Case — 구간 사건을 **다시 읽고**, 계획한 수단이 막혔으면 경로를 다시 고른다."""
        trigger = task.context.current_state.get("trigger") or {}
        item = next((i for i in ctx["items"] if str(i.item_id) == str(trigger.get("item_id"))), None)
        if item is None:
            return self.settle(task, ctx, NoChange("gone"))
        plan = plan_mobility_trigger(self, task, ctx, item)
        return plan if isinstance(plan, TeamResult) else self.settle(task, ctx, plan)

    async def execute(self, task: TeamTask) -> TeamResult:
        blocked = self._guard(task)
        if blocked is not None:
            return blocked
        if task.capability in (self.itinerary_capability, self.question_capability):
            return await self.run_itinerary(task)
        # ☆`[2026-09-29 이동 계산기 문제목록 #34·#35]` 구조화된 구간 입력(current_state.mobility — 날짜·출발 시각·구간)이
        #   오면 이동 계산기(시간표 판정)로 답한다. 앞 판은 계산기를 부르지 않았고, 아래 조회 도구(read.route·read.transit)는
        #   비어 있어 이 갈래가 늘 「모름」이었다. 구조화 입력이 없으면 종전대로 — 자연어에서 구간을 짐작하지 않는다.
        if (task.context.current_state or {}).get("mobility") is not None:
            return self._from_engine(task)

        seen: set[str] = set()
        route = self._read(task, "read.route", {"case_id": str(task.case_id)}, seen)
        evidence = self._evidence(task, source_id="read.route",
                                  claim="구간 정보", value=route)
        if route is None:
            return self._unknown(task, "구간 정보", evidence)

        transit = self._read(task, "read.transit",
                             {"route_id": route.get("route_id")}, seen)
        evidence = self._evidence(task, source_id="read.transit",
                                  claim="운행 정보", value=transit, base=evidence)
        if transit is None:
            return self._unknown(task, "운행 정보", evidence)

        # ★여유 시간은 계산이다. LLM 을 부르지 않는다.
        need = route.get("duration_minutes")
        have = route.get("gap_minutes")
        if need is None or have is None:
            return self._unknown(task, "이동 시간 또는 여유 시간", evidence)

        fits = have >= need
        last_ok = transit.get("last_departure_ok")
        return self._result(
            task, outcome="completed", confidence=1.0, evidence=evidence,
            next_action=NextAction.RESPOND,
            answer=(f"이동이 일정 안에 들어갑니다 — 필요 {need}분, 여유 {have}분."
                    if fits else
                    f"이동 시간이 부족합니다 — 필요 {need}분인데 여유가 {have}분입니다."),
            decisions=[{"fits": fits, "need_minutes": need, "gap_minutes": have,
                        "last_departure_ok": last_ok}],
            warnings=[] if last_ok is not False else ["막차 시각을 넘긴다"])

    # ── 이동 계산기 판정 → 계약(TeamResult) — `[2026-09-29 이동 계산기 문제목록 #34·#35]` ─────────────
    def _from_engine(self, task: TeamTask) -> TeamResult:
        """계산기 어댑터(engine/adapter.py)가 준 dict 를 계약으로 옮긴다. 어댑터는 계약 타입을 모르게 짜여 있다.

        ★꺼져 있으면(설정 mobility_data_dir 비움) 오류로 올린다 — 지어낸 답도, 「모름」 답도 내지 않는다.
        ★근거의 observed_at 은 계산기가 「built:…」·「fetched:…」 접두를 붙여 준다 — 접두를 떼어 시각으로 읽는다.
          못 읽으면 판정 시각(decided_at)으로 두고 claim 에 원래 값을 남긴다(시각을 지어내지 않는다).
        """
        from . import wiring
        out = wiring.team_result(task)
        if out is None:
            return self._escalate(task, "mobility_engine_disabled",
                                  warnings=[f"이동 계산기 상태: {wiring.mode()}"])
        decided = datetime.now().astimezone()
        evidence = []
        for e in out.get("evidence") or []:
            raw = str(e.get("observed_at") or "")
            stamp = raw.split(":", 1)[1] if raw.split(":", 1)[0] in ("built", "fetched") else raw
            try:
                at = datetime.fromisoformat(stamp)
                claim = e.get("claim", "")
            except ValueError:
                at, claim = decided, f"{e.get('claim', '')} (자료 시각 표기: {raw or '없음'})"
            evidence.append(Evidence(evidence_id=e["evidence_id"], source_type=e["source_type"],
                                     source_id=e["source_id"], claim=claim, value=e.get("value"),
                                     confidence=e.get("confidence", 0.2), observed_at=at))
        return self._result(task, outcome=out["outcome"], confidence=out.get("confidence", 0.0),
                            answer=out.get("answer"), evidence=evidence,
                            decisions=out.get("decisions") or [], action_proposals=[],
                            next_action=NextAction(out["next_action"]),
                            failure_code=out.get("failure_code"), warnings=out.get("warnings") or [])


def plan_mobility_trigger(work: ItineraryWork, task: TeamTask, ctx: dict[str, Any], item: Any):
    """감시 — 이동 항목 하나의 구간 사건을 **다시 읽고** 경로를 다시 고를 안을 계산한다(쓰지 않는다). 결과: 변경 · `NoChange` · `TeamResult`(여기서 멈춤).
    ★`[2026-10-03]` `MobilityTeam.handle_trigger` 에서 떼어 냈다 — 같은 여행의 문제 묶음(`trip_watch_batch`)이 한 초안 위에서 항목마다 부른다."""
    route = route_of(item) if item.kind == "mobility" else None
    if route is None:
        return work._unknown(task, "경로 정의", ctx["evidence"])
    view = work._read(task, "read.route_events", {"targets": route_targets(route)}, ctx["seen"])
    ctx["evidence"] = work._evidence(task, source_id="read.route_events", claim="구간 운행·통제 사건",
                                     value=view, base=ctx["evidence"])
    if view is None or view.get("events") is None:
        # ★사건을 못 읽었다 — 「사건 없음」으로 넘기지 않는다(결정 15 의 치명).
        return work._escalate(task, "fatal_source_failure", ctx["evidence"])
    return plan_route_adjustment(item=item, following=next_after(ctx["items"], item), route=route,
                                 events=view["events"], now=ctx["at"],
                                 previous=place_before(ctx["items"], item))   # #38·#39 계산기 재탐색의 출발지
