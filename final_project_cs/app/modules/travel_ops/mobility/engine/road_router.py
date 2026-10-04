# -*- coding: utf-8 -*-
"""서버 없는 파이썬 도로 라우터 — 76 서울 차도 그래프 파일(`road_graph_v1`) 위 최단 거리 경로. 77번 방(2026-09-30).

왜: 택시·자동차 소요가 경로 서버(GraphHopper)가 있는 기기에서만 나왔다. 팀원·코어 서버엔 그 서버가 없다(공용 서버 없음) →
  실행에서 택시 대안이 늘 근거없음이었다(본인 9/29 「시험은 픽스처로 돌더라도 실행이 서버 없어 근거없음이면 안 된다」).
  ☆99(2026-10-04) 그 서버를 부르는 코드를 지웠다 — 이 라우터가 택시·자동차의 **유일한** 경로 계산이다. 아래에 남은 「옛 서버」
  비교는 77 때의 측정 기록이다.

무엇을 하나
  ① 적재(프로세스당 1회 · 약 10~15초): nodes·edges `.jsonl.gz` → 방향 간선(일방 `ow` 1 = u→v · -1 = v→u · 0 = 양방향)
     → `scipy.sparse` csr(같은 두 노드 사이 평행 간선은 짧은 것 하나) · 형상 `g`(polyline 1e-6)를 25 m 이하 조각으로
     잘라 조각 중점 KD-tree(`scipy.spatial.cKDTree` · 평면 근사 m).
  ② 스냅: 좌표 → 가장 가까운 간선 위 점. 차량은 `main=1`(최대 강연결 성분)만, 자전거는 `bike=1`·`bmain=1` 만.
     200 m(`SNAP_MAX_M`)를 넘으면 스냅하지 않는다(→ RouterDown · 근거없음). 두 끝 노드가 모두
     범위 밖(`r=0` · 3 km 여백)이면 범위 밖으로 본다.
  ③ `scipy.sparse.csgraph.dijkstra` — 가중은 **정적 시간**(기본 배선 · `speed=CarGraph.static_kmh`): TOPIS 링크 평일
     07~20시 평균 · 링크 없는 간선급은 도로급 평균 · 골목은 고정값 — 18번 방 maxspeed 주입 v2 와 같은 잣대라 경로
     **선택**이 옛 서버와 같은 원리다. `speed` 를 안 주면 길이 가중. 출발 가상 노드 하나(스냅 후보 → 간선 양끝, 방향 허용된
     쪽만)로 한 번 돈다. 도착은 스냅점까지 남은 몫을 더해 가장 싼 쪽. 스냅 이격은 벌점으로 더한다.
     ★ 77 첫 메시지는 「길이 가중」이었다 — 353 도로·표본 7구간 대조에서 길이 가중이 간선도로를 버리고 골목·시내로
       질러 소요가 크게 틀려(인천공항→명동 03시 134분 vs 네이버 52 · 옛 서버 76) 시간 가중으로 바꿨다(77 닫힘 문서 표).
  ④ 결과는 **옛 서버의 `/route` 응답과 같은 모양**(`paths[0].points.coordinates` · `details.osm_way_id` ·
     `details.road_class` · `distance`)으로 돌려준다 → 소요는 `car.CarGraph.compute`(= 옛 graph_time.py) **같은 함수**가
     TOPIS 링크×요일형×시간대 프로파일로 낸다. 계산 규칙·등급 규칙·요금 산식은 그대로다.

한계(문서 · 등급 추정)
  · **회전 제약 없음** — turn restriction 을 싣지 않았다. 좌회전 금지 교차로에서 실제보다 짧은 길이 나올 수 있다.
  · 경로 선택 속도는 정적(평일 낮 평균)이다 — 시각별 막힘으로 길을 바꾸지 않는다(옛 서버와 같다 · 18번 「경로는 정적, 소요는 동적」).
  · 회전·신호 대기 없음(옛 서버도 같다).
  · ★ 경로는 저장하지 않는다 — 응답 dict 는 CarService.leg 안에서 소요 계산 뒤 버린다(car.py 와 같은 원칙).

자전거(profile="bike"): 같은 그래프 `bike=1` 간선(`bow` 자전거 일방)으로 **거리만** 낸다. 소요(time)는 싣지 않는다 —
  자전거 속도 규칙이 없고 대여소까지 걷는 보행망이 이 그래프에 없다. 그래서 판정기에는 끼우지 않았다 — 자전거 승차
  소요는 근거없음이다(99 · 살릴 때는 보행 경로 거리 ÷ 자전거 평균 속도(단위 환산) · 본인 10/4).
"""
from __future__ import annotations

