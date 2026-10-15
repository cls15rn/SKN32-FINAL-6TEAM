"""웹의 변경 업무를 등록된 Team과 공통 적용 경로에 연결한다."""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable
from uuid import UUID

from app.core.contracts import StateConflict
from app.core.case_lifecycle.events import EventType
from app.core.transition import transition_case
from app.infrastructure.db import repository
from app.infrastructure.db.session import get_connection


BUSINESS_ACTIONS = frozenset({"clarify", "apply_change", "propose_alternatives", "rollback", "redo", "report_delay", "report_closed"})


def _run(value: Any) -> Any:
    return asyncio.run(value) if inspect.isawaitable(value) else value


def response(*, tenant: str, case_id: UUID, execution: dict[str, Any], decision: Any = None) -> dict[str, Any]:
    with get_connection() as conn:
        case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
    state = case.get("state_json") or {}
    decisions = state.get("decisions") or []
    last = next((d for d in reversed(decisions) if d.get("itinerary")), {})
    outcome = dict(last)
    status = last.get("itinerary") or ("clarify" if case["status"] == "waiting_input" else "answered")
    applied = state.get("applied_actions") or []
    if applied:
        ref = str(applied[-1].get("result_ref") or "")
        if ":v" in ref and ref.rsplit(":v", 1)[-1].isdigit():
            outcome["version"] = int(ref.rsplit(":v", 1)[-1])
        status = "rolled_back" if status == "rollback" else "adjusted"
        # 보류 제안은 실제 일정 변경과 구분한다.
        if ":proposal:" in ref:
            status = "asked"
            outcome["proposal_id"] = ref.rsplit(":proposal:", 1)[1]
    if case["status"] in ("escalated", "failed"):
        status = str(case["status"])
    if execution.get("idempotent"):
        status = "duplicate"
    outcome["status"] = status
    request = (state.get("subject_ref") or {}).get("request") or {}
    if case["status"] == "waiting_input" and (last.get("required") == "confirmed" or request.get("action") == "rollback"):
        status = "ask_rollback"
        outcome["status"] = status
    choices = last.get("choices") or request.get("choices") or []
    result = {"case_id": str(case_id), "case_status": str(case["status"]), "version": case["version"],
              "classification": {"intent": case.get("intent"), "issue_code": case.get("issue_code")},
              "status": status, "reason": "team_conversation", "outcome": outcome,
              "answer": ("요청을 완료하지 못했어요. 계획서에서 현재 일정을 확인해 주세요."
                         if case["status"] in ("escalated", "failed") else
                         state.get("answer") or "요청을 처리하지 못했어요. 다시 확인해 주세요.")}
    if decision is not None:
        result["decision"] = decision.record()
        result["reason"] = f"decision_{decision.action}"
        named = (getattr(decision, "raw", None) or {}).get("named_destination")
        if named and case["status"] not in ("escalated", "failed"):
            # ★`[2026-10-13]` 말한 곳으로 바로 바꾸지 못했음을 먼저 밝힌다(`decision_unit.decide` — 바꾸지 않고 후보를 보여 준다)
            result["answer"] = (f"말씀하신 「{named['text']}」(으)로 바로 바꾸는 기능은 아직 없어 일정은 바꾸지 않았어요. "
                                f"아래에서 고르시면 그곳으로 바꿔 드릴게요.\n{result['answer']}")
    if case["status"] == "waiting_input":
        result["required_input_schema"] = state.get("required_input_schema")
        if execution.get("resume_token"):
            result["resume_token"] = execution["resume_token"]
        if choices:
            result["choices"] = choices
    if request.get("type") == "stock_out" and status == "store_recommended":
        result["status"] = outcome["status"] = "answered"
        outcome["text"] = result["answer"]
        result["report"] = {"type": "stock_out", "products": request["products"]}
    return result


