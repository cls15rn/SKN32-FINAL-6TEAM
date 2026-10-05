# -*- coding: utf-8 -*-
"""X3 — 걸음 그래프 **변형 실험판** 빌더. 발표 코드의 `build_road_graph_v2.py`(팀장)를 고치지 않고, 같은 규칙에 칸 셋을 더한 판을 따로 만든다.

보려는 것: 걸음 그래프 v2 가 길게 틀리는 자리(보도 결손 · 지하상가·고가 통로에 붙는 출구)가 **빌드 규칙으로 풀리는가**.
  ar  1 = 광장 면(`highway=pedestrian|footway` + `area=yes` 닫힌 길 · 같은 태그의 multipolygon)에서 만든 간선 — 테두리 + 진입점끼리 가로지르는 선.
        v2 는 `area=yes` 를 통째로 뺀다(광장을 건너는 길이 없어 돌아간다).
  gr  0 = 지상 · 1 = 지하(`tunnel=yes|passage` · `layer<0` · `level<0` · `indoor=yes` · `highway=corridor`) · 2 = 고가(`layer>0` · `bridge`)
        출발·도착을 **붙일** 간선을 고를 때만 쓴다(지하상가·고가 통로 위로 붙으면 그 통로가 땅과 이어진 먼 곳까지 돌아간다). 지나가는 것은 막지 않는다.
  fmain_a  광장 간선까지 넣은 걸음 최대 연결 성분(fmain 은 v2 와 같은 뜻 — 광장 간선 없이).
나머지 칸·범위·규칙은 v2 와 같다(같은 함수 import). 산출은 **저장소 밖**에만 둔다(실험 자료 · ODbL © OpenStreetMap contributors).

실행: python exp/x3_walk/x3_build_variant.py --repo . --src <south-korea-latest.osm.pbf> --out C:\\final_project\\exp\\x3_walk\\road_graph_x3
"""
import argparse
import gzip
import hashlib
import json
import math
import sys
import time
from pathlib import Path

CHORD_MAX_CONNECTORS = 80


