#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""28 — LineOrder.passes() 는 출발~목적지 구간의 간선 등급만 본다(목적지 너머는 경로 존재만).

옛 판은 행선지까지 전체 경로의 최저 등급을 봐서, 04호선 오이도행이 남태령 훨씬 너머 안산–신길온천(근거없음 ·
신길온천 시간표 0행) 때문에 「남태령을 지난다」도 못 믿었다 → 오이도행 전부 버림.
잠그는 것: ① 목적지 너머 근거없음은 무시 ② 목적지 앞에 근거없음이 끼면 여전히 근거없음(None) ③ 지나지 않는 것은 False.

  python mobility_scripts/mobility_checks/check_passes28_v1.py      (저장소 루트 · PYTHONPATH=final_project_cs)
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine.line_order import LineOrder   # noqa: E402

CASES = [
    # (이름, (노선, 출발, 행선지, 목적지), 기대 value, 기대 등급 또는 None)
    ("beyond_target_ignored", ("04호선", "사당", "오이도", "남태령"), True, "추정"),
    ("beyond_target_ignored_far", ("04호선", "사당", "오이도", "안산"), True, "추정"),
    ("weak_edge_before_target", ("04호선", "사당", "오이도", "정왕"), None, "근거없음"),
    ("short_turn_not_passing", ("04호선", "사당", "안산", "정왕"), False, None),
    ("onsu_passes_namseong", ("07호선", "고속터미널", "온수", "남성"), True, "확정"),
    ("onsu_not_kkachiul", ("07호선", "고속터미널", "온수", "까치울"), False, None),
]


def main():
    lo = LineOrder.load()
    bad = 0
    for name, args, want_v, want_g in CASES:
        v = lo.passes(*args)
        ok = v.value is want_v and (want_g is None or v.grade == want_g)
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name}: {args} → {v.value} {v.grade}"
              + ("" if ok else f" (기대 {want_v} {want_g})"))
    print(f"[passes28] {len(CASES) - bad}/{len(CASES)} 통과 · 실패 {bad}")
    return 1 if bad else 0


def test_passes28():          # 67: pytest 수집용 — 실데이터(line_station_order) 시험 · 없는 기기는 SKIP
    from app.modules.travel_ops.mobility.engine.paths import PROCESSED
    if not (PROCESSED / "mobility" / "line_station_order_v1.json").exists():
        import pytest
        pytest.skip("data not present (DATA_DIR/travel/processed/mobility/line_station_order_v1.json)")
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
