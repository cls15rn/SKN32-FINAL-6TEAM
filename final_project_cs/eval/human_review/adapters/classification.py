# -*- coding: utf-8 -*-
"""분류 평가 자료 G96(= travel_golden 72 + travel_holdout 24) — 의도 · 대상(issue_code) · 감정 · 긴급도를 사람이 단다.

현재 이 96개의 네 라벨은 모두 `label_by: claude-draft`(AI 초안, 사람 검수 전)다. 시트에는 문장 · 채널 · 언어와 칸마다의 뜻만 싣는다.
선택지 목록은 앱의 분류 어휘(`core_hooks/feedback.py`)와 같아야 한다 — 시험이 둘을 맞춰 본다(어휘가 바뀌면 시험이 깨진다).
"""
from __future__ import annotations

import json

from ..core import Bundle, Field, Item, sample_ids
from .paths import Sources

AI_SOURCE = "claude_draft"

INTENTS = ("itinerary_submit", "incident_report", "confirm_request", "adjust_reject", "other")
SENTIMENTS = ("positive", "neutral", "negative")
SEVERITIES = ("low", "medium", "high", "critical")
ISSUE_CODES = (
    "activity_cancel_or_change", "activity_weather_risk", "activity_time_conflict", "activity_other",
    "dining_hours", "dining_conditions", "dining_other",
    "mobility_missed_or_disrupted", "mobility_route_infeasible", "mobility_other",
    "booking_mismatch", "booking_change_request", "booking_cancel_request", "booking_other", "booking_revert_request",
    "lodging_other", "flight_other", "other",
)

CRITERIA = (
    "의도(intent) = 무엇을 해 달라는 요청인가: itinerary_submit 일정을 보내거나 등록 · incident_report 문제가 생겼다(늦음 · 닫음 · 매진 · 날씨) · "
    "confirm_request 묻거나 확인을 부탁 · adjust_reject 바꾸기 · 다른 선택지 · 되돌리기 요청 · other 위에 없음. "
    "대상(issue_code) = 무엇에 관한 어떤 상황인가. 앞 낱말(activity_/dining_/mobility_/booking_/lodging_/flight_)이 어느 팀이 맡을지를 정한다 — "
    "맞는 코드가 없을 때만 other. "
    "감정(sentiment) = 말투에 담긴 감정. "
    "긴급도(severity) = 지금 처리가 얼마나 급한가. ※코드 · wiki 에서 긴급도의 정의 문서를 찾지 못했다(app · wiki · prompts 에서 severity 검색) — "
    "이 시트용 가정이며 공식 정의가 아니다: low 기다려도 되는 문의 · 확인 / medium 일정에 지장이 생길 수 있어 오늘 안에 답이 필요 / "
    "high 곧 이용할 일정에 바로 영향 · 안전 걱정 / critical 생명 · 안전 · 법적 위험. 두 사람이 같은 가정으로 단다."
)


def build(src: Sources | None = None) -> Bundle:
    src = src or Sources()
    items: list[Item] = []
    for fname in ("travel_golden.jsonl", "travel_holdout.jsonl"):
        path = src.need(src.cs / "eval" / "datasets" / fname, fname)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            items.append(Item(
                id=r["case_id"],
                context={"고객 문장": r["message"], "채널": r["channel"], "언어": r["locale"]},
                criteria=CRITERIA,
                ai={AI_SOURCE: {"intent": r["expected_intent"], "issue_code": r["expected_issue_code"],
                                "sentiment": r["expected_sentiment"], "severity": r["expected_severity"]}},
                meta={"label_by": r.get("label_by"), "split": "holdout" if r["case_id"].startswith("th-") else "golden"},
            ))
    return Bundle(
        name="g96", title="분류 평가 자료 G96(golden 72 + holdout 24) — 네 라벨 사람 판정",
        fields=[Field("intent", "의도", "choice", INTENTS), Field("issue_code", "대상", "choice", ISSUE_CODES),
                Field("sentiment", "감정", "choice", SENTIMENTS, ordered=("negative", "neutral", "positive")),
                Field("severity", "긴급도", "choice", SEVERITIES, ordered=("low", "medium", "high", "critical"))],
        items=items, context_columns=["고객 문장", "채널", "언어"],
        notes="네 라벨 모두 AI 초안(label_by: claude-draft). 한 행에 네 칸을 단다. second30 = 두 번째 평가자(카파)용 30행 무작위(시드 20261015).",
        subsets={"second30": lambda: sample_ids([it.id for it in items], 30)},
    )