import gzip
import json
import math
import threading
from pathlib import Path

SNAP_MAX_M = 200.0          # 76 검수: 353 도로 706점 p90 6.8 m · 최대 21.2 m · 랜드마크 최대 211 m(킨텍스 건물 안 좌표)
PIECE_M = 25.0              # 스냅 색인 조각 길이 상한
SNAP_ALT_M = 30.0           # 가장 가까운 간선보다 이만큼 안쪽의 다른 간선도 후보(중앙분리 도로 반대 차로 · 길 건너기 폭)
SNAP_K = 6                  # 스냅 후보 간선 수 상한
NO_DOOR_HW = frozenset({"motorway", "motorway_link"})   # 출발·도착(승하차) 스냅에서 빼는 도로급 — 경유점은 그대로
SNAP_PEN_S_PER_M = 3.6 / 20.0   # 시간 가중일 때 이격 1 m 의 벌점(초) — 20 km/h 로 그 거리를 도는 셈(추정 · 후보 고르기용)
ROAD_SOURCE_ID = "road_graph_v1@2026-09-18"      # OSM 기준 시각(76 README) — 옛 서버 그래프와 같은 pbf
_R = 6371000.0
_LAT0 = math.radians(37.55)
_KX = _R * math.cos(_LAT0) * math.pi / 180.0      # 경도 1도 → m (평면 근사 · 스냅 후보 찾기에만)
_KY = _R * math.pi / 180.0


def _hav(lon1, lat1, lon2, lat2):
    la1, la2 = math.radians(lat1), math.radians(lat2)
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * _R * math.asin(math.sqrt(d))


def decode_polyline(s, precision=6):
    """Google encoded polyline → [(lat, lon), …]. 76 은 정밀도 1e-6 으로 썼다."""
    out, i, lat, lon, f = [], 0, 0, 0, 10.0 ** precision
    n = len(s)
    while i < n:
        vals = []
        for _ in range(2):
            shift = res = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(res >> 1) if res & 1 else res >> 1)
        lat += vals[0]
        lon += vals[1]
        out.append((lat / f, lon / f))
    return out


class _Net:
    """한 프로파일(차량 · 자전거)의 방향 그래프 + 스냅 색인."""

    def __init__(self, n_nodes, arcs, edge_ids, snap_ok, geoms):
        import numpy as np
        from scipy.spatial import cKDTree
        # arcs: (tail, head, w, edge, fwd) — 같은 (tail, head) 는 가장 짧은 것 하나
        a = np.array(arcs, dtype=np.float64).reshape(-1, 5)
        order = np.lexsort((a[:, 2], a[:, 1], a[:, 0]))
        a = a[order]
        keep = np.ones(len(a), dtype=bool)
        keep[1:] = (a[1:, 0] != a[:-1, 0]) | (a[1:, 1] != a[:-1, 1])
        a = a[keep]
        self.tail = a[:, 0].astype(np.int64)
        self.head = a[:, 1].astype(np.int64)
        self.w = a[:, 2]
        self.arc_edge = a[:, 3].astype(np.int64)
        self.arc_fwd = a[:, 4].astype(bool)
        self.N = n_nodes
        # 가상 출발 노드 = N (행 N 은 비어 있다 · 질의마다 끝에 두 칸을 덧붙인다)
        counts = np.bincount(self.tail, minlength=n_nodes + 1)
        self.indptr = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
        self.indices = self.head.astype(np.int32)
        self.data = self.w.copy()
        self.csr_shape = (n_nodes + 1, n_nodes + 1)
        # 스냅 색인 — 조각 중점
        pts, own = [], []
        for e in edge_ids:
            if not snap_ok[e]:
                continue
            g = geoms[e]
            for k in range(len(g) - 1):
                (la1, lo1), (la2, lo2) = g[k], g[k + 1]
                seg = _hav(lo1, la1, lo2, la2)
                m = max(1, int(math.ceil(seg / PIECE_M)))
                for j in range(m):
                    t = (j + 0.5) / m
                    pts.append(((lo1 + t * (lo2 - lo1)) * _KX, (la1 + t * (la2 - la1)) * _KY))
                    own.append(e)
        self.snap_pts = np.array(pts, dtype=np.float64)
        self.snap_edge = np.array(own, dtype=np.int64)
        self.kd = cKDTree(self.snap_pts)

    def arc_of(self, p, q):
        """p→q 방향 간선(arc) 번호."""
        import numpy as np
        lo, hi = self.indptr[p], self.indptr[p + 1]
        k = lo + int(np.searchsorted(self.head[lo:hi], q))
        if k < hi and self.head[k] == q:
            return k
        return None


