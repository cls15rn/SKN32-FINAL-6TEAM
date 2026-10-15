# -*- coding: utf-8 -*-
"""규칙 해석 경로가 「숙소 바꿔 줘」의 대상을 숙소 항목으로 잡는다. `[2026-10-15 숙박예약 동작 점검 LB-02]`

☆왜: 규칙 경로의 대상 고르기(`trip_messages._change_target`)는 식사 · 활동만 셌다 — 숙소를 말하거나 화면에서 숙소를 눌러 둔 채 「바꿔 줘」라고 해도 대상이 비었다.
  (운영은 결정 단위 경로가 기본이라 이 경로는 모델이 없거나 꺼졌을 때의 대비다. 결정 단위 경로의 배선은 `tests/e2e/test_chat_lodging_change_web.py`.)

★지키려는 것
 ①문장이 숙소라고 말하면(숙소 · 숙박 · 호텔) 숙소 항목이 대상이다. 숙소가 여럿이면 이름이 겹친 하나, 못 가리면 비운다
 ②화면에서 숙소를 눌러 두고 종류를 안 말했으면 그 숙소가 대상이다
 ③식사 · 활동을 말한 문장은 그대로다 — 「점심 바꿔 줘」 · 「호텔 근처 식당 바꿔 줘」가 숙소로 새지 않고, 「호텔 조식」은 식사다
 ④「2번」 같은 번호는 여전히 식사 · 활동만 센다(숙소가 끼어 번호가 밀리지 않는다)

재현:

    python -m pytest tests/unit/travel/test_chat_lodging_target.py -v
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.domains.travel_ops.components.conversation.trip_messages import _change_target
from app.domains.travel_ops.components.itinerary.itinerary import Item

KST = ZoneInfo("Asia/Seoul")
AT = datetime(2030, 1, 5, 9, 0, tzinfo=KST)


def _item(seq, kind, title, hour, day=5, end=None):
    return Item(item_id=uuid4(), seq=seq, kind=kind, title=title, place_id=None,
                starts_at=datetime(2030, 1, day, hour, 0, tzinfo=KST),
                ends_at=end or datetime(2030, 1, day, hour + 1, 0, tzinfo=KST), detail={})


def _trip(extra_stay=False, breakfast="아침 식사"):
    items = [_item(1, "dining", breakfast, 8), _item(2, "activity", "경복궁 관람", 10), _item(3, "dining", "광장시장 점심", 12),
             _item(4, "lodging", "롯데호텔 서울", 15, end=datetime(2030, 1, 7, 11, 0, tzinfo=KST))]
    if extra_stay:
        items.append(_item(5, "lodging", "해밀톤 호텔", 15, day=7, end=datetime(2030, 1, 8, 11, 0, tzinfo=KST)))
    return items


def test_a_sentence_that_says_stay_targets_the_lodging_item():
    items = _trip()
    stay = next(i for i in items if i.kind == "lodging")
    for text in ("숙소 바꿔 줘", "숙소 다른 곳으로", "다른 호텔 보여 줘", "숙박 바꿔 줘"):
        assert _change_target(items, text, None, AT) is stay, text


def test_with_two_stays_the_one_whose_name_overlaps_is_taken_and_an_unclear_one_is_left_empty():
    items = _trip(extra_stay=True)
    롯데, 해밀톤 = (next(i for i in items if i.title == name) for name in ("롯데호텔 서울", "해밀톤 호텔"))
    assert _change_target(items, "롯데호텔 서울 바꿔 줘", None, AT) is 롯데
    assert _change_target(items, "해밀톤 호텔 다른 곳", None, AT) is 해밀톤
    assert _change_target(items, "숙소 바꿔 줘", None, AT) is None, "어느 숙소인지 못 가리면 비운다 — 부르는 쪽이 되묻는다"


def test_a_stay_pressed_on_the_screen_is_the_target_when_the_sentence_names_no_other_kind():
    items = _trip()
    stay = next(i for i in items if i.kind == "lodging")
    assert _change_target(items, "바꿔 줘", stay.item_id, AT) is stay
    assert _change_target(items, "점심 바꿔 줘", stay.item_id, AT).title == "광장시장 점심", "문장이 점심을 말하면 문장이 이긴다"


def test_meal_and_activity_sentences_do_not_leak_to_the_stay():
    items = _trip(breakfast="호텔 조식")
    assert _change_target(items, "호텔 조식 바꿔 줘", None, AT).title == "호텔 조식", "이름 있는 식사 「호텔 조식」은 식사다"
    assert _change_target(items, "호텔 근처 식당 바꿔 줘", None, AT) is None or _change_target(items, "호텔 근처 식당 바꿔 줘", None, AT).kind == "dining"
    assert _change_target(items, "경복궁 바꿔 줘", None, AT).title == "경복궁 관람"


def test_numbers_count_only_meals_and_activities():
    items = _trip()
    assert [_change_target(items, f"{n}번 바꿔 줘", None, AT).title for n in (1, 2, 3)] == ["아침 식사", "경복궁 관람", "광장시장 점심"]
    assert _change_target(items, "4번 바꿔 줘", None, AT) is None, "숙소는 번호에 들어가지 않는다"
