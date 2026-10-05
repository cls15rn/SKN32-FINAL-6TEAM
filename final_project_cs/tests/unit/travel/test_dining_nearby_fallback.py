"""근처 원장 가게 들여놓기는 더해 주는 것이다 — 원장을 못 읽어도 대체 계산은 예전처럼 돈다(2026-10-01)."""
from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.travel_ops.dining import nearby
from app.modules.travel_ops.itinerary import Item

KST = timezone(timedelta(hours=9))


class Conn:
    def transaction(self):
        return contextlib.nullcontext()

    def cursor(self):
        raise RuntimeError('relation "dining.dn_place" does not exist')


class Store:
    tenant_id = "t"

    def adopt_places(self, *a, **k):
        raise AssertionError("원장을 못 읽었으면 아무것도 넣지 않는다")


def test_an_unreadable_ledger_leaves_the_places_as_they_were():
    place = {"place_id": str(uuid.uuid4()), "name": "토속촌삼계탕", "kind": "dining",
             "latitude": 37.5777, "longitude": 126.9715}
    at = datetime(2026, 10, 2, 12, 0, tzinfo=KST)
    meal = Item(item_id=uuid.uuid4(), seq=1, kind="dining", title="토속촌삼계탕", place_id=uuid.UUID(place["place_id"]),
                starts_at=at, ends_at=at + timedelta(hours=1), place=place)

    assert nearby.add_nearby(Conn(), Store(), uuid.uuid4(), [meal], [place]) == [place]


# ── 여행 창구 연결 — 「늦어요」·「문 닫았어요」만, 그 식사 하나 기준 ─────────────
def _desk(monkeypatch, items, places, added, closed=()):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.modules.travel_ops.trip_desk import TripDesk

    calls = []

    def fake_add(conn, store, trip_id, meals, seen_places, **_):
        calls.append([m.title for m in meals])
        return seen_places + added

    monkeypatch.setattr(nearby, "add_nearby", fake_add)
    desk = TripDesk(store=SimpleNamespace(tenant_id="t"), connection_factory=lambda: nullcontext(object()))
    monkeypatch.setattr(desk, "_read", lambda _: ({"version": 1}, items, places))
    monkeypatch.setattr(desk, "_outcome", lambda _id, _version, _items, plan, **_: plan)
    shut = {p["place_id"] for p in closed}
    monkeypatch.setattr(desk, "_dining_states", lambda slots: {
        s["place_id"]: {"linked": True, "open_at_slot": s["place_id"] not in shut} for s in slots})
    return desk, calls


def _place(name, lat=37.5777, lng=126.9715):
    return {"place_id": str(uuid.uuid4()), "name": name, "kind": "dining", "latitude": lat, "longitude": lng,
            "attributes": {}}


def _item(place, hm, seq=1):
    at = datetime.fromisoformat(f"2026-10-02T{hm}:00+09:00")
    return Item(item_id=uuid.uuid4(), seq=seq, kind="dining", title=place["name"],
                place_id=uuid.UUID(place["place_id"]), starts_at=at, ends_at=at + timedelta(hours=1), place=place)


def test_a_closed_report_finds_an_alternative_from_the_ledger(monkeypatch):
    lunch, dinner, alt = _place("토속촌삼계탕"), _place("저녁집"), _place("마지", 37.5778, 126.9716)
    desk, calls = _desk(monkeypatch, [_item(lunch, "12:00"), _item(dinner, "18:00", 2)], [lunch, dinner], [alt])

    plan = desk.report_closed(trip_id=uuid.uuid4(), at=datetime.fromisoformat("2026-10-02T12:10:00+09:00"),
                              message="문 닫았어요")

    assert calls == [["토속촌삼계탕"]]                    # 그 식사 하나 기준 — 저녁은 보지 않는다
    assert plan.notice and "마지" in plan.notice["text"]


def test_a_closed_report_replans_when_the_ledger_removes_an_existing_alias(monkeypatch):
    from app.modules.travel_ops.itinerary_changes import NoChange

    lunch, alias = _place("원래 식당"), _place("원장 별칭")
    desk, _ = _desk(monkeypatch, [_item(lunch, "12:00")], [lunch, alias], [])
    monkeypatch.setattr(nearby, "add_nearby", lambda *args, **kwargs: [lunch])

    plan = desk.report_closed(trip_id=uuid.uuid4(), at=datetime.fromisoformat("2026-10-02T12:10:00+09:00"),
                              message="문 닫았어요")

    assert isinstance(plan, NoChange) and plan.status == "unresolved"


