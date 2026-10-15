"""사람 검토 일치도 — 카파 · 가중 카파 · 분산 0 · 모름 제외 · 사람 대 사람 / 사람 대 AI 를 따로."""
from __future__ import annotations

import pytest

from eval.human_review import agreement
from eval.human_review.core import Bundle, Field, Item

ORDER = ("맞다", "중간", "틀리다")


def test_kappa_matches_sklearn_on_unbalanced_labels():
    sk = pytest.importorskip("sklearn.metrics")
    pairs = ([("맞다", "맞다")] * 8 + [("맞다", "중간")] * 2 + [("중간", "중간")] * 2 + [("중간", "틀리다")] * 1
             + [("틀리다", "틀리다")] * 5 + [("틀리다", "중간")] * 2 + [("맞다", "틀리다")] * 1 + [("틀리다", "맞다")] * 1)
    a, b = [x for x, _ in pairs], [y for _, y in pairs]
    got = agreement.cohen_kappa(a, b, ORDER)
    assert got["kappa"] == pytest.approx(sk.cohen_kappa_score(a, b), abs=1e-3)
    assert got["weighted_kappa"] == pytest.approx(sk.cohen_kappa_score(a, b, labels=list(ORDER), weights="linear"))
    assert not got["degenerate"] and got["n"] == len(a)


def test_perfect_agreement_gives_one_and_zero_variance_is_flagged():
    got = agreement.cohen_kappa(["맞다", "중간", "틀리다", "맞다"], ["맞다", "중간", "틀리다", "맞다"], ORDER)
    assert got["kappa"] == 1.0 and got["weighted_kappa"] == 1.0 and not got["degenerate"]
    flat = agreement.cohen_kappa(["맞다"] * 5, ["맞다"] * 5, ORDER)           # 분산 0: 0/0 이라 정의 불가 → 1.0 으로 두고 표시
    assert flat["kappa"] == 1.0 and flat["degenerate"] is True


def test_chance_level_and_one_sided_constant():
    assert agreement.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])["kappa"] == pytest.approx(0.0, abs=1e-3)
    one_sided = agreement.cohen_kappa(["a"] * 4, ["a", "a", "b", "b"])        # 한쪽만 고정 — 우연 일치 기대와 같아 0
    assert one_sided["kappa"] == pytest.approx(0.0, abs=1e-3) and not one_sided["degenerate"]


def test_too_few_rows_and_length_mismatch():
    assert agreement.cohen_kappa(["a"], ["a"])["kappa"] is None
    assert agreement.cohen_kappa([], [])["kappa"] is None
    with pytest.raises(ValueError):
        agreement.cohen_kappa(["a"], ["a", "b"])
    assert agreement.percent_agreement([], []) is None


def bundle() -> Bundle:
    return Bundle(
        name="t", title="t", context_columns=["x"],
        fields=[Field("verdict", "판정", "choice", ORDER + ("모름",), ordered=ORDER, exclude=("모름",)),
                Field("extras", "함께", "set", ("a", "b", "c"), required=False),
                Field("why", "이유", "text", required=False)],
        items=[Item(id=str(n), context={"x": "x"}) for n in range(6)],
    )


def lab(v, extras=()):
    return {"verdict": v, "extras": list(extras), "why": "무시됨"}


def test_compare_excludes_unknown_and_reports_counts_and_disagreements():
    a = {"0": lab("맞다"), "1": lab("중간"), "2": lab("틀리다"), "3": lab("모름"), "4": lab("맞다"), "5": lab("틀리다")}
    b = {"0": lab("맞다"), "1": lab("틀리다"), "2": lab("틀리다"), "3": lab("맞다"), "4": lab("맞다"), "9": lab("맞다")}
    result = agreement.compare(bundle(), a, b, "사람 A", "사람 B")
    verdict = result["fields"]["verdict"]
    assert result["ids_paired"] == 5 and (result["a"], result["b"]) == ("사람 A", "사람 B")
    assert verdict["n"] == 4 and verdict["excluded"] == 1                          # id 3 은 모름이라 뺀다 · id 5 와 9 는 짝이 없다
    assert verdict["exact_match"] == pytest.approx(3 / 4, abs=1e-3) and verdict["disagree_ids"] == ["1"]
    assert "why" not in result["fields"] and "warning" in verdict                  # 자유 기재 칸은 안 센다 · 30 미만 경고
    text = agreement.summarize(result)
    assert "n=4" in text and "사람 A" in text and "카파" in text


def test_set_field_uses_exact_set_match_and_jaccard():
    a = {"0": lab("맞다", ["a", "b"]), "1": lab("맞다", []), "2": lab("맞다", ["c"])}
    b = {"0": lab("맞다", ["b", "a"]), "1": lab("맞다", []), "2": lab("맞다", ["a"])}
    extras = agreement.compare(bundle(), a, b, "A", "B")["fields"]["extras"]
    assert extras["exact_match"] == pytest.approx(2 / 3, abs=1e-3)                           # 순서가 달라도 같은 집합이면 일치
    assert extras["mean_jaccard"] == pytest.approx((1 + 1 + 0) / 3, abs=1e-3) and extras["disagree_ids"] == ["2"]


def test_human_vs_ai_is_reported_separately_from_human_vs_human():
    bun = bundle()
    for it in bun.items:
        it.ai = {"draft": {"verdict": "맞다", "extras": []}}
    h1 = {str(n): lab(v) for n, v in enumerate(["맞다", "중간", "틀리다", "맞다", "맞다", "중간"])}
    h2 = {str(n): lab(v) for n, v in enumerate(["맞다", "중간", "맞다", "맞다", "맞다", "중간"])}
    hh = agreement.compare(bun, h1, h2, "사람 1", "사람 2")
    ha = agreement.compare(bun, h1, bun.ai_labels("draft"), "사람 1", "AI 초안 draft")
    assert hh["b"] == "사람 2" and ha["b"] == "AI 초안 draft"
    assert hh["fields"]["verdict"]["exact_match"] == pytest.approx(5 / 6, abs=1e-3)
    assert ha["fields"]["verdict"]["exact_match"] == pytest.approx(3 / 6, abs=1e-3)           # AI 는 전부 「맞다」 — 분산 0 이라 카파는 0
    assert ha["fields"]["verdict"]["kappa"] == pytest.approx(0.0, abs=1e-3)
    assert bun.ai_sources() == ["draft"]
