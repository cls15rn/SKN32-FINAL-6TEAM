# -*- coding: utf-8 -*-
"""80번 방(2026-10-02) — 역 순서 표 읽기(`LineOrder`)의 경계. 데이터 없이 돈다(합성 표).

잠그는 것:
  ① 갈래 — 갈래 끝 행선지 편은 다른 갈래의 역을 지나지 않는다(False) · 갈래 너머 본선 역은 지난다.
  ② 건너 관측 간선(`travel_min_source: observed_across`) — 시간표 없는 역의 양옆 두 간선을 **함께** 지날 때만 소요를 낸다.
     한쪽만 쓰면(그 역에서 타고 내림) None — 반씩 나눠 적은 값은 합일 때만 관측값이다.
  ③ 소요 없는 간선(관측 없는 수동 갈래 · `추정:구조만`)을 지나면 「지난다(추정)」는 말하되 소요는 None(도착 시각을 지어내지 않는다).
  ④ 목적지 앞에 근거없음 간선이 끼면 None(모른다) · 목적지 너머 근거없음은 무시.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))      # final_project_cs

from app.modules.travel_ops.mobility.engine.line_order import LineOrder  # noqa: E402


def _edge(a, b, grade="확정", tm=2.0, **kw):
    e = {"a": a, "b": b, "grade": grade}
    if tm is not None:
        e["travel_min"] = tm
    e.update(kw)
    return e


def _lo():
    # 본선 A–B–C–M–D–E(M 은 시간표 없는 역 · C–M · M–D 건너 관측 3.0 씩 = 합 6.0) + 갈래 B–X(수동 · 소요 없음) + 끝 E–F(근거없음)
    names = ["A", "B", "C", "M", "D", "E", "F", "X"]
    across = {"travel_min_source": "observed_across", "observed_across": {"mid": "M", "between": ["C", "D"], "total_min": 6.0}}
    edges = [_edge("A", "B"), _edge("B", "C"),
             _edge("C", "M", "추정:건너관측", 3.0, **across), _edge("M", "D", "추정:건너관측", 3.0, **across),
             _edge("D", "E"), _edge("E", "F", "근거없음", None), _edge("B", "X", "추정:구조만", None)]
    doc = {"built_at": "2026-10-02T00:00:00+09:00", "dest_alias": {},
           "lines": {"L": {"stations": [{"station_nm": n, "fr_order": i, "is_spur": n == "X"} for i, n in enumerate(names)],
                           "edges": edges, "dir_label": {"reliable": True}, "is_loop": False, "direction": {}}}}
    return LineOrder(doc)


def test_branch_end_train_does_not_pass_other_branch():
    lo = _lo()
    v = lo.passes("L", "C", "X", "A")            # C 에서 X(갈래 끝)행 — B 에서 갈라지므로 A 는 안 지난다
    assert v.value is False
    v = lo.passes("L", "A", "E", "X")            # 본선 E 행은 갈래 X 를 안 지난다
    assert v.value is False
    v = lo.passes("L", "X", "E", "C")            # 갈래에서 나온 편은 본선 C 를 지난다(수동 간선이라 추정)
    assert v.value is True and v.grade == "추정"


def test_across_edges_give_time_only_when_both_are_used():
    lo = _lo()
    v = lo.passes("L", "B", "E", "D")
    assert v.value is True and v.grade == "추정"
    assert lo.travel_min_on_path("L", v.path, "D") == 2.0 + 6.0       # B–C + (C–M–D 합)
    assert lo.travel_min("L", "B", "D") == 8.0
    v = lo.passes("L", "B", "E", "M")            # 시간표 없는 역에서 내림 — 지나기는 하지만 소요는 못 낸다
    assert v.value is True
    assert lo.travel_min_on_path("L", v.path, "M") is None
    assert lo.travel_min("L", "M", "E") is None  # 그 역에서 탐 — 마찬가지


def test_edge_without_time_keeps_arrival_unknown():
    lo = _lo()
    v = lo.passes("L", "C", "X", "X")
    assert v.value is True and v.grade == "추정"
    assert lo.travel_min_on_path("L", v.path, "X") is None
    assert lo.travel_min("L", "C", "X") is None


def test_unverified_edge_before_target_is_unknown():
    lo = _lo()
    v = lo.passes("L", "D", "F", "F")
    assert v.value is None and v.grade == "근거없음"
    v = lo.passes("L", "D", "F", "E")            # 근거없음이 목적지 너머면 무시
    assert v.value is True and v.grade == "확정"
