#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""잇기 검수 — 묶음(열차 잇기)이 그럴듯한지 원천만으로 볼 수 있는 숫자. (X1 · 2026-10-04)

보존 검사 5 는 「행이 빠지거나 바뀌지 않았다」를 본다. 「서로 다른 열차를 한 편으로 잘못 묶지 않았다」는 열차 번호가 없어
직접 볼 수 없다. 여기서는 간접 숫자 넷을 낸다(정답 대조가 아니다 — 정답은 열차 번호가 있는 원천으로만 댈 수 있다).
  A 한 편 안에 행선지가 둘 이상(순환선·원천 결함 표기 제외)
  B 이웃 정차 시차가 그 간선 중앙값의 0.4배 미만 / 2.5배 초과 (건너뛰기 제외)
  C 같은 간선에서 앞뒤가 뒤바뀐 쌍(추월) — 복복선 급행이 아니면 일어날 수 없다
  D 편의 첫 역 · 끝 역 분포 — 열차가 생기거나 끝날 수 없는 역에서 시작·끝나는 편
  python exp/x1_gtfs/link_check.py
"""
import argparse
import collections
import csv
import gzip
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next((p for p in HERE.parents if (p / "datasets" / "mobility").is_dir()), HERE.parents[1])
M = REPO / "datasets" / "mobility" / "processed" / "mobility"


def sec(s):
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timetable", default=str(M / "timetable_v1.jsonl.gz"))
    ap.add_argument("--order", default=str(M / "line_station_order_v1.json"))
    ap.add_argument("--out", default=str(REPO.parent / "exp" / "x1_gtfs" / "out"))
    a = ap.parse_args()
    out = Path(a.out)
    rows = [json.loads(ln) for ln in gzip.open(a.timetable, "rt", encoding="utf-8") if ln.strip()]
    order = json.loads(Path(a.order).read_text(encoding="utf-8"))
    alias = order.get("dest_alias") or {}
    loops = {ln for ln, L in order["lines"].items() if L.get("is_loop")}
    adj = {ln: {(e["a"], e["b"]) for e in L["edges"]} | {(e["b"], e["a"]) for e in L["edges"]} for ln, L in order["lines"].items()}
    deg = {ln: collections.Counter(x for x, _ in adj[ln]) for ln in adj}
    with gzip.open(out / "row_map.csv.gz", "rt", encoding="utf-8") as f:
        tr = collections.defaultdict(list)
        for m in csv.DictReader(f):
            tr[m["trip_id"]].append((int(m["stop_sequence"]), int(m["row_no"]) - 1))
    defect = set()
    cr = out / "check_report.json"
    if cr.exists():
        for ln, day, dr, dest, _ in json.loads(cr.read_text(encoding="utf-8")).get("dest_label_defect", []):
            defect.add((ln, day, dr, dest))
    est = {r["trip_id"] for r in csv.DictReader(open(out / "est_stops.csv", encoding="utf-8-sig"))}

    multi, hops, heads, tails = [], collections.defaultdict(list), collections.Counter(), collections.Counter()
    edge_pairs = collections.defaultdict(list)
    skip_hops = 0
    for t, v in tr.items():
        v.sort()
        idx = [i for _, i in v]
        ln, day = rows[idx[0]]["line"], rows[idx[0]]["day_type"]
        ds = set()
        for i in idx:
            r = rows[i]
            d = r.get("dest_nm")
            if d and (ln, day, r["dir"], d) not in defect and ln not in loops:
                ds.add(alias.get(ln, {}).get(d, d))
        if len(ds) > 1:
            multi.append((t, ln, sorted(ds)))
        for x, y in zip(idx, idx[1:]):
            s, n = rows[x]["station_nm"], rows[y]["station_nm"]
            dt = sec(rows[y]["dep_time"]) - sec(rows[x]["dep_time"])
            if (s, n) in adj[ln]:
                hops[(ln, s, n)].append((dt, t))
                edge_pairs[(ln, day, s, n)].append((sec(rows[x]["dep_time"]), sec(rows[y]["dep_time"])))
            else:
                skip_hops += 1
        heads[(ln, rows[idx[0]]["station_nm"])] += 1
        if t not in est:
            tails[(ln, rows[idx[-1]]["station_nm"])] += 1
    odd = []
    for k, v in hops.items():
        med = statistics.median(x for x, _ in v)
        odd += [(k, dt, med, t) for dt, t in v if dt < 0.4 * med or dt > 2.5 * med]
    cross = collections.Counter()
    for (ln, day, s, n), v in edge_pairs.items():
        v.sort()
        mx = -1
        for _, b in v:
            if b < mx:
                cross[ln] += 1
            mx = max(mx, b)
    mid_head = collections.Counter()
    for (ln, s), c in heads.items():
        if deg[ln][s] != 1 and c < 5:                        # 끝 역이 아니고 하루 몇 편만 시작하는 역
            mid_head[ln] += c
    n_trip = len(tr)
    n_hop = sum(len(v) for v in hops.values())
    print(f"  편 {n_trip:,} · 이웃 간선 이음 {n_hop:,} · 건너뛰기 이음 {skip_hops:,}")
    print(f"  A 한 편에 행선지 둘 이상: {len(multi)}편  {collections.Counter(m[1] for m in multi).most_common(6)}")
    print(f"  B 시차가 간선 중앙값의 0.4배 미만·2.5배 초과: {len(odd):,}건 ({100 * len(odd) / n_hop:.3f}%)  "
          f"{collections.Counter(o[0][0] for o in odd).most_common(6)}")
    print(f"  C 같은 간선 앞뒤 뒤바뀜(추월): {sum(cross.values()):,}건  {cross.most_common(6)}")
    print(f"  D 종착 추정 없이 끝난 편(조각·종착행) {sum(tails.values()):,} · 드문 중간역(하루 5편 미만)에서 시작한 편 {sum(mid_head.values()):,}  "
          f"{mid_head.most_common(6)}")
    rep = {"trips": n_trip, "adjacent_links": n_hop, "skip_links": skip_hops, "A_multi_dest_trips": len(multi),
           "A_sample": multi[:50], "B_odd_gap": len(odd), "B_by_line": dict(collections.Counter(o[0][0] for o in odd)),
           "B_sample": [[list(o[0]), o[1], o[2], o[3]] for o in odd[:80]], "C_overtake": dict(cross),
           "D_tail_no_est": [[k[0], k[1], v] for k, v in tails.most_common(60)],
           "D_rare_mid_heads": dict(mid_head), "D_heads_top": [[k[0], k[1], v] for k, v in heads.most_common(80)]}
    (out / "link_check.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
