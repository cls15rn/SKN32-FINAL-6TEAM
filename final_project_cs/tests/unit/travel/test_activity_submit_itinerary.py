# -*- coding: utf-8 -*-
"""Activity 가 "일정 제출"(`itinerary_submit`)을 **어떻게 받는지**.

★★**기존 셋(check_cancelable·check_feasible·propose_change)과 전제가
  다르다.** 그것들은 전부 "이미 있는 예약"을 판정하지만, 일정 제출은
  **예약이 없는 게 정상**이다. 실측(2026-09-20) — 고치기 전에는 라우팅이
  ActivityTeam까지는 왔어도 `capability_for()`가 `intent`를 못 써서 항상
  `default_capability`("check_feasible")를 골랐고, `check_feasible`이
  `read.booking`부터 불러 예약이 없어서 **100% `unknown_예약 내역`으로
  escalate** 됐다.

★★**Phase 1 — 여기서 `places`/`activities`에 쓰지 않는다.** `ActionProposal`만
  만든다(`activity.submit`, `risk="low"`). 실제 반영은 이 시스템의 승인 실행
  계층이 아직 스텁이라(outbox worker, "Phase 1" 명시) 이 Team의 몫이 아니다
  — `activity.change`와 같은 성숙도에 맞춘다.

★고객 문장을 이 Team이 자연어로 해석하지 않는다. `requested_place_name`·
  `requested_activity_time`이 이미 `current_state`에 있다고 본다 — 문장에서
  장소·시각을 뽑는 일은 분류·추출 계층의 몫이다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.contracts import NextAction
from app.core.registry import TeamRegistry
from app.modules.travel_ops.activity import ActivityTeam

from .helpers import FakeTools, pack, task

ALLOWED = ActivityTeam.manifest.allowed_tools
UTC = timezone.utc
REQUESTED_AT = datetime(2026, 10, 3, 15, tzinfo=UTC)


def _task(*, state=None):
    context = pack("activity", scope=["activity"], state=state or {})
    return task("activity", "activity.submit_itinerary", context, ALLOWED)


def _found(**overrides):
    base = {"status": "found", "via": "place_catalog", "content_id": "126508", "content_type_id": "12",
            "matched_title": "경복궁", "latitude": 37.5760, "longitude": 126.9767}
    base.update(overrides)
    return base


# ── 라우팅 — 코어를 안 건드리고 select_capability 훅으로 고친다 ──

def test_select_capability_routes_itinerary_submit_intent():
    """`registry.py`의 훅. 이게 없으면 이 capability는 영원히 안 불린다."""
    assert ActivityTeam.select_capability("itinerary_submit", "") == "activity.submit_itinerary"


def test_select_capability_leaves_other_intents_to_existing_rules():
    """다른 intent는 손대지 않는다 — 기존 check_feasible 등 라우팅이 그대로다."""
    assert ActivityTeam.select_capability("confirm_request", "") is None
    assert ActivityTeam.select_capability("incident_report", "") is None
    assert ActivityTeam.select_capability("other", "") is None
    assert ActivityTeam.select_capability(None, "") is None


def test_registry_actually_resolves_to_the_new_capability_end_to_end():
    """`select_capability` 훅이 실제 `TeamRegistry`를 통해서도 먹히는지."""
    registry = TeamRegistry([ActivityTeam(tools=None)])
    entry = registry.get("activity")
    chosen = registry.capability_for(entry, "itinerary_submit", input_text="화요일에 경복궁 갈래")
    assert chosen == "activity.submit_itinerary"


# ── propose_change 라우팅 ────────────────────────────────────────

def test_select_capability_routes_adjust_reject_to_propose_change():
    """`adjust_reject` — 고객이 현재 상태를 거부하고 대안을 요청할 때."""
    assert ActivityTeam.select_capability("adjust_reject", "") == "activity.propose_change"
    assert ActivityTeam.select_capability("adjust_reject", "다른 날로 바꿔주세요") == "activity.propose_change"


def test_select_capability_propose_change_via_registry():
    """`adjust_reject`가 실제 `TeamRegistry`를 통해 propose_change로 도달하는지."""
    registry = TeamRegistry([ActivityTeam(tools=None)])
    entry = registry.get("activity")
    chosen = registry.capability_for(entry, "adjust_reject", input_text="일정 변경 원해요")
    assert chosen == "activity.propose_change"


# ── 실행 — 예약을 찾지 않는다 ──────────────────────────────────

@pytest.mark.asyncio
async def test_execute_never_calls_read_booking_for_this_capability():
    """★핵심 회귀 가드. 이 capability가 `read.booking`을 부르면 예약이 없어서
    바로 escalate 되던 옛 버그로 되돌아간 것이다."""
    tools = FakeTools({"read.place_lookup": _found()})
    result = await ActivityTeam(tools).execute(_task(state={
        "requested_place_name": "경복궁", "requested_activity_time": REQUESTED_AT}))

    assert result.outcome == "completed"
    assert "read.booking" not in [name for name, _ in tools.calls]


# ── 필수값 없으면 escalate ────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_place_name_escalates_as_unknown():
    result = await ActivityTeam(FakeTools({})).execute(
        _task(state={"requested_activity_time": REQUESTED_AT}))
    assert result.outcome == "escalated"
    assert result.failure_code == "unknown_요청 장소명"


@pytest.mark.asyncio
async def test_missing_activity_time_escalates_as_unknown():
    result = await ActivityTeam(FakeTools({})).execute(
        _task(state={"requested_place_name": "경복궁"}))
    assert result.outcome == "escalated"
    assert result.failure_code == "unknown_요청 시각"


@pytest.mark.asyncio
async def test_unparseable_activity_time_escalates_as_unknown():
    result = await ActivityTeam(FakeTools({})).execute(
        _task(state={"requested_place_name": "경복궁", "requested_activity_time": "화요일"}))
    assert result.outcome == "escalated"
    assert result.failure_code == "unknown_요청 시각"


# ── 장소를 찾으면 — 제안까지만 ─────────────────────────────────

@pytest.mark.asyncio
async def test_resolved_place_creates_a_low_risk_proposal_not_a_write():
    tools = FakeTools({"read.place_lookup": _found()})
    result = await ActivityTeam(tools).execute(_task(state={
        "requested_place_name": "경복궁", "requested_activity_time": REQUESTED_AT}))

    assert result.outcome == "completed"
    assert result.next_action is NextAction.WAIT_FOR_APPROVAL
    assert len(result.action_proposals) == 1
    proposal = result.action_proposals[0]
    assert proposal.action_type == "activity.submit"
    assert proposal.risk_level == "low"          # ★high(기본값)가 아니다 — 고객 자기 입력
    assert proposal.approval_required is True     # ★Phase 1 — 자동 실행 아님
    assert proposal.arguments["content_id"] == "126508"
    assert proposal.arguments["activity_time"] == REQUESTED_AT.isoformat()
    assert "경복궁" in result.answer


@pytest.mark.asyncio
async def test_place_lookup_is_called_with_the_customer_supplied_name():
    """`read.place_lookup`가 고객이 준 이름 그대로 불리는지 — 조용히 안 바꾼다."""
    tools = FakeTools({"read.place_lookup": _found()})
    await ActivityTeam(tools).execute(_task(state={
        "requested_place_name": "경복궁", "requested_activity_time": REQUESTED_AT}))
    arguments = dict(tools.calls)["read.place_lookup"]
    assert arguments["name"] == "경복궁"


# ── 장소를 못 찾으면 — 사람에게 넘기지 않고 고객에게 되묻는다 ────

@pytest.mark.asyncio
async def test_unmatched_place_asks_the_customer_instead_of_escalating():
    """★애매함도 「모름」이다(`read.place_lookup` → `not_found`). 사람에게 넘기는
    escalate가 아니라 고객에게 되묻는 WAIT_FOR_INPUT으로 보낸다."""
    tools = FakeTools({"read.place_lookup": {"status": "not_found", "via": "place_catalog"}})
    result = await ActivityTeam(tools).execute(_task(state={
        "requested_place_name": "듣도보도못한곳", "requested_activity_time": REQUESTED_AT}))

    assert result.outcome == "completed"          # ★escalated 가 아니다
    assert result.next_action is NextAction.WAIT_FOR_INPUT
    assert result.required_input_schema is not None
    assert "place_hint" in result.required_input_schema["properties"]


@pytest.mark.asyncio
async def test_unmatched_place_still_carries_evidence():
    """★회귀 가드 — 검색이 비어도(`_evidence`가 `None` 값을 안 쌓으므로)
    고객이 제출한 값 자체가 근거로 남아야 한다. 안 남으면 "answer 가 있는데
    evidence 가 비었다"는 계약 검증(TeamResult)에서 ValidationError가 난다
    (2026-09-20 구현 중 실제로 이 오류를 만나서 고쳤다).
    """
    tools = FakeTools({"read.place_lookup": {"status": "not_found", "via": "place_catalog"}})
    result = await ActivityTeam(tools).execute(_task(state={
        "requested_place_name": "듣도보도못한곳", "requested_activity_time": REQUESTED_AT}))
    assert len(result.evidence) >= 1
    assert any(e.source_id == "case.current_state" for e in result.evidence)
