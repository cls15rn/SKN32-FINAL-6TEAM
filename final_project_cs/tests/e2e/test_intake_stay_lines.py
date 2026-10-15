# -*- coding: utf-8 -*-
"""체크인 · 체크아웃 줄(숙소가 든 여행) — 장소가 풀리고, 숙소 항목의 후보 · 검색이 숙소 후보를 낸다. `[2026-10-15 숙박예약 동작 점검 LB-01]`

☆왜: 10-13 숙소 항목 B안(체크인 ~ 체크아웃 = 한 항목, 종류 `lodging`)이 들어간 뒤 x600 서버 웹에서 숙소가 든 여행을 만들 수 없었다.
  뿌리가 둘이었다.
  ①**장소 읽기** — 줄 글자 그대로(「더 플라자 호텔 서울 체크인」 · 「호텔 체크인」)를 장소 이름으로 찾았다. 「체크인」이 붙은 말은 어떤 곳과도 이름이 맞지 않아
    이름 있는 호텔도 `unresolved`, 이름 없는 「호텔 체크인」은 「체크인」이 남는 조각이라 이름 없는 줄(`needs_choice`)로도 못 읽혔다.
    (제목에서 「체크인」을 떼는 것은 항목을 조립할 때(`assemble._stays`)라 장소를 찾은 **뒤**였다.)
  ②**후보 · 검색의 종류 비교** — 후보 · 장소 검색은 지도의 숙소를 활동(`activity`)으로 읽는데 항목 종류는 `lodging` 이라 전부 걸러져 늘 `no_same_kind` 였다.
  이 시험은 접수 원문 → 장소 확정 → 항목 종류 → 후보 · 검색까지를 **실제 접수 흐름**으로 본다(시험 `test_lodging_flight_options.py` 는 `kind="lodging"` 항목을 직접 만들어 접수 경로를 거치지 않았다 — LB-13).

★지키려는 것
 ①이름 있는 호텔 줄(「더 플라자 호텔 서울 체크인」)은 체크인 말을 빼고 찾아 장소가 풀린다 — 항목은 숙소 한 개, 제목은 호텔 이름
 ②이름 없는 「호텔 체크인」은 이름 없는 줄(`needs_choice`, 분류 숙박)로 읽히고 후보는 숙소다 — 숙소 항목에는 **식사 짝이 붙지 않는다**
 ③못 푼 숙소 항목의 장소 검색도 지도의 숙소를 돌려준다
 ④「체크아웃」만 있는 줄은 찾을 이름이 없으므로 옛 길 그대로(항목이 되지 않고 합쳐진다)
 ⑤전체 자동 추천은 숙소 항목의 **장소만** 채운다 — 숙소 후보가 나오게 된 뒤에도 체크인 · 체크아웃 시각과 날짜를 앞뒤 이동에 맞춰 줄이지 않는다

재현:

    python -m pytest tests/e2e/test_intake_stay_lines.py -v
"""
from __future__ import annotations

import pytest

from app.domains.travel_ops.components.intake import candidates

from .test_intake_review import Tour, _catalog, _client, _item, _key, _send, rv  # noqa: F401
from .test_trip_api import api  # noqa: F401

PLAZA = {"id": "31", "name": "더 플라자 호텔 서울", "category": "여행 > 숙박 > 호텔", "category_group": "AD5",
         "address": "서울 중구 소공로 119", "latitude": 37.5655, "longitude": 126.9780}
STAY_MAP = {"id": "41", "name": "광화문스테이호텔", "category": "여행 > 숙박 > 호텔", "category_group": "AD5",
            "address": "서울 종로구 사직로 1", "latitude": 37.5801, "longitude": 126.9775}
BUFFET = {"id": "42", "name": "호텔 뷔페 광화문", "category": "음식점 > 뷔페", "category_group": "FD6",
          "address": "서울 종로구 사직로 2", "latitude": 37.5802, "longitude": 126.9776}


