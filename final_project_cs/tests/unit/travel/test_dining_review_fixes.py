"""리뷰에서 확인한 모름 처리·선택적 조회 예산·추천 경고의 회귀 시험."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.travel_ops.dining import DiningTeam
from app.modules.travel_ops.itinerary_changes import NoChange, plan_closed, plan_delay, plan_swap
from app.modules.travel_ops.pending import decide, options_from, proposal_notice

from .helpers import FakeTools, pack, task
from .test_dining_alternative_opening import at, meal, place


@pytest.mark.parametrize("core_hours", [True, False])
def test_unknown_original_does_not_trigger_a_replacement(core_hours):
    origin, candidate = place(hours=core_hours), place()
    calls = []

    def lookup(slots):
        calls.append(slots)
        return {origin["place_id"]: {"linked": True, "open_at_slot": None}}

    def price_lookup(_):
        pytest.fail("영업 여부를 모른다는 이유로 대체 가격을 조회하면 안 된다")

    plan = plan_delay(trip={}, items=[meal(origin)], places=[origin, candidate], at=at("11:00"),
                      minutes=30, message="늦어요", request_id=None,
                      state_lookup=lookup, price_lookup=price_lookup)
    assert isinstance(plan, NoChange)
    assert plan.status == "needs_check"
    assert "확인" in plan.detail["message"] and "바꾸지" in plan.detail["message"]
    assert len(calls) == 1


@pytest.mark.parametrize("lookup_name", ["_state_lookup", "_price_lookup"])
def test_optional_lookups_fall_back_when_the_tool_budget_is_exhausted(lookup_name):
    origin = place()
    team = DiningTeam(FakeTools({}))
    work = task("dining", "dining.itinerary", pack("dining"), team.manifest.allowed_tools)
    ctx = {"seen": {str(i) for i in range(team.manifest.max_steps)}, "evidence": []}
    arguments = ([{"place_id": origin["place_id"], "at": at("12:00"), "until": at("12:50")}]
                 if lookup_name == "_state_lookup" else [origin])
    assert getattr(team, lookup_name)(work, ctx)(arguments) is None


@pytest.mark.asyncio
async def test_closed_report_finishes_when_optional_reads_have_no_budget_left():
    origin, candidate = place(), place()
    team = DiningTeam(FakeTools({"read.place_catalog": [origin, candidate]}))
    work = task("dining", "dining.itinerary", pack("dining"), team.manifest.allowed_tools)
    ctx = {"trip": {"trip_id": str(uuid4()), "version": 1}, "items": [meal(origin)],
           "at": at("12:00"), "report": {}, "ref": {}, "evidence": [],
           "seen": {str(i) for i in range(team.manifest.max_steps - 1)}}
    result = await team.handle_report(work, "closed", ctx)
    assert result.outcome == "completed" and len(result.action_proposals) == 1


@pytest.mark.asyncio
async def test_team_reports_unknown_original_without_a_change_proposal():
    origin, candidate = place(), place()
    team = DiningTeam(FakeTools({
        "read.place_catalog": [origin, candidate],
        "read.dining_states": {origin["place_id"]: {"linked": True, "open_at_slot": None}}}))
    work = task("dining", "dining.itinerary", pack("dining"), team.manifest.allowed_tools)
    ctx = {"trip": {"trip_id": str(uuid4()), "version": 1}, "items": [meal(origin)],
           "at": at("11:00"), "report": {"minutes": 30}, "ref": {}, "evidence": [], "seen": set()}
    result = await team.handle_report(work, "delay", ctx)
    assert result.outcome == "completed" and result.action_proposals == []
    assert "확인" in result.answer and result.warnings


def test_recommendation_and_pending_options_keep_each_restaurants_warnings():
    origin = place()
    first = dict(place(key=uuid4()), name="첫 식당")
    second = dict(place(key=uuid4()), name="다른 식당")
    item = meal(origin)

    def lookup(slots):
        return {s["place_id"]: {"linked": True, "open_at_slot": True,
                                "needs_check": True, "needs_holiday_check": True} for s in slots}

    plan = plan_closed(trip={}, items=[item], places=[origin, first, second], at=at("12:00"),
                       message="휴무", request_id=None, state_lookup=lookup)
    assert not isinstance(plan, NoChange)
    assert "마지막 주문" in plan.notice["text"] and "명절" in plan.notice["text"]
    assert all(p["name"] in plan.notice["text"] for p in [first, second])
    options = options_from(plan, item)
    assert len(options) == 2 and all(len(o["warnings"]) == 2 for o in options)
    notice = proposal_notice(
        item=item, decision=decide(constraints={"on_disruption": "ask_first"}, item=item),
        causes=plan.causes, options=options, proposal_id=uuid4())
    assert "마지막 주문" in notice["text"] and "명절" in notice["text"]
    assert all(o["warnings"] for o in notice["options"])


def test_an_already_rejected_candidate_is_not_queried_for_hours():
    origin, candidate = place(), place()
    following = meal(origin)
    following.seq, following.kind, following.starts_at = 2, "activity", at("13:00")

    def lookup(_):
        pytest.fail("다음 일정과 겹쳐 이미 탈락한 후보는 조회하지 않는다")

    plan = plan_closed(trip={}, items=[meal(origin), following],
                       places=[origin, candidate], at=at("12:00"), message="휴무", request_id=None,
                       state_lookup=lookup)
    assert isinstance(plan, NoChange) and plan.status == "unresolved"


@pytest.mark.parametrize("warnings", [[], ["마지막 주문을 확인해야 한다"]])
def test_selecting_an_alternative_replaces_the_previous_restaurants_warnings(warnings):
    origin, candidate = place(), place()
    item = meal(origin)
    item.detail = {"warnings": ["이전 식당의 명절 영업을 확인해야 한다"], "alternates": [{
        "key": candidate["place_id"], "place_id": candidate["place_id"], "name": candidate["name"],
        "starts_at": item.starts_at.isoformat(), "ends_at": item.ends_at.isoformat(),
        "warnings": warnings}]}
    plan = plan_swap(trip_version=1, base_version=1, items=[item], item_id=item.item_id,
                     places_by_id={candidate["place_id"]: candidate}, choice=candidate["place_id"],
                     message="다른 곳으로", request_id=None, check=None)
    replacement = plan.replacements[item.item_id]
    assert replacement.detail.get("warnings", []) == warnings
    assert plan.notice.get("warnings", []) == [f"{candidate['name']}: {w}" for w in warnings]
    assert replacement.detail["alternates"][-1]["warnings"] == item.detail["warnings"]
