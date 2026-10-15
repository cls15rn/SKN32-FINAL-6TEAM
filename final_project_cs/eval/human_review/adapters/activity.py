# -*- coding: utf-8 -*-
"""활동 판정 골든셋 52건(휴무 16 · 운영시간 14 · 실내외 10 · 재난 12) — **독립(정답을 가린) 두 번째 검토**.

기존 `eval/activity_judge/review.py` 를 고치지 않고 재사용한다(사례 · 판정 기준 · 허용 값 · 입력 글).
- 시트: 입력 · 판정 기준 · 고를 수 있는 값만. `labels_first_pass.json`(Claude 1차 = AI 초안)은 시트에 안 싣는다.
- 이미 있는 사람 검토 `labels_human.json`(팀원 1명, **AI 1차 라벨을 보고** 확정 — 독립 아님)은 `reference_humans` 로 꺼내 두 번째 검토와 견준다.
- 실시간 운영(live_status) 8건은 라벨 자체가 없다(실제 공지를 사람이 확인해야 한다) — 시트에서 뺀다.
"""
from __future__ import annotations

import json

from ..core import Bundle, Field, Item
from .paths import Sources

AI_SOURCE = "claude_first_pass"          # AI 초안(Claude 10-11, 사람 검수 전)
HUMAN_REF = "human_1_saw_ai"             # 팀원 1명 — AI 1차 라벨을 보고 확정


def _norm(entry: dict) -> dict:
    """「함께 맞다고 칠 값」에서 정답 자신을 뺀다 — 시트 · 기존 라벨 · AI 초안이 같은 모양이 되게."""
    entry = dict(entry)
    entry["acceptable"] = [v for v in entry.get("acceptable", []) if v != entry.get("expected")]
    return entry


def build(src: Sources | None = None) -> Bundle:
    from eval.activity_judge import review                       # 앱 import 가 필요하다 — 여기서만(늦게)

    cases = review.load_cases()
    first = json.loads(review.FIRST_PASS.read_text(encoding="utf-8")).get("labels", {})
    items = []
    for c in cases:
        values = tuple(review.VALUES[c["kind"]])
        lab = first.get(c["case_id"]) or {}
        items.append(Item(
            id=c["case_id"],
            context={"종류": c["kind"], "방문 시각": review._when(c["inputs"].get("starts_at")), "입력": review._inputs(c)},
            criteria=review.CRITERIA[c["kind"]],
            allowed={"expected": values, "acceptable": values},
            ai={AI_SOURCE: _norm({"expected": lab.get("expected"), "acceptable": lab.get("acceptable", [])})} if lab else {},
        ))

    def reference():
        human = json.loads(review.HUMAN_LABELS.read_text(encoding="utf-8"))
        labels = {cid: {"expected": v["expected"], "acceptable": [a for a in v["acceptable"] if a != v["expected"]],
                        "note": v.get("note", "")} for cid, v in human["labels"].items()}
        return [{"bundle": "activity", "reviewer": human["reviewer"], "reviewed_on": human["reviewed_on"], "saw_ai_labels": True,
                 "name": HUMAN_REF, "labels": labels}]

    return Bundle(
        name="activity", title="활동 판정 골든셋 52건 — 독립 두 번째 검토",
        fields=[Field("expected", "정답", "choice"),
                Field("acceptable", "함께 맞다고 칠 값(; 로 구분)", "set", required=False)],
        items=items, context_columns=["종류", "방문 시각", "입력"], normalize=_norm,
        notes="live_status(실시간 운영) 8건은 라벨이 없어 뺀다. 「함께 맞다고 칠 값」은 정답과 같이 맞다고 칠 값(예: 공휴일이어야 정해지면 unknown)이며 비워도 된다.",
        reference_humans=reference,
    )