class _StayKakao:
    """카카오 모방 — 「플라자」가 든 말은 그 호텔을, 「숙소」는 지도의 숙소와 음식점을 돌려준다. 그 밖은 비어 있다."""

    def __init__(self):
        self.asked: list = []
        self.misses: dict = {}

    def search(self, query, size=5, near=None, **_):
        self.asked.append(query)
        if "플라자" in query:
            return [dict(PLAZA)]
        if "숙소" in query or "게스트하우스" in query:
            return [dict(STAY_MAP), dict(BUFFET)]
        return []


@pytest.fixture()
def hotels(rv):  # noqa: F811
    _catalog(rv, "920001", "32", "광화문호텔", 37.5790, 126.9772, "서울특별시 종로구 사직로 161")
    yield rv


def _send_plan(text, kakao=None):
    client, tour, kakao = _client(kakao=kakao or _StayKakao())
    headers = _key(client)
    return client, headers, tour, kakao, _send(client, headers, text)


def _candidates(client, headers, view, item):
    return client.get(f"/v1/web/trip-intakes/{view['intake_id']}/candidates?source_id={item['source_id']}&index={item['index']}"
                      f"&revision={view['revision']}", headers=headers).json()


def _search(client, headers, view, item, q):
    return client.get(f"/v1/web/trip-intakes/{view['intake_id']}/place-search?source_id={item['source_id']}&index={item['index']}"
                      f"&revision={view['revision']}&q={q}", headers=headers).json()


def test_a_named_hotel_check_in_line_resolves_its_place_without_the_check_in_word(hotels):
    client, headers, tour, kakao, view = _send_plan(
        "2030-11-06\n15:00 더 플라자 호텔 서울 체크인\n17:00 경복궁\n2030-11-07\n11:00 체크아웃")
    stay = _item(view, "더 플라자 호텔 서울")
    assert stay["kind"] == "lodging", stay
    assert stay["place_state"] == "found" and stay["place"]["name"] == "더 플라자 호텔 서울", stay
    assert not any("체크인" in str(q) for q in [*tour.asked, *kakao.asked]), (tour.asked, kakao.asked)           # 찾을 때 체크인 말을 빼고 물었다
    assert not [i for i in view["review"]["items"] if "체크아웃" in i["title"]], "체크아웃 줄은 항목이 되지 않고 숙소에 합쳐진다"


def test_a_nameless_hotel_check_in_line_is_a_pick_a_lodging_line_and_never_a_meal_pair(hotels):
    client, headers, tour, kakao, view = _send_plan("2030-11-06\n15:00 호텔 체크인\n17:00 경복궁\n2030-11-07\n11:00 호텔 체크아웃")
    stay = _item(view, "호텔")
    assert stay["kind"] == "lodging" and stay["place_state"] == "needs_choice" and stay["place"] is None, stay
    assert stay["parts"]["content_type"] == "32" and stay["parts"]["label"] == "숙소", stay["parts"]
    assert not any(word in str(q) for q in [*tour.asked, *kakao.asked] for word in ("체크인", "체크아웃", "호텔")), (tour.asked, kakao.asked)
    got = _candidates(client, headers, view, stay)
    names = [c["place"]["name"] for c in got["candidates"]]
    assert "광화문호텔" in names and "광화문스테이호텔" in names, got                       # 목록의 숙박과 지도의 숙소가 후보로 온다
    assert "호텔 뷔페 광화문" not in names, got                                               # 음식점은 버린다
    assert all(c["place"]["kind"] == "activity" and c["basis"] == "lodging" for c in got["candidates"]), got   # 숙박 한 건은 식사 짝을 붙이지 않는다
    assert "stay_and_meal" not in got["notes"] and "no_same_kind" not in got["notes"], got


def test_the_place_search_of_an_unresolved_stay_finds_lodgings_on_the_map(hotels):
    client, headers, _tour, _kakao, view = _send_plan("2030-11-06\n15:00 무명 게스트하우스 체크인\n17:00 경복궁\n2030-11-07\n11:00 체크아웃")
    stay = _item(view, "무명 게스트하우스")
    assert stay["kind"] == "lodging" and stay["place_state"] == "unresolved", stay                    # 이름으로는 못 찾았다 → 고객이 검색으로 고친다
    got = _search(client, headers, view, stay, "게스트하우스")
    assert [r["place"]["name"] for r in got["results"]] == ["광화문스테이호텔"], got                     # 지도의 숙소만 — 음식점은 거른다
    assert "no_same_kind" not in got["notes"], got


