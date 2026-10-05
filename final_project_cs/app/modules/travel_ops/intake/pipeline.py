# -*- coding: utf-8 -*-
"""계획 읽기 한 건 — 접수 · 글자로 · 규칙 읽기 · 읽은 값 저장. `[2026-09-27]` 설계서 §3·§6

    open_intake   접수 행 + 원본 행(글자 판별·sha256). ★여기서는 읽지 않는다 — 요청이 45초를 기다리지 않게
    process       뒤에서: 원본마다 글자로(받아쓰기 포함) → 규칙 읽기 → `intake_claims` (revision 1) → review
    view          화면이 읽는 모양: 진행 단계 · 원본별 줄 번호 글 · 읽은 항목 · 확인 필요 · 남은 줄

★실패를 숨기지 않는다 — 형식을 못 읽으면 `fatal`(코드·사유), 받아쓰기 모델이 죽으면 `fatal` + 다시 시도 가능.
  「읽은 항목 0개」는 실패가 아니다 — 확인 화면이 남은 줄을 보여 주고 고객이 고친다(2주차부터 LLM 이 남은 줄을 돕는다).
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Callable
from uuid import UUID
from zoneinfo import ZoneInfo

from .assemble import assemble, effective
from .rules import read_plan
from .sources import UnsupportedSource, sniff, to_text

KST = ZoneInfo("Asia/Seoul")

#: 받는 크기 · 개수 — 우리가 고른 값(설계서 §9 「악성 파일·압축 폭탄」). 넘으면 접수하지 않는다
MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 20_000

STAGES = {"received": "받았어요", "transcribing": "글자로 옮기는 중", "reading": "규칙으로 읽는 중",
          "review": "확인해 주세요", "confirmed": "등록했어요", "fatal": "읽지 못했어요"}


class IntakeRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def open_intake(conn, *, tenant_id: str, customer_id: UUID, text: str | None,
                files: list[tuple[str, bytes]]) -> UUID:
    """접수 한 건. `files` = [(파일 이름, 바이트)]. ★종류는 첫 바이트로 — 모르는 형식은 여기서 거절한다."""
    text = (text or "").strip()
    if not text and not files:
        raise IntakeRejected("empty_intake", "글이나 파일을 하나 이상 보내 주세요")
    if len(text) > MAX_TEXT_CHARS:
        raise IntakeRejected("text_too_long", f"글은 {MAX_TEXT_CHARS:,}자까지 받아요")
    if len(files) > MAX_FILES:
        raise IntakeRejected("too_many_files", f"파일은 {MAX_FILES}개까지 받아요")
    kinds = []
    for name, data in files:
        if len(data) > MAX_FILE_BYTES:
            raise IntakeRejected("file_too_large", f"{name}: 파일 하나는 {MAX_FILE_BYTES // 1024 // 1024}MB 까지 받아요")
        try:
            kinds.append(sniff(data, filename=name))
        except UnsupportedSource as exc:
            raise IntakeRejected("unsupported_format", f"{name}: {exc}") from None
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("INSERT INTO trip_intakes (tenant_id, customer_id, stage) VALUES (%s,%s,'received') "
                    "RETURNING intake_id", (tenant_id, customer_id))
        intake_id = cur.fetchone()[0]
        position = 0
        if text:
            raw = text.encode("utf-8")
            cur.execute("INSERT INTO intake_sources (intake_id, tenant_id, position, kind, sha256, size_bytes, "
                        "transcript) VALUES (%s,%s,%s,'chat',%s,%s,%s)",
                        (intake_id, tenant_id, position, hashlib.sha256(raw).hexdigest(), len(raw), text))
            position += 1
        for (name, data), kind in zip(files, kinds):
            # ★원본은 DB 에 두지 않는다 — 뒤에서 읽을 동안만 메모리/임시 저장소(`blobs`)에 둔다
            cur.execute("INSERT INTO intake_sources (intake_id, tenant_id, position, kind, filename, sha256, "
                        "size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (intake_id, tenant_id, position, kind, name[:200], hashlib.sha256(data).hexdigest(),
                         len(data)))
            position += 1
    return intake_id


def process(connect: Callable[[], Any], *, tenant_id: str, intake_id: UUID, blobs: dict[int, bytes],
            see: Callable[[str, bytes], str] | None, chat: Any = None, tour: Any = None, kakao: Any = None,
            today: date | None = None, dining: Any = None) -> str:
    """뒤에서 한 건을 읽는다. 돌려주는 값은 마지막 상태(review · fatal). `blobs` = {position: 바이트}.

    ★2주차(2026-09-27): 규칙 → 남은 줄은 모델이 **가리키기만**(`chat`) → 날짜 해석 → 장소 찾기(`tour` 관광공사 ·
      `kakao`). 넣지 않은 것은 건너뛴다 — 그 값은 「확인 필요」로 남고 확인 화면에서 고객이 고친다.
    """
    try:
        with connect() as conn:
            sources = _sources(conn, tenant_id, intake_id)
        for source in sources:
            if source["transcript"] is None:
                _stage(connect, tenant_id, intake_id, "transcribing")
                data = blobs.get(source["position"])
                if data is None:
                    raise IntakeRejected("source_missing", f"{source['filename']}: 원본을 찾지 못했다")
                result = to_text(data, filename=source["filename"] or "", see=see)
                with connect() as conn, conn.transaction(), conn.cursor() as cur:
                    cur.execute("UPDATE intake_sources SET transcript=%s, transcribed=%s, missing_json=%s, "
                                "seconds=%s WHERE source_id=%s AND tenant_id=%s",
                                (result.text, bool(result.transcribed_pages) or result.kind == "image",
                                 json.dumps(result.missing, ensure_ascii=False), result.seconds,
                                 source["source_id"], tenant_id))
                source["transcript"] = result.text
        _stage(connect, tenant_id, intake_id, "reading")
        with connect() as conn:
            our_places = _our_places(conn, tenant_id)
            aliases = load_aliases(conn, tenant_id)
        rows_by_source = []
        for source in sources:
            rows_by_source.append((source, read_source(source["transcript"] or "", chat=chat, tour=tour, kakao=kakao,
                                                       our_places=our_places, aliases=aliases, dining=dining,
                                                       today=today or datetime.now(KST).date())))
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM intake_claims WHERE tenant_id=%s AND intake_id=%s AND revision=1 "
                        "AND method <> 'customer'", (tenant_id, intake_id))
            for source, rows in rows_by_source:
                for row in rows:
                    cur.execute("INSERT INTO intake_claims (intake_id, tenant_id, revision, source_id, field, "
                                "value_json, method, evidence, needs_review, note) "
                                "VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s)",
                                (intake_id, tenant_id, source["source_id"], row["field"],
                                 json.dumps(row["value"], ensure_ascii=False), row["method"],
                                 json.dumps({**row["evidence"], "source_id": str(source["source_id"])},
                                            ensure_ascii=False, default=str), row["needs_review"], row["note"]))
            cur.execute("UPDATE trip_intakes SET status='review', stage='review', updated_at=now() "
                        "WHERE tenant_id=%s AND intake_id=%s", (tenant_id, intake_id))
        return "review"
    except (IntakeRejected, UnsupportedSource) as exc:
        code = getattr(exc, "code", "unsupported_format")
        _fatal(connect, tenant_id, intake_id, code, str(exc))
        return "fatal"
    except Exception as exc:                         # ★받아쓰기 모델 장애 등 — 숨기지 않고 남긴다
        _fatal(connect, tenant_id, intake_id, "reading_failed", f"{type(exc).__name__}: {exc}"[:300])
        return "fatal"


def view(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID) -> dict[str, Any] | None:
    """확인 화면이 읽는 모양. ★남의 접수는 없는 것과 같다(None)."""
    with conn.cursor() as cur:
        cur.execute("SELECT status, stage, revision, fatal_code, fatal_detail, trip_id, received_at "
                    "FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s",
                    (tenant_id, intake_id, customer_id))
        row = cur.fetchone()
        if row is None:
            return None
        status, stage, revision, fatal_code, fatal_detail, trip_id, received_at = row
        sources = _sources(conn, tenant_id, intake_id)
        cur.execute("SELECT source_id, field, value_json, method, evidence, needs_review, note FROM intake_claims "
                    "WHERE tenant_id=%s AND intake_id=%s AND revision=%s ORDER BY created_at, claim_id",
                    (tenant_id, intake_id, revision))
        claims = [dict(zip(("source_id", "field", "value", "method", "evidence", "needs_review", "note"), r))
                  for r in cur.fetchall()]
    # ★고객이 고친 값이 같은 칸의 앞 값을 덮는다 — 화면에는 칸마다 하나만(이전 값은 이전 판에 남아 있다)
    claims = effective(claims)
    check = None
    if status in ("review", "confirmed"):
        built = assemble(intake_id=str(intake_id), revision=revision, sources=sources, claims=claims)
        check = {"ready": not built.problems and bool(built.body["items"]),
                 "problems": [p.as_dict() for p in built.problems],
                 "filled": built.filled, "items": len(built.body["items"]), "title": built.body["title"],
                 "plan": built.plan}
    out_sources = []
    for source in sources:
        mine = [c for c in claims if c["source_id"] == source["source_id"]]
        lines = (source["transcript"] or "").splitlines()
        read_lines = {c["evidence"].get("line") for c in mine}
        out_sources.append({
            "source_id": str(source["source_id"]), "kind": source["kind"], "filename": source["filename"],
            "transcribed": source["transcribed"], "missing": source["missing_json"], "seconds": source["seconds"],
            "lines": [{"no": n, "text": text, "read": n in read_lines} for n, text in enumerate(lines, start=1)],
            "items": _items(mine), "trip": _trip_fields(mine),
            # 남은 줄에 모델이 가리킨 결과 — 몇 개 받고 무엇을 버렸나(원문에 없는 인용). 모델을 안 불렀으면 None
            "reading": next((c["value"] for c in mine if c["field"] == "reading.llm"), None)})
    return {"intake_id": str(intake_id), "status": status, "stage": stage, "stage_label": STAGES.get(stage, stage),
            "revision": revision, "fatal": {"code": fatal_code, "detail": fatal_detail} if fatal_code else None,
            "trip_id": str(trip_id) if trip_id else None, "received_at": received_at.isoformat(),
            "sources": out_sources, "check": check,
            "needs_review": [{"field": c["field"], "value": c["value"], "note": c["note"],
                              "evidence": c["evidence"]} for c in claims if c["needs_review"]]}


# ── 고치기 · 등록 준비 (3주차) ───────────────────────────────────────
#: 고객이 고칠 수 있는 칸. ★값은 서버가 모양을 확인한다 — 받은 글자를 그대로 믿지 않는다
_ITEM_FIELDS = {"title", "date", "starts_at", "ends_at", "kind", "place", "booking_no", "removed"}
_TRIP_FIELDS = {"title", "party_size", "first_day"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
#: 「일정 짜 줘」 모양 — 일정·코스·계획을 짜/만들어/추천해 달라는 말
PLAN_ASK = re.compile(r"(일정|코스|계획|동선|여행)\S{0,3}\s*(?:(?:좀|다시|새로)\s*)?(짜|만들어|추천해|세워|잡아)\s*"
                      r"(줘|주세요|줄래|주실|주라|달라)")


class IntakeConflict(ValueError):
    """낡은 확인 화면 — 그 사이 판이 바뀌었거나 아직 등록할 수 없는 상태다."""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


def edit(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID, revision: int,
         edits: list[dict[str, Any]], tour: Any = None, kakao: Any = None, dining: Any = None) -> int:
    """고객이 고친 값 → **새 판**(revision + 1). 앞 판의 값은 그대로 남는다(근거를 지우지 않는다, 설계서 §5).

    `edits` = [{"source_id", "field", "value"}]. 장소는 `{"name": "…"}` 로 받아 **다시 찾고**, 찾은 곳 하나를
    싣는다. `{"none": true}` 는 「장소 없음」(자유시간 · 이름 없는 호텔). 돌려주는 값은 새 판 번호.
    """
    if not edits:
        raise IntakeRejected("no_edits", "고칠 값이 없습니다")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT status, revision FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s "
                    "FOR UPDATE", (tenant_id, intake_id, customer_id))
        row = cur.fetchone()
        if row is None:
            raise LookupError("intake")
        status, current = row
        if status != "review":
            raise IntakeConflict("intake_not_editable", f"지금은 고칠 수 없습니다(상태 {status})", status=status)
        if revision != current:
            raise IntakeConflict("stale_revision", "그 사이 다른 화면에서 고쳤습니다 — 새로 불러와 주세요",
                                 current_revision=current)
        sources = {str(s["source_id"]) for s in _sources(conn, tenant_id, intake_id)}
        our_places = None
        rows = []
        for entry in edits:
            source_id, field_name = str(entry.get("source_id") or ""), str(entry.get("field") or "")
            if field_name.startswith("items[") and source_id not in sources:
                raise IntakeRejected("unknown_source", f"{field_name}: 이 접수의 원본이 아닙니다")
            value, evidence, note = _checked(field_name, entry.get("value"))
            if field_name.endswith(".place") and value is not None:
                if our_places is None:
                    our_places = _our_places(conn, tenant_id)
                typed = str(value["name"]).strip()
                kind = _item_kind(cur, tenant_id, intake_id, current, source_id, field_name, edits)
                value, evidence, note = _typed_place(value, our_places, tour, kakao, dining,
                                                     kind_hint="dining" if kind == "dining" else None)
                # 원래 읽은 이름 → 고객이 고친 이름을 별칭으로 쌓는다(다음 고객은 안 고쳐도 되게)
                cur.execute("SELECT value_json FROM intake_claims WHERE tenant_id=%s AND intake_id=%s AND revision=%s "
                            "AND source_id=%s AND field=%s ORDER BY created_at DESC LIMIT 1",
                            (tenant_id, intake_id, current, source_id, field_name[:-len("place")] + "title"))
                was = cur.fetchone()
                if was and isinstance(was[0], str):
                    remember_alias(cur, tenant_id, was[0], typed)
            rows.append((source_id if field_name.startswith("items[") else None, field_name, value, evidence, note))
        new = current + 1
        # 앞 판을 그대로 옮기고(만든 순서 유지) 고친 값을 뒤에 얹는다 — 칸마다 마지막 값이 이긴다
        cur.execute("INSERT INTO intake_claims (intake_id, tenant_id, revision, source_id, field, value_json, method, "
                    "evidence, needs_review, note, created_at) SELECT intake_id, tenant_id, %s, source_id, field, "
                    "value_json, method, evidence, needs_review, note, created_at FROM intake_claims "
                    "WHERE tenant_id=%s AND intake_id=%s AND revision=%s",
                    (new, tenant_id, intake_id, current))
        for source_id, field_name, value, evidence, note in rows:
            cur.execute("INSERT INTO intake_claims (intake_id, tenant_id, revision, source_id, field, value_json, "
                        "method, evidence, needs_review, note, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,'customer',%s,false,%s, clock_timestamp())",
                        (intake_id, tenant_id, new, source_id or None, field_name,
                         json.dumps(value, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False, default=str),
                         note))
        cur.execute("UPDATE trip_intakes SET revision=%s, updated_at=now() WHERE tenant_id=%s AND intake_id=%s",
                    (new, tenant_id, intake_id))
    return new


def draft(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID, revision: int):
    """등록 직전 — (현재 상태, 이미 만든 여행 id, 조립 결과). ★판이 다르면 낡은 화면이라 막는다."""
    with conn.cursor() as cur:
        cur.execute("SELECT status, revision, trip_id FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s "
                    "AND customer_id=%s", (tenant_id, intake_id, customer_id))
        row = cur.fetchone()
        if row is None:
            raise LookupError("intake")
        status, current, trip_id = row
        if status not in ("review", "confirmed"):
            raise IntakeConflict("intake_not_ready", f"아직 등록할 수 없습니다(상태 {status})", status=status)
        if revision != current:
            raise IntakeConflict("stale_revision", "그 사이 다른 화면에서 고쳤습니다 — 새로 불러와 주세요",
                                 current_revision=current)
        cur.execute("SELECT source_id, field, value_json, method, evidence, needs_review, note FROM intake_claims "
                    "WHERE tenant_id=%s AND intake_id=%s AND revision=%s ORDER BY created_at, claim_id",
                    (tenant_id, intake_id, current))
        claims = [dict(zip(("source_id", "field", "value", "method", "evidence", "needs_review", "note"), r))
                  for r in cur.fetchall()]
    built = assemble(intake_id=str(intake_id), revision=current, sources=_sources(conn, tenant_id, intake_id),
                     claims=claims)
    return status, trip_id, built


def mark_confirmed(conn, *, tenant_id: str, intake_id: UUID, trip_id: Any) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE trip_intakes SET status='confirmed', stage='confirmed', trip_id=%s, updated_at=now() "
                    "WHERE tenant_id=%s AND intake_id=%s", (trip_id, tenant_id, intake_id))


def _checked(field_name: str, value: Any) -> tuple[Any, dict[str, Any], str | None]:
    """칸 이름과 값의 모양을 확인한다. 돌려주는 것 = (값, 근거, 메모)."""
    evidence = {"source": "customer"}
    if field_name.startswith("trip."):
        name = field_name[5:]
        if name not in _TRIP_FIELDS:
            raise IntakeRejected("unknown_field", f"{field_name}: 고칠 수 없는 칸입니다")
        if name == "party_size":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 4:
                raise IntakeRejected("invalid_value", "인원은 1~4명입니다")
        elif name == "first_day":
            _iso_date(field_name, value)
        elif not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise IntakeRejected("invalid_value", "여행 이름은 1~80자입니다")
        return value, evidence, None
    if not field_name.startswith("items[") or "]." not in field_name:
        raise IntakeRejected("unknown_field", f"{field_name}: 고칠 수 없는 칸입니다")
    try:
        index = int(field_name[6:field_name.index("]")])
    except ValueError:
        raise IntakeRejected("unknown_field", f"{field_name}: 항목 번호가 숫자가 아닙니다") from None
    name = field_name[field_name.index("]") + 2:]
    if index < 0 or index > 500 or name not in _ITEM_FIELDS:
        raise IntakeRejected("unknown_field", f"{field_name}: 고칠 수 없는 칸입니다")
    if name in ("starts_at", "ends_at"):
        if not isinstance(value, str) or not _HHMM.match(value):
            raise IntakeRejected("invalid_value", f"{field_name}: 시각은 HH:MM 입니다")
    elif name == "date":
        _iso_date(field_name, value)
    elif name == "kind":
        if value not in ("activity", "dining"):
            raise IntakeRejected("invalid_value", f"{field_name}: 종류는 activity · dining 입니다")
    elif name == "removed":
        if value is not True:
            raise IntakeRejected("invalid_value", f"{field_name}: 빼려면 true 를 보냅니다")
    elif name == "place":
        if isinstance(value, dict) and value.get("none") is True:
            return None, {"source": "customer", "none": True}, "고객이 「장소 없음」으로 두었다"
        if not isinstance(value, dict) or not str(value.get("name") or "").strip():
            raise IntakeRejected("invalid_value", f'{field_name}: 장소는 {{"name": …}} 또는 {{"none": true}} 입니다')
    elif not isinstance(value, str) or not value.strip() or len(value) > 80:
        raise IntakeRejected("invalid_value", f"{field_name}: 1~80자 글이어야 합니다")
    return value, evidence, None


def _iso_date(field_name: str, value: Any) -> None:
    try:
        date.fromisoformat(str(value))
    except ValueError:
        raise IntakeRejected("invalid_value", f"{field_name}: 날짜는 YYYY-MM-DD 입니다") from None


def _item_kind(cur, tenant_id: str, intake_id: UUID, revision: int, source_id: str, place_field: str,
               edits: list[dict[str, Any]]) -> str | None:
    """장소를 고치는 항목의 종류 — 같은 요청에서 함께 고친 값이 먼저, 없으면 지금 판의 값. 식사면 요식 원장부터 찾는다."""
    kind_field = place_field[:-len("place")] + "kind"
    for entry in edits:
        if str(entry.get("source_id") or "") == source_id and entry.get("field") == kind_field:
            return entry.get("value")
    cur.execute("SELECT value_json FROM intake_claims WHERE tenant_id=%s AND intake_id=%s AND revision=%s "
                "AND source_id=%s AND field=%s ORDER BY created_at DESC LIMIT 1",
                (tenant_id, intake_id, revision, source_id, kind_field))
    row = cur.fetchone()
    return row[0] if row and isinstance(row[0], str) else None


def _typed_place(value: dict[str, Any], our_places, tour, kakao, dining=None, kind_hint: str | None = None):
    """고객이 적은 장소 이름 → 다시 찾아 **하나**. 못 찾으면 받지 않는다(지어내지 않는다)."""
    from .places import resolve

    name = str(value["name"]).strip()[:80]
    found = resolve(name, our_places=our_places, tour=tour, kakao=kakao, dining=dining, kind_hint=kind_hint)
    if found.status != "resolved":
        raise IntakeRejected("place_not_found", f"「{name}」: {found.note}")
    resolved = {"name": found.name, "kind": found.kind, "latitude": found.latitude, "longitude": found.longitude,
                "place_id": found.place_id, "content_id": found.content_id, "source": found.evidence()["source"]}
    return resolved, {**found.evidence(), "typed": name}, found.note


# ── 한 원본 읽기: 규칙 → 남은 줄 → 날짜 → 장소 ─────────────────────
def read_source(text: str, *, chat: Any = None, tour: Any = None, kakao: Any = None,
                our_places: list[dict[str, Any]] | None = None, today: date,
                aliases: dict[str, str] | None = None, dining: Any = None) -> list[dict[str, Any]]:
    """읽은 값 줄들(`intake_claims` 한 행 = 한 dict). ★값마다 방법(rule·llm_span·lookup)과 근거가 붙는다."""
    from .dates import resolve_dates
    from .llm_spans import label_lines
    from .places import _kind_hits, nearest, resolve

    read = read_plan(text)
    rows = [claim.as_dict() for claim in read.claims]
    marks = [(c.span.line, c.value if c.field.endswith(".heading") else None,
              c.value if c.field.endswith(".date") else None) for c in read.claims if c.field.startswith("days[")]
    items: list[dict[str, Any]] = []
    for item in read.items:
        items.append({"line": (item.title or item.booking_no).line if (item.title or item.booking_no) else 0,
                      "day": item.day, "date": item.date, "title": item.title.text if item.title else None,
                      "meal": any(r["field"] == f"items[{len(items)}].kind" for r in rows)})

    # 남은 줄 — 모델은 가리키기만. 실패하면 이 단계만 건너뛴다(그 줄은 남은 줄로 보인다)
    if chat is not None and read.unread_lines:
        try:
            span_claims, span_items, report = label_lines(read.lines, read.unread_lines, chat)
        except Exception as exc:                      # noqa: BLE001 — 모델 장애는 기록하고 넘어간다
            span_claims, span_items, report = [], [], {"error": f"{type(exc).__name__}: {exc}"[:200]}
        rows.append({"field": "reading.llm", "value": report, "method": "llm_span",
                     "evidence": {"source": "model", "lines": read.unread_lines}, "needs_review": False,
                     "note": "남은 줄에 모델이 가리킨 결과(원문에 없는 인용은 버렸다)"})
        by_line = {c.span.line: c for c in span_claims}
        for span_item in span_items:
            index = len(items)
            day, date_mark = _mark_for(span_item.line, marks)
            rows.append({"field": f"items[{index}].title", "value": span_item.title.text, "method": "llm_span",
                         "evidence": {"source": "text", **span_item.title.as_dict()}, "needs_review": True,
                         "note": "모델이 가리킨 원문 조각"})
            time_claim = by_line.get(span_item.line)
            if time_claim is not None:
                rows.append({**time_claim.as_dict(), "field": f"items[{index}].starts_at"})
            if span_item.meal:
                rows.append({"field": f"items[{index}].kind", "value": "dining", "method": "llm_span",
                             "evidence": {"source": "text", "line": span_item.line}, "needs_review": False,
                             "note": f"끼니 말({span_item.meal})"})
            items.append({"line": span_item.line, "day": day, "date": span_item.date or date_mark,
                          "title": span_item.title.text, "meal": bool(span_item.meal)})
        if report.get("plan_request"):
            rows.append({"field": "trip.plan_request", "value": True, "method": "llm_span",
                         "evidence": {"source": "text"}, "needs_review": False,
                         "note": "「일정 짜 줘」— 확인 화면에서 일정 생성기로 넘긴다"})

    # 「일정 짜 줘」 — 모델이 못 가리켜도 규칙으로 잡는다(모델이 꺼져 있어도 요청을 놓치지 않게)
    if not any(r["field"] == "trip.plan_request" for r in rows):
        for number, line in enumerate(read.lines, start=1):
            asked = PLAN_ASK.search(line)
            if asked:
                rows.append({"field": "trip.plan_request", "value": True, "method": "rule",
                             "evidence": {"source": "text", "line": number, "start": asked.start(),
                                          "end": asked.end(), "text": asked.group(0)},
                             "needs_review": False, "note": "「일정 짜 줘」 — 확인 화면에서 일정 생성기로 넘긴다"})
                break

    # 날짜 — 우선순위대로. 다 없으면 첫날을 묻는다
    dated, ask = resolve_dates(items, today=today, lines=read.lines)
    for index, day_date in enumerate(dated):
        rows.append({"field": f"items[{index}].date", "value": day_date.value, "method": "rule",
                     "evidence": {"source": "rule", "how": day_date.how, "line": items[index]["line"]},
                     "needs_review": day_date.needs_review, "note": day_date.note})
    if ask:
        rows.append({"field": "trip.ask_first_day", "value": True, "method": "rule",
                     "evidence": {"source": "rule"}, "needs_review": True,
                     "note": "날짜가 적혀 있지 않다 — 첫날 날짜 하나만 묻는다"})

    # 장소 — 항목마다 하나(원문이 가리킨 만큼만)
    resolved: dict[int, Any] = {}
    for index, item in enumerate(items):
        if item["title"]:
            # 일정 시각(날짜+시간)을 near 힌트 기준으로 넘긴다
            if hasattr(tour, "set_current_date"):
                date_val = dated[index].value if index < len(dated) else None
                starts_at = next((r["value"] for r in rows if r["field"] == f"items[{index}].starts_at"), None)
                dt_str = f"{date_val}T{starts_at}" if date_val and starts_at else date_val
                tour.set_current_date(dt_str)
            resolved[index] = resolve(item["title"], our_places=our_places or [], tour=tour, kakao=kakao,
                                      kind_hint="dining" if item["meal"] else None, aliases=aliases,
                                      dining=dining)
    # 두 번째 패스 — near 없이 미뤄진 항목을 다른 장소가 확정된 뒤 재시도한다(한 번만)
    # kakao 로 resolved 된 항목도 재시도 대상에 포함한다:
    # places.py normalize()가 점포 접미사를 제거하므로 "올리브영 홍대사거리점" → "올리브영" exact match →
    # Kakao 첫 번째 결과가 무조건 선택된다. near 반영을 위해 2차 패스에서 재시도한다.
    _ctx = getattr(tour, "_ctx", None)
    if getattr(tour, "had_deferred", False) and _ctx is not None and _ctx.get_near() is not None:
        tour.had_deferred = False
        for index, item in enumerate(items):
            if item["title"] and resolved.get(index) is not None and (
                resolved[index].status == "unresolved"
                or getattr(resolved[index], "method", None) == "kakao"
            ):
                if hasattr(tour, "set_current_date"):
                    date_val = dated[index].value if index < len(dated) else None
                    starts_at = next((r["value"] for r in rows if r["field"] == f"items[{index}].starts_at"), None)
                    dt_str = f"{date_val}T{starts_at}" if date_val and starts_at else date_val
                    tour.set_current_date(dt_str)
                resolved[index] = resolve(item["title"], our_places=our_places or [], tour=tour, kakao=kakao,
                                          kind_hint="dining" if item["meal"] else None, aliases=aliases,
                                          dining=dining)
    for index, found in resolved.items():
        evidence = found.evidence()
        value = None
        note, review = found.note, found.needs_review
        if found.status == "resolved":
            value = {"name": found.name, "kind": found.kind, "latitude": found.latitude,
                     "longitude": found.longitude, "place_id": found.place_id, "content_id": found.content_id,
                     "source": evidence["source"]}
        elif found.candidates:
            # ★설계서 §4-2 — 이름이 특정하지 않으면 종류가 맞는 후보 중 **같은 날 앞뒤 일정에 가장 가까운 곳** 하나.
            #   선택지를 나열하지 않는다. 고른 이유(거리)를 근거에 남기고 확인을 받는다
            near = _neighbours(index, items, resolved, dated)
            pool = list(found.candidates)
            if near and kakao is not None:
                # 앞뒤 일정 가운데에서 **거리순**으로 한 번 더 찾는다 — 서울 전역 상위 5곳만으로는 14km 밖을 골랐다(실측)
                mid = (sum(p[0] for p in near) / len(near), sum(p[1] for p in near) / len(near))
                around = kakao.search(items[index]["title"], near=mid) or []
                seen = {c["name"] for c in pool}
                pool += [h for h in _kind_hits(items[index]["title"], around) if h["name"] not in seen]
            pick, metres = nearest(pool, near)
            value = {"name": pick["name"], "kind": "dining" if pick.get("category_group") in ("FD6", "CE7")
                     else (found.kind or "activity"), "latitude": pick["latitude"], "longitude": pick["longitude"],
                     "place_id": None, "content_id": None, "source": "kakao"}
            evidence = {**evidence, "source": "kakao", "method": "kakao_nearest", "name": pick["name"],
                        "chosen_from": [c["name"] for c in pool],
                        "neighbours": len(near), "mean_distance_m": round(metres) if metres is not None else None}
            review = True
            note = (f"이름이 특정하지 않아 종류가 맞는 {len(pool)}곳 중 "
                    + (f"앞뒤 일정에 가장 가까운 「{pick['name']}」(평균 {round(metres):,}m)을 골랐다"
                       if metres is not None else f"앞뒤 일정 좌표가 없어 카카오 첫 후보 「{pick['name']}」을 골랐다")
                    + " — 이 여행에만 싣는다. 다르면 고쳐 주세요")
        rows.append({"field": f"items[{index}].place", "value": value, "method": "lookup",
                     "evidence": evidence, "needs_review": review, "note": note})
        if found.item_kind and not any(r["field"] == f"items[{index}].kind" for r in rows):
            # 「광장시장 빈대떡」 — 장소는 광장시장, 항목은 식사(설계서 §4-2). 근거는 원문 전체로 찾은 카카오 결과의 종류
            rows.append({"field": f"items[{index}].kind", "value": found.item_kind, "method": "lookup",
                         "evidence": {"source": "kakao", "rule": "food_hint", "line": items[index]["line"]},
                         "needs_review": False, "note": "뗀 말로 찾은 곳이 대부분 음식점이라 식사 일정으로 본다"})
    return rows


def _neighbours(index: int, items: list[dict[str, Any]], resolved: dict[int, Any], dated) -> list[tuple[float, float]]:
    """같은 날(날짜를 모르면 같은 원본)의 바로 앞·뒤 항목 중 좌표를 아는 것."""
    day = dated[index].value if index < len(dated) else None
    same = [i for i in range(len(items)) if i != index and (day is None or (i < len(dated) and dated[i].value == day))]
    near = []
    for pick in (max([i for i in same if i < index and _coords(resolved.get(i))], default=None),
                 min([i for i in same if i > index and _coords(resolved.get(i))], default=None)):
        if pick is not None:
            near.append(_coords(resolved[pick]))
    return near


def _coords(found) -> tuple[float, float] | None:
    if found is None or found.status != "resolved" or found.latitude is None or found.longitude is None:
        return None
    return float(found.latitude), float(found.longitude)


def _mark_for(line: int, marks: list[tuple[int, Any, Any]]) -> tuple[int | None, str | None]:
    day = date_value = None
    for mark_line, mark_day, mark_date in sorted(marks, key=lambda m: m[0]):
        if mark_line > line:
            break
        if mark_day is not None:
            day, date_value = mark_day, None
        if mark_date is not None:
            date_value = mark_date
    return day, date_value


#: 우리가 넣은 기본 별칭 — 옛 이름 · 흔한 줄임말(고객 글 → 다시 찾을 이름). 값은 매번 조회한다
SEED_ALIASES = {"남산타워": "N서울타워", "남산서울타워": "N서울타워", "서울타워": "N서울타워",
                "롯데타워": "롯데월드타워", "동대문디자인플라자": "DDP"}


def load_aliases(conn, tenant_id: str) -> dict[str, str]:
    """정규화한 원문 → 다시 찾을 이름. 고객이 고친 것이 기본값을 이긴다."""
    from .places import normalize

    out = {normalize(k): v for k, v in SEED_ALIASES.items()}
    with conn.cursor() as cur:
        cur.execute("SELECT phrase_norm, replacement FROM place_aliases WHERE tenant_id=%s", (tenant_id,))
        out.update(dict(cur.fetchall()))
    return out


def remember_alias(cur, tenant_id: str, phrase: str, replacement: str) -> None:
    """확인 화면에서 고객이 장소 이름을 고치면 (원래 글 → 고친 글)을 쌓는다. ★둘 다 고객 글이다(약관, 030 머리)."""
    from .places import normalize

    key = normalize(phrase)
    if not key or key == normalize(replacement):
        return
    cur.execute("INSERT INTO place_aliases (tenant_id, phrase_norm, phrase, replacement, source) "
                "VALUES (%s,%s,%s,%s,'customer') ON CONFLICT (tenant_id, phrase_norm) DO UPDATE "
                "SET replacement=EXCLUDED.replacement, source='customer', uses=place_aliases.uses + 1, updated_at=now()",
                (tenant_id, key, phrase[:120], replacement[:120]))


def _our_places(conn, tenant_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT place_id, name, kind, latitude, longitude FROM places WHERE tenant_id=%s "
                    "AND trip_scope IS NULL", (tenant_id,))                # ★공용만 — 다른 여행 전용 행은 안 쓴다
        return [dict(zip(("place_id", "name", "kind", "latitude", "longitude"), r)) for r in cur.fetchall()]


# ── 부품 ─────────────────────────────────────────────────────────
def _items(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`items[n].필드` 줄들을 항목으로 묶는다. 각 값은 근거를 같이 들고 간다."""
    items: dict[int, dict[str, Any]] = {}
    for claim in claims:
        field = claim["field"]
        if not field.startswith("items["):
            continue
        index = int(field[6:field.index("]")])
        name = field[field.index("]") + 2:]
        entry = items.setdefault(index, {"index": index, "fields": {}})
        entry["fields"][name] = {"value": claim["value"], "method": claim["method"],
                                 "evidence": claim["evidence"], "needs_review": claim["needs_review"],
                                 "note": claim["note"]}
    days = _day_dates(claims)
    for entry in items.values():
        line = min((f["evidence"].get("line") or 0 for f in entry["fields"].values()
                    if f["evidence"].get("line")), default=0)
        entry["line"] = line
        entry["day"], entry["date"] = _day_for(line, days)
        if "date" in entry["fields"] and entry["fields"]["date"]["value"]:
            entry["date"] = entry["fields"]["date"]["value"]        # ★날짜 해석이 정한 값이 이긴다
    return [items[k] for k in sorted(items)]


