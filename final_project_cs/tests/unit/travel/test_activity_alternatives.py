# -*- coding: utf-8 -*-
"""대체 장소 후보(`alternatives.py`) + `check_feasible` 3분기 status.

wiki/teams/activity.md 「에이전트 유스케이스 검토」 — 작업자 B 범위:
  2단계 문제 판정 3분기(problem / insufficient_info / ok)
  4단계 대체 장소 후보(① 가용성 → ② 유사도 → ②' 반경 1→10km → ③ 선호도 폴백 → ④ 거리 순위)
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.travel_ops.activity import ActivityTeam
from app.modules.travel_ops.activity.alternatives import (
    RADIUS_MAX_KM, bounding_box, closed_on, open_at, rank_alternatives, search_steps)

from .helpers import FakeTools, pack, task

KST = timezone(timedelta(hours=9))        # ★요청 시각은 전부 한국 시간으로 다룬다(2026-10-01)
MONDAY = datetime(2026, 10, 5, 10, tzinfo=KST)
SATURDAY = datetime(2026, 10, 3, 10, tzinfo=KST)

CSV_PATH = (Path(__file__).resolve().parents[3]
            / "app" / "modules" / "travel_ops" / "activity" / "data_processing"
            / "activity_total_data.csv")


def _place(cid, *, l1="HS", l2="HS01", l3="HS010100", ctype="12", sgg="23",
           x=126.9770, y=37.5796, closed="연중무휴", title=None, brand=None, hours=None):
    return {"brand": brand, "contentid": cid, "title": title or f"장소{cid}", "contenttypeid": ctype,
            "lclsSystm1": l1, "lclsSystm2": l2, "lclsSystm3": l3, "sigungucode": sgg,
            "mapx": str(x), "mapy": str(y), "closed_days": closed,
            "business_hours": hours}


ORIGIN = _place("origin", title="경복궁", closed="매주 화요일")


# ══════════════════════════════════════════════════════════════════
# ① 가용성 — closed_on
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text, at, expected", [
    ("매주 월요일", MONDAY, True),
    ("매주 월요일", SATURDAY, False),
    ("연중무휴", MONDAY, False),
    ("매주 토요일~일요일 / 법정공휴일", SATURDAY, True),
    ("매주 토요일~일요일 / 법정공휴일", MONDAY, False),
    ("매주 일요일~월요일", MONDAY, True),           # 주를 넘는 범위
    ("주말", SATURDAY, True),
    ("매주 월요일 / 1월 1일 / 설·추석 당일", MONDAY, True),
    ("설·추석 당일", MONDAY, False),                 # 날짜 휴무는 요일 판정에 안 넣는다
    ("", MONDAY, None),
    (None, MONDAY, None),
    ("점포별 상이", MONDAY, None),
    ("홈페이지 참조", MONDAY, None),
])
def test_closed_on(text, at, expected):
    assert closed_on(text, at) is expected


def test_closed_candidates_are_dropped_unknown_kept_but_marked():
    pool = [_place("a", closed="매주 월요일"),
            _place("b", closed="점포별 상이"),
            _place("c", closed="연중무휴")]
    result = rank_alternatives(ORIGIN, pool, MONDAY)
    ids = [a["contentid"] for a in result["alternatives"]]
    assert ids == ["c", "b"]            # 확인된 곳이 먼저, 모름은 뒤
    assert result["alternatives"][1]["availability"] == "unconfirmed"


def test_origin_itself_is_never_an_alternative():
    result = rank_alternatives(ORIGIN, [dict(ORIGIN, closed_days="연중무휴")], SATURDAY)
    assert result["alternatives"] == []


# ── 운영시간 — open_at ─────────────────────────────────────────

def _at(weekday_offset, hour, minute=0):
    """2026-10-05(월) 기준 KST 시각. 요일은 offset 으로(0=월)."""
    return datetime(2026, 10, 5 + weekday_offset, hour, minute, tzinfo=KST)


@pytest.mark.parametrize("text, at, expected", [
    ("상시 개방", _at(0, 3), True),
    ("24시간 운영", _at(2, 3), True),
    ("09:00~18:00", _at(0, 10), True),
    ("09:00~18:00", _at(0, 18), False),                         # 끝 시각은 닫힌 것
    ("09:00 ~ 18:00(입장마감 17:00)", _at(0, 17, 30), True),     # 괄호 설명은 뗀다
    ("10:00~02:00", _at(0, 1), True),                           # 자정 넘김
    ("10:00~02:00", _at(0, 5), False),
    ("00:00~24:00", _at(0, 23, 59), True),
    ("평일·금·토·일·휴일 10:00 ~ 22:00", _at(5, 21), True),      # 사실상 매일
    ("평일·금·토·일·휴일 10:00 ~ 22:00", _at(0, 22, 30), False),
    ("평일 09:00~18:00 / 주말 10:00~20:00", _at(5, 19), True),   # 토요일은 주말 구간
    ("평일 09:00~18:00 / 주말 10:00~20:00", _at(0, 19), False),
    ("월~금 09:00~18:00", _at(0, 10), True),                    # 요일 범위
    ("월~금 09:00~18:00", _at(5, 10), None),                    # 토요일 구간이 없다 — 모름
    ("금~월 10:00~20:00", _at(6, 12), True),                    # 주를 넘는 범위
    ("", _at(0, 10), None),
    (None, _at(0, 10), None),
    ("점포별 상이함", _at(0, 10), None),
    ("[한국영상자료원]<br>10:00~19:00", _at(0, 10), None),       # 시설이 둘 — 모름
    ("10:00~22:00 (브레이크타임 15:00~16:00)", _at(0, 15, 30), None),
    ("평일 09:00~18:00", _at(5, 10), None),                     # 토요일 구간이 없다 — 모름
    ("평일 11:00~20:00 / 휴일 09:00~22:00", _at(0, 10), None),   # 공휴일 시간이 따로 있다 — 오늘이 공휴일인지 몰라 단정 안 함
])
def test_open_at(text, at, expected):
    assert open_at(text, at) is expected


def test_open_at_reads_utc_input_as_korean_time():
    """UTC 10:00 = KST 19:00 — 09:00~18:00 은 닫힌 시각이다."""
    assert open_at("09:00~18:00", datetime(2026, 10, 5, 10, tzinfo=timezone.utc)) is False
    assert open_at("09:00~18:00", datetime(2026, 10, 5, 0, 30, tzinfo=timezone.utc)) is True


def test_candidates_closed_at_that_hour_are_dropped_before_similarity():
    pool = [_place("closed_now", closed=None, hours="09:00~12:00"),
            _place("open_now", closed=None, hours="09:00~20:00"),
            _place("unknown_hours", closed=None, hours=None)]
    result = rank_alternatives(ORIGIN, pool, _at(0, 14))
    ids = [a["contentid"] for a in result["alternatives"]]
    assert ids == ["open_now", "unknown_hours"]                  # 확인된 곳이 먼저, 모름은 뒤
    assert result["alternatives"][0]["availability"] == "open_at_time"   # 휴무는 모르지만 운영시간 안
    assert result["alternatives"][1]["availability"] == "unconfirmed"
    assert result["total_matched"] == 2


def test_hours_filter_runs_before_fallback_so_closed_places_never_reach_the_fallback():
    pool = [_place("closed_now", l3="X", hours="09:00~12:00")]
    result = rank_alternatives(ORIGIN, pool, _at(0, 14), preference="activity")
    assert result["alternatives"] == [] and result["total_matched"] == 0


# ══════════════════════════════════════════════════════════════════
# ②③ 유사도 + 반경 + 선호도 폴백
# ══════════════════════════════════════════════════════════════════

KM = 0.01134          # 원래 장소(북위 37.58°)에서 경도 1km 폭 — `x=126.9770 + KM * n` 은 동쪽 약 n km


def _at_km(cid, km, **kw):
    return _place(cid, x=126.9770 + KM * km, **kw)


def test_search_steps_order_per_preference():
    """이동은 반경이 바깥 고리(가까운 곳에서 분류부터 푼다), 활동은 분류가 바깥 고리(반경부터 넓힌다)."""
    mobility = search_steps("mobility")
    assert mobility[:5] == [(1.0, ()), (1.0, ("lclsSystm3",)), (1.0, ("lclsSystm3", "lclsSystm2")),
                            (1.0, ("lclsSystm3", "lclsSystm2", "lclsSystm1", "contenttypeid")),
                            (2.0, ())]
    activity = search_steps("activity")
    assert activity[:3] == [(1.0, ()), (2.0, ()), (3.0, ())]
    assert activity[10] == (1.0, ("lclsSystm3",))
    assert search_steps(None) == [(float(r), ()) for r in range(1, 11)]
    assert mobility[-1][0] == activity[-1][0] == RADIUS_MAX_KM


def test_bounding_box_is_1km_per_009_lat_and_0113_lng():
    assert bounding_box(37.5, 127.0, 2) == pytest.approx((37.482, 37.518, 126.9774, 127.0226))


def test_stops_at_1km_once_ten_are_found():
    """★화면 3곳이 아니라 「더보기」까지 채울 10곳이 남으면 멈춘다(2026-10-03)."""
    pool = [_at_km(f"p{i}", 0.05 * (i + 1)) for i in range(10)] + [_at_km("far", 1.5)]
    result = rank_alternatives(ORIGIN, pool, SATURDAY)
    assert result["radius_km"] == 1.0 and result["total_matched"] == 10
    assert [a["contentid"] for a in result["alternatives"]] == ["p0", "p1", "p2"]
    assert "far" not in [a["contentid"] for a in result["more_alternatives"]]


def test_three_found_is_not_enough_widening_continues_to_fill_more():
    pool = [_at_km(f"n{i}", 0.1 * (i + 1)) for i in range(3)] + [_at_km("b", 1.5), _at_km("c", 2.5)]
    result = rank_alternatives(ORIGIN, pool, SATURDAY)
    assert result["radius_km"] == RADIUS_MAX_KM          # 10곳이 안 차서 끝까지 넓혔다
    assert [a["contentid"] for a in result["more_alternatives"]] == ["b", "c"]


def test_widens_radius_1km_at_a_time_until_ten():
    pool = [_at_km(f"p{i}", 0.3 * (i + 1)) for i in range(12)]     # 0.3·0.6·…·3.6km
    result = rank_alternatives(ORIGIN, pool, SATURDAY)
    assert result["radius_km"] == 3.0 and result["total_matched"] == 10


def test_never_widens_beyond_10km():
    """★최대 10km — 그 밖은 같은 갈래여도 「근처에 없음」이다."""
    result = rank_alternatives(ORIGIN, [_at_km("far", 12)], SATURDAY, preference="activity")
    assert result["alternatives"] == [] and result["reason"] == "none_within_max_radius"
    assert result["radius_km"] == RADIUS_MAX_KM


def test_fewer_than_three_returns_what_the_loosest_step_found():
    result = rank_alternatives(ORIGIN, [_at_km("x", 0.3, l3="HS019999")], SATURDAY, preference="activity")
    assert [a["contentid"] for a in result["alternatives"]] == ["x"]
    assert result["dropped_fields"] == ["lclsSystm3", "lclsSystm2"] and result["radius_km"] == RADIUS_MAX_KM


def test_preference_none_widens_radius_but_never_drops_class_fields():
    pool = [_at_km("x", 0.1, ctype="14"), _at_km("same", 4)]     # 관광타입만 다른 가까운 곳 / 같은 갈래 먼 곳
    result = rank_alternatives(ORIGIN, pool, SATURDAY, preference=None)
    assert [a["contentid"] for a in result["alternatives"]] == ["same"]
    assert result["dropped_fields"] == []


def test_preference_none_fills_only_more_with_a_dropped_small_class():
    """★선호도 없음 — 화면은 정확한 분류만, 10곳이 안 차면 「더보기」에만 소분류를 푼 곳을 채운다."""
    exact = [_at_km(f"exact{i}", 3 + 0.1 * i) for i in range(2)]
    loose = [_at_km(f"loose{i}", 0.05 * (i + 1), l3="HS019999") for i in range(12)]
    other_mid = [_at_km("other_mid", 0.01, l2="HS02", l3="HS020100")]          # 중분류가 다르면 안 든다
    result = rank_alternatives(ORIGIN, exact + loose + other_mid, SATURDAY, preference=None)
    assert [a["contentid"] for a in result["alternatives"]] == ["exact0", "exact1"]   # 느슨한 곳으로 안 채운다
    more = [a["contentid"] for a in result["more_alternatives"]]
    assert more == [f"loose{i}" for i in range(8)]                                  # 10곳까지, 가까운 순
    assert [a["rank"] for a in result["more_alternatives"]] == list(range(3, 11))
    assert result["dropped_fields"] == [] and result["more_dropped_fields"] == ["lclsSystm3"]
    assert result["total_matched"] == 2


def test_more_only_fill_is_not_used_with_a_preference_or_when_full():
    pool = [_at_km(f"p{i}", 0.05 * (i + 1)) for i in range(10)] + [_at_km("loose", 0.01, l3="X")]
    assert rank_alternatives(ORIGIN, pool, SATURDAY)["more_dropped_fields"] == []          # 이미 10곳
    assert rank_alternatives(ORIGIN, pool[:1], SATURDAY, preference="activity")["more_dropped_fields"] == []


def test_more_only_fill_skips_brands_already_found_strictly():
    oy = _shop("oy_strict", "올리브영", x=126.9850)
    loose = {**SHOP, "l3": "SH049999"}
    oy_loose = _place("oy_loose", brand="올리브영", sgg="24", x=126.9775, **loose)   # 더 가깝지만 같은 체인
    ds_loose = _place("ds_loose", brand="다이소", sgg="24", x=126.9780, **loose)
    result = rank_alternatives(BRAND_ORIGIN, [oy, oy_loose, ds_loose], SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["oy_strict"]
    assert [a["contentid"] for a in result["more_alternatives"]] == ["ds_loose"]


@pytest.mark.parametrize("preference, diff, dropped", [
    ("mobility", dict(l3="X"), ["lclsSystm3"]),
    ("mobility", dict(l3="X", l2="HS02"), ["lclsSystm3", "lclsSystm2"]),
    ("mobility", dict(l3="X", l2="NA01", l1="NA", ctype="14"),
     ["lclsSystm3", "lclsSystm2", "lclsSystm1", "contenttypeid"]),
    ("activity", dict(l3="X"), ["lclsSystm3"]),
    ("activity", dict(l3="X", l2="HS02"), ["lclsSystm3", "lclsSystm2"]),
])
def test_fallback_order_per_preference(preference, diff, dropped):
    """이동: 소분류→중분류→대분류(+타입). 활동: 소분류→중분류. 그 단계에서야 맞는 후보 10곳을 1km 안에 둔다."""
    pool = [_at_km(f"x{i}", 0.05 * (i + 1), **diff) for i in range(10)]
    result = rank_alternatives(ORIGIN, pool, SATURDAY, preference=preference)
    assert result["dropped_fields"] == dropped
    assert result["radius_km"] == 1.0
    assert len(result["alternatives"]) == 3 and len(result["more_alternatives"]) == 7


def test_mobility_loosens_class_before_widening_radius_activity_the_opposite():
    near_other = [_at_km(f"near{i}", 0.05 * (i + 1), l1="VE", l2="VE01", l3="VE010100", ctype="14")
                  for i in range(10)]
    far_same = [_at_km(f"far{i}", 2.1 + 0.05 * i) for i in range(10)]
    mobility = rank_alternatives(ORIGIN, near_other + far_same, SATURDAY, preference="mobility")
    activity = rank_alternatives(ORIGIN, near_other + far_same, SATURDAY, preference="activity")
    assert [a["contentid"] for a in mobility["alternatives"]] == ["near0", "near1", "near2"]
    assert mobility["radius_km"] == 1.0 and "lclsSystm1" in mobility["dropped_fields"]
    assert [a["contentid"] for a in activity["alternatives"]] == ["far0", "far1", "far2"]
    assert activity["radius_km"] == 3.0 and activity["dropped_fields"] == []


def test_loosening_keeps_what_the_stricter_step_found_farther_away():
    """★활동 중요 — 같은 소분류 셋은 3km 에만 있다. 소분류를 풀면 반경이 1km 부터 다시 시작하지만
    앞에서 찾은 셋은 남고, 분류가 더 가까워 화면 앞에 선다(실데이터 창경궁에서 나온 경우)."""
    exact = [_at_km(f"exact{i}", 3 + 0.1 * i) for i in range(3)]
    loose = [_at_km(f"loose{i}", 0.05 * (i + 1), l3="HS019999") for i in range(8)]
    result = rank_alternatives(ORIGIN, exact + loose, SATURDAY, preference="activity")
    assert result["dropped_fields"] == ["lclsSystm3"] and result["radius_km"] == 1.0
    assert [a["contentid"] for a in result["alternatives"]] == ["exact0", "exact1", "exact2"]
    assert len(result["more_alternatives"]) == 7


def test_activity_never_drops_large_class_or_type():
    pool = [_at_km("other_type", 0.1, ctype="14", l3="X", l2="HS02")]
    result = rank_alternatives(ORIGIN, pool, SATURDAY, preference="activity")
    assert result["alternatives"] == []


def test_sigungu_is_no_longer_compared():
    """★`[2026-10-02]` 옆 구라도 가까우면 후보다 — 시군구 경계 문제."""
    result = rank_alternatives(ORIGIN, [_at_km("next_gu", 0.3, sgg="1")], SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["next_gu"]
    assert "sigungucode" not in result["matched_fields"]


def test_unknown_preference_is_rejected():
    with pytest.raises(ValueError):
        rank_alternatives(ORIGIN, [], SATURDAY, preference="food")


# ══════════════════════════════════════════════════════════════════
# ④ 거리 순위
# ══════════════════════════════════════════════════════════════════

def test_ranks_by_distance_top_three():
    pool = [_place(str(i), x=126.9770 + 0.01 * i) for i in (4, 1, 3, 2)]   # 약 0.9·1.8·2.6·3.5km
    result = rank_alternatives(ORIGIN, pool, SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["1", "2", "3"]
    assert [a["rank"] for a in result["alternatives"]] == [1, 2, 3]
    assert [a["contentid"] for a in result["more_alternatives"]] == ["4"]
    assert result["total_matched"] == 4


# ── 브랜드: 반경 1km 안의 같은 브랜드가 먼저, 그보다 먼 같은 브랜드는 거리로만 겨룬다 ──

SHOP = dict(l1="SH", l2="SH04", l3="SH040300", ctype="38")
BRAND_ORIGIN = _place("o", brand="올리브영", sgg="24", **SHOP)


def _shop(cid, brand, *, sgg="24", x=126.9770, **kw):
    return _place(cid, brand=brand, sgg=sgg, x=x, **SHOP, **kw)


def test_same_brand_within_1km_beats_a_closer_other_brand():
    pool = [_shop("daiso_near", "다이소", x=126.9771),          # 가장 가깝지만 다른 브랜드
            _shop("oy_far", "올리브영", x=126.9850)]             # 같은 브랜드·0.7km
    result = rank_alternatives(BRAND_ORIGIN, pool, SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["oy_far", "daiso_near"]


def test_same_brand_beyond_1km_does_not_beat_a_closer_other_brand():
    pool = [_shop("oy_1_2km", "올리브영", x=126.9900),            # 같은 브랜드지만 1.2km
            _shop("daiso_near", "다이소", sgg="2", x=126.9775)]
    result = rank_alternatives(BRAND_ORIGIN, pool, SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["daiso_near", "oy_1_2km"]


def test_one_store_per_brand_chain_keeps_the_nearest():
    """★다양성 — 같은 브랜드 체인은 가장 가까운 1곳만. 브랜드 없는 곳은 그대로."""
    pool = [_shop("oy_b", "올리브영", x=126.985), _shop("oy_a", "올리브영", x=126.980),
            _shop("ds_b", "다이소", x=126.983), _shop("ds_a", "다이소", x=126.978),
            _shop("plain1", None, x=126.981), _shop("plain2", None, x=126.982)]
    result = rank_alternatives(BRAND_ORIGIN, pool, SATURDAY)
    ids = [a["contentid"] for a in result["alternatives"] + result["more_alternatives"]]
    assert ids == ["oy_a", "ds_a", "plain1", "plain2"]


def test_brand_diversity_counts_before_stopping():
    """같은 브랜드 열이 1km 안에 있어도 1곳으로 세므로, 1km 에서 멈추지 않고 반경을 넓힌다."""
    pool = [_shop(f"oy{i}", "올리브영", x=126.9770 + KM * 0.05 * (i + 1)) for i in range(10)]
    pool += [_shop("ds", "다이소", x=126.9770 + KM * 1.5), _shop("ab", "아트박스", x=126.9770 + KM * 2.5)]
    result = rank_alternatives(BRAND_ORIGIN, pool, SATURDAY)
    assert result["radius_km"] == RADIUS_MAX_KM
    assert [a["contentid"] for a in result["alternatives"]] == ["oy0", "ds", "ab"]


# ── 하드 필터 · 점수 · 노출 개수 ──────────────────────────────────

def test_places_already_in_the_itinerary_are_excluded():
    pool = [_at_km("in_plan", 0.1), _at_km("a", 0.2)]
    result = rank_alternatives(ORIGIN, pool, SATURDAY, exclude_ids=["in_plan"])
    assert [a["contentid"] for a in result["alternatives"]] == ["a"]


def test_shows_three_and_keeps_up_to_ten_for_more():
    pool = [_at_km(f"p{i:02d}", 0.05 * (i + 1)) for i in range(15)]          # 전부 1km 안
    result = rank_alternatives(ORIGIN, pool, SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["p00", "p01", "p02"]
    assert [a["rank"] for a in result["more_alternatives"]] == list(range(4, 11))
    assert result["total_matched"] == 15


def test_activity_scores_category_before_distance_mobility_the_reverse():
    """둘 다 같은 1km 안 — 활동 중요는 같은 소분류를, 이동 중요는 더 가까운 곳을 앞에 둔다."""
    pool = [_at_km("near_other_l3", 0.1, l3="HS019999"), _at_km("same_l3", 0.6),
            _at_km("x", 0.7, l3="HS019998"), _at_km("y", 0.8, l3="HS019997")]
    activity = rank_alternatives(ORIGIN, pool, SATURDAY, preference="activity")
    mobility = rank_alternatives(ORIGIN, pool, SATURDAY, preference="mobility")
    assert activity["alternatives"][0]["contentid"] == "same_l3"
    assert mobility["alternatives"][0]["contentid"] == "near_other_l3"


def test_each_place_has_a_one_line_reason():
    pool = [_shop("oy", "올리브영", x=126.9800, closed="연중무휴")]
    item = rank_alternatives(BRAND_ORIGIN, pool, SATURDAY)["alternatives"][0]
    assert item["reason"] == "0.3km · 같은 소분류 · 같은 브랜드(올리브영) · 휴무일 아님"
    assert "\n" not in item["reason"]


def test_origin_without_brand_ranks_by_distance_only():
    origin = _place("o", sgg="24", **SHOP)                      # 브랜드 모름
    pool = [_shop("far_oy", "올리브영", x=126.985), _shop("near_daiso", "다이소", x=126.9772)]
    result = rank_alternatives(origin, pool, SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["near_daiso", "far_oy"]


def test_candidates_without_coordinates_are_left_out():
    """★좌표가 없으면 근처인지 모른다 — 반경 안이라고 읽지 않는다."""
    pool = [_place("nocoord", x=""), _place("far", x=127.05)]
    result = rank_alternatives(ORIGIN, pool, SATURDAY)
    assert [a["contentid"] for a in result["alternatives"]] == ["far"]


def test_origin_without_coordinates_is_not_none_nearby():
    result = rank_alternatives(_place("o", x=""), [_place("a")], SATURDAY)
    assert result["reason"] == "origin_no_coordinates" and result["alternatives"] == []
    assert result["radius_km"] is None


@pytest.mark.skipif(not CSV_PATH.exists(), reason="후보 CSV 없음")
def test_real_catalog_changgyeonggung_on_monday():
    """실 CSV(서울 후보)로 — 창경궁(매주 월요일 휴무)을 월요일에 대체하면 같은
    역사관광(HS) 대안이 나오고, 그 대안도 월요일 휴무가 아니다."""
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as fh:
        pool = list(csv.DictReader(fh))
    origin = next(r for r in pool if r["contentid"] == "126511")   # 창경궁
    assert closed_on(origin["closed_days"], MONDAY) is True
    result = rank_alternatives(origin, pool, MONDAY, preference="activity")
    assert 1 <= len(result["alternatives"]) <= 3
    assert all(a["contentid"] != origin["contentid"] for a in result["alternatives"])
    by_id = {r["contentid"]: r for r in pool}
    assert all(by_id[a["contentid"]]["lclsSystm1"] == origin["lclsSystm1"]
               for a in result["alternatives"])
    assert all(closed_on(a["closed_days"], MONDAY) is not True
               for a in result["alternatives"])


# ══════════════════════════════════════════════════════════════════
# check_feasible 3분기 status
# ══════════════════════════════════════════════════════════════════

def _feasible_task():
    context = pack("activity", scope=["activity"])
    return task("activity", "activity.check_feasible", context,
                ActivityTeam.manifest.allowed_tools)


def _values(*, place="default", starts_at=None, party=2, capacity=4, disaster=None):
    starts_at = starts_at or datetime.now(KST) + timedelta(days=30)
    if place == "default":
        place = {"place_id": "p1", "weather_sensitive": False,
                 "latitude": 37.5796, "longitude": 126.9770}
    return {
        "read.booking": {"booking_id": "b1", "place_id": "p1", "starts_at": starts_at,
                         "party_size": party, "capacity": capacity},
        "read.policy": [{"cancel_deadline_hours": 24}],
        "read.place": place,
        "read.weather": None,
        "read.disaster": disaster,
    }


@pytest.mark.asyncio
async def test_feasible_ok():
    result = await ActivityTeam(FakeTools(_values())).execute(_feasible_task())
    assert result.decisions[0]["feasible"] is True


@pytest.mark.asyncio
async def test_feasible_place_unknown_returns_infeasible():
    """place=None → feasible=False + place_confirmed=False (장소를 모르면 성립 단정 안 함)."""
    result = await ActivityTeam(FakeTools(_values(place=None))).execute(_feasible_task())
    assert result.decisions[0]["feasible"] is False
    assert result.decisions[0]["place_confirmed"] is False


@pytest.mark.asyncio
async def test_feasible_problem_on_capacity():
    result = await ActivityTeam(FakeTools(_values(party=5, capacity=4))).execute(_feasible_task())
    assert result.decisions[0]["feasible"] is False
    assert result.decisions[0]["reason"] == "party_over_capacity"


@pytest.mark.asyncio
async def test_feasible_disrupted_by_critical_disaster():
    """위급재난 발령 → feasible=False + disaster.blocks=True."""
    disaster = {"for_region": [{"step": "위급재난", "kind": "지진"}]}
    result = await ActivityTeam(FakeTools(_values(disaster=disaster))).execute(_feasible_task())
    assert result.decisions[0]["feasible"] is False
    assert result.decisions[0]["disaster"]["blocks"] is True
