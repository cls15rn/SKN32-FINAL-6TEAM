# -*- coding: utf-8 -*-
"""상용화 기준 판정기 — 사전에 정한 기준과 **95% 구간의 아래(위) 끝**으로 합격 여부를 가른다. `[2026-10-15]`

6주차 점검에서 「합격 판정은 사전에 정한 기준과 구간 아래 끝으로 한다」는 요구가 나왔다. 점추정(92.6%)이 기준(90%)을 넘어도
구간 아래 끝(83.9%)이 못 넘으면 합격이 아니다. 이 모듈은 그 규칙을 한 곳에 두고, 측정 한 줄(분자 · 분모 · 조건 · 날짜 · 출처)을 넣으면 판정을 낸다.

    python -m eval.commercial_criteria            # 표로 인쇄
    python -m eval.commercial_criteria --json     # JSON

★규칙(이 파일이 정한 것 — 바꾸려면 여기만 고친다)
  - 비율 하한 기준(`min_rate`): 구간 아래 끝 ≥ 기준 → 「합격」 · 점추정만 ≥ 기준 → 「점추정만 통과」(표본이 모자라 합격 아님) · 점추정 < 기준 → 「불합격」
  - 비율 상한 기준(`max_rate`): 구간 위 끝 ≤ 기준 → 「합격」 · 점추정만 ≤ 기준 → 「점추정만 통과」 · 점추정 > 기준 → 「불합격」
  - 0건 기준(`zero`): 관측 0건이고 95% 위 끝(Clopper-Pearson) ≤ 1% 이면 「합격」 · 관측 0건이지만 위 끝이 1% 를 넘으면 「표본 부족」 · 1건 이상이면 「불합격」
  - 측정이 없으면(`n` 이 None 또는 0) 「측정 없음」. 값을 지어내지 않는다.
  - 기준이 말하는 범위의 **일부만** 잰 값(`partial_scope=True`)이 합격선을 넘으면 「일부 범위만 통과」 — 합격으로 세지 않는다.
★구간 방법: 비율은 Wilson 점수 구간, 0건 상한은 Clopper-Pearson(정확). 둘 다 이 모듈이 **직접 계산**한 값이다(문서에 적힌 구간이 있으면 근거 칸에 따로 적는다).
★이 판정은 「그 조건에서 잰 값」에 대한 것이다. 사람이 쓴 실제 질문이 아니면 `real_human_questions=False` 로 표시하고, 공통 조건
  (「사람이 쓴 실제 질문 기준」)을 충족한 것으로 세지 않는다.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson 점수 구간 (아래, 위) — 0~1."""
    if n <= 0:
        raise ValueError("분모는 1 이상")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def zero_upper(n: int, alpha: float = 0.05) -> float:
    """관측 0건일 때 Clopper-Pearson 한쪽 95% 위 끝 = 1 - (alpha)^(1/n)."""
    if n <= 0:
        raise ValueError("분모는 1 이상")
    return 1 - alpha ** (1 / n)


@dataclass(frozen=True)
class Criterion:
    key: str
    name: str
    kind: str                 # min_rate | max_rate | zero
    threshold: float | None   # 비율(0~1). zero 는 None
    source: str               # 기준을 정한 곳(양식 / 계획서 / 제안)
    fixed_by: str             # 「양식·계획서」 = 정해진 기준, 「제안」 = 고객 손해 기준으로 제안한 값


@dataclass(frozen=True)
class Measurement:
    key: str
    label: str                # 무엇을 어떤 구성으로 쟀나
    k: int | None             # 분자(기준을 어긴/맞은 쪽 — 기준의 방향에 맞춘 값: min_rate 는 맞은 수, max_rate·zero 는 어긴 수)
    n: int | None             # 분모
    measured_on: str          # 측정 날짜
    condition: str            # 세트 · 모델 · 반복 · 작성자
    source: str               # 근거 파일
    real_human_questions: bool = False
    reran_on_current_product: bool = False   # 오늘 제품(모델 · 코드)으로 다시 쟀나
    partial_scope: bool = False              # 기준이 말하는 범위의 일부만 잰 값(예: 전체 응답이 아니라 해석 단계만)
    note: str = ""


