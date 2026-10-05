# -*- coding: utf-8 -*-
"""X3 — 걸음 그래프(road_graph_v2 의 foot 칸)를 **numpy·scipy 로만** 읽어 걷는 거리를 내는 독립 계산기.

왜 따로 있나: 발표 코드의 길찾기(팀장 `graph_router.py` · 표준 라이브러리 A*)와 **다른 구현**으로 같은 값을 내는지 대조하고,
정류장↔역 출구 전수(수만 쌍)를 한 번에 재려고(한 출발점에서 여러 도착점 — scipy 다익스트라 한 번).
택시 라우터 방(77-2)이 차도 그래프를 읽던 방식(OSM 간선 표 → CSR → scipy 다익스트라) 그대로다.

값의 뜻(팀장 길찾기와 같은 약속):
  거리 = 길 위 거리(간선 `len` 합 · 계단은 고를 때만 1.5배, 거리는 실제 길이) + 양 끝 길 밖 직선(snap) 거리
  붙는 간선 = 걸음 최대 연결 성분(`fmain`)의 가장 가까운 간선 하나
  낙관 표시 = 길 밖 직선이 30 m 를 넘는 끝이 있다(짧게 나올 수 있다)
경로 형상은 내지 않는다(저장하지 않는다) — 거리 숫자만.
"""
from __future__ import annotations

import gzip
import json
import math
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

KY = 110540.0
STEPS_FACTOR = 1.5
OPTIMISTIC_ACCESS_M = 30.0
SNAP_MAX_M = 1200.0
SAMPLE_M = 8.0            # 간선 형상을 이 간격으로 찍어 KD 트리에 넣는다(가까운 간선 후보 찾기용 — 거리는 선분에 정확히 내려 잰다)


def hav(lat1, lon1, lat2, lon2):
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def decode_polyline(s, precision=1e6):
    """→ [(lat, lon)…]"""
    out, i, lat, lng, n = [], 0, 0, 0, len(s)
    while i < n:
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += d
            else:
                lng += d
        out.append((lat / precision, lng / precision))
    return out


