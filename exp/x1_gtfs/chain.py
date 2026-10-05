# -*- coding: utf-8 -*-
"""열차 잇기 — 역별 출발 행(열차 번호 없음)을 편(trip)으로 묶는다. (X1 · 2026-10-04)

원천 `timetable_v1.jsonl.gz` 의 한 행은 「노선·역·요일형·출발시각·dir·행선지」다. 열차 번호·도착 시각·종착역 행이 없다.
GTFS 는 편 단위라, 같은 열차의 행을 역 순서대로 이어 붙여야 한다. 여기서 하는 일은 **묶기뿐**이다 —
출발 시각은 한 글자도 바꾸지 않고, 원천에 없는 정차를 만들지 않는다(종착역 도착 1개만 따로 표시해 더한다 · build_gtfs.py).

방법
  1) 행마다 「다음 역 후보」를 정한다 — 행선지까지의 경로(가지 노선) 또는 간선의 dir 라벨(순환선 · 행선지 없음).
  2) 간선(s→n)마다 출발↔출발 시차의 **최빈값**을 데이터에서 배운다(10초 칸).
  3) (노선·요일형)마다 후보 쌍 (a at s, b at n) 을 「배운 시차와의 어긋남」이 작은 순으로 1:1 확정한다.
       1차  같은 행선지 · 창 −75/+150초
       2차  남은 것끼리 · 창 −90/+420초(대피 정차) · 서로 유일할 때만
       3차  역 건너뛰기(급행) · 남은 것끼리 · 같은 행선지 · 서로 유일할 때만
  4) 이어진 사슬 하나 = 편 하나. 못 이은 행은 버리지 않고 짧은 편(조각)으로 남긴다.
"""
import collections

W1 = (-75, 150)        # 1차 창(초)
W2 = (-150, 420)        # 2차 창(초) — 급행 대피로 정차가 길어지는 열차
SKIP_MAX = 14          # 3차 — 최대 몇 역까지 건너뛰어 찾나
SKIP_FAST = 0.35       # 3차 — 급행이 완행 누적 시차의 이 비율보다 빠를 수는 없다
SKIP_SLOW = 180        # 3차 — 완행 누적 시차보다 이만큼(초)까지 늦을 수 있다
BIN = 10
MIN_GAP = 20           # 같은 열차의 이웃 역 출발 시차 하한(초)

# 응암순환(6호선) — 한 방향으로만 돈다: 새절→응암→역촌→불광→독바위→연신내→구산→응암→새절
ONEWAY = {"06호선": ["응암", "역촌", "불광", "독바위", "연신내", "구산", "응암"]}
LOOP_GATE = {"06호선": ("응암", "새절", "역촌", "구산")}     # (관문역, 본선 쪽, 고리 첫 역, 고리 끝 역)


def to_sec(s):
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(x)


class Net:
    """노선 하나의 간선 그래프 — line_station_order_v1.json 의 lines[노선]."""

    def __init__(self, line, L):
        self.line = line
        self.loop = bool(L.get("is_loop"))
        self.reliable = bool((L.get("dir_label") or {}).get("reliable", True))
        self.spur = {s["station_nm"] for s in L["stations"] if s.get("is_spur")}
        self.order = {s["station_nm"]: s["fr_order"] for s in L["stations"]}
        self.adj = collections.defaultdict(dict)
        for e in L["edges"]:
            tm = e.get("travel_min")
            self.adj[e["a"]][e["b"]] = {"tm": None if tm is None else tm * 60, "lab": e.get("dir_a_to_b")}
            self.adj[e["b"]][e["a"]] = {"tm": None if tm is None else tm * 60, "lab": e.get("dir_b_to_a")}
        self.oneway = set()
        seq = ONEWAY.get(line)
        if seq:
            for a, b in zip(seq, seq[1:]):
                self.oneway.add((a, b))
        self._hop = {}

    def hop(self, s, d):
        """s 에서 d 로 가는 최단 경로의 첫 역(없으면 None)."""
        k = (s, d)
        if k in self._hop:
            return self._hop[k]
        r = None
        if s in self.adj and d in self.adj and s != d:
            prev = {s: None}
            q = [s]
            for u in q:
                if u == d:
                    break
                for v in self.adj[u]:
                    if v not in prev and not self._blocked(u, v):
                        prev[v] = u
                        q.append(v)
            if d in prev:
                u = d
                while prev[u] != s:
                    u = prev[u]
                r = u
        self._hop[k] = r
        return r

    def path(self, s, d, limit):
        out, u = [], s
        while u != d and len(out) < limit:
            u = self.hop(u, d)
            if u is None:
                break
            out.append(u)
        return out

    def _blocked(self, u, v):
        return bool(self.oneway) and (v, u) in self.oneway and (u, v) not in self.oneway

    def lab_ok(self, s, n, dr, d):
        """dir 라벨로 볼 때 s→n 이 그 dir 의 진행 방향인가."""
        if self._blocked(s, n):
            return False
        if not self.reliable:
            return True
        lab, rev = self.adj[s][n]["lab"], self.adj[n][s]["lab"]
        if lab == dr:
            return True
        if lab is None:                                   # 관측 못 한 방향 — 종착 쪽 끝 간선이 그렇다
            return rev != dr
        return False

    def cands(self, s, dr, d, dir_mode):
        """이 행의 다음 역 후보."""
        if s not in self.adj:
            return []
        if d is not None and d not in self.adj:
            d = None
        if dir_mode:
            d = None
        by_lab = [n for n in self.adj[s] if self.lab_ok(s, n, dr, d)]
        if self.loop:
            if d and d != s and (s in self.spur or d in self.spur):
                h = self.hop(s, d)
                return [h] if h else []
            main = [n for n in by_lab if n not in self.spur]
            return main or by_lab
        if d and d != s:
            h = self.hop(s, d)
            return [h] if h else []
        if d == s:
            return []
        return by_lab


