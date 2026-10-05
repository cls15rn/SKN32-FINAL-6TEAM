"""대체 식당은 방문 시간대의 원장 판정을 우선한다. 바깥 API 없이 검증한다."""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.travel_ops.dining import DiningTeam
from app.modules.travel_ops.itinerary import Item
from app.modules.travel_ops.itinerary_changes import NoChange, plan_closed, plan_closed_on_day, plan_delay
from app.modules.travel_ops.replan import dining_candidates, dining_fits

from .helpers import FakeTools, pack, task


def at(hm):
    return datetime.fromisoformat(f"2026-10-07T{hm}:00+09:00")


def place(*, hours=True, key=None):
    return {"place_id": str(key or uuid4()), "name": "식당", "kind": "dining",
            "latitude": 37.54, "longitude": 127.05,
            "attributes": {"hours": ["11:00", "22:00"]} if hours else {}}


def meal(origin):
    return Item(item_id=uuid4(), seq=1, kind="dining", title="점심",
                place_id=uuid4(), place=origin, starts_at=at("12:00"), ends_at=at("12:50"))


@pytest.mark.parametrize("opened, expected", [(True, True), (False, False), (None, None)])
def test_linked_ledger_verdict_wins_over_missing_or_stale_core_hours(opened, expected):
    candidate = place(hours=opened is not True)
    result, _ = dining_fits(candidate, at("12:00"), 50,
                            state={"linked": True, "open_at_slot": opened})
    assert result is expected


def test_unlinked_place_keeps_the_existing_hours_check():
    result, _ = dining_fits(place(), at("12:00"), 50,
                            state={"linked": False, "open_at_slot": None})
    assert result is True


def test_candidates_batch_only_nearby_dining_slots_and_keep_unknown_out():
    origin, opened, closed, unknown = place(), place(hours=False), place(), place()
    far = dict(place(), latitude=38)
    activity = dict(place(), kind="activity")
    seen = []

    def lookup(slots):
        seen.append(slots)
        return {p["place_id"]: {"linked": True, "open_at_slot": verdict}
                for p, verdict in [(opened, True), (closed, False), (unknown, None)]}

    candidates = dining_candidates(
        original=origin, places=[origin, opened, closed, unknown, far, activity],
        arrival=at("12:00"), minutes=50, constraints={}, radius_m=700,
        next_start=at("14:00"), state_lookup=lookup)
    assert len(seen) == 1
    assert {s["place_id"] for s in seen[0]} == {p["place_id"] for p in [opened, closed, unknown]}
    assert all(s["at"] == at("12:00") and s["until"] == at("12:50") for s in seen[0])
    assert {c.key for c in candidates if not c.rejected} == {opened["place_id"]}


def test_delay_checks_the_original_at_the_delayed_slot_then_the_alternative():
    origin, candidate = place(), place(hours=False)
    item = meal(origin)
    seen = []

    def lookup(slots):
        seen.extend(slots)
        return {s["place_id"]: {"linked": True, "open_at_slot": s["place_id"] != origin["place_id"]}
                for s in slots}

    plan = plan_delay(trip={}, items=[item], places=[origin, candidate], at=at("11:00"),
                      minutes=30, message="늦어요", request_id=None, state_lookup=lookup)
    assert not isinstance(plan, NoChange)
    assert plan.replacements[item.item_id].place == candidate
    assert all(s["at"] == at("12:30") and s["until"] == at("13:20") for s in seen)


def test_closed_report_batches_actual_arrival_slots_instead_of_querying_each_place_twice():
    origin = place()
    candidates = [dict(place(hours=False), latitude=37.54 + i * .0002) for i in range(15)]
    item = meal(origin)
    seen = []

    def lookup(slots):
        seen.append(slots)
        return {s["place_id"]: {"linked": True, "open_at_slot": True} for s in slots}

    plan = plan_closed(trip={}, items=[item], places=[origin, *candidates], at=at("12:00"),
                       message="휴무", request_id=None, state_lookup=lookup)
    assert not isinstance(plan, NoChange)
    assert len(seen) == 1 and len(seen[0]) == len(candidates)
    assert all(s["at"] >= at("12:15") and s["until"] == s["at"] + timedelta(minutes=50)
               for s in seen[0])
    replacement = plan.replacements[item.item_id]
    slot = next(s for s in seen[0] if s["place_id"] == replacement.place["place_id"])
    assert replacement.starts_at == slot["at"]


def test_dawn_replacement_uses_ledger_at_the_original_planned_slot():
    origin, candidate = place(), place(hours=False)
    item = meal(origin)
    seen = []

    def lookup(slots):
        seen.extend(slots)
        return {s["place_id"]: {"linked": True, "open_at_slot": True} for s in slots}

    plan = plan_closed_on_day(trip={}, items=[item], places=[origin, candidate], meal=item,
                              source="google_places", detail="closed", checked_at=at("03:00"),
                              state_lookup=lookup)
    assert not isinstance(plan, NoChange)
    assert seen == [{"place_id": candidate["place_id"], "at": item.starts_at, "until": item.ends_at}]