CRITERIA: dict[str, Criterion] = {c.key: c for c in [
    Criterion("rule_search", "규정 검색 Recall@3", "min_rate", 0.90, "계획서·양식", "양식·계획서"),
    Criterion("decision_unit", "결정 단위 pass^3", "min_rate", 0.95, "계획서·양식", "양식·계획서"),
    Criterion("wrong_target_change", "엉뚱한 대상 변경", "zero", None, "계획서·양식", "양식·계획서"),
    Criterion("invented_value", "지어낸(변조된) 값", "zero", None, "계획서·양식", "양식·계획서"),
    Criterion("response_15s", "응답 15초 초과 비율(p95 ≤ 15초 ⇔ 초과 ≤ 5%)", "max_rate", 0.05, "양식", "양식·계획서"),
    Criterion("irrelevant_pick", "무관한 말에 일정 항목 오지목", "max_rate", 0.01, "제안(고객 손해 기준)", "제안"),
    Criterion("human_correct", "사람 판정 규정 답 「맞다」", "min_rate", 0.90, "제안(고객 손해 기준)", "제안"),
    Criterion("urgency", "긴급도 분류 정확도", "min_rate", 0.90, "제안(고객 손해 기준)", "제안"),
]}


def judge(criterion: Criterion, m: Measurement | None) -> dict:
    out: dict = {"criterion": criterion.name, "kind": criterion.kind, "threshold": criterion.threshold, "fixed_by": criterion.fixed_by}
    if m is None or not m.n:
        return {**out, "verdict": "측정 없음", "why": "이 조건의 측정값이 없다"}
    out.update({"label": m.label, "k": m.k, "n": m.n, "measured_on": m.measured_on, "condition": m.condition, "source": m.source,
                "real_human_questions": m.real_human_questions, "reran_on_current_product": m.reran_on_current_product, "partial_scope": m.partial_scope, "note": m.note})
    k, n = m.k, m.n
    if criterion.kind == "zero":
        up = zero_upper(n) if k == 0 else wilson(k, n)[1]
        out.update({"point": k / n, "upper": up})
        if k > 0:
            verdict, why = "불합격", f"{k}/{n}건 관측"
        elif up <= 0.01:
            verdict, why = "합격", f"0/{n}건, 95% 위 끝 {up:.2%} ≤ 1%"
        else:
            verdict, why = "표본 부족", f"0/{n}건이지만 95% 위 끝 {up:.2%} > 1% — 0건을 증명하려면 약 299건 이상 필요"
    else:
        lo, hi = wilson(k, n)
        p = k / n
        out.update({"point": p, "lower": lo, "upper": hi})
        thr = criterion.threshold
        if criterion.kind == "min_rate":
            verdict = "합격" if lo >= thr else ("점추정만 통과" if p >= thr else "불합격")
            why = f"{p:.1%} (95% 구간 {lo:.1%}~{hi:.1%}) 기준 ≥ {thr:.0%}, 아래 끝 {lo:.1%}"
        else:
            verdict = "합격" if hi <= thr else ("점추정만 통과" if p <= thr else "불합격")
            why = f"{p:.1%} (95% 구간 {lo:.1%}~{hi:.1%}) 기준 ≤ {thr:.0%}, 위 끝 {hi:.1%}"
    if m.partial_scope and verdict == "합격":
        verdict, why = "일부 범위만 통과", why + " — 기준이 말하는 범위 전체가 아니라 일부만 잰 값이라 합격으로 세지 않는다"
    return {**out, "verdict": verdict, "why": why}