def build(rows, order_doc, log=print):
    """rows: [{line, station_nm, day_type, dep_time, dir, dest_nm}] → 사슬 정보.

    반환 dict: succ{i:j} · pred{j:i} · how{i:'1'|'2'|'skip'|'loop'} · term_row(set — 종착역 도착 행)
              · nxt{i:다음 역}(마지막 행이 향하던 역) · delta{(line,s,n):초} · dest{i:정규화 행선지} · dirmode(set)
    """
    alias = order_doc.get("dest_alias") or {}
    nets = {ln: Net(ln, L) for ln, L in order_doc["lines"].items()}
    N = len(rows)
    T = [to_sec(r["dep_time"]) for r in rows]
    D = []
    for r in rows:
        d = r.get("dest_nm") or None
        D.append(alias.get(r["line"], {}).get(d, d) if d else None)

    groups = collections.defaultdict(list)                 # (line, day) → [i]
    for i, r in enumerate(rows):
        groups[(r["line"], r["day_type"])].append(i)

    # 행선지가 dir 라벨과 어긋나는 행(원천 결함 — 시발역·옛 종착역을 행선지로 준 행) → 그 행은 dir 로 방향을 정하고 행선지는 모름으로 둔다
    conflict = set()
    term_row = set()
    C = [None] * N                                         # 다음 역 후보
    for i, r in enumerate(rows):
        net, s, d = nets[r["line"]], r["station_nm"], D[i]
        if s not in net.adj:
            C[i] = []
            continue
        if d and d != s and d in net.adj and net.reliable and not net.loop:
            h = net.hop(s, d)
            lab = net.adj[s][h]["lab"] if h else None
            rev = net.adj[h][s]["lab"] if h else None
            if h and ((lab is not None and lab != r["dir"]) or (lab is None and rev == r["dir"])):
                conflict.add(i)
                D[i] = d = None
        if d == s and not net.loop:
            term_row.add(i)                                # 행선지 = 이 역 — 종착 도착 행이거나(지축) 옛 종착 표기(구리)다. 이어지면 잇는다
            C[i] = [n for n in net.adj[s] if net.lab_ok(s, n, r["dir"], None)]
            continue
        C[i] = net.cands(s, r["dir"], d, False)
    if conflict:
        cc = collections.Counter((rows[i]["line"], rows[i]["day_type"]) for i in conflict)
        log("  행선지-방향 어긋남(행선지를 모름으로): " + " · ".join(f"{k[0]} {k[1]} {v:,}" for k, v in sorted(cc.items())))

    at = collections.defaultdict(list)                     # (line, day, station) → [i] 시각순
    for i, r in enumerate(rows):
        at[(r["line"], r["day_type"], r["station_nm"])].append(i)
    for v in at.values():
        v.sort(key=lambda i: T[i])

    def from_ok(net, b, s, n):
        """b(at n)가 s 쪽에서 온 열차일 수 있나."""
        cb = C[b]
        if cb == [s]:
            return False
        lab = net.adj[s][n]["lab"]
        if net.reliable and lab is not None and lab != rows[b]["dir"]:
            return False
        return True

    def dest_ok(a, b, n, net):
        da, db = D[a], D[b]
        if b in term_row and da == n:
            return True
        if da == n and net.loop and rows[a]["station_nm"] in net.spur:
            return False                                   # 지선 열차는 본선 접속역에서 끝난다(순환 열차와 잇지 않는다)
        if da == n and not (net.loop and db == n) and net.line not in LOOP_GATE:
            return False                                   # a 는 n 에서 끝난다
        if net.line in LOOP_GATE and da == n:
            return n == LOOP_GATE[net.line][0]             # 응암행은 응암에서 고리로 들어간다(행선지 표기가 바뀐다)
        return da is None or db is None or da == db

    # ── 간선 시차 배우기 — (간선·행선지)마다 출발↔출발 시차의 최빈값. 대피 정차로 행선지별 시차가 다른 간선이 있다(9호선 가양) ──
    diffs = collections.defaultdict(list)
    for (ln, day), idx in groups.items():
        net = nets[ln]
        for a in idx:
            s = rows[a]["station_nm"]
            for n in C[a] or []:
                lst = at.get((ln, day, n), [])
                lo = _bis(lst, T, T[a] + 20)
                for b in lst[lo:]:
                    dt = T[b] - T[a]
                    if dt > 1200:
                        break
                    if not from_ok(net, b, s, n) or not dest_ok(a, b, n, net):
                        continue
                    if D[a] is not None and D[a] == D[b]:
                        diffs[(ln, s, n, D[a])].append(dt)
                        diffs[(ln, s, n)].append(dt)
                    elif (D[a] is None or D[b] is None) and rows[a]["dir"] == rows[b]["dir"]:
                        diffs[(ln, s, n)].append(dt)

    def modes_of(v, tm):
        """[주 봉우리, 부 봉우리…] — 부 봉우리는 정차가 길어지는 열차(대피)의 시차. 주 봉우리 −120~+300초 안 · 높이 35% 이상만."""
        c = collections.Counter(x // BIN for x in v)
        if max(c.values()) < 5:
            return []
        # 급행·완행처럼 시차가 조금 다른 두 무리가 한 간선에 있으면 봉우리가 둘로 갈려, 배차 간격이 만드는 가짜 봉우리(다른 열차끼리의
        # 일정한 시차)보다 낮아진다 → ±2칸(±25초)을 합쳐서 높이를 잰다. 높이가 비슷하면(80%) 짧은 시차가 진짜다.
        w = {k: sum(c.get(k + j, 0) for j in range(-2, 3)) for k in c}
        top = max(w.values())
        k0 = min(k for k, n_ in w.items() if n_ >= 0.8 * top)
        k0 = max(range(k0 - 2, k0 + 3), key=lambda k: (c.get(k, 0), -abs(k - k0)))

        def med(k):
            near = sorted(x for x in v if k - 1 <= x // BIN <= k + 1)
            return near[len(near) // 2]
        out = [med(k0)]
        for k, n_ in sorted(c.items(), key=lambda x: -x[1]):
            m = k * BIN
            if n_ >= 0.35 * c[k0] and -120 <= m - out[0] <= 300 and all(abs(m - o) >= 40 for o in out):
                out.append(med(k))
        return out

    delta = {}
    for k, v in diffs.items():
        m = modes_of(v, nets[k[0]].adj[k[1]][k[2]]["tm"])
        if m:
            delta[k] = m
    for ln, net in nets.items():
        for s in net.adj:
            for n in net.adj[s]:
                if (ln, s, n) not in delta and net.adj[s][n]["tm"] is not None:
                    delta[(ln, s, n)] = [int(net.adj[s][n]["tm"])]

    def dmodes(ln, s, n, d, primary=False):
        m = delta.get((ln, s, n, d)) if d is not None else None
        if not m:
            m = delta.get((ln, s, n)) or []
        return m[:1] if primary else m

    succ, pred, how = {}, {}, {}

    def gate_fix(ln, i):
        """관문역(응암) 행은 어디서 왔는지로 다음 역이 정해진다."""
        g = LOOP_GATE.get(ln)
        if not g or rows[i]["station_nm"] != g[0] or i not in pred:
            return
        frm = rows[pred[i]]["station_nm"]
        if frm == g[1]:
            C[i] = [g[2]]
        elif frm == g[3]:
            C[i] = [g[1]]

    def run(win, tag, strict_dest, mutual, primary=False):
        made = 0
        for (ln, day), idx in groups.items():
            net = nets[ln]
            pairs = []
            for a in idx:
                if a in succ:
                    continue
                s = rows[a]["station_nm"]
                for n in C[a] or []:
                    modes = dmodes(ln, s, n, D[a], primary)
                    if not modes:
                        continue
                    lst = at.get((ln, day, n), [])
                    lo = _bis(lst, T, T[a] + min(modes) + win[0])
                    for b in lst[lo:]:
                        if T[b] - T[a] - max(modes) > win[1]:
                            break
                        if T[b] - T[a] < MIN_GAP:
                            continue
                        dev = min((T[b] - T[a] - m for m in modes), key=abs)
                        if not win[0] <= dev <= win[1]:
                            continue
                        if b in pred or not from_ok(net, b, s, n) or not dest_ok(a, b, n, net):
                            continue
                        if strict_dest and D[a] is not None and D[b] is not None and D[a] != D[b] and b not in term_row \
                                and not (ln in LOOP_GATE and D[a] == n):
                            continue
                        pairs.append((abs(dev), T[b] - T[a], T[a], a, b))
            if mutual:
                ca, cb = collections.Counter(p[3] for p in pairs), collections.Counter(p[4] for p in pairs)
                pairs = [p for p in pairs if ca[p[3]] == 1 and cb[p[4]] == 1]
            pairs.sort()
            for _, _, _, a, b in pairs:
                if a in succ or b in pred:
                    continue
                succ[a], pred[b], how[a] = b, a, tag
                made += 1
                gate_fix(ln, b)
        return made

    n1 = run(W1, "1", True, False, primary=True)
    n1 += run(W1, "1", True, False, primary=True)          # 관문역 후보가 좁혀진 뒤 한 번 더
    n1c = run(W1, "1b", True, False)                       # 부 봉우리(대피 정차)까지
    n2 = run(W2, "2", True, True)
    log(f"  잇기 1차 {n1:,} · 1차-부봉우리 {n1c:,} · 2차(넓은 창·서로 유일) {n2:,}")

    # ── 3차: 역 건너뛰기(급행) ──
    made = 0
    for (ln, day), idx in groups.items():
        net = nets[ln]
        if net.loop:
            continue
        pairs = []
        for a in idx:
            if a in succ or not D[a] or D[a] == rows[a]["station_nm"]:
                continue
            s = rows[a]["station_nm"]
            p = net.path(s, D[a], SKIP_MAX)
            acc, prev = 0, s
            for k, n in enumerate(p):
                dl = dmodes(ln, prev, n, D[a], True)
                if not dl:
                    break
                acc += dl[0]
                prev = n
                if k == 0:
                    continue
                lst = at.get((ln, day, n), [])
                lo = _bis(lst, T, T[a] + acc * SKIP_FAST)
                hit = []
                for b in lst[lo:]:
                    if T[b] - T[a] > acc + SKIP_SLOW:
                        break
                    if b in pred:
                        continue
                    if D[b] != D[a] or C[b] == [p[k - 1]]:
                        continue
                    hit.append(b)
                if hit:
                    for b in hit:
                        pairs.append((k, abs(T[b] - T[a] - acc), a, b))
                    break                                   # 가장 가까운 역에서만 찾는다
        ca, cb = collections.Counter(p[2] for p in pairs), collections.Counter(p[3] for p in pairs)
        for k, _, a, b in sorted(p for p in pairs if ca[p[2]] == 1 and cb[p[3]] == 1):
            if a in succ or b in pred:
                continue
            succ[a], pred[b], how[a] = b, a, "skip"
            made += 1
    log(f"  잇기 3차(건너뛰기) {made:,}")

    nxt = {}
    for i in range(N):
        if i not in succ and C[i] and len(C[i]) == 1:
            nxt[i] = C[i][0]
    return {"succ": succ, "pred": pred, "how": how, "term_row": term_row, "nxt": nxt, "delta": delta,
            "dest": D, "conflict": conflict, "nets": nets, "T": T, "cands": C}


def _bis(lst, T, t):
    lo, hi = 0, len(lst)
    while lo < hi:
        m = (lo + hi) // 2
        if T[lst[m]] < t:
            lo = m + 1
        else:
            hi = m
    return lo