def _day_dates(claims: list[dict[str, Any]]) -> list[tuple[int, int | None, str | None]]:
    """(줄, 몇째 날, 날짜) — 일차 머리줄·날짜 줄의 위치."""
    marks: dict[int, list] = {}
    for claim in claims:
        field, line = claim["field"], claim["evidence"].get("line", 0)
        if field.startswith("days[") and field.endswith(".heading"):
            marks.setdefault(line, [None, None])[0] = claim["value"]
        elif field.startswith("days[") and field.endswith(".date"):
            marks.setdefault(line, [None, None])[1] = claim["value"]
    return [(line, day, date) for line, (day, date) in sorted(marks.items())]


def _day_for(line: int, marks: list[tuple[int, int | None, str | None]]) -> tuple[int | None, str | None]:
    day = date = None
    for mark_line, mark_day, mark_date in marks:
        if mark_line > line:
            break
        if mark_day is not None:
            day, date = mark_day, mark_date
        elif mark_date is not None:
            date = mark_date
    return day, date


def _trip_fields(claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {c["field"][5:]: {"value": c["value"], "evidence": c["evidence"]}
            for c in claims if c["field"].startswith("trip.")}


def _sources(conn, tenant_id: str, intake_id: UUID) -> list[dict[str, Any]]:
    columns = ("source_id", "position", "kind", "filename", "transcript", "transcribed", "missing_json", "seconds")
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(columns)} FROM intake_sources WHERE tenant_id=%s AND intake_id=%s "
                    "ORDER BY position", (tenant_id, intake_id))
        return [dict(zip(columns, r)) for r in cur.fetchall()]


def _stage(connect, tenant_id: str, intake_id: UUID, stage: str) -> None:
    with connect() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE trip_intakes SET stage=%s, updated_at=now() WHERE tenant_id=%s AND intake_id=%s",
                    (stage, tenant_id, intake_id))


def _fatal(connect, tenant_id: str, intake_id: UUID, code: str, detail: str) -> None:
    with connect() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE trip_intakes SET status='fatal', stage='fatal', fatal_code=%s, fatal_detail=%s, "
                    "updated_at=now() WHERE tenant_id=%s AND intake_id=%s", (code, detail, tenant_id, intake_id))


__all__ = ["IntakeConflict", "IntakeRejected", "MAX_FILES", "MAX_FILE_BYTES", "MAX_TEXT_CHARS", "STAGES", "draft",
           "edit", "mark_confirmed", "open_intake", "process", "view"]