def test_a_delay_that_still_fits_reads_nothing_from_the_ledger(monkeypatch):
    lunch, dinner = _place("토속촌삼계탕"), _place("저녁집")
    desk, calls = _desk(monkeypatch, [_item(lunch, "12:00"), _item(dinner, "18:00", 2)], [lunch, dinner], [])

    plan = desk.report_delay(trip_id=uuid.uuid4(), at=datetime.fromisoformat("2026-10-02T13:30:00+09:00"),
                             minutes=30, message="30분 늦어요")

    assert plan.status == "still_fits" and calls == []


def test_a_delay_that_breaks_the_meal_looks_around_the_next_meal_only(monkeypatch):
    lunch, dinner, alt = _place("토속촌삼계탕"), _place("저녁집"), _place("마지", 37.5778, 126.9716)
    desk, calls = _desk(monkeypatch, [_item(lunch, "12:00"), _item(dinner, "18:00", 2)], [lunch, dinner], [alt],
                        closed=[dinner])

    plan = desk.report_delay(trip_id=uuid.uuid4(), at=datetime.fromisoformat("2026-10-02T13:30:00+09:00"),
                             minutes=30, message="30분 늦어요")

    assert calls == [["저녁집"]]
    assert plan.notice and "마지" in plan.notice["text"]


def test_no_meal_means_no_ledger_read(monkeypatch):
    lunch = _place("토속촌삼계탕")
    desk, calls = _desk(monkeypatch, [_item(lunch, "12:00")], [lunch], [])

    desk.report_closed(trip_id=uuid.uuid4(), at=datetime.fromisoformat("2026-10-02T15:00:00+09:00"),
                       message="문 닫았어요")

    assert calls == []


@pytest.mark.parametrize("remove_alias", [False, True])
def test_dawn_check_replans_when_ledger_shops_are_added_or_an_existing_alias_is_removed(monkeypatch, remove_alias):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.modules.travel_ops import dawn_check

    lunch, alt = _place("토속촌삼계탕"), _place("마지", 37.5778, 126.9716)
    item = _item(lunch, "12:00")
    conn = SimpleNamespace(transaction=lambda: nullcontext())
    store = SimpleNamespace(tenant_id="t", latest=lambda *_: ({}, [item]))
    asked = []

    def fake_add(connection, store_, trip_id, meals, places, **_):
        asked.append([m.title for m in meals])
        return [lunch] if remove_alias else places + [alt]

    monkeypatch.setattr(nearby, "add_nearby", fake_add)
    monkeypatch.setattr(dawn_check, "dining_states",
                        lambda c, t, slots: {s["place_id"]: {"linked": True, "open_at_slot": True} for s in slots})
    applied = []
    monkeypatch.setattr(dawn_check, "apply_or_ask",
                        lambda *_, plan, **__: applied.append(plan) or {"status": "adjusted", "version": 2})
    clock = datetime.fromisoformat("2026-10-02T03:00:00+09:00")
    checker = dawn_check.DawnCheck(
        store=store, connection_factory=lambda: nullcontext(conn), clock=lambda: clock,
        source=SimpleNamespace(open_verdict=lambda *_a, **_k: ("closed", "closed")),
        start=clock.time(), until=datetime.fromisoformat("2026-10-02T08:00:00+09:00").time())
    monkeypatch.setattr(checker, "_provider_id", lambda *_: "google-original")
    monkeypatch.setattr(checker, "_closed_places", lambda *_: set())
    monkeypatch.setattr(checker, "_insert_check", lambda *_: None)
    monkeypatch.setattr(checker, "_price_lookup", lambda: None)
    result = dawn_check.DawnResult()

    checker._check(uuid.uuid4(), item, item.starts_at.date(), [lunch, alt] if remove_alias else [lunch], result)

    assert asked == [["토속촌삼계탕"]]
    if remove_alias:
        assert result.adjusted == [] and result.unresolved and applied == []
    else:
        assert len(result.adjusted) == 1 and "마지" in applied[0].notice["text"]
