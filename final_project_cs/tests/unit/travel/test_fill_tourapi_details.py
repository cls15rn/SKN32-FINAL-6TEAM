# -*- coding: utf-8 -*-
"""fill_tourapi_details — 상세 칸 채우기와 인원·예약 확인용 5칸(통일된 컬럼 이름)의 규칙. 네트워크 없이 본다."""
from __future__ import annotations

from app.modules.travel_ops.activity.data_processing import fill_tourapi_details as f


def _row(cid="1", ctype="12", **cols):
    base = {"contentid": cid, "contenttypeid": ctype, "overview": "", "business_hours": "", "closed_days": "",
            "fee": "", **{c: "" for c in f.EXTRA_COLUMNS}}
    return {**base, **cols}


def test_extra_columns_use_one_name_per_concept_and_only_known_ones():
    mapped = {column for fields in f.EXTRA_FIELDS.values() for column in fields}
    assert mapped <= set(f.EXTRA_COLUMNS) and set(f.EXTRA_COLUMNS) <= mapped
    assert set(f.EXTRA_FIELDS) <= set(f.INTRO_FIELDS)


def test_each_type_maps_its_own_field_names():
    assert f.EXTRA_FIELDS["12"] == {"info_center": "infocenter", "capacity": "accomcount", "age_limit": "expagerange",
                                    "experience_guide": "expguide"}
    assert f.EXTRA_FIELDS["14"] == {"info_center": "infocenterculture", "capacity": "accomcountculture",
                                    "spend_time": "spendtime"}
    assert f.EXTRA_FIELDS["15"] == {"info_center": ("sponsor1tel", "sponsor2tel"), "reservation": ("bookingplace", "eventhomepage"),
                                    "spend_time": "spendtimefestival", "age_limit": "agelimit"}
    assert f.EXTRA_FIELDS["28"] == {"info_center": "infocenterleports", "reservation": "reservation",
                                    "capacity": "accomcountleports", "age_limit": "expagerangeleports"}
    assert f.EXTRA_FIELDS["38"] == {"info_center": "infocentershopping"}


def test_several_fields_with_one_meaning_are_joined_without_blanks_or_duplicates():
    assert f._joined({"sponsor1tel": "02-1", "sponsor2tel": "02-2"}, ("sponsor1tel", "sponsor2tel")) == "02-1 / 02-2"
    assert f._joined({"sponsor1tel": "02-1", "sponsor2tel": ""}, ("sponsor1tel", "sponsor2tel")) == "02-1"
    assert f._joined({"sponsor1tel": "02-1", "sponsor2tel": "02-1"}, ("sponsor1tel", "sponsor2tel")) == "02-1"
    assert f._joined({}, ("sponsor1tel", "sponsor2tel")) == ""


def test_operations_depend_on_what_is_already_filled():
    assert f.operations_for(_row()) == f.BOTH_OPERATIONS                          # 상세가 비었다
    assert f.operations_for(_row(overview="개요")) == f.INTRO_ONLY                # 상세는 있고 새 칸이 비었다
    assert f.operations_for(_row(overview="개요", info_center="02-1")) == ()      # 다 채워졌다
    assert f.operations_for(_row(cid="OY1", ctype="38")) == ()                    # 브랜드 행(숫자 아님)은 안 부른다
    assert f.operations_for(_row(ctype="99")) == ()                               # 모르는 타입


def test_apply_cache_fills_extras_from_the_intro_response_without_overwriting():
    rows = [_row("1", "28", overview="이미 있음"), _row("2", "15")]
    cache = {("1", "detailIntro2"): {"infocenterleports": "02-111", "reservation": "전화 예약",
                                     "accomcountleports": "30명", "expagerangeleports": "만 7세 이상",
                                     "usetimeleports": "09~18"},
             ("2", "detailCommon2"): {"overview": "행사 개요"},
             ("2", "detailIntro2"): {"bookingplace": "현장", "eventhomepage": "https://ex.kr", "spendtimefestival": "1시간",
                                     "agelimit": "전체",
                                     "sponsor1tel": "02-1", "sponsor2tel": "02-2"}}
    stats = f.apply_cache(rows, cache)
    assert rows[0]["info_center"] == "02-111" and rows[0]["reservation"] == "전화 예약"
    assert rows[0]["capacity"] == "30명" and rows[0]["overview"] == "이미 있음"        # 있던 칸은 그대로
    assert (rows[1]["reservation"], rows[1]["spend_time"], rows[1]["age_limit"]) == (
        "현장 / https://ex.kr", "1시간", "전체")
    assert rows[0]["age_limit"] == "만 7세 이상" and rows[1]["info_center"] == "02-1 / 02-2"
    assert rows[1]["capacity"] == ""                                                   # 그 타입에 없는 칸은 비운다
    assert stats["reservation"] == 2


def test_apply_cache_leaves_extras_empty_when_the_api_has_no_value():
    rows = [_row("1", "12", overview="개요")]
    f.apply_cache(rows, {("1", "detailIntro2"): {"infocenter": "", "accomcount": ""}})
    assert all(rows[0][c] == "" for c in f.EXTRA_COLUMNS)


def test_experience_guide_comes_only_from_sightseeing_expguide():
    rows = [_row("1", "12", overview="개요"), _row("2", "14", overview="개요")]
    f.apply_cache(rows, {("1", "detailIntro2"): {"expguide": "체험 예약은 전화로"},
                         ("2", "detailIntro2"): {"expguide": "무시되어야 함"}})
    assert rows[0]["experience_guide"] == "체험 예약은 전화로" and rows[1]["experience_guide"] == ""
