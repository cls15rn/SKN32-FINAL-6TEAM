"""일정 접수의 이름 찾기 — 식사는 요식 원장이 먼저(2026-10-01).

관광공사 자리(`tour`)는 지금 액티비티 CSV 다(액티비티 쪽 결정 8e9d8d0). 식당이 그 CSV 를 거치면
식당 이름이 관광지로 잡히거나(접두사 일치) 아예 못 찾았다. 요식 원장(`dining`)을 따로 둔다.

    식사로 읽힌 항목   우리 장소 표 → 요식 원장 → 관광공사 자리 → 카카오
    그 밖의 항목       우리 장소 표 → 관광공사 자리 → 요식 원장 → 카카오   (관광공사 자리는 그대로 먼저)
"""
from __future__ import annotations

from app.modules.travel_ops.intake.places import resolve


class Lookup:
    """관광공사 자리 · 요식 원장 흉내 — 같은 계약(`find` + `misses`)."""

    def __init__(self, known, content_type="39"):
        self.known, self.content_type, self.asked, self.misses = known, content_type, [], {}

    def find(self, name, area_code=None):
        self.asked.append(name)
        if name not in self.known:
            self.misses["not_found"] = self.misses.get("not_found", 0) + 1
            return None
        return {"matched_title": self.known[name], "content_id": "c-" + name, "content_type_id": self.content_type,
                "latitude": 37.5777, "longitude": 126.9715, "address": "서울특별시 종로구"}


class Kakao:
    def __init__(self, hits):
        self.hits, self.asked = hits, []

    def search(self, query, size=5, near=None):
        self.asked.append(query)
        return self.hits.get(query, [])


def _hit(name, group="FD6"):
    return {"id": "k", "name": name, "category": "", "category_group": group, "address": "서울 종로구",
            "latitude": 37.5777, "longitude": 126.9715}


def test_a_meal_goes_to_the_dining_ledger_before_the_activity_slot():
    # 「경복궁」은 관광지이자 고깃집 체인 이름이다. 식사면 관광지를 고르면 안 된다.
    tour, dining = Lookup({"경복궁": "경복궁"}, content_type="12"), Lookup({"경복궁": "경복궁"})

    found = resolve("경복궁", our_places=[], tour=tour, dining=dining, kind_hint="dining")

    assert (found.method, found.kind, found.content_id) == ("dining_ledger", "dining", "c-경복궁")
    assert found.evidence()["source"] == "dining_ledger"
    assert tour.asked == []


def test_a_meal_word_in_front_is_dropped_before_looking_up():
    # 「점심 토속촌삼계탕」을 뒤에서부터 좁히면 「점심」만 남는다 — 가게 이름으로는 한 번도 안 찾았다.
    dining = Lookup({"토속촌삼계탕": "토속촌삼계탕"})

    found = resolve("점심 토속촌삼계탕", our_places=[], dining=dining, kind_hint="dining")

    assert (found.status, found.name, found.needs_review) == ("resolved", "토속촌삼계탕", False)
    assert dining.asked[0] == "토속촌삼계탕"


def test_other_items_keep_the_activity_slot_first():
    tour, dining = Lookup({"경복궁": "경복궁"}, content_type="12"), Lookup({"경복궁": "경복궁"})

    found = resolve("경복궁", our_places=[], tour=tour, dining=dining)

    assert (found.method, found.kind) == ("tour_api", "activity")
    assert dining.asked == []


def test_other_items_fall_back_to_the_ledger_when_the_activity_slot_misses():
    # 끼니 말이 없는 줄(「12:00-13:00 토속촌삼계탕」)도 식당을 찾는다.
    tour, dining = Lookup({}), Lookup({"토속촌삼계탕": "토속촌삼계탕"})

    found = resolve("토속촌삼계탕", our_places=[], tour=tour, dining=dining)

    assert (found.method, found.kind) == ("dining_ledger", "dining")
    assert tour.asked == ["토속촌삼계탕"]


