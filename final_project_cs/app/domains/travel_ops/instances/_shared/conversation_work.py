"""웹 업무도 읽기·계산·제안만 한다. 고객의 추가 답변은 같은 업무의 빈칸을 채운다."""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from app.core.contracts import NextAction, TeamResult, TeamTask
from app.domains.travel_ops.components.itinerary.itinerary_changes import (
    NoChange, plan_fresh_alternate, plan_swap,
)


def _text(value: Any) -> str:
    return str(value.get("message") or "") if isinstance(value, dict) else str(value or "")


def replies(task: TeamTask) -> list[str]:
    state = task.context.current_state
    history = [_text(v).strip() for v in state.get("customer_input_history") or []]
    latest = _text(state.get("customer_input")).strip()
    if latest and (not history or history[-1] != latest):
        history.append(latest)
    return [v for v in history if v]


def delay_minutes(text: str) -> int | None:
    """분 단위 답변만 해석한다. 전화번호·음수·소수·범위는 지연 시간으로 쓰지 않는다."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(?:약\s*)?(\d+)\s*(?:분(?:\s*정도)?(?:이요|이예요|이에요|요)?|이요|요)?[.!]?\s*", text)
    if match and 1 <= int(match[1]) <= 1440:
        return int(match[1])
    match = re.fullmatch(r"\s*(\d+)\s*시간\s*(?:(\d+)\s*분)?(?:이요|이에요|요)?[.!]?\s*", text)
    if match:
        value = int(match[1]) * 60 + int(match[2] or 0)
        return value if 1 <= value <= 1440 else None
    return None


def wait_for_input(work: Any, task: TeamTask, ctx: dict[str, Any], question: str,
                   field: str, *, choices: list[dict[str, Any]] | None = None) -> TeamResult:
    schema: dict[str, Any] = {"type": "object", "required": [field],
                              "properties": {field: {"type": "string", "minLength": 1}},
                              "x-conversation": {"request": dict(ctx["request"]),
                                                 "part_id": ctx["ref"].get("part_id")}}
    if choices:
        schema["choices"] = choices
    return work._result(task, outcome="waiting", confidence=1.0, answer=question,
                        evidence=ctx["evidence"], next_action=NextAction.WAIT_FOR_INPUT,
                        required_input_schema=schema,
                        decisions=[{"itinerary": "clarify", "required": field,
                                    **({"choices": choices} if choices else {})}])


def _choice(choices: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    match = re.fullmatch(r"\s*(\d+)\s*(?:번(?:이요|요)?|번째)?[.!]?\s*", text)
    if match and 1 <= int(match[1]) <= len(choices):
        return choices[int(match[1]) - 1]
    hits = [c for c in choices if text in (str(c.get("message") or ""), str(c.get("label") or ""))]
    return hits[0] if len(hits) == 1 else None


def _target(items: list[Any], text: str) -> Any | None:
    # 동명이거나 같은 제목이면 고객에게 다시 묻는다. 첫 항목을 임의 선택하지 않는다.
    hits = [i for i in items if text == str(i.item_id) or text == i.title
            or ((i.place or {}).get("name") and text == i.place["name"])]
    return hits[0] if len(hits) == 1 else None


async def run_conversation(work: Any, task: TeamTask, ctx: dict[str, Any]) -> TeamResult:
    pending_schema = task.context.current_state.get("required_input_schema") or {}
    pending = pending_schema.get("x-conversation") or {}
    request = {**ctx["request"], **(pending.get("request") or {})}
    ref = dict(ctx["ref"])
    if pending.get("part_id"):
        ref["part_id"] = pending["part_id"]
    ctx = {**ctx, "request": request, "ref": ref}
    answers = replies(task)
    action = request.get("action")
    chosen: dict[str, Any] | None = None
    choices = pending_schema.get("choices") or request.get("choices") or []
    for answer in answers:
        option = _choice(choices, answer)
        if option is not None:
            chosen = option
    if action == "clarify":
        action = request.get("pending_action")
        if chosen:
            action = chosen.get("action") or action
            target = chosen.get("item_id") or chosen.get("target_item_id")
            if target:
                ref["part_id"] = str(target)
            for key in ("minutes", "to_version", "confirmed", "choice", "fact"):
                if key in chosen:
                    request[key] = chosen[key]
        if not action and answers:
            report = work._read(task, "read.customer_report", {"text": answers[-1]}, ctx["seen"]) or {}
            ctx["evidence"] = work._evidence(task, source_id="read.customer_report",
                claim="추가 답변에서 확인한 업무", value=report, base=ctx["evidence"])
            action = {"delay": "report_delay", "closed": "report_closed", "change": "apply_change",
                      "rollback": "rollback"}.get(report.get("type"))
            for key in ("minutes", "to_version"):
                if report.get(key) is not None:
                    request[key] = report[key]
        if not action:
            return wait_for_input(work, task, ctx, request.get("question") or "어떤 일정을 어떻게 바꿀까요?",
                                  "message", choices=request.get("choices"))
    request["action"] = action

    target = ref.get("part_id") or request.get("target_item_id")
    if not target and chosen:
        target = chosen.get("item_id") or chosen.get("target_item_id")
    if not target:
        for answer in answers:
            item = _target(ctx["items"], answer)
            if item:
                target = str(item.item_id)
    if target:
        ref["part_id"] = str(target)
    item = next((i for i in ctx["items"] if str(i.item_id) == str(target)), None)
    if action == "answer_fact":
        from app.domains.travel_ops.components.conversation.trip_facts import fact_reply

        fact = request.get("fact") or "detail"
        if item is None and fact not in ("day", "booking", "next"):
            return wait_for_input(work, task, ctx, "어느 일정에 관한 정보가 필요하세요?", "target_item_id")
        answer, basis = fact_reply(fact, message=answers[-1] if answers else task.input_text,
                                   items=ctx["items"], now=ctx["at"], item=item)
        evidence = work._evidence(task, source_id="trip.facts", claim="여행 기록에 있는 사실",
                                  value=basis, base=ctx["evidence"])
        return work._result(task, outcome="completed", confidence=1, evidence=evidence, answer=answer,
                            next_action=NextAction.RESPOND, decisions=[{"itinerary": "answered", "basis": basis}])
    if item is not None and item.kind != work.manifest.team_id:
        return work._escalate(task, "target_kind_mismatch", ctx["evidence"])

    if action == "report_delay":
        minutes = request.get("minutes")
        # 같은 숫자라도 선택지를 고른 답과 지연 시간을 알려 준 답은 다르다.
        latest = answers[-1] if answers else ""
        if latest and (pending_schema.get("required") == ["minutes"] or _choice(choices, latest) is None):
            parsed = delay_minutes(latest)
            if parsed is not None:
                minutes = parsed
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            return wait_for_input(work, task, ctx, "몇 분쯤 늦으세요? 예를 들어 ‘30분이요’라고 알려 주세요.", "minutes")
        ctx["report"] = {**request, "type": "delay", "minutes": minutes}
        return await work.handle_report(task, "delay", ctx)
    if action == "report_closed":
        if item is not None and item.kind != "dining":
            return _fresh_change(work, task, ctx, item)
        ctx["report"] = {**request, "type": "closed"}
        return await work.handle_report(task, "closed", ctx)
    if action in ("apply_change", "propose_alternatives"):
        if item is None:
            if target:
                return work.settle(task, ctx, NoChange("gone"))
            choices = [{"label": i.title, "message": i.title, "item_id": str(i.item_id),
                        "action": action} for i in ctx["items"] if i.kind == work.manifest.team_id and i.place]
            return wait_for_input(work, task, ctx, "어느 일정을 다른 곳으로 바꿀까요?", "target_item_id", choices=choices)
        if getattr(work, "conversation_change_by_team", False):
            # ★`[2026-10-15 숙박예약 점검 LB-02]` 후보를 **직접 찾는** 팀(숙소)은 식당 · 활동의 장소 목록으로 대신 고르지 않는다 — 전에는 이 갈래에 들어오지 못하고
            #   아래 공용 계획(`_fresh_plan`)이 「장소를 바꿀 수 있는 일정(식사 · 활동)이 아니다」로 답했다. 팀 자신의 「바꿔 줘」(`_change`: 새 후보를
            #   「선택이 필요해요」 제안으로 내거나, 고른 후보로 바꾼다)로 보낸다
            if chosen and chosen.get("choice") is not None:
                request["choice"] = chosen["choice"]
            return work._change(task, ctx)
        if action == "propose_alternatives":
            return _propose_alternatives(work, task, ctx, item)
        if item.detail.get("alternates"):
            if chosen and chosen.get("choice") is not None:
                request["choice"] = chosen["choice"]
            return work._change(task, ctx)
        return _fresh_change(work, task, ctx, item)
    if action in ("rollback", "redo"):
        to_version = request.get("to_version")
        if to_version is None and answers:
            for answer in answers:
                version_answer = re.fullmatch(r"\s*(?:v|버전\s*)?(\d+)\s*(?:버전|판)?(?:이요|요)?[.!]?\s*", answer, re.IGNORECASE)
                if version_answer:
                    to_version = int(version_answer[1])
        if to_version is None:
            return wait_for_input(work, task, ctx,
                                  request.get("question") or "어느 변경 전 일정으로 되돌릴까요? 버전을 알려 주세요.",
                                  "to_version")
        request["to_version"] = to_version
        if request.get("confirmed") is False:
            latest = answers[-1].strip().rstrip(".!?") if answers else ""
            if latest in ("아니요", "아니", "취소", "그대로", "유지"):
                return work._result(task, outcome="completed", confidence=1, evidence=ctx["evidence"],
                                    answer="일정은 그대로 두었어요.", next_action=NextAction.RESPOND,
                                    decisions=[{"itinerary": "unchanged"}])
            affirmative = re.fullmatch(
                r"(?:(?:네|예|응|맞아요)[,，]?\s*)?(?:그렇게\s*해\s*줘|되돌려(?:\s*(?:줘|주세요))?)"
                r"|네|예|응|맞아요", latest)
            if affirmative is None:
                return wait_for_input(work, task, ctx, request.get("question") or "그 변경 전 일정으로 되돌릴까요?", "confirmed")
        try:
            version = int(to_version)
        except (TypeError, ValueError):
            return wait_for_input(work, task, ctx, "되돌릴 일정 버전을 숫자로 알려 주세요.", "to_version")
        ctx["report"] = {**request, "type": "rollback", "to_version": version}
        return work._rollback(task, ctx)
    return work._escalate(task, "conversation_action_not_supported", ctx["evidence"])


def _fresh_plan(work: Any, task: TeamTask, ctx: dict[str, Any], item: Any) -> Any:
    places = work.catalog(task, ctx)
    if places is None:
        return work._unknown(task, "장소 목록", ctx["evidence"])
    dining = work.manifest.team_id == "dining"
    return plan_fresh_alternate(
        trip=ctx["trip"], trip_version=ctx["trip"]["version"],
        base_version=int(ctx["ref"].get("base_version") or ctx["trip"]["version"]),
        items=ctx["items"], places=places, item_id=item.item_id,
        message=task.input_text, request_id=task.context.current_state.get("request_id"),
        state_lookup=work._state_lookup(task, ctx) if dining else None,
        price_lookup=work._price_lookup(task, ctx) if dining else None)


def _propose_alternatives(work: Any, task: TeamTask, ctx: dict[str, Any], item: Any) -> TeamResult:
    from app.domains.travel_ops.components.planning.pending import options_from

    plan = _fresh_plan(work, task, ctx, item)
    if isinstance(plan, TeamResult):
        return plan
    if isinstance(plan, NoChange):
        if plan.status != "relaxed":
            return work.settle(task, ctx, plan)
        options = plan.detail.get("options") or []
    else:
        options = options_from(plan, item)
    original = str((item.place or {}).get("place_id") or item.place_id)
    options = work._plain({"options": [{**o, "rank": n} for n, o in enumerate(
        (o for o in options if str(o.get("place_id") or "") != original), start=1)]})["options"]
    if not options:
        return work.settle(task, ctx, NoChange("no_alternate"))
    pending = task.context.current_state.get("required_input_schema") or {}
    answers = replies(task)
    selection = _choice(pending.get("choices") or [], answers[-1]) if answers and pending.get("required") == ["choice"] else None
    selected = next((o for o in options if selection and o["key"] == selection.get("choice")
                     and all(o.get(key) == selection.get(key) for key in ("place_id", "starts_at", "ends_at"))), None)
    if selected is not None:
        current = replace(item, detail={**item.detail, "alternates": options})
        places = work.catalog(task, ctx)
        swap = plan_swap(trip_version=ctx["trip"]["version"],
                         base_version=int(ctx["ref"].get("base_version") or ctx["trip"]["version"]),
                         items=[current if i.item_id == item.item_id else i for i in ctx["items"]],
                         places_by_id={str(p["place_id"]): p for p in places}, item_id=item.item_id,
                         choice=selected["key"], message=task.input_text,
                         request_id=task.context.current_state.get("request_id"),
                         check=work.recheck(task, ctx) if item.kind == "activity" else None)
        return work.settle(task, ctx, swap)
    choices = [{**o, "label": (str(o.get("note") or "") + " " + str(o.get("option_label") or o["name"])).strip(),
                "message": str(o.get("option_label") or o["name"]), "item_id": str(item.item_id),
                "action": "apply_change", "choice": o["key"]} for o in options]
    listed = " · ".join(f"{n}) {o['label']}" for n, o in enumerate(choices, start=1))
    result = wait_for_input(work, task, ctx,
                            f"일정은 바꾸지 않았어요. 다른 안은 {listed}입니다. 번호로 고르시면 바꿀게요. "
                            "답이 없으면 원래 일정을 그대로 둡니다.", "choice", choices=choices)
    result.decisions = [{"itinerary": "asked", "required": "choice", "options": options, "choices": choices}]
    return result


def _fresh_change(work: Any, task: TeamTask, ctx: dict[str, Any], item: Any) -> TeamResult:
    plan = _fresh_plan(work, task, ctx, item)
    if isinstance(plan, TeamResult):
        return plan
    dining = work.manifest.team_id == "dining"
    places = work.catalog(task, ctx)
    if isinstance(plan, NoChange) and plan.status == "relaxed":
        options = plan.detail.get("options") or []
        answer = replies(task)
        pending_schema = task.context.current_state.get("required_input_schema") or {}
        shown = pending_schema.get("choices") if pending_schema.get("required") == ["choice"] else options
        selected_option = _choice(shown or [], answer[-1]) if answer else None
        option = next((o for o in options if selected_option and all(
            o.get(key) == selected_option.get(key)
            for key in ("key", "place_id", "starts_at", "ends_at"))), None)
        if option is None:
            return wait_for_input(work, task, ctx, str(plan.detail["text"]), "choice", choices=options)
        selected = replace(item, detail={**item.detail, "alternates": options})
        plan = plan_swap(trip_version=ctx["trip"]["version"],
                         base_version=int(ctx["ref"].get("base_version") or ctx["trip"]["version"]),
                         items=[selected if i.item_id == item.item_id else i for i in ctx["items"]],
                         places_by_id={p["place_id"]: p for p in places}, item_id=item.item_id,
                         choice=option["key"], message=task.input_text,
                         request_id=task.context.current_state.get("request_id"),
                         check=work.recheck(task, ctx) if not dining else None)
    return work.settle(task, ctx, plan)
