# -*- coding: utf-8 -*-
"""규정 답(RAG) · 알림 번역 사람 평가 35행 — 두 번째 평가자(카파)용 **Claude 의견을 가린** 시트.

기존 도구(`program/scripts/rag_gate/human_eval/`)는 HTML 에 Claude 의견(예상 판정 + 이유)이 같이 보인다. 두 번째 평가자가 같은 HTML 을 써도
`score_simple.py A B` 로 가중 카파는 나오지만, 둘 다 AI 의견을 본 상태라 독립 일치도가 아니다. 이 어댑터는 같은 HTML 안의 자료에서
**고객 말 · 시스템 답 · 확인 자료(규정 문장 · 숫자 목록)**만 시트에 싣는다. `claude` · `why`(AI 의견)는 시트에 안 싣고 일치도 계산용으로만 `Item.ai` 에 둔다.
★「확인 자료」의 정답 규정은 질문을 만들 때 Codex 가 붙인 칸이고(틀렸을 수 있다), 기계가 붙인 판정 문장은 싣지 않는다(숫자 목록만 싣는다).
★이미 있는 사람 평가(`1006_사람평가_A.csv`, 평가자 1명)는 `reference_humans` 로 꺼낸다 — 그 평가자는 Claude 의견을 보았다.
"""
from __future__ import annotations

import csv
import json

from ..core import Bundle, Field, Item
from .paths import Sources

AI_SOURCE = "claude_opinion"                  # 손으로 쓴 Claude 의견(AI 초안) — 사람 점수가 아니다
VERDICTS = ("맞다", "중간", "틀리다", "모름")
ORDER = ("맞다", "중간", "틀리다")

CRITERIA_POLICY = ("시스템 답이 고객 질문에 맞는 정확한 안내인가. 맞다 = 모든 사실이 맞고 물은 것에 답한다 · 중간 = 일부만 맞거나 애매하다 · "
                   "틀리다 = 틀린 사실이 있거나 물은 것과 상관없는 규정을 붙였다 · 모름 = 판단할 수 없다. "
                   "(질문에 규정이 없어 「찾지 못했다」고 거절한 답은 지어내지 않았다면 맞다)")
CRITERIA_TRANSLATION = ("번역이 원문의 뜻 · 시각 · 금액 · 단위를 바꾸지 않았는가. 맞다 = 그대로 옮겼다 · 중간 = 뜻은 같지만 어색하거나 일부 빠졌다 · "
                        "틀리다 = 숫자 · 단위 · 뜻이 바뀌었다 · 모름 = 그 언어를 읽을 수 없다")
LANG = {"en": "영어", "ja": "일본어", "zh-CN": "중국어(간체)"}


def _load_data(html_path) -> list[dict]:
    text = html_path.read_text(encoding="utf-8")
    start = text.index("var DATA = ") + len("var DATA = ")
    data, _ = json.JSONDecoder().raw_decode(text[start:].replace("<\\/", "</"))
    return data


NEUTRAL_CHECKS = ("시각 · 금액 · 큰 숫자", "한 자리 숫자")


def _evidence(ev: dict) -> str:
    """확인 자료 = 질문에 맞는 규정 문장(블록) + 번역이면 원문 · 번역의 숫자 목록.

    ★기계가 붙인 판정 문장(「~같아요」 · 「위 규정이 없어요」 · 「단위가 바뀐 거예요」)은 뺀다 — 결론을 미리 알려 주기 때문이다.
      그래서 이 시트의 조건은 평가자 A 가 본 화면(기계 판정 문장 + Claude 의견)보다 더 가려져 있다.
    """
    parts = []
    for b in ev.get("blocks", []):
        parts.append(f"[{b['label']}] " + (f"({b['where']}) " if b.get("where") else "") + b["text"])
    for c in ev.get("checks", []):
        if c["t"].startswith(NEUTRAL_CHECKS):
            parts.append("[숫자 목록] " + c["t"].split(" → ")[0])
    return "\n".join(parts)


def build(src: Sources | None = None) -> Bundle:
    src = src or Sources()
    html = src.need(src.human_eval / "human_eval_sheet.html", "사람 평가 HTML")
    items = []
    for d in _load_data(html):
        translation = d["kind"] == "번역"
        label_q = f"원문(한국어) → {LANG.get(d.get('lang'), d.get('lang'))}" if translation else "고객이 한 말"
        items.append(Item(
            id=d["id"],
            context={"종류": "알림 번역" if translation else "규정 질문 답", "입력": f"{label_q}\n{d['q']}",
                     "시스템 답": d["a"], "확인 자료": _evidence(d["ev"])},
            criteria=CRITERIA_TRANSLATION if translation else CRITERIA_POLICY,
            ai={AI_SOURCE: {"verdict": d["claude"]}},                      # 이 값은 시트에 안 실린다
            meta={"kind": d["kind"]},
        ))

    def reference():
        path = src.need(src.human_eval / "1006_사람평가_A.csv", "기존 사람 평가 결과")
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
        return [{"bundle": "rag", "reviewer": "평가자 A(사용자 1명)", "reviewed_on": "2026-10-06", "saw_ai_labels": True, "name": "human_A_saw_claude",
                 "labels": {r["id"]: {"verdict": r["사람 판정"], "note": r.get("사람 메모", "")} for r in rows if r["사람 판정"] in VERDICTS}}]

    return Bundle(
        name="rag", title="규정 답 · 알림 번역 사람 평가 35행 — 두 번째 평가자(Claude 의견 가림)",
        fields=[Field("verdict", "판정", "choice", VERDICTS, ordered=ORDER, exclude=("모름",))],
        items=items, context_columns=["종류", "입력", "시스템 답", "확인 자료"],
        notes="규정 질문 답 24 + 알림 번역 11. 시스템 답은 2026-10-06 측정 시점의 개선 전(운영 색인) 판이다.",
        reference_humans=reference,
    )