def test_an_unresolved_stay_gets_lodging_candidates_from_the_map_not_no_same_kind(hotels):
    client, headers, _tour, kakao, view = _send_plan("2030-11-06\n15:00 무명 게스트하우스 체크인\n17:00 경복궁\n2030-11-07\n11:00 체크아웃")
    stay = _item(view, "무명 게스트하우스")
    got = _candidates(client, headers, view, stay)
    assert "no_same_kind" not in got["notes"], got
    assert any(c["place"]["name"] == "광화문스테이호텔" for c in got["candidates"]), (got, kakao.asked)


def test_auto_fix_fills_only_the_place_of_a_stay_and_never_touches_its_check_in_and_check_out(hotels):
    client, headers, _tour, _kakao, view = _send_plan("2030-11-06\n15:00 호텔 체크인\n17:00 경복궁\n2030-11-08\n11:00 호텔 체크아웃")
    stay = _item(view, "호텔")
    assert stay["place_state"] == "needs_choice" and stay["ends_at"] == "11:00" and stay["end_date"] == "2030-11-08", stay
    got = client.post(f"/v1/web/trip-intakes/{view['intake_id']}/autofix", headers=headers, json={"revision": view["revision"]})
    assert got.status_code == 200, got.text
    body = got.json()
    [change] = [c for c in body["changed"] if c["title"] == "호텔"]
    assert change["reason"] == "place", change                                                    # 시각을 바꾸는 이유(`place_and_time` · `time`)가 아니다
    assert (change["to"]["starts_at"], change["to"]["ends_at"]) == ("15:00", "11:00"), change
    new = _item(body["view"], "호텔")
    assert new["place_state"] == "customer" and new["place"]["name"] in ("광화문호텔", "광화문스테이호텔"), new
    assert (new["starts_at"], new["ends_at"], new["end_date"]) == ("15:00", "11:00", "2030-11-08"), "체크아웃 날과 시각이 그대로다"


def test_a_plan_with_a_named_hotel_goes_all_the_way_to_a_registered_trip(hotels):
    """★접수 원문 → 장소 확정 → 확인 화면 준비 → 등록. 전에는 호텔 줄이 장소를 못 정해 이 화면에서 멈췄다(x600 서버 웹에서 숙소가 든 여행을 못 만든다)."""
    from tests.helpers.plugin_bindings import publish_fixed_mobility

    client, tour, kakao = _client(kakao=_StayKakao())
    publish_fixed_mobility(client.app, source="stay_lines")
    headers = _key(client)
    view = _send(client, headers, "2026-12-06\n15:00 더 플라자 호텔 서울 체크인\n17:00 경복궁\n2026-12-07\n09:30 N서울타워\n11:00 체크아웃")
    assert view["review"]["ready"] is True, [(i["title"], i["place_state"]) for i in view["review"]["items"]]
    done = client.post(f"/v1/web/trip-intakes/{view['intake_id']}/confirm", headers=headers, json={"revision": view["revision"]})
    assert done.status_code == 200, done.text
    items = done.json()["trip"]["items"]
    [stay] = [i for i in items if i["kind"] == "lodging"]
    assert stay["title"] == "더 플라자 호텔 서울" and (stay["starts_at"][:16], stay["ends_at"][:16]) == ("2026-12-06T15:00", "2026-12-07T11:00"), stay
    assert stay["place"], stay                                                                  # 장소가 있다 — 지도 핀 자리
    assert [i["title"] for i in items if i["kind"] == "activity"] == ["경복궁", "N서울타워"], "체크아웃 줄은 따로 일정이 되지 않는다"


def test_the_stay_kind_compares_as_an_activity_place():
    assert candidates.place_kind_of({"kind": "lodging"}) == "activity"
    assert candidates.place_kind_of({"kind": "dining"}) == "dining" and candidates.place_kind_of({"kind": "activity"}) == "activity"
