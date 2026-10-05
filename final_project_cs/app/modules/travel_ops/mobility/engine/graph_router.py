# -*- coding: utf-8 -*-
"""서버 없이 파이썬만으로 길을 찾는다 — `road_graph_v1`(OSM 서울+인접 도시 차도·자전거 그래프) 위 최단경로. 2026-10-04.

왜 만들었나
  택시·자전거·장소 사이 도보는 GraphHopper 서버(Java · 메모리 1~3GB)를 따로 띄워야 돌았다. 이동 담당이 같은 자료(`road_graph_v1` ·
  README 첫 줄 「서버(GraphHopper) 없이 파이썬에서 … 경로를 계산하기 위한 지도 데이터」)를 이미 만들어 뒀는데 그걸 읽는 길찾기 코드가
  저장소에 없었다(team/develop · team/role-mobility 에서 찾지 못함). 이 파일이 그 코드다.

무엇을 하나
  `GraphHopperClient`(car.py)와 **같은 모양**으로 답한다 — `route(s, e, profile, via)` → GraphHopper `/route` 응답 모양(`paths[0]` 의 거리·시간·
  좌표·`details.osm_way_id`·`details.road_class`). 그래서 `CarGraph.compute`(TOPIS 링크 프로파일로 시각별 소요) 와 `BikeRouter` 가 고치지 않고
  그대로 붙는다. 형상은 이 파일 밖으로 나가지 않는다(저장하지 않는 원칙 그대로 — 받는 쪽이 거리·소요만 남긴다).

프로파일
  car   `car=1` 간선, 일방통행(`ow`) 지킴. 경로 선택 비용 = 시간(도로급 기본속도 · 있으면 OSM maxspeed). 실제 소요는 받는 쪽이 TOPIS 로 다시 잰다.
  bike  `bike=1` 간선, 자전거 일방(`bow`) 지킴. 비용 = 거리 × 도로급 계수(자전거길 선호 · 간선도로 기피).
  foot  자동차전용(motorway·trunk) 빼고 **양방향** 거리. ★이 그래프에는 보행자 전용 길(계단·샛길)이 자전거 허용일 때만 들어 있다 —
        그 길만으로 이어진 곳은 실제보다 멀게 나올 수 있다(거리는 하한이 아니라 **추정**). 경로 없음은 「길 없음」의 근거가 아니다.

못 찾았을 때
  좌표가 지도 범위 밖이거나(`out_of_area`) 이어진 길이 없으면(`no_path`) `RouterDown` 을 낸다 — 받는 쪽은 라우터가 없을 때와 같이 근거없음·
  직선×계수로 간다. 「길이 없다」고 단정하지 않는다(이 그래프에 없는 길이 있을 수 있다).

비용·메모리(예상 — 아래 검증에서 잰 값은 리포트에): 첫 호출 때 한 번 올린다(약 10초 · 수백 MB). 프로파일별 인접표는 처음 쓸 때 만든다.
"""
from __future__ import annotations

import array
import gzip
import heapq
import json
import math
import re
import threading
from pathlib import Path

from .car import RouterDown, hav

SOURCE_ID = "osm_road_graph@2026-09-18"           # car.GRAPH_SOURCE_ID 와 같은 원자료(pbf 2026-09-18)

# 경로 선택용 정적 속도(km/h) — car.DEFAULT_KMH 의 골목 값과 같은 결을 간선에 이어 붙인 것. 소요를 내는 값이 아니다(소요는 TOPIS).
HW_KMH = {"motorway": 70, "motorway_link": 45, "trunk": 55, "trunk_link": 40, "primary": 40, "primary_link": 30,
          "secondary": 35, "secondary_link": 28, "tertiary": 30, "tertiary_link": 25, "unclassified": 20,
          "residential": 15, "living_street": 6, "service": 10, "road": 15, "track": 10}
