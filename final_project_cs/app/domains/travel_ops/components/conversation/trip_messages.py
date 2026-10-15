# -*- coding: utf-8 -*-
"""고객 **자유 문장** 한 통 처리 — 여행 API(`/v1/trips/{id}/messages`)와 시나리오 모드가 같이 쓴다.

    Case 생성 → 분류 → 신고 추출 → 여행 창구 → Case 닫기

★Case 는 코어의 한 문(`transition_case`)으로만 움직인다:
    CREATED → (분류) CLASSIFIED → ROUTED(여행 창구) → COMPLETED(= resolved)
  분류 실패는 `classify_case` 가 escalated 로 보낸다. 신고 내용을 못 뽑았거나 창구가
  받지 않는 문장이면 ROUTING_FAILED, 창구가 처리하지 못하면 GUARDRAIL_ESCALATED —
  **추측으로 일정을 바꾸지 않고 처리 불가로 닫는다**(고객에게는 처리하지 못했다고 답한다. 상담원 대기는 없다 — D-018 · D-CS-014).
★같은 `request_id` 는 Case 를 새로 만들지 않는다.
★재요청(「다른 안으로 바꿔 줘」, v11 §1) — 가장 최근에 바뀐 항목을 우리가 들고 있던
  「다른 안」으로 바꾼다. 무엇으로 바꿀지 모델이 지어내게 두지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable
from uuid import UUID

from app.infrastructure.db.session import get_connection

from app.domains.travel_ops.components.itinerary.itinerary import TripStore
from app.domains.travel_ops.components.conversation.trip_desk import TripDesk
from app.domains.travel_ops.components.conversation.trip_intake import extract

logger = logging.getLogger(__name__)

REGISTERED_TEAMS = frozenset({"activity", "dining", "mobility", "booking", "lodging", "flight"})


def _stage(progress: Callable[[str], None] | None, name: str) -> None:
    """진행 알림 — 없으면 아무것도 안 한다. ★알림이 실패해도 처리는 계속된다."""
    if progress is None:
        return
    try:
        progress(name)
    except Exception:                                   # noqa: BLE001
        pass


class TripNotFound(LookupError):
    pass


#: 끼니 말 → 시작 시각 범위(시). 「점심 바꿔 줘」처럼 식당이라는 말이 없어도 식사를 가리킨다
_MEALS = {"아침": (0, 11), "조식": (0, 11), "브런치": (9, 13), "점심": (11, 15), "중식": (11, 15),
          "저녁": (17, 24), "석식": (17, 24)}


#: 문장이 말한 **종류** — 이 말이 있으면 다른 종류 항목은 대상이 아니다(`[2026-09-29 ui 세션 지적]` 「첫 식당」에 활동 답이 나갔다)
_KIND_WORDS = {"dining": ("식당", "식사", "밥", "맛집", "끼니", "아침", "조식", "브런치", "점심", "중식", "저녁", "석식"),
               "activity": ("활동", "관광", "관람", "체험", "구경", "명소", "투어")}
#: 서수 — 「첫 식당」「두 번째 활동」「마지막 일정」. 「첫날」처럼 날을 말하는 것은 서수가 아니다(바로 뒤에 종류 말이 와야 한다)
_ORDINAL = {"첫": 0, "두": 1, "둘": 1, "세": 2, "셋": 2, "네": 3, "넷": 3, "다섯": 4}


def asked_kind(message: str) -> str | None:
    """문장이 말한 종류(dining · activity) — 둘 다 말했거나 안 말했으면 None."""
    found = {kind for kind, words in _KIND_WORDS.items() if any(w in (message or "") for w in words)}
    return found.pop() if len(found) == 1 else None


def _asked_day(message: str, stops: list[Any]):
    """문장이 말한 날 — 「첫날」「첫째 날」「2일차」「둘째 날」「마지막 날」. 없으면 None. 첫날 = 여행의 첫 일정 날."""
    import re

    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")

    days = sorted({i.starts_at.astimezone(KST).date() for i in stops})
    if not days:
        return None
    text = message or ""
    if re.search(r"마지막\s*날", text):
        return days[-1]
    found = re.search(r"(\d+)\s*일\s*차", text)
    n = int(found.group(1)) - 1 if found else None
    if n is None:
        word = re.search(r"(첫|둘|셋|넷|다섯)\s*(?:째)?\s*날", text)
        n = {"첫": 0, "둘": 1, "셋": 2, "넷": 3, "다섯": 4}[word.group(1)] if word else None
    if n is None:
        return None
    from datetime import timedelta

    return days[0] + timedelta(days=n)


#: 자기 팀이 있는 일정 종류(식사 · 활동이 아닌 것) → 문장이 그 종류를 말하는 낱말. 규칙 경로 대상 고르기가 쓴다
_OWN_TEAM_WORDS = {"lodging": r"숙소|숙박|호텔", "flight": r"항공|비행기"}


def _change_target(items: list[Any], message: str, selected: UUID | None, at: datetime):
    """「바꿔 줘」가 가리키는 식사·활동.

    ★`[2026-09-29 ui 세션 지적]` **문장이 분명히 말하면 문장이 이긴다** — 경복궁을 눌러 둔 채 「점심 식당 바꿔 줘」라고
      쓰면 점심이 바뀌어야 한다(전에는 화면에서 고른 일정이 먼저라 경복궁이 바뀌었다). 순서:
      ①번호(「2번」 = 식사·활동을 시각 순으로 센 차례) ②이름(제목·장소 이름의 단어) ③끼니(「점심」 — 그 시간대 식사,
      여럿이면 지금 날짜의 것 → 다음 것) ④화면에서 고른 일정. 모르면 None(부르는 쪽이 들고 있던 안 · 다음 일정으로).
    ★`[2026-09-29 ui 세션 지적]` **서수 · 날 · 종류**도 읽는다 — 「첫 식당 다른 걸로 바꿔」가 서수를 못 읽어 화면에서 고른
      14:01 활동으로 떨어졌고, 「첫 식당」에 **활동** 답이 나갔다. 문장이 종류(식당 · 활동)를 말하면 그 종류 안에서만 고르고,
      화면에서 고른 일정도 종류가 다르면 쓰지 않는다. 서수는 날이 없으면 여행 첫날 기준(「2일차 두 번째 식당」은 그 날).
    """
    import re

    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
    from app.domains.travel_ops.instances._shared.itinerary_team import mentioned_item

    text = message or ""
    stops = sorted((i for i in items if i.kind in ("dining", "activity")), key=lambda i: (i.starts_at, i.seq))
    kind = asked_kind(text)
    pool = [i for i in stops if kind is None or i.kind == kind]
    day = _asked_day(text, stops)
    on_day = [i for i in pool if day is None or i.starts_at.astimezone(KST).date() == day]
    number = re.search(r"(\d+)\s*번", text)
    if number and 1 <= int(number.group(1)) <= len(stops):
        return stops[int(number.group(1)) - 1]
    ordinal = re.search(r"(첫|두|둘|세|셋|네|넷|다섯)\s*(?:번\s*째|째)?\s*(?=" + "|".join(
        w for words in _KIND_WORDS.values() for w in words) + "|일정)", text)
    last = re.search(r"마지막\s*(?=" + "|".join(w for words in _KIND_WORDS.values() for w in words) + "|일정)", text)
    if ordinal or last:
        first_day = on_day if day is not None else [
            i for i in pool if pool and i.starts_at.astimezone(KST).date() == pool[0].starts_at.astimezone(KST).date()]
        if last:
            return first_day[-1] if first_day else None
        n = _ORDINAL[ordinal.group(1)]
        return first_day[n] if n < len(first_day) else None
    named = mentioned_item(on_day, text)
    if named is not None:
        return named
    for word, (start, end) in _MEALS.items():
        if word in text:
            meals = [i for i in on_day if i.kind == "dining" and start <= i.starts_at.astimezone(KST).hour < end]      # ★서울 시각의 시
            today = [i for i in meals if i.starts_at.astimezone(KST).date() == at.astimezone(KST).date()]
            upcoming = [i for i in meals if i.starts_at >= at]
            found = (today or upcoming or meals or [None])[0]
            if found is not None:
                return found
    # ★`[2026-10-15 숙박예약 점검 LB-02]` 숙소 · 항공편은 번호 · 서수 · 끼니로 세는 목록(식사 · 활동)에 넣지 않는다(「2번」 같은 번호가 밀리지 않게). 문장이 **그 종류를 말했을 때**만 대상이다 —
    #   이름이 있는 식사 · 활동(「호텔 조식」)은 위에서 이미 골랐다. 같은 종류가 여럿이면 이름의 단어가 겹친 것, 못 가리면 None(부르는 쪽이 되묻는다). 두 종류를 함께 말했으면 가리지 않는다
    said = [own for own, words in _OWN_TEAM_WORDS.items() if re.search(words, text)]
    if kind is None and len(said) == 1:
        own_items = [i for i in items if i.kind == said[0]]
        pick = own_items[0] if len(own_items) == 1 else _rule_named(own_items, text)
        if pick is not None:
            return pick
    if selected is not None:
        # 화면에서 고른 일정이 숙소 · 항공편이면 그것이 대상이다(문장이 다른 종류를 말하지 않았을 때)
        chosen = next((i for i in [*stops, *(x for x in items if x.kind in _OWN_TEAM_WORDS)] if i.item_id == selected), None)
        if chosen is not None and (kind is None or chosen.kind == kind) and (
                day is None or chosen.starts_at.astimezone(KST).date() == day):   # 문장이 말한 종류 · 날과 맞을 때만
            return chosen
    if day is not None and on_day:
        return on_day[0]
    return None


def _rule_named(stays: list[Any], text: str):
    """같은 종류가 여럿일 때 문장의 단어가 가장 많이 겹친 하나 — 동점이거나 겹침이 없으면 None(지어내지 않는다)."""
    import re

    scored = []
    for stay in stays:
        name = f"{stay.title} {(stay.place or {}).get('name', '')}"
        words = {w for w in re.split(r"[\s·()\[\],./→\-]+", name) if len(w) >= 2}
        scored.append((sum(1 for w in words if w in text), stay))
    scored.sort(key=lambda pair: -pair[0])
    return scored[0][1] if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]) else None


def _rollback_target(store: TripStore, trip_id: UUID, items: list[Any], message: str,
                     selected: UUID | None, at: datetime, target: Any = None,
                     use_rules: bool = True) -> tuple[int | None, str | None]:
    """번호 없는 되돌리기 — (되돌릴 버전, None) 또는 (None, 되묻는 문장).

    ★`[2026-09-29 사용자 지적 · ui 세션 합의안 4번]` 모델이 번호를 만들지 않는다. 서버가 정한다:
      ①문장·화면이 가리키는 항목(`_change_target` — 번호 · 이름 · 끼니 · 고른 일정)이 있으면 **그 항목이 바뀐 버전**을
        찾아(안 바뀐 항목은 버전을 건너 같은 id 로 이어진다) 그 직전 버전으로. 그 뒤에 다른 변경이 끼었으면 되돌리지 않고 되묻는다
        (되돌리면 뒤 변경까지 함께 돌아간다).
      ②가리키는 항목이 없으면 **가장 최근 변경**(「방금 바꾼 것」)의 직전 버전으로.
    예: v1 등록 → v2 저녁을 금용문으로 → 「금용문 이전 식당으로 되돌려」 = v1 로.
    """
    if target is None and use_rules:
        target = _change_target(items, message, selected, at)
    with get_connection() as conn:
        history = store.versions(conn, trip_id)
        latest = history[-1]["version"] if history else 1
        if latest <= 1:
            return None, "되돌릴 변경이 없어요 — 이 여행은 처음 만든 일정 그대로예요."
        if target is None:
            return latest - 1, None
        introduced = latest
        while introduced > 1 and target.item_id in {i.item_id for i in store.items(conn, trip_id, introduced - 1)}:
            introduced -= 1
    if introduced <= 1:
        return None, f"「{target.title}」은(는) 처음 일정 그대로라 되돌릴 변경이 없어요."
    if introduced != latest:
        return None, (f"「{target.title}」은(는) {introduced}번 일정에서 바뀌었고, 그 뒤에 다른 변경이 "
                      f"{latest - introduced}번 더 있었어요. 되돌리면 그 변경도 함께 되돌아가요 — 괜찮으시면 "
                      f"「{introduced - 1}번 일정으로 되돌려」라고 말씀해 주세요.")
    return introduced - 1, None


def _latest_changed_item(store: TripStore, trip_id: UUID):
    """가장 최근 버전에서 새로 바뀐, 「다른 안」을 가진 항목. 없으면 가장 뒤의 그런 항목."""
    with get_connection() as conn:
        trip, items = store.latest(conn, trip_id)
        previous = {i.item_id for i in store.items(conn, trip_id, trip["version"] - 1)} \
            if trip["version"] > 1 else set()
    candidates = [i for i in items if i.detail.get("alternates")]
    fresh = [i for i in candidates if i.item_id not in previous]
    pick = (fresh or candidates)
    return trip, (max(pick, key=lambda i: i.seq) if pick else None)


#: 일정 항목의 종류이자 그 종류를 맡는 팀 id — 둘이 같은 말이다(`config/project.yaml`)
ITEM_TEAMS = frozenset({"activity", "dining", "mobility", "lodging", "flight"})


def owner_team(team: str, *, items: list[Any], message: str, selected: UUID | None,
               at: datetime) -> tuple[str, dict[str, Any]]:
    """★`[2026-09-29 사용자 지시]` 담당 팀은 **요청이 가리키는 일정의 실제 종류**로 정한다 — 분류기 딱지가 아니라.

    ☆왜 — 「무구옥 다른 곳으로 바꿔줘」(식사)·「첫날 아침 일정 바꿔」(순대국 식사)가 `activity_*` 딱지를 받아 활동 담당으로
      기록됐다(demo 실측 2건). 답은 일정 종류로 나가 맞았지만 담당·통계가 틀렸고, 딱지대로 팀에 넘기는 경로라면 식당 일을
      활동팀이 받는다. 일정의 종류는 공용 DB(여행 일정 버전)에 있다 — 그것이 정본이다.
    ★분류기가 **일정 팀**(활동·식당·이동·숙소)을 골랐을 때만 고친다. 예약·창구 같은 다른 딱지는 그대로 둔다
      (「경복궁 예약 취소」는 예약 일이다). 가리키는 일정을 못 찾으면 딱지를 쓴다(지어내지 않는다).
    돌려주는 두 번째 값은 기록용 — 무엇으로 정했는지(`routed_by`)와 그 일정 id.
    """
    if team not in ITEM_TEAMS:
        return team, {"routed_by": "classifier"}
    target = _change_target(items, message, selected, at)
    if target is None and selected is not None:
        target = next((i for i in items if i.item_id == selected), None)
    if target is None or target.kind not in ITEM_TEAMS:
        return team, {"routed_by": "classifier"}
    basis = {"routed_by": "item_kind", "item_id": str(target.item_id), "item_kind": target.kind}
    if target.kind != team:
        basis["classifier_team"] = team
    return target.kind, basis


def _finish_fact_case(*, tenant: str, case_id: UUID, trip_id: UUID, message: str, classifier: Any,
                      actor_id: str, fact: str, answer: str, items: list[Any] | None = None,
                      selected: UUID | None = None, at: datetime | None = None,
                      diagnostic_recorder=None) -> bool:
    """사실 질문 Case 를 끝낸다 — 분류하고, 되면 담당을 적고 완료로. 분류가 실패하면 기록만 남기고(escalated —
    모델 장애를 운영이 본다) False. ★이 여행의 사실을 묻는 말(하루 요약 · 일정 상세 · 다음 일정 · 예약 · 주소 ·
    운영시간 · 이동)은 규정 검색이 아니라 여행 기록으로 답한다(`trip_facts.py`) — 답은 이미 나갔다. 여기는 기록이다."""
    from app.application.classification import classify_case
    from app.application.routing import case_type_of
    from app.core.transition import transition_case
    from app.core.case_lifecycle.events import EventType
    from app.infrastructure.db import repository

    try:
        with get_connection() as conn:
            event = classify_case(conn, tenant_id=tenant, case_id=case_id, text=message, classifier=classifier,
                                  actor_id=actor_id, state_patch={"answer": answer},
                                  diagnostic_recorder=diagnostic_recorder)
            if event is not EventType.CLASSIFIED:
                return False
            case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
            team = case_type_of(case.get("issue_code") or "", fallback="trip_desk") or "trip_desk"
            basis: dict[str, Any] = {}
            if items is not None and at is not None:
                team, basis = owner_team(team, items=items, message=message, selected=selected, at=at)
            if team not in REGISTERED_TEAMS:
                team = "trip_desk"
            for event_type, payload in ((EventType.ROUTED, {"owner_team_id": team, "capability": "trip_desk.fact",
                                                             **basis}),
                                        (EventType.COMPLETED, {"answer_ref": f"trip:{trip_id}:fact:{fact}",
                                                               "state_patch": {"answer": answer}})):
                case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
                with conn.transaction():
                    transition_case(conn, tenant_id=tenant, case_id=case_id, expected_version=case["version"],
                                    event_type=event_type, payload=payload, actor_type="api", actor_id=actor_id)
            return True
    except Exception:            # noqa: BLE001 — 뒤에서 돌 때 죽으면 아무도 모른다. 세어 남긴다(조용히 넘기지 않는다)
        logger.exception("fact case finish failed case=%s", case_id)
        return False


def handle_trip_message(*, tenant: str, trip_id: UUID, request_id: str, message: str,
                        at: datetime, classifier: Any, chat: Any, desk: TripDesk,
                        actor_id: str, policy_search: Any = None, place_source: Any = None,
                        defer: Callable[[Callable[[], None]], None] | None = None,
                        selected_item_id: UUID | None = None, location: Any = None,
                        progress: Callable[[str], None] | None = None,
                        controller: Any = None, resume_case_id: UUID | None = None,
                        resume_token: str | None = None, expected_version: int | None = None,
                        summary_day: Any = None) -> dict[str, Any]:
    """★`[2026-09-28]` **어떤 결과로 끝나든 `answer`(고객에게 보일 문장)를 싣는다** — `trip_replies.py` 머리.
    질문은 규정 근거로 답하고(`policy_search`, 없으면 못 찾았다고 답한다), 잡담·모호한 말은 할 수 있는 일과
    이 여행의 사실로 답한다. 답은 서버가 가진 사실로만 만든다.

    `defer` — ★`[2026-09-29]` 사실 질문의 **분류를 응답 뒤로** 미루는 자리(웹 입구가 넣는다). 모델이 식어 있으면
    분류가 30초 넘게 걸려 답이 기록으로 이미 만들어져 있는데도 「하루 일정 요약」이 34.8·33.5초 걸렸다(ui 세션 실측,
    깨어 있으면 2.0·2.1초). 넣으면 사실 질문은 곧바로 답하고 분류·완료 기록은 뒤에서 한다 — 분류는 **그대로 한다**
    (모든 Case 가 분류를 거치고 실패는 기록한다, CLAUDE.md §1). 에이전트 입구는 넣지 않아 지금처럼 끝까지 기다린다.

    `progress` — ★`[2026-10-02]` 웹이 **실시간 진행(SSE)** 으로 받을 때 넣는다(`op_stream.py`). 서버가 **실제로 그 단계에 들어갈 때**
    `progress("reading" | "understanding" | "looking_up" | "classifying" | "extracting" | "applying")` 를 부른다. 처리 결과는 안 바뀐다 —
    부르는 쪽 실패는 삼킨다(진행 알림이 일 자체를 깨면 안 된다)."""
    from app.application.classification import classify_case
    from app.application.routing import case_type_of
    from app.core.transition import transition_case
    from app.core.case_lifecycle.events import EventType
    from app.infrastructure.db import repository
    from app.infrastructure.ollama_chat import OllamaError

    store = TripStore(tenant)
    marker = {"trip_message": {"trip_id": str(trip_id), "request_id": request_id}}
    from app.core.settings import get_guardrails

    from app.domains.travel_ops.components.conversation import trip_facts
    from app.domains.travel_ops.components.conversation import trip_replies

    _stage(progress, "reading")
    with get_connection() as conn:
        try:
            trip, items = store.latest(conn, trip_id)
        except KeyError:
            raise TripNotFound(str(trip_id)) from None
        summary_decision = None
        if summary_day is not None:
            from .summary_request import decide_summary
            if resume_case_id is not None or resume_token is not None or expected_version is not None:
                raise ValueError("하루 요약과 업무 재개는 함께 요청할 수 없습니다")
            summary_decision = decide_summary(summary_day, message=message, items=items)
        with conn.cursor() as cur:
            cur.execute("SELECT case_id FROM customer_cases WHERE tenant_id=%s "
                        "AND state_json @> %s::jsonb", (tenant, json.dumps(marker)))
            existing = cur.fetchone()
        if existing:
            case = repository.get_case(conn, tenant_id=tenant, case_id=existing[0])
            from app.core.redaction import masked
            from app.core.contracts import StateConflict
            import hashlib

            stored_hash = ((case.get("state_json") or {}).get("trip_message") or {}).get("message_sha256")
            if (stored_hash != hashlib.sha256(message.encode("utf-8")).hexdigest() if stored_hash else
                    case["subject"] != masked(message)):
                raise StateConflict("같은 요청 번호에 다른 메시지를 보낼 수 없습니다")
            from app.domains.travel_ops.components.conversation.fact_read_retry import retry_read
            read = retry_read(conn, case=case, tenant=tenant, trip=trip, items=items, actor_id=actor_id,
                              desk=desk, store=store, message=message, request_id=request_id,
                              policy_search=policy_search, place_source=place_source, controller=controller)
            if read is not None:
                return read
            from app.domains.travel_ops.components.conversation.case_dispatch import retry_initial

            retried = retry_initial(conn, controller=controller, tenant=tenant, trip=trip,
                                    case_id=existing[0], items=items, actor_id=actor_id, desk=desk)
            if retried is not None:
                return retried
            case = repository.get_case(conn, tenant_id=tenant, case_id=existing[0])
            before = (case.get("state_json") or {}).get("answer")
            reply = (case.get("state_json") or {}).get("chat_reply") or {}
            return {"status": "duplicate", "case_id": str(existing[0]),
                    "case_status": str(case["status"]),
                    **{k: v for k, v in reply.items() if k in ("choices", "choices_title", "more", "answer_web")},
                    "answer": f"같은 요청을 이미 받았어요.{chr(10) + str(before) if before else ''}"}
        from app.domains.travel_ops.components.conversation.case_dispatch import resume_pending
        try:
            resumed = resume_pending(conn, controller=controller, tenant=tenant, trip=trip,
                                     request_id=request_id, message=message, actor_id=actor_id,
                                     case_id=resume_case_id, token=resume_token, expected_version=expected_version, desk=desk, chat=chat,
                                     fresh=(lambda: _try_decide(conn, store=store, tenant=tenant, trip_id=trip_id, items=items,
                                                                message=message, selected=selected_item_id, chat=chat, now=at))
                                     if summary_day is None and _decision_mode(chat, tenant) == "on" else None)
        except LookupError:
            raise TripNotFound(str(resume_case_id)) from None
        # ★`[2026-10-13]` 대기 건이 있어도 새 문장이 혼자서 완결된 요청이면 새 요청으로 처리한다(같은 해석을 다시 부르지 않는다)
        fresh_decision = None
        if resumed is not None and "fresh_decision" in resumed:
            fresh_decision, resumed = resumed["fresh_decision"], None
        if resumed is not None:
            return resumed
        # ★`[2026-09-29]` **사실 질문의 답은 분류 전에 규칙으로 만든다**(모델을 안 쓴다) — 그리고 Case 기록에 **처음부터**
        #   싣는다. 분류가 뒤에서 도는 동안 같은 요청이 다시 와도 앞의 답이 나간다(아래 중복 경로)
        # ★`[2026-09-29 사용자 지시 · Codex 합의]` **결정 단위** — 여행 상태(일정 · 변경 이력 · 앞 대화 · 화면 선택)를 보며
        #   모델 한 번이 할 일과 대상을 고른다. 켜져 있으면(`travel.decision_unit.mode = on`) 아래 규칙 경로(사실 낱말 ·
        #   분류기 · 추출기 · 대상 규칙)를 타지 않는다 — 모델 실패에도 규칙 경로로 넘기지 않는다(옛 오처리를 되살린다).
        mode = _decision_mode(chat, tenant)
        if mode != "off" or summary_decision is not None:
            _stage(progress, "reading" if summary_decision is not None else "understanding")
            _clear_model_path()
            if summary_decision is not None:
                decision, failure = summary_decision, None
            elif fresh_decision is not None:
                decision, failure = fresh_decision
            else:
                decision, failure = _try_decide(conn, store=store, tenant=tenant, trip_id=trip_id, items=items,
                                                message=message, selected=selected_item_id, chat=chat, now=at)
            record = decision.record() if decision is not None else {"failed": failure}
            record = {**record, **_model_path()}               # ★`[2026-10-07]` 어느 경로(로컬 · API)로 답했나 — 넘김 장치가 켜져 있을 때만 들어간다
            if mode == "on" or summary_decision is not None:
                _stage(progress, "applying")
                from .fact_read_retry import read_snapshot
                from . import chat_log

                snapshot = read_snapshot(decision, at=at, selected=selected_item_id, trip=trip,
                                         turns=chat_log.recent(conn, tenant_id=tenant, trip_id=trip_id))
                case_id = _open_message_case(conn, tenant=tenant, trip=trip, message=message, marker=marker,
                                             selected=selected_item_id, actor_id=actor_id,
                                             extra={"decision": record,
                                                    **({"fact_read_snapshot": snapshot} if snapshot else {})})
                return _run_decision(conn, tenant=tenant, trip_id=trip_id, case_id=case_id, message=message,
                                     request_id=request_id, at=at, desk=desk, store=store, items=items,
                                     decision=decision, failure=failure, actor_id=actor_id,
                                     policy_search=policy_search, place_source=place_source,
                                     selected=selected_item_id, here=_fix_of(location, at), controller=controller,
                                     read_snapshot=snapshot)
            marker = {**marker}                         # shadow — 결정만 기록하고 아래 경로로 답한다
            shadow = {"decision_shadow": record}
        else:
            shadow = {}
        fact = trip_facts.fact_question(message)
        if fact is not None:
            _stage(progress, "looking_up")
        fact_answer = (trip_facts.fact_reply(fact, message=message, items=items, now=at, place_source=place_source)
                       if fact is not None else None)
        case_id = _open_message_case(conn, tenant=tenant, trip=trip, message=message, marker=marker,
                                     selected=selected_item_id, actor_id=actor_id,
                                     extra={**shadow, **({"answer": fact_answer[0], "fact_read_cached": True} if fact_answer else {})})
        # ★사실 질문 — 분류(모델)가 실패해도 답은 나간다(전에는 「분류하지 못해 처리하지 못했어요」로 끝났다, 2026-09-29).
        #   `defer` 가 있으면 분류·완료 기록을 응답 **뒤로** 미룬다. 없으면 여기서 끝까지 한다
        if fact_answer is not None:
            finish = lambda: _finish_fact_case(tenant=tenant, case_id=case_id, trip_id=trip_id,  # noqa: E731
                                               message=message, classifier=classifier, actor_id=actor_id,
                                               fact=fact, answer=fact_answer[0], items=items,
                                               selected=selected_item_id, at=at,
                                               diagnostic_recorder=getattr(controller, "diagnostic_recorder", None))
            if defer is not None:
                defer(finish)
                pending = True
            else:
                pending, classified = False, finish()
            case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
            return {"case_id": str(case_id), "case_status": str(case["status"]),
                    "classification": {"intent": case.get("intent"), "issue_code": case.get("issue_code")},
                    "status": "answered", "reason": "trip_fact_answered",
                    **({"classification_pending": True} if pending else
                       {} if classified else {"classification_failed": True}),
                    "report": {"type": "question", "fact": fact}, "answer": fact_answer[0], "basis": fact_answer[1]}
        # ★분류는 생성 트랜잭션 **밖**에서(v8 §3-A) — REST 접수와 같은 이유다.
        # 실행 불가 뒤 재전송에서도 해석을 반복하지 않도록 분류 이벤트에 실행 입력을 함께 남긴다.
        _stage(progress, "extracting")
        try:
            report = extract(message, chat) if chat is not None else None
            why = "no_extractor" if chat is None else "not_understood"
        except OllamaError as exc:
            report, why = None, f"extractor_failed: {exc}"[:120]
        dispatch_state = {}

        def classify_with_dispatch(text):
            result = classifier(text) if classifier is not None else None
            if result and report and report.get("type") in ("delay", "closed", "change", "rollback", "stock_out"):
                guessed = case_type_of(result.get("issue_code") or "", fallback="trip_desk") or "trip_desk"
                routed, _ = owner_team(guessed, items=items, message=message, selected=selected_item_id, at=at)
                plan, _ = _report_dispatch_plan(report=report, team=routed, trip=trip, store=store,
                                                 items=items, message=message, selected=selected_item_id, at=at)
                dispatch_state["conversation_dispatch"] = plan
            return result

        _stage(progress, "classifying")
        event = classify_case(conn, tenant_id=tenant, case_id=case_id, text=message,
                              classifier=classify_with_dispatch, actor_id=actor_id, state_patch=dispatch_state,
                              customer_id=trip["customer_id"],
                              diagnostic_recorder=getattr(controller, "diagnostic_recorder", None))
        conn.commit()

        def move(event_type, payload):
            case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
            with conn.transaction():
                transition_case(conn, tenant_id=tenant, case_id=case_id,
                                expected_version=case["version"], event_type=event_type,
                                payload=payload, actor_type="api", actor_id=actor_id)

        def view(extra):
            case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
            return {"case_id": str(case_id), "case_status": str(case["status"]),
                    "classification": {"intent": case.get("intent"),
                                       "issue_code": case.get("issue_code")}, **extra}

        if event is not EventType.CLASSIFIED:
            return view({"status": "escalated", "reason": "classification_failed",
                         "answer": trip_replies.not_understood_reply("classification_failed")})
        case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
        team = case_type_of(case.get("issue_code") or "", fallback="trip_desk") or "trip_desk"
        # ★담당은 가리키는 일정의 실제 종류로(`owner_team`) — 분류기 딱지가 일정 종류와 다르면 일정이 이긴다
        team, routed_basis = owner_team(team, items=items, message=message, selected=selected_item_id, at=at)
        if team not in REGISTERED_TEAMS:
            team = "trip_desk"
        if report is None:
            answer = trip_replies.not_understood_reply(why)
            # ★답은 실패 전이에도 Case 에 남긴다(`state_patch`) — 같은 요청이 다시 오면 그 답을 그대로 싣는다
            move(EventType.ROUTING_FAILED, {"failure_code": why, "state_patch": {"answer": answer}})
            return view({"status": "escalated", "reason": why, "report": None, "answer": answer})
        # ★`[2026-09-28]` 질문 · 그 밖 — 전에는 답 없이 escalated 로 끝났다(화면이 「담당자에게 넘겼어요」를 채웠다).
        #   질문은 **일정 사실 + 문턱을 넘은 규정 조각**으로 답한다(`*.itinerary_question` 과 같은 `question_answer`).
        #   그 밖(잡담 · 인사 · 모호한 말)은 할 수 있는 일과 이 여행의 사실로 답한다. ★일정은 바꾸지 않는다 —
        #   아래 「그 밖 = 다른 안으로 바꾸기」로 새지 않게 여기서 끝낸다.
        if report["type"] in ("question", "other"):
            if report["type"] == "question":
                status, answer, basis = trip_replies.question_reply(
                    message=message, items=items, tenant_id=tenant, policy_search=policy_search,
                    scopes=list(trip_replies.QUESTION_SCOPES),
                    min_score=float(get_guardrails().get("travel.question.min_policy_score")), now=at)
                reason = "question_answered" if status == "answered" else "question_needs_policy_answer"
            else:
                status, answer, basis = "answered", trip_replies.other_reply(items, at), {}
                reason = "not_a_trip_report"
            if status == "answered":
                move(EventType.ROUTED, {"owner_team_id": team, "capability": f"trip_desk.{report['type']}", **routed_basis})
                move(EventType.COMPLETED, {"answer_ref": f"trip:{trip_id}:{report['type']}",
                                           # ★근거는 기록에 남긴다(고객 문장에는 안 싣는다 — 2026-09-29 사용자 지시)
                                           "state_patch": {"answer": answer, "basis": basis}})
            else:
                move(EventType.ROUTING_FAILED, {"failure_code": reason, "state_patch": {"answer": answer}})
            return view({"status": status, "reason": reason, "report": report, "answer": answer,
                         "basis": basis})

        # ★분류가 `other` 여도(실측: 품절 문장) 처리는 추출값으로 간다 — 담당은 여행 창구로 적는다.
        _stage(progress, "applying")
        if report["type"] in ("delay", "closed", "change", "rollback", "stock_out"):
            from app.domains.travel_ops.components.conversation import case_dispatch
            plan = dispatch_state["conversation_dispatch"]
            target = next((i for i in items if str(i.item_id) == str(plan["subject_ref"].get("part_id"))), None)
            return case_dispatch.execute(conn, controller=controller, tenant=tenant, trip=trip, case_id=case_id,
                                         team=plan["team_id"], request=plan["subject_ref"]["request"], item=target,
                                         actor_id=actor_id, desk=desk, plan=plan)
        move(EventType.ROUTED, {"owner_team_id": team, "capability": f"trip_desk.{report['type']}", **routed_basis})

    if report["type"] == "delay":
        outcome = desk.report_delay(trip_id=trip_id, at=at, minutes=report["minutes"],
                                    message=message, request_id=request_id)
    elif report["type"] == "closed":
        outcome = desk.report_closed(trip_id=trip_id, at=at, message=message,
                                     request_id=request_id)
    elif report["type"] == "stock_out":
        outcome = desk.ask_nearby_store(trip_id=trip_id, at=at, products=report["products"],
                                        message=message, request_id=request_id)
    elif report["type"] == "rollback":
        # ★`[2026-09-26]` 전에는 이 분기가 없어 「N번 일정으로 되돌려 주세요」가 아래 「다른 안으로 바꾸기」로
        #   떨어졌다 — 되돌리는 대신 **다른 장소로 바꿨다**(시험으로 확인, triPilot : RAG 세션이 코드를 읽고 찾았다).
        #   기준 버전은 지금 최신 — 그 사이 바뀌었으면 되돌리기가 `stale` 로 답한다.
        with get_connection() as conn:
            current, latest_items = store.latest(conn, trip_id)
        to_version, ask = ((int(report["to_version"]), None) if report.get("to_version")
                           else _rollback_target(store, trip_id, latest_items, message, selected_item_id, at))
        outcome = ({"status": "ask_rollback", "text": ask} if to_version is None else
                   desk.rollback(trip_id=trip_id, base_version=current["version"], to_version=to_version,
                                 message=message, request_id=request_id))
    elif report["type"] == "change":                 # 재요청 — 다른 안으로
        # ★`[2026-09-29 사용자 지적]` 들고 있던 「다른 안」이 없으면 **그 자리에서 찾는다**(`plan_fresh_alternate`).
        #   전에는 감시가 한 번 고친 항목만 봐서, 막 만든 일정은 늘 「바꿀 수 있는 다른 안이 없어요」였다.
        #   어느 항목인가: 화면에서 고른 일정 → 문장(번호 · 이름 · 끼니) → 들고 있던 안이 있는 항목 → 다음 일정
        with get_connection() as conn:
            current, latest_items = store.latest(conn, trip_id)
        target = _change_target(latest_items, message, selected_item_id, at)
        kind = asked_kind(message)
        if target is None:
            _, target = _latest_changed_item(store, trip_id)
            if target is not None and kind is not None and target.kind != kind:
                target = None                        # ★문장이 말한 종류와 다르면 쓰지 않는다
        if target is None:
            stops = sorted((i for i in latest_items if i.kind in ((kind,) if kind else ("dining", "activity"))),
                           key=lambda i: (i.starts_at, i.seq))
            target = next((i for i in stops if i.starts_at >= at), stops[0] if stops else None)
        if target is None and kind is not None:
            word = "식당" if kind == "dining" else "활동"
            outcome = {"status": "ask_target",
                       "text": f"말씀하신 {word} 일정을 찾지 못했어요. 바꿀 일정을 화면에서 고르시거나 "
                               f"「2일차 저녁 식당 바꿔 줘」처럼 날과 끼니(또는 이름)를 함께 말씀해 주세요."}
        elif target is None:
            outcome = {"status": "no_alternate", "reason": "바꿀 식사·활동 일정이 없다"}
        elif target.detail.get("alternates"):
            outcome = desk.swap_alternate(trip_id=trip_id, item_id=target.item_id, base_version=current["version"],
                                          message=message, request_id=request_id)
        else:
            outcome = desk.fresh_alternate(trip_id=trip_id, item_id=target.item_id, base_version=current["version"],
                                           message=message, request_id=request_id)
    else:
        # ★모르는 종류는 어떤 처리로도 떨어뜨리지 않는다 — 처리하지 못한 것으로 남긴다
        outcome = {"status": f"unhandled_{report['type']}"}
    with get_connection() as conn:
        def move2(event_type, payload):
            case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
            with conn.transaction():
                transition_case(conn, tenant_id=tenant, case_id=case_id,
                                expected_version=case["version"], event_type=event_type,
                                payload=payload, actor_type="api", actor_id=actor_id)
        # ★`asked` — 「먼저 물어봐줘」·「변경 안 할 일정」이라 바꾸지 않고 물었다(D-020). 처리한 것이다 —
        #   답은 묻는 문장이고, 고객은 계획서 링크·웹에서 고른다. 사람에게 넘길 일이 아니다.
        answer = trip_replies.outcome_reply(outcome.get("status"), outcome, message)
        # ★`[2026-09-28]` 「바꿀 것 없음」(오늘 일정 없음 · 그 뒤 식사 없음 · 영향 없음 · 이미 바뀜)도 **답**이다 —
        #   `ANSWERS` 머리 주석이 그렇게 말하는데 여기 목록에 없어 사람 대기로 떨어졌다(ui 세션 실서버 시험).
        #   「바꿀 다른 안이 없음」(`no_alternate`)도 오류가 아니라 답이다 — 사람 대기는 버그·오류 리포트에만(사용자 결정)
        if outcome.get("status") in ("adjusted", "answered", "still_fits", "needs_check", "asked", "rolled_back",
                                     "not_today", "no_meal", "clear", "gone", "no_alternate", "ask_rollback",
                                     "ask_target", "knock_on", "rechecked"):
            ref = f"trip:{trip_id}:" + (f"v{outcome['version']}" if outcome.get("version")
                                         else str(outcome.get("status")))
            # ★컨트롤러와 같은 모양 — 답은 `state_patch.answer` 로 Case 에 들어간다.
            # ★`[2026-09-29 ui 세션 지적]` 무엇을 봤고 왜 안 됐는지(반경 · 떨어진 후보와 이유)도 남긴다 — 「없어요」만 남아
            #   기록으로 원인을 알 수 없었다
            kept = {k: outcome[k] for k in ("status", "reason", "radius_m", "seen", "rejected", "version") if k in outcome}
            move2(EventType.COMPLETED, {"answer_ref": ref, "state_patch": {"answer": answer, "trip_outcome": kept}})
        else:
            # ★`[2026-09-28]` 답을 남긴다 — 전에는 완료 전이에만 남겨, 「바꿀 것 없음」(`no_meal` 등) 뒤 같은 요청이 오면
            #   「같은 요청을 이미 받았어요」만 나가고 앞의 답이 빠졌다(시험을 조여서 찾았다)
            move2(EventType.GUARDRAIL_ESCALATED, {"guardrail": "trip_desk",
                                                  "observed": str(outcome.get("status")),
                                                  "state_patch": {"answer": answer}})
        case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
        return {"case_id": str(case_id), "case_status": str(case["status"]),
                "classification": {"intent": case.get("intent"),
                                   "issue_code": case.get("issue_code")},
                "status": outcome.get("status"), "report": report, "outcome": outcome, "answer": answer}


# ── 결정 단위 (2026-09-29) ───────────────────────────────────────
def _decision_mode(chat: Any, tenant: str | None = None) -> str:
    """off · shadow · on. ★운영 설정 `chat.decision_mode`(재기동 없이 바꾼다, 기본은 가드레일 `travel.decision_unit.mode`).
    구조화 호출을 못 하는 모델(시험의 고정 답변 등)이면 off — 규칙 경로로 돈다."""
    from app.core.settings import get_guardrails

    if chat is None or not callable(getattr(chat, "structured", None)):
        return "off"
    raw = get_guardrails().get("travel.decision_unit.mode")
    # ★YAML 은 따옴표 없는 on/off 를 참/거짓으로 읽는다 — 그래서 한 번 꺼진 채로 돌았다(2026-09-29 실서버)
    mode = {True: "on", False: "off"}.get(raw, str(raw or "off")) if isinstance(raw, bool) else str(raw or "off")
    if tenant:
        # ★`[2026-10-06]` 전에는 웹 남용 방어 설정 표를 직접 읽었다(D-CS-013 칸끼리 규칙 위반) —
        #   운영자 조정값을 **주는 쪽**이 조립 때 꽂는다. 아무도 안 꽂으면 가드레일 기본값 그대로.
        from app.domains.travel_ops.components.settings_hook import operator_setting

        mode = str(operator_setting(tenant, "chat.decision_mode") or mode)
    return mode if mode in ("off", "shadow", "on") else "off"


def _clear_model_path() -> None:
    from app.infrastructure import llm_failover

    llm_failover.clear_path()


def _model_path() -> dict[str, Any]:
    """직전 모델 호출의 경로 — `{"model_path": {path, backend, reasons}}`. 넘김 장치가 꺼져 있으면 `{}`(기록에 아무것도 더하지 않는다)."""
    from app.infrastructure import llm_failover

    path = llm_failover.current_path()
    if not path:
        return {}
    return {"model_path": {"path": path["path"], "backend": path["backend"], "reasons": [list(r) for r in path["reasons"]]}}


def _try_decide(conn, *, store: TripStore, tenant: str, trip_id: UUID, items: list[Any], message: str,
                selected: UUID | None, chat: Any, now: datetime | None = None):
    from app.core.settings import get_guardrails

    from app.domains.travel_ops.components.conversation import chat_log
    from app.domains.travel_ops.components.conversation import decision_unit

    try:
        decision = decision_unit.decide(
            chat, stops=decision_unit.stops_of(items), changes=decision_unit.changes_of(store, conn, trip_id, items),
            history=chat_log.recent(conn, tenant_id=tenant, trip_id=trip_id), selected_item_id=selected,
            message=message, num_predict=int(get_guardrails().get("travel.decision_unit.num_predict") or 160), now=now)
        return decision, None
    except decision_unit.DecisionFailed as exc:
        return None, str(exc)


def _open_message_case(conn, *, tenant: str, trip: dict[str, Any], message: str, marker: dict[str, Any],
                       selected: UUID | None, actor_id: str, extra: dict[str, Any]) -> UUID:
    from app.application.case_intake import create_case_once, IdempotencyConflict
    from app.core.contracts import StateConflict
    from app.core.idempotency import idempotency_key
    from app.infrastructure.db import repository
    import hashlib

    request_id = marker["trip_message"]["request_id"]
    key = idempotency_key(tenant_id=tenant, request_id=request_id, action_type="case.create",
                          business_subject=f"trip_message:{trip['trip_id']}")
    selected_state = {"selected_item_id": str(selected)} if selected else {}
    # psycopg의 SELECT도 트랜잭션을 연다. 생성 잠금이 뒤의 모델·Team 호출까지 남지 않게 끊는다.
    conn.commit()
    try:
        opened = create_case_once(conn, repository=repository, tenant_id=tenant, customer_id=trip["customer_id"],
                                  request_id=request_id, message=message, channel="trip_message",
                                  actor_type="api", actor_id=actor_id, idempotency_key=key,
                                  state_json={**marker, **extra, **selected_state, "trip_message": {
                                      **marker["trip_message"],
                                      "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest()}},
                                  created_payload=selected_state)
    except IdempotencyConflict as exc:
        raise StateConflict("같은 요청 번호에 다른 메시지를 보낼 수 없습니다") from exc
    conn.commit()
    if not opened.created:
        raise StateConflict("같은 요청이 먼저 접수되었습니다. 같은 요청 번호로 다시 확인해 주세요")
    return opened.case_id


#: 처리한 것(답이 나간 것) — 사람 대기가 아니다. 바꾸지 않고 물은 것 · 되물은 것도 여기다
#: ★`[2026-10-05]` `needs_check`(영업 여부를 못 알아 바꾸지 않고 알린 것)도 답이다 — 사람 대기로 떨어지지 않게
_DONE = ("adjusted", "answered", "still_fits", "needs_check", "asked", "rolled_back", "not_today", "no_meal", "clear", "gone",
         "no_alternate", "ask_rollback", "ask_target", "clarify", "already_applied", "stale", "knock_on", "rechecked")
#: 바꾼 뒤 이 시간 안에 되돌리면 오변경 의심으로 표시한다 — ★우리가 고른 값(2026-09-29, Codex 합의의 「오변경 감지」)
SUSPECT_UNDO_SECONDS = 15 * 60
_CLARIFY = "어느 일정을 말씀하시는지 알려 주세요 — 화면에서 일정을 고르시거나 「2일차 점심 식당 바꿔 줘」처럼 날과 끼니(또는 이름)를 적어 주세요."


def _run_decision(conn, *, tenant: str, trip_id: UUID, case_id: UUID, message: str, request_id: str, at: datetime,
                  desk: TripDesk, store: TripStore, items: list[Any], decision: Any, failure: str | None,
                  actor_id: str, policy_search: Any, place_source: Any, selected: UUID | None,
                  here: Any = None, controller: Any = None, read_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """결정 단위가 고른 할 일을 **서버가 확인하고** 기존 실행 함수로 처리한다. 결과는 늘 「한 일」·「고를 안」·「되물음」이다."""
    import re

    from app.application.classification import classify_case
    from app.core.settings import get_guardrails
    from app.core.transition import transition_case
    from app.core.case_lifecycle.events import EventType
    from app.infrastructure.db import repository

    from app.domains.travel_ops.components.conversation import trip_facts
    from app.domains.travel_ops.components.conversation import trip_replies
    from app.domains.travel_ops.components.conversation.decision_unit import LOCATION_FACTS, fact_first, labels, maybe_meant

    def move(event_type, payload):
        case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
        with conn.transaction():
            transition_case(conn, tenant_id=tenant, case_id=case_id, expected_version=case["version"],
                            event_type=event_type, payload=payload, actor_type="api", actor_id=actor_id)

    def view(extra):
        case = repository.get_case(conn, tenant_id=tenant, case_id=case_id)
        return {"case_id": str(case_id), "case_status": str(case["status"]),
                "classification": {"intent": case.get("intent"), "issue_code": case.get("issue_code")}, **extra}

    if decision is None:
        # ★모델 장애 · 스키마 밖 답 — **바꾸지 않는다.** 분류 실패로 기록한다(시스템 오류 리포트 — 사람 몫은 버그·오류뿐)
        def broken(_text):
            raise RuntimeError(f"결정 단위 실패: {failure}")
        classify_case(conn, tenant_id=tenant, case_id=case_id, text=message, classifier=broken, actor_id=actor_id,
                      diagnostic_recorder=getattr(controller, "diagnostic_recorder", None))
        answer = ("지금 요청을 해석하지 못했어요(해석 모델 응답 오류) — 일정은 바꾸지 않았어요. 잠시 뒤 다시 말씀해 주세요.")
        return view({"status": "escalated", "reason": "decision_failed", "failure": failure, "answer": answer})

    decision = fact_first(decision)      # 사실 질문이면 되묻지 않고 먼저 답한다(사용자 지시 2026-09-29)
    from app.domains.travel_ops.components.conversation import chat_log, fact_context

    safe_read = (decision.action == "answer_fact" and decision.fact not in LOCATION_FACTS
                 and read_snapshot is not None and read_snapshot.get("recoverable") is True)
    read_case = None
    if safe_read:
        from .fact_read_retry import lock_read_case
        from app.core.contracts import StateConflict

        read_case = lock_read_case(conn, tenant=tenant, case_id=case_id)
        if read_case["status"] not in ("classifying", "routing", "running"):
            raise StateConflict("같은 읽기 요청이 이미 처리되었습니다. 같은 요청 번호로 결과를 확인해 주세요.")
    current_context, context_items = store.latest(conn, trip_id)
    if safe_read and current_context["version"] != read_snapshot["base_version"]:
        from .fact_read_retry import NEW_REQUEST
        raise StateConflict(NEW_REQUEST)
    context_now = datetime.now(timezone.utc)  # 문맥 만료는 고객이 보낸 여행 시각으로 연장하지 않는다.
    turns = ([{"role": "assistant", "reply": {"focus": read_snapshot.get("focus") or {}}}]
             if safe_read else chat_log.recent(conn, tenant_id=tenant, trip_id=trip_id))
    decision, focus, stale_selection = fact_context.resolve(
        decision, items=context_items, message=message, selected=selected,
        turns=turns,
        version=current_context["version"], now=context_now,
        ttl_seconds=int(get_guardrails().get("travel.decision_unit.focus_ttl_seconds")))
    # 복합 읽기는 각 질문의 대상·범위를 따로 검증한다.
    if decision.reads:
        focus, stale_selection = None, False
    if safe_read:
        # ★`[2026-10-13]` 문맥으로 **고친 대상**으로 읽기 기록을 다시 만든다 — 아래 ROUTED 가 결정과 함께 저장한다.
        #   Case 를 열 때 만든 기록은 고치기 전 대상이라, 저장된 결정(고친 대상)과 기록이 어긋나
        #   중단 뒤 재시도가 다른 일정을 답할 수 있었다. develop 의 복구 수정(07202a63 계열)이 병합(2b5ad163)에서 빠졌던 것을
        #   되살렸다(tests/e2e/test_recovery_read_merge_web.py).
        from .fact_read_retry import read_snapshot as snapshot_read

        read_snapshot = snapshot_read(
            decision, at=at, selected=selected, trip={"trip_id": trip_id, "version": current_context["version"]},
            turns=turns) or read_snapshot
    from app.domains.travel_ops.components.conversation import case_dispatch

    plan = (_dispatch_decision(conn, controller=controller, tenant=tenant, trip_id=trip_id, case_id=case_id,
                              message=message, at=at, store=store, items=items, decision=decision,
                              selected=selected, actor_id=actor_id, desk=desk, prepare_only=True)
            if decision.action in case_dispatch.BUSINESS_ACTIONS else None)
    event = EventType.CLASSIFIED
    if not safe_read or read_case["status"] == "classifying":
        event = classify_case(conn, tenant_id=tenant, case_id=case_id, text=message,
                              classifier=lambda _text: labels(decision), actor_id=actor_id,
                              state_patch={"conversation_dispatch": plan} if plan is not None else None,
                              diagnostic_recorder=getattr(controller, "diagnostic_recorder", None))
    if not safe_read:
        conn.commit()
    if event is not EventType.CLASSIFIED:
        return view({"status": "escalated", "reason": "classification_failed",
                     "answer": trip_replies.not_understood_reply("classification_failed")})
    item, action = decision.item, decision.action
    if action in case_dispatch.BUSINESS_ACTIONS:
        current, _ = store.latest(conn, trip_id)
        target = next((i for i in items if str(i.item_id) == str(plan["subject_ref"].get("part_id"))), None)
        return case_dispatch.execute(conn, controller=controller, tenant=tenant, trip=current, case_id=case_id,
                                     team=plan["team_id"], request=plan["subject_ref"]["request"], item=target,
                                     actor_id=actor_id, decision=decision, desk=desk, plan=plan)
    team = {"dining": "dining", "activity": "activity", "mobility": "mobility"}.get(getattr(item, "kind", None),
                                                                                    "trip_desk")
    # 읽기 재시도(fact_read_retry.retry_read)가 쓰는 스냅샷 — Case 를 열 때와 같은 이름으로 남긴다
    # ★`[2026-10-14]` 문맥으로 바로잡은 대상(`fact_context.resolve`)을 스냅샷에도 같이 적는다 — 전에는 해석 기록은 바로잡은 대상,
    #   스냅샷은 모델이 처음 고른 대상이라 중단 뒤 재전송이 엉뚱한 일정을 읽을 수 있었다(`test_context_corrected_target_and_snapshot_are_committed_together`)
    if read_snapshot and not decision.reads:
        read_snapshot = {**read_snapshot, "item_id": str(decision.item.item_id) if decision.item else None}
    read_patch = {"fact_read_snapshot": read_snapshot} if read_snapshot else {}
    if not safe_read or read_case["status"] != "running":
        move(EventType.ROUTED, {"owner_team_id": team, "capability": f"trip_desk.{action}", "routed_by": "decision_unit",
                                "decision": decision.record(),
                                "state_patch": {**read_patch, "decision": decision.record()}})
    with get_connection() as fresh:
        current, now_items = store.latest(fresh, trip_id)
        history = store.versions(fresh, trip_id)
    base, latest = current["version"], (history[-1]["version"] if history else current["version"])
    if safe_read and base != read_snapshot["base_version"]:
        from .fact_read_retry import NEW_REQUEST
        raise StateConflict(NEW_REQUEST)
    if action == "answer_fact" and decision.fact in fact_context.ITEM_FACTS:
        if item is not None:
            current_item = next((i for i in now_items if i.item_id == item.item_id), None)
            stale_selection |= current_item is None
            item = current_item
            from dataclasses import replace
            decision = replace(decision, item=item)
        if focus and focus["source"] == "previous_fact" and focus["version"] != base:
            stale_selection = True
        if focus:
            focus = {**focus, "version": base}
    changeable = item is not None and item.kind in ("dining", "activity") and item.place is not None
    basis: dict[str, Any] = {}
    outcome: dict[str, Any]

    if action == "answer_fact" and decision.reads:
        from .read_requests import reply as read_reply

        outcome, basis, focus = read_reply(
            decision, items=now_items, message=message, selected=selected,
            turns=([{"role": "assistant", "reply": {"focus": read_snapshot.get("focus") or {}}}]
                   if safe_read else chat_log.recent(conn, tenant_id=tenant, trip_id=trip_id)),
            version=base, now=at, context_now=context_now,
            ttl_seconds=int(get_guardrails().get("travel.decision_unit.focus_ttl_seconds")), place_source=place_source)
    elif stale_selection:
        outcome = {"status": "clarify", "text": "가리키는 일정 하나를 현재 여행에서 확인하지 못했어요. 최신 일정에서 다시 선택해 주세요."}
    elif action == "clarify":
        outcome = _clarify_with_choices(decision, items, at=at, selected=selected)
    elif action == "other":
        outcome = {"status": "answered", "text": decision.social or trip_replies.other_reply(items, at)}
    elif action == "answer_service":
        from app.domains.travel_ops.components.conversation import service_help

        # ★`[2026-10-14]` 기능 안내는 규정 답과 다른 문턱을 쓴다(`travel.service_help.min_score` — 근거는 설정 파일 주석)
        status, text, basis = service_help.reply(
            message=message, tenant_id=tenant, policy_search=policy_search,
            min_score=float(get_guardrails().get("travel.service_help.min_score")), now=at)
        outcome = {"status": status, "text": text}
    elif action == "answer_fact" and decision.fact in ("schedule", "changeability"):
        from app.domains.travel_ops.components.conversation import schedule_query

        text, basis = schedule_query.reply(decision.fact, decision.query, items=now_items, now=at)
        basis["version"] = base
        outcome = {"status": "answered", "text": text}
    elif action == "answer_fact" and decision.fact in LOCATION_FACTS:
        # ★`[2026-09-30 사용자 지시]` 현재 위치가 필요한 질문 — 위치가 없으면 **일정을 바꾸지 않고** 위치를 부탁한다(`needs_location`)
        outcome = _answer_here(conn, kind=decision.fact, fix=here, trip=current, items=now_items, item=item, at=at,
                               store=store, desk=desk, trip_id=trip_id)
    elif action == "answer_fact":
        kind = decision.fact if decision.fact != "none" else "detail"
        # 답변에서 이름으로 다시 찾은 대상도 선택지·다음 질문과 같은 객체로 연결한다.
        if item is None:
            _, item = trip_facts.target(now_items, message, at)
            if item is not None:
                from dataclasses import replace
                decision = replace(decision, item=item)
                focus = {"item_id": str(item.item_id), "version": base, "at": context_now.isoformat(), "source": "fact_lookup"}
        text, basis = trip_facts.fact_reply(kind, message=message, items=now_items, now=at, place_source=place_source,
                                            item=item)
        # ★`[2026-09-29 ui 세션 요청]` 전체 설명은 핵심만 답으로, 나머지는 `more`(화면의 「더 보기」)로 나눈다
        text, _, more = text.partition(trip_facts.MORE_MARK)
        if kind == "detail" and item is not None and item.place:
            if safe_read:
                # 보조 정보 연결의 rollback이 Case 상태 전이와 잠금을 풀지 않게 한다.
                with get_connection() as extra_conn:
                    more += _place_extras(extra_conn, tenant, item.place)
            else:
                more += _place_extras(conn, tenant, item.place)
        # ★`[2026-09-29 사용자 지시]` 답하면서 「혹시 이런 뜻이었나요?」 — 다른 해석 · 가까운 질문을 버튼으로(`choices`)
        first = min((i.starts_at for i in now_items if i.kind != "mobility"), default=None)
        meant = maybe_meant(decision, kind, first)
        plain = text                  # 웹은 목록을 버튼(`choices`)으로 그린다 — 문장에서는 뺀 판(`answer_web`)
        if meant:
            text += "\n\n혹시 이런 뜻이었나요? " +" · ".join(f"{n}) {c['label']}" for n, c in enumerate(meant, start=1))
        outcome = {"status": "answered", "text": text, **({"more": more.strip()} if more.strip() else {}),
                   **({"choices": meant, "choices_title": "혹시 이런 뜻이었나요?", "text_web": plain} if meant else {})}
    elif action == "ask_policy":
        status, text, basis = trip_replies.question_reply(
            message=message, items=items, tenant_id=tenant, policy_search=policy_search,
            scopes=list(trip_replies.QUESTION_SCOPES),
            min_score=float(get_guardrails().get("travel.question.min_policy_score")), now=at)
        outcome = {"status": "answered" if status == "answered" else "question_needs_policy_answer", "text": text}
    elif action in ("apply_change", "propose_alternatives"):
        if not changeable:
            outcome = {"status": "clarify", "text": decision.question or _CLARIFY}
        elif action == "propose_alternatives":
            outcome = desk.propose_alternatives(trip_id=trip_id, item_id=item.item_id, base_version=base,
                                                message=message, request_id=request_id)
        elif item.detail.get("alternates"):
            outcome = desk.swap_alternate(trip_id=trip_id, item_id=item.item_id, base_version=base, message=message,
                                          request_id=request_id)
        else:
            outcome = desk.fresh_alternate(trip_id=trip_id, item_id=item.item_id, base_version=base, message=message,
                                           request_id=request_id)
    elif action == "rollback":
        explicit = re.search(r"(\d+)\s*번\s*(?:일정|버전|판)", message or "")
        change = decision.change
        if explicit:
            to_version, ask = int(explicit.group(1)), None
        elif change is not None and change.target_item_id is None:
            # ★`[2026-09-29 ui 세션 지적]` 결과가 지금 일정에 없는 변경(이미 되돌렸거나 다시 바뀜)은 되돌리지 않는다 — 되묻는다
            to_version, ask = None, None
        elif change is not None and change.version != latest and not _confirmed_rollback(conn, tenant, trip_id, change):
            # ★뒤에 다른 변경이 있으면 한 번 묻는다 — 고객이 「네」로 답하면(앞 대화에 이 질문이 있으면) 그때 되돌린다
            to_version, ask = None, (f"그 변경({change.line}) 뒤에 다른 변경이 {latest - change.version}번 더 있었어요. "
                                     f"되돌리면 그 뒤 변경까지 함께 되돌아가요. 그래도 되돌릴까요? "
                                     f"「네, 되돌려 줘」라고 답하시면 되돌릴게요.")
        elif change is not None:
            to_version, ask = change.version - 1, None
            # ★`[2026-09-29 Codex 합의]` 바꾼 직후 되돌리면 **오변경 의심** — 기록에 표시해 매일 모아 본다(1건이라도 나오면 shadow)
            made = next((h for h in history if h["version"] == change.version), None)
            if made is not None and (at - made["created_at"]).total_seconds() <= SUSPECT_UNDO_SECONDS:
                basis["suspect_wrong_change"] = {"version": change.version, "line": change.line}
        else:
            to_version, ask = _rollback_target(store, trip_id, now_items, message, selected, at, target=item,
                                               use_rules=False)
        if to_version is None and ask is None:
            outcome = _clarify_with_choices(decision, items, at=at, selected=selected,
                                            lead="그 변경은 지금 일정에 이미 없어요(되돌렸거나 다시 바뀌었어요).")
        else:
            outcome = ({"status": "ask_rollback", "text": ask} if to_version is None else
                       desk.rollback(trip_id=trip_id, base_version=base, to_version=to_version, message=message,
                                     request_id=request_id))
    elif action == "redo":
        change = decision.change
        undone = history[-1] if history and history[-1]["reason"] == "rollback" else None
        to_version = change.version if change is not None else (latest - 1 if undone else None)
        if to_version is None:
            outcome = {"status": "clarify", "text": decision.question or "다시 적용할 변경을 찾지 못했어요 — 어떤 일정을 다시 바꿀지 알려 주세요."}
        elif to_version >= latest:
            outcome = {"status": "already_applied", "text": "그 변경은 지금 일정에 이미 적용돼 있어요."}
        else:
            outcome = desk.rollback(trip_id=trip_id, base_version=base, to_version=to_version, message=message,
                                    request_id=request_id)
    elif action == "report_delay":
        outcome = ({"status": "clarify", "text": "몇 분쯤 늦으세요? 「30분 늦어요」처럼 알려 주시면 식사가 아직 괜찮은지, 뒤 일정에 걸리는 곳이 없는지 확인해 드릴게요."}
                   if decision.minutes is None else
                   desk.report_delay(trip_id=trip_id, at=at, minutes=decision.minutes, message=message,
                                     request_id=request_id))
    elif action == "report_closed":
        meal_now = next((i for i in items if i.kind == "dining"
                         and i.starts_at <= at < (i.ends_at or i.starts_at)), None)
        if item is None or (meal_now is not None and item.item_id == meal_now.item_id):
            outcome = desk.report_closed(trip_id=trip_id, at=at, message=message, request_id=request_id)
        elif changeable:
            outcome = desk.fresh_alternate(trip_id=trip_id, item_id=item.item_id, base_version=base, message=message,
                                           request_id=request_id)
        else:
            outcome = {"status": "clarify", "text": _CLARIFY}
    else:
        outcome = {"status": f"unhandled_{action}"}

    answer = trip_replies.outcome_reply(outcome.get("status"), outcome, message)
    status = outcome.get("status")
    chat_reply = {k: outcome[k] for k in ("choices", "choices_title", "more") if k in outcome}
    if outcome.get("text_web"):
        chat_reply["answer_web"] = outcome["text_web"]
    if focus and status == "answered":
        chat_reply["focus"] = focus
    kept = {k: outcome[k] for k in ("status", "reason", "radius_m", "seen", "rejected", "version", "proposal_id",
                                    "choices") if k in outcome}
    if basis.get("suspect_wrong_change"):
        kept["suspect_wrong_change"] = basis["suspect_wrong_change"]
    if basis:
        # ★근거는 **늘** Case 기록에 남긴다(고객 문장에는 안 싣는다 — 개발 모드일 때만 웹에 보인다)
        kept["basis"] = json.loads(json.dumps(basis, ensure_ascii=False, default=str))
    if status in _DONE:
        ref = f"trip:{trip_id}:" + (f"v{outcome['version']}" if outcome.get("version") else str(status))
        move(EventType.COMPLETED, {"answer_ref": ref, "state_patch": {"answer": answer, "trip_outcome": kept,
                                                                     "chat_reply": chat_reply}})
    else:
        move(EventType.GUARDRAIL_ESCALATED, {"guardrail": "trip_desk", "observed": str(status),
                                             "state_patch": {"answer": answer, "trip_outcome": kept}})
    return view({"status": status, "reason": f"decision_{action}", "decision": decision.record(),
                 "outcome": outcome, "answer": answer, **({"choices": outcome["choices"]} if outcome.get("choices") else {}),
                 **({"more": outcome["more"]} if outcome.get("more") else {}),
                 **({"needs_location": True} if outcome.get("needs_location") else {}),
                 **({"choices_title": outcome["choices_title"]} if outcome.get("choices_title") else {}),
                 # ★`[2026-09-29 ui 세션 요청]` 웹은 목록을 버튼으로 또 그려 두 번 보였다 — 웹 입구는 이 판을 `answer` 로 쓴다
                 **({"answer_web": outcome["text_web"]} if outcome.get("text_web") else {}),
                 **({"basis": basis} if basis else {})})


def _dispatch_decision(conn, *, controller: Any, tenant: str, trip_id: UUID, case_id: UUID,
                       message: str, at: datetime, store: TripStore, items: list[Any], decision: Any,
                       selected: UUID | None, actor_id: str, desk: TripDesk,
                       prepare_only: bool = False) -> dict[str, Any]:
    """결정의 입력을 보존하고, 변경 실행은 실제 담당 팀에 맡긴다."""
    import re

    from app.domains.travel_ops.components.conversation import case_dispatch

    trip, current_items = store.latest(conn, trip_id)
    history = store.versions(conn, trip_id)
    latest = history[-1]["version"] if history else trip["version"]
    item = decision.item
    action = decision.action
    request = {"type": "conversation", "action": action, "at": at.isoformat()}
    if decision.minutes is not None:
        request["minutes"] = decision.minutes
    if decision.question:
        request["question"] = decision.question
    if action == "clarify":
        question = _clarify_with_choices(decision, current_items, at=at, selected=selected)
        request.update(question=question["text"], choices=question.get("choices") or [])
    if action == "rollback":
        explicit = re.search(r"(\d+)\s*번\s*(?:일정|버전|판)", message or "")
        change = decision.change
        if explicit:
            request.update(to_version=int(explicit.group(1)), confirmed=True)
        elif change is not None and change.target_item_id is not None:
            request.update(to_version=change.version - 1, confirmed=change.version == latest)
            item = next((i for i in current_items if str(i.item_id) == str(change.target_item_id)), item)
            if not request["confirmed"]:
                request["question"] = (f"그 변경({change.line}) 뒤에 다른 변경이 {latest - change.version}번 더 있었어요. "
                                       "되돌리면 그 뒤 변경까지 함께 되돌아가요. 그래도 되돌릴까요?")
        elif change is not None:
            question = _clarify_with_choices(decision, current_items, at=at, selected=selected,
                                            lead="그 변경은 지금 일정에 이미 없어요(되돌렸거나 다시 바뀌었어요).")
            request.update(action="clarify", question=question["text"], choices=question.get("choices") or [])
        else:
            target, question = _rollback_target(store, trip_id, current_items, message, selected, at,
                                                target=item, use_rules=False)
            request.update(to_version=target, question=question, confirmed=question is None)
    elif action == "redo":
        change = decision.change
        undone = history[-1] if history and history[-1]["reason"] == "rollback" else None
        request.update(to_version=change.version if change else (latest - 1 if undone else None), confirmed=True)
    elif action == "report_closed" and item is not None and item.kind in ("activity", "dining"):
        current_meal = next((i for i in current_items if i.kind == "dining"
                             and i.starts_at <= at < (i.ends_at or i.starts_at)), None)
        if current_meal is None or current_meal.item_id != item.item_id:
            request["action"] = "apply_change"
    # ★`[2026-10-15 숙박예약 점검 LB-02]` 숙소 항목도 자기 팀(`lodging`)이 맡는다 — 전에는 식당 · 활동 · 이동만 알아서 숙소를 가리킨 「바꿔 줘」가
    #   활동 팀으로 넘어가 처리 못 한 채 사람에게 넘어갔다(`ITEM_TEAMS` = 일정 항목 종류 = 맡는 팀 id)
    owner = item.kind if item is not None and item.kind in ITEM_TEAMS else None
    if action in ("report_delay", "report_closed") and request["action"] != "apply_change":
        owner = "dining"
    if owner is None:
        typed_choices = request.get("choices") or []
        chosen = next((i for i in current_items if typed_choices
                       and str(i.item_id) == str(typed_choices[0].get("item_id"))), None)
        owner = chosen.kind if chosen is not None else "activity"
    plan = case_dispatch.descriptor(trip=trip, team=owner, request=request, item=item)
    if prepare_only:
        return plan
    return case_dispatch.execute(conn, controller=controller, tenant=tenant, trip=trip, case_id=case_id,
                                 team=owner, request=request, item=item, actor_id=actor_id,
                                 decision=decision, desk=desk, plan=plan)


def _report_dispatch_plan(*, report: dict[str, Any], team: str, trip: dict[str, Any], store: TripStore,
                          items: list[Any], message: str, selected: UUID | None, at: datetime):
    """규칙 해석 모드도 기존 대상·되돌림 규칙으로 재사용 가능한 입력을 만든다."""
    from app.domains.travel_ops.components.conversation import case_dispatch

    action = {"delay": "report_delay", "closed": "report_closed", "change": "apply_change",
              "rollback": "rollback", "stock_out": "stock_out"}[report["type"]]
    request = {"type": "conversation", "action": action, "at": at.isoformat(),
               **{k: report[k] for k in ("minutes", "to_version", "choice") if report.get(k) is not None}}
    if action == "stock_out":
        request.update(type="stock_out", products=report["products"])
    target = _change_target(items, message, selected, at) if action in ("apply_change", "report_closed") else None
    if action == "rollback" and request.get("to_version") is None:
        target_version, question = _rollback_target(store, UUID(str(trip["trip_id"])), items, message, selected, at)
        request.update(to_version=target_version, question=question, confirmed=question is None)
    owner = target.kind if target is not None else ("activity" if action == "stock_out" else "dining" if action in ("report_delay", "report_closed")
                                                   else team if team in ("dining", "activity") else "activity")
    return case_dispatch.descriptor(trip=trip, team=owner, request=request, item=target), target


def _confirmed_rollback(conn, tenant: str, trip_id: UUID, change: Any) -> bool:
    """바로 앞 답이 「그 변경(…) 뒤에 … 그래도 되돌릴까요?」였고 같은 변경을 다시 되돌리라고 하면 — 확인된 것이다."""
    from app.domains.travel_ops.components.conversation import chat_log

    turns = chat_log.recent(conn, tenant_id=tenant, trip_id=trip_id, limit=2)
    last = next((t for t in reversed(turns) if t["role"] == "assistant"), None)
    return bool(last and last["text"].startswith(f"그 변경({change.line})") and "되돌릴까요" in last["text"])


def _clarify_with_choices(decision: Any, items: list[Any], lead: str | None = None, *,
                          at: datetime | None = None, selected: UUID | None = None) -> dict[str, Any]:
    """★`[2026-09-29 사용자 제안]` 이해하지 못했으면 **이해한 범위 안에서** 「다음 중 하나인가요?」 — 번호 목록과 버튼용 문장.
    고객이 「1번」이라고 답하면 결정 단위가 앞 대화(이 목록)로 푼다. 후보가 없으면 되묻는 한 문장."""
    from app.domains.travel_ops.components.conversation.decision_unit import choice_message

    first = min((i.starts_at for i in items if i.kind != "mobility"), default=None)
    choices = []
    for choice in decision.choices:
        text = choice_message(choice, first)
        if text and all(text != c["message"] for c in choices):
            choices.append({"label": text, "message": text, "action": choice["action"],
                            "item_id": str(choice["item"].item_id) if choice.get("item") else None,
                            "fact": choice.get("fact"),
                            "to_version": choice["change"].version - 1 if choice.get("change") else None})
    if not choices:
        # ★`[2026-09-29 ui 세션 지적 · 사용자 제안]` 모델이 해석 후보를 못 냈어도 **고를 수 있는 것**을 보인다 —
        #   화면에서 고른 일정 → 지금부터 가까운 식사 · 활동(없으면 앞에서부터) 셋을 「다른 곳으로 바꿔 줘」로
        stops = sorted((i for i in items if i.kind in ("dining", "activity") and i.place), key=lambda i: (i.starts_at, i.seq))
        picked = [i for i in stops if selected is not None and i.item_id == selected]
        ahead = [i for i in stops if at is None or i.starts_at >= at] or stops
        for item in picked + [i for i in ahead if i not in picked]:
            text = choice_message({"action": "apply_change", "item": item, "change": None, "fact": "none"}, first)
            if text and all(text != c["message"] for c in choices):
                choices.append({"label": text, "message": text, "action": "apply_change",
                                "item_id": str(item.item_id)})
            if len(choices) >= 3:
                break
    if not choices:
        return {"status": "clarify", "text": " ".join(x for x in (lead, decision.question or _CLARIFY) if x)}
    listed = "\n".join(f"{n}) {c['label']}" for n, c in enumerate(choices, start=1))
    head = lead or "말씀을 정확히 알아듣지 못했어요."
    return {"status": "clarify", "choices": choices, "text_web": f"{head} 다음 중 하나인가요?",
            "text":f"{head} 다음 중 하나인가요?\n{listed}\n번호로 답하시거나 원하시는 것을 다시 말씀해 주세요."}


def _fix_of(location: Any, at: datetime) -> Any:
    """요청의 위치 → `trip_here.Fix`(좌표는 메모리에만). 없으면 None. 시각이 시간대 없이 오면 서울로 본다."""
    if location is None:
        return None
    from app.domains.travel_ops.components.conversation.trip_here import Fix

    seen = location.at
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=at.tzinfo)
    return Fix(lat=location.lat, lon=location.lng, accuracy_m=location.accuracy_m, at=seen)


def _answer_here(conn, *, kind: str, fix: Any, trip: dict[str, Any], items: list[Any], item: Any, at: datetime,
                 store: TripStore, desk: TripDesk, trip_id: UUID) -> dict[str, Any]:
    """현재 위치로 답한다 — 가는 길(`route_here`) · 근처 식당 · 근처 볼거리. ★일정은 바꾸지 않는다. 좌표는 어디에도 남기지 않는다."""
    from app.domains.travel_ops.components.conversation import trip_here

    if fix is None:
        return {"status": "answered", "text": trip_here.ask_for_location(kind), "needs_location": True}
    usable, note, refusal = trip_here.assess(fix, now=at)
    if not usable:
        return {"status": "answered", "text": refusal}
    if kind == "route_here":
        stops = sorted((i for i in items if i.kind != "mobility" and i.place is not None), key=lambda i: (i.starts_at, i.seq))
        dest = item if item is not None and item.place is not None and item.kind != "mobility" else next(
            (i for i in stops if (i.ends_at or i.starts_at) >= at), None)
        if dest is None:
            text = "어느 곳까지 가는 길을 알려 드릴까요? 일정에 있는 곳 이름을 말씀해 주세요."
        else:
            # ★`[2026-10-06]` 이동 팀을 직접 부르지 않는다 — 팀이 조립 때 꽂은 자리에서 받는다(D-CS-013)
            from app.domains.travel_ops.components.team_hooks import legs

            text = trip_here.route_here(fix, dest, now=at,
                                        leg=legs.leg_planner(trip.get("party_size"), trip.get("constraints")))
    else:
        from app.domains.travel_ops.components.itinerary.itinerary_changes import plan_nearby

        wanted = "dining" if kind == "nearby_dining" else "activity"
        if wanted == "activity":
            from app.domains.travel_ops.components.places.catalog_pool import widen_activity_pool
            from app.domains.travel_ops.components.itinerary.itinerary_changes import RELAX_WIDE_M

            origin = fix.as_origin()
            widen_activity_pool(conn, tenant_id=store.tenant_id, trip_id=trip_id, place=origin,
                                known_names={str(p.get("name")) for p in store.places(conn, trip_id)},
                                radius_m=RELAX_WIDE_M, now=at, memo=False)
        places = store.places(conn, trip_id)
        state_lookup = None
        if wanted == "dining" and getattr(desk, "_use_ledger", True):
            # ★`[2026-10-05]` 요식 원장이 식당의 정본이다(팀 방식). 현재 위치 둘레의 원장 가게를 **이 여행 전용 장소**로 들여놓고
            #   (`dining.nearby.add_nearby`) 영업 판정은 원장(`dining_states`)에게 묻는다. 전에는 후보를 원장에서 직접 받았다(A 방식 · 걷어냄).
            #   ★판정은 **이 연결**로 묻는다 — 방금 들여놓은 장소 · 연결이 아직 커밋 전이라 다른 연결에는 안 보인다.
            #   ★반경은 1.5km 까지만 들여놓는다(3km 는 한 번에 수백~수천 곳이라 여행 전용 장소 표를 부풀린다 · 우리가 고른 값).
            from types import SimpleNamespace

            # ★`[2026-10-06]` 요식 팀을 직접 부르지 않는다 — 팀이 조립 때 꽂은 자리에서 받는다(D-CS-013)
            from app.domains.travel_ops.components.team_hooks import dining_ledger

            places = dining_ledger.add_nearby(conn, store, trip_id,
                                              [SimpleNamespace(kind="dining", place=fix.as_origin())], places,
                                              radius_m=NEARBY_HERE_RADIUS_M)
            state_lookup = dining_ledger.state_lookup(conn, store.tenant_id)
        found, radius, sought = plan_nearby(wanted, trip=trip, places=places, origin=fix.as_origin(), at=at,
                                            state_lookup=state_lookup, items=items)
        text = trip_here.nearby(kind, fix, found=found, radius_m=radius, sought=sought)
    return {"status": "answered", "text": (note + " " if note else "") + text}


#: 「근처 식당」 — 현재 위치 둘레에서 원장 가게를 이 여행 전용 장소로 들여놓는 반경(m). ★우리가 고른 값(2026-10-05)
NEARBY_HERE_RADIUS_M = 1500

_TAG_KO = {"card_payment": "카드 결제", "parking": "주차", "takeout": "포장", "vegetarian_menu": "채식 메뉴",
           "kids_allowed": "아이 동반", "halal": "할랄", "reservation": "예약 가능", "wifi": "와이파이",
           "delivery": "배달", "pet_allowed": "반려동물 동반"}


def _place_extras(conn, tenant: str, place: dict[str, Any]) -> str:
    """세부 답에 더하는 장소 정보(요식 원장 · 관광공사) — 분류 · 전화 · 미쉐린 · 편의 · 출처. 모르는 값은 싣지 않는다."""
    from app.domains.travel_ops.components.places.place_info import place_info

    info = place_info(conn, tenant, place) or {}
    lines = []
    if info.get("category"):
        lines.append(f"· 분류: {info['category']}")
    if info.get("phone"):
        lines.append(f"· 전화: {info['phone']}")
    if info.get("michelin"):
        m = info["michelin"]
        lines.append(f"· 미쉐린: {m.get('level') or ''}{f' ({m["year"]})' if m.get('year') else ''}".rstrip())
    tags = [_TAG_KO[c] for c in info.get("tags") or [] if c in _TAG_KO]
    if tags:
        lines.append(f"· 편의: {', '.join(tags)}")
    if info.get("source_note"):
        lines.append(f"· 출처: {info['source_note']}")
    return ("\n" + "\n".join(lines)) if lines else ""


__all__ = ["TripNotFound", "handle_trip_message"]
