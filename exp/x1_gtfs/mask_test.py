#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""행선지 가림 안정성 + 잇기 자체 점검 (X1 · 2026-10-04 · GPT 대조 10/5 반영)

★ 이 숫자는 「정확도」가 아니다 — 같은 방법으로 두 번 이은 결과가 서로 같은 비율이다(GPT 대조 #5). 두 실행이 같은 시차 학습·
  같은 확정 규칙을 쓰므로 같은 방향으로 틀리면 서로 같다고 나온다. 정답 대조는 열차 번호가 있는 원천으로만 할 수 있다.
덧붙인 점검(GPT 대조 #1·#2·#3):
  공식 대조  배운 간선 시차를 서울교통공사 공식 주행시간(역 순서 표의 official_travel_min — 시간표와 독립)과 댄다
  관문       응암(6호선)에서 「새절→응암→역촌」「구산→응암→새절」이 아닌 이음 수
  순서 섞기  입력 행 순서를 섞어 다시 이었을 때 편이 달라지는 수

가림: 행선지를 전부 가리고(없음으로) 시각만으로 이었을 때, 행선지를 보고 이은 결과와 얼마나 같은가.

열차 번호가 없어 잇기의 정답을 직접 댈 수 없다. 행선지는 「같은 열차」의 독립된 단서라, 이것을 가리고도 같은 짝이 나오면
시각만으로 이은 부분(행선지가 같은 급행·완행 · 행선지 없는 행)도 그만큼 믿을 수 있다. 4호선·7호선 행선지 채우기(28번)의 가림 시험과 같은 생각이다.
  같은 짝 n / 행선지로 이은 짝 m  (노선별)
dir 라벨을 믿을 수 없는 노선(신림선·인천2호선)과 순환선은 가리면 방향을 정할 수 없어 뺀다.
  python exp/x1_gtfs/mask_test.py
"""
import argparse
import collections
import gzip
import json
import random
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
    print(f"  가림 합계(안정성 · 정확도 아님): 같은 짝 {tot[0]:,}/{tot[1]:,} ({100 * tot[0] / tot[1]:.2f}%) · 다른 짝 {tot[2]:,}")
    rep["_total"] = {"same": tot[0], "base": tot[1], "other_pair": tot[2]}

    # 공식 대조 — 시간표와 독립인 값(서울교통공사 역간 주행시간 · 정차 제외)
    T, delta, succ, pred = base["T"], base["delta"], base["succ"], base["pred"]
    off = {}
    for ln, L in order["lines"].items():
        for e in L["edges"]:
            if e.get("official_travel_min") is not None:
                off[(ln, e["a"], e["b"])] = off[(ln, e["b"], e["a"])] = e["official_travel_min"] * 60
    learned = [(delta[k][0] - v) for k, v in off.items() if k in delta]
    out_rng = sum(1 for d in learned if not -30 <= d <= 150)
    used = [T[j] - T[i] - off[(rows[i]["line"], rows[i]["station_nm"], rows[j]["station_nm"])] for i, j in succ.items()
            if (rows[i]["line"], rows[i]["station_nm"], rows[j]["station_nm"]) in off]
    used_bad = sum(1 for d in used if d < -30 or d > 420)
    print(f"  공식 대조: 공식 주행시간이 있는 간선(방향) {len(learned)} 중 배운 시차가 공식 −30~+150초 밖 {out_rng} · "
          f"그 간선에서 쓴 이음 {len(used):,} 중 공식 −30초 미만·+420초 초과 {used_bad}")
    rep["_official"] = {"edges": len(learned), "edges_out_of_range": out_rng, "links": len(used), "links_out_of_range": used_bad}

    # 관문(응암)
    gate = collections.Counter()
    for ln, g in chain.LOOP_GATE.items():
        for i, r in enumerate(rows):
            if r["line"] == ln and r["station_nm"] == g[0] and i in pred and i in succ:
                ok = (rows[pred[i]]["station_nm"], rows[succ[i]]["station_nm"]) in ((g[1], g[2]), (g[3], g[1]))
                gate["ok" if ok else "bad"] += 1
    print(f"  관문(응암): 규칙에 맞는 통과 {gate['ok']:,} · 어긋난 통과 {gate['bad']}")
    rep["_gate"] = dict(gate)

    # 순서 섞기
    def trips(rs, R):
        out_ = set()
        for h in range(len(rs)):
            if h in R["pred"]:
                continue
            ch = [h]
            while ch[-1] in R["succ"]:
                ch.append(R["succ"][ch[-1]])
            out_.add(tuple((rs[i]["line"], rs[i]["station_nm"], rs[i]["day_type"], rs[i]["dep_time"], rs[i]["dir"], rs[i].get("dest_nm"))
                           for i in ch))
        return out_
    t0 = trips(rows, base)
    rr = rows[:]
    random.Random(1).shuffle(rr)
    t1 = trips(rr, chain.build(rr, order, log=quiet))
    print(f"  순서 섞기: 사슬 {len(t0):,} 중 달라진 것 {len(t0 - t1)}")
    rep["_shuffle"] = {"chains": len(t0), "changed": len(t0 - t1)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mask_test.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