BIKE_FACTOR = {"cycleway": 0.9, "living_street": 1.0, "residential": 1.0, "unclassified": 1.0, "service": 1.1,
               "tertiary": 1.0, "tertiary_link": 1.0, "secondary": 1.1, "secondary_link": 1.1, "primary": 1.25,
               "primary_link": 1.25, "trunk": 1.6, "trunk_link": 1.6, "path": 1.2, "footway": 1.2, "pedestrian": 1.15,
               "track": 1.2}
FOOT_BLOCK = {"motorway", "motorway_link", "trunk", "trunk_link"}
FOOT_MPS = 1.39                                    # GraphHopper foot 기본(5 km/h) — 엔진은 foot 거리만 쓴다
BIKE_MPS = 15 / 3.6
STEPS_FACTOR = 1.5                                 # 계단은 같은 거리를 1.5배로 친다(느리고 힘들다)
STEPS_MPS = FOOT_MPS * 0.6
SNAP_MAX_M = 1200.0                                # 이보다 먼 곳은 지도 밖으로 본다
OPTIMISTIC_ACCESS_M = 30.0                         # 길에서 이만큼 넘게 떨어진 점은 길 밖 직선 구간을 걷는 것으로 되어 거리가 낙관적일 수 있다
ARTERIAL_HW = {"primary", "primary_link", "secondary", "secondary_link", "trunk", "trunk_link"}
SNAP_TIE_M = 12.0                                  # 가장 가까운 간선보다 이 안쪽의 간선도 출발·도착 후보로 둔다
CELL_LAT, CELL_LON = 0.004, 0.005                  # 격자 한 칸 ≈ 440 m
F_CAR, F_BIKE, F_MAIN, F_BMAIN = 1, 2, 4, 8
F_FOOT, F_FMAIN, F_STEPS = 16, 32, 64                # road_graph_v2 — 걸음 통행 · 걸음 최대 연결 성분 · 계단


def decode_polyline(s, precision=1e6):
    """Google encoded polyline → [(lng, lat)…]. 정밀도 1e-6(road_graph_v1 간선 `g`)."""
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
        out.append((lng / precision, lat / precision))
    return out


def _maxspeed(v):
    if v is None:
        return None
    m = re.match(r"\s*(\d{1,3})\s*(mph)?\s*$", str(v))
    if not m:
        return None
    k = int(m.group(1)) * (1.609 if m.group(2) else 1.0)
    return min(max(k, 5.0), 100.0)


def _read_jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


_SHARED: dict = {}
_SHARED_LOCK = threading.Lock()


class _Snap:
    __slots__ = ("e", "d_u", "dist", "pt", "seg")

    def __init__(self, e, d_u, dist, pt, seg):
        self.e, self.d_u, self.dist, self.pt, self.seg = e, d_u, dist, pt, seg


