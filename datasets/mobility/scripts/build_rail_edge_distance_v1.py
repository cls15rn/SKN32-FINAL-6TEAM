# -*- coding: utf-8 -*-
"""역간 운임거리 보강 자료 — OSM 선로 길이로 간선별 거리 추정 (문제목록 #21 · 2026-10-04).

왜: 공표 역간거리(서울교통공사 역간거리 CSV)는 1~8호선 서울교통공사 구간 270 간선뿐이라 9호선·코레일 구간·공항철도 등은 요금을 못 냈다.
방법: Geofabrik OSM(ODbL)의 railway=subway|rail|light_rail 선로를 그래프로 만들고, 같은 노선의 이웃한 두 역의 좌표를 선로에 붙여
      선로를 따라가는 최단 길이를 낸다. **공표값이 있는 간선으로 오차를 재서**(보정표) 값에 같이 적는다.
등급: 추정(OSM 선로 길이 · 공표 거리와 같지 않다). 공표 거리가 있으면 공표가 이긴다 — 이 파일은 공표가 없는 간선만 채운다.
실행(지도 읽기 도구는 별도 가상환경): <venv>/python datasets/mobility/scripts/build_rail_edge_distance_v1.py
"""
import gzip
import heapq
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import osmium

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PBF = ROOT / "raw" / "osm" / "south-korea-latest.osm.pbf"
OUT = ROOT / "processed" / "mobility"
BBOX = (126.55, 37.15, 127.55, 37.95)            # 서울·수도권(경도 최소·위도 최소·경도 최대·위도 최대)
RAIL = {"subway", "rail", "light_rail", "narrow_gauge"}
SNAP_M = 200.0                                    # 역 좌표 → 선로 붙이기 한도(공표 좌표 오차·깊은 환승역 포함)
SNAP_K = 6                                        # 역마다 시험할 선로 점 수(서로 다른 연결 덩어리·평행 선로)


def hav(a, b):
    R = 6371008.8
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = p2 - p1, math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


