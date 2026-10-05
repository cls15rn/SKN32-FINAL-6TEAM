#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""가림 시험 — 행선지를 전부 가리고(없음으로) 시각만으로 이었을 때, 행선지를 보고 이은 결과와 얼마나 같은가. (X1 · 2026-10-04)

열차 번호가 없어 잇기의 정답을 직접 댈 수 없다. 행선지는 「같은 열차」의 독립된 단서라, 이것을 가리고도 같은 짝이 나오면
시각만으로 이은 부분(행선지가 같은 급행·완행 · 행선지 없는 행)도 그만큼 믿을 수 있다. 4호선·7호선 행선지 채우기(28번)의 가림 시험과 같은 생각이다.
  같은 짝 n / 행선지로 이은 짝 m  (노선별)
dir 라벨을 믿을 수 없는 노선(신림선·인천2호선)과 순환선은 가리면 방향을 정할 수 없어 뺀다.
  python exp/x1_gtfs/mask_test.py
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chain  # noqa: E402

REPO = next((p for p in HERE.parents if (p / "datasets" / "mobility").is_dir()), HERE.parents[1])
M = REPO / "datasets" / "mobility" / "processed" / "mobility"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timetable", default=str(M / "timetable_v1.jsonl.gz"))
    ap.add_argument("--order", default=str(M / "line_station_order_v1.json"))
    ap.add_argument("--out", default=str(REPO.parent / "exp" / "x1_gtfs" / "out"))
    a = ap.parse_args()
    rows = [json.loads(ln) for ln in gzip.open(a.timetable, "rt", encoding="utf-8") if ln.strip()]
    order = json.loads(Path(a.order).read_text(encoding="utf-8"))
    quiet = lambda *x: None  # noqa: E731
    base = chain.build(rows, order, log=quiet)
    skip = {ln for ln, L in order["lines"].items()
            if L.get("is_loop") or not (L.get("dir_label") or {}).get("reliable", True)}
    masked_rows = [dict(r, dest_nm=None) if r["line"] not in skip else r for r in rows]
    mk = chain.build(masked_rows, order, log=quiet)
    rep, tot = {}, [0, 0, 0]
    for ln in sorted(order["lines"]):
        if ln in skip:
            continue
        b = {i: j for i, j in base["succ"].items() if rows[i]["line"] == ln and rows[i].get("dest_nm") and rows[j].get("dest_nm")
             and i not in base["conflict"] and j not in base["conflict"]}
        same = sum(1 for i, j in b.items() if mk["succ"].get(i) == j)
        other = sum(1 for i, j in b.items() if i in mk["succ"] and mk["succ"][i] != j)
        rep[ln] = {"same": same, "base": len(b), "other_pair": other, "not_linked": len(b) - same - other}
        tot[0] += same
        tot[1] += len(b)
        tot[2] += other
        print(f"  {ln}: 같은 짝 {same:,}/{len(b):,} ({100 * same / max(1, len(b)):.2f}%) · 다른 짝 {other:,} · 못 이음 {len(b) - same - other:,}")
    print(f"  합계: 같은 짝 {tot[0]:,}/{tot[1]:,} ({100 * tot[0] / tot[1]:.2f}%) · 다른 짝 {tot[2]:,}")
    rep["_total"] = {"same": tot[0], "base": tot[1], "other_pair": tot[2]}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mask_test.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