class GraphRouter:
    """`GraphHopperClient` 와 같은 모양의 파이썬 라우터. 자료는 처음 길을 물을 때 한 번 올린다."""

    is_local = True                                  # 호출 비용이 없다 — 플래너가 역·정류장 접근 걷기를 길로 잰다(#13)
    url = None                                       # 서버가 아니다 — BikeRouter.url 이 None 으로 읽힌다
    force_dijkstra = False
    basis = "로컬 도로그래프"                          # BikeRouter 응답의 basis · 근거 문구(「보행망 …」)에 그대로 들어간다
    has_foot = False                                 # 적재 뒤 — v2(걸음 칸이 있는 판)이면 True

    @property
    def source_id(self):
        """근거 칸에 적는 자료 식별자 — 폴더의 MANIFEST 가 말하는 OSM 기준일로. 없으면(합성 시험) 기본값."""
        got = getattr(self, "_source_id", None)
        if got is None:
            got = SOURCE_ID
            try:
                m = json.loads((self.dir / "MANIFEST.json").read_text(encoding="utf-8"))
                day = str(m.get("osm_data_at") or "")[:10]
                if day:
                    got = f"osm_road_graph{'_v2' if m.get('version') == 'road_graph_v2' else ''}@{day}"
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            self._source_id = got
        return got

    @property
    def foot_ok(self):
        """걸음 길이 든 판(road_graph_v2)인가 — **자료를 올리지 않고** 답한다(폴더의 MANIFEST `version` · 없으면 폴더 이름).
        ☆101(2026-10-05 · 이동 담당이 더함) 차도만 있는 v1 로는 걷기를 재지 않게 플래너가 이 값을 본다(plan._foot_router) —
        v1 로 걸음을 재면 차도로 돌아가 길게 나온다(도보 20구간 10/20 · 예시 일정 2/4 못 만듦). 택시·자전거는 v1 로도 같은 값이다.
        합성 자료(records · 시험)는 걸을 수 있는 것으로 본다."""
        got = getattr(self, "_foot_ok", None)
        if got is None:
            if self._records is not None or self.dir is None:
                got = True
            else:
                got = self.dir.name == "road_graph_v2"
                try:
                    m = json.loads((self.dir / "MANIFEST.json").read_text(encoding="utf-8"))
                    got = m.get("version") == "road_graph_v2"
                except (OSError, ValueError, AttributeError, TypeError):
                    pass
            self._foot_ok = got
        return got

    def __init__(self, graph_dir=None, *, records=None):
        self.dir = Path(graph_dir) if graph_dir else None
        self._records = records                      # 시험용 {"nodes": [...], "edges": [...]}
        self._lock = threading.Lock()
        self._loaded = False
        self.calls = 0
        self.load_seconds = None

    @classmethod
    def default(cls):
        """자료 폴더마다 **하나만** 만든다 — 그래프는 읽기 전용이고 프로세스에 약 230MB 라, 판정기를 다시 세울 때마다(서버 조립 · 시험)
        새로 올리면 메모리가 판정기 수만큼 늘어난다(시험 묶음에서 한 프로세스가 2.8GB 까지 자랐다)."""
        from .paths import PROCESSED
        base = Path(PROCESSED) / "mobility"
        # ☆`[2026-10-04]` v2(걸음 길 포함)가 있으면 그것, 없으면 v1(차도·자전거만 — 걸음은 차도 위로만 간다)
        d = (base / "road_graph_v2" if (base / "road_graph_v2" / "edges.jsonl.gz").exists() else base / "road_graph_v1").resolve()
        key = str(d)
        with _SHARED_LOCK:
            r = _SHARED.get(key)
            if r is None:
                r = _SHARED[key] = cls(d)
            return r

    # ── 존재 확인(가볍다 — 자료를 올리지 않는다) ─────────────────────────────
    def available(self):
        if self._records is not None:
            return True
        return bool(self.dir and (self.dir / "nodes.jsonl.gz").exists() and (self.dir / "edges.jsonl.gz").exists())

    def info(self):
        return {"version": "local:road_graph_v1", "source_id": SOURCE_ID} if self.available() else None

    def warm(self, profiles=("car", "foot", "bike")):
        """미리 올려 둔다 — 첫 길 묻기가 10초 가까이 멈추지 않게. 서버 기동 뒤 백그라운드 스레드에서 부른다. 실패는 호출한 쪽이 센다."""
        self._load()
        for p in profiles:
            self._adjacency(p)

    # ── 자료 적재 ─────────────────────────────────────────────────────────────
    def _load(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            import time
            t0 = time.monotonic()
            if not self.available():
                raise RouterDown(f"도로 그래프 자료가 없다({self.dir})")
            nodes = self._records["nodes"] if self._records is not None else _read_jsonl(self.dir / "nodes.jsonl.gz")
            edges = self._records["edges"] if self._records is not None else _read_jsonl(self.dir / "edges.jsonl.gz")
            self.nid, self.nlat, self.nlon = {}, array.array("d"), array.array("d")
            for r in nodes:
                self.nid[r["id"]] = len(self.nlat)
                self.nlat.append(r["lat"])
                self.nlon.append(r["lon"])
            self.hw_code, self.hw_name = {}, []
            A = array.array
            self.eu, self.ev, self.elen, self.eway = A("i"), A("i"), A("f"), A("q")
            self.ehw, self.ems, self.eflag, self.eow, self.ebow = A("H"), A("f"), A("B"), A("b"), A("b")
            self.has_foot = False
            self.eg = []
            self.bb = [A("d"), A("d"), A("d"), A("d")]          # 간선 경계 상자(minlat, minlon, maxlat, maxlon)
            self.grid = {}
            self.skipped = 0
            for r in edges:
                u, v = self.nid.get(r["u"]), self.nid.get(r["v"])
                if u is None or v is None:
                    self.skipped += 1
                    continue
                e = len(self.eu)
                self.eu.append(u)
                self.ev.append(v)
                self.elen.append(float(r["len"]))
                self.eway.append(int(r["way"]))
                hw = r.get("hw") or ""
                c = self.hw_code.get(hw)
                if c is None:
                    c = self.hw_code[hw] = len(self.hw_name)
                    self.hw_name.append(hw)
                self.ehw.append(c)
                ms = _maxspeed(r.get("ms"))
                self.ems.append(ms or 0.0)
                if "foot" in r:
                    self.has_foot = True
                self.eflag.append((F_CAR if r.get("car") else 0) | (F_BIKE if r.get("bike") else 0)
                                  | (F_MAIN if r.get("main") else 0) | (F_BMAIN if r.get("bmain") else 0)
                                  | (F_FOOT if r.get("foot") else 0) | (F_FMAIN if r.get("fmain") else 0)
                                  | (F_STEPS if r.get("st") else 0))
                self.eow.append(int(r.get("ow") or 0))
                self.ebow.append(int(r.get("bow") or 0))
                g = r["g"]
                self.eg.append(g)
                pts = decode_polyline(g)
                lats = [p[1] for p in pts]
                lons = [p[0] for p in pts]
                box = (min(lats), min(lons), max(lats), max(lons))
                for k in range(4):
                    self.bb[k].append(box[k])
                for ci in range(int(box[0] // CELL_LAT), int(box[2] // CELL_LAT) + 1):
                    for cj in range(int(box[1] // CELL_LON), int(box[3] // CELL_LON) + 1):
                        self.grid.setdefault((ci, cj), []).append(e)
            self._geo, self._adj = {}, {}
            self._loaded = True
            self.load_seconds = round(time.monotonic() - t0, 1)

    # ── 간선 정보 ─────────────────────────────────────────────────────────────
    def _allowed(self, profile, e):
        """(u→v 가능, v→u 가능)."""
        fl = self.eflag[e]
        if profile == "car":
            if not fl & F_CAR:
                return False, False
            ow = self.eow[e]
        elif profile == "bike":
            if not fl & F_BIKE:
                return False, False
            ow = self.ebow[e]
        else:                                                         # foot
            if self.has_foot:                                         # v2 — OSM 태그로 판정한 걸음 통행(양방향)
                return (bool(fl & F_FOOT),) * 2
            return ((self.hw_name[self.ehw[e]] not in FOOT_BLOCK),) * 2
        return (ow >= 0), (ow <= 0)

    def _snappable(self, profile, e):
        fl = self.eflag[e]
        if profile == "car":
            return bool(fl & F_MAIN)
        if profile == "bike":
            return bool(fl & F_BMAIN)
        if self.has_foot:
            return bool(fl & F_FMAIN)
        return bool(fl & (F_MAIN | F_BMAIN))

    def _cost(self, profile, e):
        """간선 전체를 지나는 비용(양방향 같다)."""
        L = self.elen[e]
        hw = self.hw_name[self.ehw[e]]
        if profile == "car":
            kmh = self.ems[e] or HW_KMH.get(hw, 15)
            return L / (kmh / 3.6)                                    # 초
        if profile == "bike":
            return L * BIKE_FACTOR.get(hw, 1.2)                       # 거리(가중)
        if self.eflag[e] & F_STEPS:
            return L * STEPS_FACTOR                                   # 계단
        return float(L)                                               # 걸음 — 거리

    def _geom(self, e):
        """(좌표 [(lng,lat)…], 누적 거리[m], 구간 거리[m]) — 캐시."""
        got = self._geo.get(e)
        if got is None:
            pts = decode_polyline(self.eg[e])
            seg = [hav(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
            cum = [0.0]
            for s in seg:
                cum.append(cum[-1] + s)
            if len(self._geo) > 150_000:
                self._geo.clear()
            got = self._geo[e] = (pts, cum, seg)
        return got

    def _adjacency(self, profile):
        adj = self._adj.get(profile)
        if adj is not None:
            return adj
        with self._lock:
            adj = self._adj.get(profile)
            if adj is not None:
                return adj
            n, m = len(self.nlat), len(self.eu)
            deg = [0] * (n + 1)
            allowed = []
            for e in range(m):
                f, b = self._allowed(profile, e)
                allowed.append((f, b))
                if f:
                    deg[self.eu[e] + 1] += 1
                if b:
                    deg[self.ev[e] + 1] += 1
            for i in range(n):
                deg[i + 1] += deg[i]
            tot = deg[n]
            to, pe, w = array.array("i", [0]) * tot, array.array("i", [0]) * tot, array.array("f", [0.0]) * tot
            fill = deg[:n]
            for e in range(m):
                f, b = allowed[e]
                if not (f or b):
                    continue
                c = self._cost(profile, e)
                if f:
                    k = fill[self.eu[e]]
                    fill[self.eu[e]] += 1
                    to[k], pe[k], w[k] = self.ev[e], e * 2, c
                if b:
                    k = fill[self.ev[e]]
                    fill[self.ev[e]] += 1
                    to[k], pe[k], w[k] = self.eu[e], e * 2 + 1, c
            adj = self._adj[profile] = (deg, to, pe, w)
            return adj

    # ── 좌표 → 간선 붙이기 ────────────────────────────────────────────────────
    def _snap_many(self, profile, lng, lat):
        """좌표에서 가장 가까운 간선과, 그보다 SNAP_TIE_M 안에 있는 다른 간선들(최대 6) — 가까운 순.

        하나만 고르면 교차로 위의 점이 하필 일방통행 간선에 붙어 나갈 길이 막힌다(시험에서 잡았다) — 같은 거리쯤의 간선은 모두 출발·도착 후보로 둔다."""
        # 같은 거리쯤의 간선을 여럿 두는 것은 **자동차**만이다 — 일방통행 때문에 어느 간선에 붙었느냐가 갈 수 있는 방향을 가른다.
        #   걸음·자전거는 가장 가까운 간선 하나만 쓴다(실제 서울 14쌍에서 여럿을 두면 길 건너편 간선으로 갈아타 직선에 가깝게 짧아졌다).
        tie = SNAP_TIE_M if profile == "car" else 0.0
        ci, cj = int(lat // CELL_LAT), int(lng // CELL_LON)
        kx = math.cos(math.radians(lat)) * 111320.0
        ky = 110540.0
        found: dict[int, _Snap] = {}
        best = None
        for ring in (1, 2, 4, 8):
            seen = set()
            for di in range(-ring, ring + 1):
                for dj in range(-ring, ring + 1):
                    seen.update(self.grid.get((ci + di, cj + dj), ()))
            for e in seen:
                if e in found or not self._snappable(profile, e) or not any(self._allowed(profile, e)):
                    continue
                if best is not None:
                    # 경계 상자까지의 거리가 (찾은 것 + 허용 오차)보다 멀면 건너뛴다
                    dy = max(self.bb[0][e] - lat, 0.0, lat - self.bb[2][e]) * ky
                    dx = max(self.bb[1][e] - lng, 0.0, lng - self.bb[3][e]) * kx
                    if dx * dx + dy * dy >= (best + tie) ** 2:
                        continue
                pts, cum, seg = self._geom(e)
                top = None
                for i in range(len(seg)):
                    (ax, ay), (bx, by) = pts[i], pts[i + 1]
                    px, py = (lng - ax) * kx, (lat - ay) * ky
                    dx, dy = (bx - ax) * kx, (by - ay) * ky
                    L2 = dx * dx + dy * dy
                    t = 0.0 if L2 == 0 else max(0.0, min(1.0, (px * dx + py * dy) / L2))
                    d = math.hypot(px - t * dx, py - t * dy)
                    if top is None or d < top.dist:
                        top = _Snap(e, cum[i] + t * seg[i], d, (ax + t * (bx - ax), ay + t * (by - ay)), (i, t))
                found[e] = top
                if best is None or top.dist < best:
                    best = top.dist
            if best is not None and best + tie <= ring * min(CELL_LAT * ky, CELL_LON * kx) * 0.9:
                break
        if best is None or best > SNAP_MAX_M:
            raise RouterDown(f"out_of_area: 좌표({lng:.5f},{lat:.5f}) 근처 {SNAP_MAX_M:.0f} m 안에 {profile} 길이 없다")
        near = sorted((sn for sn in found.values() if sn.dist <= best + tie), key=lambda sn: sn.dist)
        return near[:6]

    # ── 길 찾기 ───────────────────────────────────────────────────────────────
    def route(self, s, e, profile="car", via=None):
        """s, e = (lng, lat). GraphHopper `/route` 응답 모양으로 돌려준다. 못 찾으면 RouterDown."""
        if via:
            raise RouterDown("via(경유지)는 이 라우터가 지원하지 않는다")
        if profile not in ("car", "bike", "foot"):
            raise RouterDown(f"지원하지 않는 프로파일: {profile}")
        self._load()
        self.calls += 1
        deg, to, pe, w = self._adjacency(profile)
        Ss, Ts = self._snap_many(profile, s[0], s[1]), self._snap_many(profile, e[0], e[1])
        S, T = Ss[0], Ts[0]                                  # 가장 가까운 붙임 — 응답의 snap_m 과 휴리스틱 목표
        cost = lambda ed: self._cost(profile, ed)           # noqa: E731
        L = lambda ed: self._geom(ed)[1][-1]                # noqa: E731  간선 형상 길이(m)

        # 시작: 간선 위 점에서 나갈 수 있는 끝점 / 도착: 들어와 닿을 수 있는 끝점. 값 = (비용, 어느 끝, 어느 붙임)
        starts, goals = {}, {}

        def put(table, node, c, side, snap):
            if node not in table or c < table[node][0]:
                table[node] = (c, side, snap)

        near_node = 1.0                                      # 끝점에서 이만큼 안이면 그 끝점 위에 있는 것으로 본다(방향 제약 없이)
        for sn in Ss:
            fS, bS = self._allowed(profile, sn.e)
            Ls = L(sn.e)
            if bS:                                           # 점 → u (v→u 방향 진행)
                put(starts, self.eu[sn.e], cost(sn.e) * sn.d_u / Ls, "u", sn)
            if fS:                                           # 점 → v
                put(starts, self.ev[sn.e], cost(sn.e) * (Ls - sn.d_u) / Ls, "v", sn)
            if sn.d_u <= near_node:
                put(starts, self.eu[sn.e], 0.0, "u", sn)
            if Ls - sn.d_u <= near_node:
                put(starts, self.ev[sn.e], 0.0, "v", sn)
        for sn in Ts:
            fT, bT = self._allowed(profile, sn.e)
            Lt = L(sn.e)
            if fT:                                           # u → 점
                put(goals, self.eu[sn.e], cost(sn.e) * sn.d_u / Lt, "u", sn)
            if bT:                                           # v → 점
                put(goals, self.ev[sn.e], cost(sn.e) * (Lt - sn.d_u) / Lt, "v", sn)
            if sn.d_u <= near_node:
                put(goals, self.eu[sn.e], 0.0, "u", sn)
            if Lt - sn.d_u <= near_node:
                put(goals, self.ev[sn.e], 0.0, "v", sn)

        direct, direct_pair = None, None                     # 같은 간선 위 직행
        for a in Ss:
            for b in Ts:
                if a.e != b.e:
                    continue
                fA, bA = self._allowed(profile, a.e)
                La = L(a.e)
                c = None
                if b.d_u >= a.d_u and fA:
                    c = cost(a.e) * (b.d_u - a.d_u) / La
                elif b.d_u < a.d_u and bA:
                    c = cost(a.e) * (a.d_u - b.d_u) / La
                if c is not None and (direct is None or c < direct):
                    direct, direct_pair = c, (a, b)

        tx, ty = T.pt
        # 비용 단위 대비 직선거리의 하한 — A* 가 틀린 답을 내지 않으려면 실제 비용보다 작아야 한다.
        #   car 는 초(최고 100 km/h = 27.8 m/s) · bike 는 거리×계수(최소 0.9) · foot 은 거리. 목표 점이 여럿(SNAP_TIE_M 안)이라 그만큼 뺀다
        hscale = {"car": 1.0 / 28.0, "bike": 0.9}.get(profile, 1.0)
        if self.force_dijkstra:                              # 시험용 - 휴리스틱을 끈 순수 다익스트라(A* 가 같은 답을 내는지 대조)
            h = lambda n: 0.0                                # noqa: E731
        else:
            h = lambda n: max(hav((self.nlon[n], self.nlat[n]), (tx, ty)) - (SNAP_TIE_M if profile == "car" else 0.0), 0.0) * hscale  # noqa: E731

        dist, parent, heap = {}, {}, []
        for n, (c0, _side, _sn) in starts.items():
            dist[n] = c0
            parent[n] = None
            heapq.heappush(heap, (c0 + h(n), n))
        best, best_node = (direct if direct is not None else math.inf), None
        closed = set()
        while heap:
            f, n = heapq.heappop(heap)
            if f >= best:
                break
            if n in closed:
                continue
            closed.add(n)
            g = dist[n]
            if n in goals:
                tot = g + goals[n][0]
                if tot < best:
                    best, best_node = tot, n
            for k in range(deg[n], deg[n + 1]):
                m = to[k]
                if m in closed:
                    continue
                ng = g + w[k]
                if ng < dist.get(m, math.inf):
                    dist[m] = ng
                    parent[m] = (n, pe[k])
                    heapq.heappush(heap, (ng + h(m), m))
        if best == math.inf:
            raise RouterDown("no_path: 이 그래프에서 두 지점이 이어지지 않는다(길이 없다는 뜻이 아니다)")

        # 되짚어 조각 만들기: [(edge, 좌표열)]
        pieces = []
        if best_node is None:                                # 같은 간선 위 직행
            pieces.append((direct_pair[0].e, self._cut(*direct_pair)))
        else:
            n, chain = best_node, []
            while parent[n] is not None:
                p, code = parent[n]
                chain.append((code >> 1, code & 1))
                n = p
            chain.reverse()
            _c0, side0, snap0 = starts[n]                    # 출발 점이 나간 끝점과 그때 붙은 간선
            pieces.append((snap0.e, self._part(snap0, side0)))
            for ed, rev in chain:
                pts = self._geom(ed)[0]
                pieces.append((ed, list(reversed(pts)) if rev else list(pts)))
            _c1, side1, snap1 = goals[best_node]
            pieces.append((snap1.e, self._part(snap1, side1, entering=True)))

        path = self._response(pieces, profile, s, e)
        path["snap_m"] = [round(S.dist, 1), round(T.dist, 1)]
        # ☆`[2026-10-04]` 낙관 가능 표시 — GraphHopper 기록보다 크게 짧게 나온 두 쌍을 지도 태그와 코덱스 교차 판정으로 따져 봤더니
        #   「우리 로직이 더 나아서」가 아니라 ①길 밖 직선 접근 구간이 전체의 절반이 넘거나(고가 보행로 위 점이 85 m 밖 길에 붙음)
        #   ②보도·횡단 정보 없이 큰길 중앙선을 따라 걷는 경로여서였다. 거리 보정이 아니라 **불확실성을 드러낸다**(근거 문구 · 대체 비교).
        acc_max = max(S.dist, T.dist)
        arterial_m = sum(
            sum(hav(path["points"]["coordinates"][k], path["points"]["coordinates"][k + 1]) for k in range(a, b))
            for (a, b, hw) in path["details"]["road_class"] if hw in ARTERIAL_HW) if profile == "foot" else 0.0
        path["quality"] = {"access_max_m": round(acc_max, 1),
                           "arterial_pct": round(100 * arterial_m / max(path["distance"], 1.0), 1),
                           "optimistic": bool(profile == "foot" and acc_max > OPTIMISTIC_ACCESS_M)}
        if profile == "foot":
            # 걸어서는 도로까지 가는 직선 구간도 걷는다(건물·공원 안의 점은 길에서 떨어져 있다). 자동차·자전거는 더하지 않는다
            # — GraphHopper 도 거리에 넣지 않고, 차는 길 위에서 타고 내린다.
            acc = S.dist + T.dist
            path["distance"] = round(path["distance"] + acc, 2)
            path["time"] += int(round(acc / FOOT_MPS * 1000))
        return {"paths": [path]}

    def _cut(self, S, T):
        """같은 간선 위 S→T 좌표열."""
        pts = self._geom(S.e)[0]
        (i, _), (j, _) = S.seg, T.seg
        if T.d_u >= S.d_u:
            return [S.pt] + pts[i + 1:j + 1] + [T.pt]
        return [S.pt] + list(reversed(pts[j + 1:i + 1])) + [T.pt]

    def _part(self, P, side, entering=False):
        """간선 위 점 P 와 끝점(side: 'u'|'v') 사이 좌표열. 나가면 P→끝점, 들어오면 끝점→P."""
        pts = self._geom(P.e)[0]
        i = P.seg[0]
        if side == "u":
            seq = [P.pt] + list(reversed(pts[:i + 1]))             # P → u
        else:
            seq = [P.pt] + pts[i + 1:]                             # P → v
        return list(reversed(seq)) if entering else seq

    def _response(self, pieces, profile, s, e):
        coords, det_way, det_cls = [], [], []
        dist_m = secs = 0.0
        for ed, pts in pieces:
            clean = [pts[0]]
            for p in pts[1:]:
                if hav(clean[-1], p) > 0.05:
                    clean.append(p)
            if len(clean) < 2:
                continue
            a = len(coords) - 1 if coords else 0
            if coords:
                if hav(coords[-1], clean[0]) > 0.05:
                    coords.append(clean[0])
                    a = len(coords) - 1
            else:
                coords.append(clean[0])
            coords.extend(clean[1:])
            b = len(coords) - 1
            length = sum(hav(clean[k], clean[k + 1]) for k in range(len(clean) - 1))
            dist_m += length
            hw = self.hw_name[self.ehw[ed]]
            det_way.append([a, b, int(self.eway[ed])])
            det_cls.append([a, b, hw])
            if profile == "car":
                secs += length / ((self.ems[ed] or HW_KMH.get(hw, 15)) / 3.6)
            elif profile == "bike":
                secs += length / BIKE_MPS
            else:
                secs += length / (STEPS_MPS if self.eflag[ed] & F_STEPS else FOOT_MPS)
        if not coords:                                        # 같은 점 — 0 m 경로
            coords = [list(s), list(e)]
        return {"distance": round(dist_m, 2), "time": int(round(secs * 1000)),
                "points": {"type": "LineString", "coordinates": [list(c) for c in coords]},
                "details": {"osm_way_id": det_way, "road_class": det_cls}}
