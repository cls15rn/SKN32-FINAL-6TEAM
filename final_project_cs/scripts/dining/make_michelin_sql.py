"""미쉐린 가이드 서울 목록을 원장의 michelin 속성으로 넣는 SQL 을 만든다.

무엇을 읽는가.
    datasets/dining/processed/michelin/미쉐린_서울_2026.csv  (상호, 등급) 180곳
    guide.michelin.com 의 서울 목록(별 · 빕 구르망 · 셀렉티드)에서 이름과 등급만 옮겼다.

어떻게 잇는가.
    1  상호가 같다(띄어쓰기·괄호·기호를 뗀 뒤). 한 곳만 걸릴 때만 잇는다.
    2  ALIAS 에 적은 곳. 원장 상호가 달라 1 에서 안 걸리지만, 가이드 페이지의 주소와
       원장 주소를 사람이 맞춰 본 곳이다.
    이름만 비슷한 곳은 잇지 않는다. 「오레노 라멘」은 원장에 「오레노라멘 본점」이 있지만
    가이드 주소(독막로8길 16)와 달라 뺐다.

무엇을 넣는가.
    잇는 가게마다 출처 레코드(michelin_guide) 하나와 속성 michelin = yes 하나.
    상세는 「1스타 (2026)」처럼 등급과 에디션. 가이드에 없는 곳은 행을 만들지 않는다.

상세 자료가 있는 145곳 중 원장에 없는 가게는 새로 만든다.
    datasets/dining/processed/michelin/미쉐린_서울_2026_가게.jsonl — 가이드 가게 페이지에서 옮긴 사실만
    (주소 · 우편번호 · 좌표 · 전화 · 요리 종류 · 가격대 · 편의시설 · 가족 동반). 소개 글은 없다.
    - 가게: 이름 · 도로명주소 · 좌표(coord_source=michelin_guide) · 자치구 · 전화. 권역(hub)은 비운다.
    - 대표 분류: 가이드의 요리 종류로 정한다(category_method=manual — 규칙이 덮지 않게).
    - 속성: 카드(신용카드 사용 가능 → yes · 현금만 가능 → no), 주차(주차장 → yes · 발렛만 → limited),
            아이 동반(가이드가 「가족 모두 즐길 수 있는」으로 표시 → yes). 표시가 없으면 넣지 않는다(모름).
    - 영업시간은 가이드에서 옮기지 않는다. 가이드 페이지를 요약 도구로만 읽을 수 있었는데 점심·저녁이
      섞이거나 빠졌다(2026-09-28 확인). 대신 검수 시트(미쉐린_영업시간_검수.csv, 비건 시트와 같은 형식)에
      사람이 적은 행만 넣는다(make_vegan_sql.hours_sql). 채우기 전까지 이 가게들의 영업 판정은 「모름」이다.
    - 검수 행의 장소는 SQL이 확정한 미쉐린 출처 행에서 읽는다. 다른 출처와도 연결 기준이 같다.

사용법:  python scripts/dining/make_michelin_sql.py [--dry]
출력:    datasets/dining/processed/_build/michelin.sql
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DINING_DATA = os.environ.get("DINING_DATA") or os.path.join(  # 데이터는 git 밖(datasets/dining/processed)
    os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "datasets", "dining", "processed")
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = DINING_DATA
OUT = os.path.join(DATA, "_build")
LIST = os.path.join(DATA, "michelin", "미쉐린_서울_2026.csv")
FACTS = os.path.join(DATA, "michelin", "미쉐린_서울_2026_가게.jsonl")
HOURS_SHEET = os.path.join(DATA, "michelin", "미쉐린_영업시간_검수.csv")

NS = uuid.UUID("6f1c0d2e-0000-4000-8000-000000000004")
SOURCE = "michelin_guide"
EDITION = "2026"
LOADED = "2026-09-28"

#: 가이드 상호 → 원장 상호. 가이드 페이지 주소와 원장 주소가 같은 것만 적는다(2026-09-28).
ALIAS = {
    "무오키": "무오키(MUOKI)",          # 강남구 학동로55길 12-12
    "버드나무집": "버드나무집 본점",     # 서초구 효령로 434
    "봉밀가": "봉밀가 강남구청점",       # 강남구 선릉로 664
}


def norm(name: str | None) -> str:
    """한글·영문·숫자만 남긴다. SQL 쪽 regexp_replace 와 같은 규칙이다."""
    return re.sub(r"[^가-힣a-z0-9]", "", (name or "").lower())


#: 가이드 좌표와 이 거리 안이어야 같은 가게로 본다. 구글 자동 일괄 확정과 같은 기준이다.
#: 「소울」은 가이드가 용산구 신흥로, 관광공사가 종로구 자하문로 — 이름만 같고 4km 떨어진 다른 가게다.
SAME_PLACE_M = 50


#: 가이드 요리 종류 → 원장 대표 분류(034). 앞에서부터 처음 걸리는 것. 안 걸리면 기타.
CATEGORY_RULES = [
    ("한식", ("한식", "곰탕", "설렁탕", "냉면", "칼국수", "국수", "만두", "게장", "국밥", "도가니",
              "삼계탕", "두부", "메밀", "바비큐")),
    ("중식", ("중식", "딤섬")),
    ("일식", ("일식", "스시", "소바", "라멘", "야키토리", "쿠시아게", "재패니즈")),
    ("양식", ("프렌치", "이탤리언", "컨템퍼러리", "모던", "이노베이티브", "지중해", "스칸디나비안")),
]


def category_of(cuisine: str | None) -> str:
    text = cuisine or ""
    for category, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return category
    return "기타"


def phone_of(phone: str | None) -> str | None:
    """+82 2-2230-3367 → 02-2230-3367. 원장의 전화 표기에 맞춘다."""
    if not phone:
        return None
    return re.sub(r"^\+82[\s-]*", "0", phone.strip())


def area_of(address: str) -> str | None:
    head = address.split()[0] if address.split() else ""
    return head if head.endswith("구") else None


def facts_attributes(fact: dict) -> list[tuple[str, str, str]]:
    """(속성 코드, 값 상태, 상세). 가이드에 표시가 있는 것만."""
    facilities = set(fact.get("편의시설") or [])
    out = []
    if "현금만 가능" in facilities:
        out.append(("card_payment", "no", "현금만 가능"))
    elif "신용카드 사용 가능" in facilities:
        out.append(("card_payment", "yes", None))
    if "주차장" in facilities:
        out.append(("parking", "yes", "발렛파킹" if "발렛파킹" in facilities else None))
    elif "발렛파킹" in facilities:
        out.append(("parking", "limited", "발렛파킹만"))
    if fact.get("가족") is True:
        out.append(("kids_allowed", "yes", "미쉐린: 가족 모두 즐길 수 있는"))
    return out


def q(value) -> str:
    if value is None or str(value) == "":
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def main() -> None:
    rows = list(csv.DictReader(open(LIST, encoding="utf-8")))
    facts = {}
    if os.path.exists(FACTS):
        facts = {f["상호"]: f for f in (json.loads(line) for line in open(FACTS, encoding="utf-8"))}
    place_of: dict[str, str] = {}
    place_records: dict[str, str] = {}  # 검수 행도 SQL이 확정한 미쉐린 출처 행의 장소를 쓴다.
    load_id = str(uuid.uuid5(NS, f"load:michelin:{EDITION}"))
    lines = ["-- make_michelin_sql.py 결과. 생성 파일이므로 직접 고치지 않는다.",
             "BEGIN;", "",
             "-- 같은 에디션을 다시 넣어도 쌓이지 않게 이 출처의 것을 먼저 비운다.",
             f"DELETE FROM dining.dn_attribute WHERE source_code = '{SOURCE}';",
             f"DELETE FROM dining.dn_source_record WHERE source_code = '{SOURCE}';",
             f"DELETE FROM dining.dn_load_meta WHERE source_code = '{SOURCE}';", "",
             "INSERT INTO dining.dn_load_meta (load_id, source_code, fetched_at, schema_version, scope, "
             f"row_count, raw_uri, status) VALUES ('{load_id}', '{SOURCE}', '{LOADED} 12:00+09', "
             f"'michelin-{EDITION}', '미쉐린 가이드 서울 {EDITION}', {len(rows)}, "
             f"'datasets/dining/processed/michelin/{os.path.basename(LIST)}', 'loaded');", ""]
    for row in rows:
        name, grade = row["상호"].strip(), row["등급"].strip()
        target = ALIAS.get(name)
        # 원장 상호로 찾는다. 같은 이름이 둘 이상이면 잇지 않는다(어느 지점인지 모른다).
        match = (f"(SELECT place_uid FROM dining.dn_place WHERE name_ko = {q(target)})" if target else
                 "(SELECT min(place_uid::text)::uuid FROM dining.dn_place "
                 "WHERE regexp_replace(lower(name_ko), '[^가-힣a-z0-9]', '', 'g') = "
                 f"{q(norm(name))} HAVING count(*) = 1)")
        fact = facts.get(name)
        if fact:
            # 원장에 없던 가게 — 새로 만든다. 이미 같은 이름이 하나 있고 가이드 좌표와 SAME_PLACE_M 안이면
            # 그 가게를 쓴다. 이름만 같고 멀리 있는 가게는 다른 가게다.
            match = ("(SELECT min(place_uid::text)::uuid FROM dining.dn_place "
                     "WHERE regexp_replace(lower(name_ko), '[^가-힣a-z0-9]', '', 'g') = "
                     f"{q(norm(name))} AND lat IS NOT NULL "
                     f"AND dining.distance_m(lat, lng, {fact['위도']}, {fact['경도']}) <= {SAME_PLACE_M} "
                     "HAVING count(*) = 1)")
            new_uid = str(uuid.uuid5(NS, f"place:michelin:{fact['url'].rsplit('/', 1)[-1]}"))
            lines.append(
                "INSERT INTO dining.dn_place (place_uid, name_ko, road_address, lat, lng, coord_source, "
                "area, phone, record_status, category, category_method) "
                f"SELECT '{new_uid}', {q(name)}, {q('서울특별시 ' + fact['주소'])}, {fact['위도']}, {fact['경도']}, "
                f"'{SOURCE}', {q(area_of(fact['주소']))}, {q(phone_of(fact.get('전화')))}, 'unknown', "
                f"{q(category_of(fact.get('요리')))}, 'manual' "
                f"WHERE {match} IS NULL ON CONFLICT (place_uid) DO NOTHING;")
            match = f"coalesce({match}, '{new_uid}'::uuid)"
            place_of[name] = new_uid
        rec_id = str(uuid.uuid5(NS, f"record:michelin:{EDITION}:{name}"))
        if fact:
            place_records[name] = rec_id
        attr_id = str(uuid.uuid5(NS, f"attr:michelin:{EDITION}:{name}"))
        raw = {"상호": name, "등급": grade, "에디션": EDITION,
               **({k: fact[k] for k in ("url", "주소", "우편번호", "전화", "요리", "가격대", "편의시설", "가족")}
                  if fact else {})}
        lines.append(
            "INSERT INTO dining.dn_source_record (record_id, load_id, source_code, external_id, "
            f"place_uid, match_status, match_basis, raw_json) SELECT '{rec_id}', '{load_id}', '{SOURCE}', "
            f"{q(name)}, m.uid, 'confirmed', "
            f"$j${json.dumps({'기준': '주소 대조' if target else '상호 일치'}, ensure_ascii=False)}$j$::jsonb, "
            f"$j${json.dumps(raw, ensure_ascii=False)}$j$::jsonb "
            f"FROM (SELECT {match} AS uid) m WHERE m.uid IS NOT NULL;")
        lines.append(
            "INSERT INTO dining.dn_attribute (attr_id, place_uid, source_code, record_id, attr_code, "
            "value_state, value_detail, source_text, extract_method, valid_from) "
            f"SELECT '{attr_id}', sr.place_uid, '{SOURCE}', sr.record_id, 'michelin', 'yes', "
            f"{q(f'{grade} ({EDITION})')}, {q(f'미쉐린 가이드 서울 {EDITION} · {grade}')}, 'manual', '{LOADED}' "
            f"FROM dining.dn_source_record sr WHERE sr.record_id = '{rec_id}';")
        if fact:
            # 편의시설 속성은 우리가 만든 가게에만 — 원래 있던 가게의 관광공사 속성을 덮지 않는다.
            for code, state, detail in facts_attributes(fact):
                aid = str(uuid.uuid5(NS, f"attr:michelin:{EDITION}:{name}:{code}"))
                lines.append(
                    "INSERT INTO dining.dn_attribute (attr_id, place_uid, source_code, record_id, attr_code, "
                    "value_state, value_detail, source_text, extract_method, valid_from) "
                    f"SELECT '{aid}', sr.place_uid, '{SOURCE}', sr.record_id, '{code}', '{state}', {q(detail)}, "
                    f"{q('미쉐린 가이드 가게 페이지')}, 'manual', '{LOADED}' "
                    f"FROM dining.dn_source_record sr WHERE sr.record_id = '{rec_id}' "
                    f"AND sr.place_uid = '{new_uid}';")
    counted = {}
    if os.path.exists(HOURS_SHEET):
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_vegan_sql", os.path.join(HERE, "make_vegan_sql.py"))
        vegan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vegan)
        hours, counted = vegan.hours_sql(place_of, set(), HOURS_SHEET, tag="michelin",
                                         scope="미쉐린 가게 영업시간 검수", folder="michelin",
                                         place_records=place_records)
        lines += ["", "-- 영업시간·휴무 (검수 시트)"] + hours
    lines += ["", "COMMIT;", ""]
    print(f"영업시간 검수 {os.path.basename(HOURS_SHEET)}: {counted}")
    print(f"미쉐린 가이드 서울 {EDITION}: {len(rows)}곳 (주소로 맞춘 별칭 {len(ALIAS)}곳, "
          f"상세 자료 {len(place_of)}곳 — 기존 장소 연결·생성은 SQL에서 판정)")
    if "--dry" in sys.argv:
        return
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "michelin.sql")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print(f"→ {path}")


if __name__ == "__main__":
    main()