class WalkGraph:
    def __init__(self, graph_dir, *, foot_key="foot", main_key=None, area=False, snap_exclude=(), snap_area=True, verbose=True):
        """area         실험판(road_graph_x3)의 광장 간선(ar)을 쓴다. 발표 판(v2)에는 그 칸이 없다 — 끄면 v2 와 같은 길.
        snap_area    False 면 광장 간선에는 붙이지 않는다(지나가기만) — 광장 테두리에 붙으면 테두리를 따라 돈다.
        snap_exclude 출발·도착을 붙이지 않을 간선의 gr 값(1 지하 · 2 고가). 지나가는 것은 막지 않는다."""
        main_key = main_key or ("fmain_a" if area else "fmain")
        snap_exclude = set(snap_exclude)
        t0 = time.time()
        d = Path(graph_dir)
        self.manifest = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8")) if (d / "MANIFEST.json").exists() else {}
        nid = {}
        nlat, nlon = [], []
        with gzip.open(d / "nodes.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                nid[r["id"]] = len(nlat)
                nlat.append(r["lat"])
                nlon.append(r["lon"])
        self.nlat, self.nlon = np.array(nlat), np.array(nlon)
        N = len(nlat)
        eu, ev, elen, est, emain, ehw, eg = [], [], [], [], [], [], []
        n_all = 0
        with gzip.open(d / "edges.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                n_all += 1
                if not r.get(foot_key) or (r.get("ar") and not area):
                    continue
                eu.append(nid[r["u"]])
                ev.append(nid[r["v"]])
                elen.append(float(r["len"]))
                est.append(int(r.get("st") or 0))
                emain.append(int(bool(r.get(main_key)) and r.get("gr", 0) not in snap_exclude
                                 and (snap_area or not r.get("ar"))))
                ehw.append(r.get("hw") or "")
                eg.append(r["g"])
        self.eu, self.ev = np.array(eu, np.int32), np.array(ev, np.int32)
        self.elen, self.est = np.array(elen), np.array(est, bool)
        self.emain = np.array(emain, bool)
        self.ehw = ehw
        E = len(eu)
        cost = self.elen * np.where(self.est, STEPS_FACTOR, 1.0)
        # 같은 두 노드를 잇는 간선이 여럿이면 싼 것 하나(CSR 은 겹친 칸을 더해 버린다) — 양방향
        a = np.concatenate([self.eu, self.ev])
        b = np.concatenate([self.ev, self.eu])
        c = np.concatenate([cost, cost])
        L = np.concatenate([self.elen, self.elen])
        keep = a != b
        a, b, c, L = a[keep], b[keep], c[keep], L[keep]
        order = np.lexsort((c, b, a))
        a, b, c, L = a[order], b[order], c[order], L[order]
        first = np.ones(len(a), bool)
        first[1:] = (a[1:] != a[:-1]) | (b[1:] != b[:-1])
        a, b, c, L = a[first], b[first], c[first], L[first]
        self.G = csr_matrix((c, (a, b)), shape=(N, N))
        self.Lm = csr_matrix((L, (a, b)), shape=(N, N))
        t1 = time.time()
        # 붙이기용: fmain 간선의 선분 표 + 찍은 점 KD 트리
        self.kx = math.cos(math.radians(37.55)) * 111320.0
        seg_e, seg_ax, seg_ay, seg_bx, seg_by, seg_cum, seg_len = [], [], [], [], [], [], []
        for e in range(E):
            if not self.emain[e]:
                continue
            pts = decode_polyline(eg[e])
            cum = 0.0
            for i in range(len(pts) - 1):
                (la1, lo1), (la2, lo2) = pts[i], pts[i + 1]
                l = hav(la1, lo1, la2, lo2)
                seg_e.append(e)
                seg_ax.append(lo1)
                seg_ay.append(la1)
                seg_bx.append(lo2)
                seg_by.append(la2)
                seg_cum.append(cum)
                seg_len.append(l)
                cum += l
        del eg
        self.seg_e = np.array(seg_e, np.int32)
        self.seg_a = np.column_stack([seg_ax, seg_ay])
        self.seg_b = np.column_stack([seg_bx, seg_by])
        self.seg_cum, self.seg_len = np.array(seg_cum), np.array(seg_len)
        # 간선 형상 전체 길이(위치 비율 계산용)
        self.geom_len = np.zeros(E)
        np.add.at(self.geom_len, self.seg_e, self.seg_len)
        n_s = np.maximum(np.ceil(self.seg_len / SAMPLE_M).astype(int), 1) + 1
        idx = np.repeat(np.arange(len(n_s)), n_s)
        start = np.cumsum(n_s) - n_s
        k = np.arange(len(idx)) - start[idx]
        t = k / (n_s[idx] - 1)
        px = (self.seg_a[idx, 0] + t * (self.seg_b[idx, 0] - self.seg_a[idx, 0])) * self.kx
        py = (self.seg_a[idx, 1] + t * (self.seg_b[idx, 1] - self.seg_a[idx, 1])) * KY
        self.tree = cKDTree(np.column_stack([px, py]))
        self.pt_seg = idx
        self.load_s = round(time.time() - t0, 1)
        self.stats = {"nodes": N, "edges_all": n_all, "edges_foot": E, "edges_foot_main": int(self.emain.sum()),
                      "edges_steps": int(self.est.sum()), "csr_nnz": int(self.G.nnz), "segments_main": len(seg_e),
                      "sample_points": len(idx), "read_csr_s": round(t1 - t0, 1), "snap_index_s": round(time.time() - t1, 1),
                      "load_s": self.load_s}
        if verbose:
            print("[걸음 그래프]", json.dumps(self.stats, ensure_ascii=False), flush=True)

    # ── 좌표 → 가장 가까운 걸음 간선 ─────────────────────────────────────────
    def snap(self, lat, lon):
        """(간선, u 에서의 길 위 거리 m, 간선 길이 m, 길 밖 직선 m) 또는 None(1,200 m 안에 걸음 길 없음)."""
        kx = math.cos(math.radians(lat)) * 111320.0
        q = (lon * self.kx, lat * KY)
        best = None
        for kq in (24, 96, 400):
            dd, ii = self.tree.query(q, k=kq)
            segs = np.unique(self.pt_seg[ii[np.isfinite(dd)]])
            ax, ay = (self.seg_a[segs, 0] - lon) * kx, (self.seg_a[segs, 1] - lat) * KY
            bx, by = (self.seg_b[segs, 0] - lon) * kx, (self.seg_b[segs, 1] - lat) * KY
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = np.where(L2 > 0, np.clip(-(ax * dx + ay * dy) / np.where(L2 > 0, L2, 1), 0, 1), 0.0)
            dist = np.hypot(ax + t * dx, ay + t * dy)
            j = int(np.argmin(dist))
            best = (int(segs[j]), float(t[j]), float(dist[j]))
            # 후보로 본 점들 중 가장 먼 것보다 가깝게 찾았으면 더 가까운 선분은 없다(찍은 간격 8 m 여유)
            if best[2] + SAMPLE_M <= float(np.max(dd)):
                break
        s, t, dist = best
        if dist > SNAP_MAX_M:
            return None
        e = int(self.seg_e[s])
        return e, float(self.seg_cum[s] + t * self.seg_len[s]), float(self.geom_len[e]), dist

    def _ends(self, sn):
        """붙은 점에서 간선 두 끝까지 (노드, 고르는 비용, 실제 거리)."""
        e, d_u, gl, _ = sn
        f = 0.0 if gl <= 0 else min(max(d_u / gl, 0.0), 1.0)
        L = float(self.elen[e])
        k = STEPS_FACTOR if self.est[e] else 1.0
        return [(int(self.eu[e]), L * f * k, L * f), (int(self.ev[e]), L * (1 - f) * k, L * (1 - f))]

    def from_point(self, lat, lon, limit_m=6000.0):
        """한 출발점 준비 — 여러 도착점에 다시 쓴다."""
        sn = self.snap(lat, lon)
        if sn is None:
            return None
        ends = self._ends(sn)
        src = [ends[0][0], ends[1][0]]
        D, P = dijkstra(self.G, directed=True, indices=src, limit=limit_m * STEPS_FACTOR, return_predecessors=True)
        return {"sn": sn, "ends": ends, "D": D, "P": P}

    def _real_len(self, P_row, src, dst):
        tot, n, guard = 0.0, dst, 0
        while n != src:
            p = int(P_row[n])
            if p < 0:
                return None
            tot += float(self.Lm[p, n])
            n = p
            guard += 1
            if guard > 100000:
                return None
        return tot

    def to_point(self, S, lat, lon):
        """S = from_point 결과. → dict(거리 m, 길 위 m, 길 밖 m(출발·도착), 낙관, 계단 여부는 안 냄) 또는 {"error": …}."""
        if S is None:
            return {"error": "out_of_area"}
        tn = self.snap(lat, lon)
        if tn is None:
            return {"error": "out_of_area"}
        sn = S["sn"]
        best = None
        if sn[0] == tn[0]:                                   # 같은 간선 위
            e = sn[0]
            gl = sn[2] or 1.0
            L = float(self.elen[e]) * abs(tn[1] - sn[1]) / gl
            best = (L * (STEPS_FACTOR if self.est[e] else 1.0), L)
        for i, (us, cs, ls) in enumerate(S["ends"]):
            for (ut, ct, lt) in self._ends(tn):
                d = S["D"][i, ut]
                if not np.isfinite(d):
                    continue
                c = cs + d + ct
                if best is None or c < best[0]:
                    mid = self._real_len(S["P"][i], us, ut)
                    if mid is None:
                        continue
                    best = (c, ls + mid + lt)
        if best is None:
            return {"error": "no_path"}
        acc = sn[3] + tn[3]
        return {"dist_m": round(best[1] + acc, 1), "on_path_m": round(best[1], 1), "snap_m": [round(sn[3], 1), round(tn[3], 1)],
                "optimistic": max(sn[3], tn[3]) > OPTIMISTIC_ACCESS_M}

    def route(self, lat1, lon1, lat2, lon2, limit_m=6000.0):
        return self.to_point(self.from_point(lat1, lon1, limit_m), lat2, lon2)


if __name__ == "__main__":
    import sys
    g = WalkGraph(sys.argv[1])
    # 경복궁역 5번 출구 → 광화문 문루
    print(g.route(37.576276, 126.975494, 37.5759283, 126.9768161))
