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
import logging
import threading
from typing import Any, Callable
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domains.travel_ops.components import settings_hook
from . import progress as progress_module
from . import review as review_module
from . import survey_answers
from .areas import areas_for
from .assemble import assemble, effective
from .line_parts import lookup_title
from .rules import read_plan
from .sources import UnsupportedSource, other_reading, sniff, to_text

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger(__name__)

#: 받는 크기 · 개수 — 우리가 고른 값(설계서 §9 「악성 파일·압축 폭탄」). 넘으면 접수하지 않는다
MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 20_000

STAGES = {"received": "받았어요", "transcribing": "글자로 옮기는 중", "reading": "일정 읽는 중",
          # ★`[2026-10-02]` 읽은 장소의 운영시간 · 휴무 · 이동을 확인하는 단계 — 목업의 「장소·운영시간 확인」(`review.py`)
          "checking": "장소·운영시간 확인", "review": "확인해 주세요", "confirmed": "등록했어요", "fatal": "읽지 못했어요"}


class _Abandoned(Exception):
    """이 일꾼이 맡은 접수가 더는 `reading` 이 아니다(실패로 확정됐거나 다른 곳에서 끝났다) — 더 쓰지 않고 물러난다."""


class _Heartbeat:
    """읽는 동안 **일꾼이 살아 있다는 표시**를 주기적으로 남긴다(접수의 갱신 시각을 올린다). 사진 받아쓰기처럼 한 번의 긴 호출 동안에는 값이 안 적혀
    갱신 시각이 멈추는데, 그때 조회가 「죽은 일꾼」으로 오해해 실패로 확정하지 않게(`reap_stalled`) 한다. 일꾼이 정말 죽으면 표시도 멎는다(스레드가 같이 죽는다).

    ★접수가 이미 `reading` 이 아니면 아무것도 쓰지 않는다 — 실패로 확정된 접수를 되살리지 않는다."""

    def __init__(self, connect: Callable[[], Any], tenant_id: str, intake_id: UUID, every: float = 20.0) -> None:
        self._connect, self._tenant, self._intake, self._every = connect, tenant_id, intake_id, every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"intake-heartbeat-{intake_id}", daemon=True)

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self._every):
            try:
                with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
                    cur.execute("UPDATE trip_intakes SET updated_at=now() WHERE tenant_id=%s AND intake_id=%s AND status='reading'",
                                (self._tenant, self._intake))
            except Exception:                                  # noqa: BLE001 — 표시 한 번을 못 남겨도 읽기는 계속한다
                log.warning("intake heartbeat failed intake=%s", self._intake, exc_info=True)


class IntakeRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def open_intake(conn, *, tenant_id: str, customer_id: UUID, text: str | None,
                files: list[tuple[str, bytes]]) -> UUID:
    """접수 한 건. `files` = [(파일 이름, 바이트)]. ★종류는 첫 바이트로 — 모르는 형식은 여기서 거절한다."""
    text = (text or "").strip()
    from .test_plans import TestPlanRejected, expand

    try:
        text, test_scenario = expand(text, tenant_id=tenant_id, has_files=bool(files))
    except TestPlanRejected as exc:
        raise IntakeRejected(exc.code, exc.message) from None
    if not text and not files:
        # ★`[2026-09-30 사용자 결정 — ui 세션 전달]` 입력칸을 비운 채 「계획 확인하기」를 눌러도 **다음 화면으로 넘어간다** —
        #   읽을 것이 없으니 곧바로 확인 화면 상태(`review`)로 만든다. 그 화면에 「읽은 일정이 없어요 — 대신 짜 드릴까요?」 짜기 칸이
        #   이미 있다. 「짜 달라는 요청」 표시(`trip.plan_request`)는 넣지 않는다 — 고객이 그 칸에서 직접 고른다.
        #   ☆전에는 422 `empty_intake` 로 거절했다. 사람 확인 · 남용 방어 한도는 부르는 쪽이 그대로 적용한다(빈 접수도 한 건).
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("INSERT INTO trip_intakes (tenant_id, customer_id, status, stage) VALUES (%s,%s,'review','review') "
                        "RETURNING intake_id", (tenant_id, customer_id))
            return cur.fetchone()[0]
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
        if test_scenario is not None:
            cur.execute("INSERT INTO intake_test_scenarios (tenant_id, intake_id, customer_id, metadata) "
                        "VALUES (%s,%s,%s,%s::jsonb)",
                        (tenant_id, intake_id, customer_id, json.dumps(test_scenario, ensure_ascii=False)))
        survey_answers.inherit(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id)
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
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            # ★읽기는 한 접수에 한 번만 — **한 번의 조건부 갱신으로 맡는다**(받은 직후 단계 `received` 인 접수만 읽는 중으로 바꾼다). 이미 읽었거나(확인 화면 · 등록 · 실패)
            #   다른 일꾼이 맡았으면 건드리지 않는다: 두 일꾼이 서로의 값을 지우거나 늦은 쪽이 등록된 접수를 확인 화면으로 되돌리는 일이 없다
            cur.execute("UPDATE trip_intakes SET stage='reading', updated_at=now() WHERE tenant_id=%s AND intake_id=%s "
                        "AND status='reading' AND stage='received' RETURNING received_at", (tenant_id, intake_id))
            claimed = cur.fetchone()
            if claimed is None:
                cur.execute("SELECT status FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s", (tenant_id, intake_id))
                row = cur.fetchone()
                return row[0] if row else "fatal"
            received_at = claimed[0]
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=KST)
            received_at = received_at.astimezone(KST)
        beat = _Heartbeat(connect, tenant_id, intake_id)
        beat.__enter__()
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
                    _hold_reading(cur, tenant_id, intake_id)          # 받아쓰는 동안 실패로 확정됐으면 결과를 저장하지 않고 물러난다
                    _store_transcript(conn, cur, source["source_id"], tenant_id, result)
                source["transcript"] = result.text
                source["differs_json"] = result.differs
        _stage(connect, tenant_id, intake_id, "reading")
        with connect() as conn:
            our_places = _our_places(conn, tenant_id)
            with conn.cursor() as cur:
                cur.execute("SELECT customer_id FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s",
                            (tenant_id, intake_id))
                owner = cur.fetchone()[0]
            aliases = load_aliases(conn, tenant_id, owner)
            areas = areas_for(conn, tenant_id)               # 지명 사전 — 이름 없는 줄(「성수 예약 식당」)을 알아보는 데 쓴다(`areas.py`, DB 만 읽는다 · 한 시간 기억)
            # ★다시 읽는 접수면 앞에 읽은 값부터 지운다(고객이 고친 값은 남긴다). 값은 이제 읽는 **동안** 바로바로 적힌다 —
            #   실시간 진행(`GET …/events`)이 읽은 줄 · 찾은 일정을 그때그때 보여 주려면 끝에 한꺼번에 적을 수 없다
            with conn.transaction(), conn.cursor() as cur:
                _hold_reading(cur, tenant_id, intake_id)              # 같은 트랜잭션에서 접수 행을 잠그고 확인한 뒤 지운다(실패 확정과 겹치지 않게)
                cur.execute("DELETE FROM intake_claims WHERE tenant_id=%s AND intake_id=%s AND revision=1 "
                            "AND method <> 'customer'", (tenant_id, intake_id))
        push = progress_module.sink(intake_id)             # ★검사가 끝나기 전에 장소 n/m · 일정 · 검사 줄 · 이동을 실시간 진행에 알린다(`progress.py`)
        for source in sources:
            def places_progress(phase, done, total, index, title, _position=source["position"]):
                push("progress", {"phase": phase, "done": done, "total": total, "current": {"id": f"{_position}-{index}", "title": title}})

            read_source(source["transcript"] or "", chat=chat, tour=tour, kakao=kakao, our_places=our_places,
                        aliases=aliases, areas=areas, dining=dining, today=(today or received_at.date()), now=received_at,
                        on_rows=_claim_sink(connect, tenant_id, intake_id, source["source_id"]), on_progress=places_progress,
                        differs=differs_by_line(source.get("differs_json")))
        # 검사 — 읽은 장소의 운영시간 · 휴무 · 이동(`review.py`). 실패해도 읽은 값은 그대로 확인 화면으로 간다(검사만 비고 이유가 남는다)
        _stage(connect, tenant_id, intake_id, "checking")
        try:
            with connect() as conn, conn.transaction():
                review_module.ensure(conn, tenant_id=tenant_id, intake_id=intake_id, revision=1,
                                     sources=_sources(conn, tenant_id, intake_id),
                                     claims=_claims(conn, tenant_id, intake_id, 1), force=True, on_event=push)
        except Exception:                                  # noqa: BLE001 — 검사 장애가 읽기를 지우지 않는다
            log.warning("intake review failed intake=%s", intake_id, exc_info=True)
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("UPDATE trip_intakes SET status='review', stage='review', updated_at=now() "
                        "WHERE tenant_id=%s AND intake_id=%s AND status='reading'", (tenant_id, intake_id))
        _dining_shadow(connect, tenant_id, intake_id, sources)
        return "review"
    except _Abandoned:                               # 읽는 사이 실패로 확정됐다(`reap_stalled`) — 값도 상태도 더 건드리지 않는다
        return "fatal"
    except (IntakeRejected, UnsupportedSource) as exc:
        code = getattr(exc, "code", "unsupported_format")
        _fatal(connect, tenant_id, intake_id, code, str(exc))
        return "fatal"
    except Exception as exc:                         # ★받아쓰기 모델 장애 등 — 숨기지 않고 남긴다
        _fatal(connect, tenant_id, intake_id, "reading_failed", f"{type(exc).__name__}: {exc}"[:300])
        return "fatal"
    finally:
        if "beat" in locals():
            beat.__exit__()
        progress_module.clear(str(intake_id))                # 끝났다 — 최종 값은 DB 에서 읽히므로 보관소는 비운다(`progress.py`)


