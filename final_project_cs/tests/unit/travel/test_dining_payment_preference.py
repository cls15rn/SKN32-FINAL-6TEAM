"""카드 결제 가능 여부는 고객에게 **참고로 보여 주기만** 한다 — 고르는 순서와 탈락에는 쓰지 않는다(2026-10-02).

「카드만 쓰는 고객」 분류는 아직 없다. 그 전까지 카드 정보는 안내에만 싣는다. 원장 판정(참 · 거짓 · 모름)이
먼저이고, 원장에 없으면 코어 장소의 결제 칸을 본다. 모르는 곳은 적지 않는다(「카드 된다」고 지어내지 않는다).
★여행에 명시된 결제 조건(`constraints.payment`)은 예전 그대로다 — 확정 시나리오 · 일정 생성기 · 등록 점검과 같다.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.travel_ops.itinerary_changes import NoChange, plan_closed, plan_closed_on_day
from app.modules.travel_ops.pending import options_from
from app.modules.travel_ops.replan import alternate_record, choose, dining_candidates

from .test_dining_alternative_opening import at, meal, place


def candidates(origin, pool, *, payment=None, state_lookup=None):
    return dining_candidates(original=origin, places=[origin, *pool], arrival=at("12:00"),
                             minutes=50, constraints={"payment": payment} if payment else {},
                             radius_m=700, next_start=None, state_lookup=state_lookup)


def ledger(card_payment):
    return lambda slots: {s["place_id"]: {"linked": True, "open_at_slot": True, "card_payment": card_payment}
                          for s in slots}


@pytest.mark.parametrize("card_payment", [True, False, None])
def test_the_ledgers_card_verdict_is_kept_as_reference_without_rejecting(card_payment):
    origin, alternative = place(), place(hours=False)

    result = candidates(origin, [alternative], state_lookup=ledger(card_payment))

    assert len(result) == 1 and result[0].rejected == [] and result[0].warnings == []
    assert result[0].card_payment is card_payment


@pytest.mark.parametrize("accepted, expected", [(["card"], True), (["cash"], False), ([], None), (None, None)])
def test_core_payment_is_the_reference_when_the_ledger_has_no_verdict(accepted, expected):
    origin, alternative = place(), place()
    if accepted is not None:
        alternative["attributes"]["payment"] = accepted

    result = candidates(origin, [alternative])

    assert result[0].rejected == [] and result[0].card_payment is expected


@pytest.mark.parametrize("card_payment", [False, None])
def test_a_linked_ledger_verdict_wins_over_stale_core_payment(card_payment):
    origin, alternative = place(), place()
    alternative["attributes"]["payment"] = ["card"]

    result = candidates(origin, [alternative], state_lookup=ledger(card_payment))

    assert result[0].card_payment is card_payment


def test_card_information_does_not_change_the_order():
    origin = place()
    cash, card = place(key=UUID(int=1)), place(key=UUID(int=2))
    cash["attributes"]["payment"] = ["cash"]
    card["attributes"]["payment"] = ["card"]

    best, others, rejected = choose(candidates(origin, [card, cash]))

    assert best.place == cash and [c.place for c in others] == [card] and rejected == []


@pytest.mark.parametrize("dawn", [False, True])
def test_the_notice_shows_known_card_information_as_a_reference_line_only(dawn):
    origin = place()
    card, cash, unknown = (dict(place(key=UUID(int=i)), name=n)
                           for i, n in ((1, "카드집"), (2, "현금집"), (3, "모름집")))
    card["attributes"]["payment"], cash["attributes"]["payment"] = ["card"], ["cash"]
    item = meal(origin)
    args = dict(trip={}, items=[item], places=[origin, card, cash, unknown])
    plan = (plan_closed_on_day(**args, meal=item, source="google_places", detail="휴무", checked_at=at("03:00"))
            if dawn else plan_closed(**args, at=at("12:00"), message="휴무", request_id=None))

    assert not isinstance(plan, NoChange)
    text = plan.notice["text"]
    assert "결제 참고(확인된 곳만): 카드집 카드 가능 · 현금집 카드 불가." in text
    assert "모름집 카드" not in text
    assert "warnings" not in plan.notice                    # 참고는 경고가 아니다
    options = options_from(plan, item)
    by_name = {o["name"]: o for o in options}
    assert by_name["카드집"]["card_payment"] is True and by_name["현금집"]["card_payment"] is False
    assert "card_payment" not in by_name["모름집"]


def test_no_reference_line_when_nothing_is_known():
    origin, alternative = place(), place()
    plan = plan_closed(trip={}, items=[meal(origin)], places=[origin, alternative], at=at("12:00"),
                       message="휴무", request_id=None)
    assert "결제 참고" not in plan.notice["text"]


def test_the_alternate_record_carries_the_reference():
    origin, alternative = place(), place()
    alternative["attributes"]["payment"] = ["card"]
    record = alternate_record(candidates(origin, [alternative])[0])
    assert record["card_payment"] is True


def test_an_explicit_card_condition_keeps_its_old_meaning():
    # 여행에 「카드 결제」 조건을 적은 경우(확정 시나리오 · 일정 생성기 · 등록 점검과 같은 기준)
    origin = place()
    card, cash, unknown = place(key=UUID(int=1)), place(key=UUID(int=2)), place(key=UUID(int=3))
    card["attributes"]["payment"], cash["attributes"]["payment"] = ["card"], ["cash"]

    result = {c.place["place_id"]: c for c in candidates(origin, [card, cash, unknown], payment="card")}

    assert result[card["place_id"]].rejected == []
    assert result[cash["place_id"]].rejected == ["결제 조건(card) 불충족"]
    assert result[unknown["place_id"]].rejected == ["결제 조건(card) 불충족"]


@pytest.mark.parametrize("identity", ["source_content_id", "dining_place_uid"])
def test_the_original_restaurant_is_not_reselected_with_another_core_id(identity):
    origin, alias = place(), dict(place(), name="원장의 다른 이름")
    origin["attributes"][identity] = alias["attributes"][identity] = "same-restaurant"
    assert candidates(origin, [alias]) == []
