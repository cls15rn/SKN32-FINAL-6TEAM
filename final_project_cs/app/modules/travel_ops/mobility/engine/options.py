# -*- coding: utf-8 -*-
"""`routes{}.options[]` 한 칸씩 채우기 — 23번 방(2026-09-25). plan.py 가 부른다.

무엇을 채우나 (전부 기존 칸 · 새 키 0 · 스펙 v1.4)
  id        수단 태그를 **접두로** 싣는다 — `subway_1` · `bus_7022` · `walk` · `bike`(◆선호: 지하철/버스 구분).
            팀 시나리오도 id 를 이렇게 쓴다(`subway_direct` · `bus_sejong` · `taxi` · `walk`). 새 키 `modes` 를 안 만든다.
  label     경로 + **이유**(축별 사실 · 순위 아님). 예 「3호선 경복궁→을지로3가 → 2호선 을지로3가→성수 — 소요 가장 짧음」
  walk_m    경로 안 도보 합(m · 정수) — 소요 분과 **같은 모델**의 미터. 한 조각이라도 모르면 키를 뺀다
  fare_krw  1인 요금 — **규칙 파일에 요금 근거가 있는 것만**(도보 0 · 자전거 이용권 · 54: 지하철 괄호 · 버스 한 번).
            모르는 조각(거리 모르는 간선 · 경계에 걸린 괄호 · 공항버스 · 버스 섞인 환승)이 있으면 뺀다
  uses      plan.uses_of 그대로 + 팀 `route_uses.problem()` 자가 검사(불통과 후보는 싣지 않는다)

표시 전용(◆칸 답 전 — 만들어만 둔다 · 기본 off)
  transfer_car  환승 칸. 46 조회 규칙 그대로 — **판정기가 고른 실제 역열로만** · 정확 일치 · 폴백 없음 · 없으면 키 없음
"""
from __future__ import annotations

import collections
import heapq
import json
import math
import threading
import weakref
from pathlib import Path

from ...route_uses import problem as uses_problem   # 69: engine/ 가 팀 폴더 안으로 한 칸 더 깊어져 travel_ops 는 두 단계 위
from .timeutil import MIN_DAY
from .verify_time import leg_mode, leg_txt

# uses·label 노선명 — 팀장 확정 표기(9/24): 1~9호선은 `N호선`, 그 밖은 `route_uses.OTHER_LINES` 의 이름.
# 시간표 이름 → 계약 이름. 역 목록(시·종점)으로 같은 노선임을 확인한 것만 적는다(결정 5).
LINE_OFFICIAL = {
    "경의선": "경의중앙선",          # 용산·이촌·서빙고 … 서울역 (32 확인)
    "인천선": "인천1호선",           # 검단호수공원 … 송도달빛축제공원 — 인천도시철도 1호선
    "우이신설경전철": "우이신설선",   # 북한산우이 … 신설동
    "김포도시철도": "김포골드라인",   # 양촌 … 김포공항
}

MODE_PREFIX = {"subway": "subway", "bus": "bus", "bike": "bike", "walk": "walk"}
CAVEAT_LINE9 = "9호선은 일반열차 혼잡 값 — 급행은 더 붐빌 수 있다"


def line_name(line):
    """시간표 노선명 → uses·label 노선명. '02호선' → '2호선'."""
    if line and len(line) == 4 and line.endswith("호선") and line[:2].isdigit():
        return f"{int(line[:2])}호선"
    return LINE_OFFICIAL.get(line, line)


def station_name(nm):
    """uses 역명 — 괄호 병기와 끝의 「역」을 뺀다. 이름 자체가 「서울역」이면 그대로(9/24 표기)."""
    s = str(nm).split("(")[0].strip()
    if s == "서울역":
        return s
    return s[:-1] if s.endswith("역") and len(s) > 1 else s


def modes_of(legs):
    return [leg_mode(x) for x in legs] or ["walk"]


def kind_of(legs):
    """후보 하나의 수단 태그 — 지하철만 · 버스만 · 자전거 · 도보. 섞이면 탄 순서대로 `_` 로 잇는다."""
    ms = []
    for m in modes_of(legs):
        if not ms or ms[-1] != m:
            ms.append(m)
    return "_".join(MODE_PREFIX.get(m, m) for m in ms)


def make_id(legs, taken):
    """route 안에서 유일한 id. 버스는 노선번호를 붙인다(`bus_7022`) — 팀 시나리오 모양."""
    kind = kind_of(legs)
    if kind in ("walk", "bike") and kind not in taken:
        return kind
    if kind == "bus" and len(legs) == 1:
        base = f"bus_{legs[0]['route']}"
        if base not in taken:
            return base
        n = 2                        # 같은 노선의 다른 승하차 — `bus_405_2` (GPT 23 #9 · id 로 노선번호를 복원하지 않는다)
        while f"{base}_{n}" in taken:
            n += 1
        return f"{base}_{n}"
    if kind == "bus":
        # (98) 버스 환승(버스→버스 · 버스→버스→버스) — 탄 순서대로 잇는다(`bus_bus_1` · `bus_bus_bus_1`). `bus_<노선번호>` 와 안 겹치게
        kind = "_".join(["bus"] * len(legs))
    n = 1
    while f"{kind}_{n}" in taken:
        n += 1
    return f"{kind}_{n}"


def uses_problems(uses):
    """팀 route_uses.problem() 을 전부 — [(값, 이유)]. 빈 목록이면 통과."""
    return [(u, p) for u in uses if (p := uses_problem(u)) is not None]


