# -*- coding: utf-8 -*-
"""활동 후보 데이터(`activity_total_data.csv`)의 형식과 적재 스크립트를 지킨다.

CSV 는 레포에 올라가 있어 CI 가 매번 이 파일로 시험한다. 누가 CSV 를 다시 만들거나 합치다
식별자가 겹치거나 좌표·분류가 깨지면 여기서 멈춘다 — DB 에 넣은 뒤에 알면 늦다.
적재 스크립트(`scripts/load_place_catalog_csv.py`)의 순수 함수도 여기서 본다(DB 없이).
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import pytest

from app.infrastructure.travel.tour_api import LARGE_CLASS_NAMES
from app.modules.travel_ops.activity.db_search.place_candidates import DEFAULT_SOURCES
from scripts.load_place_catalog_csv import (
    SEOUL_LAT, SEOUL_LON, _origin_from_id, read_rows, to_row)

CSV_PATH = (Path(__file__).resolve().parents[3] / "app" / "modules" / "travel_ops"
            / "activity" / "data_processing" / "activity_total_data.csv")

COLUMNS = ["contentid", "contenttypeid", "title", "addr1", "addr2", "sigungucode",
           "mapx", "mapy", "lclsSystm1", "lclsSystm2", "lclsSystm3",
           "overview", "business_hours", "closed_days", "fee", "brand"]
#: 브랜드 행 contentid 접두어 → 적재 출처. 후보 조회(`DEFAULT_SOURCES`)가 읽는 출처와 맞아야 한다.
BRAND_PREFIX = {"OY": "oliveyoung", "DS": "daiso", "AB": "artbox", "MS": "musinsa"}
#: 브랜드 분류코드(memory: 올영·다이소·아트박스 = SH040300, 무신사는 텍스리펀 매장만 SH040300 나머지 SH070100)
BRAND_LCLS3 = {"OY": {"SH040300"}, "DS": {"SH040300"}, "AB": {"SH040300"},
               "MS": {"SH040300", "SH070100"}}


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _is_brand(row: dict[str, str]) -> bool:
    return not row["contentid"].isdigit()


# ── CSV 형식 ────────────────────────────────────────────────────

#: `[2026-10-01]` 인원·예약 확인용 칸 — `fill_tourapi_details` 가 맨 뒤에 붙인다. 아직 안 돌렸으면 없어도 된다.
EXTRA_COLUMNS = ["info_center", "reservation", "capacity", "spend_time", "age_limit", "experience_guide"]


def test_columns_are_exactly_the_expected_ones(rows):
    keys = list(rows[0].keys())
    assert keys[:len(COLUMNS)] == COLUMNS
    assert keys[len(COLUMNS):] in ([], EXTRA_COLUMNS)      # 일부만 있는 헤더는 허용하지 않는다


def test_every_row_has_id_and_title(rows):
    assert all(r["contentid"].strip() and r["title"].strip() for r in rows)


def test_content_ids_are_unique(rows):
    dups = [k for k, v in Counter(r["contentid"] for r in rows).items() if v > 1]
    assert not dups, f"contentid 중복: {dups[:5]}"


def test_content_id_is_digits_or_known_brand_prefix(rows):
    bad = [r["contentid"] for r in rows
           if not r["contentid"].isdigit() and r["contentid"][:2] not in BRAND_PREFIX]
    assert not bad, f"출처를 모르는 contentid: {bad[:5]}"


def test_coordinates_are_inside_seoul(rows):
    """★서울 밖 좌표(공급자 자리표시값)는 적재 때 NULL 이 된다. CSV 단계에서 이미 없어야 한다."""
    bad = []
    for r in rows:
        try:
            lon, lat = float(r["mapx"]), float(r["mapy"])
        except ValueError:
            bad.append(r["contentid"])
            continue
        if not (SEOUL_LON[0] <= lon <= SEOUL_LON[1] and SEOUL_LAT[0] <= lat <= SEOUL_LAT[1]):
            bad.append(r["contentid"])
    assert not bad, f"서울 범위 밖이거나 좌표가 없다: {bad[:5]}"


def test_addresses_are_in_seoul(rows):
    """CsvPlaceLookup.find 가 서울 주소만 돌려준다 — 아닌 행은 찾을 수 없는 죽은 행이다."""
    bad = [r["contentid"] for r in rows if not r["addr1"].startswith("서울")]
    assert not bad, f"서울이 아닌 주소: {bad[:5]}"


def test_classification_codes_are_well_formed_and_nested(rows):
    for r in rows:
        l1, l2, l3 = r["lclsSystm1"], r["lclsSystm2"], r["lclsSystm3"]
        assert l1 in LARGE_CLASS_NAMES, (r["contentid"], l1)
        assert re.fullmatch(r"[A-Z]{2}\d{2}", l2) and l2.startswith(l1), (r["contentid"], l2)
        assert not l3 or (re.fullmatch(r"[A-Z]{2}\d{6}", l3) and l3.startswith(l2)), (r["contentid"], l3)


def test_sigungu_code_is_a_number(rows):
    assert all(r["sigungucode"].isdigit() for r in rows)


# ── 브랜드 행 ───────────────────────────────────────────────────

def test_brand_rows_have_brand_name_and_expected_classification(rows):
    for r in rows:
        if not _is_brand(r):
            continue
        prefix = r["contentid"][:2]
        assert r["brand"].strip(), r["contentid"]
        assert r["lclsSystm3"] in BRAND_LCLS3[prefix], (r["contentid"], r["lclsSystm3"])


def test_every_brand_source_is_read_by_the_candidate_pool():
    """★적재 출처와 후보 조회 출처가 어긋나면 적재해도 후보에 안 나온다(2026-10-01 점검)."""
    assert set(BRAND_PREFIX.values()) | {"tour_api"} == set(DEFAULT_SOURCES)


# ── 적재 스크립트(DB 없이) ──────────────────────────────────────

@pytest.mark.parametrize("cid, source", [
    ("126511", "tour_api"), ("OY3ba0d", "oliveyoung"), ("DS1", "daiso"),
    ("AB1", "artbox"), ("MS1", "musinsa"), ("XX1", ""), ("", ""), (None, "")])
def test_origin_from_id(cid, source):
    assert _origin_from_id(cid) == source


def _src(**over):
    base = {"contentid": "1", "contenttypeid": "12", "title": "경복궁", "addr1": "서울 종로구",
            "mapx": "126.977", "mapy": "37.579", "lclsSystm1": "HS", "overview": "개요",
            "closed_days": ""}
    base.update(over)
    return base


def test_to_row_keeps_seoul_coordinates_and_drops_empty_raw_values():
    row = to_row(_src(), area_code="1", csv_name="x.csv")
    assert (row["latitude"], row["longitude"]) == (37.579, 126.977)
    assert row["large_class_name"] == LARGE_CLASS_NAMES["HS"]
    assert "closed_days" not in row["raw"] and row["raw"]["overview"] == "개요"
    assert row["raw"]["_load"] == {"origin": "csv", "file": "x.csv", "coord_nulled": False}


def test_to_row_nulls_placeholder_coordinates_but_keeps_the_original_in_raw():
    row = to_row(_src(mapx="117.9925662504", mapy="19.69442748"), area_code="1", csv_name="x.csv")
    assert row["latitude"] is None and row["longitude"] is None
    assert row["raw"]["mapx"] == "117.9925662504"
    assert row["raw"]["_load"]["coord_nulled"] is True


def _write(tmp_path, records):
    path = tmp_path / "a.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    return path


def test_read_rows_separates_sources_and_filters(tmp_path):
    path = _write(tmp_path, [
        _src(contentid="1"),
        _src(contentid="1"),                                   # 중복
        _src(contentid="2", lclsSystm1="FD"),                  # 제외 코드
        _src(contentid="3", title=" "),                        # 이름 없음
        _src(contentid="OY1", lclsSystm1="SH"),                # 다른 출처
    ])
    rows, stats = read_rows(path, area_code="1", exclude_codes={"FD"}, data_source="tour_api")
    assert [r["content_id"] for r in rows] == ["1"]
    assert (stats["skipped_duplicate"], stats["skipped_excluded_code"],
            stats["skipped_no_id_or_title"], stats["skipped_other_source"]) == (1, 1, 1, 1)

    rows, _ = read_rows(path, area_code="1", data_source="oliveyoung")
    assert [r["content_id"] for r in rows] == ["OY1"]


def test_real_csv_loads_cleanly_for_every_source(rows):
    """실제 CSV 를 출처별로 읽었을 때 하나도 버려지지 않고, 출처 합이 전체(제외 코드 뺀)와 같다."""
    excluded = {"FD", "AC", "EV"}
    expected = sum(1 for r in rows if r["lclsSystm1"] not in excluded)
    total = 0
    for source in DEFAULT_SOURCES:
        loaded, stats = read_rows(CSV_PATH, area_code="1", exclude_codes=excluded, data_source=source)
        assert stats["skipped_duplicate"] == 0 and stats["skipped_no_id_or_title"] == 0, source
        assert stats["coord_nulled"] == 0 and stats["unknown_large_class"] == 0, source
        assert loaded, f"{source} 행이 하나도 없다"
        total += len(loaded)
    assert total == expected
