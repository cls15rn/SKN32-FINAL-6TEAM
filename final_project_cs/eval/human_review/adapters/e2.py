# -*- coding: utf-8 -*-
"""E2(요청 계획 비교) 467문항(고유 가족 167) — 정답 **계획의 큰 갈래**를 사람이 가린 채 다시 고른다.

E2 의 정답은 요청 계획 JSON(행동 · 대상 · 값 · 모드)이라 사람이 한 칸으로 달 수 없다. 그래서 이 시트는 JSON 전체가 아니라
  ① 처리 갈래(바로 실행 / 조건부 실행 / 되묻기 / 막힘 / 조회·답변 / 할 일 없음)
  ② 요청이 바꾸거나 지우려는 일정 항목 번호
두 가지만 묻는다. AI 정답(`gold.jsonl` — 소넷 검토 후 정정, Codex 작성 · 사람 검수 0/467)은 같은 갈래로 환산해 `Item.ai` 에만 둔다.
★한계: 이 시트가 일치해도 시각 · 값 · 모드의 세부(JSON 안쪽)는 사람이 본 것이 아니다 — 세부 검수는 별도 일이다.
★가족(고유 대화 틀) 167개 × 말투 변형(원문 · 정중 · SNS · 붙여쓰기)이라 말투만 다른 행은 같은 정답이다 — 우선 시트는 가족당 원문 1행(167)이다.
"""
from __future__ import annotations

import json
from datetime import datetime

from ..core import Bundle, Field, Item, sample_ids
from .paths import Sources

AI_SOURCE = "codex_gold_sonnet_reviewed"      # Codex 작성 + 소넷 검토 후 정정한 E2-v2 정답(사람 검수 전)

C_EXEC, C_COND, C_CLARIFY, C_BLOCK, C_READ, C_NONE = "바로 실행", "조건부 실행", "되묻기", "막힘", "조회·답변", "할 일 없음"
CLASSES = (C_EXEC, C_COND, C_CLARIFY, C_BLOCK, C_READ, C_NONE)
WRITE = {"create", "update", "delete"}
WEEK = "월화수목금토일"

CRITERIA = (
    "고객 말에 대해 도우미가 어떻게 처리해야 하는가를 고른다. "
    "바로 실행 = 요청이 분명하고 권한이 있어 일정을 실제로 바꾼다(추가 · 수정 · 삭제). "
    "조건부 실행 = 고객이 건 조건이 맞을 때만 바꾼다. "
    "되묻기 = 정보가 모자라거나 대상이 둘 이상이라 먼저 물어본다. "
    "막힘 = 권한 · 기능 한계(남의 일정 · 예약 · 결제 등)로 못 한다고 알린다. "
    "조회·답변 = 일정을 바꾸지 않고 읽어 주거나 제안 · 안내만 한다. "
    "할 일 없음 = 처리할 요청이 없는 말(감사 · 잡담 등). "
    "항목 번호 = 이 말이 바꾸거나 지우려는 일정 항목의 번호(i1, i2 …). 바꿀 항목이 없으면 비운다."
)


def plan_class(gold: dict) -> str:
    """AI 정답(요청 계획)을 처리 갈래 하나로 환산한다. 사람 시트의 선택지와 같은 어휘."""
    reqs, reply = gold["requests"], gold["reply_kind"]
    if reply == "blocked":
        return C_BLOCK
    if reply == "clarify":
        return C_CLARIFY
    if any(r.get("mode") == "execute" and r["action"] in WRITE for r in reqs):
        return C_EXEC
    if any(r.get("mode") == "conditional" for r in reqs):
        return C_COND
    return C_NONE if not reqs else C_READ


def plan_targets(gold: dict) -> list[str]:
    return sorted({t for r in gold["requests"] if r["action"] in WRITE for t in r.get("target_ids", [])})


def _when(text: str) -> str:
    at = datetime.fromisoformat(text)
    return f"{at:%Y-%m-%d}({WEEK[at.weekday()]}) {at:%H:%M}"


def _day(date: str) -> str:
    d = datetime.fromisoformat(date)
    return f"{d:%m/%d}({WEEK[d.weekday()]})"


def render_itinerary(items: dict) -> str:
    lines = []
    for it in items.values():
        flags = ("[잠김]" if it.get("locked") else "") + ("[예약됨]" if it.get("reservation") else "")
        lines.append(f"{it['id']} | {_day(it['date'])} {it['start']}-{it['end']} | {it['title']} @ {it['place']} | {it['kind']} {flags}".rstrip())
    return "\n".join(lines)