def execute(conn, *, controller: Any, tenant: str, trip: dict[str, Any], case_id: UUID,
            team: str, request: dict[str, Any], item: Any = None, actor_id: str,
            decision: Any = None, desk: Any = None, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    if controller is None:
        raise RuntimeError("conversation Controller is not configured")
    entry = controller.require_team(team, case_id=case_id)
    case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
    capability = controller.registry.capability_for(
        entry, case.get("intent"), input_text=case.get("subject"),
        state=controller._capability_state({**case, "state_json": {
            **(case.get("state_json") or {}), "subject_ref": {"request": request}}}))
    controller.require_team(team, capability, case_id=case_id)
    conn.commit()
    _prepare_candidates(desk, tenant=tenant, trip_id=UUID(str(trip["trip_id"])), request=request, item=item)
    case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
    if case["status"] != "routing":
        raise StateConflict("같은 요청의 업무가 이미 실행 중이거나 처리되었습니다")
    ref = plan["subject_ref"] if plan is not None else descriptor(trip=trip, team=team, request=request, item=item)["subject_ref"]
    with conn.transaction():
        transition_case(conn, tenant_id=tenant, case_id=case_id, expected_version=case["version"],
                        event_type=EventType.ROUTED,
                        payload={"owner_team_id": team, "capability": capability,
                                 "state_patch": {"subject_ref": ref}}, actor_type="api", actor_id=actor_id)
    conn.commit()
    execution = _run(controller.run_case(tenant_id=tenant, case_id=case_id, actor_id=actor_id))
    return response(tenant=tenant, case_id=case_id, execution=execution, decision=decision)


def descriptor(*, trip: dict[str, Any], team: str, request: dict[str, Any], item: Any = None) -> dict[str, Any]:
    """해석 시점의 실행 입력을 보존한다. 가용성 관문이나 일정 적용은 하지 않는다."""
    ref = {"kind": "trip", "id": str(trip["trip_id"]), "base_version": trip["version"], "request": request}
    if item is not None:
        ref["part_id"] = str(item.item_id)
    return {"team_id": team, "subject_ref": ref}


def retry_initial(conn, *, controller: Any, tenant: str, trip: dict[str, Any], case_id: UUID,
                  items: list[Any], actor_id: str, desk: Any = None) -> dict[str, Any] | None:
    """초기 실행 실패의 같은 요청은 저장된 해석을 사용해 같은 업무만 이어간다."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM customer_cases WHERE tenant_id=%s AND case_id=%s FOR UPDATE", (tenant, case_id))
    case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
    if case["status"] in ("waiting_input", "waiting_approval", "waiting_external", "resolved", "escalated", "failed", "cancelled"):
        return None
    if case["status"] not in ("routing", "running"):
        raise StateConflict("미완료 요청은 원래 실행 또는 답변 요청으로 재시도해 주세요")
    state = case.get("state_json") or {}
    if state.get("resume_requests") or state.get("customer_input_history"):
        raise StateConflict("답변 처리 중인 업무는 원래 답변 요청으로 재시도해 주세요")
    plan = state.get("conversation_dispatch")
    if not isinstance(plan, dict) or not plan.get("team_id") or not isinstance(plan.get("subject_ref"), dict):
        raise StateConflict("미완료 요청의 저장된 실행 계획이 없어 자동 재시도할 수 없습니다")
    ref = plan["subject_ref"]
    if (ref.get("kind") != "trip" or str(ref.get("id")) != str(trip["trip_id"])
            or not isinstance(ref.get("request"), dict) or ref["request"].get("action") not in BUSINESS_ACTIONS):
        raise StateConflict("저장된 실행 계획이 현재 여행 요청과 일치하지 않습니다")
    if ref.get("base_version") != trip["version"]:
        raise StateConflict("실패한 요청 이후 일정이 바뀌었습니다. 현재 일정으로 다시 요청해 주세요")
    if controller is None:
        raise RuntimeError("conversation Controller is not configured")
    entry = controller.require_team(plan["team_id"], case_id=case_id)
    capability = controller.registry.capability_for(entry, case.get("intent"), input_text=case.get("subject"),
        state=controller._capability_state({**case, "state_json": {**state, "subject_ref": plan["subject_ref"]}}))
    controller.require_team(plan["team_id"], capability, case_id=case_id)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agent_runs WHERE tenant_id=%s AND case_id=%s "
                    "AND status IN ('active','running','resuming') LIMIT 1", (tenant, case_id))
        if cur.fetchone() is not None:
            raise StateConflict("같은 요청의 업무가 아직 실행 중입니다")
    conn.commit()
    from app.application.case_service import ActiveRunError

    try:
        if case["status"] == "routing":
            item = next((i for i in items if str(i.item_id) == str(ref.get("part_id"))), None)
            return execute(conn, controller=controller, tenant=tenant, trip=trip, case_id=case_id,
                           team=plan["team_id"], request=ref["request"], item=item, actor_id=actor_id,
                           desk=desk, plan=plan)
        execution = _run(controller.run_case(tenant_id=tenant, case_id=case_id, actor_id=actor_id))
    except ActiveRunError as exc:
        # 사전 확인 뒤 다른 재시도가 먼저 실행권을 얻어도 500이나 성공으로 바꾸지 않는다.
        raise StateConflict("같은 요청의 업무가 아직 실행 중입니다") from exc
    return response(tenant=tenant, case_id=case_id, execution=execution)


def resume_pending(conn, *, controller: Any, tenant: str, trip: dict[str, Any], request_id: str,
                   message: str, actor_id: str, case_id: UUID | None, token: str | None,
                   expected_version: int | None, desk: Any = None, chat: Any = None,
                   fresh: Callable[[], Any] | None = None) -> dict[str, Any] | None:
    """같은 여행·고객의 입력 대기만 재개한다. 명시한 ID는 완료 뒤에도 소유권을 확인한다.

    `fresh` — ★`[2026-10-13]` 대기 건이 있어도 새 문장을 **새 요청으로 한 번 읽어 보는** 자리(해석 모델 한 번, `(결정, 실패)` 를 돌려준다).
    문장이 저장된 질문 형식에 맞는 답(번호 · 선택지 글자 · 분 · 판 번호 · 네/아니요)이면 부르지 않는다. 맞지 않는데 새로 읽은 결과가
    혼자서도 완결된 요청(`decision_unit.stands_alone`)이면 대기 건을 잇지 않고 `{"fresh_decision": (결정, 실패)}` 를 돌려준다 —
    부르는 쪽이 그 해석으로 새 요청을 처리한다. 완결되지 않으면(「아직 몰라요」 같은 말) 지금처럼 대기 건의 답으로 넘긴다."""
    with conn.cursor() as cur:
        cur.execute("SELECT case_id, state_json->'resume_requests' @> %s::jsonb FROM customer_cases WHERE tenant_id=%s AND customer_id=%s "
                    "AND state_json->'trip_message'->>'trip_id'=%s AND "
                    "((status='waiting_input' AND state_json->>'wait_reason'='customer_input') "
                    "OR state_json->'resume_requests' @> %s::jsonb) ORDER BY created_at, case_id",
                    (json.dumps([{"request_id": request_id}]), tenant, trip["customer_id"], str(trip["trip_id"]),
                     json.dumps([{"request_id": request_id}])))
        rows = cur.fetchall()
        replayed = [row[0] for row in rows if row[1]]
        candidates = replayed or [row[0] for row in rows]
    implicit = case_id is None and token is None and not replayed
    if implicit:
        # 대기 중이라는 이유만으로 새 문의를 이전 질문의 답변으로 소비하지 않는다.
        candidates = [candidate for candidate in candidates if _matches_implicit_reply(
            (repository.get_case(conn, tenant_id=tenant, case_id=candidate) or {}).get("state_json") or {}, message)]
        if candidates:
            import re

            if re.fullmatch(r"\d+\s*(?:번(?:이요|요)?|번째)?[.!]?", message.strip()):
                from . import chat_log

                turns = chat_log.recent(conn, tenant_id=tenant, trip_id=trip["trip_id"])
                latest = next((turn for turn in reversed(turns) if turn.get("role") == "assistant"), None)
                # 숫자는 방금 보인 안내를 가리킨다. 새 안내 뒤 옛 선택 질문을 재개하지 않는다.
                if latest and latest.get("case_id"):
                    candidates = [candidate for candidate in candidates
                                  if str(candidate) == str(latest["case_id"])]
    if case_id is None:
        if not candidates:
            return None
        if len(candidates) > 1:
            raise StateConflict("답변할 업무를 지정해 주세요")
        case_id = candidates[0]
    case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
    state = (case or {}).get("state_json") or {}
    if (case is None or str(case["customer_id"]) != str(trip["customer_id"])
            or (state.get("trip_message") or {}).get("trip_id") != str(trip["trip_id"])):
        raise LookupError("case not found")
    if fresh is not None and not replayed and not _matches_implicit_reply(state, message):
        from .decision_unit import stands_alone

        decided = fresh()
        if stands_alone(decided[0]):
            return {"fresh_decision": decided}
    if controller is None:
        raise RuntimeError("conversation Controller is not configured")
    conn.commit()
    patch = _interpret_reply(state, message, chat)
    def route(current, safe_message):
        current_state = {**(current.get("state_json") or {}), **patch}
        owner = _resume_route({**current, "state_json": current_state}, safe_message)
        return {"owner_team_id": owner or current["owner_team_id"], "state_patch": patch} if patch else owner

    def prepare(current, safe_message):
        current_state = current.get("state_json") or {}
        ref = current_state.get("subject_ref") or {}
        request = ((current_state.get("required_input_schema") or {}).get("x-conversation") or {}).get("request") or ref.get("request") or {}
        _prepare_candidates(desk, tenant=tenant, trip_id=UUID(str(trip["trip_id"])), request=request,
                            message=safe_message, state=current_state)

    arguments = dict(tenant_id=tenant, case_id=case_id, actor_id=actor_id, event_id=request_id,
                     message=message, expected_version=expected_version, resume_route=route,
                     resume_prepare=prepare)
    from app.application.controller import ResumeExpiredError

    try:
        execution = _run(controller.resume(token=token, **arguments) if token is not None else
                         controller.resume_customer_input(customer_id=trip["customer_id"], **arguments))
    except ResumeExpiredError:
        if not implicit:
            raise
        # Controller가 WAIT_EXPIRED를 기록했다. 명시 연결이 없는 새 메시지는 버리지 않는다.
        return None
    return response(tenant=tenant, case_id=case_id, execution=execution)


def _matches_implicit_reply(state: dict[str, Any], message: str) -> bool:
    """저장한 질문 형식에 명확히 맞는 짧은 답만 자동 연결한다. 업무 해석은 Team이 한다."""
    import re

    schema = state.get("required_input_schema") or {}
    request = (schema.get("x-conversation") or {}).get("request") or {}
    choices = schema.get("choices") or request.get("choices") or []
    text = message.strip()
    if any(text and text in (choice.get("message"), choice.get("label"), choice.get("item_id")) for choice in choices):
        return True
    match = re.fullmatch(r"(\d+)\s*(?:번(?:이요|요)?|번째)?[.!]?", text)
    if match and 1 <= int(match[1]) <= len(choices):
        return True
    required = schema.get("required") or []
    if required == ["minutes"]:
        return re.fullmatch(r"(?:약\s*)?\d+\s*(?:분(?:\s*정도)?(?:이요|이예요|이에요|요)?|이요|요)?[.!]?"
                            r"|\d+\s*시간\s*(?:\d+\s*분)?(?:이요|이에요|요)?[.!]?", text) is not None
    if required == ["to_version"]:
        return re.fullmatch(r"(?:v|버전\s*)?\d+\s*(?:버전|판)?(?:이요|요)?[.!]?", text, re.IGNORECASE) is not None
    if required == ["confirmed"]:
        return re.fullmatch(r"(?:(?:네|예|응|맞아요)[,，]?\s*)?(?:그렇게\s*해\s*줘|되돌려(?:\s*(?:줘|주세요))?)"
                            r"|네|예|응|맞아요|아니요|아니|취소|그대로|유지", text.rstrip(".!?")) is not None
    return False


def _interpret_reply(state: dict[str, Any], message: str, chat: Any) -> dict[str, Any]:
    """질문에 온 자유 답변을 같은 추출기로 한 번 읽고 팀과 공유한다."""
    schema = state.get("required_input_schema") or {}
    pending = schema.get("x-conversation") or {}
    request = pending.get("request") or {}
    if request.get("action") != "clarify" or schema.get("required") != ["message"]:
        return {}
    choices = schema.get("choices") or request.get("choices") or []
    if any(message.strip() in (c.get("message"), c.get("label")) for c in choices):
        return {}
    import re

    if re.fullmatch(r"\s*\d+\s*(?:번(?:이요|요)?|번째)?[.!]?\s*", message) or not callable(getattr(chat, "json", None)):
        return {}
    from .trip_intake import extract

    report = extract(message, chat) or {}
    action = {"delay": "report_delay", "closed": "report_closed", "change": "apply_change",
              "rollback": "rollback"}.get(report.get("type"))
    if action is None:
        return {}
    typed = {**request, "action": action, **{k: report[k] for k in ("minutes", "to_version") if k in report}}
    ref = {**state["subject_ref"], "request": typed}
    ref.pop("part_id", None)
    return {"subject_ref": ref, "required_input_schema": {
        **schema, "choices": [], "x-conversation": {"request": typed, "part_id": None}}}


def _resume_route(case: dict[str, Any], message: str | None) -> str | None:
    """저장된 후보에서 고른 실제 항목의 팀으로만 넘긴다."""
    import re

    state = case.get("state_json") or {}
    ref = state.get("subject_ref") or {}
    schema = state.get("required_input_schema") or {}
    request = (schema.get("x-conversation") or {}).get("request") or ref.get("request") or {}
    choices = schema.get("choices") or request.get("choices") or []
    text = (message or "").strip()
    choice = None
    match = re.fullmatch(r"(\d+)\s*(?:번(?:이요|요)?|번째)?[.!]?", text)
    if match and 1 <= int(match[1]) <= len(choices):
        choice = choices[int(match[1]) - 1]
    else:
        hits = [c for c in choices if text in (c.get("message"), c.get("label"))]
        if len(hits) == 1:
            choice = hits[0]
    action = (choice or {}).get("action") or request.get("pending_action") or request.get("action")
    if action == "report_delay":
        return "dining"
    target = (choice or {}).get("item_id") or (choice or {}).get("target_item_id")
    if not ref.get("id"):
        return None
    from app.domains.travel_ops.components.itinerary.itinerary import TripStore

    with get_connection() as conn:
        _, items = TripStore(case["tenant_id"]).latest(conn, UUID(str(ref["id"])))
    matches = [i for i in items if str(i.item_id) == str(target) or text in (i.title, str(i.item_id))]
    if len(matches) == 1 and matches[0].kind in ("dining", "activity", "mobility", "lodging"):    # ★`[2026-10-15 LB-02]` 숙소도 자기 팀으로
        return matches[0].kind
    return None


def _prepare_candidates(desk: Any, *, tenant: str, trip_id: UUID, request: dict[str, Any],
                        item: Any = None, message: str | None = None, state: dict[str, Any] | None = None) -> None:
    """팀 실행 전에 기존 원장·관광 목록의 후보 확보를 유지한다. 일정은 저장하지 않는다."""
    if desk is None:
        return
    from datetime import datetime
    from app.domains.travel_ops.components.itinerary.itinerary import TripStore

    with get_connection() as conn:
        _, items = TripStore(tenant).latest(conn, trip_id)
        places = TripStore(tenant).places(conn, trip_id)
    state = state or {}
    schema = state.get("required_input_schema") or {}
    choices = schema.get("choices") or request.get("choices") or []
    selected = next((c for c in choices if message and message in (c.get("message"), c.get("label"))), None)
    if selected is None and message:
        import re
        match = re.fullmatch(r"(\d+)\s*(?:번(?:이요|요)?|번째)?[.!]?", message)
        if match and 1 <= int(match[1]) <= len(choices):
            selected = choices[int(match[1]) - 1]
    action = (selected or {}).get("action") or request.get("pending_action") or request.get("action")
    target = (selected or {}).get("item_id") or (state.get("subject_ref") or {}).get("part_id")
    if item is None:
        matches = [i for i in items if str(i.item_id) == str(target) or (message and message in (i.title, str(i.item_id)))]
        item = matches[0] if len(matches) == 1 else None
    if action in ("report_delay", "report_closed") and item is None:
        at = datetime.fromisoformat(str(request["at"]))
        item = next((i for i in items if i.kind == "dining" and (i.ends_at or i.starts_at) > at), None)
    if action not in ("apply_change", "propose_alternatives", "report_delay", "report_closed") or item is None:
        return
    if item.kind == "activity":
        desk._widen_activities(trip_id, item.item_id)
    elif item.kind == "dining" and desk._use_ledger:
        desk._add_nearby(trip_id, item, places)
