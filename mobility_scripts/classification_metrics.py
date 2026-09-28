# -*- coding: utf-8 -*-
"""분류 지표 공용 유틸 (40번 방 · 설계 v2 §6-1) — 표준 라이브러리만.

`classify_report(y_true, y_pred, labels)` → confusion matrix · 클래스별 precision/recall/F1/support ·
accuracy · macro/weighted 평균. `.to_markdown()` 으로 바로 표.
`misclassified_catalog(cases, y_true, y_pred, limit_per_cell)` → 혼동 셀(실제→예측)별 사례 묶음.

0 으로 나누는 칸(예측 0건 · 실제 0건)은 표에 **—** 로 보인다(None). 단 **F1 과 macro/weighted 평균은 그 칸을 0 으로 센다** —
sklearn(zero_division=0)과 같은 값이다. 평균에서 빼면 「불가를 한 번도 안 내는 판정기」의 macro 가 부풀려진다(자체 대조 #1).
final_project_cs/tests/unit/travel/mobility/test_judgment_log.py 가 sklearn 과 칸·평균을 대조한다.
28(평가 지표)·eval_ml_compare 가 같이 쓴다.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field


def _div(a, b):
    return a / b if b else None


def _f1(p, r):
    # 분모 0 칸은 F1 = 0(sklearn zero_division=0 · 계약). 실제·예측 모두 없는 클래스도 0(GPT #6)
    p, r = p or 0.0, r or 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class ClassReport:
    labels: list
    matrix: list                      # matrix[i][j] = 실제 labels[i] → 예측 labels[j]
    per_class: dict                   # label → {precision, recall, f1, support, predicted}
    accuracy: float
    macro: dict
    weighted: dict
    n: int
    outside: int = 0                  # labels 밖 값이 섞인 건수(표에서 빠짐)
    notes: list = field(default_factory=list)

    def to_markdown(self, title=None, fmt="{:.3f}"):
        def f(v):
            return "—" if v is None else fmt.format(v)
        out = [f"**{title}**" if title else "", ""]
        out.append("| 실제 \\ 예측 | " + " | ".join(self.labels) + " | 계 |")
        out.append("|---|" + "---:|" * (len(self.labels) + 1))
        for i, lb in enumerate(self.labels):
            out.append(f"| {lb} | " + " | ".join(str(x) for x in self.matrix[i]) + f" | {sum(self.matrix[i])} |")
        out.append("")
        out.append("| 클래스 | precision | recall | F1 | support | 예측 수 |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for lb in self.labels:
            c = self.per_class[lb]
            out.append(f"| {lb} | {f(c['precision'])} | {f(c['recall'])} | {f(c['f1'])} | {c['support']} | {c['predicted']} |")
        out.append(f"| macro | {f(self.macro['precision'])} | {f(self.macro['recall'])} | {f(self.macro['f1'])} | {self.n} | |")
        out.append(f"| weighted | {f(self.weighted['precision'])} | {f(self.weighted['recall'])} | {f(self.weighted['f1'])} | {self.n} | |")
        out.append("")
        out.append(f"accuracy {f(self.accuracy)} (n={self.n})" + (f" · 라벨 밖 {self.outside}건 제외" if self.outside else ""))
        out += [f"- {x}" for x in self.notes]
        return "\n".join(x for x in out if x is not None).strip() + "\n"


def classify_report(y_true, y_pred, labels):
    labels = list(labels)
    idx = {lb: i for i, lb in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    outside = 0
    for t, p in zip(y_true, y_pred):
        if t not in idx or p not in idx:
            outside += 1
            continue
        m[idx[t]][idx[p]] += 1
    n = sum(map(sum, m))
    per = {}
    for i, lb in enumerate(labels):
        tp = m[i][i]
        sup = sum(m[i])
        pred = sum(m[r][i] for r in range(len(labels)))
        p, r = _div(tp, pred), _div(tp, sup)
        per[lb] = {"precision": p, "recall": r, "f1": _f1(p, r), "support": sup, "predicted": pred}

    def avg(key, weighted):
        vals = [(per[lb][key] or 0.0, per[lb]["support"]) for lb in labels]      # 분모 0 칸은 0(sklearn 과 같게)
        if not vals or not n:
            return None
        if weighted:
            w = sum(s for _, s in vals)
            return sum(v * s for v, s in vals) / w if w else None
        return sum(v for v, _ in vals) / len(vals)

    macro = {k: avg(k, False) for k in ("precision", "recall", "f1")}
    weighted = {k: avg(k, True) for k in ("precision", "recall", "f1")}
    acc = _div(sum(m[i][i] for i in range(len(labels))), n)
    notes = []
    skipped = [lb for lb in labels if per[lb]["precision"] is None or per[lb]["recall"] is None]
    if skipped:
        notes.append("— 는 분모 0(예측 0건 또는 실제 0건) · F1·평균에서는 0 으로 셌다(sklearn zero_division=0): " + ", ".join(skipped))
    return ClassReport(labels, m, per, acc, macro, weighted, n, outside, notes)


def binary_report(y_true, y_pred, positive):
    """한 값(예: no_data)을 양성으로 본 precision/recall/F1 + TP/FP/FN/TN."""
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p == positive)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p == positive)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p != positive)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p != positive)
    p, r = _div(tp, tp + fp), _div(tp, tp + fn)
    return {"positive": positive, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": p, "recall": r, "f1": _f1(p, r), "n": tp + fp + fn + tn}


def misclassified_catalog(cases, y_true, y_pred, limit_per_cell=5):
    """(실제, 예측) 셀 → {count, examples[:limit]}. 셀은 건수 많은 순."""
    cells = collections.OrderedDict()
    for c, t, p in zip(cases, y_true, y_pred):
        if t == p:
            continue
        cell = cells.setdefault((t, p), {"count": 0, "examples": []})
        cell["count"] += 1
        if len(cell["examples"]) < limit_per_cell:
            cell["examples"].append(c)
    return dict(sorted(cells.items(), key=lambda kv: -kv[1]["count"]))
