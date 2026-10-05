# -*- coding: utf-8 -*-
"""고객 자유 문장에 **늘 답 문장을 돌려준다.** `[2026-09-28]` (ui 세션 인계 · 사용자 지시)

★왜. 웹 채팅이 질문·기타 문장에 `escalated` 만 받고 답이 없어서, 화면이 제 고정 문구(「담당자에게 넘겼어요」)를
  채웠다. 사용자 결정: 그런 답은 하지 않는다 — **모든 메시지에 답이 나온다.**
★근거 없는 문장 금지(CLAUDE.md §0)는 그대로다. 답은 **서버가 가진 사실로만** 만든다:
    · 이 여행의 일정 항목(날짜 · 시각 · 장소)          — `TripStore`
    · 여행 규정 조각(출처와 함께 그대로)               — `search_policy`, 문턱 `travel.question.min_policy_score`
    · 처리 결과(바꿨다 · 안 바꿨다 · 물었다 …)          — `TripDesk` 의 결과
  모르는 것은 **모른다고 말하는 것도 답이다** — 무엇을 찾아봤고 무엇을 못 찾았는지를 말한다.
★사람 확인이 정말 필요한 경우에도 「넘겼어요」만 쓰지 않는다 — **무엇을 확인하는지**를 말한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

#: 채팅 질문이 찾아보는 여행 규정 갈래. ★`[2026-09-28]` 전에는 일정 생성기의 갈래(`PLAN_SCOPES` — 활동·식사·동행·
#: 기상)를 그대로 써서 **취소·환급(`travel_cancellation`)과 이동(`travel_mobility`) 규정을 한 번도 찾지 않았다** —
#: 「취소하면 위약금 있어요?」가 취소 규정을 못 봤다(ui 세션 점검 요청으로 DB 를 세어 찾았다: 12문서 중 4문서가 빠짐)
QUESTION_SCOPES = ("travel_activity", "travel_dining", "travel_access", "travel_weather",
                   "travel_cancellation", "travel_mobility")

#: 할 수 있는 일 — 잡담 · 모호한 말 · 못 알아들은 말에 붙인다(지어낸 약속이 아니라 실제로 받는 요청 종류)
CAN_DO = ("이런 걸 말씀해 주시면 바로 처리해요 — 「○○ 식당이 문을 닫았어요」 · 「30분 늦어요」(일정을 다시 맞춰요) · "
          "「다른 안으로 바꿔 줘」 · 「2번 일정으로 되돌려 줘」 · 「경복궁 몇 시에 가요?」 · 「취소하면 위약금 있어요?」")

def _answers() -> dict[str, str]:
    from .itinerary_team import ANSWERS

    return dict(ANSWERS)


#: 처리 결과 → 고객 문장. ★결과마다 **무슨 일이 있었는지**만 말한다. 대화 경로와 같은 문장표(`itinerary_team.ANSWERS`)에
#: 이 경로에서만 나오는 결과를 더한다. 표에 없는 결과는 `outcome_reply` 가 「무엇을 확인하는지」를 말한다
OUTCOME_ANSWERS = {
    **_answers(),
    "stale": "그 사이 일정이 새로 바뀌어서 이 요청은 적용하지 않았어요. 여행계획서의 최신 일정을 보시고 다시 말씀해 주세요.",
    "kept": "일정은 그대로 두었어요.",
    "chosen": "고르신 안으로 바꿨어요.",
}


def _when(moment: datetime | None) -> str:
    if moment is None:
        return "시각 모름"
    local = moment.astimezone(KST)
    return f"{local.month}월 {local.day}일 {local:%H:%M}"


def item_fact(item: Any) -> str:
    """일정 항목 하나를 사실 그대로 — 「경복궁 관람 · 10월 15일 09:00~10:30 · 경복궁」."""
    end = f"~{item.ends_at.astimezone(KST):%H:%M}" if item.ends_at else ""
    place = (item.place or {}).get("name")
    return f"{item.title} · {_when(item.starts_at)}{end}" + (f" · 장소 {place}" if place and place != item.title else "")


def trip_summary(items: list[Any], now: datetime) -> str:
    stops = [i for i in items if i.kind != "mobility"]
    if not stops:
        return "이 여행에는 아직 일정이 없어요."
    days = sorted({i.starts_at.astimezone(KST).date() for i in stops})
    upcoming = [i for i in stops if i.starts_at >= now]
    head = f"이 여행은 {len(days)}일 · 일정 {len(stops)}개예요"
    if upcoming:
        return f"{head}. 다음 일정은 {item_fact(min(upcoming, key=lambda i: i.starts_at))}이에요."
    return f"{head}. 남은 일정은 없어요."


def question_reply(*, message: str, items: list[Any], tenant_id: str,
                   policy_search: Callable[..., list[Any]] | None, scopes: list[str],
                   min_score: float) -> tuple[str, str, dict[str, Any]]:
    """질문 → (상태, 답, 근거). 상태 `answered`(규정을 찾음) · `escalated`(규정을 못 찾아 사람이 확인).

    ★짚은 일정이 있으면 그 일정의 사실을 먼저 싣는다(「몇 시에 가요?」는 그것으로 답이 된다).
    ★규정은 문턱을 넘은 조각만 — 관련 없는 조각을 답처럼 싣지 않는다(문턱 근거는 가드레일 주석).
    """
    from .itinerary_team import mentioned_item, question_answer

    item = mentioned_item(items, message)
    fact = f"일정에서 찾은 것 — {item_fact(item)}" if item is not None else None
    evidence: dict[str, Any] = {"item": item.title if item else None, "policy_hits": 0, "sources": []}
    if policy_search is None:
        # 규정을 찾아볼 수 없다 — 일정 사실이 있으면 그것으로 답하고, 없으면 사람이 규정을 확인하게 남긴다
        if fact:
            return "answered", (f"{fact}\n지금은 여행 규정을 찾아볼 수 없어서 규정에 관한 답은 드리지 못했어요. "
                                "일정은 바꾸지 않았어요."), {**evidence, "policy": "unavailable"}
        return "escalated", ("지금은 여행 규정을 찾아볼 수 없어서 이 질문에 답하지 못했어요. "
                             "사람이 규정을 확인하도록 남겨 두었어요. 일정은 바꾸지 않았어요."), \
            {**evidence, "policy": "unavailable"}
    query = message if item is None else f"{item.title} {message}"
    try:
        chunks = policy_search(tenant_id=tenant_id, query=query, allowed_scopes=scopes, top_k=3) or []
    except Exception as exc:                          # noqa: BLE001 — 검색 실패도 답에 그대로 말한다
        lines = [fact] if fact else []
        lines.append("여행 규정을 찾다가 오류가 나서 규정에 관한 답은 드리지 못했어요. "
                     "규정 부분은 사람이 확인하도록 남겨 두었어요. 일정은 바꾸지 않았어요.")
        return "escalated", "\n".join(lines), {**evidence, "policy": f"error: {type(exc).__name__}"}
    good = [c for c in chunks if float(getattr(c, "score", 0) if not isinstance(c, dict) else c.get("score", 0))
            >= min_score]
    evidence["policy_hits"] = len(good)
    if good:
        answer, sources = question_answer(item, good, None)
        evidence["sources"] = sources
        return "answered", (f"{fact}\n{answer}" if fact else answer), evidence
    if item is not None:
        # 일정 사실은 있다 — 그것이 답이 되는 질문(시각·장소)일 수 있다. 규정은 못 찾았다고 같이 말한다
        return "answered", (f"{fact}\n여행 규정에서 이 질문에 맞는 내용은 찾지 못했어요. "
                            "더 궁금하신 점을 조금 더 자세히 적어 주세요. 일정은 바꾸지 않았어요."), evidence
    return "escalated", ("여행 규정과 이 여행의 일정에서 이 질문에 맞는 내용을 찾지 못했어요. 지어내서 답하지 않고, "
                         "사람이 확인하도록 남겨 두었어요. 일정의 어느 항목에 대한 질문인지 적어 주시면 바로 찾아볼게요."), evidence


def other_reply(items: list[Any], now: datetime) -> str:
    """잡담 · 인사 · 모호한 말 — 무엇을 할 수 있는지와 이 여행의 사실."""
    return f"{trip_summary(items, now)}\n{CAN_DO}"


def not_understood_reply(why: str) -> str:
    if why == "no_extractor" or why.startswith("extractor_failed"):
        lead = "지금은 문장을 읽는 기능이 잠시 멈춰 있어 말씀을 처리하지 못했어요. 잠시 뒤 다시 보내 주세요."
    elif why == "classification_failed":
        lead = "말씀을 분류하지 못해 처리하지 못했어요."
    else:
        lead = "말씀을 제대로 알아듣지 못해 일정은 바꾸지 않았어요."
    return f"{lead}\n{CAN_DO}"


def outcome_reply(status: str | None, outcome: dict[str, Any], message: str) -> str:
    """여행 창구의 처리 결과 → 답. 이미 문장이 있으면(변경 통지 · 묻는 문장) 그것을 쓴다."""
    text = (outcome.get("notice") or {}).get("text") or outcome.get("text")
    if text:
        return str(text)
    if status == "needs_check" and outcome.get("message"):
        return str(outcome["message"])
    if status in OUTCOME_ANSWERS:
        return OUTCOME_ANSWERS[status]
    if status in ("adjusted", "rolled_back", "asked", "answered"):
        return {"adjusted": "일정을 바꿨어요. 여행계획서에서 바뀐 일정을 확인해 주세요.",
                "rolled_back": "이전 일정으로 되돌렸어요.",
                "asked": "바꾸기 전에 확인이 필요해요. 여행계획서에서 안을 골라 주세요.",
                "answered": "답을 드렸어요."}[status]
    short = message.strip().replace("\n", " ")
    short = short if len(short) <= 40 else short[:40] + "…"
    return (f"「{short}」 — 자동으로 처리하지 못한 요청이라(처리 결과: {status or '없음'}) 사람이 일정을 확인하도록 "
            "남겨 두었어요. 일정은 바꾸지 않았어요.")


__all__ = ["CAN_DO", "OUTCOME_ANSWERS", "QUESTION_SCOPES", "item_fact", "not_understood_reply", "other_reply", "outcome_reply",
           "question_reply", "trip_summary"]