@pytest.mark.asyncio
async def test_dining_team_reads_candidate_states_in_one_scoped_tool_call(monkeypatch):
    origin, candidate = place(), place(hours=False)
    item = meal(origin)
    tools = FakeTools({"read.dining_states": {
        candidate["place_id"]: {"linked": True, "open_at_slot": True}}})
    team = DiningTeam(tools)
    context = pack("dining", scope=["travel_dining"])
    work = task("dining", "dining.itinerary", context, team.manifest.allowed_tools)
    ctx = {"trip": {}, "items": [item], "at": at("12:00"), "evidence": [], "seen": set()}
    monkeypatch.setattr(team, "catalog", lambda *_: [origin, candidate])
    monkeypatch.setattr(team, "settle", lambda _work, _ctx, plan: plan)
    plan = await team.handle_report(work, "closed", ctx)
    assert not isinstance(plan, NoChange)
    calls = [args for name, args in tools.calls if name == "read.dining_states"]
    assert len(calls) == 1
    assert calls[0]["slots"][0]["place_id"] == candidate["place_id"]


def test_trip_desk_uses_its_tenant_when_reading_alternative_states(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.modules.travel_ops.dining import ledger
    from app.modules.travel_ops.trip_desk import TripDesk

    origin, candidate = place(), place(hours=False)
    item = meal(origin)
    conn = object()
    seen = []

    def states(connection, tenant_id, slots):
        seen.append((connection, tenant_id, slots))
        return {s["place_id"]: {"linked": True, "open_at_slot": True} for s in slots}

    monkeypatch.setattr(ledger, "dining_states", states, raising=False)
    desk = TripDesk(store=SimpleNamespace(tenant_id="tenant-a"), connection_factory=lambda: nullcontext(conn))
    monkeypatch.setattr(desk, "_read", lambda _: ({"version": 1}, [item], [origin, candidate]))
    monkeypatch.setattr(desk, "_outcome", lambda _id, _version, _items, plan, **_: plan)
    plan = desk.report_closed(trip_id=uuid4(), at=at("12:00"), message="휴무")
    assert not isinstance(plan, NoChange)
    assert len(seen) == 1 and seen[0][:2] == (conn, "tenant-a")


def test_batch_read_tool_uses_scope_tenant_and_existing_ledger_lookup(monkeypatch):
    from contextlib import nullcontext

    from app.modules.travel_ops.dining import ledger
    from app.tools.read_tools import ReadToolbox

    conn, seen = object(), []
    candidate = place()
    slots = [{"place_id": candidate["place_id"], "at": at("12:00"), "until": at("12:50")}]

    def state(connection, tenant_id, place_id, starts, ends):
        seen.append((connection, tenant_id, place_id, starts, ends))
        return {"linked": True, "open_at_slot": False}

    monkeypatch.setattr(ledger, "dining_state", state)
    toolbox = ReadToolbox(connection_factory=lambda: nullcontext(conn))
    context = pack("dining", scope=["travel_dining"])
    result = toolbox.call("read.dining_states", context, {"slots": slots},
                          DiningTeam.manifest.allowed_tools, set(), budget=12)
    assert result[candidate["place_id"]]["open_at_slot"] is False
    assert seen == [(conn, context.tenant_id, candidate["place_id"], at("12:00"), at("12:50"))]


def test_dawn_check_wires_ledger_without_calling_google_for_alternatives(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.modules.travel_ops import dawn_check

    origin, candidate = place(), place(hours=False)
    item = meal(origin)
    conn = SimpleNamespace(transaction=lambda: nullcontext())
    store = SimpleNamespace(tenant_id="tenant-dawn", latest=lambda *_: ({}, [item]))
    google_calls, ledger_calls = [], []

    def google_verdict(provider_id, *, start, end):
        google_calls.append((provider_id, start, end))
        return "closed", "closed"

    def states(connection, tenant_id, slots):
        ledger_calls.append((connection, tenant_id, slots))
        return {s["place_id"]: {"linked": True, "open_at_slot": True} for s in slots}

    monkeypatch.setattr(dawn_check, "dining_states", states)
    monkeypatch.setattr(dawn_check, "apply_or_ask", lambda *_, **__: {"status": "adjusted", "version": 2})
    checker = dawn_check.DawnCheck(
        store=store, connection_factory=lambda: nullcontext(conn), clock=lambda: at("03:00"),
        source=SimpleNamespace(open_verdict=google_verdict), start=at("03:00").time(), until=at("08:00").time())
    monkeypatch.setattr(checker, "_provider_id", lambda *_: "google-original")
    monkeypatch.setattr(checker, "_closed_places", lambda *_: set())
    monkeypatch.setattr(checker, "_insert_check", lambda *_: None)
    result = dawn_check.DawnResult()
    checker._check(uuid4(), item, item.starts_at.date(), [origin, candidate], result)
    assert len(result.adjusted) == 1 and result.unresolved == []
    assert google_calls == [("google-original", item.starts_at, item.ends_at)]
    assert len(ledger_calls) == 1 and ledger_calls[0][:2] == (conn, "tenant-dawn")
