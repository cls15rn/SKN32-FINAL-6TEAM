# -*- coding: utf-8 -*-
"""등록 판정이 식당은 요식 원장의 「그 시각에 여는가」로 본다. `[2026-10-02]`

★전에는 일정 접수가 원장에서 식당을 **찾기만** 하고 영업 정보는 넘기지 않았다(attributes 에 출처 · 관광공사 ID 뿐).
  그래서 등록 판정(`check_itinerary`)이 휴무 · 브레이크 · 영업 종료 뒤 식당을 그대로 통과시켰고,
  원장은 등록 뒤(새벽 3시 확인 · 하루 점검)에야 그것을 봤다.
★원장이 모르면(None) 위반이 아니다 — 판정기 원칙 그대로(결정 15).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.modules.travel_ops.itinerary_checks import Part, check_itinerary, with_ledger

KST = ZoneInfo("Asia/Seoul")


def _at(hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return datetime(2026, 10, 5, int(hour), int(minute), tzinfo=KST)


def _dining(seq, start, end=None, *, ledger=None, content_id="t123"):
    attributes = {"source": "dining_ledger", "source_content_id": content_id} if content_id else {}
    place = {"name": "토속촌삼계탕", "attributes": attributes}
    if ledger is not None:
        place["ledger"] = ledger
    return Part(seq=seq, kind="dining", title="토속촌 저녁", starts_at=_at(start),
                ends_at=_at(end) if end else None, place=place)


# ── 판정 ──────────────────────────────────────────────────────
def test_the_ledger_saying_closed_at_that_time_is_a_violation():
    found = check_itinerary([_dining(1, "23:30", "23:59", ledger={"open_at_slot": False, "closed": False})])
    assert [v.code for v in found] == ["dining_closed_at_slot"]
    assert found[0].seq == (1,)
    assert "23:30" in found[0].reason and found[0].remedy


def test_a_place_the_ledger_knows_as_shut_down_says_so():
    found = check_itinerary([_dining(1, "12:00", "13:00", ledger={"open_at_slot": False, "closed": True})])
    assert [v.code for v in found] == ["dining_closed_at_slot"]
    assert "폐업" in found[0].reason


def test_the_ledger_not_knowing_is_not_a_violation():
    assert check_itinerary([_dining(1, "23:30", ledger={"open_at_slot": None, "closed": False})]) == []


def test_the_ledger_saying_open_passes():
    assert check_itinerary([_dining(1, "12:00", "13:00", ledger={"open_at_slot": True, "closed": False})]) == []


# ── 원장 판정 붙이기 ──────────────────────────────────────────
def test_with_ledger_asks_only_dining_items_that_carry_a_tour_id():
    asked = []

    def lookup(slots):
        asked.extend(slots)
        return {1: {"open_at_slot": False, "closed": False}}

    activity = Part(seq=2, kind="activity", title="경복궁", starts_at=_at("14:00"),
                    place={"name": "경복궁", "attributes": {"source": "tour_api", "source_content_id": "a1"}})
    unknown = _dining(3, "19:00", content_id=None)
    parts = with_ledger([_dining(1, "23:30", "23:59"), activity, unknown], lookup)

    assert asked == [{"seq": 1, "content_id": "t123", "at": _at("23:30"), "until": _at("23:59")}]
    assert parts[0].place["ledger"] == {"open_at_slot": False, "closed": False}
    assert "ledger" not in parts[1].place and "ledger" not in parts[2].place
    assert [v.code for v in check_itinerary(parts)] == ["dining_closed_at_slot"]


def test_with_ledger_asks_by_the_ledger_id_first_so_shops_without_a_tour_id_are_judged():
    asked = []

    def lookup(slots):
        asked.extend(slots)
        return {slot["seq"]: {"open_at_slot": False, "closed": False} for slot in slots}

    vegan = Part(seq=1, kind="dining", title="비건 키친", starts_at=_at("12:00"),
                 place={"name": "비건 키친", "attributes": {"source": "dining_ledger", "dining_place_uid": "u9"}})
    both = Part(seq=2, kind="dining", title="토속촌", starts_at=_at("18:00"),
                place={"name": "토속촌", "attributes": {"source": "dining_ledger", "dining_place_uid": "u1",
                                                        "source_content_id": "t123"}})
    with_ledger([vegan, both], lookup)
    assert [(s["seq"], s.get("place_uid"), s.get("content_id")) for s in asked] == [(1, "u9", None), (2, "u1", None)]


def test_with_ledger_leaves_the_plan_as_is_when_the_ledger_cannot_be_read():
    def broken(slots):
        raise RuntimeError("요식 표가 없는 DB")

    parts = [_dining(1, "23:30")]
    assert with_ledger(parts, broken) == parts
    assert check_itinerary(with_ledger(parts, broken)) == []


def test_with_ledger_does_not_call_the_ledger_without_dining_items():
    def never(slots):
        raise AssertionError("부르면 안 된다")

    assert with_ledger([], never) == []
