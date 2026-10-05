# -*- coding: utf-8 -*-
"""읽은 값 → 등록 몸통. `[2026-09-27]` 설계서 §3-7 · §3-8 · §5

    effective   한 판(revision)의 값 줄들 → (원본, 필드)마다 **마지막 값 하나**(고객이 고친 값이 뒤에 온다)
    assemble    → (`CreateTrip` 몸통, 막는 문제들). 문제가 하나라도 있으면 등록하지 않는다

★빈칸 채우기(§3-7) — 시각이 없는 항목은 **규칙으로 배치**하고 그렇게 했다고 적는다(`method: rule`):
    끼니  원문 줄에 끼니 말이 있으면 그 시각 — 아침·조식 08:00 · 점심·중식 12:00 · 저녁·석식 18:00
          ☆2026-09-27 실제 화면: 「점심은 토속촌에서」가 18:00 에 놓였다(끼니 말을 안 봤고, 원문 순서가 아니라
            항목 번호 순서로 채웠다 — 모델이 가리킨 항목은 번호가 규칙 항목 뒤에 붙는다)
    식사  (끼니 말이 없으면) 앞 항목이 12시 전에 끝나면 12:00, 아니면 18:00
    활동  앞 항목 끝 + 여유(`MOVE_BUFFER_MIN`), 첫 항목이면 하루 첫 활동 시각(`planner.DAY_START`)
    끝    적혀 있지 않으면 시작 + 활동 90분 · 식사 60분(`planner`) — 다음 항목 시작을 넘지 않게 자른다
★필수값 검사(§3-7) — 판정기 `check_itinerary` 는 **값이 없는 칸을 통과시킨다.** 그래서 앞에서 막는다:
    제목 없음 · 날짜 없음 · 장소를 못 정함(고객이 「장소 없음」으로 두면 통과) · 예약번호가 있는데 시각이 규칙 배치
★근거(§5) — 항목마다 `detail.provenance` 에 필드별 방법과 근거(원문 줄·조각 · 조회 출처)를 싣는다.
★예약번호(§3-8) — `detail.booking` 에 싣는다. **예약 표(`bookings`)는 만들지 않는다** — 업체 확인 없는 번호를
  「확정 예약」으로 적게 된다(업체 연결은 Booking Handoff, MVP 밖). 대신 이 표시가 있는 항목은
  `pending.protected_reason` 이 「booked」로 보고 **바꾸기 전에 묻는다.**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from .. import planner

KST = ZoneInfo("Asia/Seoul")
MAX_PARTY = 4                      #: 상품 범위 — 서울 · 최대 7일 · 4인
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
#: 제목으로 쓰지 않는 첫 줄 — 일차 머리줄
_DAY_HEADING = re.compile(r"^\s*(\d+\s*일\s*차|day\s*\d+)", re.IGNORECASE)
#: 외부 서비스에서 온 장소 — 등록할 때 그 여행 전용 행이 된다(`trip_api.EXTERNAL_PLACE_SOURCES`)
_SOURCE_ATTRS = {"tour_api": "source_content_id",
                 # ★요식 원장도 관광공사 ID 를 싣는다 — 판정 때 그 ID 로 원장 가게와 다시 잇는다(220)
                 "dining_ledger": "source_content_id"}
#: 원문 줄의 끼니 말 → 시각. 우리가 고른 값(planner 의 LUNCH_FROM · DINNER_FROM 과 같다)
_MEAL_TIMES = (("아침", "08:00"), ("조식", "08:00"), ("점심", "12:00"), ("중식", "12:00"),
               ("저녁", "18:00"), ("석식", "18:00"))


@dataclass
class Problem:
    code: str
    field: str
    message: str
    source_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "field": self.field, "message": self.message, "source_id": self.source_id}


@dataclass
class Assembled:
    body: dict[str, Any]
    problems: list[Problem] = field(default_factory=list)
    filled: list[dict[str, Any]] = field(default_factory=list)     # 규칙으로 채운 칸 — 화면이 배지로 보인다
    #: 「일정 짜 줘」 — 일정 생성기에 넘길 기본값(화면이 미리 채우고 고객이 고친다). 모르는 값은 None
    plan: dict[str, Any] = field(default_factory=dict)


def effective(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """(원본, 필드)마다 마지막 값 하나. `claims` 는 만든 순서여야 한다(`view` 가 그렇게 읽는다)."""
    last: dict[tuple[Any, str], dict[str, Any]] = {}
    for claim in claims:
        last[(claim["source_id"], claim["field"])] = claim
    return list(last.values())


def assemble(*, intake_id: str, revision: int, sources: list[dict[str, Any]],
             claims: list[dict[str, Any]]) -> Assembled:
    """`sources` = 원본 행(`position` 순), `claims` = 그 판의 값 줄(만든 순서)."""
    claims = effective(claims)
    trip = {c["field"][5:]: c for c in claims if c["field"].startswith("trip.")}
    problems: list[Problem] = []
    rows: list[dict[str, Any]] = []
    first_day = _date_value(trip.get("first_day"))
    for source in sources:
        mine = [c for c in claims if c["source_id"] == source["source_id"]]
        marks = _day_marks(mine)
        by_index: dict[int, dict[str, dict[str, Any]]] = {}
        for claim in mine:
            if claim["field"].startswith("items["):
                index = int(claim["field"][6:claim["field"].index("]")])
                by_index.setdefault(index, {})[claim["field"][claim["field"].index("]") + 2:]] = claim
        for index in sorted(by_index):
            fields = by_index[index]
            if (fields.get("removed") or {}).get("value") is True:
                continue
            rows.append(_row(source, index, fields, marks, first_day, problems))
    # ★시각이 없는 것은 **원문 순서** 안에서 규칙으로 배치한다 — 시각순으로 먼저 세우면 시각 없는 「점심」이
    #   그날 마지막 항목 뒤로 밀려 저녁이 된다. 채운 뒤에 날짜·시각 순으로 세운다.
    rows.sort(key=lambda r: (r["date"] or "9999", r["order"]))       # order = (원본, 원문 줄, 번호)
    filled: list[dict[str, Any]] = []
    _fill_times(rows, filled, problems)
    rows.sort(key=lambda r: (r["date"] or "9999", r["start"] or "99:99", r["order"]))
    places: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for seq, row in enumerate(rows, start=1):
        key = None
        if row["place"]:
            key = f"p{len(places) + 1}"
            places.append(_place_in(key, row["place"], row["kind"]))
        detail: dict[str, Any] = {"provenance": row["provenance"],
                                  "intake": {"intake_id": intake_id, "revision": revision}}
        if row["booking_no"]:
            detail["booking"] = {"booking_no": row["booking_no"], "declared_by": "customer_plan"}
        if row["date"] and row["start"]:
            items.append({"seq": seq, "kind": row["kind"], "title": row["title"] or "(제목 없음)", "place": key,
                          "starts_at": _at(row["date"], row["start"]).isoformat(),
                          "ends_at": _at(row["date"], row["end"]).isoformat() if row["end"] else None,
                          "detail": detail})
    body = {"request_id": f"intake:{intake_id}:r{revision}", "title": _title(trip, sources),
            "locale": "ko", "party_size": _int_value(trip.get("party_size")),
            "places": places, "items": items}
    plan = _plan_basis(trip, rows, sources, first_day)
    if not rows and not plan["requested"]:
        problems.append(Problem("no_items", "items", "읽은 일정이 없습니다 — 항목을 하나 이상 넣어 주세요"))
    party = body["party_size"]
    if party is not None and not 1 <= party <= MAX_PARTY:
        # ★상품은 최대 4인(루트 CLAUDE.md 「가격 · 비용」). 범위 밖 인원은 계약 오류로 새지 않게 여기서 막는다
        problems.append(Problem("party_size_out_of_range", "trip.party_size",
                                f"인원이 {party}명으로 읽혔습니다 — 1~{MAX_PARTY}명으로 고쳐 주세요"))
    return Assembled(body=body, problems=problems, filled=filled, plan=plan)


# ── 항목 하나 ─────────────────────────────────────────────────────
def _row(source, index, fields, marks, first_day, problems) -> dict[str, Any]:
    def value(name):
        return (fields.get(name) or {}).get("value")

    where = f"items[{index}]"
    sid = str(source["source_id"])
    title = (value("title") or "").strip() or None
    if not title:
        problems.append(Problem("no_title", f"{where}.title", "이 항목의 이름이 없습니다", sid))
    line = min((c["evidence"].get("line") or 0 for c in fields.values() if c["evidence"].get("line")), default=0)
    day, marked = _day_for(line, marks)
    when = value("date") or marked
    date_claim = fields.get("date")
    if first_day and (date_claim is None or date_claim["method"] != "customer") and \
            (not when or (date_claim and date_claim["evidence"].get("how") in ("unknown", "day_offset"))):
        # ★고객이 첫날을 알려 줬다 — 적힌 날짜가 없는 항목은 첫날 + (일차 − 1)
        when = (first_day + timedelta(days=(day or 1) - 1)).isoformat()
    if when and when.startswith("--"):
        when = None                      # 해가 없는 날짜는 날짜 해석이 채운다 — 여기까지 왔으면 모르는 것이다
    if not when:
        problems.append(Problem("no_date", f"{where}.date", f"「{title or '이 항목'}」의 날짜를 모릅니다 — "
                                "첫날 날짜를 알려 주세요", sid))
    place = value("place")
    no_place = (fields.get("place") or {}).get("method") == "customer" and place is None
    if not place and not no_place:
        problems.append(Problem("no_place", f"{where}.place", f"「{title or '이 항목'}」의 장소를 정하지 못했습니다 — "
                                "장소 이름을 고치거나 「장소 없음」으로 두세요", sid))
    kind = value("kind") if value("kind") in ("activity", "dining") else \
        ("dining" if (place or {}).get("kind") == "dining" else "activity")
    start, end = value("starts_at"), value("ends_at")
    lines = (source.get("transcript") or "").splitlines()
    text = lines[line - 1] if 0 < line <= len(lines) else ""
    return {"order": (source["position"], line or 10_000, index), "source_id": sid, "index": index, "title": title,
            "text": text,
            "date": when, "start": start if start and _HHMM.match(str(start)) else None,
            "end": end if end and _HHMM.match(str(end)) else None, "kind": kind,
            "place": place if place and place.get("latitude") is not None else None,
            "booking_no": value("booking_no"), "provenance": _provenance(fields), "where": where}


def _fill_times(rows, filled, problems) -> None:
    previous_end: dict[str, str] = {}
    for n, row in enumerate(rows):
        if not row["date"]:
            continue
        if not row["start"]:
            row["start"], why = _placed(row, previous_end.get(row["date"]))
            filled.append({"source_id": row["source_id"], "field": f"{row['where']}.starts_at",
                           "value": row["start"], "method": "rule", "note": why})
            row["provenance"]["starts_at"] = {"method": "rule", "rule": "intake.fill_start", "why": why}
            if row["booking_no"]:
                problems.append(Problem("booking_without_time", f"{row['where']}.starts_at",
                                        f"「{row['title']}」에 예약번호가 있는데 시각이 없습니다 — 예약 시각을 알려 주세요",
                                        row["source_id"]))
        if not row["end"]:
            minutes = planner.MEAL_MIN if row["kind"] == "dining" else planner.ACTIVITY_MIN
            end = _plus(row["start"], minutes)
            following = next((r for r in rows[n + 1:] if r["date"] == row["date"] and r["start"]), None)
            if following and row["start"] < following["start"] < end:
                end = following["start"]           # ★우리가 채운 끝이 다음 항목과 겹치게 하지 않는다
            if end <= row["start"]:
                end = None                          # 자정을 넘기면 끝을 비운다(판정기가 끝 없는 항목을 받는다)
            row["end"] = end
            if end:
                filled.append({"source_id": row["source_id"], "field": f"{row['where']}.ends_at", "value": end,
                               "method": "rule", "note": f"끝 시각이 없어 {minutes}분으로 두었다"})
                row["provenance"]["ends_at"] = {"method": "rule", "rule": "intake.default_duration",
                                                "minutes": minutes}
        previous_end[row["date"]] = row["end"] or row["start"]


def _placed(row, previous: str | None) -> tuple[str, str]:
    """(시각, 그렇게 정한 이유)."""
    for word, at in _MEAL_TIMES:
        if word in row["text"]:
            return at, f"시각이 없어 원문의 「{word}」로 {at} 에 두었다"
    if row["kind"] == "dining":
        at = "12:00" if not previous or previous <= "12:00" else "18:00"
        return at, f"시각이 없어 식사 시간({at})에 두었다"
    if previous:
        return _plus(previous, planner.MOVE_BUFFER_MIN), "시각이 없어 앞 일정 뒤에 두었다"
    return planner.DAY_START.strftime("%H:%M"), "시각이 없어 하루 첫 일정 시각에 두었다"


# ── 일정 생성 기본값 ─────────────────────────────────────────────
def _plan_basis(trip, rows, sources, first_day) -> dict[str, Any]:
    """「일정 짜 줘」의 기본값 — **읽은 값에서만** 낸다. 없으면 None 으로 두고 화면이 묻는다(지어내지 않는다).

    첫날 = 고객이 알려 준 첫날 > 읽은 항목의 가장 이른 날짜. 일수 = 「n박 m일」의 m > 읽은 날짜 수.
    선호 = 고객이 올린 원문 그대로(일정 생성기가 규정에 붙여 읽는다, `planner.ground_request`).
    """
    dates = sorted({r["date"] for r in rows if r["date"]})
    days = _int_value(trip.get("days")) or (len(dates) or None)
    text = "\n".join(s.get("transcript") or "" for s in sources).strip()
    return {"requested": bool((trip.get("plan_request") or {}).get("value")),
            "start_date": first_day.isoformat() if first_day else (dates[0] if dates else None),
            "days": days if days is None or 1 <= days <= planner.MAX_DAYS else None,
            "party_size": _int_value(trip.get("party_size")),
            "preferences": text[:2000]}


# ── 부품 ─────────────────────────────────────────────────────────
def _place_in(key: str, place: dict[str, Any], kind: str) -> dict[str, Any]:
    source = str(place.get("source") or "")
    attributes: dict[str, Any] = {}
    if source in ("tour_api", "kakao", "dining_ledger"):
        attributes["source"] = source
        if source in _SOURCE_ATTRS and place.get("content_id"):
            attributes[_SOURCE_ATTRS[source]] = str(place["content_id"])
    return {"key": key, "name": place["name"], "kind": place.get("kind") or kind,
            "lat": float(place["latitude"]), "lon": float(place["longitude"]),
            "weather_sensitive": False, "attributes": attributes}


def _provenance(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for name, claim in fields.items():
        evidence = claim.get("evidence") or {}
        keep = {k: evidence[k] for k in ("source", "line", "start", "end", "text", "how", "query",
                                        "content_id", "source_id") if evidence.get(k) is not None}
        # ★근거 안의 `method`(장소를 찾은 단계 — places · typo · tour_api · kakao)는 `via` 로 옮긴다.
        #   그대로 펼치면 값의 방법(lookup)을 덮어쓴다(시험에서 잡았다).
        if evidence.get("method"):
            keep["via"] = evidence["method"]
        out[name] = {**keep, "method": claim["method"]}
    return out


def _day_marks(claims) -> list[tuple[int, int | None, str | None]]:
    marks: dict[int, list] = {}
    for claim in claims:
        field_name, line = claim["field"], claim["evidence"].get("line", 0)
        if field_name.startswith("days[") and field_name.endswith(".heading"):
            marks.setdefault(line, [None, None])[0] = claim["value"]
        elif field_name.startswith("days[") and field_name.endswith(".date"):
            marks.setdefault(line, [None, None])[1] = claim["value"]
    return [(line, day, when) for line, (day, when) in sorted(marks.items())]


def _day_for(line, marks) -> tuple[int | None, str | None]:
    day = when = None
    for mark_line, mark_day, mark_date in marks:
        if mark_line > line:
            break
        if mark_day is not None:
            day, when = mark_day, mark_date
        elif mark_date is not None:
            when = mark_date
    return day, when


def _title(trip: dict[str, dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    if trip.get("title") and str(trip["title"]["value"] or "").strip():
        return str(trip["title"]["value"]).strip()[:80]
    for source in sources:
        for line in (source.get("transcript") or "").splitlines():
            text = line.strip()
            if text and not _DAY_HEADING.match(text) and not re.match(r"^\d{1,2}[:시]", text):
                return text[:80]
    return "내 여행"


def _date_value(claim: dict[str, Any] | None) -> date | None:
    try:
        return date.fromisoformat(str(claim["value"])) if claim and claim.get("value") else None
    except ValueError:
        return None


def _int_value(claim: dict[str, Any] | None) -> int | None:
    try:
        return int(claim["value"]) if claim and claim.get("value") is not None else None
    except (TypeError, ValueError):
        return None


def _plus(hhmm: str, minutes: int) -> str:
    moved = datetime.combine(date(2000, 1, 1), time.fromisoformat(hhmm)) + timedelta(minutes=minutes)
    return "23:59" if moved.date() != date(2000, 1, 1) else moved.strftime("%H:%M")


def _at(day: str, hhmm: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(hhmm), tzinfo=KST)


__all__ = ["Assembled", "Problem", "assemble", "effective"]
