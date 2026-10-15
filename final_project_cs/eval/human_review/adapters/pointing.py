# -*- coding: utf-8 -*-
"""일정 짚기 — 「이 문장이 일정의 몇 번 항목을 가리키는가(없음/애매)」를 사람이 다는 세 묶음.

  pointing_h          어려운 세트 H 시험용 이견 64  (솔 6.1 이 쓰고 루나 6.0 이 라벨했는데 둘이 갈린 것)
  pointing_mx_dispute MX 세트 이견 558              (같은 두 모델이 갈린 것 — MX4 410 이 대부분)
  pointing_mx_check   MX 세트 사람 점검 표본 158     (두 모델이 합의한 정답을 사람이 확인하는 표본 — 기존 CSV 는 기계 정답이 보였다)

시트에는 고객 문장 · 일정 표 · 풀이 기준만 싣는다. 솔 · 루나의 정답은 `Item.ai` 에만 있다(시트에 안 실린다).
★AI Hub · KoCulture 말뭉치 문장이 섞인 입력이 있다 — 시트는 로컬에만 둔다(외부 서비스 전송 금지).
"""
from __future__ import annotations

import csv
import json
import random
import re

from ..core import SEED, Bundle, Field, Item, sample_ids
from .paths import Sources

NONE_, VAGUE = "없음", "애매"
MX4_SAMPLE = 52                    # 우선 시트에 넣는 MX4 이견 수

CRITERIA = ("고객 문장이 일정의 항목 하나를 가리키면 그 번호. 일정에 없는 장소 · 일반 정보 · 다른 가게를 묻거나, 아무것도 묻거나 요청하지 않는 "
            "감상 · 잡담이면 「없음」. 두 항목 이상이 똑같이 맞아 보이거나 문장만으로 정할 수 없으면 「애매」. "
            "(문장 속 잡담 부분은 무시하고 요청 부분이 가리키는 항목을 본다. 일정 표의 번호는 표 맨 앞 숫자다)")

SPLIT = re.compile(r"고객 질문: ", re.S)
ITEM_LINE = re.compile(r"^(\d+)\. ", re.M)


def _answer(value) -> str | None:
    if value is None:
        return NONE_
    if value == "m":                       # 라벨러가 답을 안 낸 칸(결측)
        return None
    return str(value)


def _head(prompt: str) -> str:
    return SPLIT.split(prompt, maxsplit=1)[0].rstrip()


def _numbers(head: str) -> tuple[str, ...]:
    return tuple(ITEM_LINE.findall(head))


def _item(item_id: str, schedule_id: str, sentence: str, req: str, head: str, ai: dict, meta: dict) -> Item:
    nums = _numbers(head)
    return Item(
        id=item_id,
        context={"일정 번호": schedule_id, "고객 문장": sentence, "요청 부분만(참고)": req, "일정 표": head},
        criteria=CRITERIA,
        allowed={"item": (*nums, NONE_, VAGUE)},
        ai={k: {"item": v} for k, v in ai.items() if v is not None},
        meta=meta,
    )


def _fields() -> list[Field]:
    return [Field("item", "판정(번호/없음/애매)", "choice")]


CONTEXT = ["일정 번호", "고객 문장", "요청 부분만(참고)", "일정 표"]


def build_h(src: Sources | None = None) -> Bundle:
    src = src or Sources()
    path = src.need(src.plan_qa / "results" / "hard_set" / "h_set_test_30_20261015.json", "H 시험용 전체 파일")
    rows = [r for r in json.loads(path.read_text(encoding="utf-8")) if r["status"] == "dispute"]
    items = [_item(f"H-{r['i']}", f"H-{r['itin']}", r["q"], "", _head(r["prompt"]),
                   {"sol_author": _answer(r["gold"]), "luna_label": _answer(r["label_other"])},
                   {"type": r["type"], "itin": r["itin"]}) for r in rows]
    items.sort(key=lambda it: (it.meta["itin"], it.id))
    return Bundle("pointing_h", "H 이견 64 — 일정 짚기 사람 판정", _fields(), items, CONTEXT,
                  notes=("솔 6.1 이 쓰고 루나 6.0 이 따로 라벨했는데 갈린 64개(함정 N1 32 · N6 13 · 대조 P2 10 · P3 7 · P1 2). "
                         "시험 점수에서는 빠져 있다. 사람 판정이 생기면 골든셋 후보가 된다. second30 = 두 번째 평가자(카파)용 30행 무작위."),
                  subsets={"second30": lambda: sample_ids([it.id for it in items], 30)})