def test_kakao_name_is_confirmed_in_the_ledger():
    # 고객이 줄여 쓴 이름 → 카카오가 가게 이름을 찾고 → 원장에서 확인되면 원장 값(관광공사 ID 포함)을 싣는다.
    dining, kakao = Lookup({"토속촌삼계탕": "토속촌삼계탕"}), Kakao({"토속촌": [_hit("토속촌삼계탕")]})

    found = resolve("토속촌", our_places=[], tour=Lookup({}), dining=dining, kakao=kakao, kind_hint="dining")

    assert (found.method, found.name, found.content_id, found.needs_review) == (
        "dining_ledger", "토속촌삼계탕", "c-토속촌삼계탕", True)


def test_without_a_ledger_nothing_changes():
    tour = Lookup({}, content_type="12")
    found = resolve("점심 토속촌삼계탕", our_places=[], tour=tour, kind_hint="dining")
    assert found.status == "unresolved"


def test_a_ledger_that_could_not_be_read_is_named():
    class Down(Lookup):
        def find(self, name, area_code=None):
            self.misses["unavailable"] = self.misses.get("unavailable", 0) + 1
            return None

    found = resolve("토속촌삼계탕", our_places=[], dining=Down({}), kind_hint="dining")

    assert found.blocked == ["dining_ledger:unavailable"]


def test_a_ledger_place_is_registered_for_this_trip_only_with_its_content_id():
    # 관광공사 ID 를 실어야 판정 때 원장 가게와 다시 이어진다(220). 공용 표에는 넣지 않는다(여행 전용 행).
    from app.modules.travel_ops.intake.assemble import _place_in
    from app.modules.travel_ops.trip_api import EXTERNAL_PLACE_SOURCES

    place = _place_in("p0", {"name": "토속촌삼계탕", "kind": "dining", "latitude": 37.5777, "longitude": 126.9715,
                             "content_id": "c-1", "source": "dining_ledger"}, "dining")

    assert place["attributes"] == {"source": "dining_ledger", "source_content_id": "c-1"}
    assert "dining_ledger" in EXTERNAL_PLACE_SOURCES


def test_the_ledger_lookup_counts_an_unreadable_ledger_as_blocked():
    from app.modules.travel_ops.dining.place_lookup import LedgerPlaceLookup

    def broken():
        raise RuntimeError("connection refused")

    lookup = LedgerPlaceLookup(broken)

    assert lookup.find("토속촌삼계탕") is None
    assert lookup.misses == {"unavailable": 1}


def test_a_meal_word_in_front_makes_it_a_meal_even_without_a_hint():
    # 규칙 읽기는 「12:00-13:00 점심 토속촌삼계탕」에 식사 표시를 하지 않는다(모델이 읽은 줄만 표시한다).
    tour, dining = Lookup({}), Lookup({"마지": "마지"})

    found = resolve("저녁 마지", our_places=[], tour=tour, dining=dining)

    assert (found.method, found.name, found.kind) == ("dining_ledger", "마지", "dining")
    assert tour.asked == []


def test_a_ledger_hit_feeds_the_near_hint_for_the_next_items():
    # 액티비티 CSV · 카카오가 쓰는 근처 힌트(`_PlaceCtx`)에 원장에서 찾은 식당 좌표도 넣는다.
    import contextlib

    from app.modules.travel_ops import trip_api
    from app.modules.travel_ops.dining import place_lookup

    shop = {"matched_title": "토속촌삼계탕", "content_id": "1", "content_type_id": "39",
            "latitude": 37.5777, "longitude": 126.9715, "address": "서울특별시 종로구"}
    ctx = trip_api._PlaceCtx()
    original = place_lookup.find_place_by_name
    place_lookup.find_place_by_name = lambda conn, name: shop if name == "토속촌삼계탕" else None
    try:
        lookup = trip_api._dining_lookup(ctx)
        lookup._connect = lambda: contextlib.nullcontext()
        lookup.find("없는집")
        assert ctx.get_near() is None
        lookup.find("토속촌삼계탕")
    finally:
        place_lookup.find_place_by_name = original

    assert ctx.get_near() == (37.5777, 126.9715)
