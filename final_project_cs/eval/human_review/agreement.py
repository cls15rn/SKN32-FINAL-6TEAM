# -*- coding: utf-8 -*-
"""두 라벨 묶음의 일치도 — 사람 두 명(평가자 간)과 사람 대 AI 초안(따로)을 낸다.

★평가자 간 일치도와 사람-대-AI 일치도는 **다른 숫자**다. 한 보고에 섞어 쓰지 않는다 — `compare()` 는 한 쌍만 비교하고
  어느 쪽이 무엇인지(`a_name` · `b_name`)를 결과에 남긴다.
★카파: 우연히 맞을 몫을 뺀 일치도. 두 쪽이 모두 한 값만 냈고 서로 같으면(분산 0) 정의가 0/0 이라 **1.0 으로 두고 `degenerate` 표시**를 남긴다
  (`eval/stats/agreement.py` 와 같은 약속). 한쪽만 분산이 0 이고 서로 다르면 카파는 0.0 이다.
★표본이 30 미만이면 `warning` 을 단다 — 점 추정치이고 구간은 낼 수 없다.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .core import Bundle, Field


def percent_agreement(a: list, b: list) -> float | None:
    if not a:
        return None
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def cohen_kappa(a: list, b: list, order: tuple[str, ...] = ()) -> dict[str, Any]:
    """Cohen's kappa. `order` 가 있으면 선형 가중 카파(`weighted`)도 낸다. n < 2 면 값이 None."""
    n = len(a)
    if n != len(b):
        raise ValueError("두 목록의 길이가 다르다")
    out: dict[str, Any] = {"n": n, "kappa": None, "weighted_kappa": None, "degenerate": False}
    if n < 2:
        return out
    cats = sorted(set(a) | set(b), key=lambda v: (order.index(v) if v in order else len(order), str(v)))
    ca, cb = Counter(a), Counter(b)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((ca[c] / n) * (cb[c] / n) for c in cats)
    if pe >= 1.0:
        out["kappa"] = 1.0 if po == 1.0 else 0.0
        out["degenerate"] = True
    else:
        out["kappa"] = (po - pe) / (1 - pe)
    if order and all(c in order for c in cats) and len(cats) > 1:
        k = len(order) - 1
        pos = {c: order.index(c) for c in cats}
        w = lambda x, y: abs(pos[x] - pos[y]) / k          # noqa: E731  선형 가중
        obs = sum(w(x, y) for x, y in zip(a, b)) / n
        exp = sum((ca[x] / n) * (cb[y] / n) * w(x, y) for x in cats for y in cats)
        out["weighted_kappa"] = (1.0 if obs == 0 else 0.0) if exp == 0 else 1 - obs / exp
    return out


def _round(v: float | None, nd: int = 4) -> float | None:
    return None if v is None else round(v, nd)


def _paired(f: Field, a: dict[str, dict], b: dict[str, dict]) -> tuple[list, list, list[str], list[str]]:
    """같은 id 만 짝지어 (a 값, b 값, 짝지은 id, 뺀 id) 를 돌려준다. `exclude` 값(예: 모름)이 한쪽이라도 있으면 뺀다."""
    ids = sorted(set(a) & set(b))
    va, vb, used, dropped = [], [], [], []
    for i in ids:
        x, y = a[i].get(f.key), b[i].get(f.key)
        if x is None or y is None:
            dropped.append(i)
            continue
        if f.kind == "set":
            x, y = tuple(sorted(x)), tuple(sorted(y))
        if f.exclude and (x in f.exclude or y in f.exclude):
            dropped.append(i)
            continue
        va.append(x)
        vb.append(y)
        used.append(i)
    return va, vb, used, dropped


def compare(bundle: Bundle, a: dict[str, dict], b: dict[str, dict], a_name: str, b_name: str) -> dict[str, Any]:
    """`a`, `b`: {id: {칸 key: 값}}. 칸마다 일치율 · 카파(선택지 칸)나 집합 일치율 · 평균 자카드(집합 칸)를 낸다."""
    result: dict[str, Any] = {"a": a_name, "b": b_name, "ids_in_a": len(a), "ids_in_b": len(b),
                              "ids_paired": len(set(a) & set(b)), "fields": {}}
    for f in bundle.fields:
        if f.kind == "text":
            continue
        va, vb, used, dropped = _paired(f, a, b)
        entry: dict[str, Any] = {"column": f.column, "n": len(va), "excluded": len(dropped),
                                 "exact_match": _round(percent_agreement(va, vb))}
        if f.kind == "choice":
            k = cohen_kappa(va, vb, f.ordered)
            entry.update(kappa=_round(k["kappa"]), weighted_kappa=_round(k["weighted_kappa"]), degenerate=k["degenerate"],
                         a_counts=dict(Counter(va)), b_counts=dict(Counter(vb)))
            disagree = [i for i, x, y in zip(used, va, vb) if x != y]
        else:
            jac = []
            for x, y in zip(va, vb):
                sx, sy = set(x), set(y)
                jac.append(1.0 if not (sx | sy) else len(sx & sy) / len(sx | sy))
            entry["mean_jaccard"] = _round(sum(jac) / len(jac)) if jac else None
            disagree = [i for i, x, y in zip(used, va, vb) if x != y]
        entry["disagree_ids"] = disagree
        if len(va) < 30:
            entry["warning"] = f"n={len(va)} — 표본이 작다(30 미만). 점 추정치이고 구간은 못 낸다"
        result["fields"][f.key] = entry
    return result


def human_labels(data: dict) -> dict[str, dict]:
    return data["labels"]


def ai_as_labels(bundle: Bundle, source: str) -> dict[str, dict]:
    return bundle.ai_labels(source)


def summarize(result: dict[str, Any]) -> str:
    """사람이 읽는 몇 줄 요약(보고서에 옮길 때 분자·분모가 같이 나가게)."""
    lines = [f"[{result['a']}] 대 [{result['b']}] — 짝지은 id {result['ids_paired']} (a {result['ids_in_a']} · b {result['ids_in_b']})"]
    for key, e in result["fields"].items():
        part = f"  {e['column']}: n={e['n']} 일치 {e['exact_match']}"
        if "kappa" in e:
            part += f" · 카파 {e['kappa']}" + (f" · 가중 카파 {e['weighted_kappa']}" if e.get("weighted_kappa") is not None else "")
            if e.get("degenerate"):
                part += " (분산 0 — 카파 정의 불가, 1.0 으로 둠)"
        else:
            part += f" · 평균 자카드 {e.get('mean_jaccard')}"
        if e["excluded"]:
            part += f" · 뺀 행 {e['excluded']}"
        if "warning" in e:
            part += f" · ※ {e['warning']}"
        lines.append(part)
    return "\n".join(lines)


__all__ = ["cohen_kappa", "percent_agreement", "compare", "summarize", "human_labels", "ai_as_labels"]