class Rail(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = []

    def way(self, w):
        t = w.tags
        if t.get("railway") not in RAIL or t.get("service") in ("yard", "siding", "spur", "crossover"):
            return
        if t.get("tunnel") == "building_passage":
            return
        pts = []
        try:
            for n in w.nodes:
                if not n.location.valid():
                    return
                pts.append((n.ref, n.lon, n.lat))
        except osmium.InvalidLocationError:
            return
        if all(BBOX[0] <= x <= BBOX[2] and BBOX[1] <= y <= BBOX[3] for _r, x, y in pts):
            self.ways.append((w.id, t.get("railway"), pts))


def main():
    h = Rail()
    h.apply_file(str(PBF), locations=True)
    print("선로 way", len(h.ways), file=sys.stderr)
    coords, adj = {}, defaultdict(list)
    for _wid, _kind, pts in h.ways:
        for (r1, x1, y1), (r2, x2, y2) in zip(pts, pts[1:]):
            coords[r1], coords[r2] = (x1, y1), (x2, y2)
            d = hav((x1, y1), (x2, y2))
            adj[r1].append((r2, d))
            adj[r2].append((r1, d))
    ids = list(adj)
    # 연결 덩어리 — 끊긴 선로 조각에 붙은 점을 거른다
    comp, ncomp = {}, 0
    for r0 in ids:
        if r0 in comp:
            continue
        comp[r0], stack = ncomp, [r0]
        while stack:
            u = stack.pop()
            for v, _w in adj[u]:
                if v not in comp:
                    comp[v] = ncomp
                    stack.append(v)
        ncomp += 1
    csize = defaultdict(int)
    for c0 in comp.values():
        csize[c0] += 1
    # 선분 색인 — 역을 **선분 위의 점**에 붙인다(OSM 선로 점은 직선 구간에서 수백 m 띄엄띄엄이라 점에 붙이면 오차가 크다)
    cell = 0.003
    grid, segs = defaultdict(set), []
    for _wid, _kind, pts in h.ways:
        for (r1, x1, y1), (r2, x2, y2) in zip(pts, pts[1:]):
            i = len(segs)
            segs.append((r1, r2, x1, y1, x2, y2, hav((x1, y1), (x2, y2))))
            for cx in range(int(min(x1, x2) / cell), int(max(x1, x2) / cell) + 1):
                for cy in range(int(min(y1, y2) / cell), int(max(y1, y2) / cell) + 1):
                    grid[(cx, cy)].add(i)

    def snap(lng, lat):
        """가까운 선분 위 점 — 덩어리마다 가장 가까운 것 하나씩 최대 SNAP_K 개. 항목 = (거리, 덩어리, {끝점: 거기까지 m})."""
        kx = math.cos(math.radians(lat)) * 111320.0
        ky = 110574.0
        cand = {}
        cx, cy = int(lng / cell), int(lat / cell)
        seen = set()
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                for i in grid.get((cx + dx, cy + dy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    r1, r2, x1, y1, x2, y2, L = segs[i]
                    c0 = comp[r1]
                    if csize[c0] < 200:
                        continue
                    vx, vy = (x2 - x1) * kx, (y2 - y1) * ky
                    wx, wy = (lng - x1) * kx, (lat - y1) * ky
                    vv = vx * vx + vy * vy
                    t = 0.0 if vv == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / vv))
                    d = math.hypot(wx - t * vx, wy - t * vy)
                    if d <= SNAP_M and (c0 not in cand or d < cand[c0][0]):
                        cand[c0] = (d, c0, {r1: t * L, r2: (1 - t) * L}, i, t)
        return sorted(cand.values(), key=lambda z: z[0])[:SNAP_K] or None

    def dijkstra(src, dst, cap):
        """src·dst = {끝점: 시작/끝 비용}. 같은 선분 위의 두 점이면 선분 안 거리가 더 짧을 수 있어 호출 쪽이 따로 본다."""
        dist, pq = {}, []
        for n0, c0 in src.items():
            dist[n0] = c0
            pq.append((c0, n0))
        heapq.heapify(pq)
        best = None
        while pq:
            d, u = heapq.heappop(pq)
            if best is not None and d >= best:
                break
            if d > dist.get(u, 1e18) or d > cap:
                continue
            if u in dst and (best is None or d + dst[u] < best):
                best = d + dst[u]
            for v, w in adj[u]:
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return best

    order = json.loads((OUT / "line_station_order_v1.json").read_text(encoding="utf-8"))
    sc = json.loads((OUT / "station_coords.json").read_text(encoding="utf-8"))["stations"]
    rows = []
    for ln, doc in order["lines"].items():
        for e in doc["edges"]:
            a, b = sc.get(f"{ln}|{e['a']}"), sc.get(f"{ln}|{e['b']}")
            row = {"line": ln, "a": e["a"], "b": e["b"], "official_m": e.get("distance_m")}
            if not a or not b or a.get("lat") is None or b.get("lat") is None:
                row["why"] = "no_coords"
                rows.append(row)
                continue
            straight = hav((a["lng"], a["lat"]), (b["lng"], b["lat"]))
            row["straight_m"] = round(straight)
            sa, sb = snap(a["lng"], a["lat"]), snap(b["lng"], b["lat"])
            if sa is None or sb is None:
                row["why"] = "no_snap"
                rows.append(row)
                continue
            path, pick = None, None
            for da_, ca_, ra, ia_, ta_ in sa:
                for db_, cb_, rb, ib_, tb_ in sb:
                    if ca_ != cb_:
                        continue
                    pth = dijkstra(ra, rb, max(straight * 2.2 + 600, 3000))
                    if ia_ == ib_:                                    # 같은 선분 위의 두 점
                        pth = min(pth if pth is not None else 1e18, abs(ta_ - tb_) * segs[ia_][6])
                    if pth is not None and pth >= straight * 0.9 and (path is None or pth < path):
                        path, pick = pth, (da_, db_)
            if path is None:
                row["why"] = "no_path"
                rows.append(row)
                continue
            row["track_m"] = round(path)
            row["snap_m"] = [round(pick[0]), round(pick[1])]
            # 역 좌표가 선로에서 떨어진 만큼(역사 중심 ↔ 선로) 길이에 더하지 않는다 — 보정표가 그 평균 편차를 흡수한다
            rows.append(row)
    with gzip.open(OUT / "rail_edge_track_v1.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok = sum(1 for r in rows if "track_m" in r)
    print(f"간선 {len(rows)} · 선로 길이 얻음 {ok} · 못 얻음 {len(rows) - ok}", file=sys.stderr)


if __name__ == "__main__":
    main()
