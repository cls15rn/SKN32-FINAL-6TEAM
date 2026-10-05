# -*- coding: utf-8 -*-
"""Activity Team — 활동 예약이 지금 성립하는지 판정한다.

대상은 **활동 그 자체**다 — 골프(티타임)·수상레저·스키 같은 레저뿐 아니라
경복궁 관람처럼 시각이 정해진 활동도 포함한다.

★**판정은 계산으로 한다. LLM 을 부르지 않는다**(v10 §4-D).
  취소 기한·인원·운영시간·날씨 조건은 전부 계산이다. 대안 생성(③)만 LLM 이고
  그건 별도 capability 로 나온다.

★**「모름」을 「없음」으로 읽지 않는다.** 도구가 `None` 을 돌려주면 그건
  "그런 게 없다" 가 아니라 "확인 못 했다" 이고, 그때는 확정 답을 만들지 않는다.
  커머스에서 이걸 빠뜨려 반품 제한을 안 보고 "정책 근거상 가능합니다" 라고
  답한 결함이 있었다.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.contracts import NextAction, TeamManifest, TeamResult, TeamTask

from .._base import TravelTeamBase
from ..itinerary_changes import NoChange, plan_activity_adjustment, plan_nearby_store
from ..itinerary_team import ITINERARY_TOOLS, ItineraryWork
from . import failure_codes as fc
from .cancellation import CancellationMixin
from .feasibility import FeasibilityMixin
from .replacement import ReplacementMixin
from .weather import WeatherMixin

_failure_log = logging.getLogger(fc.LOGGER_NAME)


class ActivityTeam(FeasibilityMixin, WeatherMixin, CancellationMixin, ReplacementMixin,
                   ItineraryWork, TravelTeamBase):
    manifest = TeamManifest(
        team_id="activity",
        display_name="Activity Team",
        contract_name="a_cop.team_task",
        supported_contract_versions=["1.0"],
        capabilities=[
            "activity.check_cancelable",   # 지금 취소할 수 있나 · 위약금은 얼마인가
            "activity.check_feasible",     # 이 시각에 이 활동이 성립하나
            "activity.propose_change",     # 대안을 제안한다 (승인 대기)
            "activity.submit_itinerary",   # ★`[2026-09-20]` 고객 직접 일정 제출 — 예약 없이 장소·시각만
            "activity.itinerary",          # ★`[2026-09-17]` 여행 일정 관리 — 감시 Case · 품절 · 재요청
            "activity.itinerary_question", # ★`[2026-09-25]` 규정 질문 — 규정을 읽는다(면제 아님)
        ],
        accepted_case_types=["activity"],
        # ★`[2026-09-17]` `policy` 를 뺐다 — 규정은 `read.policy` 도구로 **직접** 읽고(없으면 모름),
        #   일정 관리는 실시간 사실로 판단한다. 선언에 두면 정책 검색 0건이 Case 전체를 degraded 로 만든다.
        # ★`[2026-09-22]` **되돌렸다.** 그때 0건이던 까닭은 코퍼스가 쇼핑몰 25문서뿐이고 여행 문서가
        #   0건이었기 때문이다(`knowledge/travel/` 12문서·130청크로 채웠다). 이제 아래 scope 에
        #   문서가 있어 검색이 0건으로 떨어지지 않는다. `policy` 를 선언해야 Controller 가 RAG 를
        #   돌고(`app/application/controller.py:134`) 그 결과가 ContextPack·Evidence 에 실린다 —
        #   「근거 없는 문장 금지」(CLAUDE.md §0.1)를 지키려면 근거가 실제로 실려야 한다.
        required_context=["case_state", "policy", "db_facts", "history"],
        # ★일정 관리만 면제한다 — 예보·운행·영업 같은 **실시간 사실**로 판단하는 일이라
        #   정책 검색 결과에 막히면 안 된다(감시 루프가 여는 Case 가 전부 사람에게 간다).
        policy_optional_capabilities=["activity.itinerary"],
        # ★`[2026-09-23]` `read.booking_terms` 를 더했다 — **수치는 이 도구가** 댄다.
        #   `read.policy` 는 그대로 **문장 근거**를 댄다. 둘의 몫이 갈린다
        #   (`wiki/records/reports/debugs/2026-09-22_정책청크에서_수치를_못_꺼낸다.md`).
        allowed_tools=["read.booking", "read.booking_terms", "read.policy", "read.place",
                       "read.weather", "read.disaster", "read.place_lookup",
                       "read.place_candidates", "read.disruptions", *ITINERARY_TOOLS],
        # ★`[2026-09-22]` 여행 scope 로 바꿨다. 앞 값(`activity`·`cancellation`·`refund`·`weather`)
        #   가운데 **`refund` 는 쇼핑몰 코퍼스에 실재하는 scope** 라, 정책을 켜는 순간 활동 판정이
        #   쇼핑몰 환불 문서를 근거로 집어 왔다. 이름이 겹치지 않게 `travel_` 을 붙이고
        #   겹침 0건을 `scripts/check_corpus.py` 검사 9 가 센다.
        knowledge_scope=["travel_activity", "travel_weather", "travel_cancellation", "travel_access"],
        # ★대안 후보마다 재점검한다(도구 1회씩) — 6 으로는 후보 셋에서 예산이 끝난다.
        max_steps=12,
        active=True,
        implementation_revision="2026-09-22",
        default_capability="activity.check_feasible",
    )


    @staticmethod
    def _hours_until(when: Any) -> float | None:
        """예약 시각까지 남은 시간. 모르면 `None`."""
        if not isinstance(when, datetime):
            return None
        reference = when if when.tzinfo else when.replace(tzinfo=UTC)
        return (reference - datetime.now(UTC)).total_seconds() / 3600

    def _record_failure(self, task: TeamTask, code: str, **meta: Any) -> str:
        """실패·예외 코드를 한 줄 JSON 으로 남기고 코드를 돌려준다(`failure_codes.py`).

        ★좌표·장소명·고객 문장은 싣지 않는다 — Case id·capability·코드와 짧은 메타(도구 이름·사유 코드·예외 종류)만.
        ★기록은 **알리는 것**일 뿐 흐름을 바꾸지 않는다. 어디에 쓸지는 운영의 logging 설정이 정한다.
        """
        _failure_log.warning(json.dumps(
            {"event": "activity_failure", "code": code, "team": self.manifest.team_id,
             "case_id": str(task.case_id), "capability": task.capability, **meta},
            ensure_ascii=False, default=str))
        return code

    def _read(self, task: TeamTask, name: str, arguments: dict[str, Any], seen: set[str]) -> Any:
        """도구 예외(API·DB 실패)를 코드로 남기고 **그대로 다시 던진다** — 삼켜서 「모름」으로 바꾸지 않는다(RULE §3.2)."""
        try:
            return super()._read(task, name, arguments, seen)
        except Exception as exc:
            self._record_failure(task, fc.TOOL_ERROR, tool=name, error=type(exc).__name__)
            raise

    @staticmethod
    def select_capability(intent: str | None, input_text: str, state: dict | None = None) -> str | None:
        """★여행이 정해진 Case 는 일정 관리로 — 그 밖은 기본 동작에 맡긴다."""
        if intent == "itinerary_submit":
            return "activity.submit_itinerary"
        if intent == "adjust_reject":
            return "activity.propose_change"
        return ItineraryWork.itinerary_route("activity", state)

    async def handle_trigger(self, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
        """감시가 연 Case — 그 항목을 **다시 점검**하고, 깨졌으면 대안을 계산해 제안한다."""
        trigger = task.context.current_state.get("trigger") or {}
        item = next((i for i in ctx["items"] if str(i.item_id) == str(trigger.get("item_id"))), None)
        if item is None:
            return self.settle(task, ctx, NoChange("gone"))
        if item.kind != "activity" or item.place is None:
            return self._escalate(task, "target_kind_mismatch", ctx["evidence"])
        report = self._read(task, "read.disruptions", self.check_arguments(item.place, item.starts_at),
                            ctx["seen"])
        ctx["evidence"] = self._evidence(task, source_id="read.disruptions", claim="성립 점검",
                                         value=report, base=ctx["evidence"])
        if report is None or report.get("verdict") == "fatal":
            # ★점검 소스가 대체까지 실패 — 「clear」로 읽지 않는다(결정 15).
            return self._escalate(task, "fatal_source_failure", ctx["evidence"])
        if report.get("verdict") != "disrupted":
            return self.settle(task, ctx, NoChange("clear"))
        places = self.catalog(task, ctx)
        if places is None:
            return self._unknown(task, "장소 목록", ctx["evidence"])
        plan = plan_activity_adjustment(item=item, report=report, places=places,
                                        check=self.recheck(task, ctx), now=ctx["at"])
        return self.settle(task, ctx, plan)

    async def handle_report(self, task: TeamTask, kind: str, ctx: dict[str, Any]) -> TeamResult:
        if kind != "stock_out":
            return await super().handle_report(task, kind, ctx)
        products = list(ctx["report"].get("products") or [])
        if not products:
            return self._unknown(task, "품절 상품", ctx["evidence"])
        places = self.catalog(task, ctx)
        if places is None:
            return self._unknown(task, "장소 목록", ctx["evidence"])
        answer = plan_nearby_store(items=ctx["items"], places=places, at=ctx["at"], products=products,
                                   message=task.input_text,
                                   request_id=task.context.current_state.get("request_id"))
        if answer.get("status") != "answered":
            return self._escalate(task, "store_unresolved", ctx["evidence"],
                                  warnings=[str(answer.get("message") or "동선 위 매장을 찾지 못했다")])
        # ★일정은 바꾸지 않는다 — 제안 없이 답만. 재고는 `[미확인]` 으로 말한다.
        return self._result(task, outcome="completed", confidence=0.7, evidence=ctx["evidence"],
                            answer=answer["text"], next_action=NextAction.RESPOND,
                            decisions=[{"itinerary": "store_recommended",
                                        "recommendation": answer["recommendation"],
                                        "stock": answer["stock"],
                                        "other_options": answer["other_options"]}])

    async def execute(self, task: TeamTask) -> TeamResult:
        blocked = self._guard(task)
        if blocked is not None:
            return blocked
        if task.capability in (self.itinerary_capability, self.question_capability):
            return await self.run_itinerary(task)

        if task.capability == "activity.submit_itinerary":
            return self._submit_itinerary(task)

        seen: set[str] = set()
        booking = self._read(task, "read.booking", {"case_id": str(task.case_id)}, seen)
        policy = self._read(task, "read.policy", {"query": task.input_text}, seen)

        evidence = self._evidence(task, source_id="read.booking",
                                  claim="예약 내역", value=booking)
        evidence = self._evidence(task, source_id="read.policy",
                                  claim="취소·환급 규정", value=policy, base=evidence)

        # ★예약을 모르면 아무것도 판정하지 않는다. 여기서 지어내면 그 오류가
        #   그대로 고객 답변까지 간다.
        if booking is None:
            return self._unknown(task, "예약 내역", evidence)
        feasible_only = task.capability == "activity.check_feasible"
        # ★성립 판정(`check_feasible`)은 취소·환급 규정을 쓰지 않는다 — 규정이 없다고 사람에게 넘기지 않는다.
        #   규정이 필요한 `check_cancelable`·`propose_change` 는 그대로 모르면 멈춘다.
        if not policy and not feasible_only:
            return self._unknown(task, "취소·환급 규정", evidence)

        remaining = self._hours_until(booking.get("starts_at"))
        # ★시각을 모르면 성립 판정은 「정보 부족」으로 답한다(escalate 가 아니다). 나머지는 시각이 꼭 필요하다.
        if remaining is None and not feasible_only:
            return self._unknown(task, "예약 시각", evidence)

        if task.capability == "activity.check_cancelable":
            # ★수치는 규정 문장이 아니라 **이 예약의 조건**에서 온다(마이그레이션 023).
            terms = self._read(task, "read.booking_terms",
                               {"booking_id": booking.get("booking_id")}, seen)
            evidence = self._evidence(task, source_id="read.booking_terms",
                                      claim="이 예약의 취소 조건", value=terms, base=evidence)
            return self._check_cancelable(task, booking, terms, remaining, evidence)
        if task.capability == "activity.check_feasible":
            return self._check_feasible(task, booking, policy, remaining, evidence, seen)
        return self._propose_change(task, booking, evidence)

    # ── ② 일정 제출 — 예약 없이 장소·시각으로 ───────────────────
    def _submit_itinerary(self, task: TeamTask) -> TeamResult:
        """★예약 없는 일정 제출. read.booking을 부르지 않는다."""
        state = task.context.current_state
        place_name = state.get("requested_place_name")
        activity_time = state.get("requested_activity_time")

        # ★검색이 비어도 고객이 요청한 값 자체가 근거로 남아야 한다.
        evidence = self._evidence(task, source_id="case.current_state",
                                  claim="고객 요청 장소·시각",
                                  value={"requested_place_name": place_name,
                                         "requested_activity_time": activity_time})
        if not place_name:
            return self._unknown(task, "요청 장소명", evidence)
        if not isinstance(activity_time, datetime):
            return self._unknown(task, "요청 시각", evidence)

        seen: set[str] = set()
        found = self._read(task, "read.place_lookup", {"name": place_name}, seen)
        # ★결과에는 카카오가 준 값이 없다(판정만) — 근거로 저장해도 약관에 걸리지 않는다(`place_lookup.py`).
        if found is not None:
            evidence = self._evidence(task, source_id="read.place_lookup",
                                      claim="장소 조회 결과", value=found, base=evidence)
        status = (found or {}).get("status")

        if status == "unknown":
            # ★못 물어본 것이다 — 「없음」이 아니다. 고객에게 이름을 다시 쓰라고 하지 않고 사람에게 넘긴다.
            self._record_failure(task, fc.PLACE_LOOKUP_BLOCKED, reason=found.get("reason"))
            return self._unknown(task, "장소 조회", evidence)
        if status == "ambiguous":
            return self._ask_place(
                task, evidence, fc.PLACE_AMBIGUOUS,
                f"'{place_name}'은(는) 여러 곳을 가리킵니다"
                f"({', '.join(c['title'] for c in found.get('candidates', []))} 등 {found.get('match_count')}곳). "
                "어느 곳인지 정확한 이름이나 주소를 알려주시겠어요?")
        if status == "exists_unregistered":
            # ★실재는 하지만 우리 목록에 없다. 카카오 값을 저장하지 않으므로 일정 제안을 만들지 않고 되묻는다.
            return self._ask_place(
                task, evidence, fc.PLACE_EXISTS_UNREGISTERED,
                f"'{place_name}'은(는) 실제로 있는 장소로 확인되지만 지원하는 장소 목록에 없어 "
                "일정을 만들 수 없습니다. 정확한 이름이나 주소를 알려주시겠어요?")
        if status != "found":
            return self._ask_place(
                task, evidence, fc.PLACE_NOT_FOUND,
                f"'{place_name}'을(를) 찾지 못했습니다. 좀 더 정확한 장소 이름을 알려주시겠어요?")

        proposal = self._proposal(task, "activity.submit",
                                  arguments={"content_id": found["content_id"],
                                             "activity_time": activity_time.isoformat()},
                                  evidence=evidence, risk="low")
        return self._result(
            task, outcome="completed", confidence=0.9, evidence=evidence,
            next_action=NextAction.WAIT_FOR_APPROVAL,
            action_proposals=[proposal],
            answer=f"{found.get('matched_title', place_name)} 일정 제출 제안을 만들었습니다. 확인 후 승인해 주세요.")

    def _ask_place(self, task: TeamTask, evidence: list, code: str, answer: str) -> TeamResult:
        """장소를 하나로 못 정했을 때 — 사람에게 넘기지 않고 고객에게 되묻는다."""
        return self._result(
            task, outcome="completed", confidence=0.5, evidence=evidence,
            next_action=NextAction.WAIT_FOR_INPUT,
            decisions=[{"failure_code": self._record_failure(task, code)}],
            answer=answer,
            required_input_schema={"type": "object",
                                   "properties": {"place_hint": {"type": "string"}},
                                   "required": ["place_hint"]})