#: 측정 한 줄 = (기준 키, 측정). 전부 **기존 저장 결과를 다시 읽고 다시 센 값**이다 — 이번 회차에 모델을 새로 돌려 잰 것은 없다.
#: 날짜 · 분모 · 조건은 각 근거 파일에서 열어 확인한 값. `reran_on_current_product` 가 False 면 오늘 제품으로 다시 잰 값이 아니다.
MEASUREMENTS: list[Measurement] = [
    # 규정 검색 — 두 구성은 같은 세트(H 68)에서 짝으로 비교한다. 제품 방식은 오늘 제품 코드와 같다(재순위는 연결 안 됨).
    Measurement("rule_search", "제품 방식(직원용 본문 색인 · 상위 3) · 보류 세트 H", 41, 68, "2026-10-07",
                "H 68문제(Claude 작업자 둘이 작성 · 라벨) · bge-m3 · 1회 · Hit@3", "program/scripts/rag_gate/results/rag_improve/holdout_paired.txt:5",
                note="제품 코드는 retriever.py · trip_replies.py 가 상위 3 + 점수 0.55 (재순위 꺼짐 — guardrails.yaml rerank_enabled=false). 2026-10-15 저장 벡터로 오프라인 재집계해 41/68 재현"),
    Measurement("rule_search", "재순위(색인 E + 후보 20 + gemma4:12b) · 보류 세트 H — 제품 미반영", 63, 68, "2026-10-07",
                "H 68문제 · 같은 세트 · 1회 · 후보 20 · Hit@3", "program/scripts/rag_gate/results/rag_improve/holdout_paired.txt:5",
                note="제품에 연결돼 있지 않아 오늘 제품의 값이 아니다. 2026-10-15 저장 캐시로 오프라인 재집계해 63/68 재현"),
    Measurement("decision_unit", "gemma4:12b(학습 없음) · 결정 단위 재생 172사례×3 · 3번 모두 맞은 사례", 157, 172, "2026-09-30",
                "172사례(Claude 계열 작성 · 정답 표기) · 3회 · 제품 지시문이 이 사례로 다듬어져 12B 에 유리할 수 있다",
                "final_project_cs/wiki/records/reports/2026-10-07_오응답_실제경로와_Luna_Sol_비교_x600_반영.md:92",
                note="pass^3 91.3%(문서 구간 86.6~95.3) = 3번 모두 맞은 157/172 (환산). 오늘 해석 자리는 학습 E4B 로 바뀌어(2026-10-14) 이 값은 오늘 제품이 아니다"),
    Measurement("wrong_target_change", "엉뚱한 대상 변경 · 결정 단위 재생 172사례(안전장치 뒤)", 0, 172, "2026-10-07",
                "172사례 1회 · gemma4:12b · 안전장치(guard_target) 후", "final_project_cs/wiki/records/reports/2026-10-07_엉뚱한_대상_변경과_값_변조_재측정.md",
                note="합성 · 같은 일정 틀이 많음 · 독립 고객 자료 아님. 같은 문서가 쓰는 3회 반복(12B)에서는 1 · 0 · 1 건이었다"),
    Measurement("invented_value", "계획 읽기 값 변조, 표시 없이 새는 것 · 합성 60건(350값)", 2, 350, "2026-10-07",
                "합성 60건 · 사진/스캔 두 번 읽기 · 전체 변조는 12/350=3.4%(이 중 10건은 「확인 필요」로 표시)",
                "final_project_cs/wiki/records/reports/2026-10-07_엉뚱한_대상_변경과_값_변조_재측정.md",
                note="두 번 똑같이 오독한 경우는 못 잡는 한계. 조건: 합성 · 사람 검수 아님"),
    # 응답 시간 — x600 서버 10-14 재시험(해석 단계). 전체 응답이 아니라 「요청 해석」 단계만이다.
    Measurement("response_15s", "해석 단계(요청 해석 모델 호출)가 15초 초과한 비율 · x600 서버 실사용 재시험", 0, 324, "2026-10-14",
                "x600 서버 웹 채팅 재시험 433질문 중 해석 기록이 있는 324건(10:02~16:35) · 학습 E4B · 1회씩 · 해석 중앙 5.64초 · p95 10.49초 · 최대 13.47초",
                "datasets/chat_probe/2026-10-14_x600_web_chat_rerun/raw/trip_{a,b}_cases.csv 의 state_json.decision.seconds (이번에 다시 셈)",
                partial_scope=True,
                note="해석 단계만이다 — 규정 검색 · 일정 조회 · 답 작성을 합친 전체 응답 시간은 기록에 없다. 같은 날 Oracle ARM(무료 CPU 시험 기계)은 59.8~68.5초(4건)"),
    Measurement("response_15s", "전체 채팅 응답이 15초 초과한 비율 · Oracle ARM 무료 CPU 시험 기계(학습 없는 Gemma E4B)", 4, 4, "2026-10-12",
                "실제 HTTP 채팅 4건(59.80 · 65.11 · 67.51 · 68.53초) · 1회씩 · 같은 기계에서 평가 작업과 CPU 를 나눔",
                "final_project_cs/wiki/records/reports/2026-10-12_0454_arm-search-model-repair.md:36-39",
                note="표본 4건이라 구간이 넓다. 이 기계는 GPU 가 없는 시험 기계이고 x600 서버와 모델 · 하드웨어가 다르다"),
    # 무관한 말 오지목 — 가장 나은 판(oos2r)의 세 세트. 이미 만든 비교표(2026-10-09)에서 읽은 값
    Measurement("irrelevant_pick", "무관한 말에 일정 항목 오지목 · D(요청 없음 1,786) · 가르친 E4B oos2r", 5, 1786, "2026-10-09",
                "D 1,786 · Gemma E4B QAT Q4_0 oos2r(우리 데이터 + 잡담 보강) · 1회 · 합성(Codex·Claude 작성)",
                "program/산출물양식/w6/갱신_src/_조사노트/note_test.md A2 (원본 program/scripts/plan_qa_bench/results/comparison_abcde_20261009_111621/)",
                partial_scope=True,
                note="D 한 세트만 — 같은 판의 어려운 세트 H 함정은 3/855=0.35%지만 H 학습 자료를 같은 방식으로 만들어 낙관적일 수 있다. 시드 편차가 커서 한 판의 값으로 순위를 정하지 않는다"),
    Measurement("human_correct", "사람 판정 규정 답 「맞다」 · 어려운 표본 24행", 10, 24, "2026-10-06",
                "사람 1명 · 질문은 합성 · 평가자가 Claude 의견을 보고 판정 · 10-06 운영 색인 답",
                "final_project_cs/wiki/records/reports/2026-10-06_사람평가_첫_결과.md (rag_gate/human_eval)", real_human_questions=False,
                note="평가자 1명(두 번째 평가자 미실시) · 규정 검색 개선 뒤 다시 판정하지 않았다"),
    Measurement("urgency", "긴급도 분류 · 독립 세트 G96", 60, 96, "2026-10-07",
                "G96(독립 · 사람 라벨 아님 · AI 작성) · 학습한 e5-base 분류기 · 1회",
                "final_project_cs/wiki/records/reports/2026-10-07_오응답_실제경로와_Luna_Sol_비교_x600_반영.md:80",
                note="우리 분류기 긴급도 62.5%(60/96으로 환산). 같은 합성 분포로 학습한 CX 400 에서는 88.4%"),
]


def judge_all() -> list[dict]:
    rows = []
    for m in MEASUREMENTS:
        rows.append(judge(CRITERIA[m.key], m))
    seen = {m.key for m in MEASUREMENTS}
    rows += [judge(c, None) for k, c in CRITERIA.items() if k not in seen]
    return rows


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = judge_all()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    for r in rows:
        print(f"[{r['verdict']}] {r['criterion']} — {r.get('label', '')} :: {r['why']}")


if __name__ == "__main__":
    main()