def render_situation(inp: dict) -> str:
    """입력 중 정답이 아닌 상황 정보(보낸 시각 · 화면 선택 · 앞 대화 · 권한 …)를 글로 옮긴다."""
    perm, cap = inp["permissions"], inp["capabilities"]
    lines = [f"보낸 시각: {_when(inp['received_at'])} ({inp['timezone']}) · 일정 버전 {inp['itinerary_version']}",
             f"화면에서 선택한 것: {inp.get('ui_selected_day') or '없음'} / {inp.get('ui_selected_item') or '없음'}"
             + (f" · 직전에 확인한 항목: {inp['last_verified_target']}" if inp.get("last_verified_target") else ""),
             "권한: 본인 일정 읽기 " + ("O" if perm["read_own"] else "X") + " · 본인 일정 쓰기 " + ("O" if perm["write_own"] else "X")
             + " · 남의 일정 읽기 " + ("O" if perm["read_other"] else "X") + " · 남의 일정 쓰기 " + ("O" if perm["write_other"] else "X")
             + " · 예약 " + ("O" if perm["book"] else "X") + " · 결제 " + ("O" if perm["pay"] else "X"),
             "기능: 일정 추가·수정·삭제 " + ("가능" if cap["itinerary_crud"] else "불가") + " · 외부 예약 " + ("가능" if cap["external_booking"] else "불가")
             + " · 결제 " + ("가능" if cap["payment"] else "불가")]
    for h in inp.get("history", []):
        pend = f" (대기 중인 변경: {json.dumps(h['pending_operation'], ensure_ascii=False)})" if h.get("pending_operation") else ""
        lines.append(f"앞 대화 [{h.get('role')}]: {h.get('content')}{pend}")
    for key, label in (("current_location", "현재 위치"), ("condition_facts", "조건 확인용 사실"),
                       ("completed_requests", "이미 처리된 요청"), ("request_id", "요청 번호")):
        if inp.get(key):
            lines.append(f"{label}: {json.dumps(inp[key], ensure_ascii=False)}")
    facts = [e.get("facts") for e in inp.get("evidence", []) if e.get("facts")]
    if facts:
        lines.append("참고 사실(시험용 가짜 자료): " + json.dumps(facts, ensure_ascii=False))
    return "\n".join(lines)


def build(src: Sources | None = None, version: str = "v2") -> Bundle:
    src = src or Sources()
    root = src.need(src.datasets_root / "travel" / f"e2_itinerary_crud_20261012_{version}" / "processed", f"E2-{version} processed 폴더")
    inputs = [json.loads(line) for line in (root / "inputs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {g["case_id"]: g["gold"] for g in (json.loads(line) for line in (root / "gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    items = []
    for row in inputs:
        inp = row["input"]
        g = gold[row["case_id"]]
        items.append(Item(
            id=row["case_id"],
            context={"고객 말": inp["message"], "상황": render_situation(inp), "현재 일정": render_itinerary(inp["items"])},
            criteria=CRITERIA,
            allowed={"plan_class": CLASSES, "targets": tuple(inp["items"].keys())},
            ai={AI_SOURCE: {"plan_class": plan_class(g), "targets": plan_targets(g)}},
            meta={"family": row["family_id"], "style": row["style"], "slice": row["slice"], "provenance": row["provenance"]["kind"]},
        ))

    def families() -> list[str]:
        return [it.id for it in items if it.meta["style"] == "original"]

    return Bundle(
        name=f"e2_{version}", title=f"E2-{version} 요청 계획 — 처리 갈래 사람 판정(가족당 원문 1행 = 167)",
        fields=[Field("plan_class", "처리 갈래", "choice", CLASSES),
                Field("targets", "바꾸거나 지울 항목 번호(; 로 구분)", "set", (), required=False)],
        items=items, context_columns=["고객 말", "상황", "현재 일정"],
        notes="처리 갈래 + 대상 항목만 확인한다. 요청 계획 JSON 안쪽(시각 · 값 · 모드)은 사람이 본 것이 아니다. 우선 시트 families = 가족당 원문 1행.",
        subsets={"families": families, "second30": lambda: sample_ids(families(), 30)},
    )
