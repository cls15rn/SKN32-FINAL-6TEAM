#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""지하철 시간표 행선지(dest_nm) 빈칸 채우기 — 열차 잇기(28번 방 · 2026-09-27)

왜: TAGO 원천이 일부 열차의 행선지를 비워 준다(07호선 평일 25% · 04호선 44% · 03호선 8% · 08호선 10% · 서해선 50%).
판정기는 행선지 없는 열차를 막차 안전 규칙으로 **전부 버린다** → 배차가 반으로 보이고 예정이 늦게 나온다
(28 실측: 7호선 고속터미널 22시대 22:36 편을 못 써 T16·T19 예정 22:54 vs 실측 22:35).

빈칸 모양(28 조사): 두 가지다.
  ⓐ 중간 종착 — 07호선 하행 빈칸은 장암~온수에만 있고 까치울~석남엔 0 → 온수행(휴일 표는 「온수」로 준다)
  ⓑ 운영 구간 — 한 운영사 구간의 행만 비어 있다(07호선 상행 석남~까치울 · 03호선 대화~삼송 · 04호선 진접~남태령 …)
  둘 다 「빈칸 열차가 어디까지 가는가」를 **같은 열차를 다음 역에서 다시 찾아** 정한다.

방법(열차 잇기): (노선·요일형·dir) 안에서 빈칸 행 하나를 진행 방향 다음 역으로 잇는다.
  다음 역 출발 ∈ [t + 간선 소요 − TOL_LO, t + 간선 소요 + TOL_HI] 인 행이 **하나**면 같은 열차다.
    · 그 행이 행선지가 있으면 → 그 행선지를 쓴다                (basis = named@<역>)
    · 그 행도 빈칸이면 → 계속 잇는다
    · 다음 역에 맞는 행이 없으면 → 이 역이 종착이다            (basis = end@<역>)
    · 창 안에 둘 이상 · 간선 소요 없음 · 순서 밖(지선) → **채우지 않는다**(판정기가 종전대로 버린다)
  간선 소요는 `line_station_order_v1.json` 의 관측값(출발↔출발 · 정차 포함).
  채운 행에 `dest_inferred: "chain_v1"` · `dest_basis` 를 붙인다. 원래 있던 행선지는 건드리지 않는다.

검증(보고서에 적는다): 행선지가 **있는** 행을 빈칸으로 가려 같은 방법으로 추정 → 실제 행선지와 맞는 비율(가림 시험).

  python mobility_scripts/collect/fill_timetable_dest_v1.py --dry-run     # 보고서만
  python mobility_scripts/collect/fill_timetable_dest_v1.py                # timetable_v1.jsonl 교체(원본은 _backup 으로)