def ground_code(tags):
    def num(v):
        try:
            return float(str(v).split(";")[0])
        except (TypeError, ValueError):
            return 0.0
    layer, level = num(tags.get("layer")), num(tags.get("level"))
    if (tags.get("tunnel") in ("yes", "passage") or layer < 0 or level < 0 or tags.get("indoor") in ("yes", "corridor", "room")
            or tags.get("highway") == "corridor"):
        return 1
    if layer > 0 or tags.get("bridge") not in (None, "no"):
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.repo) / "datasets" / "mobility" / "scripts"))
    import build_road_graph_v1 as V1
    import build_road_graph_v2 as V2
    import numpy as np
    import osmium
    import shapely
    from shapely.geometry import LineString, Polygon

    t0 = time.time()
    src, out = Path(a.src), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log = {"version": "road_graph_x3(실험)", "src": src.name, "src_bytes": src.stat().st_size}
    h = hashlib.md5()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    log["src_md5"] = h.hexdigest()
    hdr = osmium.io.Reader(str(src), osmium.osm.osm_entity_bits.NOTHING).header()
    log["osmosis_replication_timestamp"] = hdr.get("osmosis_replication_timestamp", "")
    region, polys, corridor = V1.build_region(str(src), log)
    load = region.buffer(V1.MARGIN_M / 111320.0 / math.cos(math.radians(37.5))) if V1.MARGIN_M else region
    shapely.prepare(load)
    minx, miny, maxx, maxy = load.bounds
    print(f"[범위] ({time.time()-t0:.0f}s)", flush=True)

    def inside(ll):
        if not any(miny <= la <= maxy and minx <= lo <= maxx for la, lo in ll):
            return False
        return bool(shapely.contains_xy(load, np.fromiter((p[1] for p in ll), float), np.fromiter((p[0] for p in ll), float)).any())

    # ── 1패스: v2 와 같은 way 수집(+ 지상/지하/고가 표시) ──
    ways = []
    keep_tags = ("highway", "oneway", "oneway:bicycle", "maxspeed", "junction", "service", "name", "ref")
    for w in osmium.FileProcessor(str(src)).with_locations().with_filter(osmium.filter.KeyFilter("highway")):
        if not isinstance(w, osmium.osm.Way):
            continue
        tags = dict(w.tags)
        car, bike = V1.classify(tags)
        foot, steps = V2.classify_foot(tags)
        if not (car or bike or foot):
            continue
        try:
            nids = [n.ref for n in w.nodes]
            ll = [(n.lat, n.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            continue
        if len(nids) < 2 or not inside(ll):
            continue
        ways.append((w.id, nids, ll, {k: tags[k] for k in keep_tags if k in tags}, car, bike, foot, steps, ground_code(tags), 0))
    n_v2 = len(ways)
    print(f"[way] v2 규칙 {n_v2:,} ({time.time()-t0:.0f}s)", flush=True)
    other_nodes = set()
    for _id, nids, *_r in ways:
        other_nodes.update(nids)

    # ── 2패스: 광장 면 ──
    areas, n_area_way, n_area_rel, skipped = [], 0, 0, 0
    fp = osmium.FileProcessor(str(src)).with_areas().with_filter(osmium.filter.KeyFilter("highway"))
    for o in fp:
        if not isinstance(o, osmium.osm.Area):
            continue
        tags = dict(o.tags)
        if tags.get("highway") not in ("pedestrian", "footway"):
            continue
        if o.from_way() and tags.get("area") != "yes":
            continue
        t2 = dict(tags)
        t2.pop("area", None)
        if not V2.classify_foot(t2)[0]:                    # foot=no · access=private 광장은 v2 규칙대로 안 걷는다
            continue
        try:
            for outer in o.outer_rings():
                ring = [(n.ref, n.lat, n.lon) for n in outer]
                holes = [[(n.ref, n.lat, n.lon) for n in inner] for inner in o.inner_rings(outer)]
                if len(ring) < 4 or not inside([(la, lo) for _, la, lo in ring]):
                    continue
                areas.append((o.orig_id(), o.from_way(), ring, holes, ground_code(tags)))
                n_area_way += int(o.from_way())
                n_area_rel += int(not o.from_way())
        except osmium.InvalidLocationError:
            skipped += 1
    print(f"[광장] 닫힌 길 {n_area_way:,} · multipolygon 고리 {n_area_rel:,} · 건너뜀 {skipped} ({time.time()-t0:.0f}s)", flush=True)

    chords = []
    n_conn_cut = 0
    for aid, from_way, ring, holes, gc in areas:
        wid = aid if from_way else -aid                    # multipolygon 은 음수 id(way id 와 겹치지 않게)
        for r in [ring] + holes:
            ways.append((wid, [x[0] for x in r], [(x[1], x[2]) for x in r], {"highway": "pedestrian"}, False, False, True, False, gc, 1))
        pts = {}
        for r in [ring] + holes:
            for ref, la, lo in r:
                if ref in other_nodes:
                    pts[ref] = (la, lo)
        conn = list(pts.items())
        if len(conn) < 2:
            continue
        if len(conn) > CHORD_MAX_CONNECTORS:
            n_conn_cut += 1
            conn = conn[:CHORD_MAX_CONNECTORS]
        try:
            poly = Polygon([(lo, la) for _, la, lo in ring], [[(lo, la) for _, la, lo in hh] for hh in holes if len(hh) >= 4])
            if not poly.is_valid:
                poly = poly.buffer(0)
            polyb = poly.buffer(2e-6)
        except Exception:  # noqa: BLE001
            continue
        for i in range(len(conn)):
            for j in range(i + 1, len(conn)):
                (u, (la1, lo1)), (v, (la2, lo2)) = conn[i], conn[j]
                if polyb.covers(LineString([(lo1, la1), (lo2, la2)])):
                    chords.append((wid, u, v, (la1, lo1), (la2, lo2), gc))
    print(f"[광장] 가로지르는 선 {len(chords):,} · 진입점이 {CHORD_MAX_CONNECTORS} 넘어 자른 면 {n_conn_cut} ({time.time()-t0:.0f}s)", flush=True)

    cnt = {}
    for _, nids, *_rest in ways:
        for i, n in enumerate(nids):
            cnt[n] = cnt.get(n, 0) + (2 if i in (0, len(nids) - 1) else 1)
    gnode = {n for n, c in cnt.items() if c >= 2}
    coord, edges = {}, []
    for wid, nids, ll, tg, car, bike, foot, steps, gc, ar in ways:
        hw = tg["highway"]
        ow = V1.oneway_of(tg, hw) if car else 0
        bow = V1.oneway_of(tg, hw) if bike else 0
        if tg.get("oneway:bicycle") == "no":
            bow = 0
        elif hw in V1.BIKE_ONLY_HW and "oneway" not in tg:
            bow = 0
        ms = V1.parse_maxspeed(tg.get("maxspeed"))
        start = 0
        for i in range(1, len(nids)):
            if nids[i] in gnode or i == len(nids) - 1:
                seg = ll[start:i + 1]
                L = sum(V1.hav(*seg[k], *seg[k + 1]) for k in range(len(seg) - 1))
                u, v = nids[start], nids[i]
                coord[u] = ll[start]
                coord[v] = ll[i]
                edges.append({"u": u, "v": v, "way": wid, "sa": start, "sb": i, "len": round(L, 1), "ow": ow, "hw": hw, "ms": ms,
                              "car": int(car), "bike": int(bike), "bow": bow, "foot": int(foot), "st": int(steps),
                              "g": V1.enc_polyline(seg), "gr": gc, "ar": ar})
                start = i
    for wid, u, v, p1, p2, gc in chords:
        if u in coord and v in coord:
            edges.append({"u": u, "v": v, "way": wid, "sa": -1, "sb": -1, "len": round(V1.hav(*p1, *p2), 1), "ow": 0, "hw": "pedestrian",
                          "ms": None, "car": 0, "bike": 0, "bow": 0, "foot": 1, "st": 0, "g": V1.enc_polyline([p1, p2]), "gr": gc, "ar": 2})
    print(f"[그래프] 노드 {len(coord):,} · 간선 {len(edges):,} ({time.time()-t0:.0f}s)", flush=True)

    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    ids = list(coord)
    ix = {n: i for i, n in enumerate(ids)}

    def comp(pred):
        us, vs = [], []
        for e in edges:
            if pred(e):
                us += [ix[e["u"]], ix[e["v"]]]
                vs += [ix[e["v"]], ix[e["u"]]]
        M = coo_matrix((np.ones(len(us)), (us, vs)), shape=(len(ids), len(ids))).tocsr()
        _, lab = connected_components(M, directed=False)
        used = np.zeros(len(ids), bool)
        used[us] = True
        return lab, np.bincount(lab[used]).argmax()

    lab0, big0 = comp(lambda e: e["foot"] and not e["ar"])
    lab1, big1 = comp(lambda e: e["foot"])
    for e in edges:
        iu = ix[e["u"]]
        e["fmain"] = int(bool(e["foot"]) and not e["ar"] and lab0[iu] == big0)
        e["fmain_a"] = int(bool(e["foot"]) and lab1[iu] == big1)

    def write_gz(path, rows):
        with open(path, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0) as gz:
            for r in rows:
                gz.write((json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))

    write_gz(out / "nodes.jsonl.gz", ({"id": n, "lat": round(coord[n][0], 7), "lon": round(coord[n][1], 7)} for n in sorted(coord)))
    edges.sort(key=lambda e: (e["ar"], e["way"], e["sa"]))
    write_gz(out / "edges.jsonl.gz", edges)
    f0 = [e for e in edges if e["foot"] and not e["ar"]]
    log["counts"] = {"ways_v2_rule": n_v2, "nodes": len(coord), "edges": len(edges), "edges_foot_v2_rule": len(f0),
                     "edges_foot_main_v2_rule": sum(e["fmain"] for e in edges),
                     "area_rings_way": n_area_way, "area_rings_multipolygon": n_area_rel,
                     "edges_area_ring": sum(e["ar"] == 1 for e in edges), "edges_area_chord": sum(e["ar"] == 2 for e in edges),
                     "edges_foot_main_with_area": sum(e["fmain_a"] for e in edges),
                     "foot_edges_underground": sum(e["gr"] == 1 for e in f0), "foot_edges_elevated": sum(e["gr"] == 2 for e in f0),
                     "foot_m_underground": round(sum(e["len"] for e in f0 if e["gr"] == 1)),
                     "foot_m_elevated": round(sum(e["len"] for e in f0 if e["gr"] == 2)),
                     "foot_m_all": round(sum(e["len"] for e in f0))}
    log["elapsed_s"] = round(time.time() - t0, 1)
    man = {"version": "road_graph_x3", "note": "실험판 — 발표 코드가 읽지 않는다", "license": "ODbL 1.0 (© OpenStreetMap contributors)",
           "src_md5": log["src_md5"], "osm_data_at": log["osmosis_replication_timestamp"], "counts": log["counts"]}
    (out / "MANIFEST.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "build_report.json").write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(log["counts"], ensure_ascii=False), f"{log['elapsed_s']}s")


if __name__ == "__main__":
    main()