def _dining_shadow(connect: Callable[[], Any], tenant_id: str, intake_id: UUID, sources: list[dict[str, Any]]) -> None:
    """요식 판정 에이전트 **그림자** — 확인 화면으로 넘긴 **뒤에** 규칙 검사와 나란히 판정해 요식 표에만 기록한다. `[2026-10-13 · 설계 §10 단계 5]`
    ★가드레일 `travel.dining.agent_shadow.enabled` 를 켜야 돈다(기본 꺼짐 — 꽂히지 않으면 아무것도 안 한다). 고객 결과 · 상태는 바꾸지 않고,
      실패해도 접수에 영향이 없다(경고만 남긴다)."""
    from app.domains.travel_ops.components.team_hooks import dining_ledger

    runner = dining_ledger.agent_shadow_runner()
    if runner is None:
        return
    try:
        with connect() as conn:
            payload = review_module.load(conn, tenant_id, intake_id, 1)
        if not payload:
            return
        notes = "\n".join(s.get("transcript") or "" for s in sources)
        runner(connect, tenant_id=tenant_id, intake_id=intake_id, revision=1, review=payload, notes=notes)
    except Exception:                                  # noqa: BLE001 — 그림자 장애가 접수를 건드리지 않는다
        log.warning("dining agent shadow failed intake=%s", intake_id, exc_info=True)


def reap_stalled(conn, *, tenant_id: str, intake_id: UUID) -> None:
    """뒤에서 읽던 일꾼이 죽어(서버 재시작 · 읽다 난 오류를 실패로 적지도 못한 DB 장애) **오래 갱신이 없는** 접수를 `fatal`(`stalled`)로 확정한다.

    ★이게 없으면 그 접수는 영원히 「읽는 중」으로 남는다(실시간 진행은 `stalled` 오류로 연결만 끝낼 뿐 접수 상태를 안 바꾼다). 기준은 실시간 진행의 `travel.op_stream.intake_stalled_seconds` 의 두 배. 읽는 동안 값이 적힐 때마다 갱신 시각이 오르므로(`_claim_sink`) 느리게 읽는 중인 접수는 해당되지 않는다."""
    from app.core.settings import get_guardrails

    # 실시간 진행의 「멈춤」(`stalled`, 연결만 끝내고 다시 연결할 수 있다)보다 두 배 기다린 뒤에야 **실패로 확정**한다 — 사진 받아쓰기 한 번은
    # 한 번의 긴 호출이라 그동안 갱신 시각이 오르지 않는다(실측 45~90초). 확정은 되돌릴 수 없으니 여유를 둔다
    seconds = 2 * float(get_guardrails().get("travel.op_stream.intake_stalled_seconds"))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE trip_intakes SET status='fatal', stage='fatal', fatal_code='stalled', "
                    "fatal_detail='읽던 일이 멈춰 끝내지 못했다 — 다시 올려 주세요', updated_at=now() "
                    "WHERE tenant_id=%s AND intake_id=%s AND status='reading' "
                    "AND now() - updated_at > make_interval(secs => %s)", (tenant_id, intake_id, seconds))
        if cur.rowcount:
            cur.execute("DELETE FROM intake_claims WHERE tenant_id=%s AND intake_id=%s AND revision=1 AND method <> 'customer'",
                        (tenant_id, intake_id))


def view(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID) -> dict[str, Any] | None:
    """확인 화면이 읽는 모양. ★남의 접수는 없는 것과 같다(None)."""
    reap_stalled(conn, tenant_id=tenant_id, intake_id=intake_id)
    with conn.cursor() as cur:
        cur.execute("SELECT status, stage, revision, fatal_code, fatal_detail, trip_id, received_at, "
                    "reopen_trip_id, reopen_version, reopen_frozen "
                    "FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s",
                    (tenant_id, intake_id, customer_id))
        row = cur.fetchone()
        if row is None:
            return None
        status, stage, revision, fatal_code, fatal_detail, trip_id, received_at, reopen_trip, reopen_version, frozen = row
        cur.execute("SELECT metadata FROM intake_test_scenarios WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s",
                    (tenant_id, intake_id, customer_id))
        scenario_row = cur.fetchone()
        test_scenario = scenario_row[0] if scenario_row else None
        sources = _sources(conn, tenant_id, intake_id)
        cur.execute("SELECT source_id, field, value_json, method, evidence, needs_review, note FROM intake_claims "
                    "WHERE tenant_id=%s AND intake_id=%s AND revision=%s ORDER BY created_at, claim_id",
                    (tenant_id, intake_id, revision))
        claims = [dict(zip(("source_id", "field", "value", "method", "evidence", "needs_review", "note"), r))
                  for r in cur.fetchall()]
    # ★고객이 고친 값이 같은 칸의 앞 값을 덮는다 — 화면에는 칸마다 하나만(이전 값은 이전 판에 남아 있다)
    raw_claims = claims
    claims = effective(claims)
    check = None
    review = review_error = None
    if status in ("review", "confirmed"):
        built = assemble(intake_id=str(intake_id), revision=revision, sources=sources, claims=claims)
        check = {"ready": not built.problems and bool(built.body["items"]),
                 "problems": [p.as_dict() for p in built.problems],
                 "filled": built.filled, "items": len(built.body["items"]), "title": built.body["title"],
                 "plan": built.plan}
        review, review_error = _review_of(conn, tenant_id, intake_id, revision, sources, claims)
        review = review_module.public(review, tenant_id=tenant_id)               # 내부 칸(이동 재사용 키 · 장소 행 번호)은 응답에 싣지 않는다
    out_sources = []
    for source in sources:
        mine = [c for c in claims if c["source_id"] == source["source_id"]]
        lines = (source["transcript"] or "").splitlines()
        # ★읽힌 줄은 덮이기 전의 모든 값에서 센다 — 「2026-10-15」 · 「2026-10-16」 두 날짜 줄은 같은 칸 이름(`days[?].date`)이라 `effective` 가
        #   앞 줄을 지워 첫 날짜 줄이 「안 읽은 줄」로 보였다(2026-10-03 실서버 확인)
        read_lines = {c["evidence"].get("line") for c in raw_claims if c["source_id"] == source["source_id"]}
        out_sources.append({
            "source_id": str(source["source_id"]), "kind": source["kind"], "filename": source["filename"],
            "transcribed": source["transcribed"], "missing": source["missing_json"], "differs": source.get("differs_json") or [], "seconds": source["seconds"],
            "lines": [{"no": n, "text": text, "read": n in read_lines} for n, text in enumerate(lines, start=1)],
            "items": _items(mine), "trip": _trip_fields(mine),
            # 남은 줄에 모델이 가리킨 결과 — 몇 개 받고 무엇을 버렸나(원문에 없는 인용). 모델을 안 불렀으면 None
            "reading": next((c["value"] for c in mine if c["field"] == "reading.llm"), None)})
    if review is not None and test_scenario is not None:
        review = {**review, "test_scenario": test_scenario}
    return {"intake_id": str(intake_id), "status": status, "stage": stage, "stage_label": STAGES.get(stage, stage),
            "test_scenario": test_scenario,
            "revision": revision, "fatal": {"code": fatal_code, "detail": fatal_detail} if fatal_code else None,
            "trip_id": str(trip_id) if trip_id else None, "received_at": received_at.isoformat(),
            # ★`[2026-10-13]` 등록한 여행을 다시 연 수정 접수(`reopen.py`) — 어느 여행의 몇 판인가 · 접수에 넣지 않고 그대로 두는 일정. 보통 접수는 None
            "reopen": ({"trip_id": str(reopen_trip), "version": reopen_version, "frozen_item_ids": list(frozen or [])}
                       if reopen_trip else None),
            "sources": out_sources, "check": check, "review": review, "review_error": review_error,
            # ★`[2026-10-06]` 로딩 중 질문 — 계획서만으로 알 수 없는 것만(쓰는 문항만, 최대 3). 답은 `POST …/survey` 로 한 문항씩 저장한다(`survey_answers.py`)
            "questions": survey_answers.questions(survey_answers.stored(conn, tenant_id=tenant_id, intake_id=intake_id)),
            "questions_version": survey_answers.QUESTION_SET_VERSION,
            "needs_review": [{"field": c["field"], "value": c["value"], "note": c["note"],
                              "evidence": c["evidence"]} for c in claims if c["needs_review"]]}


