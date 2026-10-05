"""운영자가 고른 비건 식당 목록을 원장에 넣는 SQL 로 바꾼다.

무엇을 넣는가.
    datasets/dining/processed/vegan/ 의 두 파일을 읽는다.
      비건식당_*.csv       식당 목록. 상호·자치구·주소·네이버 플레이스 ID·좌표
      비건식당_근거_*.csv   식당마다 영업을 무엇으로 확인했는가. 인허가 관리번호 또는 운영자 확인

    식당마다 출처 레코드가 둘 붙는다.
      근거 레코드    localdata_food / localdata_rest / operator_check 중 하나.
                     인허가로 확인한 곳은 관리번호를 external_id 로 둔다.
      목록 레코드    vegan_curated. 「이 목록에 비건 식당으로 올라 있다」는 사실 하나.
    비건 속성(vegetarian_menu = yes, 비건)은 목록 레코드를 근거로 건다.
    그래서 속성에 사람 이름을 적지 않아도 입력 제약을 지킨다.

좌표.
    목록의 위도·경도는 인허가 원장의 좌표정보(EPSG:5174, 베셀 TM)를 WGS84 로 바꾼 값이다.
    관광공사 좌표가 있는 기존 138곳으로 맞춰 보면 중앙값 6.7m, 90% 가 17m 안이다(2026-09-23).
    EPSG:2097 로 바꾸면 256m 씩 어긋난다 — 원점 경도 10.405초 차이다.
    좌표출처 칸이 어디서 왔는지를 적는다(localdata_food / localdata_rest / operator_check).

무엇을 넣지 않는가.
    HappyCow 에서 온 내용. 후보 이름을 찾는 데만 썼고 약관상 저장할 수 없다.

이미 있는 식당.
    관광공사로 이미 들어온 곳(발우공양·마지·산촌)은 새로 만들지 않고 목록 레코드와
    속성만 붙인다. 같은 가게가 두 행이 되면 판정이 둘로 갈린다.

영업시간·휴무.
    비건식당_영업시간_검수.csv 에서 확인일이 적힌 행만 operator_check 규칙으로 넣는다.
    이미 있는 식당은 그 요일의 관광공사 규칙을 물러나게 한다(make_operator_sql 과 같은 방식).
    명절·공휴일 휴무는 규칙으로만 남는다. is_closed_on 은 그것을 보지 않는다(023 설계).
    dn_closure_coverage 에 쓰므로 030 마이그레이션이 먼저 있어야 한다.

사용법:  python scripts/dining/make_vegan_sql.py [--dry] [--sheet 시트.csv] [--out 출력.sql]
출력:    datasets/dining/processed/_build/vegan.sql
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DINING_DATA = os.environ.get("DINING_DATA") or os.path.join(  # 데이터는 git 밖(datasets/dining/processed)
    os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "datasets", "dining", "processed")
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(DINING_DATA, "vegan")
OUT = os.path.join(DINING_DATA, "_build")
LIST = os.path.join(DATA, "비건식당_2026-09-23.csv")
EVIDENCE = os.path.join(DATA, "비건식당_근거_2026-09-23.csv")

NS = uuid.UUID("6f1c0d2e-0000-4000-8000-000000000001")
LIST_SOURCE = "vegan_curated"
CHECKED = "2026-09-23"

#: 관광공사로 이미 들어온 곳. place_uid 는 make_load_sql 이 uuid5 로 만든 값이라 재구축해도 같다.
EXISTING = {
    "Balwoo Gongyang - 발우공양": "ea34c618-7c68-5a25-aa82-9f8305f31bee",
    "Maji - 마지": "7645dc81-5846-580f-aca6-67de9069f234",
    "SanChon Korean Temple Cooking - 산촌 사찰음식전문점": "c87f3453-2222-58c6-be3e-9b7efe5272a0",
}

#: 목록의 상호가 영문뿐이거나 한글 부분이 가게 이름이 아닌 곳.
NAME_KO = {
    "Vegan Cafe Dalyang": "비건카페 달냥",
    "MitBord": "밋보어",
    "Ooh Breado (우부래도베지찬 - 동작구청점)": "우부래도",
    "Il Sang - 일상": "일상",
    "Dr. Vegan 닥터비건": "닥터비건",
    "Plant Cafe & Kitchen - Itaewon": "플랜트",
    "Plant Cafe & Kitchen - Yeonnam": "플랜트",
    "Bebab": "비밥",
    "Bium": "비움",
    "Mahina Vegan Table": "마히나 비건 테이블",
    "Pinch Brunch Bar": "핀치 브런치바",
    "Veg Green": "베지그린",
    "Loving Hut Smile - 러빙헛 스마일": "러빙헛 스마일",
    "Loving Hut - Real Love": "러빙헛 리얼러브",
    "Monk's Butcher - Itaewon": "몽크스부처",
    "Sunny Bowl": "써니보울",
    "base is nice": "베이스 이즈 나이스",
    "Cafe Yeorm": "까페여름",
    "Vegenarang": "베지나랑",
    "Nammi Plant Lab": "남미플랜트랩",
    "Sangrokwon Chaesikdang": "상록원 채식당",
    "Cosmos Grocery Cafe": "코스모스상점",
    "ByTOFU": "바이두부",
    "Nono Shop & Cafe Seoul": "노노샵",
    "Saravanaa Bhavan": "사라바나 바반",
    "Vivi's Veggie": "비비스베지",
    "Mananim Recipe": "마나님 레시피",
    "Maru JaYeonSik Kimbap": "마루 자연식 김밥",
    "Soiroum": "소이로움",
    "Gosari Express": "고사리 익스프레스",
    "On The Move": "온더무브",
    "The Buttons": "더버튼스",
    "SanChon Korean Temple Cooking - 산촌 사찰음식전문점": "산촌",
}

#: 지점이 여러 곳인 브랜드. 목록 상호가 「브랜드 - 지점」 꼴이다.
BRANDS = {"Plantude": "플랜튜드", "VEGE STUDIO": "베지스튜디오", "Ferments": "퍼멘츠", "Perlen": "펠른"}
BRANCH_OF = {
    "Plant Cafe & Kitchen - Itaewon": "이태원",
    "Plant Cafe & Kitchen - Yeonnam": "연남",
    "Plantude - Godeok": "고덕",
    "Plantude - I'Park Mall Yongsan": "용산아이파크몰",
}


def q(value) -> str:
    """작은따옴표 문자열. None 은 NULL."""
    if value is None or value == "":
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def jq(obj) -> str:
    """jsonb 리터럴. 달러 인용으로 따옴표 걱정을 없앤다."""
    return "$j$" + json.dumps(obj, ensure_ascii=False) + "$j$::jsonb"


def names(label: str) -> tuple[str, str | None, str | None]:
    """목록 상호를 (한글명, 영문명, 지점명) 으로 나눈다."""
    brand, _, tail = label.partition(" - ")
    if brand in BRANDS:
        return BRANDS[brand], brand, BRANCH_OF.get(label, tail or None)
    # 「Rooted 루티드」처럼 영문과 한글을 붙여 적은 것은 앞의 영문만 떼어 낸다.
    latin = re.match(r"[A-Za-z0-9 .&'å]+", brand)
    english = latin.group(0).strip() if latin and latin.group(0).strip() else None
    if label in NAME_KO:
        return NAME_KO[label], english, BRANCH_OF.get(label)
    korean = " ".join(re.findall(r"[가-힣]+(?:\s+[가-힣]+)*", label.split(" - ")[-1]))
    return korean or label, english, None


def arg(name: str, default: str) -> str:
    """--sheet 경로 처럼 값을 받는 인자. 시험할 때 실제 시트를 건드리지 않으려고 둔다."""
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def full_address(addr: str) -> str:
    """목록은 「서울 …」로 줄여 적었다. 원장은 「서울특별시 …」로 적는다."""
    return re.sub(r"^서울\s", "서울특별시 ", addr.strip())


# ── 영업시간·휴무 검수 시트 ───────────────────────────────────────────────
#
# 운영자가 네이버 등에서 보고 요일별 칸에 적는다. 자유 서술을 받지 않는 까닭은
# parse_hours 가 관광공사 문장에 맞춰져 있어 네이버식 표기(브레이크가 섞인 줄,
# 「월 휴무」)를 틀리게 읽었기 때문이다(2026-09-23 시험).
#
#   요일 칸   11:30-20:00  /  11:30-15:00, 17:00-20:00 (브레이크는 구간 둘)
#             휴무  /  모름  /  빈칸(아직 안 봄 — 적재하지 않는다)
#   라스트오더  20:30  /  마감 30분 전  /  없음  /  모름 · 빈칸
#             점심 14:00, 저녁 20:30  (구간마다)  /  20:00(일요일 13:00)  (요일 예외)
#   정기휴무 외  매월 둘째 주 화요일 · 명절 당일 · 공휴일 · 연중무휴 · 없음
#
# 확인일이 적힌 행만 넣는다. 확인일이 없으면 아직 보지 않은 것이다.

SHEET = os.path.join(DATA, "비건식당_영업시간_검수.csv")
DAYS = ["월", "화", "수", "목", "금", "토", "일"]
RE_SPAN = re.compile(r"^(\d{1,2}):(\d{2})\s*[-~]\s*(\d{1,2}):(\d{2})$")
RE_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
RE_BEFORE = re.compile(r"마감\s*(\d+)\s*분\s*전")


def parse_day(cell: str) -> tuple[str, list[tuple[int, int]]]:
    """요일 칸 하나 → (coverage, 구간들). 빈칸은 ('', [])."""
    cell = cell.strip()
    if not cell:
        return "", []
    if cell == "휴무":
        return "closed", []
    if cell == "모름":
        return "unknown", []
    spans = []
    for part in re.split(r"\s*,\s*", cell):
        m = RE_SPAN.match(part)
        if not m:
            raise ValueError(f"요일 칸을 읽지 못함: {cell!r}")
        h1, m1, h2, m2 = map(int, m.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if end <= start:            # 02:00 처럼 적어도 자정 넘김으로 본다
            end += 1440
        spans.append((start, end))
    spans.sort()
    for a, b in zip(spans, spans[1:]):
        if b[0] < a[1]:
            raise ValueError(f"구간이 겹친다: {cell!r}")
    return "intervals", spans


RE_TIME = re.compile(r"(조식|점심|런치|저녁|디너)?\s*(\d{1,2}):(\d{2})")
RE_DAY_EXCEPTION = re.compile(r"\(([^)]*)\)")


def _times(text: str) -> list[int]:
    """「점심 14:00, 저녁 8:40」 → 분. 저녁 · 디너에 12시 전 숫자를 적었으면 오후로 본다."""
    out = []
    for label, hh, mm in RE_TIME.findall(text):
        minute = int(hh) * 60 + int(mm)
        if label in ("저녁", "디너") and int(hh) < 12:
            minute += 720
        out.append(minute)
    return out


def _exception_days(inner: str) -> set[int]:
    """괄호 안 「토, 일」 「일요일」 → 요일 번호(월=1)."""
    return {DAYS.index(ch) + 1 for ch in re.sub(r"\d{1,2}:\d{2}|요일", "", inner) if ch in DAYS}


def parse_last_order(cell: str, spans: list[tuple[int, int]],
                     weekday: int | None = None) -> list[tuple[str, int | None]]:
    """라스트오더 칸 → 구간마다 (상태, 분). 구간 수만큼 돌려준다.

        빈칸 · 모름              모든 구간 모름
        없음 · 마감 N분 전 · 20:30   마지막 구간에만 붙인다. 앞 구간은 모름
        점심 14:00, 저녁 20:30   시각이 들어가는 구간에 붙인다. 들어갈 구간이 없는 구간은 모름
        20:00(일요일 13:00)      괄호의 요일은 괄호 안 시각을 쓴다
    """
    cell = cell.strip()
    unknown = [("unknown", None)] * len(spans)
    if not cell or cell == "모름":
        return unknown
    if cell == "없음":
        return unknown[:-1] + [("none", None)]
    m = RE_BEFORE.search(cell)
    if m:
        return unknown[:-1] + [("present", spans[-1][1] - int(m.group(1)))]
    m = RE_HHMM.match(cell)
    if m:
        lo = int(m.group(1)) * 60 + int(m.group(2))
        if lo < spans[-1][0]:       # 00:30 처럼 적은 자정 넘김
            lo += 1440
        if not spans[-1][0] <= lo <= spans[-1][1]:
            raise ValueError(f"라스트오더가 구간 밖: {cell!r}")
        return unknown[:-1] + [("present", lo)]

    times = _times(RE_DAY_EXCEPTION.sub("", cell))
    for inner in RE_DAY_EXCEPTION.findall(cell):
        if weekday in _exception_days(inner):
            times = _times(inner)
    if not times:
        raise ValueError(f"라스트오더를 읽지 못함: {cell!r}")
    out = []
    for start, end in spans:
        hit = next((t for t in times for t in (t, t + 1440) if start <= t <= end), None)
        out.append(("present", hit) if hit is not None else ("unknown", None))
    return out


def parse_extra_closure(cell: str) -> tuple[str, list[dict]]:
    """정기휴무 외 칸 → (none|present|unknown|'', 규칙들).

    「공휴일」은 parse_closure 에 넘기지 않는다. '일' 자에 걸려 매주 일요일로 읽힌다.
    """
    cell = cell.strip()
    if not cell:
        return "", []
    if cell in ("없음", "연중무휴"):
        return "none", []
    if cell == "모름":
        return "unknown", []
    rules: list[dict] = []
    rest = []
    for part in re.split(r"\s*/\s*", cell):
        if part in ("공휴일", "법정공휴일"):
            rules.append({"pattern_kind": "public_holiday"})
        else:
            rest.append(part)
    if rest:
        sys.path.insert(0, HERE)
        from parse_hours import parse_closure  # 같은 폴더의 관광공사 파서를 그대로 쓴다
        got, notes = parse_closure(" / ".join(rest))
        if not got:
            raise ValueError(f"정기휴무 외를 읽지 못함: {cell!r} {notes}")
        rules += got
    return "present", rules


def hours_sql(place_of: dict[str, str], existing: set[str], sheet: str, *, tag: str = "vegan",
              scope: str = "서울 비건 식당 영업시간 검수",
              folder: str = "vegan", place_records: dict[str, str] | None = None) -> tuple[list[str], dict]:
    """검수 시트 → 영업·휴무 규칙 SQL. 두 번째 값은 센 수.

    같은 형식의 시트(미쉐린 등)도 이것으로 넣는다. tag 로 id 와 적재 묶음을 가른다 —
    한 시트를 다시 넣을 때 다른 시트의 규칙을 지우지 않게.
    place_records를 주면 해당 출처 행에서 SQL이 확정한 장소를 참조한다.
    관광공사·비건 등 기존 출처를 Python에서 다시 매칭하지 않는다.
    이 모드의 검수 값은 적힌 요일·정기휴무 외 항목의 기존 수동 규칙도 교체한다.
    빈 항목에서는 다른 검수 시트의 활성 규칙을 물러나게 하지 않는다.
    """
    load_id = str(uuid.uuid5(NS, f"load:{tag}-hours:operator_check"))
    body = [
        "-- 검수 시트가 바뀌면 바뀐 대로 따라가야 한다. 이 적재가 만든 것만 지우고 다시 넣는다.",
        f"DELETE FROM dining.dn_hours_rule   WHERE record_id IN (SELECT record_id FROM dining.dn_source_record WHERE load_id = '{load_id}');",
        f"DELETE FROM dining.dn_closure_rule WHERE record_id IN (SELECT record_id FROM dining.dn_source_record WHERE load_id = '{load_id}');",
        f"DELETE FROM dining.dn_closure_coverage WHERE record_id IN (SELECT record_id FROM dining.dn_source_record WHERE load_id = '{load_id}');",
        f"DELETE FROM dining.dn_source_record WHERE load_id = '{load_id}';",
    ]
    n = {"식당": 0, "영업 규칙": 0, "구간": 0, "휴무 규칙": 0, "물러난 관광공사 규칙": 0}
    for row in csv.DictReader(open(sheet, encoding="utf-8-sig")):
        label, checked = row["상호(목록)"].strip(), row["확인일"].strip()
        if row["번호"] == "예시" or not checked:
            continue
        if label not in place_of:
            raise ValueError(f"목록에 없는 식당: {label!r}")
        place_uid = place_of[label]
        record = (place_records or {}).get(label)
        place_sql = (f"(SELECT place_uid FROM dining.dn_source_record WHERE record_id = {q(record)})"
                     if record else q(place_uid))
        days = [parse_day(row[d]) for d in DAYS]
        extra_state, extra_rules = parse_extra_closure(row["정기휴무 외"])

        rec_id = str(uuid.uuid5(NS, f"record:{tag}-hours:{label}:{checked}"))
        raw = {k: row[k] for k in DAYS + ["라스트오더", "정기휴무 외", "확인일", "메모"]}
        body.append(
            "INSERT INTO dining.dn_source_record (record_id, load_id, source_code, external_id, "
            "place_uid, match_status, raw_json) VALUES ("
            f"'{rec_id}', '{load_id}', 'operator_check', {q('hours:' + label)}, {place_sql}, "
            f"'confirmed', {jq(raw)}) ON CONFLICT (record_id) DO NOTHING;")
        n["식당"] += 1
        text = " / ".join(f"{d} {row[d].strip()}" for d in DAYS if row[d].strip())

        for weekday, (coverage, spans) in enumerate(days, 1):
            if not coverage:
                continue
            if place_uid in existing or record:
                # 기존 출처의 규칙을 물러나게 한다. 새 장소라면 갱신할 기존 규칙이 없다.
                exclude_manual = "" if record else "AND source_code <> 'operator_check' "
                body.append(
                    "UPDATE dining.dn_hours_rule SET retired_at = now() "
                    f"WHERE place_uid = {place_sql} AND rule_kind = 'weekly' AND weekday = {weekday} "
                    f"{exclude_manual}AND retired_at IS NULL;")
                n["물러난 관광공사 규칙"] += 1
            if record:
                # 새 시트가 여는 날이라고 확인했는데 이전 시트의 주간 휴무가 남으면 모순이다.
                body.append(
                    "UPDATE dining.dn_closure_rule SET retired_at = now() "
                    f"WHERE place_uid = {place_sql} AND pattern_kind = 'weekly' AND weekday = {weekday} "
                    "AND retired_at IS NULL;")
            rule_id = str(uuid.uuid5(NS, f"rule:{tag}-hours:{label}:{weekday}"))
            brk = ("present" if len(spans) > 1 else "none") if coverage == "intervals" else "unknown"
            body.append(
                "INSERT INTO dining.dn_hours_rule (rule_id, place_uid, source_code, record_id, rule_kind, "
                "weekday, coverage, break_state, source_text, extract_method, rules_version, valid_from) "
                f"VALUES ('{rule_id}', {place_sql}, 'operator_check', '{rec_id}', 'weekly', {weekday}, "
                f"'{coverage}', '{brk}', {q(text[:900])}, 'manual', '{tag}-sheet-v1', '{checked}');")
            n["영업 규칙"] += 1
            if coverage != "intervals":
                continue
            try:
                orders = parse_last_order(row["라스트오더"], spans, weekday)
            except ValueError as err:
                raise ValueError(f"{label} {DAYS[weekday - 1]}: {err}") from None
            for seq, ((start, end), (state, lo)) in enumerate(zip(spans, orders, strict=True), 1):
                body.append(
                    "INSERT INTO dining.dn_hours_interval (rule_id, seq, open_min, close_min, "
                    f"last_order_min, last_order_state) VALUES ('{rule_id}', {seq}, {start}, {end}, "
                    f"{lo if lo is not None else 'NULL'}, '{state}');")
                n["구간"] += 1

        if record and extra_state:
            # 정기휴무 외 칸도 새 검수 값으로 교체한다. 빈칸이면 이전 지식을 유지한다.
            body.append(
                "UPDATE dining.dn_closure_rule SET retired_at = now() "
                f"WHERE place_uid = {place_sql} AND pattern_kind <> 'weekly' AND retired_at IS NULL;")
        # 휴무 — 요일 칸의 「휴무」는 매주 쉬는 날이다. is_closed_on 은 휴무 규칙을 본다.
        closures = [{"pattern_kind": "weekly", "weekday": wd}
                    for wd, (cov, _) in enumerate(days, 1) if cov == "closed"] + extra_rules
        for idx, c in enumerate(closures, 1):
            nth = c.get("nth")
            nth_sql = ("ARRAY[" + ",".join(map(str, nth)) + "]::smallint[]") if nth else "NULL"
            closure_id = str(uuid.uuid5(NS, f"closure:{tag}-hours:{label}:{idx}"))
            body.append(
                "INSERT INTO dining.dn_closure_rule (closure_id, place_uid, source_code, record_id, "
                "pattern_kind, weekday, nth, holiday_name, holiday_scope, closed_date, source_text, "
                "extract_method, valid_from) VALUES ("
                f"'{closure_id}', {place_sql}, 'operator_check', '{rec_id}', '{c['pattern_kind']}', "
                f"{c.get('weekday') or 'NULL'}, {nth_sql}, {q(c.get('holiday_name'))}, "
                f"{q(c.get('holiday_scope'))}, {q(c.get('closed_date'))}, "
                f"{q(row['정기휴무 외'].strip() or None)}, 'manual', '{checked}');")
            n["휴무 규칙"] += 1

        # 휴무를 어디까지 아는가. 일곱 요일을 다 보고 정기휴무 외까지 적어야 「안 쉰다」다.
        all_seen = all(cov in ("intervals", "closed") for cov, _ in days)
        if closures:
            state = "present"
        elif all_seen and extra_state == "none":
            state = "none"
        else:
            state = "unknown"
        preserve_known = (
            " WHERE dining.dn_closure_coverage.retired_at IS NOT NULL"
            " OR dining.dn_closure_coverage.state = 'unknown' OR EXCLUDED.state <> 'unknown'"
            if record and not extra_state else "")
        body.append(
            "INSERT INTO dining.dn_closure_coverage (place_uid, source_code, record_id, state, "
            f"source_text, valid_from) VALUES ({place_sql}, 'operator_check', '{rec_id}', "
            f"'{state}', {q(row['정기휴무 외'].strip() or None)}, '{checked}')"
            " ON CONFLICT (place_uid, source_code) DO UPDATE SET record_id = EXCLUDED.record_id,"
            " state = EXCLUDED.state, source_text = EXCLUDED.source_text, valid_from = EXCLUDED.valid_from,"
            f" retired_at = NULL{preserve_known};")

    head = ("INSERT INTO dining.dn_load_meta (load_id, source_code, fetched_at, schema_version, scope, "
            f"row_count, raw_uri, status) VALUES ('{load_id}', 'operator_check', now(), '{tag}-sheet-v1', "
            f"{q(scope)}, {n['식당']}, "
            f"{q(f'datasets/dining/processed/{folder}/' + os.path.basename(sheet))}, 'loaded')"
            " ON CONFLICT (load_id) DO UPDATE SET row_count = EXCLUDED.row_count, fetched_at = now();")
    # load_meta 가 먼저 있어야 source_record 가 붙는다. DELETE 보다 앞에 둔다.
    return [head] + body, n


def main() -> None:
    rows = list(csv.DictReader(open(LIST, encoding="utf-8-sig")))
    evidence = {r["상호"]: r for r in csv.DictReader(open(EVIDENCE, encoding="utf-8-sig"))}
    missing = [r["상호"] for r in rows if r["상호"] not in evidence]
    if missing:
        sys.exit(f"근거가 없는 식당이 있다: {missing}")

    loads: dict[str, str] = {}
    by_source: dict[str, int] = {}
    body: list[str] = []

    def load_of(source: str) -> str:
        if source not in loads:
            loads[source] = str(uuid.uuid5(NS, f"load:vegan:{source}:{CHECKED}"))
        by_source[source] = by_source.get(source, 0) + 1
        return loads[source]

    n_new = 0
    place_of: dict[str, str] = {}
    for row in rows:
        label = row["상호"]
        ev = evidence[label]
        ko, en, branch = names(label)

        place_uid = EXISTING.get(label) or str(uuid.uuid5(NS, f"place:vegan:{ev['external_id']}"))
        place_of[label] = place_uid
        if label not in EXISTING:
            lat, lng = row["위도"] or None, row["경도"] or None
            body.append(
                "INSERT INTO dining.dn_place (place_uid, name_ko, name_en, branch_name, road_address, "
                "lat, lng, coord_source, area, hub, phone, record_status) VALUES ("
                f"'{place_uid}', {q(ko)}, {q(en)}, {q(branch)}, {q(full_address(row['주소']))}, "
                f"{lat or 'NULL'}, {lng or 'NULL'}, {q(row.get('좌표출처') if lat else None)}, "
                f"{q(row['자치구'])}, NULL, {q(ev['전화'])}, 'active')"
                # 좌표는 나중에 채워질 수 있다. 목록이 이 행의 정본이므로 다시 돌리면 좌표를 따라간다.
                " ON CONFLICT (place_uid) DO UPDATE SET lat = EXCLUDED.lat, lng = EXCLUDED.lng,"
                " coord_source = EXCLUDED.coord_source, updated_at = now()"
                " WHERE dining.dn_place.lat IS DISTINCT FROM EXCLUDED.lat"
                " OR dining.dn_place.lng IS DISTINCT FROM EXCLUDED.lng;")
            n_new += 1

        # 근거 레코드 — 영업을 무엇으로 확인했는가
        src = ev["source"]
        raw = ({"확인": "운영자", "확인일": CHECKED, "네이버플레이스ID": row["네이버플레이스ID"] or None}
               if src == "operator_check" else
               {"원장상호": ev["원장상호"], "원장갱신일": ev["원장갱신일"], "대조일": CHECKED})
        rec_id = str(uuid.uuid5(NS, f"record:vegan:{src}:{ev['external_id']}"))
        body.append(
            "INSERT INTO dining.dn_source_record (record_id, load_id, source_code, external_id, "
            "place_uid, match_status, match_basis, raw_json) VALUES ("
            f"'{rec_id}', '{load_of(src)}', '{src}', {q(ev['external_id'])}, '{place_uid}', "
            f"'{'confirmed' if src == 'operator_check' else 'auto'}', "
            f"{jq({'기준': '운영자 확인' if src == 'operator_check' else '도로명주소 + 상호'})}, {jq(raw)})"
            " ON CONFLICT (record_id) DO NOTHING;")

        # 목록 레코드 — 비건 식당 목록에 올라 있다
        list_id = str(uuid.uuid5(NS, f"record:vegan:list:{label}"))
        body.append(
            "INSERT INTO dining.dn_source_record (record_id, load_id, source_code, external_id, "
            "place_uid, match_status, raw_json) VALUES ("
            f"'{list_id}', '{load_of(LIST_SOURCE)}', '{LIST_SOURCE}', {q(label)}, '{place_uid}', "
            f"'confirmed', {jq({'목록': os.path.basename(LIST), '상호': label})})"
            " ON CONFLICT (record_id) DO NOTHING;")

        attr_id = str(uuid.uuid5(NS, f"attr:vegan:{label}"))
        body.append(
            "INSERT INTO dining.dn_attribute (attr_id, place_uid, source_code, record_id, attr_code, "
            "value_state, value_detail, extract_method, valid_from) VALUES ("
            f"'{attr_id}', '{place_uid}', '{LIST_SOURCE}', '{list_id}', 'vegetarian_menu', "
            f"'yes', '비건', 'manual', '{CHECKED}')"
            " ON CONFLICT (attr_id) DO NOTHING;")

    head = ["-- make_vegan_sql.py 결과. 생성 파일이므로 직접 고치지 않는다.", "BEGIN;", ""]
    for source, load_id in loads.items():
        head.append(
            "INSERT INTO dining.dn_load_meta (load_id, source_code, fetched_at, schema_version, "
            "scope, row_count, raw_uri, status) VALUES ("
            f"'{load_id}', '{source}', '{CHECKED} 12:00+09', 'vegan-list-v1', '서울 비건 식당 목록', "
            f"{by_source[source]}, {q('datasets/dining/processed/vegan/' + os.path.basename(LIST))}, 'loaded')"
            " ON CONFLICT (load_id) DO NOTHING;")
    sheet = arg("--sheet", SHEET)
    hours, counted = hours_sql(place_of, set(EXISTING.values()), sheet)
    sql = "\n".join(head + [""] + body + ["", "-- 영업시간·휴무 (검수 시트)"] + hours + ["", "COMMIT;", ""])

    print(f"식당 {len(rows)} (새로 {n_new}, 기존 {len(rows) - n_new}) / 근거 {dict(sorted(by_source.items()))}")
    print(f"영업시간 검수 {os.path.basename(sheet)}: {counted}")
    if "--dry" in sys.argv:
        return
    os.makedirs(OUT, exist_ok=True)
    path = arg("--out", os.path.join(OUT, "vegan.sql"))
    open(path, "w", encoding="utf-8").write(sql)
    print(f"→ {path}")


if __name__ == "__main__":
    main()