# ── 도보 미터 ─────────────────────────────────────────────────────────────
def transfer_walk_m(v, legs):
    """지하철 후보의 환승 도보 m 합(환승 없으면 0). 지하철 밖 구간이 있거나 거리표에 없는 환승이 하나라도 있으면
    None(모르면 뺀다 — 판정기는 거리표 밖 환승을 0분·근거없음으로 두지만 미터는 지어내지 않는다)."""
    if not legs or any(leg_mode(x) != "subway" for x in legs):
        return None
    tot = 0.0
    for a, b in zip(legs, legs[1:]):
        w = v.tw.lookup(a["to"], a["line"], b["line"]) if v.tw is not None else None
        if w is None or w.distance_m is None:
            return None
        tot += w.distance_m
    return tot


# ── 요금(54번 방 · 규칙 fare 절만 읽는다) ──────────────────────────────────────
def _fare(v):
    return v.R["fare"]


def distance_fare(m, steps):
    """거리 추가운임(원) — base_m 을 넘은 몫을 단계마다 「every_m 마다 won」(올림)으로 매긴다."""
    extra, cur = 0, 0
    for s in steps:
        cur = s["_from"]
        if m <= cur:
            break
        end = m if s["upto_m"] is None else min(m, s["upto_m"])
        extra += math.ceil((end - cur) / s["every_m"]) * s["won"]
    return extra


def _steps(F, base_m):
    """규칙 distance_steps 에 구간 시작(_from)을 붙인다 — 첫 단계는 base_m 부터."""
    out, cur = [], base_m
    for s in F["subway"]["distance_steps"]["value"]:
        out.append(dict(s, _from=cur))
        cur = s["upto_m"] if s["upto_m"] is not None else cur
    return out


def subway_fare_at(F, m, first_board_min):
    """지하철만 — 운임거리 m(정수) · 첫 승차 분 → 원. 조조는 기본운임에만."""
    b = F["subway"]["base"]["value"]
    base = b["won"]
    eb = F["subway"]["early_bird"]["value"]
    if first_board_min is not None and first_board_min < eb["until_min"]:
        base = round(base * (1 - eb["rate"]))
    return base + distance_fare(m, _steps(F, b["base_m"]))