def _review_of(conn, tenant_id: str, intake_id: UUID, revision: int, sources: list[dict[str, Any]],
               claims: list[dict[str, Any]], *, force: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """그 판의 검사(`review.py`) — 저장된 것이 있으면 그것, 없으면 지금 계산한다. 실패하면 (None, 코드): 확인 화면은 읽은 값으로 계속 간다.
    ★DB 오류가 바깥 트랜잭션을 망가뜨리지 않게 세이브포인트 안에서 부른다(마이그레이션 040 이 안 올라간 DB 도 읽기는 된다)."""
    try:
        with conn.transaction():
            return review_module.ensure(conn, tenant_id=tenant_id, intake_id=intake_id, revision=revision,
                                        sources=sources, claims=claims, force=force,
                                        previous_revision=revision - 1 if revision > 1 else None), None
    except Exception:                                     # noqa: BLE001
        log.warning("intake review failed intake=%s revision=%s", intake_id, revision, exc_info=True)
        return None, "review_failed"


# ── 고치기 · 등록 준비 (3주차) ───────────────────────────────────────
#: 고객이 고칠 수 있는 칸. ★값은 서버가 모양을 확인한다 — 받은 글자를 그대로 믿지 않는다
#: `locked` — 고객이 「꼭 넣을 일정」으로 고정(등록되면 `detail.customer_pinned`). `removed` — true 로 빼고 false 로 되돌린다
_ITEM_FIELDS = {"title", "date", "starts_at", "ends_at", "kind", "place", "booking_no", "removed", "locked"}
#: 고객이 지도·후보·검색에서 고른 장소를 받는 범위 — 서울 하나(루트 사실표). ★우리가 고른 값(2026-09-30 「지도에서 장소 고르기」 계획과 같다)
SEOUL_LAT, SEOUL_LON = (37.4, 37.72), (126.7, 127.3)
_PICK_ORIGINS = ("tour_api", "kakao", "places", "search", "map", "customer_pick")
_CONTENT_ID = re.compile(r"^\d{1,12}$")
_TRIP_FIELDS = {"title", "party_size", "first_day", "taxi_pricing"}
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
         edits: list[dict[str, Any]], tour: Any = None, kakao: Any = None, via: str | None = None,
         dining: Any = None) -> int:
    """고객이 고친 값 → **새 판**(revision + 1). 앞 판의 값은 그대로 남는다(근거를 지우지 않는다, 설계서 §5).

    `edits` = [{"source_id", "field", "value"}]. 장소는 `{"name": "…"}` 로 받아 **다시 찾고**, 찾은 곳 하나를
    싣는다. `{"none": true}` 는 「장소 없음」(자유시간 · 이름 없는 호텔). 돌려주는 값은 새 판 번호.
    """
    if not edits:
        raise IntakeRejected("no_edits", "고칠 값이 없습니다")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT status, revision, reopen_trip_id FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s "
                    "AND customer_id=%s FOR UPDATE", (tenant_id, intake_id, customer_id))
        row = cur.fetchone()
        if row is None:
            raise LookupError("intake")
        status, current, reopened = row
        if status != "review":
            raise IntakeConflict("intake_not_editable", f"지금은 고칠 수 없습니다(상태 {status})", status=status)
        if revision != current:
            raise IntakeConflict("stale_revision", "그 사이 다른 화면에서 고쳤습니다 — 새로 불러와 주세요",
                                 current_revision=current)
        if reopened is not None and any(str(e.get("field") or "").startswith("trip.") for e in edits):
            # ★`[2026-10-13]` 수정 접수(`reopen.py`)는 일정만 고친다 — 등록한 여행의 이름 · 인원 · 첫날은 「완료」가 새 판에 싣지 않는다
            raise IntakeRejected("trip_field_locked", "등록한 여행의 이름 · 인원 · 첫날은 여기서 바꾸지 않아요")
        source_rows = _sources(conn, tenant_id, intake_id)
        sources = {str(s["source_id"]) for s in source_rows}
        our_places = None
        rows = []
        current_claims = effective(_claims(conn, tenant_id, intake_id, current))
        locked_now = {(str(c["source_id"]), _item_no(c["field"])) for c in current_claims
                      if c["field"].startswith("items[") and c["field"].endswith(".locked") and c["value"] is True}
        # ★칸 이름을 하나로 맞춘다 — `items[01].locked` · `items[ 1 ].place` 처럼 같은 항목을 다르게 적어 잠금 검사를 피하지 못하게
        edits = [{**e, "field": _canonical(str(e.get("field") or ""))} for e in edits]
        batch = [(str(e.get("source_id") or ""), str(e.get("field") or ""), e.get("value")) for e in edits]
        unlocking = {(sid, _item_no(f)) for sid, f, v in batch if f.endswith(".locked") and v is False}
        existing = {(str(c["source_id"]), _item_no(c["field"])) for c in current_claims if c["field"].startswith("items[")}
        added = _new_items(batch, existing, sources)
        locking = {(sid, _item_no(f)) for sid, f, v in batch if f.endswith(".locked") and v is True}
        for sid, field_name, value in batch:
            # 신규 항목은 다음 번호와 필수 여섯 칸을 한 번에 보낸 요청만 허용한다.
            if field_name.startswith("items[") and field_name.count("]") == 1 and (sid, _item_no(field_name)) not in existing | added:
                raise IntakeRejected("unknown_item", f"{field_name}: 이 접수에 없는 일정입니다")
            # ★고정은 그 판의 검사로만 판단한다 — 같은 요청에서 장소를 비우거나 시각을 바꾸는 것과 섞으면 고정이 바뀐 값에 걸린다. 고정은 따로 보낸다
            if field_name.startswith("items[") and not field_name.endswith(".locked") and (sid, _item_no(field_name)) in locking:
                raise IntakeRejected("lock_with_changes", f"{field_name}: 일정을 고정하는 요청에 다른 고치기를 섞을 수 없습니다 — 따로 보내 주세요")
            # ★고정한 일정은 바꾸거나 빼지 못한다 — 잠금을 먼저 풀어야 한다(같은 요청에 잠금 풀기가 있으면 함께 된다)
            if field_name.startswith("items[") and not field_name.endswith(".locked") and field_name.count("]") == 1:
                key = (sid, _item_no(field_name))
                if key in locked_now and key not in unlocking:
                    raise IntakeConflict("item_locked", "고정한 일정이라 바꿀 수 없습니다 — 잠금을 먼저 풀어 주세요",
                                         field=field_name)
        for entry in edits:
            source_id, field_name = str(entry.get("source_id") or ""), str(entry.get("field") or "")
            if field_name.startswith("items[") and source_id not in sources:
                raise IntakeRejected("unknown_source", f"{field_name}: 이 접수의 원본이 아닙니다")
            if field_name == "trip.taxi_pricing" and not settings_hook.taxi_call_product_enabled(tenant_id):
                # ★`[2026-10-14 핫픽스]` 화면에서 칸을 감췄어도 직접 보내는 요청은 여기서 막는다(관리 콘솔 스위치가 켜져야 받는다)
                raise IntakeRejected("taxi_pricing_disabled", "택시 호출 상품 고르기는 지금 쓰지 않습니다")
            value, evidence, note = _checked(field_name, entry.get("value"))
            if field_name.endswith(".locked") and value is True:
                _require_lockable(conn, tenant_id, intake_id, current, source_rows, current_claims, source_id, field_name)
            if field_name.endswith(".place") and isinstance(value, dict) and value.get("content_id"):
                value = _verified_content_id(conn, tenant_id, value)
            if field_name.endswith(".place") and isinstance(value, dict) and value.get("source") == "customer_pick":
                value = _linked_ledger_pick(conn, tenant_id, value)
            if field_name.endswith(".place") and value is not None and "latitude" not in value:
                if our_places is None:
                    our_places = _our_places(conn, tenant_id)
                typed = str(value["name"]).strip()
                # ★`[2026-10-05]` 식사 항목이면 요식 원장부터 찾는다(팀 판 — 같은 요청에서 함께 고친 종류가 먼저, 없으면 지금 판의 종류)
                kind = _item_kind(cur, tenant_id, intake_id, current, source_id, field_name, edits)
                value, evidence, note = _typed_place(value, our_places, tour, kakao, dining,
                                                     kind_hint="dining" if kind == "dining" else None)
                # 원래 읽은 이름 → 고객이 고친 이름을 별칭으로 쌓는다(다음 고객은 안 고쳐도 되게)
                cur.execute("SELECT value_json FROM intake_claims WHERE tenant_id=%s AND intake_id=%s AND revision=%s "
                            "AND source_id=%s AND field=%s ORDER BY created_at DESC LIMIT 1",
                            (tenant_id, intake_id, current, source_id, field_name[:-len("place")] + "title"))
                was = cur.fetchone()
                if was and isinstance(was[0], str):
                    remember_alias(cur, tenant_id, customer_id, was[0], typed)
            if via:
                evidence = {**evidence, "via": via}            # 전체 자동 추천이 낸 값이면 그렇게 적는다(고객이 고른 값과 구별)
            rows.append((source_id if field_name.startswith("items[") else None, field_name, value, evidence, note))
        # 직접 고른 장소도 자동 추천과 마찬가지로 이동시간을 반영한다. 저장 전에
        # 계산하므로 불가능한 배치는 claim·별칭·revision을 모두 원래대로 유지한다.
        touched = {(str(sid), _item_no(field)) for sid, field, value, _, _ in rows
                   if field.endswith(".place") and isinstance(value, dict) and value.get("latitude") is not None}
        if (touched or added) and via != "autofix":
            from . import direct_schedule
            from .assemble import collect
            prospective = [{**c, "source_id": str(c["source_id"]) if c["source_id"] is not None else None}
                           for c in current_claims] + [
                {"source_id": sid, "field": field, "value": value, "method": "customer", "evidence": evidence,
                 "needs_review": False, "note": note} for sid, field, value, evidence, note in rows]
            got = collect(sources=[{**s, "source_id": str(s["source_id"])} for s in source_rows], claims=prospective)
            if added:
                from ..planning.planner import MAX_DAYS
                dates = sorted({r["date"] for r in got.rows if r["date"]})
                if dates and (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days >= MAX_DAYS:
                    raise IntakeRejected("trip_too_long", "서울 여행은 최대 7일까지 추가할 수 있습니다")
            party = (got.trip.get("party_size") or {}).get("value")
            try:
                adjusted = direct_schedule.fit(conn, tenant_id=tenant_id, rows=got.rows, touched=touched, party_size=party,
                                               mode_picks=review_module.picked_modes(prospective))
            except direct_schedule.ScheduleConflict as exc:
                raise IntakeConflict(exc.code, str(exc)) from exc
            for change in adjusted:
                evidence = {"source": "customer", "via": "direct_schedule", "basis": change["basis"]}
                note = "장소 변경에 따라 앞뒤 이동시간을 반영해 재배치" + ("(어림 이동시간)" if change["basis"] == "estimate" else "")
                rows.append((change["source_id"], change["field"], change["value"], evidence, note))
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
        # 검사까지 같은 트랜잭션에 저장한다. 새 장소만 반영되고 이동 검사는
        # 옛 상태인 중간 판을 다른 화면에서 읽지 않게 한다.
        found, error = _review_of(conn, tenant_id, intake_id, new, source_rows,
                                  effective(_claims(conn, tenant_id, intake_id, new)), force=True)
        if found is None:
            raise IntakeConflict("schedule_check_failed", "변경 후 검사를 끝내지 못해 저장하지 않았어요. 다시 시도해 주세요.", reason=error)
    return new


def restore(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID, revision: int,
            restore_revision: int, item: tuple[UUID, int] | None = None) -> dict[str, Any]:
    """완료된 과거 판의 원값을 새 판으로 복원한다. 보호·검사 실패는 전체 롤백한다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT status, revision FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s "
                    "FOR UPDATE", (tenant_id, intake_id, customer_id))
        row = cur.fetchone()
        if row is None:
            raise LookupError("intake")
        status, current = row
        if status != "review":
            raise IntakeConflict("intake_not_editable", "지금은 전체 되돌리기를 할 수 없습니다", status=status)
        if revision != current:
            raise IntakeConflict("stale_revision", "그 사이 다른 화면에서 고쳤습니다 — 새로 불러와 주세요",
                                 current_revision=current)
        old_review = review_module.load(conn, tenant_id, intake_id, restore_revision)
        if not 1 <= restore_revision < current or not old_review or old_review.get("revision") != restore_revision:
            raise IntakeConflict("restore_revision_invalid", "복원할 완료된 이전 판을 찾지 못했어요")
        before = effective(_claims(conn, tenant_id, intake_id, restore_revision))
        now = effective(_claims(conn, tenant_id, intake_id, current))
        if not before:
            raise IntakeConflict("restore_revision_invalid", "복원할 이전 값이 없어요")
        if item is not None:
            source_id, index = item
            def selected(claim):
                return str(claim["source_id"]) == str(source_id) and claim["field"].startswith(f"items[{index}].")
            old_item = [claim for claim in before if selected(claim)]
            if not old_item or not any(selected(claim) for claim in now):
                raise IntakeConflict("restore_item_invalid", "복원할 일정을 현재 판과 이전 판에서 확인하지 못했어요")
            before = [claim for claim in now if not selected(claim)] + old_item
        source_rows = _sources(conn, tenant_id, intake_id)
        _protect_restore(now, before, source_rows)
        new = current + 1
        # 유효 값만 복사하여 같은 시각 claim의 UUID 정렬에 따라 값이 뒤집히지 않게 한다.
        # 원래 method/evidence도 유지한다. 빈 값·없던 장소를 임의의 값으로 바꾸지 않는다.
        for claim in before:
            cur.execute("INSERT INTO intake_claims (intake_id,tenant_id,revision,source_id,field,value_json,method,"
                        "evidence,needs_review,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (intake_id, tenant_id, new, claim["source_id"], claim["field"],
                         json.dumps(claim["value"], ensure_ascii=False, default=str), claim["method"],
                         json.dumps(claim["evidence"], ensure_ascii=False, default=str), claim["needs_review"], claim["note"]))
        cur.execute("UPDATE trip_intakes SET revision=%s, updated_at=now() WHERE tenant_id=%s AND intake_id=%s",
                    (new, tenant_id, intake_id))
        # 장소 재조회나 direct_schedule.fit 없이 원값 그대로 다시 검사한다.
        found, error = _review_of(conn, tenant_id, intake_id, new, source_rows, before, force=True)
        if found is None:
            raise IntakeConflict("schedule_check_failed", "되돌린 후 검사를 끝내지 못해 저장하지 않았어요", reason=error)
        return view(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id)


def _protect_restore(now, before, sources) -> None:
    """현재 보호 일정의 값·상태·실제 조립 시각을 바꾸는 역사 복원을 막는다."""
    def fields(claims):
        out = {}
        for claim in claims:
            field = claim["field"]
            if field.startswith("items["):
                out.setdefault((str(claim["source_id"]), _item_no(field)), {})[field.split("].", 1)[-1]] = claim["value"]
        return out

    current, target = fields(now), fields(before)
    from .assemble import collect
    def assembled(claims):
        return {(str(r["source_id"]), r["index"]): r for r in collect(sources=sources, claims=claims).rows}
    current_rows, target_rows = assembled(now), assembled(before)
    for key, values in current.items():
        locked = values.get("locked") is True
        booked = values.get("booked") is True or bool(values.get("booking_no"))
        if not locked and not booked:
            continue
        desired = target.get(key)
        # 잠금·예약 상태 자체를 되돌려 보호를 없애는 것도 허용하지 않는다.
        same_values = desired is not None and {**values, "removed": values.get("removed", False),
                                               "locked": values.get("locked", False)} == {
            **desired, "removed": desired.get("removed", False), "locked": desired.get("locked", False)}
        a, b = current_rows.get(key), target_rows.get(key)
        same_slot = (a is None and b is None) or (a is not None and b is not None and
                    all(a.get(f) == b.get(f) for f in ("date", "start", "end", "place", "kind", "title")))
        if not same_values or not same_slot:
            raise IntakeConflict("item_locked" if locked else "item_booked",
                                 "잠금·예약 일정이 달라져 전체 되돌리기를 저장하지 않았어요")


def _new_items(batch, existing, sources) -> set[tuple[str, int]]:
    """신규 한 항목을 기존 edits 계약으로 생성한다. 삭제 번호도 재사용하지 않는다."""
    required = {"title", "date", "starts_at", "ends_at", "kind", "place"}
    added = {(sid, _item_no(f)) for sid, f, _ in batch if f.startswith("items[") and (sid, _item_no(f)) not in existing}
    if not added:
        return set()
    if len(added) != 1:
        raise IntakeRejected("unknown_item", "새 일정은 한 번에 하나씩 추가해 주세요")
    sid, index = next(iter(added))
    next_index = max((i for s, i in existing if s == sid), default=-1) + 1
    fields = {f.rsplit(".", 1)[-1]: v for s, f, v in batch if s == sid and _item_no(f) == index}
    if sid not in sources or index != next_index or not required <= fields.keys():
        raise IntakeRejected("unknown_item", "새 일정은 다음 번호에 이름·날짜·시작·끝·종류·장소를 함께 보내 주세요")
    if len([1 for s, f, _ in batch if s == sid and _item_no(f) == index]) != len(fields):
        raise IntakeRejected("invalid_value", "새 일정의 같은 칸을 두 번 보낼 수 없습니다")
    for name in required:
        _checked(f"items[{index}].{name}", fields[name])
    if fields["starts_at"] >= fields["ends_at"]:
        raise IntakeRejected("invalid_value", "새 일정의 끝은 시작보다 늦어야 합니다")
    if fields.get("removed") is True or fields.get("locked") is True:
        raise IntakeRejected("invalid_value", "새 일정은 추가한 뒤 확인하고 고정하거나 빼 주세요")
    place = fields["place"]
    if not isinstance(place, dict) or place.get("none") is True:
        raise IntakeRejected("invalid_value", "새 일정의 장소를 먼저 골라 주세요")
    return added


def current_review(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID, revision: int | None = None,
                   force: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """(접수 상태, 현재 판의 검사). 남의 접수는 LookupError · 아직 확인 화면이 아니면 IntakeConflict · `revision` 이 낡았으면 IntakeConflict.
    후보 · 검색 · 자동 추천 · 재검증이 같은 앞문으로 들어온다."""
    with conn.cursor() as cur:
        cur.execute("SELECT status, revision FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s",
                    (tenant_id, intake_id, customer_id))
        row = cur.fetchone()
    if row is None:
        raise LookupError("intake")
    status, current = row
    if status != "review":
        raise IntakeConflict("intake_not_editable", f"지금은 고칠 수 없습니다(상태 {status})", status=status)
    if revision is not None and revision != current:
        raise IntakeConflict("stale_revision", "그 사이 다른 화면에서 고쳤습니다 — 새로 불러와 주세요", current_revision=current)
    sources = _sources(conn, tenant_id, intake_id)
    claims = effective(_claims(conn, tenant_id, intake_id, current))
    found, error = _review_of(conn, tenant_id, intake_id, current, sources, claims, force=force)
    if found is None:
        raise IntakeConflict("review_failed", "검사를 계산하지 못했습니다 — 잠시 뒤 다시 시도해 주세요", reason=error)
    return {"status": status, "revision": current}, found


def revalidate(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID, revision: int) -> dict[str, Any]:
    """**새 판을 만들지 않고** 같은 판의 검사를 처음부터 다시 계산해 그 판의 저장된 검사를 바꾼다 — 운영시간 표가 새벽에 바뀌었거나 이동 계산기 답이 달라졌을 수 있다.
    돌려주는 것은 `view` 와 같은 모양. 이동은 앞 판의 값을 재사용하지 않고 다시 잰다."""
    with conn.transaction():
        current_review(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id, revision=revision, force=True)
    return view(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id)


class _DryRunRollback(Exception):
    """미리 보기를 읽은 뒤 저장 구간을 되돌리려고 던지는 것 — 오류가 아니다."""


def autofix(conn, *, tenant_id: str, customer_id: UUID, intake_id: UUID, revision: int, tour: Any = None,
            kakao: Any = None, dry_run: bool = False, dining: Any = None) -> dict[str, Any]:
    """**전체 자동 추천** — 확인이 필요한 일정을 검증한 대체 일정(장소 · 시각)으로 한 번에 바꾼다(`autofix.py`).
    바꿀 것이 있으면 `edits` 와 같은 길로 **새 판** 하나가 된다. 없으면 판을 만들지 않고 이유(`kept`)만 돌려준다.

    ★`dry_run` `[2026-10-03 ui 세션 요청서 3번]` — **저장하지 않고** 바뀔 모습만 돌려준다. 실제 적용과 **같은 길**(`edit`)로 새 판을 만들어 `view` 를 읽은 뒤 그 저장 구간을
      되돌린다(세이브포인트 롤백) — 그래서 미리 보기의 검사가 적용한 결과와 같다(따로 흉내 내는 계산이 아니다). 응답의 `view.preview` 가 참이고 `view.revision` 은 적용하면 생길 판 번호다.
      바꿀 것이 없으면 현재 모습 그대로(`preview` 없음)."""
    from . import autofix as autofix_module

    _, found = current_review(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id, revision=revision)
    got = autofix_module.plan(conn, tenant_id=tenant_id, review=found, kakao=kakao)
    edits = [{"source_id": c["source_id"], "field": f"items[{c['index']}].{e['field']}", "value": e["value"]}
             for c in got["changes"] for e in c["edits"]]
    new = revision
    changed = [{k: v for k, v in c.items() if k != "edits"} for c in got["changes"]]
    if edits and dry_run:
        preview: dict[str, Any] | None = None
        try:
            with conn.transaction():                       # 세이브포인트 — 안에서 만든 새 판 · 저장된 검사가 나갈 때 사라진다
                edit(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id, revision=revision, edits=edits,
                     tour=tour, kakao=kakao, via="autofix", dining=dining)
                preview = view(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id)
                raise _DryRunRollback()
        except _DryRunRollback:
            pass
        if preview is not None:
            preview["preview"] = True
        return {"applied": False, "dry_run": True, "revision": revision, "changed": changed, "kept": got["kept"], "view": preview}
    if edits:
        new = edit(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id, revision=revision, edits=edits,
                   tour=tour, kakao=kakao, via="autofix", dining=dining)
    return {"applied": bool(edits), "dry_run": dry_run, "revision": new, "changed": changed, "kept": got["kept"],
            **({"view": view(conn, tenant_id=tenant_id, customer_id=customer_id, intake_id=intake_id)} if dry_run else {})}


#: 고객이 보낸 관광공사 번호가 그 좌표의 장소 것으로 인정되는 거리(m) — `place_info.CATALOG_MATCH_M` 와 같은 값
CONTENT_ID_MATCH_M = 500


def _verified_content_id(conn, tenant_id: str, value: dict[str, Any]) -> dict[str, Any]:
    """고객이 후보·검색에서 고른 값의 관광공사 번호(`content_id`)를 **서버의 관광공사 목록과 대조**한다 — 그 번호의 장소가 보낸 좌표 500m 안이면 쓰고,
    아니면 번호만 뗀다(운영시간 조회가 다른 장소의 값으로 새지 않게. 틀린 번호를 붙여 내 일정에 남의 운영시간을 끌어오는 것을 막는다)."""
    from .hours import _tenants
    from .places import distance_m

    with conn.cursor() as cur:
        cur.execute("SELECT latitude, longitude FROM place_catalog WHERE tenant_id = ANY(%s) AND source='tour_api' AND content_id=%s LIMIT 1",
                    (_tenants(tenant_id), str(value["content_id"])))
        row = cur.fetchone()
    ok = (bool(row) and row[0] is not None and row[1] is not None
          and distance_m(float(row[0]), float(row[1]), float(value["latitude"]), float(value["longitude"])) <= CONTENT_ID_MATCH_M)
    return value if ok else {**value, "content_id": None, "content_type_id": None}


#: 고른 식당을 공용 원장 식당 행과 같은 곳으로 보는 거리 — 후보는 그 행의 좌표를 그대로 돌려보내므로 넉넉할 필요가 없다(우리가 고른 값)
LEDGER_PICK_MATCH_M = 50


def _linked_ledger_pick(conn, tenant_id: str, value: dict[str, Any]) -> dict[str, Any]:
    """고객이 후보 · 검색에서 고른 식당을 **요식 원장 가게 번호**(`dining_place_uid`)와 잇는다. `[2026-10-10]`

    ★왜. 원장 후보(`candidates._ledger_places`)는 `ref: place:{id}` 만 싣고, 웹은 고른 곳을 이름 · 좌표로만 돌려보낸다. 서버는 `place_id` 를
      받지 않으므로(남의 여행 전용 행을 가리킬 수 있다) 고른 식당이 원장과 끊겨 확인 화면 · 등록 판정이 「모름」이 됐다.
      결함: `wiki/records/reports/debugs/2026-10-10_고른_원장식당이_원장과_끊긴다.md`
    ★고객이 보낸 번호를 믿지 않고 서버가 찾는다 — **공용**(`trip_scope IS NULL`) 원장 식당 행 중 이름이 **정확히 같고** 좌표가
      `LEDGER_PICK_MATCH_M` 안인 곳이 **한 가게**일 때만 잇는다. 둘 이상이거나 없으면 전처럼 둔다(고르지 않는다 — 다른 가게의 영업시간으로
      판정하는 것이 가장 나쁘다). 활동으로 고른 곳은 건드리지 않는다.
    """
    if value.get("kind") not in (None, "dining"):
        return value
    from .hours import _tenants
    from .places import distance_m

    try:
        with conn.transaction(), conn.cursor() as cur:       # 세이브포인트 — 원장 열이 없는 DB 에서도 바깥 트랜잭션을 깨지 않는다
            cur.execute("SELECT latitude, longitude, attributes->>'dining_place_uid' FROM places "
                        "WHERE tenant_id = ANY(%s) AND source_name = 'dining_ledger' AND trip_scope IS NULL AND kind = 'dining' "
                        "AND name = %s AND attributes ? 'dining_place_uid'", (_tenants(tenant_id), str(value["name"])))
            rows = cur.fetchall()
    except Exception as exc:                                  # noqa: BLE001 — 원장 열이 없는 DB(시험 · 다른 조립)면 잇지 않는다
        if type(exc).__name__ not in ("UndefinedColumn", "UndefinedTable"):
            raise
        return value
    uids = {str(uid) for lat, lon, uid in rows
            if uid and lat is not None and lon is not None
            and distance_m(float(lat), float(lon), float(value["latitude"]), float(value["longitude"])) <= LEDGER_PICK_MATCH_M}
    return {**value, "dining_place_uid": uids.pop()} if len(uids) == 1 else value


def _canonical(field_name: str) -> str:
    """`items[ 01 ].place` → `items[1].place`. 항목 칸이 아니거나 번호를 못 읽으면 그대로(틀린 칸은 `_checked` 가 거절한다)."""
    if not field_name.startswith("items[") or "]" not in field_name:
        return field_name
    try:
        number = int(field_name[6:field_name.index("]")])
    except ValueError:
        return field_name
    return f"items[{number}]{field_name[field_name.index(']') + 1:]}"


def _item_no(field_name: str) -> int:
    """`items[3].place` → 3. 모양이 틀리면 -1(이 함수는 비교용이고, 틀린 칸은 `_checked` 가 거절한다)."""
    try:
        return int(field_name[6:field_name.index("]")])
    except ValueError:
        return -1


def _require_lockable(conn, tenant_id: str, intake_id: UUID, revision: int, sources: list[dict[str, Any]],
                      claims: list[dict[str, Any]], source_id: str, field_name: str) -> None:
    """확인이 필요한 일정은 고정하지 않는다 — 「꼭 넣을 일정」은 장소가 정해지고 검사를 통과한 것만(목업: 먼저 고쳐야 고정할 수 있어요)."""
    found, _ = _review_of(conn, tenant_id, intake_id, revision, sources, claims)
    item = next((i for i in (found or {}).get("items", [])
                 if i["source_id"] == source_id and i["index"] == _item_no(field_name)), None)
    if item is None:
        raise IntakeRejected("lock_needs_confirmed_item", f"{field_name}: 이 일정을 찾지 못했습니다")
    if not item["can_lock"]:
        raise IntakeRejected("lock_needs_confirmed_item", "확인이 필요한 일정은 먼저 고쳐야 고정할 수 있습니다")


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
        if name == "taxi_pricing":
            from .taxi_pricing import normalize
            try:
                value = normalize(value)
            except ValueError as exc:
                raise IntakeRejected("invalid_taxi_pricing", str(exc)) from exc
        elif name == "party_size":
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
        if value is not True and value is not False:
            raise IntakeRejected("invalid_value", f"{field_name}: 빼려면 true, 되돌리려면 false 를 보냅니다")
    elif name == "locked":
        if value is not True and value is not False:
            raise IntakeRejected("invalid_value", f"{field_name}: 고정은 true, 풀기는 false 입니다")
    elif name == "place":
        if isinstance(value, dict) and value.get("none") is True:
            return None, {"source": "customer", "none": True}, "고객이 「장소 없음」으로 두었다"
        if isinstance(value, dict) and ("latitude" in value or "longitude" in value):
            picked = _checked_pick(field_name, value)
            return picked, {"source": "customer", "picked_from": picked["origin"], "query": picked["name"]}, "고객이 직접 고른 장소"
        if not isinstance(value, dict) or not str(value.get("name") or "").strip():
            raise IntakeRejected("invalid_value", f'{field_name}: 장소는 {{"name": …}} · {{"name", "latitude", "longitude", "source"}} · '
                                                  f'{{"none": true}} 중 하나입니다')
    elif not isinstance(value, str) or not value.strip() or len(value) > 80:
        raise IntakeRejected("invalid_value", f"{field_name}: 1~80자 글이어야 합니다")
    return value, evidence, None


def _checked_pick(field_name: str, value: dict[str, Any]) -> dict[str, Any]:
    """고객이 후보 · 검색 · 지도에서 **고른** 장소 — `{name, latitude, longitude, source, origin?, kind?, content_id?}`.

    서버는 다시 찾지 않고 모양만 확인한다(후보 조회 결과는 저장하지 않으므로 서버가 다시 확인할 수 없다 — 고객이 본 것을 그대로 받는다).
    ★관광공사 번호와 좌표의 대조는 DB 가 필요해 여기서 못 한다 — `edit()` 가 `_verified_content_id` 로 한다.
    ★출처는 늘 `customer_pick` 으로 적는다 — 고객이 보낸 값은 관광공사 · 카카오 값이 아니다(여행 전용 장소 행으로만 들어간다, `trip_api.EXTERNAL_PLACE_SOURCES`).
    ★`place_id` 는 받지 않는다(남의 여행 전용 장소 행을 가리킬 수 있다). 관광공사 번호(`content_id`)는 숫자만 — 운영시간 표를 찾는 데만 쓴다."""
    name = str(value.get("name") or "").strip()
    if not 1 <= len(name) <= 80:
        raise IntakeRejected("invalid_value", f"{field_name}: 장소 이름은 1~80자입니다")
    coords = []
    for key in ("latitude", "longitude"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or number != number or abs(number) == float("inf"):
            raise IntakeRejected("invalid_value", f"{field_name}: {key} 는 숫자입니다")
        coords.append(float(number))
    if not (SEOUL_LAT[0] <= coords[0] <= SEOUL_LAT[1] and SEOUL_LON[0] <= coords[1] <= SEOUL_LON[1]):
        raise IntakeRejected("place_out_of_seoul", f"{field_name}: 서울 안의 장소만 고를 수 있습니다")
    source = value.get("source", "customer_pick")
    if source not in _PICK_ORIGINS:
        raise IntakeRejected("invalid_value", f"{field_name}: source 는 {', '.join(_PICK_ORIGINS)} 중 하나입니다")
    kind = value.get("kind")
    content_id = value.get("content_id")
    if content_id is not None and not _CONTENT_ID.match(str(content_id)):
        raise IntakeRejected("invalid_value", f"{field_name}: content_id 는 숫자입니다")
    category = str(value.get("category") or "").strip()[:60] or None
    return {"name": name, "kind": kind if kind in ("activity", "dining") else None, "latitude": coords[0],
            "longitude": coords[1], "place_id": None, "content_id": str(content_id) if content_id else None,
            "content_type_id": None, "category": category, "source": "customer_pick",
            "origin": source if source != "customer_pick" else str(value.get("origin") or "search")
            if str(value.get("origin") or "search") in _PICK_ORIGINS else "search"}


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
                "place_id": found.place_id, "content_id": found.content_id,
                "content_type_id": found.content_type_id, "source": found.evidence()["source"],
                **({"dining_place_uid": found.dining_place_uid} if found.dining_place_uid else {})}
    return resolved, {**found.evidence(), "typed": name}, found.note


# ── 한 원본 읽기: 규칙 → 남은 줄 → 날짜 → 장소 ─────────────────────
def _lookup_title(title: str, aliases: dict[str, str] | None) -> str:
    """장소를 찾을 말 — 체크인 · 체크아웃 동작 말을 뺀다(`line_parts.lookup_title`). 고객이 이 글을 다른 이름으로 고친 적이 있으면(별칭) 옛 길이 원문으로 찾아야 하므로 그대로 둔다."""
    from .places import normalize

    return title if normalize(title) in (aliases or {}) else lookup_title(title)


def read_source(text: str, *, chat: Any = None, tour: Any = None, kakao: Any = None,
                our_places: list[dict[str, Any]] | None = None, today: date, now: datetime | None = None,
                aliases: dict[str, str] | None = None, areas: Any = None, dining: Any = None,
                on_rows: Callable[[list[dict[str, Any]]], None] | None = None,
                on_progress: Callable[[str, int, int, int, str | None], None] | None = None,
                differs: dict[int, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """읽은 값 줄들(`intake_claims` 한 행 = 한 dict). ★값마다 방법(rule·llm_span·lookup)과 근거가 붙는다.

    `on_rows` — 값 줄이 **생길 때마다 묶음으로** 알려 준다(규칙으로 읽은 줄 → 모델이 가리킨 줄 → 날짜 → 장소가 하나씩 찾아질 때마다).
    실시간 진행이 읽은 줄 · 일정 · 장소를 그때그때 보이게 하려는 것이다. 돌려주는 목록은 전과 같다(알린 줄을 두 번 알리지 않는다).

    `on_progress(phase, done, total, index, title)` `[2026-10-03 ui 세션 요청서 2번]` — **장소 찾기가 한 일정씩 끝날 때마다** 「장소 n/m」을 알린다(`phase` 는 늘 `places`, `index` 는 항목 번호, `title` 은 원문 조각).
    ★찾았든 못 찾았든 **그 일정의 장소 값이 정해지면** 센다 — 이름이 특정된 것은 찾는 즉시, 모호한 것은 앞뒤가 다 찾아진 뒤 가까운 곳을 고른 때.

    `areas` `[2026-10-03 ui 세션 요청서 「장소 해석 개선」]` — 지명 사전(`areas.py`). 이름 없이 **종류 + 지역만** 적은 줄(「성수 예약 식당」)을 알아보는 데 쓴다(`line_parts.py`) —
    그런 줄은 가게 이름으로 **찾지 않는다**(없는 가게를 찾아 글자를 줄여 가던 것이 틀린 장소와 엉뚱한 후보의 뿌리였다). 사전이 없으면(None) 지역 말은 이름 조각으로 남고 종류 · 끼니만 있는 줄만 알아본다.

    `differs` `[2026-10-06 사용자 지시 — 값 변조 줄이기]` — 사진 · 스캔 받아쓰기가 같은 줄을 **두 번 다르게 읽은 곳**(`sources.differing_lines`). 그 줄에서 읽은 제목 · 예약번호는 「확인 필요」로 표시한다 —
    고객이 확인하기 전에는 확정 값으로 쓰지 않는다(`flag_differing`).

    `dining` `[2026-10-05]` — 요식 원장 이름 조회(`dining/place_lookup.py`, 관광공사 자리와 같은 계약 `find` + `misses`). 식사 항목은 이것이 먼저, 그 밖의 항목은 관광공사 자리 뒤다(`places.resolve`). 없으면(None) 건너뛴다."""
    from .dates import resolve_dates
    from .line_parts import parse as parse_line
    from .llm_spans import label_lines
    from .places import _kind_hits, nearest, normalize, resolve

    read = read_plan(text)
    rows = [claim.as_dict() for claim in read.claims]
    sent: set[int] = set()
    flushed = 0

    def send(batch: list[dict[str, Any]]) -> None:
        batch = [r for r in batch if id(r) not in sent]
        sent.update(id(r) for r in batch)
        flag_differing(batch, differs)
        if on_rows is not None and batch:
            on_rows(batch)

    def flush() -> None:
        nonlocal flushed
        send(rows[flushed:])
        flushed = len(rows)

    flush()                                       # 규칙으로 읽은 줄 — 이 순간 「읽은 줄」이 생긴다
    marks = [(c.span.line, c.value if c.field.endswith(".heading") else None,
              c.value if c.field.endswith(".date") else None) for c in read.claims if c.field.startswith("days[")]
    items: list[dict[str, Any]] = []
    for item in read.items:
        items.append({"line": (item.title or item.booking_no).line if (item.title or item.booking_no) else 0,
                      "day": item.day, "date": item.date, "title": item.title.text if item.title else None,
                      "meal": any(r["field"] == f"items[{len(items)}].kind" for r in rows), "booked": item.booked})

    # 남은 줄 — 모델은 가리키기만. 실패하면 이 단계만 건너뛴다(그 줄은 남은 줄로 보인다)
    if chat is not None and read.unread_lines:
        try:
            _, span_items, report = label_lines(read.lines, read.unread_lines, chat, now=now)
        except Exception as exc:                      # noqa: BLE001 — 모델 장애는 기록하고 넘어간다
            span_items, report = [], {"error": f"{type(exc).__name__}: {exc}"[:200]}
        rows.append({"field": "reading.llm", "value": report, "method": "llm_span",
                     "evidence": {"source": "model", "lines": read.unread_lines}, "needs_review": False,
                     "note": "남은 줄에 모델이 가리킨 결과(원문에 없는 인용은 버렸다)"})
        for span_item in span_items:
            index = len(items)
            day, date_mark = _mark_for(span_item.line, marks)
            rows.append({"field": f"items[{index}].title", "value": span_item.title.text, "method": "llm_span",
                         "evidence": {"source": "text", **span_item.title.as_dict(),
                                      "segment": span_item.segment.as_dict()}, "needs_review": True,
                         "note": "모델이 가리킨 원문 조각"})
            time_claim = span_item.time_claim          # ★한 줄에 장소가 여럿이면 시각도 장소마다 다르다(`llm_spans`)
            if time_claim is not None:
                rows.append({**time_claim.as_dict(), "field": f"items[{index}].starts_at"})
            if span_item.meal:
                rows.append({"field": f"items[{index}].kind", "value": "dining", "method": "llm_span",
                             "evidence": {"source": "text", "line": span_item.line}, "needs_review": False,
                             "note": f"끼니 말({span_item.meal})"})
            items.append({"line": span_item.line, "day": day, "date": span_item.date or date_mark,
                          "title": span_item.title.text, "meal": bool(span_item.meal), "booked": None,
                          "segment": span_item.segment.as_dict()})
        if report.get("plan_request"):
            rows.append({"field": "trip.plan_request", "value": True, "method": "llm_span",
                         "evidence": {"source": "text"}, "needs_review": False,
                         "note": "「일정 짜 줘」— 확인 화면에서 일정 생성기로 넘긴다"})

    flush()
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
    flush()

    # 장소 — 항목마다 하나(원문이 가리킨 만큼만)
    resolved: dict[int, Any] = {}
    early: dict[int, list[dict[str, Any]]] = {}
    nameless: set[int] = set()
    titled, placed = sum(1 for item in items if item["title"]), 0
    for index, item in enumerate(items):
        if item["title"]:
            # ★이름 없는 줄(종류 + 지역만) — 가게 이름으로 찾지 않는다. 고객이 앞서 이 글을 다른 이름으로 고친 적이 있으면(별칭) 그 이름으로 다시 찾는 옛 길이 먼저다
            # ★`[2026-10-15 숙박예약 점검 LB-01]` 장소는 체크인 · 체크아웃 말을 뺀 말로 찾는다(숙소 줄) — 고객이 이 글을 다른 이름으로 고친 적이 있으면(별칭) 원문 그대로
            query = _lookup_title(item["title"], aliases)
            parts = parse_line(query, areas=areas, kind_hint="dining" if item["meal"] else None)
            if parts.nameless and normalize(item["title"]) not in (aliases or {}):
                nameless.add(index)
                early[index] = _nameless_rows(index, parts, item, rows)
                send(early[index])
                placed += 1
                if on_progress is not None:
                    on_progress("places", placed, titled, index, item["title"])
                continue
            resolved[index] = resolve(query, our_places=our_places or [], tour=tour, kakao=kakao,
                                      kind_hint="dining" if item["meal"] else None, aliases=aliases, dining=dining,
                                      near=_near_hint(index, items, resolved, dated))
            if resolved[index].status == "resolved":
                # 이름이 특정된 곳은 앞뒤 일정을 볼 필요가 없다 — 찾은 즉시 알린다(실시간 진행에서 한 곳씩 나타난다)
                early[index] = _place_rows(index, resolved[index], items=items, resolved=resolved, dated=dated, rows=rows,
                                           kakao=kakao, nearest=nearest, kind_hits=_kind_hits)
                send(early[index])
                placed += 1
                if on_progress is not None:
                    on_progress("places", placed, titled, index, item["title"])
    # ★`[2026-10-07 팀]` 체인인데 기준 위치(지점명 · 앞뒤 일정)가 없어 미룬 항목(`chain_deferred`)은 다른 장소가 정해진 뒤 한 번 더 찾는다
    for index, found in list(resolved.items()):
        if getattr(found, "chain_deferred", False) and index not in early:
            point = _near_hint(index, items, resolved, dated)
            if point is not None:
                resolved[index] = resolve(_lookup_title(items[index]["title"], aliases), our_places=our_places or [], tour=tour, kakao=kakao,
                                          kind_hint="dining" if items[index]["meal"] else None, aliases=aliases,
                                          dining=dining, near=point)
    for index in sorted(set(resolved) | nameless):
        if index in early:
            rows.extend(early[index])
            continue
        found = resolved[index]
        new = _place_rows(index, found, items=items, resolved=resolved, dated=dated, rows=rows, kakao=kakao,
                          nearest=nearest, kind_hits=_kind_hits)
        rows.extend(new)
        send(new)
        placed += 1
        if on_progress is not None:
            on_progress("places", placed, titled, index, items[index]["title"])
    flush()
    return rows


def flag_differing(rows: list[dict[str, Any]], differs: dict[int, dict[str, Any]] | None) -> None:
    """읽은 값 줄(제목 · 예약번호) 가운데 **받아쓰기가 두 번 다르게 읽은 줄**에서 나온 것을 「확인 필요」로 고친다(제자리). 근거에 두 읽기를 남긴다.

    ★값은 바꾸지 않는다 — 어느 쪽이 맞는지 서버는 모른다. 확인 화면이 두 읽기를 보이고 고객이 고른다."""
    if not differs:
        return
    for row in rows:
        field = str(row.get("field") or "")
        if not (field.endswith(".title") or field.endswith(".booking_no")):
            continue
        evidence = row.get("evidence") or {}
        unlike = differs.get(evidence.get("line"))
        if unlike is None:
            continue
        row["needs_review"] = True
        # ★`[2026-10-07 uiux 요청]` `other_value` — 반쪽 받아쓰기에서는 **이 값이 무엇으로 읽혔나**(줄 전체가 아니라 값만). 화면이 「지금 값 / 다른 읽기」 단추를 그리고 고른 값을 보낸다. 못 정하면 `None`
        row["evidence"] = {**evidence, "transcription_differs": {"text": unlike.get("text"), "other": unlike.get("other"), "ratio": unlike.get("ratio"),
                                                                  "other_value": other_reading(str(row.get("value") or ""), str(unlike.get("text") or ""),
                                                                                               str(unlike.get("other") or ""))}}
        note = f"사진에서 이 줄을 두 가지로 읽었어요 — 「{unlike.get('text')}」 / 「{unlike.get('other')}」 · 원본과 맞는 쪽을 골라 주세요"
        row["note"] = f"{row['note']} · {note}" if row.get("note") else note


def _place_rows(index: int, found: Any, *, items: list[dict[str, Any]], resolved: dict[int, Any], dated: Any,
                rows: list[dict[str, Any]], kakao: Any, nearest: Any, kind_hits: Any) -> list[dict[str, Any]]:
    """찾은 결과 하나 → 그 항목의 장소 값 줄(+ 종류 줄). 이름이 특정하지 않으면 종류가 맞는 후보 중 앞뒤 일정에 가장 가까운 곳을 고른다."""
    out: list[dict[str, Any]] = []
    evidence = found.evidence()
    value = None
    note, review = found.note, found.needs_review
    if found.status == "resolved":
        value = {"name": found.name, "kind": found.kind, "latitude": found.latitude,
                 "longitude": found.longitude, "place_id": found.place_id, "content_id": found.content_id,
                 "content_type_id": found.content_type_id,
                 "source": evidence["source"],
                 **({"dining_place_uid": found.dining_place_uid} if found.dining_place_uid else {})}
    elif found.candidates:
        # ★설계서 §4-2 — 이름이 특정하지 않으면 종류가 맞는 후보 중 **같은 날 앞뒤 일정에 가장 가까운 곳** 하나.
        #   선택지를 나열하지 않는다. 고른 이유(거리)를 근거에 남기고 확인을 받는다
        near = _neighbours(index, items, resolved, dated)
        pool = list(found.candidates)
        if near and kakao is not None:
            # 앞뒤 일정 가운데에서 **거리순**으로 한 번 더 찾는다 — 서울 전역 상위 5곳만으로는 14km 밖을 골랐다(실측)
            mid = (sum(p[0] for p in near) / len(near), sum(p[1] for p in near) / len(near))
            query = lookup_title(items[index]["title"])
            around = kakao.search(query, near=mid) or []
            seen = {c["name"] for c in pool}
            pool += [h for h in kind_hits(query, around) if h["name"] not in seen]
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
    out.append({"field": f"items[{index}].place", "value": value, "method": "lookup",
                "evidence": evidence, "needs_review": review, "note": note})
    if found.item_kind and not any(r["field"] == f"items[{index}].kind" for r in rows):
        # 「광장시장 빈대떡」 — 장소는 광장시장, 항목은 식사(설계서 §4-2). 근거는 원문 전체로 찾은 카카오 결과의 종류
        out.append({"field": f"items[{index}].kind", "value": found.item_kind, "method": "lookup",
                    "evidence": {"source": "kakao", "rule": "food_hint", "line": items[index]["line"]},
                    "needs_review": False, "note": "뗀 말로 찾은 곳이 대부분 음식점이라 식사 일정으로 본다"})
    return out


def _nameless_rows(index: int, parts: Any, item: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """이름 없는 줄(종류 + 지역만)의 값 줄 — **장소 값은 비워 두고**(없는 가게를 지어내지 않는다) 뜻의 조각을 근거에 남긴다. 고르는 것은 고객(또는 전체 자동 추천)이다.

    ★예약: 줄이 「예약 있음」이면(칸 또는 줄 글자의 「예약한」) 후보를 권하지 않고 이름만 묻는 길이다(`needs_name`). 예약을 모르면 후보를 보이며 「이미 예약하셨나요?」를 묻는다.
    ★일정 종류(`kind`)도 여기서 정한다 — 식당 · 끼니 말이면 식사다(「성수 예약 식당」이 활동으로 읽혀 활동 후보가 권해졌다). 규칙이 이미 정한 종류는 덮지 않는다."""
    booked = item.get("booked") if item.get("booked") is not None else parts.booked
    public = parts.public()
    where = f"{public['area']['name']} 지역의 " if public["area"] else ""
    evidence = {"source": "line_parts", "method": "nameless", "line": item["line"], "query": item["title"], "parts": public,
                "booked": booked}
    out = [{"field": f"items[{index}].place", "value": None, "method": "lookup", "evidence": evidence, "needs_review": True,
            "note": f"이름이 아닌 말(「{item['title']}」)이라 가게를 찾지 않았다 — {where}{public['label']}을 고르게 한다"
                    + (" · 예약이 있다고 적혀 있어 이름을 묻는다" if booked is True else "")}]
    if public["kind"] and not any(r["field"] == f"items[{index}].kind" for r in rows):
        out.append({"field": f"items[{index}].kind", "value": public["kind"], "method": "lookup",
                    "evidence": {"source": "rule", "rule": "line_parts", "line": item["line"]}, "needs_review": False,
                    "note": (f"「{public['label']}」에서 하는 {public['meal'] or '끼니'} 말이 있어 식사 일정으로 본다" if public.get("lodging_meal")
                             else f"「{public['label']}」을 뜻하는 말이라 {'식사' if public['kind'] == 'dining' else '활동'} 일정으로 본다")})
    return out


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


def _cluster_centre(found) -> tuple[float, float] | None:
    """정하지 못했지만 후보가 서로 붙어 있으면(홍대입구역 출구들) 그 가운데 — 어느 후보든 위치가 거의 같다. `[2026-10-07 팀]`
    ☆전에는 「홍대입구역 → 교촌치킨」의 홍대입구역이 기준이 되지 못해 종로 지점을 골랐다(5.7km)."""
    from .chain_pick import CLUSTER_M, centre, spread_m

    if found is None or found.status == "resolved" or not getattr(found, "candidates", None):
        return None
    points = [(float(c["latitude"]), float(c["longitude"])) for c in found.candidates
              if c.get("latitude") is not None and c.get("longitude") is not None]
    # 관련도 1위 후보에서 CLUSTER_M 안에 절반 이상이 모였나 — 「홍대입구역 사거리」 하나가 섞여 서로 가장 먼 거리는 555m 였다
    tight = [point for point in points if spread_m([points[0], point]) <= CLUSTER_M] if points else []
    return centre(tight) if tight and len(tight) * 2 >= len(points) else None


def _near_hint(index: int, items: list[dict[str, Any]], resolved: dict[int, Any], dated) -> tuple[float, float] | None:
    """체인 지점 · 같은 이름 여럿을 고르는 **기준 좌표** — 같은 날(날짜를 모르면 같은 원본) 가장 가까운 앞 항목, 없으면 뒤 항목의
    좌표(정하지 못한 항목은 후보가 붙어 있을 때 그 가운데). 없으면 None — 부르는 쪽이 고르지 않고 「어느 곳인가요?」로 둔다. `[2026-10-07 팀 포팅]`"""
    day = dated[index].value if index < len(dated) else None
    same = [i for i in range(len(items)) if i != index and (day is None or (i < len(dated) and dated[i].value == day))]
    for i in sorted([i for i in same if i < index], reverse=True) + sorted(i for i in same if i > index):
        found = resolved.get(i)
        point = _coords(found) or _cluster_centre(found)
        if point is not None:
            return point
    return None


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


def load_aliases(conn, tenant_id: str, customer_id: Any) -> dict[str, str]:
    """정규화한 원문 → 다시 찾을 이름. 고객이 고친 것이 기본값을 이긴다.

    ★`[2026-09-30 사용자 확인 「명백한 버그」]` **누구의 별칭인지 가른다.** 기본값(`source='seed'`)만 모든 고객에게 쓰고, 고객이 고친 것은
      **그 고객의 다음 접수에만** 쓴다(마이그레이션 038). ☆전에는 테넌트 전체가 공유해 한 고객의 「이촌동 점심 식당 → 엘 샌드위치」가
      다른 고객의 같은 원문에 자동으로 적용됐다(고른 값은 그 여행에 대한 그 사람의 선택이지 이름 교정이 아니다 — 데이터 격리 문제).
      누가 고쳤는지 모르는 옛 행(`customer_id` 없음)은 아무에게도 쓰지 않는다."""
    from .places import normalize

    out = {normalize(k): v for k, v in SEED_ALIASES.items()}
    with conn.cursor() as cur:
        cur.execute("SELECT phrase_norm, replacement FROM place_aliases WHERE tenant_id=%s AND "
                    "(source='seed' OR (source='customer' AND customer_id = %s)) "
                    "ORDER BY (source='customer')", (tenant_id, customer_id))
        out.update(dict(cur.fetchall()))
    return out


def remember_alias(cur, tenant_id: str, customer_id: Any, phrase: str, replacement: str) -> None:
    """확인 화면에서 고객이 장소 이름을 고치면 (원래 글 → 고친 글)을 **그 고객의 것으로** 쌓는다. ★둘 다 고객 글이다(약관, 030 머리).
    다른 고객에게는 쓰이지 않는다(`load_aliases`)."""
    from .places import normalize

    key = normalize(phrase)
    if not key or key == normalize(replacement):
        return
    cur.execute("INSERT INTO place_aliases (tenant_id, phrase_norm, phrase, replacement, source, customer_id) "
                "VALUES (%s,%s,%s,%s,'customer',%s) "
                "ON CONFLICT (tenant_id, phrase_norm, (COALESCE(customer_id, '00000000-0000-0000-0000-000000000000'::uuid))) "
                "DO UPDATE SET replacement=EXCLUDED.replacement, source='customer', uses=place_aliases.uses + 1, "
                "updated_at=now()",
                (tenant_id, key, phrase[:120], replacement[:120], customer_id))


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
        try:
            with conn.transaction():                              # ★마이그레이션 055(`differs_json`)가 안 올라간 DB 에서도 읽는다 — 그때는 「다르게 읽은 줄」이 없는 것으로 본다
                cur.execute(f"SELECT {', '.join(columns)}, differs_json FROM intake_sources WHERE tenant_id=%s AND intake_id=%s "
                            "ORDER BY position", (tenant_id, intake_id))
                return [dict(zip((*columns, "differs_json"), r)) for r in cur.fetchall()]
        except Exception as exc:                                  # noqa: BLE001 — 칸이 없을 때만 물러난다
            if type(exc).__name__ != "UndefinedColumn":
                raise
            log.warning("intake_sources.differs_json missing — reading without it (migration 055?)")
        cur.execute(f"SELECT {', '.join(columns)} FROM intake_sources WHERE tenant_id=%s AND intake_id=%s "
                    "ORDER BY position", (tenant_id, intake_id))
        return [dict(zip(columns, r)) for r in cur.fetchall()]


def _store_transcript(conn, cur, source_id: Any, tenant_id: str, result: Any) -> None:
    """받아쓴 글 · 빠진 줄 · 다르게 읽은 줄을 저장한다. 055 가 안 올라간 DB 에서는 다르게 읽은 줄만 빼고 저장한다(경고)."""
    base = (result.text, bool(result.transcribed_pages) or result.kind == "image", json.dumps(result.missing, ensure_ascii=False), result.seconds)
    try:
        with conn.transaction():
            cur.execute("UPDATE intake_sources SET transcript=%s, transcribed=%s, missing_json=%s, seconds=%s, differs_json=%s "
                        "WHERE source_id=%s AND tenant_id=%s", (*base, json.dumps(result.differs, ensure_ascii=False), source_id, tenant_id))
        return
    except Exception as exc:                                      # noqa: BLE001
        if type(exc).__name__ != "UndefinedColumn":
            raise
        log.warning("intake_sources.differs_json missing — transcript saved without it (migration 055?)")
    cur.execute("UPDATE intake_sources SET transcript=%s, transcribed=%s, missing_json=%s, seconds=%s "
                "WHERE source_id=%s AND tenant_id=%s", (*base, source_id, tenant_id))


def differs_by_line(rows: Any) -> dict[int, dict[str, Any]]:
    """저장된 「다르게 읽은 줄」 → {줄 번호: 줄 정보}. 모양이 이상한 줄은 건너뛴다(없는 것으로 본다)."""
    out: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("line"), int):
            out[row["line"]] = row
    return out


def _hold_reading(cur, tenant_id: str, intake_id: UUID) -> None:
    """이 트랜잭션 안에서 접수 행을 **잠그고** 아직 읽는 중인지 본다 — 아니면 `_Abandoned`. 실패 확정(`reap_stalled`)은 같은 행을 갱신하므로
    둘 중 하나가 먼저 끝나야 다른 쪽이 진행한다(일꾼이 실패 확정 뒤에 원본 · 값을 쓰지 못한다)."""
    cur.execute("SELECT 1 FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND status='reading' FOR UPDATE", (tenant_id, intake_id))
    if cur.fetchone() is None:
        raise _Abandoned()


def _stage(connect, tenant_id: str, intake_id: UUID, stage: str) -> None:
    """읽는 중일 때만 단계를 바꾼다 — 그 사이 실패로 확정됐으면 `_Abandoned`(되살리지 않는다)."""
    with connect() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE trip_intakes SET stage=%s, updated_at=now() WHERE tenant_id=%s AND intake_id=%s AND status='reading'",
                    (stage, tenant_id, intake_id))
        if cur.rowcount == 0:
            raise _Abandoned()


def _fatal(connect, tenant_id: str, intake_id: UUID, code: str, detail: str) -> None:
    with connect() as conn, conn.transaction(), conn.cursor() as cur:
        # 읽는 중인 접수만 실패로 만든다(이미 확인 화면 · 등록 · 실패인 접수는 그대로). 읽다 만 값은 남기지 않는다 — 못 읽은 접수의 확인 화면에
        # 반쯤 읽은 일정이 보이지 않게(전에는 값을 끝에 한꺼번에 적어 이 경우가 없었다)
        cur.execute("UPDATE trip_intakes SET status='fatal', stage='fatal', fatal_code=%s, fatal_detail=%s, "
                    "updated_at=now() WHERE tenant_id=%s AND intake_id=%s AND status='reading'", (code, detail, tenant_id, intake_id))
        if cur.rowcount:
            cur.execute("DELETE FROM intake_claims WHERE tenant_id=%s AND intake_id=%s AND revision=1 AND method <> 'customer'",
                        (tenant_id, intake_id))


def _claim_sink(connect, tenant_id: str, intake_id: UUID, source_id: Any) -> Callable[[list[dict[str, Any]]], None]:
    """읽는 동안 값 줄을 **바로** 적는 함수 — 한 번 부를 때마다 한 묶음(트랜잭션 하나)이고 접수의 갱신 시각도 올린다
    (뒤에서 읽는 일꾼이 살아 있다는 표시 — 실시간 진행이 「멈춤」으로 오해하지 않게)."""
    def write(batch: list[dict[str, Any]]) -> None:
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            # 먼저 접수가 아직 읽는 중인지 보고 갱신 시각을 올린다(한 문장) — 실패로 확정된 접수에는 값을 더 적지 않는다
            cur.execute("UPDATE trip_intakes SET updated_at=now() WHERE tenant_id=%s AND intake_id=%s AND status='reading'",
                        (tenant_id, intake_id))
            if cur.rowcount == 0:
                raise _Abandoned()
            for row in batch:
                cur.execute("INSERT INTO intake_claims (intake_id, tenant_id, revision, source_id, field, "
                            "value_json, method, evidence, needs_review, note) "
                            "VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s)",
                            (intake_id, tenant_id, source_id, row["field"],
                             json.dumps(row["value"], ensure_ascii=False), row["method"],
                             json.dumps({**row["evidence"], "source_id": str(source_id)}, ensure_ascii=False, default=str),
                             row["needs_review"], row["note"]))
    return write


def _claims(conn, tenant_id: str, intake_id: UUID, revision: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT source_id, field, value_json, method, evidence, needs_review, note FROM intake_claims "
                    "WHERE tenant_id=%s AND intake_id=%s AND revision=%s ORDER BY created_at, claim_id",
                    (tenant_id, intake_id, revision))
        return [dict(zip(("source_id", "field", "value", "method", "evidence", "needs_review", "note"), r))
                for r in cur.fetchall()]


__all__ = ["IntakeConflict", "IntakeRejected", "MAX_FILES", "MAX_FILE_BYTES", "MAX_TEXT_CHARS", "STAGES", "draft",
           "edit", "mark_confirmed", "open_intake", "process", "view"]