class RoadRouter:
    """76 차도 그래프 파일 위 라우터. `route(s, e, profile)` 의 응답 모양은 car.CarGraph.prepare 가 읽는 `/route` 모양이다.

    s, e = (lng, lat). 실패하면 car.RouterDown(스냅 실패 · 범위 밖 · 경로 없음) — CarService 는 근거없음으로 낸다(넘길 다른 라우터 없음 · 99).
    """

    door = True                 # 출발·도착은 승하차 지점 — 자동차전용도로에 붙이지 않는다(도로 위 끝점 대조 때만 False)
    source_id = ROAD_SOURCE_ID

    def __init__(self, graph_dir, speed=None):
        """speed: None 이면 **길이 가중**. (way, seg_idx, 'fwd'|'bwd', highway) → km/h 함수를 주면 **정적 시간 가중**
        (18번 방 maxspeed 주입 v2 와 같은 잣대 — car.CarGraph.static_kmh). 소요는 어느 쪽이든 CarGraph.compute 가 따로 낸다."""
        self.dir = Path(graph_dir)
        self.speed = speed
        self.weight = "time" if speed else "length"
        self.calls = 0
        self._lock = threading.Lock()
        self._loaded = False
        self.load_s = None
        self._region = None

    # ── 적재 ──────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, graph_dir=None, speed=None):
        """폴더에 nodes·edges 가 있으면 라우터를(적재는 첫 호출 때), 없으면 None. scipy 가 없어도 None."""
        if graph_dir is None:
            from .paths import PROCESSED
            graph_dir = PROCESSED / "mobility" / "road_graph_v1"
        p = Path(graph_dir)
        if not ((p / "nodes.jsonl.gz").exists() and (p / "edges.jsonl.gz").exists()):
            return None
        try:
            import numpy  # noqa: F401
            import scipy.sparse.csgraph  # noqa: F401
            import scipy.spatial  # noqa: F401
        except ImportError:
            return None
        return cls(p, speed=speed)

    def _ensure(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            import time
            t0 = time.perf_counter()
            self._build()
            self.load_s = round(time.perf_counter() - t0, 1)
            self._loaded = True

    def _build(self):
        idx, lat, lon, rin = {}, [], [], []
        with gzip.open(self.dir / "nodes.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                idx[r["id"]] = len(lat)
                lat.append(r["lat"])
                lon.append(r["lon"])
                rin.append(r.get("r", 1))
        self.lat, self.lon, self.rin = lat, lon, rin
        E = _EdgeTable()                        # e → (u, v, way, len, hw, geom) · 열마다 배열로(메모리)
        self._ow, self._bow = [], []            # 간선별 방향(차량 ow · 자전거 bow) — 그 프로파일에 없는 간선은 None
        car_arcs, bike_arcs = [], []
        import array
        self._fac_f, self._fac_b = array.array("f"), array.array("f")   # 차량 간선 방향별 가중/m (길이 가중이면 1)
        car_ok, bike_ok = [], []
        with gzip.open(self.dir / "edges.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                u, v = idx[r["u"]], idx[r["v"]]
                e = len(E)
                E.append(u, v, r["way"], float(r["len"]), r["hw"], r["g"])
                L = float(r["len"])
                c = bool(r.get("car")) and bool(r.get("main"))
                b = bool(r.get("bike")) and bool(r.get("bmain"))
                car_ok.append(c)
                bike_ok.append(b)
                self._ow.append(r.get("ow", 0) if c else None)
                self._bow.append(r.get("bow", 0) if b else None)
                ff = fb = 1.0
                if c:
                    ow = r.get("ow", 0)
                    if self.speed is not None:
                        ff, fb = self._time_fac(r, L)
                    if ow in (0, 1):
                        car_arcs.append((u, v, L * ff, e, 1))
                    if ow in (0, -1):
                        car_arcs.append((v, u, L * fb, e, 0))
                self._fac_f.append(ff)
                self._fac_b.append(fb)
                if b:
                    bow = r.get("bow", 0)
                    if bow in (0, 1):
                        bike_arcs.append((u, v, L, e, 1))
                    if bow in (0, -1):
                        bike_arcs.append((v, u, L, e, 0))
        E.freeze()
        self.E = E
        geoms = _Geoms(E)
        n = len(lat)
        self.nets = {"car": _Net(n, car_arcs, [i for i, ok in enumerate(car_ok) if ok], car_ok, geoms)}
        # 자전거 망은 처음 부를 때 만든다(판정기는 자전거에 이 라우터를 안 쓴다 — 메모리 절약)
        import numpy as np
        self._bike_src = (n, np.array(bike_arcs, dtype=np.float64).reshape(-1, 5), bike_ok, geoms)
        del car_arcs, bike_arcs
        self.stats = {"nodes": n, "edges": len(E), "car_arcs": int(len(self.nets["car"].w)),
                      "car_snap_pieces": int(len(self.nets["car"].snap_pts))}

    def _time_fac(self, r, L):
        """간선 한 행 → (u→v 초/m, v→u 초/m). 세그먼트 i∈[sa,sb) 마다 정적 속도(km/h)로 시간을 더해 길이로 나눈다."""
        g = decode_polyline(r["g"])
        way, sa, hw = r["way"], r["sa"], r["hw"]
        seg = [_hav(a[1], a[0], b[1], b[0]) for a, b in zip(g, g[1:])]
        tot = sum(seg) or 1.0
        out = []
        for d in ("fwd", "bwd"):
            t = 0.0
            for i, m in enumerate(seg):
                v = self.speed(way, sa + i, d, hw) or 20.0
                t += m / max(v, 3.0) * 3.6
            out.append(t / tot)
        return out[0], out[1]

    def cost_fac(self, e, fwd, profile):
        """간선 e 를 그 방향으로 1 m 갈 때의 가중(길이 가중이면 1 · 시간 가중이면 초)."""
        if profile != "car":
            return 1.0
        return (self._fac_f if fwd else self._fac_b)[e]

    def _net(self, profile):
        if profile not in self.nets:
            with self._lock:
                if profile not in self.nets:
                    n, arcs, ok, geoms = self._bike_src
                    self.nets[profile] = _Net(n, arcs, [i for i, x in enumerate(ok) if x], ok, geoms)
                    self._bike_src = None
        return self.nets[profile]

    def geom(self, e):
        return decode_polyline(self.E[e][5])      # 풀어 둔 형상은 들고 있지 않는다(메모리) — 질의당 수백 간선이라 충분히 빠르다

    # ── 스냅 ──────────────────────────────────────────────────────────────
    def snap_candidates(self, pt, profile="car", door=False):
        """(lng, lat) → 간선별 가장 가까운 점 목록(가까운 순). 각 dict(edge, off_m = 간선 u 에서 m, d_m 이격, pt (lat, lon)).

        가장 가까운 것 + 그보다 SNAP_ALT_M 안쪽의 다른 간선(최대 SNAP_K 개)까지 준다 — 중앙분리 도로에서 반대 방향
        차로에 붙어 수 km 돌아 U턴하는 경로를 막는다(경로 계산이 이격 m 을 벌점으로 더해 고른다).
        """
        self._ensure()
        net = self._net(profile)
        x, y = pt[0] * _KX, pt[1] * _KY
        cand = net.kd.query_ball_point((x, y), SNAP_MAX_M + PIECE_M)
        per = {}
        for e in {int(net.snap_edge[i]) for i in cand}:
            if door and profile == "car" and self.E[e][4] in NO_DOOR_HW:
                continue                               # 자동차전용도로(본선·연결로)에서는 타고 내릴 수 없다
            g = self.geom(e)
            acc, best = 0.0, None
            for k in range(len(g) - 1):
                (la1, lo1), (la2, lo2) = g[k], g[k + 1]
                ax, ay, bx, by = lo1 * _KX, la1 * _KY, lo2 * _KX, la2 * _KY
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
                qla, qlo = la1 + t * (la2 - la1), lo1 + t * (lo2 - lo1)
                d = _hav(pt[0], pt[1], qlo, qla)
                seg = _hav(lo1, la1, lo2, la2)
                if best is None or d < best["d_m"]:
                    best = {"edge": e, "off_m": acc + t * seg, "d_m": d, "pt": (qla, qlo)}
                acc += seg
            if best is not None and best["d_m"] <= SNAP_MAX_M:
                L = self.E[e][3]
                best["off_m"] = min(L, best["off_m"] * (L / acc)) if acc > 0 else 0.0   # len(하버사인 합)에 맞춘다
                per[e] = best
        out = sorted(per.values(), key=lambda b: b["d_m"])
        if not out:
            return []
        lim = out[0]["d_m"] + SNAP_ALT_M
        return [b for b in out if b["d_m"] <= lim][:SNAP_K]

    def snap(self, pt, profile="car"):
        """가장 가까운 한 점(+ in_region) 또는 None — 검수·시험용."""
        c = self.snap_candidates(pt, profile)
        if not c:
            return None
        b = dict(c[0])
        u, v = self.E[b["edge"]][0], self.E[b["edge"]][1]
        b["in_region"] = bool(self.rin[u] or self.rin[v]) or self.in_region(pt)
        return b

    def in_region(self, pt):
        """(lng, lat) 가 76 범위(region_v1.geojson · 서울+인접 8시+영종+공항고속도로 회랑) 안인가. 파일이 없으면 True."""
        if self._region is None:
            self._region = _load_region(self.dir / "region_v1.geojson")
        if not self._region:
            return True
        return any(_in_poly(pt, poly) for poly in self._region)

    # ── 경로 ──────────────────────────────────────────────────────────────
    def route(self, s, e, profile="car", via=None):
        from .car import RouterDown
        if profile not in ("car", "bike"):
            raise RouterDown(f"도로 그래프 라우터는 {profile} 프로파일을 모른다(car · bike 만)")
        self._ensure()
        self.calls += 1
        pts = [s] + list(via or []) + [e]
        snaps = []
        for k_, p in enumerate(pts):
            end = k_ in (0, len(pts) - 1)
            cs = self.snap_candidates(p, profile, door=end and self.door)
            if not end:
                cs = cs[:1]          # 경유점은 가장 가까운 한 점 — 앞 구간 도착과 뒤 구간 출발이 같은 점이어야 이어진다(GPT 77-2 #8)
            if not cs:
                raise RouterDown(f"도로 그래프에 스냅 못 함 — {SNAP_MAX_M:.0f} m 안에 {profile} 간선이 없다 "
                                 f"({p[0]:.5f},{p[1]:.5f})", code="no_snap")
            # 범위: 질의 좌표가 범위 도형 안이거나, 붙은 간선 끝 노드가 범위 안(r=1)이면 받는다. 3 km 여백만의 질의는 밖.
            if not (self.in_region(p) or any(self.rin[self.E[c["edge"]][0]] or self.rin[self.E[c["edge"]][1]] for c in cs)):
                raise RouterDown(f"도로 그래프 범위 밖 ({p[0]:.5f},{p[1]:.5f}) — 서울·인접 8시·영종·공항고속도로 회랑만",
                                 code="out_of_area")
            snaps.append(cs)
        legs = [self._leg(snaps[i], snaps[i + 1], profile) for i in range(len(snaps) - 1)]
        used = [lg[1] for lg in legs]
        pieces = [pc for lg in legs for pc in lg[0]]
        coords, way_det, rc_det, dist = [], [], [], 0.0
        for way, hw, poly, L in pieces:          # 조각(간선 일부 포함)을 `/route` 모양으로 잇는다
            if len(poly) < 2:
                continue
            if not coords:
                coords.append([poly[0][1], poly[0][0]])
            elif abs(coords[-1][0] - poly[0][1]) > 1e-9 or abs(coords[-1][1] - poly[0][0]) > 1e-9:
                coords.append([poly[0][1], poly[0][0]])     # 경유점에서 다른 간선으로 붙은 경우 등 — 거의 없음
            start = len(coords) - 1
            for la, lo in poly[1:]:
                coords.append([lo, la])
            end = len(coords) - 1
            if way_det and way_det[-1][2] == way and way_det[-1][1] == start:
                way_det[-1][1] = end
            else:
                way_det.append([start, end, way])
            if rc_det and rc_det[-1][2] == hw and rc_det[-1][1] == start:
                rc_det[-1][1] = end
            else:
                rc_det.append([start, end, hw])
            dist += L
        if len(coords) < 2:           # 같은 점 — 길이 0 경로
            c0 = snaps[0][0]
            coords = [[c0["pt"][1], c0["pt"][0]], [c0["pt"][1], c0["pt"][0]]]
            way_det = [[0, 1, self.E[c0["edge"]][2]]]
            rc_det = [[0, 1, self.E[c0["edge"]][4]]]
        snap_m = [round(used[0][0], 1)] + [round(u[1], 1) for u in used]
        return {"paths": [{"distance": round(dist, 1), "time": None,
                           "points": {"type": "LineString", "coordinates": coords},
                           "details": {"osm_way_id": way_det, "road_class": rc_det},
                           "snap_m": snap_m}],
                "_router": ROAD_SOURCE_ID, "info": {"router": "road_graph_v1", "turn_restrictions": False}}

    def _part(self, e, off_a, off_b):
        """간선 e 형상 중 u 에서 off_a~off_b m 구간(off_a > off_b 면 거꾸로)을 (lat, lon) 열로."""
        g = self.geom(e)
        L = self.E[e][3]
        cum = [0.0]
        for a, b in zip(g, g[1:]):
            cum.append(cum[-1] + _hav(a[1], a[0], b[1], b[0]))
        sc = (cum[-1] / L) if L > 0 else 1.0
        lo_, hi_ = sorted((off_a * sc, off_b * sc))

        def at(m):
            for k in range(len(g) - 1):
                if cum[k + 1] >= m or k == len(g) - 2:
                    seg = cum[k + 1] - cum[k]
                    t = 0.0 if seg <= 0 else max(0.0, min(1.0, (m - cum[k]) / seg))
                    return (g[k][0] + t * (g[k + 1][0] - g[k][0]), g[k][1] + t * (g[k + 1][1] - g[k][1]))
            return g[-1]
        out = [at(lo_)] + [g[k] for k in range(1, len(g) - 1) if lo_ < cum[k] < hi_] + [at(hi_)]
        return out if off_a <= off_b else out[::-1]

    def _leg(self, A, B, profile):
        """스냅 후보 A(출발) → B(도착). 반환 ([(way, hw, [(lat,lon)…], 길이 m)] 지나간 순서, (출발 이격, 도착 이격)).

        비용 = 이격 m(벌점) + 도로 길이 m. 출발 가상 노드 S 하나로 dijkstra 한 번.
        """
        import numpy as np
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra
        from .car import RouterDown
        net = self._net(profile)
        N = net.N
        best = None                                   # (cost, kind, …)
        pen = 1.0 if self.weight == "length" or profile != "car" else SNAP_PEN_S_PER_M   # 이격 벌점(가중 단위)
        fac = self.cost_fac
        # 같은 간선 위 직행
        for a in A:
            fa, ba = self._dirs(a["edge"], profile)
            for b in B:
                if a["edge"] != b["edge"]:
                    continue
                fwd = b["off_m"] >= a["off_m"]
                if (fwd and fa) or (not fwd and ba):
                    c = abs(b["off_m"] - a["off_m"]) * fac(a["edge"], fwd, profile) + (a["d_m"] + b["d_m"]) * pen
                    if best is None or c < best[0]:
                        best = (c, "same", a, b)
        # 가상 출발 → 간선 끝(노드마다 가장 싼 것 하나)
        src = {}                                      # node → (cost, cand, 'fwd'|'bwd')
        for a in A:
            fa, ba = self._dirs(a["edge"], profile)
            u, v, _, L, _, _ = self.E[a["edge"]]
            if fa:
                c = a["d_m"] * pen + max(L - a["off_m"], 0.0) * fac(a["edge"], True, profile)
                if v not in src or c < src[v][0]:
                    src[v] = (c, a, "fwd")
            if ba:
                c = a["d_m"] * pen + max(a["off_m"], 0.0) * fac(a["edge"], False, profile)
                if u not in src or c < src[u][0]:
                    src[u] = (c, a, "bwd")
        if src:
            nodes = sorted(src)
            ind = np.concatenate([net.indices, np.array(nodes, dtype=np.int32)])
            dat = np.concatenate([net.data, np.array([max(src[n][0], 1e-6) for n in nodes])])
            ptr = net.indptr.copy()
            ptr[-1] = ptr[-2] + len(nodes)
            G = csr_matrix((dat, ind, ptr), shape=net.csr_shape)
            dist, pred = dijkstra(G, directed=True, indices=N, return_predecessors=True)
            for b in B:
                fb, bb = self._dirs(b["edge"], profile)
                u, v, _, L, _, _ = self.E[b["edge"]]
                for ok, node, rest, how in ((fb, u, b["off_m"] * fac(b["edge"], True, profile), "fwd"),
                                            (bb, v, (L - b["off_m"]) * fac(b["edge"], False, profile), "bwd")):
                    if ok and math.isfinite(dist[node]):
                        c = dist[node] + rest + b["d_m"] * pen
                        if best is None or c < best[0] - 1e-9:
                            best = (c, "graph", node, how, b)
        if best is None:
            raise RouterDown(f"도로 그래프에서 경로 없음({profile}) — 일방통행·섬", code="no_path")
        if best[1] == "same":
            _, _, a, b = best
            way, hw, L = self.E[a["edge"]][2], self.E[a["edge"]][4], abs(b["off_m"] - a["off_m"])
            return [(way, hw, self._part(a["edge"], a["off_m"], b["off_m"]), L)], (a["d_m"], b["d_m"])
        _, _, node, how, b = best
        seq = [node]
        while seq[-1] != N:
            p = int(pred[seq[-1]])
            if p < 0:
                raise RouterDown("도로 그래프 경로 복원 실패", code="no_path")
            seq.append(p)
        seq.reverse()                                 # N, first, …, node
        _, a, adir = src[seq[1]]
        ua, va, way_a, La, hw_a, _ = self.E[a["edge"]]
        out = []
        if adir == "fwd":
            out.append((way_a, hw_a, self._part(a["edge"], a["off_m"], La), La - a["off_m"]))
        else:
            out.append((way_a, hw_a, self._part(a["edge"], a["off_m"], 0.0), a["off_m"]))
        for p, q in zip(seq[1:-1], seq[2:]):
            k = net.arc_of(p, q)
            e_ = int(net.arc_edge[k])
            _, _, way_, L_, hw_, _ = self.E[e_]
            g = self.geom(e_)
            out.append((way_, hw_, g if net.arc_fwd[k] else g[::-1], L_))
        ub, vb, way_b, Lb, hw_b, _ = self.E[b["edge"]]
        if how == "fwd":
            out.append((way_b, hw_b, self._part(b["edge"], 0.0, b["off_m"]), b["off_m"]))
        else:
            out.append((way_b, hw_b, self._part(b["edge"], Lb, b["off_m"]), Lb - b["off_m"]))
        return out, (a["d_m"], b["d_m"])

    def _dirs(self, e, profile):
        """간선 e 가 u→v · v→u 로 갈 수 있나(이 프로파일)."""
        ow = (self._ow if profile == "car" else self._bow)[e]
        if ow is None:
            return False, False
        return ow in (0, 1), ow in (0, -1)

    def info(self):
        return {"version": "road_graph_v1", "router": "python-dijkstra"}


def _load_region(path):
    """region_v1.geojson → 바깥 고리 목록([(lng, lat)…]). 구멍은 무시(범위 판단용 · 보수 쪽)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rings = []
    for f in doc.get("features", []):
        g = f.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else (g.get("coordinates") or []) if g.get("type") == "MultiPolygon" else []
        for poly in polys:
            if poly:
                rings.append([tuple(p[:2]) for p in poly[0]])
    return rings


def _in_poly(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def resolve(spec, speed=None):
    """라우터 설정 값 → RoadRouter 또는 None.

    None · "auto" = 자료 폴더의 `mobility/road_graph_v1/`(있으면) · "none" · "" = 끔 · 그 밖 = 그 폴더.
    speed = 정적 속도 함수(car.CarGraph.static_kmh) — 주면 시간 가중(기본 배선), 없으면 길이 가중.
    적재는 첫 경로 질의 때 한 번(약 10~15초 · 상주 메모리 약 0.4 GB).
    """
    if spec in ("", "none"):
        return None
    if spec is None or spec == "auto":
        from .paths import PROCESSED
        spec = PROCESSED / "mobility" / "road_graph_v1"
    # 프로세스 안에서 같은 폴더·같은 속도 원천이면 **한 번 올린 라우터를 나눠 쓴다** — 판정기를 다시 올려도(get_verifier 의
    # 입력 변경 재적재 · 시험 모듈마다 build_verifier) 그래프(약 0.4 GB · 10~15초)를 또 올리지 않는다. 그래프 파일은 불변 판.
    #   나눠 쓰는 조건(GPT 77-2 #7): 속도 원천이 「자료 폴더를 가진 객체의 메서드」(CarGraph.static_kmh)일 때만 — 키에 그 폴더와
    #   프로파일·그래프 파일의 수정 시각을 넣는다. 그 밖의 속도 함수(시험의 맨 함수 등)는 나눠 쓰지 않고 새로 만든다.
    sp_src = getattr(getattr(speed, "__self__", None), "dir", None) if speed else None
    if speed is not None and sp_src is None:
        return RoadRouter.load(spec, speed=speed)

    def _mt(pth):
        try:
            return int(Path(pth).stat().st_mtime)
        except OSError:
            return None
    prof_mt = None
    if sp_src is not None:
        prof_mt = (_mt(Path(sp_src) / "topis_link_profile_v1.jsonl.gz"), _mt(Path(sp_src) / "topis_link_profile_v1.jsonl"),
                   _mt(Path(sp_src) / "topis_class_factor_v1.json"))
    key = (str(Path(spec).resolve()), "time" if speed else "length", str(sp_src), prof_mt,
           _mt(Path(spec) / "edges.jsonl.gz"))
    with _SHARED_LOCK:
        r = _SHARED.get(key)
        if r is None:
            r = RoadRouter.load(spec, speed=speed)
            if r is not None:
                _SHARED[key] = r
        return r


_SHARED = {}
_SHARED_LOCK = threading.Lock()


class _EdgeTable:
    """간선 열 저장(파이썬 튜플 37만 개 대신 배열 · 형상은 인코딩 문자열 그대로)."""

    def __init__(self):
        import array
        self.u, self.v = array.array("i"), array.array("i")
        self.way, self.len = array.array("q"), array.array("d")
        self.hw_code, self.hw_names, self._hw_ix = array.array("B"), [], {}
        self.g = []

    def append(self, u, v, way, L, hw, g):
        self.u.append(u)
        self.v.append(v)
        self.way.append(way)
        self.len.append(L)
        k = self._hw_ix.get(hw)
        if k is None:
            k = self._hw_ix[hw] = len(self.hw_names)
            self.hw_names.append(hw)
        self.hw_code.append(k)
        self.g.append(g)

    def freeze(self):
        self._hw_ix = None

    def __len__(self):
        return len(self.u)

    def __getitem__(self, e):
        return (self.u[e], self.v[e], self.way[e], self.len[e], self.hw_names[self.hw_code[e]], self.g[e])


class _Geoms:
    """_Net 스냅 색인을 만들 때만 형상을 푼다."""

    def __init__(self, E):
        self.E = E

    def __getitem__(self, e):
        return decode_polyline(self.E[e][5])