class FareNet:
    """지하철 운임거리 괄호(54 · 규칙 fare.subway.fare_distance_rule). 카드는 승차역·하차역만 알므로 운임은 두 역 사이 최단거리로 매겨진다.
    상한 = 거리 확정 간선만으로 된 실제 경로 중 가장 짧은 것 — ① 후보가 탄 노선별 경로 합 ② 전 노선 확정 그래프 최단
           (환승은 **서울교통공사 환승역거리 표(transfer_walk_v1.pairs)에 있는 쌍만** — 역명으로 이으면 동명이역(양평)이 이어진다) 중 작은 값.
    하한 = 전 노선 그래프 최단 — **거리 모르는 간선은 0**(입증된 하한이 없다 · GPT 54 #2 — 좌표 직선은 공표 거리보다 길 수 있어 하한이 아니다),
           같은 역명·반경 안 다른 노선 역도 0 으로 잇는다(이음을 넓게 두면 하한이 낮아질 뿐이다)."""

    def __init__(self, lo, sc, tw, cfg, est=None, tau=None, est_ok=None, floor=None, pub=None, lb_only=None, fare_lines=None):
        """est — {(노선, a, b): 선로 길이 m} 공표 거리가 없는 간선의 **추정 길이**(OSM 선로 · 문제목록 #21). None 이면 종전 그대로(공표만).
        tau — 간선 하나일 때의 상대 오차 상한. 추정 간선을 n 개 지난 경로의 오차는 tau/√n 로 본다(오차가 서로 상쇄 — 공표가 있는
        1~8호선으로 연속 n 간선 합을 재니 오차 최대가 n=1 29% · 2 18% · 3 12% · 4 8% · 8 6% · 12 4% 였다).
        요금 하한·상한은 길이 합 ∓ (추정 간선 길이 합 × tau/√n) 이다.
        est_ok — 요금을 **내도 되는** 노선(통합 거리비례 운임). est 에는 다른 노선(공항철도·신분당선 등 별도운임)의 길이도 넣는다 —
        하한 그래프가 길이 모르는 노선의 0 짜리 지름길로 무너지지 않게(그 노선을 실제로 타는 후보의 요금은 내지 않는다).
        floor — {(노선, a, b): m} 공표도 추정도 없는 간선의 **하한 바닥값**(두 역 직선 × 규칙 비율). 없으면 0(종전).
        ☆102 — 요금 거리 **원천 순서**: ① 역 순서 표 `distance_m` → ② pub(공표 역간거리 표 station_gap_v1 의 확정 값) → ③ est(선로 길이 추정).
        pub — {(노선, a, b): m} ①이 없는 간선의 **공표 거리**(확정 — 추정 간선으로 세지 않는다 · 오차 괄호 없음). 요금을 내는 노선 것만 준다.
        lb_only — {(노선, a, b): m} 요금을 내지 않는 노선(별도운임일 수 있다)이나 추정 등급 공표 값의 거리 — **하한 그래프에만** 넣는다
                  (0 짜리 지름길로 하한이 무너지지 않게 · 상한 그래프와 탄 경로 계산에는 안 쓴다 — 그 노선의 최단을 운임에 쓰는지 모른다).
        fare_lines — 요금을 **내도 되는** 노선(통합 거리비례 운임). 주면 그 밖 노선을 탄 구간은 값을 내지 않는다(pub 로 거리가 생겨도
                  별도운임 노선의 요금이 나오지 않게). None 이면 앞 판과 같다(거리 확정 간선만으로 이어지면 낸다)."""
        from .geo import meters
        pub, lb_only = pub or {}, lb_only or {}
        self.fare_lines = set(fare_lines) if fare_lines is not None else None
        self.est = dict(est or {})
        self.tau = tau or 0.0
        self.est_ok = set(est_ok or ())
        floor = floor or {}
        self._est_pairs = set()                         # (노선, frozenset({a, b})) — 길이가 추정인 간선
        self.exact = collections.defaultdict(dict)     # line → {a: {b: m}}
        self.ub = collections.defaultdict(dict)        # (line, st) → {(line, st): m} — 확정 간선 + 확실한 환승만
        self.lb = collections.defaultdict(dict)        # (line, st) → {(line, st): m}
        join_m = cfg["연결_반경_m"]
        nodes = []
        for ln, doc in lo.doc["lines"].items():
            for s in doc["stations"]:
                nodes.append((ln, s["station_nm"]))
                self.lb[(ln, s["station_nm"])]
            for e in doc["edges"]:
                a, b, d = e["a"], e["b"], e.get("distance_m")
                if d is None:                           # 102 — 역 순서 표에 없으면 공표 역간거리 표(확정)
                    d = pub.get((ln, a, b), pub.get((ln, b, a)))
                w = int(d) if d is not None else self.est.get((ln, a, b), self.est.get((ln, b, a)))
                if d is None and w is not None:
                    self._est_pairs.add((ln, frozenset((a, b))))
                if w is not None:                       # 공표 거리 또는 추정 길이(중심값) — 추정이면 _est_pairs 가 알려 준다
                    self.exact[ln].setdefault(a, {})[b] = w
                    self.exact[ln].setdefault(b, {})[a] = w
                    # (GPT 102 #5) **상한 그래프에는 요금을 내는 노선의 간선만** 넣는다. 앞 판은 목록 밖 노선(별도운임일 수 있다)의
                    #   추정 길이도 넣어, 허용 노선만 탄 후보의 상한이 그 노선을 지름길로 지나며 짧아질 수 있었다(그 길을 운임거리로
                    #   쓰는지 모른다 — 요금이 싸게 나오는 쪽 오류). 탄 경로(exact)는 _ridden 이 노선으로 막는다 · 하한 그래프에는 넣는다.
                    if (d is not None or ln in self.est_ok) and (self.fare_lines is None or ln in self.fare_lines):
                        self.ub[(ln, a)][(ln, b)] = w
                        self.ub[(ln, b)][(ln, a)] = w
                if w is None:                           # 102 — 하한 전용 공표 값(요금 안 내는 노선 · 추정 등급) → 없으면 바닥값
                    w_lb = lb_only.get((ln, a, b), lb_only.get((ln, b, a)))
                    if w_lb is None:
                        w_lb = floor.get((ln, a, b), floor.get((ln, b, a), 0))
                else:
                    w_lb = w
                self._lb_edge((ln, a), (ln, b), w_lb)
        by_nm = collections.defaultdict(list)
        for n in nodes:
            by_nm[station_name(n[1])].append(n)
        for group in by_nm.values():
            for i, x in enumerate(group):
                for y in group[i + 1:]:
                    self._lb_edge(x, y, 0)
        pts = []
        for n in nodes:
            p = sc.by_key.get(f"{n[0]}|{n[1]}") if sc else None
            if p and p.get("lat") is not None:
                pts.append((n, p["lat"], p["lng"]))
        for i, (x, xa, xo) in enumerate(pts):
            for y, ya, yo in pts[i + 1:]:
                if x[0] != y[0] and abs(xa - ya) < 0.01 and meters(xa, xo, ya, yo) <= join_m:
                    self._lb_edge(x, y, 0)
        known = set(nodes)
        # ☆105 — 거리표 줄을 판정기 이름으로 맞춘 노드 쌍(tw.links)으로 잇는다. 앞 판은 거리표 이름 그대로라 이름이 안 맞는
        #   6쌍(수서 「국철」 · 석계 「경원선」 · 서울역↔GTX-A 「서울」 · 총신대입구↔이수)이 상한 그래프에서 빠졌다.
        links = (tw.links() if getattr(tw, "links", None) else
                 [((r["from_line"], r["station_nm"]), (r["to_line"], r["station_nm"]))
                  for r in (getattr(tw, "pairs", None) or {}).values()])
        for x, y in links:
            if x in known and y in known:
                self.ub[x][y] = 0
                self.ub[y][x] = 0
                self._lb_edge(x, y, 0)

    def _lb_edge(self, x, y, w):
        for p, q in ((x, y), (y, x)):
            old = self.lb[p].get(q)
            if old is None or w < old:
                self.lb[p][q] = w

    @staticmethod
    def _dijkstra(adj, src, dst):
        dist, pq = {src: 0}, [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                return d
            if d > dist.get(u, float("inf")):
                continue
            for w, c in adj.get(u, {}).items():
                nd = d + c
                if nd < dist.get(w, float("inf")):
                    dist[w] = nd
                    heapq.heappush(pq, (nd, w))
        return None

    def _walk(self, adj, src, dst, is_est):
        """최단 경로의 (길이 합, 그중 추정 간선 길이 합, 추정 간선 수) — 길이 합이 같으면 먼저 닿은 경로. 못 이으면 None."""
        best, pq = {src: (0, 0, 0)}, [(0, 0, src)]
        cnt = 0
        while pq:
            d, _c, u = heapq.heappop(pq)
            if u == dst:
                return best[u]
            if d > best[u][0]:
                continue
            for w, c in adj.get(u, {}).items():
                nd = d + c
                cur = best.get(w)
                if cur is None or nd < cur[0]:
                    e = is_est(u, w)
                    best[w] = (nd, best[u][1] + (c if e else 0), best[u][2] + (1 if e else 0))
                    cnt += 1
                    heapq.heappush(pq, (nd, cnt, w))
        return None

    def _bounds(self, tot, est_sum, n):
        """길이 합 ∓ 추정 길이 × tau/√n."""
        if not n:
            return tot, tot
        t = self.tau / math.sqrt(n)
        return tot - est_sum * t, tot + est_sum * t

    def _ridden(self, legs):
        """① 노선별 실제 경로 — (길이 합, 추정 길이 합, 추정 간선 수). 한 구간이라도 못 이으면 None."""
        tot = est = n = 0
        for leg in legs:
            ln = leg["line"]
            if self.fare_lines is not None and ln not in self.fare_lines:
                return None                              # 102 — 요금을 내는 노선이 아니다(별도운임일 수 있다) — 거리가 있어도 값 없음
            if self.est and ln not in self.est_ok and any(k[0] == ln for k in self._est_pairs_lines()):
                return None                              # 별도운임일 수 있는 노선 — 추정 길이로 요금을 내지 않는다
            r = self._walk(self.exact.get(ln, {}), leg["from"], leg["to"],
                           lambda u, v, ln=ln: (ln, frozenset((u, v))) in self._est_pairs)
            if r is None:
                return None
            tot, est, n = tot + r[0], est + r[1], n + r[2]
        return tot, est, n

    def _est_pairs_lines(self):
        """추정 길이를 쓰는 노선 집합(처음 한 번만 만든다)."""
        got = getattr(self, "_est_lines_cache", None)
        if got is None:
            got = self._est_lines_cache = {(ln,) for ln, _p in self._est_pairs}
        return got

    def _is_est_node(self, x, y):
        return x[0] == y[0] and (x[0], frozenset((x[1], y[1]))) in self._est_pairs

    def ridden_m(self, legs):
        """① 노선별 실제 경로 거리 합(**상한값**) — 한 구간이라도 거리 확정(또는 추정) 간선만으로 못 이으면 None."""
        r = self._ridden(legs)
        return None if r is None else self._bounds(*r)[1]

    def upper_m(self, legs):
        """상한 = min(① 탄 경로, ② 확정 그래프 최단). 둘 다 못 구하면 None."""
        a, b = (legs[0]["line"], legs[0]["from"]), (legs[-1]["line"], legs[-1]["to"])
        vals = [self.ridden_m(legs)]
        r = self._walk(self.ub, a, b, self._is_est_node)
        if r is not None:
            vals.append(self._bounds(*r)[1])
        vals = [x for x in vals if x is not None]
        return min(vals) if vals else None

    def lower_m(self, legs):
        a, b = (legs[0]["line"], legs[0]["from"]), (legs[-1]["line"], legs[-1]["to"])
        r = self._walk(self.lb, a, b, self._is_est_node)
        return None if r is None else max(0, math.floor(self._bounds(*r)[0]))


_NETS = weakref.WeakKeyDictionary()
_NETS_LOCK = threading.Lock()      # ☆`[2026-09-29 문제목록 #33]` 요청 스레드가 동시에 처음 부르면 요금망을 두 번 만들었다


_NETS_EST = weakref.WeakKeyDictionary()


def est_lengths(v):
    """(추정 길이 {(노선, a, b): m}, 간선 하나의 상대 오차 상한, 요금을 내도 되는 노선, 바닥값 {(노선, a, b): m}) — 규칙
    fare.subway.distance_estimate. 규칙이 없거나 자료가 없으면 ({}, None, set(), {})."""
    rule = (_fare(v)["subway"].get("distance_estimate") or {}).get("value") or {}
    lines, tau, q = set(rule.get("lines") or ()), rule.get("tolerance"), rule.get("unknown_edge_floor")
    est = getattr(v.lo, "est_edges", None) or {}
    if not lines or not tau or not est:
        return {}, None, set(), {}
    straight = getattr(v.lo, "straight_edges", None) or {}
    floor = {k: int(m * q) for k, m in straight.items()} if q else {}
    return dict(est), tau, lines, floor


#: (102) 추정 등급 공표 값(김포골드라인 — 누적 km 의 차 · 0.1 km 반올림)의 오차 — 하한 그래프에 넣을 때 이만큼 뺀다
GAP_EST_TOL_M = 100


def pub_lengths(v):
    """(공표 역간거리 {(노선, a, b): m}, 하한 전용 {(노선, a, b): m}, 요금을 내는 노선 집합 또는 None) — 102 · 요금 거리 원천 순서의 둘째.

    공표 역간거리 표(`station_gap_v1.jsonl` · LineOrder.gap_edges)가 역 순서 표에 거리가 없는 간선을 채운다.
      · 요금을 내는 노선 = 규칙 fare.subway.distance_estimate.lines(통합 거리비례 운임 노선 — 팀장 목록 그대로 · 규칙 파일 무변경).
        그 노선의 **확정** 값만 공표 거리로 쓴다(두 원천 일치 · 단일 원천 — 본인 10/5: 단일 원천도 확정).
      · 그 밖 노선(공항철도·용인·의정부·우이신설·인천 — 별도운임일 수 있다)과 추정 등급(김포골드라인)은 **하한 그래프에만**
        넣는다(추정은 − GAP_EST_TOL_M). 그 노선을 탄 후보의 요금은 내지 않는다.
    표가 없거나 규칙에 노선 목록이 없으면 ({}, {}, None) — 앞 판과 같은 결과."""
    rule = (_fare(v)["subway"].get("distance_estimate") or {}).get("value") or {}
    lines = set(rule.get("lines") or ())
    gap = getattr(v.lo, "gap_edges", None) or {}
    if not gap or not lines:
        return {}, {}, None
    pub = {k: m for k, (m, g) in gap.items() if g == "확정" and k[0] in lines}
    lb_only = {k: (m if g == "확정" else max(0, m - GAP_EST_TOL_M)) for k, (m, g) in gap.items() if k not in pub}
    return pub, lb_only, lines


def fare_net_est(v):
    """공표 거리 + 선로 길이 추정을 합친 요금망. 추정이 없으면 None(= 공표만)."""
    net = _NETS_EST.get(v.lo)
    if net is None:
        with _NETS_LOCK:
            net = _NETS_EST.get(v.lo)
            if net is None:
                est, tau, ok, floor = est_lengths(v)
                pub, lb_only, fl = pub_lengths(v)
                net = (FareNet(v.lo, v.sc, v.tw, {"연결_반경_m": _fare(v)["subway"]["하한_연결_반경_m"]["value"]}, est, tau, ok, floor,
                               pub=pub, lb_only=lb_only, fare_lines=fl)
                       if est else False)
                _NETS_EST[v.lo] = net
    return net or None


def fare_net(v):
    """판정기(LineOrder)마다 한 번 만든다 — 판정기는 무수정(속성을 달지 않는다)."""
    net = _NETS.get(v.lo)
    if net is None:
        with _NETS_LOCK:
            net = _NETS.get(v.lo)
            if net is None:
                pub, lb_only, fl = pub_lengths(v)
                net = FareNet(v.lo, v.sc, v.tw, {"연결_반경_m": _fare(v)["subway"]["하한_연결_반경_m"]["value"]},
                              pub=pub, lb_only=lb_only, fare_lines=fl)
                _NETS[v.lo] = net
    return net


def early_bird(first_board_min, until_min, gate_window_min):
    """조조 여부 — 기준은 **개찰 태그 시각**(「교통카드 사용 승차 시」 · GPT 54 #4). 태그는 [열차 출발 − 개찰_여유, 열차 출발] 어딘가.
    True(열차가 until 전 출발 → 태그도 전) · False(창 전체가 until 이후) · None(창이 until 을 가로지른다 — 모른다)."""
    if first_board_min < until_min:
        return True
    if first_board_min - gate_window_min >= until_min:
        return False
    return None


def _subway_fare_on(v, net, legs, rides):
    if net.ridden_m(legs) is None:
        return None
    ub = net.upper_m(legs)
    lb = min(net.lower_m(legs) or 0, ub)
    F = _fare(v)
    eb = F["subway"]["early_bird"]["value"]
    early = early_bird(rides[0].depart_min, eb["until_min"], F["subway"]["early_bird_gate_window"]["value"])
    if early is None:
        return None
    board = rides[0].depart_min if early else eb["until_min"]          # subway_fare_at 은 분으로 조조를 가른다
    lo_f, hi_f = subway_fare_at(F, lb, board), subway_fare_at(F, ub, board)
    return hi_f if lo_f == hi_f else None


def subway_fare(v, legs, legs_result):
    """지하철만 후보의 1인 요금 — 괄호 하한·상한 요금이 같을 때만. 첫 승차 분은 판정기 구간 결과에서(없으면 None).
    **탄 경로의 모든 간선이 거리 확정이어야 한다**(GPT 54 #1) — 거리 모르는 노선(별도운임일 수 있는 신분당선 등)을 실제로 타면
    다른 경로의 상한으로 그 운임을 대신할 수 없다.

    ☆`[2026-10-04 #21]` 공표 거리만으로 못 내면(9호선·코레일 구간 등) **선로 길이 추정 구간**을 더한 요금망으로 다시 낸다
    (결정 15 — 대체 소스). 값이 나와도 등급은 추정이다 — 부르는 쪽이 `fare_is_est` 로 밝힌다."""
    rides = ride_results(legs, legs_result)
    if not rides or rides[0].depart_min is None:
        return None
    got = _subway_fare_on(v, fare_net(v), legs, rides)
    if got is not None:
        return got
    est = fare_net_est(v)
    return None if est is None else _subway_fare_on(v, est, legs, rides)


def fare_is_est(v, legs, legs_result):
    """이 후보의 지하철 요금이 **추정 구간**(선로 길이)을 써서 나왔나 — 공표 거리만으로는 못 낸 값이면 True."""
    if set(modes_of(legs)) != {"subway"}:
        return False                                        # 지하철만 후보의 값만 본다(버스 섞인 상한은 이미 「상한」이라 밝힌다)
    rides = ride_results(legs, legs_result)
    if not rides or rides[0].depart_min is None:
        return False
    if _subway_fare_on(v, fare_net(v), legs, rides) is not None:
        return False
    est = fare_net_est(v)
    return est is not None and _subway_fare_on(v, est, legs, rides) is not None


def bus_type(v, leg):
    r = v.bus.route(leg.get("route")) if getattr(v, "bus", None) is not None else None
    return None if r is None else (r.route_type_nm, r.term_min)


def bus_fare(v, legs, legs_result):
    """버스 한 번 — 유형별 단일요금(거리 무관). 공항·투어·모르는 유형 → None. 조조는 승차 창이 06:30 한쪽일 때만."""
    rides = ride_results(legs, legs_result)
    if not rides or len(legs) != 1:
        return None
    bt = bus_type(v, legs[0])
    if bt is None:
        return None
    typ, term = bt
    F = _fare(v)["bus"]
    if typ in F["no_fare_types"]["value"] or typ not in F["by_type"]["value"]:
        return None
    won = F["by_type"]["value"][typ]
    eb = F["early_bird"]["value"]
    if typ not in F["early_bird_types"]["value"]:
        return won
    lr = rides[0]
    if lr.depart_min is None or lr.wait_min is None or term is None:
        return None
    at_stop = lr.depart_min - lr.wait_min                  # 정류장 도착 — 승차는 [at_stop, at_stop + 배차] 어딘가(대기 모델 추정)
    if at_stop + term < eb["until_min"]:
        return round(won * (1 - eb["rate"]))
    if at_stop >= eb["until_min"]:
        return won
    return None                                            # 06:30 을 가로지른다 — 조조인지 모른다


def single_fare(F, p):
    """환승 없이 그 탈것만 탔을 때 요금 — 통합요금 상한(개별 요금 합)용. 지하철은 자기 거리 단계 · 버스는 단일요금."""
    if p["kind"] == "subway":
        return subway_fare_at(F, p["m"], None)
    return p["base"]


def transfer_fare(F, parts):
    """통합환승 합성(규칙 fare.transfer) — parts = [{kind, base, m, board, alight}] 탄 순서.
    kind 는 'subway' 또는 버스 유형. 하나라도 규칙 밖(광역·심야·모르는 유형) · 거리 모름 · 환승 창 초과 · 승차 수 초과 → None.
    요금 = 높은 기본운임 + 합산 거리 추가(**환승 단계 — 10km 넘으면 5km마다**, 지하철 단독의 50km 넘는 8km 단계가 아니다)
    · 단 개별 요금 합을 넘지 않는다(GPT 54 #3). 지하철 안 환승(개찰 안)은 부르는 쪽이 한 part 로 묶는다."""
    T = F["transfer"]
    if not parts or len(parts) > T["max_rides"]["value"]:
        return None
    if any(p["kind"] not in T["types"]["value"] or p["m"] is None or p["base"] is None for p in parts):
        return None
    w = T["window_min"]["value"]
    for a, b in zip(parts, parts[1:]):
        if a["alight"] is None or b["board"] is None:
            return None
        t = a["alight"] % MIN_DAY
        night = t >= w["night_from_min"] or t < w["night_until_min"]
        if b["board"] - a["alight"] > (w["night"] if night else w["day"]):
            return None
    d = T["distance_steps"]["value"]
    steps = [dict(s, _from=d["base_m"] if i == 0 else d["steps"][i - 1]["upto_m"]) for i, s in enumerate(d["steps"])]
    tot = max(p["base"] for p in parts) + distance_fare(sum(p["m"] for p in parts), steps)
    if T["cap"]["value"] == "sum_of_single":
        tot = min(tot, sum(single_fare(F, p) for p in parts))
    return tot


def fare_of(v, legs, legs_result):
    """1인 요금 — 규칙 fare·bike 절에 근거가 있는 것만(모르는 조각이 있으면 None → 키를 뺀다).
    도보 0 · 자전거 이용권 · 지하철만(괄호) · 버스 한 번(유형별) · 버스가 섞인 환승은 버스 운임거리 근거가 없어 None."""
    ms = set(modes_of(legs))
    if ms == {"walk"}:
        return 0
    if ms == {"bike"}:
        from .bike import fare as bike_fare
        ride = sum((lr.ride_min or 0) for lr in (legs_result or []))
        if not ride:
            return None
        return int(bike_fare(v.R["bike"]["ddareungi"]["fare"], ride)["total_won"])
    if "fare" not in v.R:
        return None
    if ms == {"subway"}:
        return subway_fare(v, legs, legs_result)
    if ms == {"bus"} and len(legs) == 1:
        return bus_fare(v, legs, legs_result)
    return None                         # 버스 섞인 환승 — fare.transfer.bus_distance 근거없음


def fare_upper_of(v, legs, legs_result):
    """버스가 섞인 환승의 **요금 상한**(1인) — 규칙 fare.transfer.cap = sum_of_single(확정): 통합요금은 탈것마다 따로 낸
    요금의 합을 넘지 않는다. 합성 요금(fare_of)은 버스 운임거리 근거가 없어 못 내지만(fare.transfer.bus_distance),
    탈것별 단일 요금은 근거가 있다 — 지하철 묶음(개찰 안 환승)은 subway_fare, 버스는 bus_fare.

    ☆`[2026-09-29 문제목록 #20]` 앞 판은 이 경로의 요금 칸을 비웠다 → 우리 재계획이 「요금 미상」으로 후보를 떨어뜨렸다.
      상한은 지어낸 값이 아니라 확정 규칙에서 나온 값이다. 부르는 쪽은 「상한」이라고 밝힌다. 한 조각이라도 모르면 None."""
    ms = [leg_mode(x) for x in legs]
    if "bus" not in ms or len(ms) < 2 or set(ms) - {"bus", "subway"}:
        return None                                         # 버스가 섞인 환승이 아니다 — fare_of 의 몫이거나 규칙 밖
    rides = ride_results(legs, legs_result)
    if rides is None:
        return None
    groups = []                                             # (수단, [leg], [결과]) — 연속 지하철은 한 묶음
    for leg, lr in zip(legs, rides):
        m = leg_mode(leg)
        if m == "subway" and groups and groups[-1][0] == "subway":
            groups[-1][1].append(leg)
            groups[-1][2].append(lr)
        else:
            groups.append((m, [leg], [lr]))
    if not any(m == "bus" for m, _l, _r in groups):
        return None                                         # 버스가 안 섞였다 — 이 함수의 몫이 아니다
    total = 0
    for m, gl, gr in groups:
        f = subway_fare(v, gl, gr) if m == "subway" else bus_fare(v, gl, gr) if m == "bus" else None
        if f is None:
            return None
        total += f
    return total


# ── 혼잡(33 · @ 부품의 경고를 이유로 옮긴다) ─────────────────────────────────
def severe_hits(warnings):
    """판정기 경고 중 극심 혼잡(MOB_W_CONGESTION_SEVERE) — [문구]. 판정기가 이미 낸 사실만 옮긴다."""
    return [w["text"].split(" — ")[0] for w in (warnings or []) if isinstance(w, dict)
            and w.get("code") == "MOB_W_CONGESTION_SEVERE"]


def ride_results(legs, legs_result):
    """판정기 구간 결과에서 「환승 …」 줄만 빼고, 남은 탄 구간이 legs 와 **개수·순서·표기 모두** 같을 때만 짝지어 준다
    (같은 표기가 두 번이어도 순서로 · GPT 23 #5 · 2차 #5 — 예상 밖 승차 결과를 건너뛰어 맞추지 않는다). 아니면 None."""
    rides = [lr for lr in (legs_result or []) if not str(lr.label).startswith("환승 ")]
    if len(rides) != len(legs) or any(lr.label != leg_txt(leg) for lr, leg in zip(rides, legs)):
        return None
    return rides


def chosen_departures(v, leg, lr, day_type):
    """판정기가 고른 편성 후보 — 그 승차 분에 떠나 그 도착 분에 닿는 (출발, 경로 판정) 목록.
    판정기와 같은 `Verifier.candidates()`·`LineOrder.travel_min_on_path()` 로 다시 찾는다(판정기 무수정)."""
    if lr is None or lr.verdict != "feasible" or lr.depart_min is None or lr.arrive_min is None:
        return []
    line, a, b = leg["line"], leg["from"], leg["to"]
    cands, _drop, _o = v.candidates(line, a, b, day_type)
    out = []
    for d, verd, _f in cands:
        if d.min != lr.depart_min:
            continue
        ride = v.lo.travel_min_on_path(line, verd.path, b)
        if ride is None or d.min + math.ceil(ride) != lr.arrive_min:
            continue
        out.append((d, verd))
    return out


def congestion_checked(v, legs, legs_result, date, day_type):
    """후보의 **실제 탄 편성**마다 승차역 혼잡 셀(노선·승차역·방향·요일·30분)이 있고 극심이 아닌가 — 전부면 True(GPT 23 #1 · 2차 #4).
    승차역 셀만 본다 — 승차 뒤 지나는 역 구간의 혼잡은 확인하지 않는다(문구도 「확인한 승차역」으로 한정).
    역이 자료에 있다는 것만으로는 안 된다(그 요일·방향·시간대 셀이 없으면 극심 경고가 안 나온 것뿐이다).
    지하철 밖 구간 · 편성 방향이 하나로 안 모임 · 셀 없음(값 null 포함) → False."""
    cg = getattr(v, "cg_data", None)
    if not cg or not legs or any(leg_mode(x) != "subway" for x in legs):
        return False
    lrs = ride_results(legs, legs_result)
    if lrs is None:
        return False
    levels = v.R["congestion"]["levels"]
    is_hol = date is not None and date.isoformat() in v.holidays
    for leg, lr in zip(legs, lrs):
        dirs = {d.dir for d, _verd in chosen_departures(v, leg, lr, day_type)}
        if len(dirs) != 1:
            return False
        hit = cg.lookup(leg["line"], leg["from"], dirs.pop(), day_type, date, lr.depart_min, levels,
                        is_holiday=is_hol)
        if hit is None or hit[1] == "극심":        # 셀이 없거나 그 셀이 극심이면 「극심 없음」이라 말할 수 없다(2차 #4)
            return False
    return True


# ── 이유 문장 ───────────────────────────────────────────────────────────
def add_reasons(opts):
    """축별 사실을 label 뒤에 붙인다 — **순위가 아니다**(rules candidates.순위_없음). 비교는 실린 후보끼리만.
    opts[i] 는 `_route`(경로 문구) · `eta_min` · `_transfers` · `_walk_min` · `_severe` · `_covered` · `_legs` 를 가진다."""
    many = len(opts) > 1
    etas = [o["eta_min"] for o in opts]
    trs = [o["_transfers"] for o in opts]
    wks = [o["_walk_min"] for o in opts]
    wms = [o.get("_walk_m") for o in opts]
    by_m = None not in wms                         # 전부 미터를 알면 거리로, 아니면 분 모델로(GPT 23 #7)
    wkey = [round(x) for x in wms] if by_m else wks
    any_severe = any(o["_severe"] for o in opts)
    for o in opts:
        facts = []
        if many and min(etas) != max(etas) and o["eta_min"] == min(etas):
            facts.append("소요 가장 짧음")
        if many and min(trs) != max(trs) and o["_transfers"] == min(trs):
            facts.append("환승 없음" if o["_transfers"] == 0 else f"환승 가장 적음({o['_transfers']}회)")
        i = opts.index(o)
        if many and None not in wkey and min(wkey) != max(wkey) and wkey[i] == min(wkey):
            facts.append("도보 거리 가장 짧음" if by_m else "도보 시간 가장 짧음")
        if o["_severe"]:
            facts.append("극심 혼잡 구간 있음(" + " · ".join(o["_severe"]) + ")")
        elif any_severe and o["_covered"]:
            facts.append("확인한 승차역 혼잡 자료에서 극심 없음")
        if (o["_severe"] or (any_severe and o["_covered"])) and any(x.get("line") == "09호선" for x in o["_legs"]):
            facts.append(CAVEAT_LINE9)
        o["label"] = o["_route"] + (" — " + " · ".join(facts) if facts else "")
    return opts


# ── 환승 칸(표시 전용 · ◆칸 · 기본 off) ─────────────────────────────────────
class TransferCar:
    """46 `transfer_car_v1.json` 조회 — `check_transfer_car_v1.lookup()`(저장소 밖 백업 · 표시 영구 off 라 갱신 스크립트에서 뺌 · 82) 과 같은 규칙.
    키 = (환승역, 타고 온 노선, 그 역열의 환승역 직전 역, 환승 노선, 환승 역열의 둘째 역) · prev 가 None 인 항목은 색인 안 함."""

    def __init__(self, doc):
        self.built_at = doc.get("built_at")
        self.by_prev, self.ambiguous = {}, set()
        for e in doc["entries"].values():
            if e.get("prev_nm") is None:
                continue
            k = (e["station_nm"], e["line"], e["prev_nm"], e["to_line"], e["to_next_nm"])
            if k in self.ambiguous:
                continue
            old = self.by_prev.get(k)
            if old is not None and old["positions"] != e["positions"]:
                # 조회 키가 같은데 칸이 다르다 — 어느 쪽인지 모른다. 키를 뺀다(순서에 따라 값이 바뀌면 안 된다 · GPT 23 #6)
                del self.by_prev[k]
                self.ambiguous.add(k)
                continue
            self.by_prev[k] = e

    @classmethod
    def load(cls, path=None):
        if path is None:
            # ☆`[2026-10-04]` cli_processed() — 자료 폴더를 아무도 안 정했으면(시험마다 처음 상태로 초기화한다 · tests/conftest.py) 명령줄 관례로 찾는다.
            #   PROCESSED 를 바로 읽으면 초기화된 뒤에는 없는 폴더를 가리켜, 표시 칸(transfer_car)이 조용히 빠졌다(test_core_routes_strips_display).
            from .paths import cli_processed
            path = cli_processed() / "mobility" / "transfer_car_v1.json"
        p = Path(path)
        if not p.exists():
            return None
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def lookup(self, line_a, path_a, line_b, path_b):
        if len(path_a) < 2 or len(path_b) < 2:
            return None
        e = self.by_prev.get((path_a[-1], line_a, path_a[-2], line_b, path_b[1]))
        if e is None or e["to_station_nm"] != path_b[0]:
            return None
        return e["positions"]


def ridden_path(v, leg, lr, day_type):
    """판정기가 고른 편성의 실제 역열(a…b). 그 승차 분·도착 분과 맞는 편성들의 경로가 하나로 모일 때만 — 아니면 None."""
    b = leg["to"]
    paths = set()
    for _d, verd in chosen_departures(v, leg, lr, day_type):
        p = list(verd.path)
        if b in p:
            paths.add(tuple(p[: p.index(b) + 1]))
    return list(paths.pop()) if len(paths) == 1 else None


def transfer_cars(v, tc, legs, legs_result, day_type):
    """후보의 환승마다 칸 — 찾은 것만 [{station, from_line, to_line, car_door}]. 하나도 없으면 None(키 없음)."""
    if tc is None or len(legs) < 2 or any(leg_mode(x) != "subway" for x in legs):
        return None
    lrs = ride_results(legs, legs_result)              # 탄 구간만 순서대로(같은 표기가 두 번이어도 · GPT 23 #5)
    if lrs is None:
        return None
    paths = [ridden_path(v, leg, lr, day_type) for leg, lr in zip(legs, lrs)]
    out = []
    for i in range(len(legs) - 1):
        pa, pb = paths[i], paths[i + 1]
        if not pa or not pb:
            continue
        pos = tc.lookup(legs[i]["line"], pa, legs[i + 1]["line"], pb)
        if not pos:
            continue
        out.append({"station": station_name(pa[-1]), "from_line": line_name(legs[i]["line"]),
                    "to_line": line_name(legs[i + 1]["line"]), "car_door": pos[0]["car_door"]})
    return out or None
