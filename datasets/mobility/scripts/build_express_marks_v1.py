# -*- coding: utf-8 -*-
"""datasets/mobility/scripts/build_express_marks_v1.py — 급행 편 표시 파일 (105 · 2026-10-05)

무엇: 9호선 시간표(timetable_v1)에는 **급행이 전 역 출발 행으로** 들어 있다(원천 TAGO 는 통과 시각도 행으로 싣고,
      열차 번호·급행 표시가 없다). 판정기는 통과역(구반포·흑석 등)에서 그 편을 [확정]으로 태웠다(PC 실험 트랙 발견 · 105 재현).
      이 스크립트는 **어느 행이 급행 편의 행인지**를 가려 `express_marks_v1.json` 으로 낸다. 판정기(verify_time.Verifier.candidates)는
      표시된 편을 **급행 정차역끼리만** 쓴다(1호선 경인 급행을 거르는 92 방식과 같은 자리).

어떻게 가리나(등급 **추정** — 원천에 급행 표시가 없어 시간표 행의 모양으로만 가린다)
  · 하행(dir D): 행선지가 `EXPRESS_DEST_D`(김포공항)인 편 전부. 근거 — 그 묶음은 전 편이 급행 소요다(신논현→당산 16~21분 ·
    개화행 23~31분 · 겹치지 않음). 스크립트가 순서 잇기로 다시 재서 보고서에 적는다.
  · 상행(dir U): 급행·완행의 행선지가 같다(중앙보훈병원). **시발역이 `EXPRESS_ORIGIN_U`(김포공항)인 편**이 급행이다 —
    역마다 「갈래별 순서 유지」(급행끼리 · 완행끼리는 서로 앞지르지 않는다)로 편을 이어 다음 역의 급행 행을 찾는다.
    급행은 통과 구간 시차가 좁게 모여 있어(구간별 최빈 ±) 먼저 맞추고, 남은 행이 완행이다.
정차역 목록(`EXPRESS_STOPS`)은 운영사 공표 값이다(값·URL·확인 시각 — 아래 STOPS_SOURCE). 시간표에서 지어내지 않는다.

실행(저장소 맨 위에서):
    python datasets/mobility/scripts/build_express_marks_v1.py                 # 자료 폴더(.env DATA_DIR 또는 저장소 안)
    python datasets/mobility/scripts/build_express_marks_v1.py --dir <processed/mobility 폴더>
산출: <폴더>/express_marks_v1.json · express_marks_v1_report.md
"""
import argparse
import collections
import gzip
import hashlib
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
LINE = "09호선"
# 운영사 공표 급행 정차역(서울시메트로9호선). 값을 바꾸면 STOPS_SOURCE 의 확인 시각도 같이 바꾼다.
EXPRESS_STOPS = ["김포공항", "마곡나루", "가양", "염창", "당산", "여의도", "노량진", "동작", "고속터미널", "신논현",
                 "선정릉", "봉은사", "종합운동장", "석촌", "올림픽공원", "중앙보훈병원"]
STOPS_SOURCE = {
    "value": "급행 정차역 16역(김포공항 ~ 중앙보훈병원)",
    "source": "서울시메트로9호선(주) 열차이용안내",
    "url": "https://www.metro9.co.kr/kor/sub01_04.do",
    "checked_at": None,          # ★ 본인이 페이지를 열어 확인한 시각을 --stops-checked-at 으로 준다(없으면 「공표 대조 안 함」으로 적힌다)
    "grade": "확정(공표) — checked_at 이 비어 있으면 추정(대조 전)",
}
EXPRESS_DEST_D = {"김포공항"}      # 하행: 이 행선지 묶음 = 급행
EXPRESS_ORIGIN_U = "김포공항"      # 상행: 이 역에서 시발하는 편 = 급행
E_WIN = (20, 300)                  # 급행 구간 시차 창(초)
L_WIN = (20, 600)                  # 완행 구간 시차 창(초) — 대피 정차 포함(원천에 30초 · 9분대 행이 있다)
CHECK_PAIR = ("신논현", "당산")    # 보고서에 적는 대조 구간(급행·완행 소요가 안 겹치는지)


def sec(t):
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def hms(x):
    return f"{x // 3600:02d}:{x % 3600 // 60:02d}:{x % 60:02d}"


