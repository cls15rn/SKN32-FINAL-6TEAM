# -*- coding: utf-8 -*-
"""취소 가능 여부 · 위약금 · 변경 제안 — `ActivityTeam` 의 ① 검증 중 **예약 조건**을 다루는 부분.

★`team.py` 에서 옮겼다(동작 변경 없음). 수치(취소 기한·위약금율)는 `read.booking_terms` 가 대고,
  `read.policy` 는 문장 근거만 댄다.
"""
from __future__ import annotations

from typing import Any

from app.core.contracts import NextAction, TeamResult, TeamTask


class CancellationMixin:
    # ── ① 검증 — 계산으로만 ────────────────────────────────────
    def _check_cancelable(self, task: TeamTask, booking: dict, terms: Any,
                          remaining: float, evidence: list) -> TeamResult:
        deadline = self._terms_hours(terms, "cancel_deadline_hours")
        if deadline is None:
            return self._unknown(task, "취소 기한", evidence)

        if remaining < deadline:
            # ★`[2026-09-23 실측]` 두 값이 같은 자리에서 반올림되면 답변이 **자기모순으로
            #   보인다** — 「6시간 전까지 취소할 수 있는데 지금은 6.0시간 남았습니다」가
            #   실제로 나왔다(demo BK-GOLF-LATE, 기한 6시간 · 남은 5.98시간). 고객은 이걸
            #   읽고 「그럼 되는 거 아닌가」 하고 다시 묻는다. 겨우 넘긴 경우는 **분으로** 말한다.
            late_minutes = (deadline - remaining) * 60
            if round(remaining, 1) >= round(deadline, 1):
                how = (f"규정상 시작 {deadline:g}시간 전까지 취소할 수 있는데 "
                       f"{late_minutes:.0f}분 차이로 지났습니다.")
            else:
                how = (f"규정상 시작 {deadline:g}시간 전까지 취소할 수 있는데 "
                       f"지금은 {remaining:.1f}시간 남았습니다.")
            return self._result(
                task, outcome="completed", confidence=1.0, evidence=evidence,
                next_action=NextAction.RESPOND,
                answer=f"취소 기한이 지났습니다. {how}",
                decisions=[{"cancelable": False, "hours_remaining": round(remaining, 1),
                            "deadline_hours": deadline,
                            # ★분자/분모를 남긴다 — 「얼마나 늦었나」가 재문의의 첫 질문이다
                            "late_by_minutes": round(late_minutes)}])

        penalty = self._penalty_rate(terms, remaining)
        if penalty is None:
            # ★위약금율을 모르면 **금액을 만들지 않는다.** 취소 가능 여부만 말한다.
            return self._result(
                task, outcome="completed", confidence=0.7, evidence=evidence,
                next_action=NextAction.RESPOND,
                answer=f"취소 기한 안입니다(시작까지 {remaining:.1f}시간). "
                       f"다만 이 구간의 위약금율은 확인되지 않아 금액은 안내하지 못합니다.",
                decisions=[{"cancelable": True, "hours_remaining": round(remaining, 1),
                            "penalty_rate": None}],
                warnings=["위약금율을 확인하지 못했다 — 금액을 만들지 않았다"])

        return self._result(
            task, outcome="completed", confidence=1.0, evidence=evidence,
            next_action=NextAction.RESPOND,
            answer=f"취소할 수 있습니다. 시작까지 {remaining:.1f}시간 남았고 "
                   f"규정상 위약금율은 {penalty:.0%}입니다.",
            decisions=[{"cancelable": True, "hours_remaining": round(remaining, 1),
                        "penalty_rate": penalty}])

    # ── ③ 재계획 — 제안까지만 ──────────────────────────────────
    def _propose_change(self, task: TeamTask, booking: dict, evidence: list, *,
                        reason: str | None = None, answer: str | None = None,
                        decisions: dict[str, Any] | None = None) -> TeamResult:
        """★대안을 실행하지 않는다. `ActionProposal` 로 승인 대기에 올린다.

        `reason` 이 없으면 고객 문장 그대로(고객이 바꿔 달라고 한 경우), 있으면
        점검이 찾은 이상(감시·점검이 바꾸자고 하는 경우)이다.
        """
        proposal = self._proposal(
            task, "activity.change",
            {"booking_id": booking.get("booking_id"), "reason": reason or task.input_text},
            evidence)
        return self._result(
            task, outcome="completed", confidence=0.6, evidence=evidence,
            next_action=NextAction.WAIT_FOR_APPROVAL,
            answer=answer or "변경 제안을 만들었습니다. 승인 뒤에 진행됩니다.",
            action_proposals=[proposal],
            decisions=[{"proposed": "activity.change", **(decisions or {})}])

    # ── 취소 조건 읽기 ────────────────────────────────────────
    #
    # ★★`[2026-09-23]` **이 둘은 전에 언제나 `None` 을 냈다.** `read.policy` 가 주는
    #   `PolicyChunk` 를 `isinstance(chunk, dict)` 로 걸렀기 때문이다 — 그 검사가 항상
    #   거짓이라 어떤 코퍼스를 넣어도 취소 기한·위약금율이 안 나왔고, 제품이 약속한
    #   「지금 취소하면 얼마인가」가 한 번도 답해진 적이 없다. 이제 **구조화된 조건**
    #   (`read.booking_terms` · 마이그레이션 023)을 읽는다.
    #   기록 — `wiki/records/reports/debugs/2026-09-22_정책청크에서_수치를_못_꺼낸다.md`
    #
    # ★**청크 목록을 받으면 그건 잘못 부른 것이다.** 조용히 `None` 을 내면 그 오진이
    #   또 몇 달 간다 — 모양이 다르면 그렇게 말한다(아래 `_terms_dict`).

    @staticmethod
    def _terms_dict(terms: Any) -> dict[str, Any] | None:
        """구조화된 취소 조건만 받는다. `None`(모름)과 **잘못된 모양**을 가른다."""
        if terms is None:
            return None
        if isinstance(terms, dict):
            return terms
        raise TypeError(
            "취소 조건은 `read.booking_terms` 가 주는 dict 여야 한다 — 받은 것: "
            f"{type(terms).__name__}. RAG 청크에서 수치를 꺼내려던 옛 경로다"
            " (debugs/2026-09-22_정책청크에서_수치를_못_꺼낸다.md)")

    @classmethod
    def _terms_hours(cls, terms: Any, key: str) -> float | None:
        found = cls._terms_dict(terms)
        if found is None or found.get(key) is None:
            return None
        try:
            return float(found[key])
        except (TypeError, ValueError):
            return None

    @classmethod
    def _penalty_rate(cls, terms: Any, remaining: float) -> float | None:
        """남은 시간 구간별 위약금율. 조건에 구간이 없으면 `None`(모름).

        ★표는 「남은 시간이 이 값보다 적으면 이 율」이다. 여러 구간에 걸리면 **가장 센
          율**을 고른다 — 고객에게 유리한 쪽으로 틀리면 나중에 더 받아야 하고, 그건
          우리가 말을 바꾸는 것이 된다.
        """
        found = cls._terms_dict(terms)
        table = (found or {}).get("penalty_by_hours")
        if not isinstance(table, dict):
            return None
        best: float | None = None
        for hours, rate in table.items():
            try:
                if remaining < float(hours):
                    value = float(rate)
                    best = value if best is None else max(best, value)
            except (TypeError, ValueError):
                continue
        return best
