# -*- coding: utf-8 -*-
"""activity_total_data.csv 의 빈 상세(overview·business_hours·closed_days·fee)를 TourAPI 로 채운다.

사용(키를 가진 사람이 **자기 PC 에서** 실행한다. 최종 실행 위치: final_project_cs):
    python -m app.modules.travel_ops.activity.data_processing.fill_tourapi_details --dry-run
    # 쇼핑(38)을 빼고 나머지만: ... fill_tourapi_details --types 12 14 28 15 --max-calls 900
    python -m app.modules.travel_ops.activity.data_processing.fill_tourapi_details --max-calls 2
    python -m app.modules.travel_ops.activity.data_processing.fill_tourapi_details --max-calls 900
    # 하루 한도에 걸려 멈추면 다음 날 같은 명령을 다시 돌린다(캐시에 있는 건 부르지 않는다)

★대상은 TourAPI 행(contentid 가 숫자) 중 **네 칸이 모두 빈 행**이다. 이미 값이 있는 칸은 덮어쓰지 않는다.
  장소마다 detailCommon2(개요)·detailIntro2(영업시간·휴무·요금) 두 번씩 부른다.
★순서는 사람이 볼 가치가 큰 타입부터다 — 관광지(12)·문화시설(14)·레포츠(28)·행사(15) 다음에
  쇼핑(38, 대부분 체인 매장). 한도가 모자라면 뒤쪽이 남는다.
★키·호출·캐시·멈춤 규칙은 `tourapi_client.py` 와 같다(그 함수를 가져다 쓴다) — 오류·한도 초과는
  「없음」으로 굳히지 않고 그 자리에서 멈추며, 받은 것까지는 CSV 에 반영한다.
★타입별 필드(원문 그대로, 파싱하지 않는다):
    12 관광지    business_hours ← usetime          closed_days ← restdate           fee ← (없음)
    14 문화시설  business_hours ← usetimeculture   closed_days ← restdateculture    fee ← usefee
    15 행사      business_hours ← playtime         closed_days ← (없음)             fee ← usetimefestival
    28 레포츠    business_hours ← usetimeleports   closed_days ← restdateleports    fee ← usefeeleports
    38 쇼핑      business_hours ← opentime         closed_days ← restdateshopping   fee ← (없음)

★`[2026-10-01]` 인원·예약 확인용 컬럼 6개를 같이 받는다 — **`detailIntro2` 응답에서 꺼낼 수 있는 것만**(`detailCommon2` 의 homepage 는 호출이 늘어 제외) — 타입마다 필드 이름이 달라서 **컬럼 이름은 하나로 통일**했다.
  원문 그대로 옮기고 파싱하지 않는다. 필드 이름은 실제 응답으로 확인했다(12·14 는 캐시, 15·28·38 은 1건씩 호출).
  같은 뜻의 필드가 둘 이상이면 ` / ` 로 이어 붙인다(예: 행사 주최자 연락처 둘).
    컬럼          12 관광지    14 문화시설        15 행사                    28 레포츠           38 쇼핑
    info_center   infocenter   infocenterculture  sponsor1tel / sponsor2tel  infocenterleports   infocentershopping   문의처
    reservation   (없음)       (없음)             bookingplace / eventhomepage  reservation      (없음)               예약처·예약 안내·예약 링크
    capacity      accomcount   accomcountculture  (없음)                     accomcountleports   (없음)               수용 인원
    spend_time    (없음)       spendtime          spendtimefestival          (없음)              (없음)               소요 시간
    age_limit     expagerange  (없음)             agelimit                   expagerangeleports  (없음)               연령 제한(체험 가능 연령 포함)
    experience_guide expguide  (없음)             (없음)                     (없음)              (없음)               체험 안내(자유 문장 — 예약·인원이 섞일 수 있다)
  ※상세는 이미 채워졌는데 이 5칸이 모두 빈 행은 `detailIntro2` **한 번만** 더 부른다(개요는 이미 있다).
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .tourapi_client import (CALL_INTERVAL_SECONDS, StopFetching, append_cache, call_tourapi,
                                    load_cache, load_service_key)

CSV_PATH = Path(__file__).resolve().parent / "activity_total_data.csv"

#: 타입 → (영업시간, 휴무, 요금) 필드 이름. None 이면 그 타입 응답에 해당 필드가 없다.
INTRO_FIELDS: dict[str, tuple[str | None, str | None, str | None]] = {
    "12": ("usetime", "restdate", None),
    "14": ("usetimeculture", "restdateculture", "usefee"),
    "15": ("playtime", None, "usetimefestival"),
    "28": ("usetimeleports", "restdateleports", "usefeeleports"),
    "38": ("opentime", "restdateshopping", None),
}
#: 사람이 볼 가치가 큰 타입부터.
PRIORITY = ["12", "14", "28", "15", "38"]
DETAIL_COLUMNS = ("overview", "business_hours", "closed_days", "fee")

#: ★`[2026-10-01]` 인원·예약 확인용 컬럼 — 통일된 컬럼 이름 → 타입별 `detailIntro2` 필드 이름. 없는 타입은 비운다.
EXTRA_FIELDS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "12": {"info_center": "infocenter", "capacity": "accomcount", "age_limit": "expagerange",
           "experience_guide": "expguide"},
    "14": {"info_center": "infocenterculture", "capacity": "accomcountculture", "spend_time": "spendtime"},
    "15": {"info_center": ("sponsor1tel", "sponsor2tel"), "reservation": ("bookingplace", "eventhomepage"),
           "spend_time": "spendtimefestival", "age_limit": "agelimit"},
    "28": {"info_center": "infocenterleports", "reservation": "reservation", "capacity": "accomcountleports",
           "age_limit": "expagerangeleports"},
    "38": {"info_center": "infocentershopping"},
}
EXTRA_COLUMNS = ("info_center", "reservation", "capacity", "spend_time", "age_limit", "experience_guide")
INTRO_ONLY = ("detailIntro2",)
BOTH_OPERATIONS = ("detailCommon2", "detailIntro2")


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _joined(intro: dict[str, Any], spec: str | tuple[str, ...]) -> str:
    """필드 하나, 또는 같은 뜻의 필드 여럿(예: 주최자 연락처 둘)을 ` / ` 로 이어 붙인다. 빈 값·중복은 뺀다."""
    names = (spec,) if isinstance(spec, str) else spec
    parts: list[str] = []
    for name in names:
        value = _text(intro.get(name))
        if value and value not in parts:
            parts.append(value)
    return " / ".join(parts)


def _filled(row: dict[str, str], columns: tuple[str, ...]) -> bool:
    return any((row.get(c) or "").strip() for c in columns)


def operations_for(row: dict[str, str]) -> tuple[str, ...]:
    """이 행에 필요한 호출. 상세 네 칸이 모두 비면 둘 다, 상세는 있는데 인원·예약 5칸이 비면 `detailIntro2` 만."""
    if not row["contentid"].isdigit() or row["contenttypeid"] not in INTRO_FIELDS:
        return ()
    if not _filled(row, DETAIL_COLUMNS):
        return BOTH_OPERATIONS
    if row["contenttypeid"] in EXTRA_FIELDS and not _filled(row, EXTRA_COLUMNS):
        return INTRO_ONLY
    return ()


def targets(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    todo = [r for r in rows if operations_for(r)]
    return sorted(todo, key=lambda r: PRIORITY.index(r["contenttypeid"]))


def apply_cache(rows: list[dict[str, str]], cache: dict[tuple[str, str], dict[str, Any] | None]) -> Counter:
    """캐시에 든 응답을 CSV 행에 옮긴다. 이미 값이 있는 칸은 건드리지 않는다."""
    stats: Counter = Counter()
    for row in rows:
        common = cache.get((row["contentid"], "detailCommon2"))
        intro = cache.get((row["contentid"], "detailIntro2"))
        fields = INTRO_FIELDS.get(row["contenttypeid"])
        if common is None and intro is None:
            continue
        new = {"overview": _text((common or {}).get("overview"))}
        if fields and intro:
            new["business_hours"] = _text(intro.get(fields[0])) if fields[0] else ""
            new["closed_days"] = _text(intro.get(fields[1])) if fields[1] else ""
            new["fee"] = _text(intro.get(fields[2])) if fields[2] else ""
        if intro:
            for column, spec in EXTRA_FIELDS.get(row["contenttypeid"], {}).items():
                new[column] = _joined(intro, spec)
        for column, value in new.items():
            if value and not (row.get(column) or "").strip():
                row[column] = value
                stats[column] += 1
    return stats


def fetch(todo: list[dict[str, str]], service_key: str, max_calls: int | None) -> int:
    cache = load_cache()
    calls = 0
    for row in todo:
        cid, ctype = row["contentid"], row["contenttypeid"]
        wanted = operations_for(row)
        for operation, params in (("detailCommon2", {"contentId": cid}),
                                  ("detailIntro2", {"contentId": cid, "contentTypeId": ctype})):
            if operation not in wanted or (cid, operation) in cache:
                continue
            if max_calls is not None and calls >= max_calls:
                return calls
            item = call_tourapi(operation, params, service_key)
            append_cache(cid, operation, item)
            cache[(cid, operation)] = item
            calls += 1
            time.sleep(CALL_INTERVAL_SECONDS)
    return calls


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-calls", type=int, default=None, help="이번 실행에서 부를 최대 횟수(장소당 2번)")
    ap.add_argument("--dry-run", action="store_true", help="호출 없이 대상 건수만 본다")
    ap.add_argument("--types", nargs="+", choices=sorted(INTRO_FIELDS), default=None,
                    help="이 타입만 받는다(예: --types 12 14 28 15). 안 주면 전부. 쇼핑(38)이 호출의 대부분이라 먼저 빼고 받을 때 쓴다")
    args = ap.parse_args(argv)

    with CSV_PATH.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    fieldnames += [c for c in EXTRA_COLUMNS if c not in fieldnames]      # 새 칸은 맨 뒤에 붙인다

    todo = [r for r in targets(rows) if not args.types or r["contenttypeid"] in args.types]
    cache = load_cache()
    remaining = sum(1 for r in todo for op in operations_for(r) if (r["contentid"], op) not in cache)
    by_type = Counter(r["contenttypeid"] for r in todo)
    print(f"대상 {len(todo)}행 {dict(by_type)} · 아직 안 부른 호출 {remaining}건")
    if args.dry_run:
        return

    try:
        calls = fetch(todo, load_service_key(), args.max_calls)
        print(f"이번 실행 호출 {calls}건")
    except StopFetching as stop:
        print(f"멈춤: {stop}\n→ 받은 것까지는 캐시에 남아 있다. 원인을 확인한 뒤 같은 명령을 다시 돌리면 이어서 받는다")

    filled = apply_cache(rows, load_cache())
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    left = sum(1 for r in targets(rows) for op in operations_for(r) if (r["contentid"], op) not in load_cache())
    print(f"CSV 반영: {dict(filled)} · 아직 안 부른 호출 {left}건")


if __name__ == "__main__":
    sys.exit(main())