def _mx_heads(src: Sources) -> dict[int, str]:
    path = src.need(src.datasets_root / "travel" / "mx_testset" / "mx_all.json", "MX 전체 파일")
    heads: dict[int, str] = {}
    for x in json.loads(path.read_text(encoding="utf-8")):
        heads.setdefault(x["itin"], _head(x["prompt"]))
    return heads


def build_mx_dispute(src: Sources | None = None) -> Bundle:
    src = src or Sources()
    heads = _mx_heads(src)
    disputes = json.loads(src.need(src.datasets_root / "travel" / "mx_testset" / "mx_disputes.json", "MX 이견 파일").read_text(encoding="utf-8"))
    items = []
    for n, d in enumerate(disputes):
        luna = d["luna_item"] if d["src"] == "code" else d.get("luna_blend")
        items.append(_item(f"MXD-{n:03d}", f"MX-{d['k']}", d["q"], d.get("req", ""), heads[d["k"]],
                           {"sol_author": _answer(d["gen_item"]), "luna_label": _answer(luna)},
                           {"cat": d["cat"], "src": d["src"], "itin": d["k"]}))
    items.sort(key=lambda it: (it.meta["itin"], it.id))

    def first200() -> list[str]:
        non_mx4 = [it.id for it in items if it.meta["cat"] != "MX4"]
        mx4 = sorted(it.id for it in items if it.meta["cat"] == "MX4")
        pick = set(random.Random(SEED).sample(mx4, MX4_SAMPLE))
        return [it.id for it in items if it.id in set(non_mx4) | pick]

    def second30() -> list[str]:
        return sample_ids(first200(), 30)

    return Bundle("pointing_mx_dispute", "MX 이견 558 — 일정 짚기 사람 판정", _fields(), items, CONTEXT,
                  notes=("MX 세트를 만들 때 솔 6.1 과 루나 6.0 의 답이 갈려 점수 계산에서 뺀 558개(MX4 410 · MX1 64 · MX2 56 · MX6a 25 · MX3 3). "
                         "먼저 볼 부분집합 first200 = MX4 가 아닌 148 전부 + MX4 중 52(시드 20261015 무작위)."),
                  subsets={"first200": first200, "second30": second30})


def build_mx_check(src: Sources | None = None) -> Bundle:
    src = src or Sources()
    heads = _mx_heads(src)
    sample = list(csv.DictReader(src.need(src.datasets_root / "travel" / "mx_testset" / "mx_human_check_sample.csv", "MX 사람 점검 표본").open(encoding="utf-8-sig", newline="")))
    items = []
    for r in sample:
        k = int(r["일정 번호(일정 파일)"])
        gold = r["기계 정답(번호/없음)"]          # 기계 정답 — AI 라벨이다(시트에 안 싣는다)
        items.append(_item(f"MXC-{r['id']}", f"MX-{k}", r["고객 입력(혼합)"], r["요청만(대조)"], heads[k],
                           {"sol_luna_agreed": gold}, {"cat": r["유형"], "src": r["생성 방식"], "itin": k}))
    items.sort(key=lambda it: (it.meta["itin"], it.id))
    return Bundle("pointing_mx_check", "MX 사람 점검 표본 158 — 일정 짚기 사람 판정", _fields(), items, CONTEXT,
                  notes="솔 6.1 · 루나 6.0 이 합의한 정답이 맞는지 보는 층화 표본(유형 × 생성 방식). 기존 mx_human_check_sample.csv 와 달리 기계 정답 열이 없다. second30 = 두 번째 평가자용 30행.",
                  subsets={"second30": lambda: sample_ids([it.id for it in items], 30)})