def fifo_match(a_times, b_times, lo, hi, prefer=None):
    """순서를 지키며 a[i] → b[j] (lo ≤ b−a ≤ hi) 를 **가장 많이** 잇는다(같은 수면 prefer 시차에서 덜 벗어나는 쪽).
    돌려주는 것: {i: j}. 동적 계획(편 수 ≤ 300 · 행 ≤ 300)."""
    n, m = len(a_times), len(b_times)
    NEG = (-1, 0.0)
    best = [[(0, 0.0)] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            c = max(best[i + 1][j], best[i][j + 1], key=lambda x: (x[0], -x[1]))
            d = b_times[j] - a_times[i]
            if lo <= d <= hi:
                k = best[i + 1][j + 1]
                cand = (k[0] + 1, k[1] + (abs(d - prefer) if prefer is not None else 0))
                c = max(c, cand, key=lambda x: (x[0], -x[1]))
            best[i][j] = c
    out, i, j = {}, 0, 0
    while i < n and j < m:
        d = b_times[j] - a_times[i]
        if lo <= d <= hi:
            k = best[i + 1][j + 1]
            if (k[0] + 1, k[1] + (abs(d - prefer) if prefer is not None else 0)) == best[i][j]:
                out[i] = j
                i, j = i + 1, j + 1
                continue
        if best[i + 1][j] == best[i][j]:
            i += 1
        else:
            j += 1
    _ = NEG
    return out


def modes_delta(a_times, b_times, lo=30, hi=420, top=1):
    """a 의 각 시각에서 b 의 행까지 시차 분포의 최빈값들(진짜 짝은 몇 값에 몰리고 우연은 흩어진다)."""
    bs = set(b_times)
    h = collections.Counter()
    for t in a_times:
        for d in range(lo, hi + 1, 5):
            if t + d in bs:
                h[d] += 1
    return [d for d, _n in h.most_common(top)]


def merge_match(E, L, B, e_modes, l_modes, e_win, l_win, max_skip, max_drop, pen=400, e_exp=None, l_exp=None):
    """두 갈래(급행 E · 완행 L — 각각 순서 유지)를 다음 역 행 B(시각 오름차순)에 **한꺼번에** 잇는다.

    상태 (i, j, s, d) = 급행 i 편 · 완행 j 편을 처리했고, B 행 s 개를 건너뛰었고(이 역에서 새로 시발한 편), 편 d 개를 놓쳤다
    (이 역에 행이 없는 편). B 의 다음 행 k = i + j − d + s. 비용 = 갈래별 최빈 시차에서 벗어난 초(상한 120) + 건너뜀·놓침 벌점.
    돌려주는 것: (급행 {i: k}, 완행 {j: k}, 건너뛴 k 목록) — 못 이으면 None."""
    nE, nL, nB = len(E), len(L), len(B)
    INF = float("inf")

    def cost(d, modes, w):
        # 최빈 시차에서 벗어난 초(상한 120) + 덜 흔한 시차일수록 벌점(순위 × 20) + 시차 자체(× w).
        # ★ 시차 항이 없으면 배차가 고른 시간대에 「한 편 뒤의 행」(진짜 + 배차 간격)이 같은 비용으로 잡힌다(개화 → 김포공항).
        # ★ 급행의 w 가 완행보다 커야 한다 — 대피역(가양·동작 등)에서 「완행이 먼저 닿고 급행이 먼저 떠나는」 엇갈림과
        #   「안 엇갈림」은 시차 합이 같다. 급행은 기다리지 않으므로 급행이 이른 행을 갖는 쪽이 맞다.
        return (min(min(120, abs(d - m)) + 20 * r for r, m in enumerate(modes)) if modes else 60) + d * w

    def cost_exp(d, exp, w):
        # 두 번째 돌림 — 그 편 **앞뒤 같은 갈래 편들의 그 구간 시차**(첫 돌림 결과의 중앙값)에서 벗어난 초. 혼잡 시간대는 시차가
        #   낮과 달라 하루 최빈값으로는 급행·완행이 엇갈려 잡힌다(평일 07:30대 2편).
        return min(120, abs(d - exp)) + d * w
    cur = {(0, 0, 0, 0): (0.0, None, None)}
    back = {}
    order_states = []
    # i + j 가 늘거나 s 가 느는 방향으로만 가므로 (i + j + s) 층으로 돈다
    layers = collections.defaultdict(dict)
    layers[0][(0, 0, 0, 0)] = 0.0
    for tot in range(0, nE + nL + max_skip + 1):
        for st, c in sorted(layers.get(tot, {}).items()):
            i, j, s_, d_ = st
            k = i + j - d_ + s_
            nxts = []
            if k < nB:
                if i < nE:
                    dl = B[k] - E[i]
                    if e_win[0] <= dl <= e_win[1]:
                        ce = cost_exp(dl, e_exp[i], 1.0) if e_exp and e_exp[i] is not None else cost(dl, e_modes, 1.0)
                        nxts.append(((i + 1, j, s_, d_), c + ce, ("E", i, k)))
                if j < nL:
                    dl = B[k] - L[j]
                    if l_win[0] <= dl <= l_win[1]:
                        cl = cost_exp(dl, l_exp[j], 0.3) if l_exp and l_exp[j] is not None else cost(dl, l_modes, 0.3)
                        nxts.append(((i, j + 1, s_, d_), c + cl, ("L", j, k)))
                if s_ < max_skip:
                    nxts.append(((i, j, s_ + 1, d_), c + pen, ("S", None, k)))
            if d_ < max_drop:
                if i < nE:
                    nxts.append(((i + 1, j, s_, d_ + 1), c + pen, ("DE", i, None)))
                if j < nL:
                    nxts.append(((i, j + 1, s_, d_ + 1), c + pen, ("DL", j, None)))
            for ns, nc, act in nxts:
                lay = layers[ns[0] + ns[1] + ns[2]]
                if nc < lay.get(ns, INF):
                    lay[ns] = nc
                    back[ns] = (st, act)
    _ = (cur, order_states)
    ends = [(c, st) for lay in layers.values() for st, c in lay.items()
            if st[0] == nE and st[1] == nL and st[0] + st[1] - st[3] + st[2] == nB]
    if not ends:
        return None
    _c, st = min(ends)
    me, ml, skipped = {}, {}, []
    while st in back:
        prev, (kind, idx, k) = back[st]
        if kind == "E":
            me[idx] = k
        elif kind == "L":
            ml[idx] = k
        elif kind == "S":
            skipped.append(k)
        st = prev
    return me, ml, sorted(skipped)


def chain_dir(T, dt, dr, seq, notes, expect=None):
    """한 요일·한 방향의 편 잇기. expect = 첫 돌림이 낸 {(갈래, 앞 역, 이 역): [(앞 역 시각, 시차)]} — 주면 편마다 앞뒤 편의 시차를 기대값으로 쓴다."""
    import bisect

    def exp_of(cls, prev, s, t):
        v = (expect or {}).get((cls, prev, s))
        if not v or len(v) < 5:
            return None
        i = bisect.bisect_left(v, (t, -1))
        near = sorted(v[max(0, i - 4):i + 5], key=lambda x: abs(x[0] - t))[:7]
        return statistics.median(d for _t, d in near)
    trains = []                           # {"cls": E/L, "dest", "start", "t": {역: 초}, "born": 시발 사유}
    open_ = []                            # 직전 역에 행이 있는 편(색인)
    for si, s in enumerate(seq):
        B_all = T.get((dt, dr, s), [])
        nxt_open, taken = [], set()
        for de in sorted({d for _t, d in B_all} | {trains[x]["dest"] for x in open_}):
            B = [t for t, d in B_all if d == de]
            Ei = [x for x in open_ if trains[x]["dest"] == de and trains[x]["cls"] == "E"]
            Li = [x for x in open_ if trains[x]["dest"] == de and trains[x]["cls"] == "L"]
            prev = seq[si - 1] if si else None
            Ei.sort(key=lambda x: trains[x]["t"][prev])     # 순서 잇기는 시각 순이어야 한다
            Li.sort(key=lambda x: trains[x]["t"][prev])
            E = [trains[x]["t"][prev] for x in Ei]
            L = [trains[x]["t"][prev] for x in Li]
            me, ml, skipped = {}, {}, list(range(len(B)))
            if B and (E or L):
                diff = len(B) - len(E) - len(L)
                res = None
                for extra in (0, 1, 2, 4):
                    res = merge_match(E, L, B, modes_delta(E, B, 20, 300, 2), modes_delta(L, B, 20, 600, 4),
                                      E_WIN, L_WIN, max(0, diff) + extra, max(0, -diff) + extra,
                                      e_exp=[exp_of("E", prev, s, t) for t in E] if expect else None,
                                      l_exp=[exp_of("L", prev, s, t) for t in L] if expect else None)
                    if res:
                        break
                if res is None:
                    notes.append(f"{dt} {dr} {prev}→{s} {de}행: 잇지 못함(급행 {len(E)} · 완행 {len(L)} · 행 {len(B)})")
                else:
                    me, ml, skipped = res
            for idxs, m in ((Ei, me), (Li, ml)):
                for i, x in enumerate(idxs):
                    if i in m:
                        trains[x]["t"][s] = B[m[i]]
                        nxt_open.append(x)
            for k in skipped:                              # 이 역에서 시발하는 편
                if dr == "D":
                    cls = "E" if de in EXPRESS_DEST_D else "L"
                else:
                    cls = "E" if s == EXPRESS_ORIGIN_U else "L"
                trains.append({"cls": cls, "dest": de, "start": s, "t": {s: B[k]}})
                nxt_open.append(len(trains) - 1)
            _ = taken
        open_ = nxt_open
    return trains


def build(rows, order):
    """rows: 그 노선의 시간표 행 dict 목록. → (marks, report 줄 목록, stats)"""
    T = collections.defaultdict(list)          # (day_type, dir, station) → [(sec, dest)]
    for r in rows:
        # 행선지를 채운 행(dest_inferred — 열린데이터광장 보충분 · 올림픽공원·흑석 휴일)은 TAGO 행과 같은 편을 다른 초로 한 번 더
        #   싣는다. 잇기에 넣으면 편이 갈라지므로 뺀다(그 행은 표시하지 않는다 — 그 원천은 그 역에 서는 편만 싣는다).
        if r.get("dest_nm") and r.get("dep_time") and not r.get("dest_inferred"):
            T[(r["day_type"], r["dir"], r["station_nm"])].append((sec(r["dep_time"]), r["dest_nm"]))
    for k in list(T):
        T[k] = sorted(set(T[k]))
    marks = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(dict)))
    rep, stats, notes = [], {}, []
    for dt in sorted({k[0] for k in T}):
        for dr in ("D", "U"):
            seq = order if dr == "U" else order[::-1]
            trains = chain_dir(T, dt, dr, seq, [])
            expect = collections.defaultdict(list)
            for tr in trains:
                ks = list(tr["t"].items())
                for (pa, ta), (pb, tb) in zip(ks, ks[1:]):
                    expect[(tr["cls"], pa, pb)].append((ta, tb - ta))
            for v in expect.values():
                v.sort()
            trains = chain_dir(T, dt, dr, seq, notes, expect)      # 두 번째 돌림 — 앞뒤 편의 시차를 기대값으로
            # 중간 역에서 시발한 편(완행으로 둔 것) 중 급행처럼 빠른 편을 다시 가른다 — 통과 구간(양 끝이 정차역이 아닌 이웃 쌍) 시차 합으로
            stops = set(EXPRESS_STOPS)
            pass_pairs = [(a, b) for a, b in zip(seq, seq[1:]) if b not in stops]
            med = {}
            for cls in ("E", "L"):
                for pr in pass_pairs:
                    v = [tr["t"][pr[1]] - tr["t"][pr[0]] for tr in trains
                         if tr["cls"] == cls and tr["start"] in (seq[0], seq[1]) and pr[0] in tr["t"] and pr[1] in tr["t"]]
                    if v:
                        med[(cls, pr)] = statistics.median(v)
            relabel = 0
            n_mark = 0
            for tr in trains:
                mid = dr == "U" and tr["start"] not in (seq[0], seq[1])      # 하행은 행선지로 가르므로 시발역을 안 본다
                if not mid:
                    if tr["cls"] == "E":
                        for st, t in tr["t"].items():
                            marks[dt][dr][st].setdefault(tr["dest"], []).append(hms(t))
                            n_mark += 1
                    continue
                # 중간 역 시발 편(첫차 05:30 대에 여러 역에서 동시에 떠나는 편 등)은 편 전체를 한 갈래로 보지 않는다 — 잇기가 두 편을
                #   한 편으로 붙일 수 있다(여의도 05:30 시발: 동작까지 완행 시차 · 그 뒤 급행 시차). **행마다** 그 행을 낀 통과 구간
                #   (도착 쪽이 정차역이 아닌 이웃 쌍)의 시차가 급행 쪽에 가까우면 표시한다.
                ks = list(tr["t"].items())
                like = {}
                for (pa, ta), (pb, tb) in zip(ks, ks[1:]):
                    pr = (pa, pb)
                    if pb in stops or ("E", pr) not in med or ("L", pr) not in med:
                        continue
                    e_like = abs((tb - ta) - med[("E", pr)]) < abs((tb - ta) - med[("L", pr)])
                    like.setdefault(pa, []).append(e_like)
                    like.setdefault(pb, []).append(e_like)
                got = [st for st, v in like.items() if v and all(v)]
                if got:
                    notes.append(f"{dt} {dr} {tr['start']} {hms(tr['t'][tr['start']])} 시발 {tr['dest']}행(중간 시발): "
                                 f"통과 시차가 급행 쪽인 행 {len(got)}개({got[0]}~{got[-1]})를 표시")
                    relabel += 1
                for st in got:
                    marks[dt][dr][st].setdefault(tr["dest"], []).append(hms(tr["t"][st]))
                    n_mark += 1
            for s in marks[dt][dr]:
                for de in marks[dt][dr][s]:
                    marks[dt][dr][s][de].sort()
            # (GPT 105 #2) 급행으로 가른 편이 중간에서 끊기면(다음 역에 그 편의 행이 없다) 그 뒤쪽 행은 「새로 시발한 편」으로 다시 태어나
            #   완행(표시 없음)이 된다 — 조용히 넘기지 않고 센다. main 이 0 이 아니면 멈춘다(--allow-broken 으로만 넘어간다).
            broken = [tr for tr in trains if dr == "U" and tr["cls"] == "E" and tr["start"] in (seq[0], seq[1])   # 하행은 행선지로 표시해 끊겨도 전 행이 표시된다
                      and tr["dest"].split("(")[0] in seq and list(tr["t"])[-1] != seq[seq.index(tr["dest"].split("(")[0]) - 1]]
            for tr in broken:
                notes.append(f"{dt} {dr} ★끊긴 급행 편: {tr['start']} {hms(tr['t'][tr['start']])} 시발 {tr['dest']}행이 "
                             f"{list(tr['t'])[-1]} 뒤로 이어지지 않는다 — 그 뒤 행은 표시되지 않았을 수 있다")
            a, b = CHECK_PAIR if dr == "D" else CHECK_PAIR[::-1]
            dur = {c: sorted((tr["t"][b] - tr["t"][a]) / 60 for tr in trains if tr["cls"] == c and a in tr["t"] and b in tr["t"])
                   for c in ("E", "L")}
            nE = sum(1 for tr in trains if tr["cls"] == "E")
            full = sum(1 for tr in trains if tr["cls"] == "E" and len(tr["t"]) >= len(seq) - 2)
            stats[(dt, dr)] = {"trains": nE, "rows": n_mark, "relabel": relabel, "broken": len(broken)}
            rng = {c: (f"{v[0]:.1f}~{v[-1]:.1f}분({len(v)}편)" if v else "없음") for c, v in dur.items()}
            rep.append(f"| {dt} | {'상행' if dr == 'U' else '하행'} | "
                       f"{'시발 ' + EXPRESS_ORIGIN_U if dr == 'U' else '행선지 ' + '·'.join(sorted(EXPRESS_DEST_D))} | {nE} | {full} | {n_mark:,} | "
                       f"{a}→{b} 급행 {rng['E']} · 완행 {rng['L']} |")
    return marks, rep + [""] + [f"- {n}" for n in notes], stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="processed/mobility 폴더(시간표·역 순서 표가 있는 곳)")
    ap.add_argument("--allow-broken", action="store_true", help="상행에서 끊긴 급행 편이 있어도 파일을 쓴다(보고서에 남는다)")
    ap.add_argument("--stops-checked-at", help="공표 정차역을 확인한 시각(ISO 8601) — 본인이 페이지를 본 시각")
    args = ap.parse_args()
    if args.dir:
        d = Path(args.dir)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _paths import PROCESSED          # noqa: E402
        d = Path(PROCESSED) / "mobility"
    tt = d / "timetable_v1.jsonl.gz"
    tt = tt if tt.exists() else d / "timetable_v1.jsonl"
    opener = gzip.open if tt.suffix == ".gz" else open
    rows, fetched = [], None
    with opener(tt, "rt", encoding="utf-8") as f:
        for raw in f:
            if LINE not in raw:
                continue
            r = json.loads(raw)
            if r.get("line") == LINE:
                rows.append(r)
                fetched = fetched or r.get("fetched_at")
    order = [s["station_nm"] for s in
             json.loads((d / "line_station_order_v1.json").read_text(encoding="utf-8"))["lines"][LINE]["stations"]]
    miss = [s for s in EXPRESS_STOPS if s not in order]
    if miss:
        raise SystemExit(f"정차역 이름이 역 순서 표에 없다: {miss}")
    marks, rep, stats = build(rows, order)
    n_broken = sum(v["broken"] for v in stats.values())
    if n_broken and not args.allow_broken:
        print("\n".join(x for x in rep if "끊긴 급행 편" in x))
        raise SystemExit(f"상행 급행 편 {n_broken}개가 중간에서 끊겼다 — 표시 파일을 쓰지 않았다(뒤쪽 행이 완행으로 남는다). "
                         f"원천 행 누락을 확인하거나 --allow-broken")
    src = dict(STOPS_SOURCE, checked_at=args.stops_checked_at)
    # 행 내용의 지문 — 시간표가 바뀌면(재수집) 표시도 다시 만들어야 한다. 판정기(Timetable.load)가 적재한 행으로 같은 지문을 내
    #   대조하고, 다르면 표시를 쓰지 않는다(통과역이 낀 구간은 모른다 — GPT 105 #1).
    fp = hashlib.sha256("\n".join(sorted(
        f"{r['day_type']}|{r['dir']}|{r['station_nm']}|{r.get('dep_time')}|{r.get('dest_nm')}"
        for r in rows if r.get("dep_time"))).encode()).hexdigest()       # 출발 없는 행은 뺀다(저장소 판에는 없고 정본에는 있다)
    now = datetime.now(KST).isoformat(timespec="seconds")
    doc = {
        "schema": "express_marks_v1", "built_at": now, "room": 105,
        "what": "급행 편의 시간표 행 표시 — 판정기는 표시된 편을 급행 정차역끼리만 쓴다(통과역 승차·하차·환승에 안 쓴다)",
        "grade": {"marks": "추정 — 원천(TAGO)에 열차 번호·급행 표시가 없어 시간표 행의 모양(행선지 · 시발역 · 구간 시차)으로 가렸다",
                  "stops": "확정(운영사 공표)" if src["checked_at"] else "추정 — 공표 대조 안 함(checked_at 없음)"},
        "timetable": {"fetched_at": fetched, "rows_line_with_dep": sum(1 for r in rows if r.get("dep_time")), "rows_fingerprint": fp},
        "lines": {LINE: {
            "stops": EXPRESS_STOPS, "stops_source": src,
            "method": {"D": f"행선지 {sorted(EXPRESS_DEST_D)} 인 편 전부",
                       "U": f"시발역 {EXPRESS_ORIGIN_U} 인 편 — 급행·완행 두 갈래를 역마다 순서를 지켜 잇는다(갈래별 최빈 시차에서 덜 벗어나는 쪽)",
                       "mid_origin": "중간 역에서 시발한 편은 통과 구간 시차 합이 급행 쪽에 가까우면 급행으로 가른다"},
            "marks": marks}},
    }
    (d / "express_marks_v1.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    lines = ["# 급행 편 표시 v1 (9호선)", "",
             f"생성 {now} · 시간표 {tt.name}(수집 {fetched}) · {LINE} {len(rows):,}행",
             f"정차역({len(EXPRESS_STOPS)}): {' · '.join(EXPRESS_STOPS)}",
             f"정차역 출처: {src['source']} {src['url']} · 확인 시각 {src['checked_at'] or '없음(공표 대조 안 함)'}",
             "등급: 편 구분 = 추정(시간표 행의 모양) · 정차역 = 공표", "",
             "| 요일 | 방향 | 가른 기준 | 급행 편 | 그중 끝까지 이은 편 | 표시한 행 | 대조(급행·완행 소요가 안 겹쳐야 한다) |", "|---|---|---|---:|---:|---:|---|"] + rep
    (d / "express_marks_v1_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