원자료는 이미 받은 시간표뿐 — 새 다운로드 없음. 규칙 값 아님(데이터 정비) · 판정기 무수정.
"""
import argparse
import collections
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

TOL_LO, TOL_HI = 1.5, 2.5          # 분 — 간선 관측 소요 대비 허용 창(정차 흔들림). 배차 최소 2.5분보다 좁다
AMBIG_GAP = 0.5                    # 창 안 후보 둘의 거리 차가 이보다 작으면 모호
GATE_BAD = 0.01                    # 한 행 가림 시험에서 틀림 비율이 이보다 크면 그 묶음은 채우지 않는다
GATE_MIN_N = 30                    # 가림 시험 표본이 이보다 적으면 채우지 않는다


def to_min(s):
    if not s:
        return None
    m = re.match(r"(\d+):(\d{2})(?::(\d{2}))?", s)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + (int(m.group(3) or 0) / 60)


def load_order(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for line, L in doc["lines"].items():
        st = sorted([s for s in L["stations"] if not s.get("is_spur")], key=lambda s: s["fr_order"])
        names = [s["station_nm"] for s in st]
        edge = {}
        for e in L.get("edges") or []:
            tm = e.get("travel_min")
            if tm is not None:
                edge[(e["a"], e["b"])] = edge[(e["b"], e["a"])] = float(tm)
        dirs = {d: v.get("fr_order") for d, v in (L.get("direction") or {}).items()}
        out[line] = {"names": names, "edge": edge, "dirs": dirs, "loop": bool(L.get("is_loop"))}
    return out, doc.get("dest_alias") or {}


class Group:
    """(노선·요일형·dir) 한 묶음 — 진행 순서대로 역별 출발 목록."""

    def __init__(self, seq, edge, rows):
        self.seq, self.edge, self.rows = seq, edge, rows
        self.pos = {n: i for i, n in enumerate(seq)}
        self.deps = collections.defaultdict(list)      # 역 → [(분, 행번호, 도착만)] — 종착역은 출발 없이 도착만 온다

    def finish(self):
        for v in self.deps.values():
            v.sort()

    def next_row(self, st, t):
        """같은 열차의 다음 역 행 — (다음역, 행번호) · 다음 역에서 끝나면 ('term', 다음역) · 못 정하면 ('stop', 이유).

        GPT 대조(9/28) #1·#2 반영: 창 안에 **서로 다른 열차가 둘 이상**이면 안 채운다(가장 가까운 것을 고르지 않는다).
        같은 열차는 두 원천이 같은 시각(≤ AMBIG_GAP)에 두 줄로 줄 수 있어 시각 묶음 하나 = 열차 하나로 센다.
        다음 역에 맞는 행이 **없으면** 종착이 아니라 「모름」이다(결측·급행 통과·시각 오차일 수 있다) → 안 채운다."""
        i = self.pos.get(st)
        if i is None:
            return "stop", "순서밖"
        if i + 1 >= len(self.seq):
            return "stop", "노선끝"
        nx = self.seq[i + 1]
        tm = self.edge.get((st, nx))
        if tm is None:
            return "stop", "간선소요없음"
        exp = t + tm
        cand = sorted((m, r, arr_only) for m, r, arr_only in self.deps.get(nx, [])
                      if exp - TOL_LO <= m <= exp + TOL_HI)
        if not cand:
            return "stop", "다음역행없음"
        groups = [[cand[0]]]                             # 시각 묶음 = 열차
        for c in cand[1:]:
            if c[0] - groups[-1][0][0] <= AMBIG_GAP:
                groups[-1].append(c)
            else:
                groups.append([c])
        if len(groups) > 1:
            return "stop", "모호"
        g = groups[0]
        dests = {self.rows[c[1]].get("dest_nm") for c in g} - {None}
        if len(dests) > 1:
            return "stop", "모호"
        arr_only = [c for c in g if c[2]]
        dep = [c for c in g if not c[2]]
        if arr_only and dep:                             # 도착 행과 출발 행이 같은 시각 — 같은 열차인지 못 정한다
            return "stop", "모호"
        if arr_only:                                     # 다음 역에 도착만 있다 = 거기서 끝나는 열차
            return "term", nx
        named = [c for c in dep if self.rows[c[1]].get("dest_nm")]
        return nx, (named or dep)[0][1]


def chain(g, rows, start_row, mask_named=False):
    """start_row 에서 열차를 잇는다 → (행선지 또는 None, basis, 이은 역 수)."""
    r = start_row
    hops = 0
    while True:
        st, t = rows[r]["station_nm"], rows[r]["_m"]
        nx, nr = g.next_row(st, t)
        if nx == "term":
            return nr, f"term@{nr}", hops + 1
        if nx == "stop":
            return None, nr, hops
        hops += 1
        d = rows[nr].get("dest_nm")
        if d and not mask_named:
            return d, f"named@{nx}", hops
        if rows[nr]["_m"] is None:                         # 이름 있는 도착 행 — 여기서 끝난다
            return nx, f"term@{nx}", hops
        r = nr
        if hops > 200:
            return None, "고리", hops


def main():
    from app.modules.travel_ops.mobility.engine.paths import PROCESSED
    M = PROCESSED / "mobility"
    ap = argparse.ArgumentParser(description="시간표 행선지 빈칸 채우기(열차 잇기)")
    ap.add_argument("--timetable", default=str(M / "timetable_v1.jsonl"))
    ap.add_argument("--order", default=str(M / "line_station_order_v1.json"))
    ap.add_argument("--report", default=str(M / "timetable_v1_destfill_report.md"))
    ap.add_argument("--backup-dir", default=str(PROCESSED.parents[2] / "_backup"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    order, alias = load_order(a.order)
    raw = Path(a.timetable).read_text(encoding="utf-8").splitlines()
    rows = []
    for ln in raw:
        if not ln.strip():
            rows.append(None)
            continue
        r = json.loads(ln)
        r["_m"] = to_min(r.get("dep_time"))
        rows.append(r)
    if any(r and r.get("dest_inferred") for r in rows):
        sys.exit("이미 채운 시간표다(dest_inferred 있음) — 원본에서 다시 돌린다")

    groups = {}
    for i, r in enumerate(rows):
        if r is None:
            continue
        arr_only = r["_m"] is None
        t = to_min(r.get("arr_time")) if arr_only else r["_m"]
        if t is None:
            continue
        o = order.get(r["line"])
        if not o or o["loop"]:
            continue
        fo = o["dirs"].get(r["dir"])
        if fo not in ("asc", "desc"):
            continue
        k = (r["line"], r["day_type"], r["dir"])
        if k not in groups:
            seq = o["names"] if fo == "asc" else o["names"][::-1]
            groups[k] = Group(seq, o["edge"], rows)
        groups[k].deps[r["station_nm"]].append((t, i, arr_only))
    for g in groups.values():
        g.finish()

    def norm(line, d):
        return (alias.get(line) or {}).get(d, d) if d else d

    # ── 가림 시험 두 가지 — 행선지 있는 행(표본 1/7)을 빈칸으로 가리고 같은 방법으로 추정 → 실제와 비교 ──
    #   한 행: 그 행만 가린다 = 채우기와 같은 조건(다음 역 행선지를 빌린다). 이웃이 같은 행선지면 틀려도 안 드러난다
    #   끝까지: 이름을 전부 가리고 시간만으로 종착까지 잇는다 = 다른 열차로 건너뛰는 오류를 드러낸다(엄격)
    #   실제 행선지가 이 노선 순서 안에서 진행 방향 앞쪽 역일 때만 채점(순환·지선·노선 밖 종착 제외)
    mask = collections.defaultdict(collections.Counter)
    for k, g in groups.items():
        for st, lst in g.deps.items():
            for m, i, ao in lst[::7]:                      # 표본 1/7
                r = rows[i]
                if ao:
                    continue
                real = norm(r["line"], r.get("dest_nm"))
                if not real or real not in g.pos or g.pos[real] <= g.pos.get(st, 10 ** 9):
                    continue
                for mode, flag in (("한행", False), ("끝까지", True)):
                    saved = r["dest_nm"]
                    r["dest_nm"] = None
                    try:
                        d, basis, _h = chain(g, rows, i, mask_named=flag)
                    finally:
                        r["dest_nm"] = saved
                    res = "못정함" if d is None else ("일치" if norm(r["line"], d) == real else "불일치")
                    mask[k][f"{mode}:{res}"] += 1

    def acc(c, mode):
        ok, bad, na = c[f"{mode}:일치"], c[f"{mode}:불일치"], c[f"{mode}:못정함"]
        return ok, ok + bad + na, bad

    def gate_ok(c):
        """묶음 관문 — 한 행 가림: 표본 ≥ GATE_MIN_N · 틀림/전체 ≤ GATE_BAD. 끝까지 가림: 틀림/(일치+틀림) ≤ GATE_BAD
        (긴 빈칸을 여러 역 이어 붙일 때 다른 열차로 건너뛰는 오류율 — GPT 대조 #4)."""
        ok1, n1, bad1 = acc(c, "한행")
        ok2, n2, bad2 = acc(c, "끝까지")
        if n1 < GATE_MIN_N or bad1 / n1 > GATE_BAD:
            return False
        if (ok2 + bad2) and bad2 / (ok2 + bad2) > GATE_BAD:
            return False
        return True

    # ── 채우기 ──
    stat = collections.defaultdict(collections.Counter)
    term = collections.defaultdict(collections.Counter)
    hopc = collections.defaultdict(collections.Counter)
    filled = {}
    for i, r in enumerate(rows):
        if r is None or r.get("dest_nm"):
            continue
        k = (r["line"], r["day_type"], r["dir"])
        g = groups.get(k)
        if r["_m"] is None:
            stat[k]["도착만"] += 1                        # 종착역 도착 행 — 판정기가 읽지 않는다(출발 없음)
            continue
        if g is None:
            stat[k]["못채움:순서표없음"] += 1
            continue
        if not gate_ok(mask.get(k, collections.Counter())):
            stat[k]["못채움:가림시험미달"] += 1
            continue
        d, basis, hops = chain(g, rows, i)
        if d:
            filled[i] = (d, basis, hops)
            stat[k][basis.split("@")[0]] += 1
            hopc[k][min(hops, 9)] += 1
            term[k][norm(r["line"], d)] += 1
        else:
            stat[k][f"못채움:{basis}"] += 1

    # ── 보고서 ──
    L = ["# 시간표 행선지 빈칸 채우기 — 열차 잇기 v1\n",
         f"자동 생성 — `mobility_scripts/collect/fill_timetable_dest_v1.py` · {datetime.now():%Y-%m-%d %H:%M} · 창 −{TOL_LO}/+{TOL_HI}분 · 모호 간격 {AMBIG_GAP}분\n",
         "| 노선·요일형·dir | 빈칸(출발 있는 행) | 채움: 다음 역 행선지 | 채움: 종착역 도착 행 | 못 채움 | 이은 역 수 분포 | 채운 행선지(상위) | 가림 한행 일치/시도 (틀림) | 가림 끝까지 일치/시도 (틀림) |",
         "|---|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    for k in sorted(set(stat) | set(mask)):
        s = stat.get(k, collections.Counter())
        blank = sum(v for kk, v in s.items() if kk != "도착만")
        if not blank:
            continue
        no = sum(v for kk, v in s.items() if kk.startswith("못채움"))
        mk = mask.get(k, collections.Counter())
        a1, n1, b1 = acc(mk, "한행")
        a2, n2, b2 = acc(mk, "끝까지")
        hops_txt = " ".join(f"{h}:{v}" for h, v in sorted(hopc[k].items())) or "—"
        L.append(f"| {' · '.join(k)} | {blank:,} | {s['named']:,} | {s['term']:,} | {no:,}"
                 + (" (" + ", ".join(f"{kk[4:]} {v}" for kk, v in s.items() if kk.startswith('못채움')) + ")" if no else "")
                 + f" | {hops_txt} | {', '.join(f'{d} {v}' for d, v in term[k].most_common(4))} | "
                 + (f"{a1}/{n1} ({b1})" if n1 else "—") + " | " + (f"{a2}/{n2} ({b2})" if n2 else "—") + " |")
        tot.update({"blank": blank, "named": s["named"], "term": s["term"], "no": no})
    L.append(f"\n**합계** 빈칸(출발 있는 행) {tot['blank']:,} · 채움 {tot['named'] + tot['term'] + tot['end']:,} "
             f"(다음 역 행선지 {tot['named']:,} · 종착역 도착 {tot['term']:,}) · 못 채움 {tot['no']:,}")
    mall = collections.Counter()
    for v in mask.values():
        mall.update(v)
    for mode in ("한행", "끝까지"):
        ok, n, bad = acc(mall, mode)
        L.append(f"\n가림 {mode} 전체(빈칸 없는 묶음 포함): 일치 {ok:,}/{n:,} · 틀림 {bad:,} · 못정함 {n - ok - bad:,}")
    L.append(f"\n- 묶음 관문: 한 행 가림 표본 ≥ {GATE_MIN_N} · 틀림/시도 ≤ {GATE_BAD:.0%} **그리고** 끝까지 가림 틀림/(일치+틀림) ≤ {GATE_BAD:.0%} — 못 넘으면 그 묶음은 채우지 않는다")
    L.append("- 「종착역 도착 행」 = 다음 역에 출발 없이 도착만 있는 행(그 역에서 끝나는 열차). 다음 역에 맞는 행이 **없으면** 종착으로 보지 않고 안 채운다(9/28 GPT #2)")
    L.append("- 창 안에 서로 다른 열차가 둘 이상이면 안 채운다(가까운 쪽을 고르지 않는다 · 9/28 GPT #1). 같은 시각 두 원천 행은 열차 하나")
    L.append("\n- 못 채운 행은 행선지 빈칸 그대로 — 판정기가 종전대로 버린다(보수적)")
    L.append("- 채운 행 표시: `dest_inferred: chain_v1` · `dest_basis: named@역 | term@역` · `dest_hops`(이은 역 수)")
    Path(a.report).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n보고서 → {a.report}")
    if a.dry_run:
        print("(dry-run — 시간표는 안 바꿨다)")
        return

    out_lines = []
    for i, ln in enumerate(raw):
        if i in filled:
            r = json.loads(ln)
            r["dest_nm"], r["dest_inferred"], r["dest_basis"] = filled[i][0], "chain_v1", filled[i][1]
            r["dest_hops"] = filled[i][2]
            out_lines.append(json.dumps(r, ensure_ascii=False))
        else:
            out_lines.append(ln)
    src = Path(a.timetable)
    bdir = Path(a.backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    bak = bdir / f"timetable_v1_before_destfill_{datetime.now():%Y%m%d_%H%M}.jsonl"
    shutil.copy2(src, bak)
    tmp = src.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(src)
    meta_p = src.with_name("timetable_v1_meta.json")
    if meta_p.exists():
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        meta["dest_fill"] = {"script": "mobility_scripts/collect/fill_timetable_dest_v1.py", "method": "chain_v1",
                             "at": datetime.now().isoformat(timespec="seconds"), "rows_filled": len(filled),
                             "blank_before": tot["blank"], "left_blank": tot["no"], "backup": bak.name,
                             "report": Path(a.report).name}
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"원본 → {bak}\n교체 → {src} · 채운 행 {len(filled):,}")


if __name__ == "__main__":
    main()
