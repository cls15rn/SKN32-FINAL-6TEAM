# -*- coding: utf-8 -*-
"""상용화 기준 판정기(`eval/commercial_criteria.py`) — 구간 아래(위) 끝으로 가르는 규칙. 네트워크 없이."""
import pytest

from eval import commercial_criteria as cc


def test_wilson_matches_the_intervals_the_reports_print():
    lo, hi = cc.wilson(63, 68)                    # 보류 세트 H 재순위 — 리포트 83.9~96.8
    assert (round(lo * 100, 1), round(hi * 100, 1)) == (83.9, 96.8)
    lo, hi = cc.wilson(41, 68)                    # 제품 방식 — 리포트 48.4~71.1
    assert (round(lo * 100, 1), round(hi * 100, 1)) == (48.4, 71.1)


def test_a_point_estimate_above_the_bar_is_not_a_pass_when_the_lower_end_is_below():
    row = cc.judge(cc.CRITERIA["rule_search"], cc.Measurement("rule_search", "x", 63, 68, "d", "c", "s"))
    assert row["verdict"] == "점추정만 통과" and row["lower"] < 0.90 <= row["point"]


def test_a_large_enough_sample_passes_on_the_lower_end():
    row = cc.judge(cc.CRITERIA["rule_search"], cc.Measurement("rule_search", "x", 950, 1000, "d", "c", "s"))
    assert row["verdict"] == "합격" and row["lower"] >= 0.90


def test_below_the_bar_fails():
    row = cc.judge(cc.CRITERIA["rule_search"], cc.Measurement("rule_search", "x", 41, 68, "d", "c", "s"))
    assert row["verdict"] == "불합격"


def test_zero_observed_needs_a_big_enough_sample_to_count_as_zero():
    small = cc.judge(cc.CRITERIA["wrong_target_change"], cc.Measurement("wrong_target_change", "x", 0, 172, "d", "c", "s"))
    big = cc.judge(cc.CRITERIA["wrong_target_change"], cc.Measurement("wrong_target_change", "x", 0, 300, "d", "c", "s"))
    one = cc.judge(cc.CRITERIA["wrong_target_change"], cc.Measurement("wrong_target_change", "x", 1, 5000, "d", "c", "s"))
    assert small["verdict"] == "표본 부족" and big["verdict"] == "합격" and one["verdict"] == "불합격"
    assert cc.zero_upper(299) == pytest.approx(0.01, abs=1e-3)       # 「약 299건」


def test_max_rate_uses_the_upper_end_and_a_partial_scope_pass_is_not_a_pass():
    full = cc.judge(cc.CRITERIA["response_15s"], cc.Measurement("response_15s", "x", 0, 324, "d", "c", "s"))
    part = cc.judge(cc.CRITERIA["response_15s"], cc.Measurement("response_15s", "x", 0, 324, "d", "c", "s", partial_scope=True))
    assert full["verdict"] == "합격" and part["verdict"] == "일부 범위만 통과"


def test_no_measurement_is_stated_as_no_measurement_not_guessed():
    row = cc.judge(cc.CRITERIA["rule_search"], None)
    assert row["verdict"] == "측정 없음"


def test_no_row_claims_the_real_human_question_condition_or_a_rerun_on_todays_product():
    """오늘 이 모듈에 든 값은 모두 저장된 결과를 다시 센 것이다 — 사람이 쓴 실제 질문 기준을 충족한 줄은 없다."""
    assert not any(m.real_human_questions for m in cc.MEASUREMENTS)
    assert not any(m.reran_on_current_product for m in cc.MEASUREMENTS)


def test_every_criterion_has_a_row_even_without_a_measurement():
    keys = {r["criterion"] for r in cc.judge_all()}
    assert keys == {c.name for c in cc.CRITERIA.values()}
